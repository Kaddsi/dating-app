"""
Premium Dating TWA - Main Bot Handler
Entry point for the Telegram bot with premium UI and full feature set.
"""

import asyncio
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    CallbackQuery,
    BotCommand,
    MenuButtonCommands,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import asyncpg
from dotenv import load_dotenv
from middlewares.phone_verification import PhoneVerificationMiddleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
load_dotenv()
load_dotenv(Path(__file__).resolve().parents[1] / '.env')

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/dating_db")
WEB_APP_URL = os.getenv("WEB_APP_URL", "http://localhost:5173")
WEB_APP_VERSION = (
    os.getenv("WEB_APP_VERSION", "").strip()
    or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
)
WEB_APP_CACHE_BUSTER = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
WEB_API_URL = (os.getenv("WEB_API_URL", "") or os.getenv("API_BASE_URL", "")).strip()
RUN_WITHOUT_DB = os.getenv("RUN_WITHOUT_DB", "false").lower() == "true"
DB_POOL: asyncpg.Pool | None = None

# In-memory user storage for testing mode
USER_STORAGE = {}

# Notifications tracking: user_id -> {likes: [], matches: [], messages: []}
NOTIFICATIONS = {}

CANCEL_WORDS = {"отмена", "cancel", "/cancel"}
SKIP_WORDS = {"пропустить", "skip", "-"}


class ProfileWizard(StatesGroup):
    first_name = State()
    age = State()
    gender = State()
    country = State()
    city = State()
    bio = State()
    looking_for = State()
    photo = State()


def _api_base_url() -> str:
    if WEB_API_URL:
        return WEB_API_URL.rstrip("/")
    base = WEB_APP_URL.rstrip("/")
    if base.endswith("/mini"):
        base = base[:-5]
    return base


def _profile_photo_url(file_id: str) -> str:
    base = _api_base_url()
    if base:
        return f"{base}/api/photos/{file_id}"
    return f"/api/photos/{file_id}"


def _profile_missing_fields(profile: dict) -> list[str]:
    missing = []
    if not (profile.get("first_name") or "").strip():
        missing.append("имя")
    if not profile.get("birthdate"):
        missing.append("возраст")
    if not (profile.get("gender") or "").strip():
        missing.append("пол")
    if not (profile.get("country") or "").strip():
        missing.append("страна")
    if not (profile.get("city") or "").strip():
        missing.append("город")
    if not (profile.get("photos_urls") or []):
        missing.append("фото")
    return missing


def _profile_complete(profile: dict) -> bool:
    return len(_profile_missing_fields(profile)) == 0


async def get_profile_data(telegram_id: int, fallback_name: str | None = None) -> dict:
    if RUN_WITHOUT_DB or DB_POOL is None:
        stored = USER_STORAGE.get(telegram_id, {}).copy()
        stored.setdefault("telegram_id", telegram_id)
        stored.setdefault("first_name", fallback_name or stored.get("first_name") or "")
        stored.setdefault("bio", "")
        stored.setdefault("gender", "")
        stored.setdefault("country", "")
        stored.setdefault("city", "")
        stored.setdefault("looking_for", "everyone")
        stored.setdefault("age_min", 18)
        stored.setdefault("age_max", 99)
        stored.setdefault("max_distance", 50)
        stored.setdefault("photos_urls", [])
        stored.setdefault("primary_photo_url", None)
        stored["profile_completed"] = _profile_complete(stored)
        return stored

    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT telegram_id, first_name, bio, gender, birthdate, city, country,
                   looking_for, age_min, age_max, max_distance,
                   photos_urls, primary_photo_url, profile_completed, is_premium
            FROM users
            WHERE telegram_id = $1
            """,
            telegram_id,
        )

    if not row:
        return {
            "telegram_id": telegram_id,
            "first_name": fallback_name or "",
            "bio": "",
            "gender": "",
            "birthdate": None,
            "city": "",
            "country": "",
            "looking_for": "everyone",
            "age_min": 18,
            "age_max": 99,
            "max_distance": 50,
            "photos_urls": [],
            "primary_photo_url": None,
            "profile_completed": False,
            "is_premium": False,
        }

    profile = dict(row)
    profile["photos_urls"] = profile.get("photos_urls") or []
    profile["profile_completed"] = _profile_complete(profile)
    return profile


async def save_profile_fields(telegram_id: int, **fields) -> dict:
    if RUN_WITHOUT_DB or DB_POOL is None:
        stored = USER_STORAGE.setdefault(telegram_id, {})
        if "age" in fields and fields["age"] is not None:
            stored["birthdate"] = date(datetime.now(timezone.utc).year - int(fields.pop("age")), 1, 1)
        stored.update({k: v for k, v in fields.items() if v is not None})
        stored["profile_completed"] = _profile_complete(stored)
        return await get_profile_data(telegram_id)

    updates = []
    args = []
    index = 1
    mutable_fields = dict(fields)
    age = mutable_fields.pop("age", None)
    if age is not None:
        mutable_fields["birthdate"] = date(datetime.now(timezone.utc).year - int(age), 1, 1)

    for column, value in mutable_fields.items():
        updates.append(f"{column} = ${index}")
        args.append(value)
        index += 1

    if updates:
        updates.append(f"updated_at = CURRENT_TIMESTAMP")
        updates.append(f"last_active = CURRENT_TIMESTAMP")
        args.append(telegram_id)
        query = f"UPDATE users SET {', '.join(updates)} WHERE telegram_id = ${index}"
        async with DB_POOL.acquire() as conn:
            await conn.execute(query, *args)

    profile = await get_profile_data(telegram_id)
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "UPDATE users SET profile_completed = $1 WHERE telegram_id = $2",
            _profile_complete(profile),
            telegram_id,
        )

    return await get_profile_data(telegram_id)


async def add_profile_photo(telegram_id: int, file_id: str) -> dict:
    profile = await get_profile_data(telegram_id)
    photos = list(profile.get("photos_urls") or [])
    new_url = _profile_photo_url(file_id)
    if new_url not in photos:
        photos.append(new_url)
    photos = photos[:3]
    primary = photos[0] if photos else None
    return await save_profile_fields(
        telegram_id,
        photos_urls=photos,
        primary_photo_url=primary,
    )


def format_profile_summary(profile: dict) -> str:
    birthdate = profile.get("birthdate")
    age = "-"
    if birthdate:
        age = str(datetime.now(timezone.utc).year - birthdate.year)

    photos_count = len(profile.get("photos_urls") or [])
    missing = _profile_missing_fields(profile)
    status = "Готова к показу в приложении" if not missing else f"Нужно заполнить: {', '.join(missing)}"
    looking_for_map = {
        "male": "мужчин",
        "female": "женщин",
        "everyone": "всех",
    }

    return (
        "👤 *Ваша анкета*\n\n"
        f"Имя: {profile.get('first_name') or '-'}\n"
        f"Возраст: {age}\n"
        f"Пол: {profile.get('gender') or '-'}\n"
        f"Страна: {profile.get('country') or '-'}\n"
        f"Город: {profile.get('city') or '-'}\n"
        f"Ищу: {looking_for_map.get(profile.get('looking_for'), profile.get('looking_for') or '-')}\n"
        f"Фото: {photos_count}/3\n"
        f"О себе: {(profile.get('bio') or '-')}\n\n"
        f"Статус: *{status}*"
    )

def init_user_storage(user_id: int):
    """Initialize or get existing user storage."""
    if user_id not in USER_STORAGE:
        USER_STORAGE[user_id] = {
            "verified": False,
            "phone": None,
            "language": "en",
            "likes_count": 0,
            "matches_count": 0,
        }
    if user_id not in NOTIFICATIONS:
        NOTIFICATIONS[user_id] = {
            "likes": [],  # List of user_ids who liked
            "matches": [],  # List of match pairs
            "messages": [],  # List of unread messages
        }
    return USER_STORAGE[user_id]

def mark_user_verified(user_id: int, phone: str, language: str = "en"):
    """Mark user as verified after phone confirmation."""
    user = init_user_storage(user_id)
    user["verified"] = True
    user["phone"] = phone
    user["language"] = language

def add_like_notification(user_id: int, liker_id: int):
    """Add a like to user's notifications."""
    if user_id not in NOTIFICATIONS:
        init_user_storage(user_id)
    if liker_id not in NOTIFICATIONS[user_id]["likes"]:
        NOTIFICATIONS[user_id]["likes"].append(liker_id)
        USER_STORAGE[user_id]["likes_count"] = len(NOTIFICATIONS[user_id]["likes"])
        return True
    return False

