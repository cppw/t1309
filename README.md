# Telegram Formatting & Inbox Bot — Ultimate Edition

A Telegram bot that reformats any text you send it (word-by-word, syntax-highlighted, mono-spaced, etc.) and doubles as a private inbox: every message sent to it is logged to a local database with admin tools for search, reply, ban, backup, and broadcast.

Everything is driven by reply-keyboard buttons and inline buttons — no slash commands needed after `/start`.

---

## 1. Requirements

- Python 3.9+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

Install dependencies:

```bash
pip install pyTelegramBotAPI flask cryptography
```

`cryptography` is technically optional — the bot still runs without it, but at-rest field encryption, encrypted export/import, and auto-backup are silently disabled (see [Section 7](#7-encryption--backups)).

---

## 2. Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `BOT_TOKEN` | **Yes** | — | Your bot token from BotFather. The process exits immediately if this is missing. |
| `ADMIN_CHAT_ID` | No | `""` | A chat ID that's always treated as admin, in addition to anyone added via the in-bot "Admins" panel. Useful for bootstrapping your first admin. |
| `WEBHOOK_URL` | No | `""` | If set, the bot registers a webhook at `{WEBHOOK_URL}/{BOT_TOKEN}` and the main thread idles. If unset, the bot uses long polling instead. |
| `PORT` | No | `10000` | Port for the Flask server (webhook endpoint + dashboard). |
| `MEDIA_BACKUP_ENABLED` | No | `1` | Set to `0` to disable downloading incoming media to local disk. |
| `MEDIA_DIR` | No | `media_backup` | Folder where backed-up media files are stored. |
| `MEDIA_MAX_BYTES` | No | `20971520` (20 MB) | Files larger than this are not downloaded locally (Telegram Bot API's own file-size cap for bots). |
| `DB_ENCRYPTION_KEY` | No | *(auto-generated)* | A base64 urlsafe Fernet key used to encrypt stored `text_content`/`caption` fields at rest. If you don't set one, the bot generates one on first run, saves it in the database, and sends it to every admin chat once — see [Section 7](#7-encryption--backups). |

Set these however your host expects — a `.env` file with a process manager, your platform's dashboard (Render, Railway, etc.), or exported in your shell for local runs.

---

## 3. Running the bot

### Local / polling mode (simplest)

```bash
export BOT_TOKEN="123456:ABC-your-token"
export ADMIN_CHAT_ID="your_telegram_user_id"
python y.py
```

Leave `WEBHOOK_URL` unset. The bot will:
1. Initialize `bot_data.db` (SQLite, created in the working directory) and run any needed migrations.
2. Start a Flask server in a background thread (health check + dashboard).
3. Start a daily auto-backup background thread (dormant until you configure a backup password).
4. Begin long-polling Telegram for updates, auto-restarting on crashes after a 5-second pause.

### Webhook mode (for hosting behind a public HTTPS URL)

```bash
export BOT_TOKEN="123456:ABC-your-token"
export WEBHOOK_URL="https://your-domain.example.com"
export PORT=10000
python y.py
```

The bot removes any existing webhook and registers `https://your-domain.example.com/{BOT_TOKEN}` as the new one. Telegram will POST updates to that path, handled by the Flask `/{BOT_TOKEN}` route.

### Finding your Telegram user ID

If you don't already know your numeric Telegram ID for `ADMIN_CHAT_ID`, message [@userinfobot](https://t.me/userinfobot) (or any similar bot) and it will reply with your ID.

---

## 4. First-time setup

1. Open a chat with your bot and send `/start`.
2. You'll get a welcome message with your current settings, and a reply keyboard with feature buttons appears at the bottom of the chat.
3. If your chat ID matches `ADMIN_CHAT_ID`, you'll also see admin buttons (📥 Inbox, 🛠 Admin, 📊 Stats, 📢 Broadcast, 🔍 Search Inbox, 💎 Premium Users).
4. To add more admins beyond the one set in `ADMIN_CHAT_ID`: **🛠 Admin → 👑 Admins → ➕ Add**, then send the numeric user ID.

From here, just send the bot any text and it will reformat and echo it back according to your current mode/style settings.

---

## 5. Text formatting features

### Modes (how text gets split/transformed)

| Mode | Button | What it does |
|---|---|---|
| Full Block | 🧱 Full Block | Whole text, smart-split into chunks under the chunk-size limit. |
| Word Mode | 🔠 Word Mode | Packs whole words into chunks up to the size limit, wrapping each chunk. |
| Mono Paragraph | 📝 Mono Para | One message per paragraph. |
| Mono Word | 📄 Mono Word | One message per individual word. |
| Mono Sentence | 📄 Mono Sentence | One message per sentence (splits on `. ! ? ।` and newlines). |
| Sentence Mode | 📃 Sentence Mode | Same engine as Mono Sentence. |
| Syntax Mode | 💻 Syntax Mode | Extracts ` ```lang ... ``` ` code blocks and renders them in `<pre language="...">` tags. Falls back to Full Block if no code block is found. |
| Reverse | ↩️ Reverse | Strips all HTML tags/backticks — use it to de-format previously formatted text. |
| Combo 1–4 | (Settings panel only) | Sends the text through two modes back-to-back, e.g. Word then Full, with shrink applied to one or both. |

### Wrapper styles (🎨 Styles)

Code, Bold, Italic, Mono Block, Pre, Spoiler, Plain, Quote, Expandable Quote — pick one from **⚙️ Settings**, applied on top of whichever mode is active.

### Quick prefixes

Skip the settings panel entirely by prefixing your message:

| Prefix | Mode |
|---|---|
| `!w text` | Word |
| `!f text` | Full |
| `!m text` | Mono paragraph |
| `!s text` | Syntax |
| `!r text` | Reverse |

### Other settings toggles

- **🧠 Smart Split** — ON splits by paragraph → sentence → hard cut; OFF does a naive hard character cut at the chunk boundary.
- **🗜️ Shrink** — wraps each chunk in an expandable `<blockquote>`.
- **🔤 Chunk Size** — 500 / 900 / 1500 / 3900 characters (3900 is Telegram's practical message-length ceiling).
- **🗑️ Auto-Delete** — automatically deletes the bot's own sent messages after 30s / 5m / 1h (OFF by default).

### Hashtags (#️⃣ Hashtags)

Detects hashtags in English, Bangla, and Hindi scripts, avoiding false positives from URLs, emails, and fenced code blocks. Three modes, cycled from Settings or set directly from the Hashtags menu:

- **Keep** — hashtags stay inline and bold (still clickable in Telegram even inside `<b>` tags).
- **Footer** — hashtags are stripped from the body and appended as a block at the end.
- **Strip** — hashtags are removed entirely.

"🔍 Analyze Last Text" shows a count and list of hashtags found in your most recent message.

### History & Favorites

- **📜 History** — your last 15 formatted inputs; tap one to re-run it through your current settings.
- **⭐ Favorites** — save the most recent input under a custom title (via "➕ Save current as Fav") and re-send it anytime.

### Inline mode

You can also invoke the bot from *any* chat by typing `@yourbotusername <text>` in the message box. It returns a picker with your text pre-formatted in Full / Word / Mono Para / Mono Word / Mono Sentence / Sentence modes, using your saved wrapper/chunk/shrink settings.

### Anti-spam

If a user sends 6+ messages within 10 seconds, the bot replies with a warning and skips processing that message.

### Large outputs

If formatting a message produces more than 10 chunks, the bot bundles them into a single `.html` file and sends that as a document instead, then sends just the first chunk as a normal message (the rest are held back to avoid flooding the chat).

---

## 6. Password authentication (optional, off by default)

A single shared password can gate the whole bot for non-admin users.

**To enable:**
1. **🛠 Admin → 🔑 Set Password** → send the password as a message (the bot deletes that message immediately after).
2. **🛠 Admin → 🔐 Auth: OFF/ON** → toggle it on.

**Behavior once enabled:**
- Non-admin users must send the correct password as a plain text message before the bot will do anything else.
- A correct password authenticates that user for **24 hours**.
- Admin chats always bypass this gate.
- Users can end their own session early with **🔒 Logout**.
- Admins can see everyone currently logged in and force-logout individuals via **🛠 Admin → 🔓 Logged-in**.

The password is hashed with PBKDF2-HMAC-SHA256 (100,000 iterations, random salt) and compared with a constant-time check — but there's no lockout or rate-limit on guesses, so use a password that can't be brute-forced by someone spamming messages.

---

## 7. Encryption & backups

Requires the `cryptography` package; if it's not installed, everything in this section is unavailable and the bot logs a warning but keeps running.

### At-rest field encryption

Message `text_content` and `caption` fields are encrypted before being written to SQLite, using a Fernet key that comes from `DB_ENCRYPTION_KEY` if you set it, or is auto-generated and stored in the database otherwise. If it had to auto-generate one, it also **sends that key once to every admin chat** so you have a copy outside the database — save it somewhere safe, since anyone who has the raw `.db` file but not this key cannot read historical messages. Set `DB_ENCRYPTION_KEY` explicitly before your first deploy if you don't want this generate-and-notify behavior.

### Manual encrypted export

**🛠 Admin → 📤 Export** → choose whether to embed backed-up media files (larger, fully self-contained) or export text/references only (smaller, but media only restores if `media_backup/` from the same server is still around) → send a password. You get back a `.enc` file: a random salt + a Fernet token, keyed off a PBKDF2-derived key from your password.

### Manual import

**🛠 Admin → 📥 Import** → send the `.enc` file → send the password used to create it. Messages are inserted into the current database (this is additive, not a wipe-and-replace).

### Automatic daily backups

**🛠 Admin → 💾 Auto Backup** → choose media inclusion → send a password. From then on, every 24 hours the bot builds an encrypted export and sends it as a document to every admin chat.

---

## 8. Admin / inbox tools

Reachable via the **🛠 Admin** button or the inline **🛠 Admin** button on other panels.

- **📥 Inbox** — paginated list of everyone who's messaged the bot, with unread-count badges. Supports **🔍 Search** (by name/username/ID) and **🎯 Filter** (by content type: text, photo, video, document, voice).
- **Per-user thread view** — tap a sender to see their messages, each with a read/unread indicator; tapping a message shows its content and, if media, either the locally backed-up file or its raw `file_id`.
- **💬 Reply** — reply as text or with media (photo/video/document/etc., caption optional); media replies are delivered to the user via `copy_message`, preserving layout exactly as you sent it.
- **🚫 Ban / ✅ Unban** — banned users get an automatic "🚫 Banned." response and nothing else is processed.
- **🏷️ Label** — free-text tag on a user (e.g., "VIP", "Spammer").
- **📝 Note** — internal admin-only notes per user (last 10 shown).
- **💎 Premium** — toggle a premium flag per user (currently just a stored flag; referenced via 💎 Premium Users but not gated to any feature in this build).
- **📊 Stats** — total messages, unique senders, breakdown by content type, top 5 senders, top 10 hashtags.
- **📢 Broadcast** — send a text message to every distinct user who has ever messaged the bot, with a small delay between sends and a sent/failed summary afterward.
- **🔔 Notify toggle** — when ON, admins get a "📩 New message" alert with a "👁 View" button for every incoming message; when OFF (default), messages are stored silently.
- **🧹 Clear Inbox** — deletes all stored messages and any locally backed-up media files. Settings, favorites, history, and admin config are untouched.
- **👑 Admins** — add or remove admins by numeric user ID.

### Web dashboard

With the Flask server running, visit:

- `http://<host>:<PORT>/` — simple "running" check.
- `http://<host>:<PORT>/health` — JSON health check (`{"ok": true, "time": ...}`), useful for uptime monitors.
- `http://<host>:<PORT>/dashboard` — read-only HTML page mirroring the 📊 Stats view (totals, by-type breakdown, top senders, top hashtags).

These routes have no authentication of their own — if you expose the port publicly, put it behind your own auth or IP restriction if the stats shouldn't be public.

---

## 9. Data storage notes

- Database: `bot_data.db` (SQLite, WAL mode) in the working directory. Created and migrated automatically on startup.
- Media backups: saved under `MEDIA_DIR` (default `media_backup/`) as `{message_id}_{content_type}{ext}`.
- Nothing is deployed to external storage — back up `bot_data.db` and `media_backup/` yourself (or rely on the auto-backup feature, which only covers the database contents, optionally with media embedded in the encrypted file rather than the raw folder).

---

## 10. Known limitations

- No rate-limiting on password attempts — pick a strong shared password if you enable auth.
- The anti-spam check (6 messages / 10 seconds) applies to formatting requests, not to every interaction (button presses, admin actions, etc. aren't throttled).
- 💎 Premium is a stored flag with no enforced behavior differences in this codebase — you'd need to add your own feature-gating if you want it to do something.
- The auto-generated encryption key is sent to admins as a **plaintext Telegram message** — fine for personal/small-team use, but don't rely on this if Telegram-level compromise is in your threat model. Set `DB_ENCRYPTION_KEY` yourself if that matters to you.
- Single shared password means no per-user accounts or granular permissions — it's an on/off gate, not a user management system.
