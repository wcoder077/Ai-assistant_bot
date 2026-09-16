# Telegram AI yordamchi

OpenAI-mos har qanday model bilan ishlaydigan shaxsiy Telegram yordamchisi
(Google Gemini, OpenAI, Groq, OpenRouter yoki o'z serveringizdagi Ollama).

**Bir nechta AI navbat bilan:** `.env` da bir nechta provayder ko'rsatsangiz, bot
ularni navbat bilan ishlatadi. Birinchisining limiti tugasa yoki xato bersa,
avtomatik keyingisiga o'tadi va foydalanuvchi buni sezmaydi ham.

**Nimalar qila oladi:**

- Erkin suhbat, savollarga javob, matn yozish va tarjima
- Eslatmalar — bir martalik, har kunlik va har haftalik ("ertaga 9 da eslat", "har kuni 7 da turg'iz")
- Vazifalar ro'yxati (qo'shish, ko'rish, bajarilgan deb belgilash, o'chirish)
- Internetdan qidirish — DuckDuckGo orqali, alohida kalit kerak emas
- Fayllarni o'qish — PDF, Word (.docx), Excel (.xlsx), matn fayllari, rasmlar
- Uzoq muddatli xotira — ismingiz, ishingiz, odatlaringizni eslab qoladi

**Nimalarni qila olmaydi:** Telegram akkauntingizni boshqarish, boshqa odamlarga xabar yuborish,
qo'ng'iroq qilish, to'lov qilish, ovozli xabar va videoni tushunish.

---

## 1. Tayyorgarlik

Ikkita kalit kerak:

