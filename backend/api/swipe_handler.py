"""Premium Dating API: discover, swipes, auth, and notifications."""

from __future__ import annotations

import json
import os
import time
import hashlib
import hmac
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs

import asyncpg
from aiogram import Bot
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Any, Optional

from .filters import create_filters_router

load_dotenv()
load_dotenv(Path(__file__).resolve().parents[2] / '.env')

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/dating_db")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
RUN_WITHOUT_DB = os.getenv("RUN_WITHOUT_DB", "false").lower() == "true" and ENVIRONMENT != "production"

DEFAULT_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
EXTRA_ORIGINS = [
    origin.strip() for origin in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if origin.strip()
]
ALLOWED_ORIGINS = list(dict.fromkeys(DEFAULT_ORIGINS + EXTRA_ORIGINS))

app = FastAPI(title="Premium Dating API")

WEBAPP_DIR = Path(__file__).resolve().parents[2] / "webapp-deploy"
if WEBAPP_DIR.exists():
    # Single deployment target: API + mini app on one Render service.
    app.mount("/mini", StaticFiles(directory=str(WEBAPP_DIR), html=True), name="mini")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None

db_pool = None
rate_buckets: dict[tuple[int, str], list[float]] = defaultdict(list)
idempotency_cache: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
IDEMPOTENCY_TTL_SEC = 10 * 60
mock_profiles: dict[int, dict[str, Any]] = {}
mock_swipes: dict[tuple[int, int], str] = {}
mock_matches: dict[tuple[int, int], dict[str, Any]] = {}
mock_messages: dict[int, list[dict[str, Any]]] = {}
mock_next_match_id = 1
mock_next_message_id = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mock_match_key(user_a: int, user_b: int) -> tuple[int, int]:
    return (min(user_a, user_b), max(user_a, user_b))


def _mock_match_contains(match_row: dict[str, Any], user_id: int) -> bool:
    return user_id in (int(match_row['user1_id']), int(match_row['user2_id']))


def _mock_other_user(match_row: dict[str, Any], user_id: int) -> int:
    return int(match_row['user2_id']) if int(match_row['user1_id']) == int(user_id) else int(match_row['user1_id'])


# Models
class SwipeRequest(BaseModel):
    target_user_id: int
    swipe_type: str  # like | dislike | superlike


class SwipeResponse(BaseModel):
    is_match: bool
    matched_user: Optional[dict[str, Any]] = None


class MessageCreateRequest(BaseModel):
    match_id: int
    content: str
    message_type: str = "text"


class MessageCreateResponse(BaseModel):
    id: int
    created_at: str


class DirectMessageCreateRequest(BaseModel):
    target_user_id: int
    content: str
    message_type: str = "text"


class NotificationSummary(BaseModel):
    likes: int
    matches: int
    unread_messages: int


class MatchItem(BaseModel):
    match_id: int
    user_id: int
    first_name: str
    city: Optional[str] = None
    primary_photo_url: Optional[str] = None
    last_message: Optional[str] = None
    last_message_at: Optional[str] = None
    unread_count: int = 0


class MessageItem(BaseModel):
    id: int
    match_id: int
    from_user_id: int
    content: Optional[str] = None
    message_type: str = "text"
    is_read: bool = False
    created_at: str


class MessageListResponse(BaseModel):
    items: list[MessageItem]


class MatchListResponse(BaseModel):
    items: list[MatchItem]


class ProfileUpdateRequest(BaseModel):
    first_name: Optional[str] = None
    age: Optional[int] = None
    bio: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    gender: Optional[str] = None
    is_premium: Optional[bool] = None


class NotificationSettingsUpdate(BaseModel):
    like_enabled: bool
    match_enabled: bool
    message_enabled: bool


class ReportRequest(BaseModel):
    reason: str
    details: Optional[str] = None


def _cleanup_idempotency_cache() -> None:
    now = time.time()
    expired = [k for k, (ts, _) in idempotency_cache.items() if now - ts > IDEMPOTENCY_TTL_SEC]
    for k in expired:
        idempotency_cache.pop(k, None)


def _check_rate_limit(user_id: int, key: str, limit: int, window_sec: int) -> None:
    now = time.time()
    bucket_key = (user_id, key)
    bucket = rate_buckets[bucket_key]
    cutoff = now - window_sec
    bucket[:] = [t for t in bucket if t >= cutoff]
    if len(bucket) >= limit:
        raise HTTPException(status_code=429, detail="Too many requests, please retry shortly")
    bucket.append(now)


def _idempotency_get(user_id: int, idem_key: str) -> Optional[dict[str, Any]]:
    _cleanup_idempotency_cache()
    cached = idempotency_cache.get((user_id, idem_key))
    if not cached:
        return None
    return cached[1]


def _idempotency_set(user_id: int, idem_key: str, response_payload: dict[str, Any]) -> None:
    idempotency_cache[(user_id, idem_key)] = (time.time(), response_payload)


