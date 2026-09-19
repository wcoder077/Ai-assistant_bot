# Loyiha tuzilishi va ishlash mantiqi

Bu hujjat kodni birinchi marta ochgan odam (yoki 3 oydan keyingi o'zing) uchun:
qaysi fayl nima qiladi, xabar qayerdan qayerga boradi va yangi imkoniyat qo'shish
uchun qaysi faylga tegish kerak.

---

## 1. Umumiy g'oya

Bot — bu **Telegram bilan AI model o'rtasidagi vositachi**. Uning ishi uch bosqich:

```
Foydalanuvchi xabari  →  modelga yuborish  →  javobni qaytarish
```

Lekin model yolg'iz o'zi eslatma qo'ya olmaydi, internetdan qidira olmaydi.
Shuning uchun biz unga **vositalar** (tools) beramiz. Model "menga qidiruv kerak"
deb aytadi, kodimiz qidiradi va natijani modelga qaytaradi. Bu sikl
**agent loop** deb ataladi — `agent.py` ning asosiy vazifasi shu.

---

## 2. Qatlamlar

Loyiha 8 ta faylga bo'lingan. Har biri **bitta ish** qiladi:

```
                 ┌──────────────────────────────────────┐
  Telegram  ───► │  bot.py        kirish nuqtasi        │
                 │                handler'lar, tugmalar │
                 └───────────────┬──────────────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
        ┌───────────┐     ┌────────────┐     ┌──────────────┐
        │ files.py  │     │  agent.py  │     │ formatting.py│
        │ fayl→matn │     │ model sikli│     │ MD→HTML      │
        └───────────┘     └─────┬──────┘     └──────────────┘
                                │
                         ┌──────▼──────┐
                         │  tools.py   │  model chaqiradigan
                         │  11 vosita  │  funksiyalar
                         └──┬───────┬──┘
                            │       │
                   ┌────────▼──┐ ┌──▼────────────┐
                   │   db.py   │ │ reminders.py  │
                   │  SQLite   │ │ vaqt bo'yicha │
                   └───────────┘ └───────────────┘

           config.py  ──►  hammasiga sozlama beradi (.env dan)
```

**Muhim qoida:** o'qlar faqat pastga qaraydi. `db.py` hech qachon `bot.py` ni
import qilmaydi. Shuning uchun `db.py` ni alohida test qilish mumkin, va
Telegram'ni ertaga boshqa narsaga almashtirsang, pastki qatlamlar o'zgarmaydi.

---

## 3. Har bir fayl nima qiladi

### `config.py` — sozlamalar (103 qator)

`.env` faylini o'qiydi va butun loyihaga o'zgarmas qiymatlar beradi.

Eng qiziq qismi — **provayderlar navbati**. `_load_providers()` funksiyasi
`PROVIDERS=gemini,openai` qatorini o'qib, har biri uchun `<NOM>_API_KEY`,
`<NOM>_BASE_URL`, `<NOM>_MODEL` ni yig'adi. Kaliti yo'q provayder navbatdan
tushib qoladi.

Bu ishlaydi, chunki Gemini, Groq, OpenRouter va Ollama — hammasi **OpenAI API
formatini** qo'llab-quvvatlaydi. Ya'ni bitta `openai` kutubxonasi bilan
hammasiga murojaat qilsa bo'ladi, faqat `base_url` ni almashtirish kifoya.

> Diqqat: bu fayl import bo'lgan zahoti `.env` ni o'qiydi va token yo'q bo'lsa
> `RuntimeError` beradi. Bu qasddan — bot yarim sozlangan holda ishga tushmasin.

### `db.py` — ma'lumotlar bazasi (336 qator)

SQLite bilan ishlaydigan yagona fayl. 6 ta jadval:

| Jadval | Nima saqlaydi | Suhbatga bog'liqmi |
|---|---|---|
| `users` | chat_id, vaqt mintaqasi, joriy suhbat | — |
| `conversations` | suhbat mavzulari (ChatGPT'dagi kabi) | — |
| `messages` | har bir xabar (JSON holida) | ha |
| `memories` | foydalanuvchi haqidagi doimiy faktlar | **yo'q** |
| `todos` | vazifalar ro'yxati | **yo'q** |
| `reminders` | eslatmalar | **yo'q** |

Bu dizayndagi asosiy qaror: **eslatma, vazifa va xotira suhbatdan mustaqil**.
Foydalanuvchi "Yangi suhbat" bossa, tarix tozalanadi, lekin eslatmalari joyida
qoladi. Shuning uchun ularda `conversation_id` ustuni yo'q, faqat `chat_id` bor.

`_migrate()` — eski bazalarni yangi sxemaga o'tkazadi. Loyihaga "suhbatlar"
keyinroq qo'shilgani uchun, eski bazada `conversation_id` ustuni yo'q edi.
Migratsiya uni qo'shadi va eski xabarlarni "Old chat" nomli suhbatga bog'laydi.

> Eslatma: `add_message` har bir xabardan keyin `commit()` qiladi. Bitta
> foydalanuvchili bot uchun bu normal. Yuzlab foydalanuvchi bo'lsa — bu tor joy
> bo'ladi va SQLite'dan PostgreSQL'ga o'tish kerak bo'ladi.

### `agent.py` — model bilan muloqot sikli (233 qator)

Loyihaning **yuragi**. `respond()` funksiyasi:

1. Bazadan oxirgi `HISTORY_LIMIT` ta xabarni oladi
2. `_sanitize()` — tarix oynasi yarim qolgan vosita siklidan boshlanmasligini
   tekshiradi (aks holda model 400 xato beradi)
3. `_build_system()` — system prompt + foydalanuvchi haqida eslab qolinganlar
4. `_complete()` — provayderlarni **navbat bilan** sinaydi: birinchisi xato
   bersa, ikkinchisiga o'tadi. Hammasi yiqilsa `AllProvidersFailed`
5. Model vosita chaqirsa — `execute_tool()` ni ishlatadi, natijani tarixga
   qo'shadi va **qayta modelga yuboradi**. Bu `MAX_TOOL_ITERATIONS` marta
   takrorlanishi mumkin (cheksiz sikldan himoya)

`_assistant_message()` dagi `extra_content` — Gemini'ga xos nozik joy.
Gemini har bir vosita chaqiruviga "thought signature" biriktiradi va keyingi
so'rovda uni aynan qaytarmasang, 400 xato beradi. Boshqa provayderlarda bu
maydon yo'q.

### `tools.py` — model chaqira oladigan funksiyalar (341 qator)

11 ta vosita, uch qismdan iborat:

1. **`_TOOLS`** — JSON schema. Bu modelga "sen nima qila olasan" deb aytadi.
   Tavsif (`description`) qanchalik aniq bo'lsa, model shunchalik to'g'ri
   ishlatadi. Bu prompt engineering'ning bir qismi.
2. **`_xxx()` funksiyalari** — haqiqiy ish shu yerda bajariladi
3. **`HANDLERS`** — nom → funksiya lug'ati

Vositalar ro'yxati:

| Vosita | Vazifasi |
|---|---|
| `web_search` | DuckDuckGo orqali qidiradi (kalit kerak emas) |
| `create_reminder` / `list_reminders` / `cancel_reminder` | eslatmalar |
| `add_todo` / `list_todos` / `complete_todo` / `delete_todo` | vazifalar |
| `remember` / `forget` | uzoq muddatli xotira |
| `set_timezone` | vaqt mintaqasi |

`execute_tool()` har qanday xatoni ushlab, uni **matn sifatida** modelga
qaytaradi. Bu muhim: bot yiqilmaydi, model xatoni o'qib, foydalanuvchiga
tushuntiradi yoki boshqacha urinib ko'radi.

### `reminders.py` — vaqt bo'yicha ishga tushirish (96 qator)

`python-telegram-bot` ning `JobQueue` (ichida APScheduler) ustidagi yupqa qatlam.

- `schedule()` — bir martalik uchun `run_once`, takrorlanuvchi uchun `run_daily`
- `restore_all()` — **bot qayta ishga tushganda** bazadagi faol eslatmalarni
  qayta rejalashtiradi. Bu bo'lmasa, har restart'da eslatmalar yo'qoladi
- Vaqti o'tib ketgan eslatma (bot o'chiq turgan) 5 soniyadan keyin
  "(delayed because the bot was offline)" izohi bilan yuboriladi

Nozik joy: haftalik eslatmada PTB'da hafta kunlari 0=yakshanba, Python'da
0=dushanba. Shuning uchun `(weekday() + 1) % 7` konvertatsiyasi bor.

### `files.py` — fayllarni model uchun tayyorlash (147 qator)

`collect_blocks()` Telegram xabarini modelga tushunarli "content blok"larga
aylantiradi:

| Nima keldi | Nimaga aylanadi |
|---|---|
| Rasm | base64 `image_url` bloki |
| PDF | `pypdf` bilan matn |
| Word (.docx) | `python-docx` bilan matn + jadvallar |
| Excel (.xlsx) | `openpyxl` bilan matn |
| Matn fayllari (40+ kengaytma) | to'g'ridan-to'g'ri matn |
| Ovoz / video | qo'llab-quvvatlanmaydi — izoh qaytadi |

Chegaralar: Telegram botlari 20 MB gacha fayl ola oladi, va `MAX_FILE_CHARS`
dan uzun matn kesiladi.

Og'ir kutubxonalar (`pypdf`, `docx`, `openpyxl`) funksiya **ichida** import
qilinadi — shunda bot ishga tushishi tezroq bo'ladi.

### `formatting.py` — Markdown → Telegram HTML (93 qator)

Model Markdown yozadi, Telegram esa o'zining cheklangan HTML'ini tushunadi.
Bu fayl tarjima qiladi.

Eng muhim nozik joy — **`stash` mexanizmi**. Kod bloklari avval olib qo'yiladi
(`\x00 0 \x00` kabi belgi qoldiriladi), keyin qolgan matn HTML uchun escape
qilinadi, oxirida kod bloklari joyiga qaytariladi. Shundagina `` `<div>` ``
ichidagi belgilar buzilmaydi.

`split_message()` — Telegram bitta xabarda 4096 belgi qabul qiladi. Bu funksiya
uzun javobni avval xatboshi, keyin qator bo'yicha bo'ladi — so'z o'rtasidan
kesmaydi.

### `bot.py` — kirish nuqtasi (427 qator)

Telegram bilan bog'liq hamma narsa:

- **Handler'lar**: `/start`, `/help`, `/new`, `/chats`, `/reminders`, `/tasks`,
  `/memory`, `/id` va oddiy xabarlar
- **Tugmalar**: pastdagi klaviatura (`MAIN_KB`) va inline tugmalar
  (bekor qilish, bajarildi, o'chirish)
- **`_authorized()`**: `ALLOWED_USER_IDS` bo'yicha tekshiruv — bu bo'lmasa
  botni topgan har kim API pulingni sarflaydi
- **`_keep_typing()`**: model o'ylayotganda "yozmoqda..." holatini ushlab turadi
- **`_startup()`**: bazani ochadi, eslatmalarni tiklaydi, buyruqlar ro'yxatini
  Telegram'ga yuboradi

---

## 4. Bitta xabarning to'liq yo'li

Foydalanuvchi: *"ertaga soat 9 da shifokorga borishni eslat"*

```
1. bot.py:on_message()
   ├─ _authorized() — ruxsat bormi?
   ├─ files.collect_blocks() — matn/fayl → bloklar
   ├─ db.current_conversation() — qaysi suhbat?
   └─ _keep_typing() ishga tushadi

2. agent.py:respond()
   ├─ db.load_history() — oxirgi 40 xabar
   ├─ _user_message() — xabarga joriy vaqt qo'shiladi
   │     [joriy vaqt: 2026-09-19 14:30, Saturday]
   └─ _complete() → Gemini

3. Model javob beradi: "create_reminder chaqir"
   {"text": "shifokorga borish", "when": "2026-09-20T09:00"}

4. tools.py:_create_reminder()
   ├─ mahalliy vaqt → UTC
   ├─ db.add_reminder() — bazaga yoziladi
   └─ reminders.schedule() — JobQueue'ga qo'yiladi

5. Natija modelga qaytadi → model odam tilida javob yozadi

6. formatting.to_telegram_html() → split_message() → Telegram
```

Ertasiga soat 9 da `reminders._deliver()` mustaqil ishga tushadi va xabar
yuboradi — bu paytda model umuman ishlatilmaydi.

---

## 5. Yangi vosita qanday qo'shiladi

Masalan, ob-havo vositasi. Faqat **`tools.py`** ga tegiladi:

```python
# 1. Schema — modelga "bu vosita bor" deb aytish
{
    "name": "get_weather",
    "description": "Berilgan shahar uchun joriy ob-havoni qaytaradi.",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
}

# 2. Funksiya — haqiqiy ish
async def _get_weather(ctx: ToolContext, args: dict[str, Any]) -> str:
    city = str(args.get("city", "")).strip()
    if not city:
        return "Xato: shahar nomi bo'sh."
    ...
    return f"{city}: 24°C, ochiq havo"

# 3. Ro'yxatga qo'shish
HANDLERS = {..., "get_weather": _get_weather}
```

Boshqa hech qayerga tegish shart emas — `agent.py` vositalarni avtomatik oladi.

**Uchta qoida:**
- Har doim **matn** qaytar (model faqat matn o'qiydi)
- Xatoni `raise` qilma — `"Xato: ..."` deb qaytar, model tushunadi
- `description` ni aniq yoz — model shu asosida qaror qabul qiladi

---

## 6. Hozirgi cheklovlar

Halol bo'lish uchun — nima yaxshilanishi mumkin:

| Muammo | Qachon muhim bo'ladi | Yechim |
|---|---|---|
| Testlar yo'q | kod o'zgartira boshlaganda | `pytest` + `formatting.py` dan boshlash |
| `bot.py` 427 qator | yana handler qo'shilsa | `handlers/` paketiga bo'lish |
| Har xabarda `commit()` | ko'p foydalanuvchida | batch commit yoki PostgreSQL |
| `config.py` import paytida yiqiladi | test yozganda | `load_config()` funksiyasiga o'tkazish |
| Rate limiting yo'q | bot ochiq bo'lsa | foydalanuvchi bo'yicha so'rov chegarasi |
| Interfeys tili aralash | hozir ham | `bot.py` inglizcha, README o'zbekcha |
