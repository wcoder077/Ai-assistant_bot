from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import openai
from openai import AsyncOpenAI
from telegram.ext import JobQueue

from . import config
from .db import Database
from .tools import TOOL_SCHEMAS, WEEKDAYS, ToolContext, execute_tool

log = logging.getLogger(__name__)

# Navbatdagi provayderlar: birinchisi asosiy, u ishlamasa keyingisiga o'tiladi.
# max_retries=1 — limiti tugagan provayderda uzoq kutib turmaslik uchun.
CLIENTS = [
    (
        provider,
        AsyncOpenAI(
            api_key=provider.api_key,
            base_url=provider.base_url,
            max_retries=1,
            timeout=120.0,
        ),
    )
    for provider in config.PROVIDERS
]


def _reason(error: Exception) -> str:
    if isinstance(error, openai.RateLimitError):
        return "limit yoki balans tugagan"
    if isinstance(error, openai.AuthenticationError):
        return "API kaliti noto'g'ri"
    if isinstance(error, openai.PermissionDeniedError):
        return "ruxsat berilmagan"
    if isinstance(error, openai.NotFoundError):
        return "model topilmadi"
    if isinstance(error, openai.BadRequestError):
        return "so'rovni qabul qilmadi"
    if isinstance(error, openai.APIConnectionError):
        return "ulanib bo'lmadi"
    if isinstance(error, openai.APIStatusError):
        return f"xato {error.status_code}"
    return type(error).__name__


class AllProvidersFailed(Exception):
    def __init__(self, failures: list[tuple[str, Exception]]) -> None:
        self.failures = failures
        super().__init__(
            "; ".join(f"{name}: {_reason(error)}" for name, error in failures)
        )

    def summary(self) -> str:
        return "\n".join(f"• {name} — {_reason(error)}" for name, error in self.failures)

SYSTEM_PROMPT = """Sen — Telegram'da ishlaydigan shaxsiy yordamchi va suhbatdoshsan.

Muloqot uslubi:
- Foydalanuvchi qaysi tilda yozsa, o'sha tilda javob ber (asosan o'zbekcha).
- Iliq, tabiiy va samimiy gapir; quruq rasmiyatchilikdan qoch. Suhbatdosh sifatida
  savol berishdan, hazillashishdan va hamdard bo'lishdan tortinma.
- Telegram uchun yoz: qisqa xatboshilar, odatda 1-6 jumla. Uzun matnni faqat
  so'ralganda yoz. Bezash uchun **qalin**, `kod` va [havola](url) ishlatsa bo'ladi;
  jadval va katta sarlavhalar ishlatma.
- Hech qachon fakt to'qima. Bilmasang — bilmasligingni ayt yoki web_search bilan tekshir.

Vositalar:
- Yangi yoki o'zgaruvchan ma'lumot kerak bo'lsa (yangiliklar, narxlar, ob-havo,
  "hozir", "bugun", "oxirgi") — web_search ishlat va javobda manbaga havola ber.
  Xotirangdagi ma'lumot eskirgan bo'lishi mumkinligini unutma.
- Vaqtga bog'liq iltimoslar ("ertaga eslat", "har kuni 7 da turg'iz") —
  create_reminder. Vaqtni doim 'YYYY-MM-DDTHH:MM' ko'rinishida ber.
- Reja, ro'yxat, "esdan chiqmasin" turidagi ishlar — add_todo va qolgan vazifa
  vositalari.
- Foydalanuvchi haqidagi doimiy ma'lumot (ismi, ishi, oila, yoqtirishlari, muhim
  sanalar) uchun — remember. Faqat kelajakda kerak bo'ladiganini saqla.
- Vosita chaqirgandan keyin natijani sodda tilda tushuntir. Ichki ID raqamlarni
  faqat kerak bo'lganda ko'rsat.
- Vositani haqiqatan ishlatmasdan turib "eslatma qo'ydim" yoki "eslab qoldim" dema.

Vaqt:
- Har bir xabar oxirida joriy sana va vaqt beriladi. "Ertaga", "2 soatdan keyin"
  kabi nisbiy vaqtlarni shunga qarab hisobla.

Cheklovlar — bularni so'rashsa ochiq ayt:
- Faqat shu chatda ishlaysan: boshqa odamlarga xabar yubora olmaysan, foydalanuvchining
  Telegram akkaunti yoki boshqa ilovalarini boshqara olmaysan, qo'ng'iroq qila olmaysan,
  to'lov qila olmaysan.
- Ovozli xabar va videoni tushunmaysan — matn so'ra.
"""