async def _ensure_notification_settings_table(pool: asyncpg.Pool | None) -> None:
    if RUN_WITHOUT_DB or pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_settings (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                like_enabled BOOLEAN DEFAULT TRUE,
                match_enabled BOOLEAN DEFAULT TRUE,
                message_enabled BOOLEAN DEFAULT TRUE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


async def _is_notification_enabled(conn: asyncpg.Connection, user_id: int, kind: str) -> bool:
    row = await conn.fetchrow(
        "SELECT like_enabled, match_enabled, message_enabled FROM notification_settings WHERE user_id = $1",
        user_id,
    )
    if not row:
        return True
    if kind == "like":
        return bool(row["like_enabled"])
    if kind == "match":
        return bool(row["match_enabled"])
    if kind == "message":
        return bool(row["message_enabled"])
    return True


# Telegram WebApp Authentication
def validate_telegram_init_data(init_data: str, bot_token: str) -> dict[str, Any]:
    """
    Validates Telegram WebApp initData to prevent spoofing.
    """
    try:
        parsed = parse_qs(init_data, keep_blank_values=True)
        received_hash = parsed.get('hash', [None])[0]
        if not received_hash:
            raise ValueError("No hash provided")
        
        # Create data check string
        data_check_parts = []
        for key in sorted(parsed.keys()):
            if key != 'hash':
                for value in parsed[key]:
                    data_check_parts.append(f"{key}={value}")
        
        data_check_string = '\n'.join(data_check_parts)
        
        # Create secret key and verify
        secret_key = hmac.new(
            b"WebAppData",
            bot_token.encode(),
            hashlib.sha256
        ).digest()
        
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(calculated_hash, received_hash):
            raise ValueError("Invalid hash")
        
        auth_date = int(parsed.get('auth_date', ['0'])[0])
        now = int(time.time())
        if auth_date <= 0 or now - auth_date > 24 * 60 * 60:
            raise ValueError("initData expired")

        user_data = json.loads(parsed.get('user', ['{}'])[0])
        if 'id' not in user_data:
            raise ValueError("No user in initData")
        return user_data
        
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid auth: {str(e)}")


def get_db_pool() -> asyncpg.Pool:
    """Get database pool."""
    if db_pool is None and not RUN_WITHOUT_DB:
        raise HTTPException(status_code=503, detail="Database is not ready")
    return db_pool


async def get_current_user(
    authorization: str = Header(...),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """Dependency to get authenticated user."""
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN is not configured")

    if not authorization.startswith("tma "):
        raise HTTPException(status_code=401, detail="Invalid authorization")
    
    init_data = authorization[4:]
    user_data = validate_telegram_init_data(init_data, BOT_TOKEN)
    
    if RUN_WITHOUT_DB or pool is None:
        # Keep per-user profile in memory for local testing without PostgreSQL.
        tg_id = int(user_data.get('id', 123))
        print(f"🔐 Authorization request: Telegram ID = {tg_id}, first_name = {user_data.get('first_name')}")
        profile = mock_profiles.get(tg_id)
        if not profile:
            profile = {
                "id": tg_id,
                "telegram_id": tg_id,
                "first_name": user_data.get('first_name', 'TestUser'),
                "bio": "",
                "city": "",
                "country": "",
                "gender": "female",
                "is_premium": False,
                "profile_completed": False,
                "location": None,
            }
            mock_profiles[tg_id] = profile
            print(f"✨ New profile created for ID {tg_id}")
        else:
            if user_data.get('first_name') and not profile.get('first_name'):
                profile['first_name'] = user_data.get('first_name')
            print(f"♻️  Returning existing profile for ID {tg_id}: country={profile.get('country')}, city={profile.get('city')}")
        return dict(profile)
    
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1",
            user_data['id']
        )
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return dict(user)


@app.on_event("startup")
async def startup():
    global db_pool
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Please provide it in .env")
    
    if RUN_WITHOUT_DB:
        print("⚠️  RUN_WITHOUT_DB=true: Backend API will use mock data (for testing only)")
    else:
        # Keep pool small on free instances to reduce cold-start time.
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        await _ensure_notification_settings_table(db_pool)


@app.on_event("shutdown")
async def shutdown():
    if db_pool:
        await db_pool.close()
    if bot:
        await bot.session.close()


@app.get("/api/health")
async def healthcheck(pool: asyncpg.Pool = Depends(get_db_pool)):
    if RUN_WITHOUT_DB or pool is None:
        return {"status": "ok", "mode": "mock"}
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok", "mode": "database"}


@app.post("/api/auth/validate")
async def validate_auth(request: Request):
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN is not configured")

    body = await request.json()
    init_data = body.get("initData", "")
    user = validate_telegram_init_data(init_data, BOT_TOKEN)
    return {"ok": True, "user": user}


@app.get("/api/discover")
async def get_discover_users(
    limit: int = 20,
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Get potential matches."""
    safe_limit = max(1, min(limit, 50))
    current_country = (current_user.get('country') or '').strip()
    current_city = (current_user.get('city') or '').strip()
    looking_for = (current_user.get('looking_for') or 'everyone').strip().lower()
    age_min = int(current_user.get('age_min') or 18)
    age_max = int(current_user.get('age_max') or 99)
    max_distance = int(current_user.get('max_distance') or 50)
    if age_min > age_max:
        age_min, age_max = age_max, age_min
    max_distance = max(1, min(max_distance, 500))

    # Require country to avoid showing unrelated global profiles.
    if not current_country:
        print(f"❌ User {current_user['id']} has no country in profile")
        return {"users": []}
    
    if RUN_WITHOUT_DB or pool is None:
        # Return other in-memory profiles with same city/country for local tests.
        print(f"🔍 Discovering for user {current_user['id']} (country='{current_country}', city='{current_city}')")
        candidate_users = []
        for user_id, profile in mock_profiles.items():
            print(f"  Checking user {user_id}: country='{profile.get('country')}', city='{profile.get('city')}', completed={profile.get('profile_completed')}")
            if user_id == int(current_user['id']):
                print(f"    ❌ Skip: same user")
                continue
            if not profile.get('profile_completed'):
                print(f"    ❌ Skip: profile not completed")
                continue
            if (profile.get('country') or '').strip().lower() != current_country.lower():
                print(f"    ❌ Skip: country mismatch")
                continue
            if current_city and (profile.get('city') or '').strip().lower() != current_city.lower():
                print(f"    ❌ Skip: city mismatch")
                continue
            if looking_for != 'everyone' and (profile.get('gender') or '').strip().lower() != looking_for:
                print(f"    ❌ Skip: gender mismatch")
                continue
            print(f"    ✅ Match found!")
            candidate_users.append(
                {
                    "id": profile['id'],
                    "first_name": profile.get('first_name') or 'User',
                    "age": 25,
                    "bio": profile.get('bio') or "",
                    "photos_urls": [],
                    "interests": ["💬 Chat"],
                    "city": profile.get('city') or '',
                    "country": profile.get('country') or '',
                    "gender": profile.get('gender') or 'female',
                    "distance": 5.0,
                }
            )

        candidate_users.sort(
            key=lambda u: (
                0 if mock_swipes.get((int(u['id']), int(current_user['id']))) in {'like', 'superlike'} else 1,
                int(u['id']),
            )
        )
        print(f"📊 Found {len(candidate_users)} matches for user {current_user['id']}")
        return {
            "users": candidate_users[:safe_limit]
        }
    
    async with pool.acquire() as conn:
        if current_user.get('location'):
            query = """
            SELECT
                u.id,
                u.first_name,
                u.bio,
                u.photos_urls,
                u.interests,
                u.city,
                u.country,
                u.gender,
                EXTRACT(YEAR FROM AGE(u.birthdate)) as age,
                ST_Distance(u.location::geography, $2::geography) / 1000 as distance
            FROM users u
            WHERE u.id != $1
                AND u.is_active = TRUE
                AND u.is_blocked = FALSE
                AND u.profile_completed = TRUE
                AND u.location IS NOT NULL
                AND ST_Distance(u.location::geography, $2::geography) / 1000 <= $6
                AND ($7::text = 'everyone' OR LOWER(COALESCE(u.gender, '')) = LOWER($7))
                AND EXTRACT(YEAR FROM AGE(u.birthdate)) BETWEEN $8 AND $9
                AND ($4::text = '' OR LOWER(COALESCE(u.country, '')) = LOWER($4))
                AND ($5::text = '' OR LOWER(COALESCE(u.city, '')) = LOWER($5))
                AND u.first_name IS NOT NULL AND u.first_name <> ''
                AND u.birthdate IS NOT NULL
                AND u.gender IS NOT NULL AND u.gender <> ''
                AND u.city IS NOT NULL AND u.city <> ''
                AND u.country IS NOT NULL AND u.country <> ''
                AND NOT EXISTS (
                    SELECT 1 FROM swipes s
                    WHERE s.from_user_id = $1 AND s.to_user_id = u.id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM user_blocks b
                    WHERE (b.blocker_id = $1 AND b.blocked_id = u.id)
                         OR (b.blocker_id = u.id AND b.blocked_id = $1)
                )
            ORDER BY distance ASC
            LIMIT $3
            """
            users = await conn.fetch(
                query,
                current_user['id'],
                current_user['location'],
                safe_limit,
                current_country,
                current_city,
                max_distance,
                looking_for,
                age_min,
                age_max,
            )
        else:
            query = """
            SELECT
                u.id,
                u.first_name,
                u.bio,
                u.photos_urls,
                u.interests,
                u.city,
                u.country,
                u.gender,
                EXTRACT(YEAR FROM AGE(u.birthdate)) as age,
                NULL::float as distance
            FROM users u
            WHERE u.id != $1
                AND u.is_active = TRUE
                AND u.is_blocked = FALSE
                AND u.profile_completed = TRUE
                AND ($3::text = '' OR LOWER(COALESCE(u.country, '')) = LOWER($3))
                AND ($4::text = '' OR LOWER(COALESCE(u.city, '')) = LOWER($4))
                AND ($5::text = 'everyone' OR LOWER(COALESCE(u.gender, '')) = LOWER($5))
                AND EXTRACT(YEAR FROM AGE(u.birthdate)) BETWEEN $6 AND $7
                AND u.first_name IS NOT NULL AND u.first_name <> ''
                AND u.birthdate IS NOT NULL
                AND u.gender IS NOT NULL AND u.gender <> ''
                AND u.city IS NOT NULL AND u.city <> ''
                AND u.country IS NOT NULL AND u.country <> ''
                AND NOT EXISTS (
                    SELECT 1 FROM swipes s
                    WHERE s.from_user_id = $1 AND s.to_user_id = u.id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM user_blocks b
                    WHERE (b.blocker_id = $1 AND b.blocked_id = u.id)
                         OR (b.blocker_id = u.id AND b.blocked_id = $1)
                )
            ORDER BY u.last_active DESC NULLS LAST
            LIMIT $2
            """
            users = await conn.fetch(
                query,
                current_user['id'],
                safe_limit,
                current_country,
                current_city,
                looking_for,
                age_min,
                age_max,
            )

    return {
        "users": [
            {
                "id": u['id'],
                "first_name": u['first_name'],
                "age": u['age'],
                "bio": u['bio'],
                "photos_urls": u['photos_urls'] or [],
                "interests": u['interests'] or [],
                "city": u['city'],
                "country": u['country'],
                "gender": u['gender'],
                "distance": round(u['distance'], 1) if u['distance'] is not None else None,
            }
            for u in users
        ]
    }


@app.post("/api/swipe", response_model=SwipeResponse)
async def create_swipe(
    swipe: SwipeRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Record swipe and check for match."""
    if swipe.swipe_type not in {"like", "dislike", "superlike"}:
        raise HTTPException(status_code=400, detail="Invalid swipe type")

    if swipe.target_user_id == current_user['id']:
        raise HTTPException(status_code=400, detail="Cannot swipe yourself")

    _check_rate_limit(current_user['id'], "swipe", limit=80, window_sec=60)

    idem_key = (request.headers.get("Idempotency-Key") or "").strip()
    if idem_key:
        cached = _idempotency_get(current_user['id'], f"swipe:{idem_key}")
        if cached is not None:
            return JSONResponse(content=cached)

    if RUN_WITHOUT_DB or pool is None:
        global mock_next_match_id

        target_profile = mock_profiles.get(int(swipe.target_user_id))
        if not target_profile:
            raise HTTPException(status_code=404, detail="Target user not found")

        mock_swipes[(int(current_user['id']), int(swipe.target_user_id))] = swipe.swipe_type

        is_match = False
        if swipe.swipe_type in {'like', 'superlike'}:
            reverse_swipe = mock_swipes.get((int(swipe.target_user_id), int(current_user['id'])))
            if reverse_swipe in {'like', 'superlike'}:
                match_key = _mock_match_key(int(current_user['id']), int(swipe.target_user_id))
                existing = mock_matches.get(match_key)
                if existing:
                    existing['is_active'] = True
                else:
                    mock_matches[match_key] = {
                        "id": mock_next_match_id,
                        "user1_id": match_key[0],
                        "user2_id": match_key[1],
                        "is_active": True,
                        "matched_at": _now_iso(),
                        "last_message_at": None,
                    }
                    mock_messages[mock_next_match_id] = []
                    mock_next_match_id += 1
                is_match = True

        payload = {
            "is_match": is_match,
            "matched_user": {
                "name": target_profile.get('first_name') or f"User{swipe.target_user_id}",
                "city": target_profile.get('city') or '',
            } if is_match else None,
        }
        if idem_key:
            _idempotency_set(current_user['id'], f"swipe:{idem_key}", payload)
        return SwipeResponse(**payload)

    async with pool.acquire() as conn:
        is_blocked_pair = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM user_blocks
                WHERE (blocker_id = $1 AND blocked_id = $2)
                   OR (blocker_id = $2 AND blocked_id = $1)
            )
            """,
            current_user['id'], swipe.target_user_id,
        )
        if is_blocked_pair:
            raise HTTPException(status_code=403, detail="Interaction is blocked")

        existing = await conn.fetchrow(
            "SELECT swipe_type FROM swipes WHERE from_user_id = $1 AND to_user_id = $2",
            current_user['id'], swipe.target_user_id
        )

        await conn.execute(
            """
            INSERT INTO swipes (from_user_id, to_user_id, swipe_type)
            VALUES ($1, $2, $3)
            ON CONFLICT (from_user_id, to_user_id)
            DO UPDATE SET swipe_type = EXCLUDED.swipe_type
            """,
            current_user['id'], swipe.target_user_id, swipe.swipe_type
        )

        actor = await conn.fetchrow(
            "SELECT telegram_id, first_name FROM users WHERE id = $1",
            current_user['id']
        )
        target = await conn.fetchrow(
            "SELECT telegram_id, first_name FROM users WHERE id = $1",
            swipe.target_user_id
        )

        like_started = swipe.swipe_type in ['like', 'superlike'] and (existing is None or existing['swipe_type'] not in ['like', 'superlike'])
        if like_started and actor and target and await _is_notification_enabled(conn, swipe.target_user_id, "like"):
            await send_like_notification(
                target['telegram_id'],
                actor['first_name'] or "Someone",
                swipe.swipe_type
            )

        if swipe.swipe_type in ['like', 'superlike']:
            reverse = await conn.fetchrow(
                "SELECT 1 FROM swipes WHERE from_user_id = $1 AND to_user_id = $2 AND swipe_type IN ('like', 'superlike')",
                swipe.target_user_id, current_user['id']
            )

            if reverse:
                u1 = min(current_user['id'], swipe.target_user_id)
                u2 = max(current_user['id'], swipe.target_user_id)
                await conn.execute(
                    """
                    INSERT INTO matches (user1_id, user2_id, is_active)
                    VALUES ($1, $2, TRUE)
                    ON CONFLICT (user1_id, user2_id)
                    DO UPDATE SET is_active = TRUE
                    """,
                    u1, u2
                )

                if actor and target:
                    send_to_actor = await _is_notification_enabled(conn, current_user['id'], "match")
                    send_to_target = await _is_notification_enabled(conn, swipe.target_user_id, "match")
                    await send_match_notification(
                        actor['telegram_id'],
                        target['telegram_id'],
                        actor['first_name'] or "You",
                        target['first_name'] or "User",
                        send_to_actor,
                        send_to_target,
                    )

                payload = {
                    "is_match": True,
                    "matched_user": {"name": (target['first_name'] if target else "User")},
                }
                if idem_key:
                    _idempotency_set(current_user['id'], f"swipe:{idem_key}", payload)
                return SwipeResponse(**payload)

        payload = {"is_match": False, "matched_user": None}
        if idem_key:
            _idempotency_set(current_user['id'], f"swipe:{idem_key}", payload)
        return SwipeResponse(**payload)


@app.get("/api/notifications/summary", response_model=NotificationSummary)
async def notifications_summary(
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Return real-time counts for likes, matches, and unread messages."""
    if RUN_WITHOUT_DB or pool is None:
        user_id = int(current_user['id'])
        likes = sum(
            1
            for (from_user, to_user), swipe_type in mock_swipes.items()
            if to_user == user_id and from_user != user_id and swipe_type in {'like', 'superlike'}
        )
        active_matches = [
            m for m in mock_matches.values()
            if m.get('is_active') and _mock_match_contains(m, user_id)
        ]
        unread = 0
        for m in active_matches:
            msgs = mock_messages.get(int(m['id']), [])
            unread += sum(
                1
                for msg in msgs
                if int(msg.get('from_user_id', 0)) != user_id
                and int(msg.get('to_user_id', 0)) == user_id
                and not bool(msg.get('is_read'))
            )
        return NotificationSummary(likes=likes, matches=len(active_matches), unread_messages=unread)

    async with pool.acquire() as conn:
        likes = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM swipes
            WHERE to_user_id = $1
              AND swipe_type IN ('like', 'superlike')
            """,
            current_user['id']
        )
        matches = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM matches
            WHERE is_active = TRUE
              AND (user1_id = $1 OR user2_id = $1)
            """,
            current_user['id']
        )
        unread = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM messages m
            JOIN matches mt ON mt.id = m.match_id
            WHERE m.is_read = FALSE
              AND m.from_user_id != $1
              AND mt.is_active = TRUE
              AND (mt.user1_id = $1 OR mt.user2_id = $1)
            """,
            current_user['id']
        )

    return NotificationSummary(
        likes=int(likes or 0),
        matches=int(matches or 0),
        unread_messages=int(unread or 0),
    )


@app.post("/api/messages", response_model=MessageCreateResponse)
async def create_message(
    payload: MessageCreateRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Create message in a match and notify receiver in Telegram."""
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Message content is empty")

    _check_rate_limit(current_user['id'], "messages", limit=120, window_sec=60)

    idem_key = (request.headers.get("Idempotency-Key") or "").strip()
    if idem_key:
        cached = _idempotency_get(current_user['id'], f"msg:{idem_key}")
        if cached is not None:
            return JSONResponse(content=cached)

    if RUN_WITHOUT_DB or pool is None:
        global mock_next_message_id

        user_id = int(current_user['id'])
        match_row = next((m for m in mock_matches.values() if int(m['id']) == int(payload.match_id)), None)
        if not match_row or not match_row.get('is_active'):
            raise HTTPException(status_code=404, detail="Match not found")
        if not _mock_match_contains(match_row, user_id):
            raise HTTPException(status_code=403, detail="Not your match")

        to_user_id = _mock_other_user(match_row, user_id)
        created_at = _now_iso()
        message = {
            "id": mock_next_message_id,
            "match_id": int(payload.match_id),
            "from_user_id": user_id,
            "to_user_id": int(to_user_id),
            "content": payload.content.strip(),
            "message_type": payload.message_type,
            "is_read": False,
            "created_at": created_at,
        }
        mock_messages.setdefault(int(payload.match_id), []).append(message)
        match_row['last_message_at'] = created_at
        payload_resp = {"id": int(mock_next_message_id), "created_at": created_at}
        mock_next_message_id += 1
        if idem_key:
            _idempotency_set(current_user['id'], f"msg:{idem_key}", payload_resp)
        return MessageCreateResponse(**payload_resp)

    async with pool.acquire() as conn:
        match_row = await conn.fetchrow(
            """
            SELECT id, user1_id, user2_id, is_active
            FROM matches
            WHERE id = $1
            """,
            payload.match_id
        )
        if not match_row or not match_row['is_active']:
            raise HTTPException(status_code=404, detail="Match not found")

        if current_user['id'] not in (match_row['user1_id'], match_row['user2_id']):
            raise HTTPException(status_code=403, detail="Not your match")

        receiver_id = match_row['user2_id'] if current_user['id'] == match_row['user1_id'] else match_row['user1_id']

        inserted = await conn.fetchrow(
            """
            INSERT INTO messages (match_id, from_user_id, message_type, content)
            VALUES ($1, $2, $3, $4)
            RETURNING id, created_at
            """,
            payload.match_id,
            current_user['id'],
            payload.message_type,
            payload.content.strip(),
        )

        await conn.execute(
            "UPDATE matches SET last_message_at = CURRENT_TIMESTAMP WHERE id = $1",
            payload.match_id,
        )

        sender = await conn.fetchrow(
            "SELECT first_name FROM users WHERE id = $1",
            current_user['id'],
        )
        receiver = await conn.fetchrow(
            "SELECT telegram_id FROM users WHERE id = $1",
            receiver_id,
        )

        if receiver and await _is_notification_enabled(conn, receiver_id, "message"):
            await send_message_notification(
                receiver['telegram_id'],
                (sender['first_name'] if sender else "Someone"),
                payload.content.strip(),
            )

    payload_resp = {"id": int(inserted['id']), "created_at": str(inserted['created_at'])}
    if idem_key:
        _idempotency_set(current_user['id'], f"msg:{idem_key}", payload_resp)
    return MessageCreateResponse(**payload_resp)


