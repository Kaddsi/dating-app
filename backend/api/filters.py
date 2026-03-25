"""User discovery filters API router."""

from __future__ import annotations

from typing import Any, Callable

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


class UserFilters(BaseModel):
    """Model for user search filters."""

    looking_for: str = Field(..., pattern="^(male|female|everyone)$")
    age_min: int = Field(..., ge=18, le=100)
    age_max: int = Field(..., ge=18, le=100)
    max_distance: int = Field(..., ge=1, le=500)
    required_interests: list[str] = Field(default_factory=list)


def create_filters_router(
    current_user_dependency: Callable[..., Any],
    db_pool_dependency: Callable[..., asyncpg.Pool],
) -> APIRouter:
    """Build filters router with injected dependencies from the main API module."""

    router = APIRouter()

    @router.get("/api/user/filters")
    async def get_user_filters(
        current_user: dict[str, Any] = Depends(current_user_dependency),
        db_pool: asyncpg.Pool = Depends(db_pool_dependency),
    ):
        async with db_pool.acquire() as conn:
            filters = await conn.fetchrow(
                """
                SELECT looking_for, age_min, age_max, max_distance
                FROM users
                WHERE id = $1
                """,
                current_user["id"],
            )

            if not filters:
                raise HTTPException(status_code=404, detail="User not found")

            interests = await conn.fetch(
                """
                SELECT interest
                FROM user_required_interests
                WHERE user_id = $1
                ORDER BY interest
                """,
                current_user["id"],
            )

        return {
            "filters": {
                "looking_for": filters["looking_for"] or "everyone",
                "age_min": filters["age_min"] or 18,
                "age_max": filters["age_max"] or 35,
                "max_distance": filters["max_distance"] or 50,
                "required_interests": [item["interest"] for item in interests],
            }
        }

    @router.put("/api/user/filters")
    async def update_user_filters(
        filters: UserFilters,
        current_user: dict[str, Any] = Depends(current_user_dependency),
        db_pool: asyncpg.Pool = Depends(db_pool_dependency),
    ):
        if filters.age_min > filters.age_max:
            raise HTTPException(
                status_code=400,
                detail="Minimum age cannot be greater than maximum age",
            )

        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE users
                    SET
                        looking_for = $1,
                        age_min = $2,
                        age_max = $3,
                        max_distance = $4,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = $5
                    """,
                    filters.looking_for,
                    filters.age_min,
                    filters.age_max,
                    filters.max_distance,
                    current_user["id"],
                )

                await conn.execute(
                    "DELETE FROM user_required_interests WHERE user_id = $1",
                    current_user["id"],
                )

                if filters.required_interests:
                    await conn.executemany(
                        """
                        INSERT INTO user_required_interests (user_id, interest)
                        VALUES ($1, $2)
                        ON CONFLICT (user_id, interest) DO NOTHING
                        """,
                        [
                            (current_user["id"], interest.strip())
                            for interest in filters.required_interests
                            if interest.strip()
                        ],
                    )

        return {"success": True, "message": "Filters updated"}

    @router.get("/api/user/filters/stats")
    async def get_filter_stats(
        current_user: dict[str, Any] = Depends(current_user_dependency),
        db_pool: asyncpg.Pool = Depends(db_pool_dependency),
    ):
        async with db_pool.acquire() as conn:
            filters = await conn.fetchrow(
                """
                SELECT looking_for, age_min, age_max, max_distance, location
                FROM users
                WHERE id = $1
                """,
                current_user["id"],
            )

            if not filters:
                raise HTTPException(status_code=404, detail="User not found")

            distance_clause = "TRUE"
            params = [
                current_user["id"],
                filters["looking_for"],
                filters["age_min"],
                filters["age_max"],
            ]
            if filters["location"] is not None:
                distance_clause = "ST_Distance(u.location::geography, $5::geography) / 1000 <= $6"
                params.extend([filters["location"], filters["max_distance"]])

            query = f"""
            SELECT COUNT(*)
            FROM users u
            WHERE u.id != $1
              AND u.is_active = TRUE
              AND u.is_blocked = FALSE
              AND u.profile_completed = TRUE
              AND (($2 = 'everyone') OR (u.gender = $2))
              AND EXTRACT(YEAR FROM AGE(u.birthdate)) BETWEEN $3 AND $4
              AND {distance_clause}
              AND NOT EXISTS (
                SELECT 1 FROM user_blocks b
                WHERE (b.blocker_id = $1 AND b.blocked_id = u.id)
                   OR (b.blocker_id = u.id AND b.blocked_id = $1)
              )
            """
            count = await conn.fetchval(query, *params)

        return {
            "available_profiles": count,
            "filters": {
                "looking_for": filters["looking_for"],
                "age_range": f"{filters['age_min']}-{filters['age_max']}",
                "max_distance": filters["max_distance"],
            },
        }

    @router.post("/api/user/filters/reset")
    async def reset_filters(
        current_user: dict[str, Any] = Depends(current_user_dependency),
        db_pool: asyncpg.Pool = Depends(db_pool_dependency),
    ):
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE users
                    SET
                        looking_for = 'everyone',
                        age_min = 18,
                        age_max = 99,
                        max_distance = 50,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = $1
                    """,
                    current_user["id"],
                )
                await conn.execute(
                    "DELETE FROM user_required_interests WHERE user_id = $1",
                    current_user["id"],
                )

        return {"success": True, "message": "Filters reset"}

    return router