async def _build_system(db: Database, chat_id: int, tz: str) -> dict[str, str]:
    parts = [SYSTEM_PROMPT, f"Foydalanuvchining vaqt mintaqasi: {tz}."]
    memories = await db.list_memories(chat_id)
    if memories:
        facts = "\n".join(f"- [{row['id']}] {row['fact']}" for row in memories)
        parts.append(f"Foydalanuvchi haqida eslab qolinganlar:\n{facts}")
    return {"role": "system", "content": "\n\n".join(parts)}


def _sanitize(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tarix oynasi to'liq bo'lmagan vosita siklidan boshlanmasligi/tugamasligi kerak."""
    while history and history[0].get("role") != "user":
        history.pop(0)
    while history and history[-1].get("role") == "assistant" and history[-1].get("tool_calls"):
        history.pop()
    return history


def _user_message(blocks: list[dict[str, Any]], tz: str) -> dict[str, Any]:
    now = datetime.now(ZoneInfo(tz))
    stamp = f"[joriy vaqt: {now:%Y-%m-%d %H:%M}, {WEEKDAYS[now.weekday()]}]"
    parts = [*blocks, {"type": "text", "text": stamp}]

    if all(part["type"] == "text" for part in parts):
        return {"role": "user", "content": "\n\n".join(part["text"] for part in parts)}
    return {"role": "user", "content": parts}


def _assistant_message(message: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"role": "assistant"}
    if message.content:
        result["content"] = message.content
    if message.tool_calls:
        tool_calls = []
        for call in message.tool_calls:
            if getattr(call, "function", None) is None:
                continue
            entry: dict[str, Any] = {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            # Gemini's OpenAI-compat layer attaches a thought_signature to
            # each function call and rejects the next turn with a 400 if it
            # isn't echoed back verbatim (see ai.google.dev/gemini-api/docs/
            # thought-signatures). Other providers don't set this field.
            extra = getattr(call, "extra_content", None)
            if extra:
                entry["extra_content"] = extra
            tool_calls.append(entry)
        result["tool_calls"] = tool_calls
    return result


async def _complete(system: dict[str, str], messages: list[dict[str, Any]]) -> Any:
    """Provayderlarni navbat bilan sinaydi; birortasi javob bersa o'shani qaytaradi."""
    kwargs: dict[str, Any] = {"messages": [system, *messages], "tools": TOOL_SCHEMAS}
    if config.MAX_TOKENS:
        kwargs["max_completion_tokens"] = config.MAX_TOKENS
    if config.REASONING_EFFORT:
        kwargs["reasoning_effort"] = config.REASONING_EFFORT

    failures: list[tuple[str, Exception]] = []
    for provider, client in CLIENTS:
        try:
            response = await client.chat.completions.create(model=provider.model, **kwargs)
        except openai.OpenAIError as error:
            log.warning("Provayder '%s' ishlamadi — %s", provider.name, _reason(error))
            failures.append((provider.name, error))
            continue

        if failures:
            log.info("'%s' provayderiga o'tildi", provider.name)
        return response

    raise AllProvidersFailed(failures)


async def respond(
    db: Database,
    job_queue: JobQueue,
    chat_id: int,
    conversation_id: int,
    user_blocks: list[dict[str, Any]],
    tz: str,
) -> str:
    user_message = _user_message(user_blocks, tz)
    messages: list[dict[str, Any]] = _sanitize(
        await db.load_history(conversation_id, config.HISTORY_LIMIT)
    )
    messages.append(user_message)
    await db.add_message(chat_id, conversation_id, user_message)

    ctx = ToolContext(db=db, job_queue=job_queue, chat_id=chat_id, tz=tz)
    system = await _build_system(db, chat_id, tz)
    collected: list[str] = []

    for _ in range(config.MAX_TOOL_ITERATIONS):
        response = await _complete(system, messages)
        choice = response.choices[0]

        assistant_message = _assistant_message(choice.message)
        messages.append(assistant_message)
        await db.add_message(chat_id, conversation_id, assistant_message)

        if choice.message.content:
            collected.append(choice.message.content)

        if not assistant_message.get("tool_calls"):
            if choice.finish_reason == "length":
                collected.append("(Javob uzunlik chegarasiga yetdi — davomini so'rasangiz bo'ladi.)")
            break

        for call in assistant_message["tool_calls"]:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                output, is_error = f"Xato: '{name}' argumentlari noto'g'ri formatda.", True
            else:
                output, is_error = await execute_tool(ctx, name, args)
            if is_error:
                log.warning("Vosita xatosi: %s -> %s", name, output)

            tool_message = {"role": "tool", "tool_call_id": call["id"], "content": output}
            messages.append(tool_message)
            await db.add_message(chat_id, conversation_id, tool_message)
    else:
        collected.append("(Ishni oxirigacha yetkaza olmadim — qayta urinib ko'ring.)")

    return "\n\n".join(collected) or "Javob bo'sh chiqdi, qayta yozib ko'ring."