1. **Telegram bot tokeni** — [@BotFather](https://t.me/BotFather) ga `/newbot` yozing.
2. **Model uchun API kaliti** — quyidagilardan birini tanlang:

| Provayder | Kalit qayerdan | Narxi |
|---|---|---|
| OpenAI | [platform.openai.com](https://platform.openai.com) | Pullik (`gpt-5.4-nano` eng arzoni) |
| Google Gemini | [aistudio.google.com](https://aistudio.google.com) | **Bepul tarifi bor** (kunlik limit bilan) |
| Groq | [console.groq.com](https://console.groq.com) | **Bepul tarifi bor**, juda tez |
| OpenRouter | [openrouter.ai](https://openrouter.ai) | Ba'zi modellari bepul |
| Ollama | o'z kompyuteringiz/serveringiz | Butunlay bepul, kuchli qurilma kerak |

### Navbatni sozlash

`.env` da tartibni `PROVIDERS` belgilaydi — birinchisi asosiy:

```ini
PROVIDERS=gemini,openai

GEMINI_API_KEY=...        # bepul tarif avval ishlatiladi
GEMINI_MODEL=gemini-2.5-flash

OPENAI_API_KEY=...        # Gemini limiti tugaganda ishga tushadi
OPENAI_MODEL=gpt-5.4-mini
```

Kaliti kiritilmagan provayder navbatdan avtomatik chiqariladi, shuning uchun
bittasini sinab ko'rish uchun qolganlarini bo'sh qoldirsangiz bo'ladi. Ro'yxatga
xohlagancha provayder qo'shish mumkin (`groq`, `openrouter`, `ollama` yoki o'zingiznikini
`<NOM>_API_KEY` + `<NOM>_BASE_URL` + `<NOM>_MODEL` bilan).

Model nomlari tez yangilanadi — provayder saytidan joriy nomini tekshirib oling.
Model nomi noto'g'ri bo'lsa bot jim turmaydi: log'da `model topilmadi` deb yozadi
va keyingi provayderga o'tadi.

## 2. Kompyuterda ishga tushirish

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

`.env` faylini ochib `TELEGRAM_BOT_TOKEN` va kamida bitta provayder kalitini
(masalan `GEMINI_API_KEY`) to'ldiring, so'ng:

```bash
.venv\Scripts\python -m assistant.bot
```

Botga Telegram'da `/start` yozing. So'ng `/id` yuboring — u sizning ID raqamingizni qaytaradi.
Shu raqamni `.env` dagi `ALLOWED_USER_IDS` ga yozib qo'ying, aks holda botni topgan **har kim**
undan foydalana oladi va API xarajati sizning hisobingizdan ketadi.

> Kalitlarni faqat `.env` ichiga yozing. `.env.example` — shablon fayl, u git'ga tushadi.

## 3. Serverda 24/7 ishlatish (Docker — tavsiya etiladi)

VPS (Hetzner, DigitalOcean, Contabo) oling, Docker o'rnating, fayllarni ko'chiring:

```bash
cp .env.example .env   # va to'ldiring
docker compose up -d --build
docker compose logs -f
```

Bot avtomatik qayta ishga tushadi, ma'lumotlar `./data` papkasida saqlanadi.
Yangilash: fayllarni yangilab `docker compose up -d --build`.

### Docker'siz variant (systemd)

`/etc/systemd/system/tg-assistant.service`:

```ini
[Unit]
Description=Telegram AI yordamchi
After=network-online.target

[Service]
WorkingDirectory=/opt/tg-assistant
ExecStart=/opt/tg-assistant/.venv/bin/python -m assistant.bot
Restart=always
RestartSec=5
User=botuser

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now tg-assistant
sudo journalctl -u tg-assistant -f
```

## 4. Sozlamalar (`.env`)

| O'zgaruvchi | Standart | Izoh |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Majburiy |
| `PROVIDERS` | `gemini,openai` | Navbat tartibi. Birinchisi asosiy |
| `<NOM>_API_KEY` | — | Provayder kaliti. Bo'sh bo'lsa o'sha provayder o'tkazib yuboriladi |
| `<NOM>_MODEL` | provayderga qarab | Model nomi, masalan `GEMINI_MODEL` |
| `<NOM>_BASE_URL` | ma'lumlar uchun avtomatik | Faqat noma'lum provayder uchun kerak |
| `ALLOWED_USER_IDS` | bo'sh | Ruxsat etilgan ID'lar, vergul bilan. **Bo'sh = hamma uchun ochiq** |
| `BOT_MAX_TOKENS` | bo'sh | Javob uzunligi chegarasi. Bo'sh = yuborilmaydi |
| `BOT_REASONING_EFFORT` | bo'sh | `minimal`…`high`. Faqat reasoning modellar uchun |
| `DEFAULT_TIMEZONE` | `Asia/Tashkent` | Botga "vaqt mintaqamni o'zgartir" deb ham aytish mumkin |
| `DB_PATH` | `data/assistant.db` | SQLite — suhbat, eslatma, vazifa va xotira shu yerda |
| `HISTORY_LIMIT` | `40` | Modelga beriladigan oxirgi xabarlar soni |
| `SEARCH_RESULTS` | `5` | Bitta qidiruvda olinadigan natijalar soni |

## 5. Xarajatni kamaytirish

- Bepul provayderni navbatda **birinchi** qo'ying: `PROVIDERS=gemini,openai` —
  shunda pullik OpenAI faqat bepul limit tugagandan keyin ishlaydi
- OpenAI'da `OPENAI_MODEL=gpt-5.4-nano` (eng arzoni) yoki `gpt-5.4-mini`
- `BOT_REASONING_EFFORT=minimal` yoki `low`
- `HISTORY_LIMIT` ni kamaytiring — har so'rovda kamroq kontekst yuboriladi
- Vaqti-vaqti bilan `/reset` yuborib suhbat tarixini tozalang

## 6. Buyruqlar

| Buyruq | Vazifasi |
|---|---|
| `/start` | Boshlash va tanishtiruv |
| `/help` | Imkoniyatlar ro'yxati |
| `/reset` | Suhbat tarixini tozalash (eslatma va vazifalar saqlanadi) |
| `/id` | Telegram ID raqamingiz |

## 7. Loyiha tuzilishi

```
assistant/
├── bot.py          Telegram handlerlari, ishga tushirish
├── agent.py        Model bilan muloqot sikli, system prompt
├── tools.py        Model chaqira oladigan vositalar (qidiruv, eslatma, vazifa, xotira)
├── reminders.py    Eslatmalarni rejalashtirish va yetkazish
├── files.py        Telegram fayllarini model uchun tayyorlash
├── formatting.py   Markdown → Telegram HTML
├── db.py           SQLite
└── config.py       .env sozlamalari
```
# Ai-assistant_bot