def add_match_notification(user_id: int, other_user_id: int):
    """Add a match to user's notifications."""
    if user_id not in NOTIFICATIONS:
        init_user_storage(user_id)
    match_pair = tuple(sorted([user_id, other_user_id]))
    if match_pair not in NOTIFICATIONS[user_id]["matches"]:
        NOTIFICATIONS[user_id]["matches"].append(match_pair)
        USER_STORAGE[user_id]["matches_count"] = len(NOTIFICATIONS[user_id]["matches"])
        return True
    return False

def add_message_notification(user_id: int, sender_id: int, text: str):
    """Add a message notification."""
    if user_id not in NOTIFICATIONS:
        init_user_storage(user_id)
    NOTIFICATIONS[user_id]["messages"].append({
        "from": sender_id,
        "text": text[:100]
    })
    return True

async def send_notification(bot: Bot, user_id: int, text: str):
    """Send notification to user."""
    try:
        await bot.send_message(user_id, text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to send notification to {user_id}: {e}")

# Router
router = Router()


# ═══════════════════════════════════════════════════════════════════════════════
# MULTILINGUAL TRANSLATIONS
# ═══════════════════════════════════════════════════════════════════════════════

TRANSLATIONS = {
    "en": {
        "greeting": (
            "✨ *Welcome to the Next Level of Dating* ✨\n\n"
            "There are no coincidences here — only interesting people, genuine emotions, and real matches 💫\n\n"
            "💖 *Want to find someone special?*\n"
            "🔥 Or just chat and spend time with interest?\n\n"
            "I'll help you:\n"
            "— Find the perfect conversation partner\n"
            "— Meet new people\n"
            "— And maybe… meet your destiny 😉\n\n"
            "👇 *Tap the button below to begin*\n"
            "Your story starts right now 💌"
        ),
        "select_language": "🌍 *Choose Your Language*",
        "verify_phone": "📱 Verify My Phone Number",
        "verified": "Verified ✓",
    },
    "ru": {
        "greeting": (
            "✨ *Добро пожаловать в мир знакомств нового уровня* ✨\n\n"
            "Здесь не бывает случайностей — только интересные люди, искренние эмоции и настоящие совпадения 💫\n\n"
            "💖 *Хочешь найти кого-то особенного?*\n"
            "🔥 Или просто пообщаться и провести время с интересом?\n\n"
            "Я помогу тебе:\n"
            "— Найти идеального собеседника\n"
            "— Познакомиться с новыми людьми\n"
            "— И, возможно… встретить свою судьбу 😉\n\n"
            "👇 *Нажми кнопку ниже, чтобы начать*\n"
            "Твоя история начинается прямо сейчас 💌"
        ),
        "select_language": "🌍 *Выберите язык*",
        "verify_phone": "📱 Подтвердить номер",
        "verified": "Подтверждено ✓",
    },
    "uk": {
        "greeting": (
            "✨ *Ласкаво просимо в світ знайомств нового рівня* ✨\n\n"
            "Тут немає випадків — тільки цікаві люди, щирі емоції та справжні збіги 💫\n\n"
            "💖 *Хочеш знайти когось особливого?*\n"
            "🔥 Або просто поговорити й провести час з цікавістю?\n\n"
            "Я допоможу тобі:\n"
            "— Знайти ідеального співрозмовника\n"
            "— Познайомитися з новими людьми\n"
            "— І, можливо… зустріти свою долю 😉\n\n"
            "👇 *Натисни кнопку нижче, щоб почати*\n"
            "Твоя історія починається прямо зараз 💌"
        ),
        "select_language": "🌍 *Виберіть мову*",
        "verify_phone": "📱 Підтвердити номер",
        "verified": "Підтверджено ✓",
    },
    "cs": {
        "greeting": (
            "✨ *Vítejte v novém světě znakomství* ✨\n\n"
            "Zde nejsou náhody — pouze zajímavé lidi, upřímné emoce a skutečné shody 💫\n\n"
            "💖 *Chceš najít někoho speciálního?*\n"
            "🔥 Nebo si jen pohovořit a strávit čas zájmem?\n\n"
            "Pomůžu ti:\n"
            "— Najít ideálního partnera pro rozhovor\n"
            "— Poznámit se s novými lidmi\n"
            "— A možná… potkat svůj osud 😉\n\n"
            "👇 *Klikni na tlačítko níže, aby ses začal*\n"
            "Tvůj příběh se začíná právě teď 💌"
        ),
        "select_language": "🌍 *Vyberte svůj jazyk*",
        "verify_phone": "📱 Ověřit číslo",
        "verified": "Ověřeno ✓",
    },
    "pl": {
        "greeting": (
            "✨ *Witaj w nowym świecie randek* ✨\n\n"
            "Tutaj nie ma zbiegów — tylko ciekawi ludzie, szczere emocje i prawdziwe dopasowania 💫\n\n"
            "💖 *Chcesz znaleźć kogoś specjalnego?*\n"
            "🔥 Lub po prostu porozmawiać i spędzić czas z zainteresowaniem?\n\n"
            "Pomogę ci:\n"
            "— Znaleźć idealnego partnera do rozmowy\n"
            "— Poznać nowych ludzi\n"
            "— I może… spotkać swój los 😉\n\n"
            "👇 *Kliknij przycisk poniżej, aby rozpocząć*\n"
            "Twoja historia zaczyna się teraz 💌"
        ),
        "select_language": "🌍 *Wybierz swój język*",
        "verify_phone": "📱 Zweryfikuj numer",
        "verified": "Zweryfikowany ✓",
    },
}


def normalize_lang(lang: str | None) -> str:
    if not lang:
        return "en"
    short = lang.lower().split("-")[0]
    return short if short in TRANSLATIONS else "en"


def get_user_lang(user_id: int, fallback: str | None = None) -> str:
    stored = USER_STORAGE.get(user_id, {}).get("language")
    if stored in TRANSLATIONS:
        return stored
    return normalize_lang(fallback)


UI_TEXTS = {
    "en": {
        "main_menu": "✨ *Welcome back!*\n\n🎯 *Premium Dating Experience*\n\nDiscover matches, view your profile, adjust settings\n\nChoose an option below:",
        "verify_complete": "🎉 *Verification Complete!*\n\nYou're all set to experience premium dating.\n\n✓ Profile activated\n✓ Full access granted\n✓ Ready to connect\n\n_Let's find your perfect match!_",
        "verify_required": "👋 Please verify your phone number first.",
        "verify_prompt": "👇 Please tap the button below to verify your phone",
        "discover": "🔍 *Discover Matches*\n\nBrowse through profiles and find your perfect match\n\n⭐ Swipe to like\n❌ Swipe to pass\n💬 Chat with matches\n\n_Open the app to start discovering:_",
        "matches": "💘 *Your Matches*\n\nView profiles you've liked and who likes you\n\n💬 Chat now with your matches\n📸 See full profiles\n❤️ Manage your favorites\n\n_Open the app to view matches:_",
        "profile": "👤 *Profile: {name}*\n\n📝 Update your photos\n✏️ Edit bio & interests\n🎯 Manage hobbies\n💰 Premium status\n\n_Edit your profile details:_",
        "settings": "⚙️ *Settings & Preferences*\n\n🔎 Search distance: Unlimited\n👥 Age range: 18-45\n💬 Language: {lang_name}\n🔔 Notifications: Enabled\n🌙 Dark mode: Enabled\n\n_Adjust your preferences:_",
        "help": "❓ *Help & Support*\n\n🔐 Your privacy is protected\n🚀 Free to use, premium features available\n🌍 Available in 50+ countries\n💬 24/7 community support\n\n📋 FAQ:\n• How to verify? Tap phone button\n• How to swipe? Open app, tap profile\n• How to chat? Tap matched profile\n\n_Questions? Use /help or chat in app_",
    },
    "ru": {
        "main_menu": "✨ *С возвращением!*\n\n🎯 *Премиум знакомства*\n\nСмотри анкеты, проверяй мэтчи, настраивай профиль\n\nВыбери раздел ниже:",
        "verify_complete": "🎉 *Проверка успешно пройдена!*\n\nТеперь вам доступен полный функционал.\n\n✓ Профиль активирован\n✓ Полный доступ открыт\n✓ Можно знакомиться\n\n_Пора найти идеальный мэтч!_",
        "verify_required": "👋 Сначала подтвердите номер телефона.",
        "verify_prompt": "👇 Нажмите кнопку ниже, чтобы подтвердить номер",
        "discover": "🔍 *Поиск анкет*\n\nЛистайте профили и находите интересных людей\n\n⭐ Лайк\n❌ Пропуск\n💬 Чат с мэтчами\n\n_Откройте приложение, чтобы начать:_",
        "matches": "💘 *Ваши мэтчи*\n\nСмотрите взаимные лайки и продолжайте общение\n\n💬 Пишите прямо сейчас\n📸 Открывайте профили\n❤️ Управляйте избранным\n\n_Откройте приложение, чтобы посмотреть мэтчи:_",
        "profile": "👤 *Профиль: {name}*\n\n📝 Обновите фото\n✏️ Измените био и интересы\n🎯 Настройте хобби\n💰 Премиум-статус\n\n_Редактировать профиль:_",
        "settings": "⚙️ *Настройки*\n\n🔎 Дистанция: без ограничений\n👥 Возраст: 18-45\n💬 Язык: {lang_name}\n🔔 Уведомления: включены\n🌙 Тема: тёмная\n\n_Изменить параметры:_",
        "help": "❓ *Помощь*\n\n🔐 Ваши данные защищены\n🚀 Базовый доступ бесплатный\n🌍 Доступно в 50+ странах\n💬 Поддержка сообщества 24/7\n\n📋 FAQ:\n• Как пройти верификацию? Нажмите кнопку телефона\n• Как свайпать? Откройте приложение\n• Как писать? Откройте мэтч\n\n_Вопросы? Используйте /help_",
    },
    "uk": {
        "main_menu": "✨ *З поверненням!*\n\n🎯 *Преміум знайомства*\n\nПереглядай анкети, дивись збіги та керуй профілем\n\nОберіть розділ нижче:",
        "verify_complete": "🎉 *Перевірку завершено!*\n\nПовний доступ уже відкрито.\n\n✓ Профіль активовано\n✓ Повний доступ надано\n✓ Можна знайомитись\n\n_Час знайти свій ідеальний метч!_",
        "verify_required": "👋 Спочатку підтвердіть номер телефону.",
        "verify_prompt": "👇 Натисніть кнопку нижче, щоб підтвердити номер",
        "discover": "🔍 *Пошук анкет*\n\nГортайте профілі та знаходьте цікавих людей\n\n⭐ Лайк\n❌ Пропуск\n💬 Чат із метчами\n\n_Відкрийте застосунок, щоб почати:_",
        "matches": "💘 *Ваші метчі*\n\nПереглядайте взаємні лайки та продовжуйте спілкування\n\n💬 Пишіть прямо зараз\n📸 Дивіться профілі\n❤️ Керуйте обраним\n\n_Відкрити застосунок:_",
        "profile": "👤 *Профіль: {name}*\n\n📝 Оновіть фото\n✏️ Змініть біо та інтереси\n🎯 Налаштуйте хобі\n💰 Преміум-статус\n\n_Редагувати профіль:_",
        "settings": "⚙️ *Налаштування*\n\n🔎 Дистанція: без обмежень\n👥 Вік: 18-45\n💬 Мова: {lang_name}\n🔔 Сповіщення: увімкнено\n🌙 Тема: темна\n\n_Змінити параметри:_",
        "help": "❓ *Допомога*\n\n🔐 Дані захищені\n🚀 Базовий доступ безкоштовний\n🌍 Працює у 50+ країнах\n💬 Підтримка 24/7\n\n📋 FAQ:\n• Як пройти верифікацію? Натисніть кнопку телефону\n• Як свайпати? Відкрийте застосунок\n• Як писати? Відкрийте метч\n\n_Питання? Використайте /help_",
    },
    "cs": {
        "main_menu": "✨ *Vítejte zpět!*\n\n🎯 *Premium Dating*\n\nProhlížej profily, sleduj shody a upravuj profil\n\nVyber sekci níže:",
        "verify_complete": "🎉 *Ověření dokončeno!*\n\nVše je připraveno pro seznamování.\n\n✓ Profil aktivní\n✓ Plný přístup povolen\n✓ Můžeš začít\n\n_Pojď najít svůj ideální match!_",
        "verify_required": "👋 Nejprve ověř své telefonní číslo.",
        "verify_prompt": "👇 Klepni na tlačítko níže a ověř telefon",
        "discover": "🔍 *Objevování*\n\nProcházej profily a najdi zajímavé lidi\n\n⭐ Lajk\n❌ Přeskočit\n💬 Chat s matchemi\n\n_Otevři aplikaci a začni:_",
        "matches": "💘 *Tvoje shody*\n\nZobraz vzájemné lajky a pokračuj v chatu\n\n💬 Piš hned teď\n📸 Zobraz profily\n❤️ Spravuj oblíbené\n\n_Otevři aplikaci pro shody:_",
        "profile": "👤 *Profil: {name}*\n\n📝 Aktualizuj fotky\n✏️ Uprav bio a zájmy\n🎯 Nastav koníčky\n💰 Premium status\n\n_Upravit profil:_",
        "settings": "⚙️ *Nastavení*\n\n🔎 Vzdálenost: neomezená\n👥 Věk: 18-45\n💬 Jazyk: {lang_name}\n🔔 Notifikace: zapnuto\n🌙 Tmavý režim: zapnutý\n\n_Upravit preference:_",
        "help": "❓ *Nápověda*\n\n🔐 Tvoje data jsou chráněná\n🚀 Základ zdarma, premium funkce dostupné\n🌍 Dostupné ve 50+ zemích\n💬 Komunita 24/7\n\n📋 FAQ:\n• Ověření? Klepni na tlačítko telefonu\n• Swipe? Otevři aplikaci\n• Chat? Otevři match\n\n_Dotazy? Použij /help_",
    },
    "pl": {
        "main_menu": "✨ *Witamy ponownie!*\n\n🎯 *Premium Dating*\n\nPrzeglądaj profile, sprawdzaj dopasowania i zarządzaj profilem\n\nWybierz sekcję poniżej:",
        "verify_complete": "🎉 *Weryfikacja zakończona!*\n\nWszystko gotowe do poznawania nowych osób.\n\n✓ Profil aktywny\n✓ Pełny dostęp przyznany\n✓ Możesz zaczynać\n\n_Czas znaleźć idealny match!_",
        "verify_required": "👋 Najpierw zweryfikuj numer telefonu.",
        "verify_prompt": "👇 Kliknij przycisk poniżej, aby zweryfikować telefon",
        "discover": "🔍 *Odkrywanie*\n\nPrzeglądaj profile i poznawaj ciekawych ludzi\n\n⭐ Polubienie\n❌ Pomiń\n💬 Czat z matchami\n\n_Otwórz aplikację, aby zacząć:_",
        "matches": "💘 *Twoje matche*\n\nZobacz wzajemne polubienia i kontynuuj rozmowy\n\n💬 Napisz teraz\n📸 Zobacz profile\n❤️ Zarządzaj ulubionymi\n\n_Otwórz aplikację i zobacz matche:_",
        "profile": "👤 *Profil: {name}*\n\n📝 Zaktualizuj zdjęcia\n✏️ Edytuj bio i zainteresowania\n🎯 Ustaw hobby\n💰 Status premium\n\n_Edytuj profil:_",
        "settings": "⚙️ *Ustawienia*\n\n🔎 Dystans: bez limitu\n👥 Wiek: 18-45\n💬 Język: {lang_name}\n🔔 Powiadomienia: włączone\n🌙 Tryb ciemny: włączony\n\n_Dostosuj preferencje:_",
        "help": "❓ *Pomoc*\n\n🔐 Twoje dane są chronione\n🚀 Dostęp podstawowy za darmo\n🌍 Dostępne w 50+ krajach\n💬 Wsparcie społeczności 24/7\n\n📋 FAQ:\n• Weryfikacja? Kliknij przycisk telefonu\n• Swipe? Otwórz aplikację\n• Czat? Otwórz match\n\n_Pytania? Użyj /help_",
    },
}

def get_language_keyboard() -> InlineKeyboardMarkup:
    """Language selection keyboard"""
    buttons = [
        [
            InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en"),
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
        ],
        [
            InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang_uk"),
            InlineKeyboardButton(text="🇨🇿 Česky", callback_data="lang_cs"),
        ],
        [
            InlineKeyboardButton(text="🇵🇱 Polski", callback_data="lang_pl"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


LANG_LABELS = {
    "en": "English",
    "ru": "Русский",
    "uk": "Українська",
    "cs": "Čeština",
    "pl": "Polski",
}


BUTTON_TEXTS = {
    "en": {"discovery": "🔍 Discovery", "matches": "💘 Matches", "profile": "👤 Profile", "settings": "⚙️ Settings", "language": "🌍 Language", "help": "❓ Help", "back": "◀️ Back to Menu", "open": "🌐 Open App", "open_setup": "🌐 Open App (setup HTTPS)", "view_matches": "💌 View Matches", "edit_profile": "✏️ Edit Profile", "adjust_settings": "⚙️ Adjust Settings"},
    "ru": {"discovery": "🔍 Поиск", "matches": "💘 Мэтчи", "profile": "👤 Профиль", "settings": "⚙️ Настройки", "language": "🌍 Язык", "help": "❓ Помощь", "back": "◀️ Назад в меню", "open": "🌐 Открыть приложение", "open_setup": "🌐 Открыть (нужен HTTPS)", "view_matches": "💌 Смотреть мэтчи", "edit_profile": "✏️ Редактировать профиль", "adjust_settings": "⚙️ Изменить настройки"},
    "uk": {"discovery": "🔍 Пошук", "matches": "💘 Метчі", "profile": "👤 Профіль", "settings": "⚙️ Налаштування", "language": "🌍 Мова", "help": "❓ Допомога", "back": "◀️ Назад до меню", "open": "🌐 Відкрити застосунок", "open_setup": "🌐 Відкрити (потрібен HTTPS)", "view_matches": "💌 Переглянути метчі", "edit_profile": "✏️ Редагувати профіль", "adjust_settings": "⚙️ Змінити налаштування"},
    "cs": {"discovery": "🔍 Objevování", "matches": "💘 Shody", "profile": "👤 Profil", "settings": "⚙️ Nastavení", "language": "🌍 Jazyk", "help": "❓ Nápověda", "back": "◀️ Zpět do menu", "open": "🌐 Otevřít aplikaci", "open_setup": "🌐 Otevřít (je potřeba HTTPS)", "view_matches": "💌 Zobrazit shody", "edit_profile": "✏️ Upravit profil", "adjust_settings": "⚙️ Upravit nastavení"},
    "pl": {"discovery": "🔍 Odkrywaj", "matches": "💘 Matche", "profile": "👤 Profil", "settings": "⚙️ Ustawienia", "language": "🌍 Język", "help": "❓ Pomoc", "back": "◀️ Wróć do menu", "open": "🌐 Otwórz aplikację", "open_setup": "🌐 Otwórz (wymagany HTTPS)", "view_matches": "💌 Zobacz matche", "edit_profile": "✏️ Edytuj profil", "adjust_settings": "⚙️ Zmień ustawienia"},
}


def build_webapp_url(tab: str | None = None, lang: str = "en") -> str:
    base = WEB_APP_URL.rstrip("/") + "/"
    params = {"lang": lang, "v": WEB_APP_VERSION, "cb": WEB_APP_CACHE_BUSTER}
    if tab:
        params["tab"] = tab
    if WEB_API_URL:
        params["api"] = WEB_API_URL
    return f"{base}?{urlencode(params)}"


def webapp_https_ready() -> bool:
    """Telegram Web Apps require HTTPS URLs in production chats."""
    return WEB_APP_URL.lower().startswith("https://")


# ═══════════════════════════════════════════════════════════════════════════════
# KEYBOARD FACTORY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def get_phone_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    """
    Creates a keyboard with a phone contact request button.
    Premium-styled with emoji.
    """
    tr = TRANSLATIONS.get(lang, TRANSLATIONS["en"])
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=tr["verify_phone"],
                    request_contact=True
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Tap the button below to verify..."
    )


def get_main_menu_inline(lang: str = "en") -> InlineKeyboardMarkup:
    """Main premium menu with navigation buttons"""
    labels = BUTTON_TEXTS.get(lang, BUTTON_TEXTS["en"])
    buttons = [
        [
            InlineKeyboardButton(text=labels["discovery"], callback_data="menu_discovery"),
            InlineKeyboardButton(text=labels["matches"], callback_data="menu_matches"),
        ],
        [
            InlineKeyboardButton(text=labels["profile"], callback_data="menu_profile"),
            InlineKeyboardButton(text=labels["settings"], callback_data="menu_settings"),
        ],
        [
            InlineKeyboardButton(text=labels["language"], callback_data="menu_language"),
            InlineKeyboardButton(text=labels["help"], callback_data="menu_help"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_discovery_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Discovery menu with WebApp button"""
    labels = BUTTON_TEXTS.get(lang, BUTTON_TEXTS["en"])
    if webapp_https_ready():
        open_button = InlineKeyboardButton(
            text=labels["open"],
            web_app=WebAppInfo(url=build_webapp_url("discovery", lang)),
        )
    else:
        open_button = InlineKeyboardButton(
            text=labels["open_setup"],
            callback_data="webapp_setup",
        )

    buttons = [
        [open_button],
        [InlineKeyboardButton(text=labels["back"], callback_data="menu_back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_matches_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Matches menu with WebApp button"""
    labels = BUTTON_TEXTS.get(lang, BUTTON_TEXTS["en"])
    if webapp_https_ready():
        open_button = InlineKeyboardButton(
            text=labels["view_matches"],
            web_app=WebAppInfo(url=build_webapp_url("matches", lang)),
        )
    else:
        open_button = InlineKeyboardButton(
            text=labels["open_setup"],
            callback_data="webapp_setup",
        )

    buttons = [
        [open_button],
        [InlineKeyboardButton(text=labels["back"], callback_data="menu_back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_profile_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Profile menu with bot editing and app preview."""
    labels = BUTTON_TEXTS.get(lang, BUTTON_TEXTS["en"])
    buttons = [
        [InlineKeyboardButton(text=labels["edit_profile"], callback_data="profile_edit_bot")],
    ]
    if webapp_https_ready():
        buttons.append([InlineKeyboardButton(
            text=labels["open"],
            web_app=WebAppInfo(url=build_webapp_url("profile", lang)),
        )])
    else:
        buttons.append([InlineKeyboardButton(
            text=labels["open_setup"],
            callback_data="webapp_setup",
        )])

    buttons.extend([
        [InlineKeyboardButton(text=labels["back"], callback_data="menu_back")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🙋 Мужчина", callback_data="profile_gender_male"),
                InlineKeyboardButton(text="🙋‍♀️ Женщина", callback_data="profile_gender_female"),
            ],
            [InlineKeyboardButton(text="🌀 Другое", callback_data="profile_gender_other")],
            [InlineKeyboardButton(text="✖️ Отмена", callback_data="profile_cancel")],
        ]
    )


def get_looking_for_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👨 Мужчин", callback_data="profile_looking_male"),
                InlineKeyboardButton(text="👩 Женщин", callback_data="profile_looking_female"),
            ],
            [InlineKeyboardButton(text="🌍 Всех", callback_data="profile_looking_everyone")],
            [InlineKeyboardButton(text="✖️ Отмена", callback_data="profile_cancel")],
        ]
    )


def get_photo_step_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово", callback_data="profile_photo_done")],
            [InlineKeyboardButton(text="✖️ Отмена", callback_data="profile_cancel")],
        ]
    )


def get_settings_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Settings menu with WebApp button"""
    labels = BUTTON_TEXTS.get(lang, BUTTON_TEXTS["en"])
    if webapp_https_ready():
        open_button = InlineKeyboardButton(
            text=labels["adjust_settings"],
            web_app=WebAppInfo(url=build_webapp_url("settings", lang)),
        )
    else:
        open_button = InlineKeyboardButton(
            text=labels["open_setup"],
            callback_data="webapp_setup",
        )

    buttons = [
        [open_button],
        [InlineKeyboardButton(text=labels["back"], callback_data="menu_back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_back_button(lang: str = "en") -> InlineKeyboardMarkup:
    """Back to menu button"""
    labels = BUTTON_TEXTS.get(lang, BUTTON_TEXTS["en"])
    buttons = [
        [
            InlineKeyboardButton(text=labels["back"], callback_data="menu_back"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_open_app_inline(lang: str = "en") -> InlineKeyboardMarkup:
    """Single inline button to open Mini App in a clean onboarding flow."""
    labels = BUTTON_TEXTS.get(lang, BUTTON_TEXTS["en"])
    if webapp_https_ready():
        btn = InlineKeyboardButton(
            text=labels["open"],
            web_app=WebAppInfo(url=build_webapp_url(lang=lang)),
        )
    else:
        btn = InlineKeyboardButton(
            text=labels["open_setup"],
            callback_data="webapp_setup",
        )
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


def get_webapp_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    """
    Creates a keyboard with the Web App launch button.
    Shows after successful verification.
    """
    if webapp_https_ready():
        launch_button = KeyboardButton(
            text="✨ Open Premium Dating",
            web_app=WebAppInfo(url=build_webapp_url(lang=lang))
        )
    else:
        launch_button = KeyboardButton(text="✨ Open Premium Dating")

    return ReplyKeyboardMarkup(
        keyboard=[[launch_button]],
        resize_keyboard=True,
        input_field_placeholder="Launch your dating experience..."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS (/start, /profile, /settings, /matches, /help)
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(CommandStart())
async def cmd_start(message: Message, user_db: dict | None = None):
    """
    /start command handler.
    Shows beautiful greeting for new users or main menu for verified users.
    """
    user_id = message.from_user.id
    lang = get_user_lang(user_id, message.from_user.language_code)
    
    # Initialize storage if needed
    init_user_storage(user_id)
    user = USER_STORAGE[user_id]
    
    if user.get("verified") or user_db:
        # Verified user — show compact main menu only
        await message.answer(
            UI_TEXTS[lang]["main_menu"],
            parse_mode="Markdown",
            reply_markup=get_main_menu_inline(lang),
        )
    else:
        # New/unverified user — clean and ordered onboarding (no verification spam)
        tr = TRANSLATIONS.get(lang, TRANSLATIONS["en"])
        await message.answer(
            tr["greeting"],
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.answer(
            tr["select_language"],
            parse_mode="Markdown",
            reply_markup=get_language_keyboard(),
        )


@router.message(Command("profile"))
async def cmd_profile(message: Message, user_db: dict | None = None):
    """
    /profile command handler.
    Shows user profile or prompts to verify.
    """
    user_id = message.from_user.id
    lang = get_user_lang(user_id, message.from_user.language_code)
    
    init_user_storage(user_id)
    user = USER_STORAGE[user_id]
    
    if not (user.get("verified") or user_db):
        await message.answer(UI_TEXTS[lang]["verify_required"])
        return

    profile = await get_profile_data(user_id, message.from_user.first_name)
    await message.answer(
        format_profile_summary(profile),
        parse_mode="Markdown",
        reply_markup=get_profile_keyboard(lang)
    )


@router.message(Command("settings"))
async def cmd_settings(message: Message, user_db: dict | None = None):
    """
    /settings command handler.
    Shows user settings.
    """
    user_id = message.from_user.id
    lang = get_user_lang(user_id, message.from_user.language_code)
    
    init_user_storage(user_id)
    user = USER_STORAGE[user_id]
    
    if not (user.get("verified") or user_db):
        await message.answer(UI_TEXTS[lang]["verify_required"])
        return

    await message.answer(
        UI_TEXTS[lang]["settings"].format(lang_name=LANG_LABELS.get(lang, "English")),
        parse_mode="Markdown",
        reply_markup=get_settings_keyboard(lang)
    )


@router.message(Command("matches"))
async def cmd_matches(message: Message, user_db: dict | None = None):
    """
    /matches command handler.
    Shows user matches.
    """
    user_id = message.from_user.id
    lang = get_user_lang(user_id, message.from_user.language_code)
    
    init_user_storage(user_id)
    user = USER_STORAGE[user_id]
    
    if not (user.get("verified") or user_db):
        await message.answer(UI_TEXTS[lang]["verify_required"])
        return

    await message.answer(
        UI_TEXTS[lang]["matches"],
        parse_mode="Markdown",
        reply_markup=get_matches_keyboard(lang)
    )


@router.message(Command("help"))
async def cmd_help(message: Message, user_db: dict | None = None):
    """
    /help command handler.
    Shows help information.
    """
    user_id = message.from_user.id
    lang = get_user_lang(user_id, message.from_user.language_code)
    
    init_user_storage(user_id)
    
    await message.answer(UI_TEXTS[lang]["help"], parse_mode="Markdown", reply_markup=get_back_button(lang))


@router.message(Command("language"))
async def cmd_language(message: Message):
    """Allow users to change language anytime from command menu."""
    lang = get_user_lang(message.from_user.id, message.from_user.language_code)
    await message.answer(
        TRANSLATIONS[lang]["select_language"],
        parse_mode="Markdown",
        reply_markup=get_language_keyboard(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CALLBACK HANDLERS (inline menu buttons)
# ═══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("lang_"))
async def lang_select(query: CallbackQuery):
    """Language selection handler for onboarding."""
    lang = (query.data or "lang_en").replace("lang_", "", 1)
    if lang not in TRANSLATIONS:
        lang = "en"

    user_id = query.from_user.id
    USER_STORAGE.setdefault(user_id, {})
    USER_STORAGE[user_id]["language"] = lang

    await query.answer(f"✓ {LANG_LABELS.get(lang, 'English')}")
    await query.message.edit_text(
        f"✓ *{LANG_LABELS.get(lang, 'English')}*\n\n{BUTTON_TEXTS.get(lang, BUTTON_TEXTS['en'])['open']}",
        parse_mode="Markdown",
        reply_markup=get_open_app_inline(lang),
    )

@router.callback_query(F.data == "start_begin")
async def start_begin(query: CallbackQuery):
    """Begin button - proceed to phone verification"""
    await query.answer()

@router.callback_query(F.data == "menu_discovery")
async def menu_discovery(query: CallbackQuery, user_db: dict | None = None):
    """
    Discovery menu callback.
    """
    user_id = query.from_user.id
    lang = get_user_lang(user_id, query.from_user.language_code)
    
    init_user_storage(user_id)
    user = USER_STORAGE[user_id]
    
    if not (user.get("verified") or user_db):
        await query.answer(UI_TEXTS[lang]["verify_required"], show_alert=True)
        return

    await query.message.edit_text(
        UI_TEXTS[lang]["discover"],
        parse_mode="Markdown",
        reply_markup=get_discovery_keyboard(lang)
    )
    await query.answer()


@router.callback_query(F.data == "menu_matches")
async def menu_matches(query: CallbackQuery, user_db: dict | None = None):
    """
    Matches menu callback.
    """
    user_id = query.from_user.id
    lang = get_user_lang(user_id, query.from_user.language_code)
    
    init_user_storage(user_id)
    user = USER_STORAGE[user_id]
    
    if not (user.get("verified") or user_db):
        await query.answer(UI_TEXTS[lang]["verify_required"], show_alert=True)
        return

    await query.message.edit_text(
        UI_TEXTS[lang]["matches"],
        parse_mode="Markdown",
        reply_markup=get_matches_keyboard(lang)
    )
    await query.answer()


@router.callback_query(F.data == "menu_profile")
async def menu_profile(query: CallbackQuery, user_db: dict | None = None):
    """
    Profile menu callback.
    """
    user_id = query.from_user.id
    lang = get_user_lang(user_id, query.from_user.language_code)
    
    init_user_storage(user_id)
    user = USER_STORAGE[user_id]
    
    if not (user.get("verified") or user_db):
        await query.answer(UI_TEXTS[lang]["verify_required"], show_alert=True)
        return

    profile = await get_profile_data(user_id, query.from_user.first_name)
    await query.message.edit_text(
        format_profile_summary(profile),
        parse_mode="Markdown",
        reply_markup=get_profile_keyboard(lang)
    )
    await query.answer()


@router.callback_query(F.data == "profile_edit_bot")
async def profile_edit_bot(query: CallbackQuery, state: FSMContext, user_db: dict | None = None):
    lang = get_user_lang(query.from_user.id, query.from_user.language_code)
    if not user_db:
        await query.answer(UI_TEXTS[lang]["verify_required"], show_alert=True)
        return

    profile = await get_profile_data(query.from_user.id, query.from_user.first_name)
    await state.clear()
    await state.set_state(ProfileWizard.first_name)
    await query.message.answer(
        "✏️ Начинаем редактирование анкеты в боте.\n\n"
        f"Текущее имя: {profile.get('first_name') or '-'}\n"
        "Отправьте новое имя одним сообщением.\n"
        "Для отмены напишите: Отмена",
        parse_mode="Markdown",
    )
    await query.answer()


@router.callback_query(F.data == "profile_cancel")
async def profile_cancel(query: CallbackQuery, state: FSMContext):
    await state.clear()
    profile = await get_profile_data(query.from_user.id, query.from_user.first_name)
    lang = get_user_lang(query.from_user.id, query.from_user.language_code)
    await query.message.answer(
        format_profile_summary(profile),
        parse_mode="Markdown",
        reply_markup=get_profile_keyboard(lang),
    )
    await query.answer("Редактирование отменено")


@router.message(ProfileWizard.first_name)
async def profile_first_name_step(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() in CANCEL_WORDS:
        await state.clear()
        await cmd_profile(message, user_db={"telegram_id": message.from_user.id})
        return
    if len(text) < 2:
        await message.answer("Имя слишком короткое. Отправьте нормальное имя.")
        return

    await save_profile_fields(message.from_user.id, first_name=text)
    await state.set_state(ProfileWizard.age)
    await message.answer("Сколько вам лет? Отправьте число от 18 до 80.")


@router.message(ProfileWizard.age)
async def profile_age_step(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() in CANCEL_WORDS:
        await state.clear()
        await cmd_profile(message, user_db={"telegram_id": message.from_user.id})
        return
    if not text.isdigit():
        await message.answer("Возраст должен быть числом от 18 до 80.")
        return

    age = int(text)
    if age < 18 or age > 80:
        await message.answer("Возраст должен быть в диапазоне от 18 до 80.")
        return

    await save_profile_fields(message.from_user.id, age=age)
    await state.set_state(ProfileWizard.gender)
    await message.answer("Выберите ваш пол:", reply_markup=get_gender_keyboard())


@router.callback_query(ProfileWizard.gender, F.data.startswith("profile_gender_"))
async def profile_gender_step(query: CallbackQuery, state: FSMContext):
    gender = (query.data or "").replace("profile_gender_", "", 1)
    await save_profile_fields(query.from_user.id, gender=gender)
    await state.set_state(ProfileWizard.country)
    await query.message.answer("Укажите страну проживания.")
    await query.answer()


@router.message(ProfileWizard.country)
async def profile_country_step(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() in CANCEL_WORDS:
        await state.clear()
        await cmd_profile(message, user_db={"telegram_id": message.from_user.id})
        return
    if len(text) < 2:
        await message.answer("Введите страну текстом, например: Украина.")
        return

    await save_profile_fields(message.from_user.id, country=text)
    await state.set_state(ProfileWizard.city)
    await message.answer("Укажите ваш город.")


@router.message(ProfileWizard.city)
async def profile_city_step(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() in CANCEL_WORDS:
        await state.clear()
        await cmd_profile(message, user_db={"telegram_id": message.from_user.id})
        return
    if len(text) < 2:
        await message.answer("Введите город текстом, например: Днепр.")
        return

    await save_profile_fields(message.from_user.id, city=text)
    await state.set_state(ProfileWizard.bio)
    await message.answer("Напишите коротко о себе. Можно написать Пропустить.")


@router.message(ProfileWizard.bio)
async def profile_bio_step(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() in CANCEL_WORDS:
        await state.clear()
        await cmd_profile(message, user_db={"telegram_id": message.from_user.id})
        return

    bio = "" if text.lower() in SKIP_WORDS else text[:400]
    await save_profile_fields(message.from_user.id, bio=bio)
    await state.set_state(ProfileWizard.looking_for)
    await message.answer("Кого вы хотите видеть в поиске?", reply_markup=get_looking_for_keyboard())


@router.callback_query(ProfileWizard.looking_for, F.data.startswith("profile_looking_"))
async def profile_looking_for_step(query: CallbackQuery, state: FSMContext):
    looking_for = (query.data or "").replace("profile_looking_", "", 1)
    await save_profile_fields(query.from_user.id, looking_for=looking_for)
    await state.set_state(ProfileWizard.photo)
    await query.message.answer(
        "📸 Теперь отправьте фото для анкеты.\n"
        "Можно отправить до 3 фото. Когда закончите, нажмите Готово.",
        reply_markup=get_photo_step_keyboard(),
    )
    await query.answer()


@router.message(ProfileWizard.photo, F.photo)
async def profile_photo_step(message: Message, state: FSMContext):
    photo = message.photo[-1]
    profile = await add_profile_photo(message.from_user.id, photo.file_id)
    count = len(profile.get("photos_urls") or [])
    await message.answer(
        f"Фото сохранено. Сейчас в анкете: {count}/3.\n"
        "Можете отправить ещё фото или нажать Готово.",
        reply_markup=get_photo_step_keyboard(),
    )


@router.callback_query(ProfileWizard.photo, F.data == "profile_photo_done")
async def profile_photo_done(query: CallbackQuery, state: FSMContext):
    await state.clear()
    profile = await get_profile_data(query.from_user.id, query.from_user.first_name)
    lang = get_user_lang(query.from_user.id, query.from_user.language_code)
    await query.message.answer(
        "✅ Анкета обновлена. Она будет показана в mini app, когда заполнены обязательные поля и есть фото.\n\n"
        + format_profile_summary(profile),
        parse_mode="Markdown",
        reply_markup=get_profile_keyboard(lang),
    )
    await query.answer("Анкета сохранена")


@router.message(ProfileWizard.photo)
async def profile_photo_fallback(message: Message, state: FSMContext):
    text = (message.text or "").strip().lower()
    if text in CANCEL_WORDS:
        await state.clear()
        await cmd_profile(message, user_db={"telegram_id": message.from_user.id})
        return
    if text in SKIP_WORDS:
        await state.clear()
        profile = await get_profile_data(message.from_user.id, message.from_user.first_name)
        lang = get_user_lang(message.from_user.id, message.from_user.language_code)
        await message.answer(
            "Фото пока не добавлены. Без фото анкета не появится в поиске.\n\n" + format_profile_summary(profile),
            parse_mode="Markdown",
            reply_markup=get_profile_keyboard(lang),
        )
        return

    await message.answer("Отправьте именно фото как изображение Telegram или нажмите Готово.", reply_markup=get_photo_step_keyboard())


@router.callback_query(F.data == "menu_settings")
async def menu_settings(query: CallbackQuery, user_db: dict | None = None):
    """
    Settings menu callback.
    """
    user_id = query.from_user.id
    lang = get_user_lang(user_id, query.from_user.language_code)
    
    init_user_storage(user_id)
    user = USER_STORAGE[user_id]
    
    if not (user.get("verified") or user_db):
        await query.answer(UI_TEXTS[lang]["verify_required"], show_alert=True)
        return

    await query.message.edit_text(
        UI_TEXTS[lang]["settings"].format(lang_name=LANG_LABELS.get(lang, "English")),
        parse_mode="Markdown",
        reply_markup=get_settings_keyboard(lang)
    )
    await query.answer()


@router.callback_query(F.data == "menu_help")
async def menu_help(query: CallbackQuery):
    """
    Help menu callback.
    """
    user_id = query.from_user.id
    lang = get_user_lang(user_id, query.from_user.language_code)
    
    init_user_storage(user_id)
    
    await query.message.edit_text(
        UI_TEXTS[lang]["help"],
        parse_mode="Markdown",
        reply_markup=get_back_button(lang)
    )
    await query.answer()


@router.callback_query(F.data == "menu_language")
async def menu_language(query: CallbackQuery):
    """Language picker from main menu."""
    lang = get_user_lang(query.from_user.id, query.from_user.language_code)
    await query.message.edit_text(
        TRANSLATIONS[lang]["select_language"],
        parse_mode="Markdown",
        reply_markup=get_language_keyboard(),
    )
    await query.answer()


@router.callback_query(F.data == "menu_back")
async def menu_back(query: CallbackQuery, user_db: dict | None = None):
    """
    Back to main menu callback.
    """
    lang = get_user_lang(query.from_user.id, query.from_user.language_code)
    if not user_db:
        await query.answer(UI_TEXTS[lang]["verify_required"], show_alert=True)
        return

    await query.message.edit_text(
        UI_TEXTS[lang]["main_menu"],
        parse_mode="Markdown",
        reply_markup=get_main_menu_inline(lang)
    )
    await query.answer()


@router.callback_query(F.data == "webapp_setup")
async def webapp_setup(query: CallbackQuery):
    """Explain why Web App cannot open with non-HTTPS URL."""
    lang = get_user_lang(query.from_user.id, query.from_user.language_code)
    await query.answer("Mini App needs HTTPS URL", show_alert=True)
    await query.message.answer(
        "🔒 *Mini App setup required*\n\n"
        "Telegram opens embedded Web Apps only via HTTPS.\n"
        f"Current URL: `{WEB_APP_URL}`\n\n"
        "To enable full in-app mode:\n"
        "1. Expose frontend with HTTPS (Cloudflare Tunnel / ngrok / deploy).\n"
        "2. Put HTTPS link into .env as WEB_APP_URL.\n"
        "3. Restart bot and press menu buttons again.",
        parse_mode="Markdown",
        reply_markup=get_back_button(lang)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGE HANDLERS (contact sharing & other messages)
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(F.contact)
async def handle_contact(message: Message, user_db: dict | None = None):
    """
    Handles contact sharing.
    Marks user as verified and saves phone number.
    """
    user_id = message.from_user.id
    phone = message.contact.phone_number
    if not phone.startswith("+"):
        phone = f"+{phone}"
    
    lang = get_user_lang(user_id, message.from_user.language_code)
    
    # Mark user as verified
    mark_user_verified(user_id, phone, lang)
    
    await message.answer(
        UI_TEXTS[lang]["verify_complete"],
        parse_mode="Markdown",
        reply_markup=get_main_menu_inline(lang)
    )


@router.message(F.text == "✨ Open Premium Dating")
async def handle_webapp_text(message: Message, user_db: dict):
    """
    Handles web app button press.
    """
    lang = get_user_lang(message.from_user.id, message.from_user.language_code)
    await message.answer(
        "🚀 *Launching App*\n\n"
        "Opening Premium Dating Web App...",
        parse_mode="Markdown",
        reply_markup=get_main_menu_inline(lang)
    )


@router.message()
async def handle_other_messages(message: Message, user_db: dict | None = None):
    """
    Handles all other messages.
    Reminds unverified users to share contact.
    """
    lang = get_user_lang(message.from_user.id, message.from_user.language_code)
    if not user_db:
        # User not verified - show phone request again
        await message.answer(
            UI_TEXTS[lang]["verify_prompt"],
            reply_markup=get_phone_keyboard(lang)
        )
    else:
        # Verified user - guide them to the app
        await message.answer(
            "💬 To access all features, use the main menu:\n\n"
            "/start - Main menu\n"
            "/profile - Your profile\n"
            "/matches - See matches\n"
            "/settings - Preferences\n"
            "/help - Help",
            reply_markup=get_main_menu_inline(lang)
        )


async def create_db_pool():
    """
    Creates database connection pool.
    """
    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=5,
        max_size=20,
        command_timeout=60
    )


async def main():
    """
    Main bot initialization and startup.
    """
    global DB_POOL

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Please add it to your .env file.")

    # Initialize bot and dispatcher
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # Make command menu available in chat so users can tap /start without typing.
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Start / open main menu"),
            BotCommand(command="language", description="Change language"),
            BotCommand(command="profile", description="Open your profile"),
            BotCommand(command="matches", description="View your matches"),
            BotCommand(command="settings", description="Open settings"),
            BotCommand(command="help", description="Help and FAQ"),
        ]
    )
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    db_pool = None
    if RUN_WITHOUT_DB:
        logger.warning("RUN_WITHOUT_DB=true: bot will use in-memory user storage (for testing only).")
    else:
        logger.info("Connecting to database...")
        db_pool = await create_db_pool()
        DB_POOL = db_pool
        logger.info("Database connected!")
    
    # Register middleware for both messages and inline callbacks
    phone_verification_middleware = PhoneVerificationMiddleware(db_pool, get_phone_keyboard)
    router.message.middleware(phone_verification_middleware)
    router.callback_query.middleware(phone_verification_middleware)
    
    # Register router
    dp.include_router(router)
    
    # Log startup info
    logger.info("🚀 Bot started! Premium Dating TWA is live.")
    logger.info("📱 Phone verification: Enabled")
    logger.info("🌐 Web App URL: " + WEB_APP_URL)
    logger.info("🔗 Web App launch URL sample: " + build_webapp_url(lang="ru"))
    logger.info("⚙️ Features: Discovery, Matches, Profile, Settings, Help")
    logger.info("💬 Commands: /start, /language, /profile, /settings, /matches, /help")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if db_pool:
            await db_pool.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
