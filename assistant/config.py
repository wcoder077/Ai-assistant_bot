from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# override=True: tizimda tasodifan bir xil nomli o'zgaruvchi bo'lsa ham .env ustun turadi.
load_dotenv(BASE_DIR / ".env", override=True)


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} topilmadi. .env faylini .env.example asosida to'ldiring."
        )
    return value


def _optional(name: str) -> str | None:
    return os.getenv(name, "").strip() or None


def _int_set(raw: str) -> frozenset[int]:
    return frozenset(int(part) for part in raw.replace(";", ",").split(",") if part.strip())


@dataclass(frozen=True)
class Provider:
    name: str
    api_key: str
    base_url: str | None
    model: str


# Ma'lum provayderlarning standart manzili va modeli. Har birini .env dagi
# <NOM>_BASE_URL / <NOM>_MODEL bilan almashtirish mumkin.
PROVIDER_DEFAULTS: dict[str, tuple[str | None, str]] = {
    "openai": (None, "gpt-5.4-mini"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.5-flash"),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "openrouter": ("https://openrouter.ai/api/v1", ""),
    "ollama": ("http://localhost:11434/v1", "llama3.1"),
}


def _load_providers() -> tuple[Provider, ...]:
    order = [
        name.strip().lower()
        for name in os.getenv("PROVIDERS", "gemini,openai").replace(";", ",").split(",")
        if name.strip()
    ]

    providers: list[Provider] = []
    for name in order:
        prefix = name.upper()
        default_url, default_model = PROVIDER_DEFAULTS.get(name, (None, ""))
        api_key = os.getenv(f"{prefix}_API_KEY", "").strip()
        base_url = os.getenv(f"{prefix}_BASE_URL", "").strip() or default_url
        model = os.getenv(f"{prefix}_MODEL", "").strip() or default_model

        if not api_key:
            if name != "ollama":
                continue  # kaliti yo'q provayder navbatdan chiqariladi
            api_key = "ollama"  # mahalliy server kalit talab qilmaydi

        if not model:
            raise RuntimeError(f"{prefix}_MODEL .env faylida ko'rsatilmagan.")
        if not base_url and name != "openai":
            raise RuntimeError(f"{prefix}_BASE_URL .env faylida ko'rsatilmagan.")

        providers.append(Provider(name, api_key, base_url, model))

    if not providers:
        raise RuntimeError(
            "Hech qanday AI provayder sozlanmagan. .env da PROVIDERS ro'yxatidagi "
            "kamida bittasining <NOM>_API_KEY qatorini to'ldiring."
        )
    return tuple(providers)


TELEGRAM_BOT_TOKEN = _require("TELEGRAM_BOT_TOKEN")
PROVIDERS = _load_providers()

# Ikkalasi ham ixtiyoriy: bo'sh bo'lsa so'rovga umuman qo'shilmaydi, shunda kod
# har qanday OpenAI-mos provayder bilan ishlayveradi.
MAX_TOKENS = int(os.getenv("BOT_MAX_TOKENS") or 0) or None
REASONING_EFFORT = _optional("BOT_REASONING_EFFORT")

DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "assistant.db")))
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Asia/Tashkent").strip()

# Bo'sh bo'lsa hamma foydalanuvchi bot bilan gaplasha oladi — API xarajati sizdan ketadi.
ALLOWED_USER_IDS = _int_set(os.getenv("ALLOWED_USER_IDS", ""))

HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "40"))
MAX_TOOL_ITERATIONS = int(os.getenv("MAX_TOOL_ITERATIONS", "12"))
MAX_FILE_CHARS = int(os.getenv("MAX_FILE_CHARS", "200000"))
SEARCH_RESULTS = int(os.getenv("SEARCH_RESULTS", "5"))
