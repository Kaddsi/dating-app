"""Premium Dating API: discover, swipes, auth, and notifications."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import time
import hashlib
import hmac
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode
from urllib.request import urlopen

import asyncpg
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
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
mock_room_messages: dict[str, list[dict[str, Any]]] = defaultdict(list)
mock_next_match_id = 1
mock_next_message_id = 1
mock_next_room_message_id = 1

GAMING_ROOMS: dict[str, dict[str, str]] = {
    "dota2": {
        "title": "Dota 2",
        "description": "Ищите тиммейтов, собирайте стак и обсуждайте катки без токсика.",
    },
    "cs2": {
        "title": "CS2",
        "description": "Собирайте пати, зовите на премьер и общайтесь по игре в дружелюбной атмосфере.",
    },
}

VOICE_PREFIX = "__voice__:"
GIFT_PREFIX = "__gift__:"
MAX_VOICE_PAYLOAD_LEN = 900_000
STARS_GIFT_CATALOG: dict[str, dict[str, Any]] = {
    "rose": {"title": "Rose", "stars": 10, "description": "A lovely rose for your match."},
    "heart": {"title": "Heart", "stars": 15, "description": "A warm heart gift."},
    "crown": {"title": "Crown", "stars": 25, "description": "A premium crown gift."},
}

PROFANITY_MARKERS = {
    "бля", "бляд", "хуй", "хуе", "пизд", "еба", "ебл", "сука", "мраз", "нах", "пошел нах",
    "fuck", "fck", "shit", "bitch", "cunt", "motherf",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mock_match_key(user_a: int, user_b: int) -> tuple[int, int]:
    return (min(user_a, user_b), max(user_a, user_b))


def _mock_match_contains(match_row: dict[str, Any], user_id: int) -> bool:
    return user_id in (int(match_row['user1_id']), int(match_row['user2_id']))


def _mock_other_user(match_row: dict[str, Any], user_id: int) -> int:
    return int(match_row['user2_id']) if int(match_row['user1_id']) == int(user_id) else int(match_row['user1_id'])


def _normalize_text(text: str) -> str:
    return "".join(ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in text)


def _contains_profanity(text: str) -> bool:
    normalized = _normalize_text(text)
    squashed = normalized.replace(" ", "")
    return any(marker in normalized or marker.replace(" ", "") in squashed for marker in PROFANITY_MARKERS)


def _message_preview_for_notification(content: str) -> str:
    trimmed = (content or "").strip()
    if trimmed.startswith(VOICE_PREFIX):
        return "🎤 Voice message"
    if trimmed.startswith(GIFT_PREFIX):
        return "🎁 Gift sent"
    return trimmed


def _validate_message_payload(content: str) -> str:
    trimmed = (content or "").strip()
    if not trimmed:
        raise HTTPException(status_code=400, detail="Message content is empty")
    if trimmed.startswith(VOICE_PREFIX) and len(trimmed) > MAX_VOICE_PAYLOAD_LEN:
        raise HTTPException(status_code=413, detail="Voice message is too large")
    if not trimmed.startswith(VOICE_PREFIX) and not trimmed.startswith(GIFT_PREFIX):
        if _contains_profanity(trimmed):
            raise HTTPException(status_code=400, detail="Please keep the chat respectful")
    return trimmed


def _get_room_info(room_slug: str) -> dict[str, str]:
    room = GAMING_ROOMS.get(room_slug)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    return room


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


class GiftInvoiceRequest(BaseModel):
    match_id: int
    gift_slug: str = "rose"


class GiftInvoiceResponse(BaseModel):
    invoice_link: str
    gift_slug: str
    stars: int
    title: str


class GamingRoomMessageCreateRequest(BaseModel):
    content: str


class GamingRoomMessageItem(BaseModel):
    id: int
    room_slug: str
    sender_name: str
    content: str
    created_at: str
    is_own: bool = False


class GamingRoomMessagesResponse(BaseModel):
    items: list[GamingRoomMessageItem]


class GamingRoomInfo(BaseModel):
    slug: str
    title: str
    description: str
    online_count: int = 0


class GamingRoomsResponse(BaseModel):
    items: list[GamingRoomInfo]


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
    looking_for: Optional[str] = None
    age_min: Optional[int] = None
    age_max: Optional[int] = None
    max_distance: Optional[int] = None
    photos_urls: Optional[list[str]] = None
    primary_photo_url: Optional[str] = None
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


def _profile_is_complete(profile: dict[str, Any]) -> bool:
    photos = profile.get("photos_urls") or []
    return bool(
        (profile.get("first_name") or "").strip()
        and profile.get("birthdate")
        and (profile.get("gender") or "").strip()
        and (profile.get("city") or "").strip()
        and (profile.get("country") or "").strip()
        and len(photos) > 0
    )


def _download_telegram_photo(file_id: str) -> tuple[bytes, str]:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")

    meta_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={quote(file_id, safe='')}"
    with urlopen(meta_url, timeout=15) as meta_response:
        meta_payload = json.loads(meta_response.read().decode("utf-8"))

    file_path = ((meta_payload or {}).get("result") or {}).get("file_path")
    if not file_path:
        raise FileNotFoundError("Telegram file not found")

    download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    with urlopen(download_url, timeout=20) as file_response:
        content = file_response.read()
        media_type = file_response.info().get_content_type() or "application/octet-stream"

    if media_type == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(file_path)
        media_type = guessed or media_type

    return content, media_type


def _create_stars_invoice_link(title: str, description: str, payload: str, amount_stars: int) -> str:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/createInvoiceLink"
    body = urlencode(
        {
            "title": title,
            "description": description,
            "payload": payload,
            "currency": "XTR",
            "prices": json.dumps([{"label": title, "amount": int(amount_stars)}]),
        }
    ).encode("utf-8")
    with urlopen(api_url, data=body, timeout=20) as response:
        payload_json = json.loads(response.read().decode("utf-8"))
    if not payload_json.get("ok"):
        raise RuntimeError(f"Telegram invoice error: {payload_json.get('description', 'unknown')}")
    return str(payload_json.get("result") or "")


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


async def _ensure_gaming_rooms_table(pool: asyncpg.Pool | None) -> None:
    if RUN_WITHOUT_DB or pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gaming_room_messages (
                id SERIAL PRIMARY KEY,
                room_slug VARCHAR(20) NOT NULL CHECK (room_slug IN ('dota2', 'cs2')),
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gaming_room_messages_room_created ON gaming_room_messages(room_slug, created_at DESC)"
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
        await _ensure_gaming_rooms_table(db_pool)


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
    city: str | None = Query(default=None),
    country: str | None = Query(default=None),
    min_age: int | None = Query(default=None, ge=18, le=100),
    max_age: int | None = Query(default=None, ge=18, le=100),
    gender: str = Query(default="any", pattern="^(any|male|female)$"),
    premium: str = Query(default="any", pattern="^(any|premium|regular)$"),
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Get all active users by default, with optional filters (age, gender, location, premium)."""
    safe_limit = max(1, min(limit, 50))
    looking_for = (current_user.get('looking_for') or 'everyone').strip().lower()
    age_min = int(min_age or current_user.get('age_min') or 18)
    age_max = int(max_age or current_user.get('age_max') or 99)
    max_distance = int(current_user.get('max_distance') or 50)
    city_norm = (city or '').strip().lower() or None
    country_norm = (country or '').strip().lower() or None
    if gender == 'any':
        gender_filter = looking_for if looking_for in {'male', 'female'} else 'any'
    else:
        gender_filter = gender
    if age_min > age_max:
        age_min, age_max = age_max, age_min
    max_distance = max(1, min(max_distance, 500))

    if RUN_WITHOUT_DB or pool is None:
        # Return all in-memory profiles without strict requirements
        print(f"🔍 Discovering for user {current_user['id']}")
        candidate_users = []
        for user_id, profile in mock_profiles.items():
            if user_id == int(current_user['id']):
                continue
            match_key = _mock_match_key(int(current_user['id']), int(user_id))
            if mock_matches.get(match_key, {}).get('is_active'):
                continue
            profile_city = (profile.get('city') or '').strip().lower()
            profile_country = (profile.get('country') or '').strip().lower()
            profile_gender = (profile.get('gender') or '').strip().lower()
            profile_premium = bool(profile.get('is_premium', False))
            # Apply filters in mock mode too
            if gender_filter != 'any' and profile_gender and profile_gender != gender_filter:
                continue
            if city_norm and profile_city != city_norm:
                continue
            if country_norm and profile_country != country_norm:
                continue
            if premium == 'premium' and not profile_premium:
                continue
            if premium == 'regular' and profile_premium:
                continue
            if age_min > 25 or age_max < 25:
                    continue
            candidate_users.append(
                {
                    "id": profile['id'],
                    "first_name": profile.get('first_name') or 'User',
                    "age": 25,
                    "bio": profile.get('bio') or "",
                    "photos_urls": profile.get('photos_urls') or [],
                    "interests": ["💬 Chat"],
                    "city": profile.get('city') or '',
                    "country": profile.get('country') or '',
                    "gender": profile.get('gender') or 'unknown',
                    "is_premium": bool(profile.get('is_premium', False)),
                    "is_premium": profile_premium,
                    "distance": None,
                }
            )

        candidate_users.sort(
            key=lambda u: (
                0 if mock_swipes.get((int(u['id']), int(current_user['id']))) in {'like', 'superlike'} else 1,
                int(u['id']),
            )
        )
        print(f"📊 Found {len(candidate_users)} candidates for user {current_user['id']}")
        return {"users": candidate_users[:safe_limit]}
    
    async with pool.acquire() as conn:
        # Base query: show all active users with gender filter
        query = """
        SELECT
            u.id,
            u.first_name,
            u.bio,
            u.photos_urls,
            u.primary_photo_url,
            u.is_premium,
            u.interests,
            u.city,
            u.country,
            u.gender,
            EXTRACT(YEAR FROM AGE(u.birthdate)) as age,
            CASE
                WHEN u.location IS NOT NULL AND $2::geography IS NOT NULL 
                THEN ST_Distance(u.location::geography, $2::geography) / 1000
                ELSE NULL::float
            END as distance
        FROM users u
        WHERE u.id != $1
            AND u.is_active = TRUE
            AND u.is_blocked = FALSE
            AND u.profile_completed = TRUE
            AND ($4 = 'any' OR u.gender = $4)
            AND EXTRACT(YEAR FROM AGE(u.birthdate)) BETWEEN $5 AND $6
            AND ($7::text IS NULL OR LOWER(TRIM(COALESCE(u.city, ''))) = $7)
            AND ($8::text IS NULL OR LOWER(TRIM(COALESCE(u.country, ''))) = $8)
            AND (
                $9 = 'any'
                OR ($9 = 'premium' AND u.is_premium = TRUE)
                OR ($9 = 'regular' AND COALESCE(u.is_premium, FALSE) = FALSE)
            )
            AND NOT EXISTS (
                SELECT 1 FROM matches m
                WHERE m.is_active = TRUE
                    AND (
                        (m.user1_id = $1 AND m.user2_id = u.id)
                        OR (m.user1_id = u.id AND m.user2_id = $1)
                    )
            )
            AND NOT EXISTS (
                SELECT 1 FROM user_blocks b
                WHERE (b.blocker_id = $1 AND b.blocked_id = u.id)
                     OR (b.blocker_id = u.id AND b.blocked_id = $1)
            )
        ORDER BY u.id DESC
        LIMIT $3
        """
        users = await conn.fetch(
            query,
            current_user['id'],
            current_user.get('location'),
            safe_limit,
            gender_filter,
            age_min,
            age_max,
            city_norm,
            country_norm,
            premium,
        )

    return {
        "users": [
            {
                "id": u['id'],
                "first_name": u['first_name'],
                "age": u['age'],
                "bio": u['bio'],
                "photos_urls": u['photos_urls'] or [],
                "primary_photo_url": u['primary_photo_url'],
                "is_premium": bool(u['is_premium']),
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
    normalized_content = _validate_message_payload(payload.content)

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
            "content": normalized_content,
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
            normalized_content,
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
                _message_preview_for_notification(normalized_content),
                payload.match_id,
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
    normalized_content = _validate_message_payload(payload.content)

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
            "content": normalized_content,
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
            normalized_content,
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
                _message_preview_for_notification(normalized_content),
                match_row['id'],
            )

    payload_resp = {"id": int(inserted['id']), "created_at": str(inserted['created_at'])}
    if idem_key:
        _idempotency_set(current_user['id'], f"msgdirect:{idem_key}", payload_resp)
    return MessageCreateResponse(**payload_resp)


@app.post("/api/gifts/invoice", response_model=GiftInvoiceResponse)
async def create_gift_invoice(
    payload: GiftInvoiceRequest,
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Create Telegram Stars invoice link for an in-chat gift."""
    gift_slug = (payload.gift_slug or "rose").strip().lower()
    gift = STARS_GIFT_CATALOG.get(gift_slug)
    if not gift:
        raise HTTPException(status_code=400, detail="Unknown gift")

    if not BOT_TOKEN:
        raise HTTPException(status_code=503, detail="Gifts are temporarily unavailable")

    if RUN_WITHOUT_DB or pool is None:
        raise HTTPException(status_code=503, detail="Gifts require database mode")

    async with pool.acquire() as conn:
        match_row = await conn.fetchrow(
            """
            SELECT id, user1_id, user2_id, is_active
            FROM matches
            WHERE id = $1
            """,
            payload.match_id,
        )
        if not match_row or not match_row["is_active"]:
            raise HTTPException(status_code=404, detail="Match not found")
        if current_user["id"] not in (match_row["user1_id"], match_row["user2_id"]):
            raise HTTPException(status_code=403, detail="Not your match")

    invoice_payload = json.dumps(
        {
            "kind": "gift",
            "match_id": int(payload.match_id),
            "sender_id": int(current_user["id"]),
            "gift_slug": gift_slug,
            "ts": int(time.time()),
        },
        ensure_ascii=False,
    )

    try:
        invoice_link = await asyncio.to_thread(
            _create_stars_invoice_link,
            str(gift["title"]),
            str(gift["description"]),
            invoice_payload,
            int(gift["stars"]),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Failed to create Stars invoice") from exc

    return GiftInvoiceResponse(
        invoice_link=invoice_link,
        gift_slug=gift_slug,
        stars=int(gift["stars"]),
        title=str(gift["title"]),
    )


@app.get("/api/gaming/rooms", response_model=GamingRoomsResponse)
async def get_gaming_rooms(
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    if RUN_WITHOUT_DB or pool is None:
        items = [
            GamingRoomInfo(
                slug=slug,
                title=room["title"],
                description=room["description"],
                online_count=len(mock_room_messages.get(slug, [])),
            )
            for slug, room in GAMING_ROOMS.items()
        ]
        return GamingRoomsResponse(items=items)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT room_slug, COUNT(*)::int AS messages_count
            FROM gaming_room_messages
            GROUP BY room_slug
            """
        )
    counts = {r["room_slug"]: int(r["messages_count"] or 0) for r in rows}
    items = [
        GamingRoomInfo(
            slug=slug,
            title=room["title"],
            description=room["description"],
            online_count=counts.get(slug, 0),
        )
        for slug, room in GAMING_ROOMS.items()
    ]
    return GamingRoomsResponse(items=items)


@app.get("/api/gaming/rooms/{room_slug}/messages", response_model=GamingRoomMessagesResponse)
async def get_gaming_room_messages(
    room_slug: str,
    limit: int = 100,
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    room = _get_room_info(room_slug)
    safe_limit = max(1, min(limit, 200))
    welcome = GamingRoomMessageItem(
        id=0,
        room_slug=room_slug,
        sender_name="Система",
        content=f"Добро пожаловать в {room['title']}. Здесь ищут пати, обсуждают игру и общаются уважительно. Без мата, без токсика, с уважением к каждому.",
        created_at=_now_iso(),
        is_own=False,
    )

    if RUN_WITHOUT_DB or pool is None:
        raw_items = mock_room_messages.get(room_slug, [])[-safe_limit:]
        items = [welcome] + [
            GamingRoomMessageItem(
                id=int(item["id"]),
                room_slug=room_slug,
                sender_name=item.get("sender_name") or "User",
                content=item.get("content") or "",
                created_at=str(item.get("created_at")),
                is_own=int(item.get("user_id", 0)) == int(current_user["id"]),
            )
            for item in raw_items
        ]
        return GamingRoomMessagesResponse(items=items)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT gm.id, gm.room_slug, gm.content, gm.created_at, gm.user_id, u.first_name
            FROM gaming_room_messages gm
            JOIN users u ON u.id = gm.user_id
            WHERE gm.room_slug = $1
            ORDER BY gm.id DESC
            LIMIT $2
            """,
            room_slug,
            safe_limit,
        )

    items = [welcome] + [
        GamingRoomMessageItem(
            id=int(r["id"]),
            room_slug=r["room_slug"],
            sender_name=r["first_name"] or "User",
            content=r["content"],
            created_at=str(r["created_at"]),
            is_own=int(r["user_id"]) == int(current_user["id"]),
        )
        for r in reversed(rows)
    ]
    return GamingRoomMessagesResponse(items=items)


@app.post("/api/gaming/rooms/{room_slug}/messages", response_model=MessageCreateResponse)
async def create_gaming_room_message(
    room_slug: str,
    payload: GamingRoomMessageCreateRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    _get_room_info(room_slug)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Message content is empty")
    if _contains_profanity(content):
        raise HTTPException(status_code=400, detail="Please keep the chat respectful")

    _check_rate_limit(current_user['id'], f"gaming:{room_slug}", limit=40, window_sec=60)
    idem_key = (request.headers.get("Idempotency-Key") or "").strip()
    if idem_key:
        cached = _idempotency_get(current_user['id'], f"gaming:{room_slug}:{idem_key}")
        if cached is not None:
            return JSONResponse(content=cached)

    if RUN_WITHOUT_DB or pool is None:
        global mock_next_room_message_id
        created_at = _now_iso()
        mock_room_messages[room_slug].append(
            {
                "id": mock_next_room_message_id,
                "room_slug": room_slug,
                "user_id": int(current_user["id"]),
                "sender_name": current_user.get("first_name") or "User",
                "content": content,
                "created_at": created_at,
            }
        )
        payload_resp = {"id": mock_next_room_message_id, "created_at": created_at}
        mock_next_room_message_id += 1
        if idem_key:
            _idempotency_set(current_user['id'], f"gaming:{room_slug}:{idem_key}", payload_resp)
        return MessageCreateResponse(**payload_resp)

    async with pool.acquire() as conn:
        inserted = await conn.fetchrow(
            """
            INSERT INTO gaming_room_messages (room_slug, user_id, content)
            VALUES ($1, $2, $3)
            RETURNING id, created_at
            """,
            room_slug,
            current_user["id"],
            content,
        )

    payload_resp = {"id": int(inserted["id"]), "created_at": str(inserted["created_at"])}
    if idem_key:
        _idempotency_set(current_user['id'], f"gaming:{room_slug}:{idem_key}", payload_resp)
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
            "looking_for": profile.get('looking_for', 'everyone'),
            "age_min": int(profile.get('age_min', 18) or 18),
            "age_max": int(profile.get('age_max', 99) or 99),
            "max_distance": int(profile.get('max_distance', 50) or 50),
            "photos_urls": profile.get('photos_urls') or [],
            "primary_photo_url": profile.get('primary_photo_url'),
            "profile_completed": bool(profile.get('profile_completed', False)),
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
                     looking_for,
                     age_min,
                     age_max,
                     max_distance,
                     photos_urls,
                     primary_photo_url,
                     profile_completed,
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
        if payload.looking_for is not None:
            profile['looking_for'] = payload.looking_for
        if payload.age_min is not None:
            profile['age_min'] = max(18, min(int(payload.age_min), 80))
        if payload.age_max is not None:
            profile['age_max'] = max(18, min(int(payload.age_max), 80))
        if payload.max_distance is not None:
            profile['max_distance'] = max(1, min(int(payload.max_distance), 500))
        if payload.photos_urls is not None:
            profile['photos_urls'] = payload.photos_urls
        if payload.primary_photo_url is not None:
            profile['primary_photo_url'] = payload.primary_photo_url
        if payload.is_premium is not None:
            profile['is_premium'] = payload.is_premium
        profile['id'] = user_id
        profile['telegram_id'] = user_id
        if payload.age is not None:
            profile['birthdate'] = datetime.now(timezone.utc).date().replace(year=datetime.now(timezone.utc).year - profile['age'])
        profile['profile_completed'] = _profile_is_complete(profile)
        profile['location'] = None
        mock_profiles[user_id] = profile
        print(f"✅ Profile updated for user {user_id}: country={profile.get('country')}, city={profile.get('city')}, completed={profile['profile_completed']}")
        print(f"📊 All profiles in memory: {[(uid, p.get('country'), p.get('city'), p.get('profile_completed')) for uid, p in mock_profiles.items()]}")
        return {"success": True}

    if payload.age is not None and not (18 <= int(payload.age) <= 80):
        raise HTTPException(status_code=400, detail="Invalid age")

    if payload.gender and payload.gender not in {"male", "female", "other"}:
        raise HTTPException(status_code=400, detail="Invalid gender")

    if payload.looking_for and payload.looking_for not in {"male", "female", "everyone"}:
        raise HTTPException(status_code=400, detail="Invalid looking_for")

    if payload.age_min is not None and not (18 <= int(payload.age_min) <= 80):
        raise HTTPException(status_code=400, detail="Invalid age_min")

    if payload.age_max is not None and not (18 <= int(payload.age_max) <= 80):
        raise HTTPException(status_code=400, detail="Invalid age_max")

    if payload.max_distance is not None and not (1 <= int(payload.max_distance) <= 500):
        raise HTTPException(status_code=400, detail="Invalid max_distance")

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
                looking_for = COALESCE($8, looking_for),
                age_min = COALESCE($9, age_min),
                age_max = COALESCE($10, age_max),
                max_distance = COALESCE($11, max_distance),
                photos_urls = COALESCE($12, photos_urls),
                primary_photo_url = COALESCE($13, primary_photo_url),
                profile_completed = (
                    LENGTH(TRIM(COALESCE($1, first_name, ''))) > 0
                    AND COALESCE(
                        CASE
                            WHEN $2::int IS NULL THEN birthdate
                            ELSE make_date(EXTRACT(YEAR FROM CURRENT_DATE)::int - $2::int, 1, 1)
                        END,
                        birthdate
                    ) IS NOT NULL
                    AND LENGTH(TRIM(COALESCE($4, city, ''))) > 0
                    AND LENGTH(TRIM(COALESCE($5, country, ''))) > 0
                    AND LENGTH(TRIM(COALESCE($6, gender, ''))) > 0
                    AND CARDINALITY(COALESCE($12, photos_urls, ARRAY[]::text[])) > 0
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $14
            """,
            payload.first_name,
            payload.age,
            payload.bio,
            payload.city,
            payload.country,
            payload.gender,
            payload.is_premium,
            payload.looking_for,
            payload.age_min,
            payload.age_max,
            payload.max_distance,
            payload.photos_urls,
            payload.primary_photo_url,
            current_user['id'],
        )
    return {"success": True}


@app.get("/api/photos/{file_id}")
async def get_profile_photo(file_id: str):
    """Proxy Telegram photo by file_id without exposing bot token to clients."""
    try:
        content, media_type = await asyncio.to_thread(_download_telegram_photo, file_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Photo not found") from exc

    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


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
    like_text = "суперлайк" if swipe_type == "superlike" else "лайк"
    
    text = (
        f"{emoji} <b>Новый {like_text}!</b>\n\n"
        f"<i>{actor_name}</i> проявил(а) к вам интерес 💕\n\n"
        f"Откройте приложение, чтобы узнать больше"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Посмотреть профиль", url="https://t.me/premiumdatingbot/premium")]
    ])
    
    try:
        await bot.send_message(receiver_tid, text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        return


async def send_match_notification(user1_tid, user2_tid, user1_name, user2_name, send_to_user1: bool = True, send_to_user2: bool = True):
    """Send match notifications via Telegram bot with beautiful formatting."""
    if not bot:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Открыть чат", url="https://t.me/premiumdatingbot/premium")],
        [InlineKeyboardButton(text="👤 Посмотреть профиль", url="https://t.me/premiumdatingbot/premium")]
    ])
    
    try:
        if send_to_user1:
            message1 = (
                f"💕 <b>Совпадение!</b>\n\n"
                f"Вы и <i>{user2_name}</i> понравились друг другу!\n\n"
                f"🎉 Поздравляем! Это взаимная любовь!\n"
                f"Начните общение прямо сейчас →"
            )
            await bot.send_message(user1_tid, message1, parse_mode="HTML", reply_markup=keyboard)
        
        if send_to_user2:
            message2 = (
                f"💕 <b>Совпадение!</b>\n\n"
                f"Вы и <i>{user1_name}</i> понравились друг другу!\n\n"
                f"🎉 Поздравляем! Это взаимная любовь!\n"
                f"Начните общение прямо сейчас →"
            )
            await bot.send_message(user2_tid, message2, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        # Notification failure should not break swipe API response.
        return


async def send_message_notification(receiver_tid: int, sender_name: str, text: str, match_id: int = None):
    """Send real-time new message notification with beautiful formatting and reply button."""
    if not bot:
        return
    preview = text if len(text) <= 70 else (text[:67] + "...")
    
    body = (
        f"💬 <b>Новое сообщение</b>\n\n"
        f"От: <i>{sender_name}</i>\n\n"
        f"<code>{preview}</code>\n\n"
        f"<i>Откройте приложение, чтобы ответить</i>"
    )
    
    # Build reply URL from existing WEB_APP_URL — no extra config needed
    web_app_base = os.getenv("WEB_APP_URL", "").strip().rstrip("/")
    api_base = os.getenv("WEB_API_URL", "").strip()
    reply_params = {"tab": "messages"}
    if match_id:
        reply_params["reply_match_id"] = str(match_id)
    if api_base:
        reply_params["api"] = api_base
    if web_app_base and web_app_base.startswith("https://"):
        separator = "&" if "?" in web_app_base else "?"
        reply_url = f"{web_app_base}{separator}{urlencode(reply_params)}"
        button = InlineKeyboardButton(text="✉️ Ответить", web_app=WebAppInfo(url=reply_url))
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[button]])
    else:
        keyboard = None
    
    try:
        await bot.send_message(receiver_tid, body, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        return


app.include_router(create_filters_router(get_current_user, get_db_pool))