@app.post("/api/messages/direct", response_model=MessageCreateResponse)
async def create_direct_message(
    payload: DirectMessageCreateRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Create message by target user id (helper for mini-app match popup)."""
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Message content is empty")

    if payload.target_user_id == current_user['id']:
        raise HTTPException(status_code=400, detail="Cannot message yourself")

    _check_rate_limit(current_user['id'], "messages", limit=120, window_sec=60)

    idem_key = (request.headers.get("Idempotency-Key") or "").strip()
    if idem_key:
        cached = _idempotency_get(current_user['id'], f"msgdirect:{idem_key}")
        if cached is not None:
            return JSONResponse(content=cached)

    if RUN_WITHOUT_DB or pool is None:
        global mock_next_message_id

        user_id = int(current_user['id'])
        target_user_id = int(payload.target_user_id)
        match_key = _mock_match_key(user_id, target_user_id)
        match_row = mock_matches.get(match_key)
        if not match_row or not match_row.get('is_active'):
            raise HTTPException(status_code=403, detail="No active match with this user")

        created_at = _now_iso()
        message = {
            "id": mock_next_message_id,
            "match_id": int(match_row['id']),
            "from_user_id": user_id,
            "to_user_id": target_user_id,
            "content": payload.content.strip(),
            "message_type": payload.message_type,
            "is_read": False,
            "created_at": created_at,
        }
        mock_messages.setdefault(int(match_row['id']), []).append(message)
        match_row['last_message_at'] = created_at
        payload_resp = {"id": int(mock_next_message_id), "created_at": created_at}
        mock_next_message_id += 1
        if idem_key:
            _idempotency_set(current_user['id'], f"msgdirect:{idem_key}", payload_resp)
        return MessageCreateResponse(**payload_resp)

    async with pool.acquire() as conn:
        u1 = min(current_user['id'], payload.target_user_id)
        u2 = max(current_user['id'], payload.target_user_id)

        match_row = await conn.fetchrow(
            """
            SELECT id, is_active
            FROM matches
            WHERE user1_id = $1 AND user2_id = $2
            """,
            u1,
            u2,
        )
        if not match_row or not match_row['is_active']:
            raise HTTPException(status_code=403, detail="No active match with this user")

        inserted = await conn.fetchrow(
            """
            INSERT INTO messages (match_id, from_user_id, message_type, content)
            VALUES ($1, $2, $3, $4)
            RETURNING id, created_at
            """,
            match_row['id'],
            current_user['id'],
            payload.message_type,
            payload.content.strip(),
        )

        await conn.execute(
            "UPDATE matches SET last_message_at = CURRENT_TIMESTAMP WHERE id = $1",
            match_row['id'],
        )

        sender = await conn.fetchrow(
            "SELECT first_name FROM users WHERE id = $1",
            current_user['id'],
        )
        receiver = await conn.fetchrow(
            "SELECT telegram_id FROM users WHERE id = $1",
            payload.target_user_id,
        )

        if receiver and await _is_notification_enabled(conn, payload.target_user_id, "message"):
            await send_message_notification(
                receiver['telegram_id'],
                (sender['first_name'] if sender else "Someone"),
                payload.content.strip(),
            )

    payload_resp = {"id": int(inserted['id']), "created_at": str(inserted['created_at'])}
    if idem_key:
        _idempotency_set(current_user['id'], f"msgdirect:{idem_key}", payload_resp)
    return MessageCreateResponse(**payload_resp)


@app.get("/api/matches", response_model=MatchListResponse)
async def get_matches(
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Return active matches with last message preview and unread counts."""
    if RUN_WITHOUT_DB or pool is None:
        user_id = int(current_user['id'])
        items: list[MatchItem] = []
        for m in mock_matches.values():
            if not m.get('is_active') or not _mock_match_contains(m, user_id):
                continue
            other_id = _mock_other_user(m, user_id)
            other_profile = mock_profiles.get(other_id, {})
            msgs = mock_messages.get(int(m['id']), [])
            last_message = msgs[-1]['content'] if msgs else None
            unread_count = sum(
                1
                for msg in msgs
                if int(msg.get('to_user_id', 0)) == user_id and not bool(msg.get('is_read'))
            )
            items.append(
                MatchItem(
                    match_id=int(m['id']),
                    user_id=other_id,
                    first_name=other_profile.get('first_name') or f"User{other_id}",
                    city=other_profile.get('city'),
                    primary_photo_url=None,
                    last_message=last_message,
                    last_message_at=m.get('last_message_at') or m.get('matched_at'),
                    unread_count=unread_count,
                )
            )
        items.sort(key=lambda it: (it.last_message_at or ''), reverse=True)
        return MatchListResponse(items=items)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                m.id AS match_id,
                p.id AS user_id,
                p.first_name,
                p.city,
                p.primary_photo_url,
                lm.content AS last_message,
                COALESCE(m.last_message_at, m.matched_at) AS last_message_at,
                (
                    SELECT COUNT(*) FROM messages mm
                    WHERE mm.match_id = m.id
                      AND mm.from_user_id != $1
                      AND mm.is_read = FALSE
                ) AS unread_count
            FROM matches m
            JOIN users p ON p.id = CASE WHEN m.user1_id = $1 THEN m.user2_id ELSE m.user1_id END
            LEFT JOIN LATERAL (
                SELECT content
                FROM messages x
                WHERE x.match_id = m.id
                ORDER BY x.id DESC
                LIMIT 1
            ) lm ON TRUE
            WHERE m.is_active = TRUE
              AND (m.user1_id = $1 OR m.user2_id = $1)
            ORDER BY COALESCE(m.last_message_at, m.matched_at) DESC
            """,
            current_user['id'],
        )

    items = [
        MatchItem(
            match_id=int(r['match_id']),
            user_id=int(r['user_id']),
            first_name=r['first_name'] or "User",
            city=r['city'],
            primary_photo_url=r['primary_photo_url'],
            last_message=r['last_message'],
            last_message_at=str(r['last_message_at']) if r['last_message_at'] else None,
            unread_count=int(r['unread_count'] or 0),
        )
        for r in rows
    ]
    return MatchListResponse(items=items)


@app.get("/api/matches/{match_id}/messages", response_model=MessageListResponse)
async def get_match_messages(
    match_id: int,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Return chat history for a specific match."""
    safe_limit = max(1, min(limit, 200))
    if RUN_WITHOUT_DB or pool is None:
        user_id = int(current_user['id'])
        match_row = next((m for m in mock_matches.values() if int(m['id']) == int(match_id)), None)
        if not match_row or not match_row.get('is_active') or not _mock_match_contains(match_row, user_id):
            raise HTTPException(status_code=404, detail="Match not found")

        msgs = mock_messages.get(int(match_id), [])
        items = [
            MessageItem(
                id=int(msg['id']),
                match_id=int(msg['match_id']),
                from_user_id=int(msg['from_user_id']),
                content=msg.get('content'),
                message_type=msg.get('message_type') or 'text',
                is_read=bool(msg.get('is_read')),
                created_at=str(msg.get('created_at')),
            )
            for msg in msgs[-safe_limit:]
        ]
        return MessageListResponse(items=items)

    async with pool.acquire() as conn:
        owns_match = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM matches
                WHERE id = $1
                  AND is_active = TRUE
                  AND (user1_id = $2 OR user2_id = $2)
            )
            """,
            match_id,
            current_user['id'],
        )
        if not owns_match:
            raise HTTPException(status_code=404, detail="Match not found")

        rows = await conn.fetch(
            """
            SELECT id, match_id, from_user_id, content, message_type, is_read, created_at
            FROM messages
            WHERE match_id = $1
            ORDER BY id DESC
            LIMIT $2
            """,
            match_id,
            safe_limit,
        )

    items = [
        MessageItem(
            id=int(r['id']),
            match_id=int(r['match_id']),
            from_user_id=int(r['from_user_id']),
            content=r['content'],
            message_type=r['message_type'] or 'text',
            is_read=bool(r['is_read']),
            created_at=str(r['created_at']),
        )
        for r in reversed(rows)
    ]
    return MessageListResponse(items=items)


@app.post("/api/matches/{match_id}/read")
async def mark_match_read(
    match_id: int,
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Mark all incoming messages in match as read for current user."""
    if RUN_WITHOUT_DB or pool is None:
        user_id = int(current_user['id'])
        match_row = next((m for m in mock_matches.values() if int(m['id']) == int(match_id)), None)
        if not match_row or not match_row.get('is_active') or not _mock_match_contains(match_row, user_id):
            raise HTTPException(status_code=404, detail="Match not found")

        for msg in mock_messages.get(int(match_id), []):
            if int(msg.get('to_user_id', 0)) == user_id and int(msg.get('from_user_id', 0)) != user_id:
                msg['is_read'] = True
        return {"success": True}

    async with pool.acquire() as conn:
        owns_match = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM matches
                WHERE id = $1
                  AND is_active = TRUE
                  AND (user1_id = $2 OR user2_id = $2)
            )
            """,
            match_id,
            current_user['id'],
        )
        if not owns_match:
            raise HTTPException(status_code=404, detail="Match not found")

        await conn.execute(
            """
            UPDATE messages
            SET is_read = TRUE, read_at = CURRENT_TIMESTAMP
            WHERE match_id = $1
              AND from_user_id != $2
              AND is_read = FALSE
            """,
            match_id,
            current_user['id'],
        )

    return {"success": True}


@app.get("/api/profile")
async def get_profile(
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Get current user profile for editing screen."""
    if RUN_WITHOUT_DB or pool is None:
        profile = mock_profiles.get(int(current_user['id']), dict(current_user))
        return {
            "id": profile.get('id', current_user['id']),
            "first_name": profile.get('first_name', current_user.get('first_name', 'User')),
            "age": int(profile.get('age', 25) or 25),
            "bio": profile.get('bio', ''),
            "city": profile.get('city', ''),
            "country": profile.get('country', ''),
            "gender": profile.get('gender', 'female'),
            "is_premium": bool(profile.get('is_premium', False)),
        }

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id,
                   first_name,
                   EXTRACT(YEAR FROM AGE(birthdate))::int AS age,
                   bio,
                   city,
                   country,
                   gender,
                   is_premium
            FROM users
            WHERE id = $1
            """,
            current_user['id'],
        )
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(row)


@app.put("/api/profile")
async def update_profile(
    payload: ProfileUpdateRequest,
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Update editable profile fields."""
    if RUN_WITHOUT_DB or pool is None:
        user_id = int(current_user['id'])
        profile = mock_profiles.get(user_id, dict(current_user))
        if payload.first_name is not None:
            profile['first_name'] = payload.first_name
        if payload.age is not None:
            profile['age'] = max(18, min(int(payload.age), 80))
        if payload.bio is not None:
            profile['bio'] = payload.bio
        if payload.city is not None:
            profile['city'] = payload.city
        if payload.country is not None:
            profile['country'] = payload.country
        if payload.gender is not None:
            profile['gender'] = payload.gender
        if payload.is_premium is not None:
            profile['is_premium'] = payload.is_premium
        profile['id'] = user_id
        profile['telegram_id'] = user_id
        profile['profile_completed'] = bool((profile.get('country') or '').strip() and (profile.get('city') or '').strip())
        profile['location'] = None
        mock_profiles[user_id] = profile
        print(f"✅ Profile updated for user {user_id}: country={profile.get('country')}, city={profile.get('city')}, completed={profile['profile_completed']}")
        print(f"📊 All profiles in memory: {[(uid, p.get('country'), p.get('city'), p.get('profile_completed')) for uid, p in mock_profiles.items()]}")
        return {"success": True}

    if payload.age is not None and not (18 <= int(payload.age) <= 80):
        raise HTTPException(status_code=400, detail="Invalid age")

    if payload.gender and payload.gender not in {"male", "female", "other"}:
        raise HTTPException(status_code=400, detail="Invalid gender")

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE users
            SET
                first_name = COALESCE($1, first_name),
                birthdate = COALESCE(
                    CASE
                        WHEN $2::int IS NULL THEN NULL
                        ELSE make_date(EXTRACT(YEAR FROM CURRENT_DATE)::int - $2::int, 1, 1)
                    END,
                    birthdate
                ),
                bio = COALESCE($3, bio),
                city = COALESCE($4, city),
                country = COALESCE($5, country),
                gender = COALESCE($6, gender),
                is_premium = COALESCE($7, is_premium),
                profile_completed = (
                    LENGTH(TRIM(COALESCE($4, city, ''))) > 0
                    AND LENGTH(TRIM(COALESCE($5, country, ''))) > 0
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $8
            """,
            payload.first_name,
            payload.age,
            payload.bio,
            payload.city,
            payload.country,
            payload.gender,
            payload.is_premium,
            current_user['id'],
        )
    return {"success": True}


@app.get("/api/settings/notifications")
async def get_notification_settings(
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Return user notification preferences."""
    if RUN_WITHOUT_DB or pool is None:
        return {"like_enabled": True, "match_enabled": True, "message_enabled": True}

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT like_enabled, match_enabled, message_enabled
            FROM notification_settings
            WHERE user_id = $1
            """,
            current_user['id'],
        )
    if not row:
        return {"like_enabled": True, "match_enabled": True, "message_enabled": True}
    return dict(row)


@app.put("/api/settings/notifications")
async def update_notification_settings(
    payload: NotificationSettingsUpdate,
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Update user notification preferences."""
    if RUN_WITHOUT_DB or pool is None:
        return {"success": True}

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO notification_settings (user_id, like_enabled, match_enabled, message_enabled)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id)
            DO UPDATE SET
                like_enabled = EXCLUDED.like_enabled,
                match_enabled = EXCLUDED.match_enabled,
                message_enabled = EXCLUDED.message_enabled,
                updated_at = CURRENT_TIMESTAMP
            """,
            current_user['id'],
            payload.like_enabled,
            payload.match_enabled,
            payload.message_enabled,
        )
    return {"success": True}


@app.post("/api/users/{target_user_id}/block")
async def block_user(
    target_user_id: int,
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Block another user and deactivate match if present."""
    if target_user_id == current_user['id']:
        raise HTTPException(status_code=400, detail="Cannot block yourself")
    if RUN_WITHOUT_DB or pool is None:
        return {"success": True}

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_blocks (blocker_id, blocked_id)
            VALUES ($1, $2)
            ON CONFLICT (blocker_id, blocked_id) DO NOTHING
            """,
            current_user['id'],
            target_user_id,
        )
        u1 = min(current_user['id'], target_user_id)
        u2 = max(current_user['id'], target_user_id)
        await conn.execute(
            """
            UPDATE matches
            SET is_active = FALSE
            WHERE user1_id = $1 AND user2_id = $2
            """,
            u1,
            u2,
        )
    return {"success": True}


@app.post("/api/users/{target_user_id}/report")
async def report_user(
    target_user_id: int,
    payload: ReportRequest,
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Report user for moderation."""
    allowed = {"inappropriate", "spam", "fake", "harassment", "other"}
    if payload.reason not in allowed:
        raise HTTPException(status_code=400, detail="Invalid report reason")
    if target_user_id == current_user['id']:
        raise HTTPException(status_code=400, detail="Cannot report yourself")
    if RUN_WITHOUT_DB or pool is None:
        return {"success": True}

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO reports (reporter_id, reported_id, reason, details)
            VALUES ($1, $2, $3, $4)
            """,
            current_user['id'],
            target_user_id,
            payload.reason,
            payload.details,
        )
    return {"success": True}


async def send_like_notification(receiver_tid: int, actor_name: str, swipe_type: str):
    """Send real-time like/superlike notification."""
    if not bot:
        return
    emoji = "⭐" if swipe_type == "superlike" else "❤️"
    text = (
        f"{emoji} Новый интерес!\n\n"
        f"{actor_name} проявил(а) к вам симпатию.\n"
        "Откройте мини-приложение, чтобы посмотреть профиль."
    )
    try:
        await bot.send_message(receiver_tid, text)
    except Exception:
        return


async def send_match_notification(user1_tid, user2_tid, user1_name, user2_name, send_to_user1: bool = True, send_to_user2: bool = True):
    """Send match notifications via Telegram bot."""
    if not bot:
        return

    message = f"🎉 Совпадение!\n\nВы и {user2_name} понравились друг другу."
    try:
        if send_to_user1:
            await bot.send_message(user1_tid, message)
        if send_to_user2:
            await bot.send_message(user2_tid, f"🎉 Совпадение!\n\nВы и {user1_name} понравились друг другу.")
    except Exception:
        # Notification failure should not break swipe API response.
        return


async def send_message_notification(receiver_tid: int, sender_name: str, text: str):
    """Send real-time new message notification."""
    if not bot:
        return
    preview = text if len(text) <= 80 else (text[:77] + "...")
    body = (
        f"💬 Новое сообщение от {sender_name}\n\n"
        f"{preview}"
    )
    try:
        await bot.send_message(receiver_tid, body)
    except Exception:
        return


app.include_router(create_filters_router(get_current_user, get_db_pool))
