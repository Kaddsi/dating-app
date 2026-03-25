"""
Premium Dating TWA - Phone Verification & Geo-Fencing Middleware
Enforces region restrictions and contact sharing before app access.
"""

from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
import phonenumbers
from phonenumbers import NumberParseException
import logging

logger = logging.getLogger(__name__)


# Block only regions requested by product requirements
BLOCKED_REGIONS = {"RU", "BY"}


def is_region_allowed(phone_number: str) -> tuple[bool, str | None]:
    """
    Validates if a phone number is from an allowed region.
    
    Args:
        phone_number: Full international phone number (e.g., "+1234567890")
    
    Returns:
        tuple: (is_allowed: bool, country_code: str | None)
    """
    try:
        # Parse the phone number
        parsed = phonenumbers.parse(phone_number, None)
        country_code = f"+{parsed.country_code}"
        
        region = phonenumbers.region_code_for_number(parsed)
        if region in BLOCKED_REGIONS:
            logger.info(f"Blocked number from {region}: {phone_number[:8]}***")
            return False, country_code

        logger.info(f"Allowed number from {region}: {phone_number[:8]}***")
        return True, country_code
        
    except NumberParseException as e:
        logger.error(f"Failed to parse phone number: {e}")
        return False, None


class PhoneVerificationMiddleware(BaseMiddleware):
    """
    Middleware that enforces phone contact sharing and geo-fencing.
    Blocks users from restricted regions before they can access the app.
    """
    
    def __init__(self, db_pool, phone_keyboard_factory):
        """
        Args:
            db_pool: Database connection pool (asyncpg)
            phone_keyboard_factory: Callable that returns ReplyKeyboardMarkup
        """
        self.db = db_pool
        self.phone_keyboard_factory = phone_keyboard_factory
        self.memory_users: dict[int, dict[str, Any]] = {}
        super().__init__()

    async def _get_user(self, user_id: int):
        if self.db is None:
            return self.memory_users.get(user_id)

        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT telegram_id, phone, country_code, is_blocked, first_name FROM users WHERE telegram_id = $1",
                user_id,
            )
            return dict(row) if row else None

    async def _save_user(self, user_data: dict[str, Any]):
        if self.db is None:
            self.memory_users[user_data["telegram_id"]] = user_data
            return user_data

        async with self.db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (
                    telegram_id,
                    phone,
                    country_code,
                    is_blocked,
                    username,
                    first_name,
                    last_name
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (telegram_id) DO UPDATE
                SET
                    phone = $2,
                    country_code = $3,
                    is_blocked = $4,
                    username = COALESCE($5, users.username),
                    first_name = COALESCE($6, users.first_name),
                    last_name = COALESCE($7, users.last_name)
                """,
                user_data["telegram_id"],
                user_data.get("phone"),
                user_data.get("country_code"),
                user_data.get("is_blocked", False),
                user_data.get("username"),
                user_data.get("first_name"),
                user_data.get("last_name"),
            )

            saved = await conn.fetchrow(
                "SELECT telegram_id, phone, country_code, is_blocked, first_name FROM users WHERE telegram_id = $1",
                user_data["telegram_id"],
            )
            return saved
    
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        """
        Main middleware logic. Intercepts message and callback updates.
        """
        if not event or not event.from_user:
            return await handler(event, data)

        user_id = event.from_user.id
        message = event if isinstance(event, Message) else event.message

        # Allow initial onboarding flow before verification.
        if isinstance(event, Message):
            text = (event.text or "").strip().lower()
            if text.startswith("/start"):
                return await handler(event, data)
        
        # Check if user is already verified in storage
        user = await self._get_user(user_id)
        
        # User exists and is verified
        if user:
            if user['is_blocked']:
                # User was previously blocked - send denial message
                if isinstance(event, CallbackQuery):
                    await event.answer("Service unavailable in your region", show_alert=True)
                elif message:
                    await message.answer(
                        "🚫 *Service Unavailable*\n\n"
                        "We sincerely apologize, but our premium dating service "
                        "is currently not available in your region.\n\n"
                        "_We're continuously expanding our global presence. "
                        "Thank you for your understanding._",
                        parse_mode="Markdown"
                    )
                return  # Terminate session
            
            # User is verified and allowed - continue
            data['user_db'] = user
            return await handler(event, data)

        # Unverified callbacks (inline menu taps) should show alert immediately,
        # except onboarding language selection flow.
        if isinstance(event, CallbackQuery):
            callback_data = event.data or ""
            if callback_data.startswith("lang_") or callback_data == "start_begin":
                return await handler(event, data)
            await event.answer("Please verify your phone first", show_alert=True)
            return

        if not message:
            return await handler(event, data)
        
        # New user - must share contact
        if message.contact:
            # Telegram may allow forwarding another person's contact.
            # Accept only self-shared contact to prevent bypass.
            if message.contact.user_id and message.contact.user_id != user_id:
                await message.answer(
                    "Please share your own phone number using the verification button.",
                    reply_markup=self.phone_keyboard_factory()
                )
                return

            phone_number = message.contact.phone_number
            
            # Ensure phone has + prefix
            if not phone_number.startswith("+"):
                phone_number = f"+{phone_number}"
            
            # Validate region
            is_allowed, country_code = is_region_allowed(phone_number)
            
            if not is_allowed:
                # Save blocked user to prevent repeated attempts
                await self._save_user(
                    {
                        "telegram_id": user_id,
                        "phone": phone_number,
                        "country_code": country_code,
                        "is_blocked": True,
                        "username": message.from_user.username,
                        "first_name": message.from_user.first_name,
                        "last_name": message.from_user.last_name,
                    }
                )
                
                # Send premium-styled denial message
                await message.answer(
                    "✨ *Thank you for your interest* ✨\n\n"
                    "Unfortunately, our exclusive dating experience is not yet "
                    "available in your region.\n\n"
                    "🌍 _We're working hard to expand our service globally. "
                    "We'll notify you when we launch in your area._\n\n"
                    "Stay extraordinary! 💜",
                    parse_mode="Markdown"
                )
                return  # Terminate session
            
            # User is from allowed region - save to storage
            user = await self._save_user(
                {
                    "telegram_id": user_id,
                    "phone": phone_number,
                    "country_code": country_code,
                    "is_blocked": False,
                    "username": message.from_user.username,
                    "first_name": message.from_user.first_name,
                    "last_name": message.from_user.last_name,
                }
            )
            
            # Welcome message with Web App access
            await message.answer(
                "✨ *Welcome to Premium Dating* ✨\n\n"
                "Your account has been verified successfully!\n\n"
                "🎯 Ready to discover extraordinary connections?\n\n"
                "_Tap the button below to start your journey._",
                parse_mode="Markdown"
            )
            
            data['user_db'] = user
            return await handler(event, data)
        
        # User hasn't shared contact yet - request it
        await message.answer(
            "💎 *Welcome to Premium Dating* 💎\n\n"
            "To ensure the highest quality experience and safety for our community, "
            "we need to verify your phone number.\n\n"
            "✓ _Your privacy is our priority_\n"
            "✓ _One-time verification only_\n"
            "✓ _Secure & encrypted_\n\n"
            "👇 Please tap the button below to continue",
            parse_mode="Markdown",
            reply_markup=self.phone_keyboard_factory()
        )
        
        return  # Don't proceed until contact is shared
