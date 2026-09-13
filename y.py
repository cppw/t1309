
"""
Telegram Formatting & Inbox Bot — ULTIMATE EDITION
All features + 100% Button UI (no slash needed)
- Custom wrappers, Smart Split, Syntax Highlight, Reverse, Inline Mode
- History, Favorites, Auto-Delete, Chunk Size, Prefix Modes, File Export
- Inbox Search/Filter, Reply, Ban/Mute, Notes, Labels, Unread, Broadcast
- Anti-Spam, Session Timeout, Backup Scheduler, Dashboard, Referral, Premium, Lang Detect
Run:
 pip install pyTelegramBotAPI flask cryptography
 python bot.py
"""

import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    BotCommand, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineQueryResultArticle, InputTextMessageContent
)
from flask import Flask, request, render_template_string
from threading import Thread, RLock, Timer
import os, html, re, sqlite3, datetime, json, hashlib, hmac, secrets, logging, traceback, time
from collections import defaultdict, deque

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    import base64
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    InvalidToken = Exception

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

# ========= CONFIG - NO HARDCODED TOKEN =========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN or ":" not in BOT_TOKEN:
    # Fallback for local dev only, warn heavily
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN env var required! Set BOT_TOKEN environment variable.")

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", 10000))

# ---------- Media backup & at-rest encryption config ----------
MEDIA_BACKUP_ENABLED = os.environ.get("MEDIA_BACKUP_ENABLED", "1") == "1"
MEDIA_DIR = os.environ.get("MEDIA_DIR", "media_backup")
MEDIA_MAX_BYTES = int(os.environ.get("MEDIA_MAX_BYTES", 20 * 1024 * 1024))  # Telegram bot API file size cap
DB_ENCRYPTION_KEY = os.environ.get("DB_ENCRYPTION_KEY", "")  # base64 urlsafe Fernet key; auto-generated & persisted if unset
if MEDIA_BACKUP_ENABLED:
    os.makedirs(MEDIA_DIR, exist_ok=True)

PAGE_SIZE = 6
HISTORY_LIMIT = 15
AUTO_DELETE_OPTIONS = [0, 30, 300, 3600] # seconds
CHUNK_OPTIONS = [500, 900, 1500, 3900]
# ---------- HASHTAG INTELLIGENT DETECTOR ----------
HASHTAG_REGEX = r'#(?:(?![0-9]+\b)[A-Za-z_\u0980-\u09FF\u0900-\u097F][A-Za-z0-9_\u0980-\u09FF\u0900-\u097F]{0,49}|[\u0980-\u09FF\u0900-\u097F][\w\u0980-\u09FF\u0900-\u097F]{0,49})'
HASHTAG_PATTERN = __import__('re').compile(HASHTAG_REGEX, __import__('re').UNICODE)
# More complex pattern for extraction (avoid URLs, emails)
URL_PATTERN = __import__('re').compile(r'https?://\S+|www\.\S+|\S+@\S+\.\S+')
HASHTAG_EXTRACT_PATTERN = __import__('re').compile(r'(?<![\w/@#])#([A-Za-z_\u0980-\u09FF\u0900-\u097F][A-Za-z0-9_\u0980-\u09FF\u0900-\u097F]{0,49}|[\u0980-\u09FF\u0900-\u097F][\w\u0980-\u09FF\u0900-\u097F]{0,49})', __import__('re').UNICODE)

def extract_hashtags_intelligent(text):
    """Complex intelligent hashtag detector"""
    if not text:
        return []
    # Remove URLs and emails to avoid false positives like https://example.com#anchor
    clean_text = URL_PATTERN.sub(' ', text)
    # Remove code blocks to avoid detecting # inside code
    clean_text = __import__('re').sub(r'```.*?```', ' ', clean_text, flags=__import__('re').DOTALL)
    hashtags = []
    seen = set()
    for m in HASHTAG_EXTRACT_PATTERN.finditer(clean_text):
        full = '#' + m.group(1)
        lower = full.lower()
        # Filter rules:
        # - Must have at least 2 chars (# + letter)
        # - Not purely numeric
        # - Length 2-50
        # - Not blacklisted
        if len(full) < 2 or len(full) > 51:
            continue
        if full.lower() in seen:
            continue
        # Skip if it's like #123 (pure numbers after #)
        if __import__('re').match(r'^#\d+$', full):
            continue
        # Skip common false positives
        if full.lower() in ['#tag', '#hashtag']:  # allow but you can customize
            pass
        hashtags.append(full)
        seen.add(lower)
    return hashtags

def analyze_hashtags(text):
    tags = extract_hashtags_intelligent(text)
    return {
        'tags': tags,
        'count': len(tags),
        'unique': len(set(t.lower() for t in tags)),
        'text': ' '.join(tags)
    }

def format_preserve_hashtags(text, wrapper_key, preserve_mode='keep'):
    """
    preserve_mode: 
    - keep: hashtags stay clickable inline outside code blocks
    - footer: extract hashtags to footer outside blocks
    - strip: remove hashtags
    """
    if preserve_mode == 'strip':
        return __import__('re').sub(HASHTAG_REGEX, '', text, flags=__import__('re').UNICODE)
    
    if preserve_mode == 'footer':
        tags = extract_hashtags_intelligent(text)
        text_without_tags = HASHTAG_PATTERN.sub('', text)
        text_without_tags = __import__('re').sub(r'\s{2,}', ' ', text_without_tags).strip()
        wrapped = WRAPPERS.get(wrapper_key, WRAPPERS["code"])[1].format(__import__('html').escape(text_without_tags)) if wrapper_key != 'plain' else text_without_tags
        if tags:
            footer = ' '.join(tags)
            return wrapped + "\n\n" + footer
        return wrapped

    # keep mode - intelligent inline preservation
    # Split text by hashtags, keep hashtags outside code
    parts = []
    last = 0
    for m in HASHTAG_PATTERN.finditer(text):
        s,e = m.span()
        if s > last:
            before = text[last:s]
            if before:
                parts.append(('text', before))
        parts.append(('hashtag', m.group()))
        last = e
    if last < len(text):
        parts.append(('text', text[last:]))

    if not parts:
        # no hashtags
        tpl = WRAPPERS.get(wrapper_key, WRAPPERS["code"])[1]
        import html as _html
        return tpl.format(_html.escape(text)) if wrapper_key != 'plain' else text

    import html as _html
    tpl = WRAPPERS.get(wrapper_key, WRAPPERS["code"])[1]
    out = []
    for typ, val in parts:
        if typ == 'text':
            if val.strip():
                if wrapper_key == 'plain':
                    out.append(_html.escape(val))
                else:
                    out.append(tpl.format(_html.escape(val)))
            else:
                out.append(_html.escape(val))
        else:
            # hashtag - keep clickable, outside code, bold for visibility
            # In Telegram HTML, hashtag inside <b> is still clickable!
            out.append(f"<b>{_html.escape(val)}</b>")
    return ''.join(out)

WRAPPERS = {
    "code": ("Code", "<code>{}</code>"),
    "bold": ("Bold", "<b>{}</b>"),
    "italic": ("Italic", "<i>{}</i>"),
    "mono": ("Mono Block", "<code>{}</code>"),
    "pre": ("Pre", "<pre>{}</pre>"),
    "spoiler": ("Spoiler", "<tg-spoiler>{}</tg-spoiler>"),
    "plain": ("Plain", "{}"),
    "quote": ("Quote", "<blockquote>{}</blockquote>"),
    "expand": ("Expandable Quote", "<blockquote expandable>{}</blockquote>"),
}
PREFIX_MAP = {
    "!w": "word", "!f": "full", "!m": "mono_para", "!s": "syntax", "!r": "reverse",
    "/w": "word", "/f": "full"
}
LANGS = {
    "en": "English",
    "bn": "বাংলা",
    "hi": "Hindi"
}

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None, threaded=True, num_threads=6)
app = Flask(__name__)

_STATE_LOCK = RLock()
ADMIN_PENDING = {}
USER_SETTINGS_CACHE = {}
RATE_LIMIT = defaultdict(lambda: deque(maxlen=10))  # user_id -> timestamps
BANNED_CACHE = set()

def _connect():
    conn = sqlite3.connect("bot_data.db", timeout=20, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    conn = _connect()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, first_name TEXT,
        content_type TEXT, text_content TEXT, file_id TEXT, caption TEXT, timestamp TEXT, is_read INTEGER DEFAULT 0,
        local_path TEXT, file_size INTEGER
    )""")
    # migration for DBs created before local_path/file_size existed
    try:
        existing_cols = {row[1] for row in c.execute("PRAGMA table_info(messages)").fetchall()}
        if "local_path" not in existing_cols:
            c.execute("ALTER TABLE messages ADD COLUMN local_path TEXT")
        if "file_size" not in existing_cols:
            c.execute("ALTER TABLE messages ADD COLUMN file_size INTEGER")
    except Exception as e:
        log.warning(f"messages table migration fail {e}")
    c.execute("""CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS auth_users (user_id INTEGER PRIMARY KEY, expires_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, username TEXT, added_by INTEGER, added_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_meta (
        user_id INTEGER PRIMARY KEY, premium INTEGER DEFAULT 0, referral_from INTEGER,
        banned INTEGER DEFAULT 0, label TEXT, lang TEXT DEFAULT 'en', join_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, note TEXT, created_at TEXT, by_admin INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, content TEXT, wrapper TEXT, created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, input_text TEXT, output_preview TEXT, created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_settings_v2 (
        user_id INTEGER PRIMARY KEY, mode TEXT DEFAULT 'full', wrapper TEXT DEFAULT 'code',
        chunk_size INTEGER DEFAULT 3900, auto_delete INTEGER DEFAULT 0, smart_split INTEGER DEFAULT 1,
        shrink INTEGER DEFAULT 0, preserve_entities INTEGER DEFAULT 0, preserve_hashtags INTEGER DEFAULT 1, hashtag_mode TEXT DEFAULT 'keep'
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS hashtags (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT, user_id INTEGER, count INTEGER DEFAULT 1, last_used TEXT
    )""")
    # migrations
    for idx in ["idx_msg_user", "idx_msg_ts", "idx_msg_read"]:
        try:
            if "user" in idx: c.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON messages(user_id)")
            elif "ts" in idx: c.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON messages(timestamp)")
            else: c.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON messages(is_read)")
        except: pass
    conn.commit(); conn.close()
    # load banned
    conn=_connect(); cur=conn.cursor()
    cur.execute("SELECT user_id FROM user_meta WHERE banned=1")
    for r in cur.fetchall(): BANNED_CACHE.add(r[0])
    conn.close()

def get_setting(k,d=None):
    conn=_connect()
    try:
        c=conn.cursor(); c.execute("SELECT value FROM bot_settings WHERE key=?",(k,)); row=c.fetchone(); return row[0] if row else d
    finally: conn.close()
def set_setting(k,v):
    conn=_connect()
    try: conn.execute("INSERT INTO bot_settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,v)); conn.commit()
    finally: conn.close()

def set_password(pw):
    salt=secrets.token_hex(16); h=hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 100000).hex()
    set_setting('password_salt', salt); set_setting('password_hash', h)
def verify_password(pw):
    salt=get_setting('password_salt'); sh=get_setting('password_hash')
    if not salt or not sh: return False
    ah=hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 100000).hex()
    return hmac.compare_digest(ah, sh)
def password_is_set(): return get_setting('password_hash') is not None
def auth_required(): return get_setting('auth_enabled','0')=='1' and password_is_set()
def is_user_authenticated(uid):
    conn=_connect()
    try:
        c=conn.cursor(); c.execute("SELECT expires_at FROM auth_users WHERE user_id=?",(uid,)); r=c.fetchone()
        if not r: return False
        if r[0]:
            try:
                exp=datetime.datetime.fromisoformat(r[0])
                if datetime.datetime.now() > exp:
                    c2=_connect(); c2.execute("DELETE FROM auth_users WHERE user_id=?",(uid,)); c2.commit(); c2.close()
                    return False
            except: pass
        return True
    finally: conn.close()
def mark_authenticated(uid, hours=24):
    exp=(datetime.datetime.now()+datetime.timedelta(hours=hours)).isoformat()
    conn=_connect(); conn.execute("INSERT OR REPLACE INTO auth_users (user_id, expires_at) VALUES (?,?)",(uid, exp)); conn.commit(); conn.close()
def revoke_user(uid):
    conn=_connect(); conn.execute("DELETE FROM auth_users WHERE user_id=?",(uid,)); conn.commit(); conn.close()

def get_authenticated_users_info():
    conn=_connect(); c=conn.cursor()
    c.execute("""SELECT a.user_id, (SELECT first_name FROM messages WHERE user_id=a.user_id ORDER BY id DESC LIMIT 1),
                 (SELECT username FROM messages WHERE user_id=a.user_id ORDER BY id DESC LIMIT 1) FROM auth_users a""")
    rows=c.fetchall(); conn.close(); return rows

def add_admin(uid, uname, added_by):
    conn=_connect(); conn.execute("INSERT INTO admins (user_id, username, added_by, added_at) VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username",
        (uid, uname, added_by, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))); conn.commit(); conn.close()
def remove_admin(uid):
    conn=_connect(); conn.execute("DELETE FROM admins WHERE user_id=?",(uid,)); conn.commit(); conn.close()
def get_admins():
    conn=_connect(); c=conn.cursor(); c.execute("SELECT user_id, username, added_at FROM admins ORDER BY added_at DESC"); r=c.fetchall(); conn.close(); return r
def is_admin_user_id(uid):
    conn=_connect(); c=conn.cursor(); c.execute("SELECT 1 FROM admins WHERE user_id=?",(uid,)); ok=c.fetchone() is not None; conn.close(); return ok
def get_all_admin_chat_ids():
    ids=[ADMIN_CHAT_ID] if ADMIN_CHAT_ID else []
    for uid,_,_ in get_admins(): ids.append(str(uid))
    seen=set(); out=[]
    for i in ids:
        if i and i not in seen: seen.add(i); out.append(i)
    return out
def is_admin_chat_id(cid):
    if ADMIN_CHAT_ID and str(cid)==str(ADMIN_CHAT_ID): return True
    try: return is_admin_user_id(int(cid))
    except: return False
def is_admin_chat(m): return is_admin_chat_id(m.chat.id)

# ----- user meta -----
def ensure_user_meta(uid):
    conn=_connect(); c=conn.cursor()
    c.execute("INSERT OR IGNORE INTO user_meta (user_id, join_at) VALUES (?,?)",(uid, datetime.datetime.now().isoformat()))
    conn.commit(); conn.close()
def is_banned(uid):
    if uid in BANNED_CACHE: return True
    conn=_connect(); c=conn.cursor(); c.execute("SELECT banned FROM user_meta WHERE user_id=?",(uid,)); r=c.fetchone(); conn.close()
    return r and r[0]==1
def set_banned(uid, banned):
    conn=_connect(); conn.execute("INSERT INTO user_meta (user_id,banned,join_at) VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET banned=excluded.banned",(uid,1 if banned else 0, datetime.datetime.now().isoformat())); conn.commit(); conn.close()
    if banned: BANNED_CACHE.add(uid)
    else: BANNED_CACHE.discard(uid)
def set_label(uid,label):
    conn=_connect(); conn.execute("INSERT INTO user_meta (user_id,label,join_at) VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET label=excluded.label",(uid,label,datetime.datetime.now().isoformat())); conn.commit(); conn.close()
def add_note(uid, note, by_admin):
    conn=_connect(); conn.execute("INSERT INTO notes (user_id,note,created_at,by_admin) VALUES (?,?,?,?)",(uid,note,datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),by_admin)); conn.commit(); conn.close()
def get_notes(uid):
    conn=_connect(); c=conn.cursor(); c.execute("SELECT note,created_at FROM notes WHERE user_id=? ORDER BY id DESC LIMIT 10",(uid,)); r=c.fetchall(); conn.close(); return r
def set_premium(uid, is_prem):
    conn=_connect(); conn.execute("INSERT INTO user_meta (user_id,premium,join_at) VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET premium=excluded.premium",(uid,1 if is_prem else 0, datetime.datetime.now().isoformat())); conn.commit(); conn.close()
def is_premium(uid):
    conn=_connect(); c=conn.cursor(); c.execute("SELECT premium FROM user_meta WHERE user_id=?",(uid,)); r=c.fetchone(); conn.close(); return bool(r and r[0]==1)
def set_referral(uid, from_id):
    conn=_connect(); c=conn.cursor(); c.execute("SELECT referral_from FROM user_meta WHERE user_id=?",(uid,)); ex=c.fetchone()
    if not ex or not ex[0]:
        conn.execute("INSERT INTO user_meta (user_id,referral_from,join_at) VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET referral_from=excluded.referral_from",(uid,from_id,datetime.datetime.now().isoformat())); conn.commit()
    conn.close()

def get_user_settings_v2(uid):
    conn=_connect(); c=conn.cursor()
    c.execute("SELECT mode,wrapper,chunk_size,auto_delete,smart_split,shrink,preserve_entities,preserve_hashtags,hashtag_mode FROM user_settings_v2 WHERE user_id=?",(uid,))
    r=c.fetchone(); conn.close()
    if not r:
        return {"mode":"full","wrapper":"code","chunk_size":3900,"auto_delete":0,"smart_split":1,"shrink":0,"preserve_entities":0,"preserve_hashtags":1,"hashtag_mode":"keep"}
    return {"mode":r[0],"wrapper":r[1],"chunk_size":r[2],"auto_delete":r[3],"smart_split":r[4],"shrink":r[5],"preserve_entities":r[6],"preserve_hashtags":r[7] if len(r)>7 else 1,"hashtag_mode":r[8] if len(r)>8 else 'keep'}
def set_user_settings_v2(uid, **kw):
    cur=get_user_settings_v2(uid); cur.update(kw)
    conn=_connect()
    conn.execute("""INSERT INTO user_settings_v2 (user_id,mode,wrapper,chunk_size,auto_delete,smart_split,shrink,preserve_entities,preserve_hashtags,hashtag_mode)
                    VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode, wrapper=excluded.wrapper,
                    chunk_size=excluded.chunk_size, auto_delete=excluded.auto_delete, smart_split=excluded.smart_split,
                    shrink=excluded.shrink, preserve_entities=excluded.preserve_entities, preserve_hashtags=excluded.preserve_hashtags, hashtag_mode=excluded.hashtag_mode""",
        (uid, cur["mode"], cur["wrapper"], cur["chunk_size"], cur["auto_delete"], cur["smart_split"], cur["shrink"], cur["preserve_entities"], cur.get("preserve_hashtags",1), cur.get("hashtag_mode","keep")))
    conn.commit(); conn.close()
    USER_SETTINGS_CACHE[uid]=cur
    return cur

def detect_lang(text):
    if re.search(r'[\u0980-\u09FF]', text): return 'bn'
    if re.search(r'[\u0900-\u097F]', text): return 'hi'
    return 'en'

# ---------- Encryption ----------
def derive_key(pw,salt):
    kdf=PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=200000)
    return base64.urlsafe_b64encode(kdf.derive(pw.encode()))

# ---------- At-rest field encryption for stored message text/caption ----------
_FIELD_FERNET = None
ENC_PREFIX = "enc::"  # marks a stored value as Fernet-encrypted so old plaintext rows still read fine

def _notify_admins_of_generated_key(key):
    """Best-effort: send the freshly auto-generated encryption key to admins over Telegram
    so it isn't the DB's only copy — if the DB file is ever lost/corrupted, whoever has this
    message can still recover it by setting DB_ENCRYPTION_KEY. Never raises."""
    try:
        msg = ("🔑 <b>Encryption key generated</b>\n"
               "No DB_ENCRYPTION_KEY was set, so one was auto-generated for encrypting stored "
               "message text. Save this somewhere safe — if the database is ever lost, this is "
               "the only way to decrypt existing messages:\n\n"
               f"<code>{html.escape(key)}</code>\n\n"
               "Set it as the DB_ENCRYPTION_KEY environment variable on next deploy to make it permanent.")
        for admin_id in get_all_admin_chat_ids():
            try: bot.send_message(admin_id, msg, parse_mode='HTML')
            except Exception as e: log.debug(f"key notify fail for {admin_id}: {e}")
    except Exception as e:
        log.debug(f"admin key notification skipped: {e}")

def _get_field_cipher():
    """Lazily build a Fernet cipher for encrypting text_content/caption at rest.
    Key comes from DB_ENCRYPTION_KEY env var if set, otherwise a key is generated
    once and persisted in bot_settings so restarts can still decrypt old rows.
    Locked because the bot runs handlers on multiple threads (num_threads=6) —
    without the lock, two concurrent first messages could each generate and
    save a different key, leaving whichever wrote first undecryptable."""
    global _FIELD_FERNET
    if not CRYPTO_AVAILABLE:
        return None
    if _FIELD_FERNET is not None:
        return _FIELD_FERNET
    with _STATE_LOCK:
        if _FIELD_FERNET is not None:
            return _FIELD_FERNET
        key = DB_ENCRYPTION_KEY.strip()
        if not key:
            key = get_setting("field_encryption_key")
            if not key:
                key = Fernet.generate_key().decode()
                set_setting("field_encryption_key", key)
                log.warning("No DB_ENCRYPTION_KEY set — generated a key and stored it in bot_settings. "
                            "Set DB_ENCRYPTION_KEY explicitly in production so it isn't lost if the DB is wiped.")
                _notify_admins_of_generated_key(key)
        try:
            _FIELD_FERNET = Fernet(key.encode() if isinstance(key, str) else key)
        except Exception as e:
            log.error(f"Invalid DB_ENCRYPTION_KEY, storage encryption disabled: {e}")
            _FIELD_FERNET = None
        return _FIELD_FERNET

def enc_field(value):
    """Encrypt a text field for storage. Returns the plain value unchanged if encryption unavailable."""
    if value is None:
        return None
    cipher = _get_field_cipher()
    if not cipher:
        return value
    try:
        return ENC_PREFIX + cipher.encrypt(value.encode()).decode()
    except Exception as e:
        log.warning(f"enc_field fail {e}")
        return value

def dec_field(value):
    """Decrypt a text field read from storage. Transparently passes through legacy plaintext rows."""
    if value is None:
        return None
    if not value.startswith(ENC_PREFIX):
        return value
    cipher = _get_field_cipher()
    if not cipher:
        return "[encrypted — key unavailable]"
    try:
        return cipher.decrypt(value[len(ENC_PREFIX):].encode()).decode()
    except Exception as e:
        # Could be genuine corruption/wrong key, or (rarely) legacy plaintext that
        # happened to start with the "enc::" sentinel. Fail safe: hand back the raw
        # value instead of an error string, so we never silently destroy real content.
        log.warning(f"dec_field fail, returning raw value: {e}")
        return value
def build_export_bytes(pw, include_media=False):
    conn=_connect(); c=conn.cursor(); c.execute("SELECT user_id,username,first_name,content_type,text_content,file_id,caption,timestamp,local_path FROM messages"); cols=["user_id","username","first_name","content_type","text_content","file_id","caption","timestamp","local_path"]
    rows=[dict(zip(cols,r)) for r in c.fetchall()]; conn.close()
    media_bytes_total=0
    for r in rows:
        r["text_content"]=dec_field(r["text_content"]); r["caption"]=dec_field(r["caption"])
        if include_media and r.get("local_path") and os.path.exists(r["local_path"]):
            try:
                with open(r["local_path"], "rb") as f:
                    raw=f.read()
                r["media_b64"]=base64.b64encode(raw).decode()
                media_bytes_total+=len(raw)
            except Exception as e:
                log.warning(f"export media embed fail for {r['local_path']}: {e}")
    payload=json.dumps({"exported_at":datetime.datetime.now().isoformat(),"media_embedded":include_media,"messages":rows}).encode()
    salt=os.urandom(16); key=derive_key(pw,salt); token=Fernet(key).encrypt(payload); return salt+token

# ---------- Messages ----------
def download_media_backup(bot_obj, mid, file_id, ctype):
    """Download a Telegram file to local disk so it survives file_id expiry. Returns (local_path, file_size) or (None, None)."""
    if not (MEDIA_BACKUP_ENABLED and bot_obj and file_id):
        return None, None
    try:
        finfo = bot_obj.get_file(file_id)
        if finfo.file_size and finfo.file_size > MEDIA_MAX_BYTES:
            log.info(f"media {file_id} too large ({finfo.file_size}b), skipping local backup")
            return None, finfo.file_size
        data = bot_obj.download_file(finfo.file_path)
        ext = os.path.splitext(finfo.file_path)[1] or ""
        fname = f"{mid}_{ctype}{ext}"
        fpath = os.path.join(MEDIA_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(data)
        return fpath, len(data)
    except Exception as e:
        log.warning(f"media backup download fail for {file_id}: {e}")
        return None, None

def save_message(message, is_read=0, bot_obj=None):
    user=message.from_user; username=f"@{user.username}" if user.username else None; first_name=user.first_name or "Unknown"; ctype=message.content_type
    text_content=None; file_id=None; caption=message.caption or None
    try:
        if ctype=='text': text_content=message.text
        elif ctype=='photo': file_id=message.photo[-1].file_id
        elif ctype=='video': file_id=message.video.file_id
        elif ctype=='document': file_id=message.document.file_id
        elif ctype=='audio': file_id=message.audio.file_id
        elif ctype=='voice': file_id=message.voice.file_id
        elif ctype=='video_note': file_id=message.video_note.file_id
        elif ctype=='animation': file_id=message.animation.file_id
        elif ctype=='sticker': file_id=message.sticker.file_id
        elif ctype=='contact': text_content=f"{message.contact.first_name} - {message.contact.phone_number}"
        elif ctype=='location': text_content=f"lat: {message.location.latitude}, lon: {message.location.longitude}"
        elif ctype=='venue': text_content=f"{message.venue.title} - {message.venue.address}"
        elif ctype=='dice': text_content=f"Dice value: {message.dice.value}"
        elif ctype=='poll': text_content=f"Poll: {message.poll.question}"
    except Exception as e: log.warning("save_message extract fail %s",e)
    conn=_connect(); c=conn.cursor()
    insert_sql="INSERT INTO messages (user_id,username,first_name,content_type,text_content,file_id,caption,timestamp,is_read) VALUES (?,?,?,?,?,?,?,?,?)"
    insert_params=(user.id, username, first_name, ctype, enc_field(text_content), file_id, enc_field(caption), datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), is_read)
    for attempt in range(3):
        try:
            c.execute(insert_sql, insert_params)
            break
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < 2:
                time.sleep(0.2 * (attempt + 1)); continue
            raise
    conn.commit(); mid=c.lastrowid; conn.close()
    if file_id:
        def _backup_and_update(mid=mid, file_id=file_id, ctype=ctype, bot_obj=bot_obj):
            local_path, file_size = download_media_backup(bot_obj, mid, file_id, ctype)
            if local_path or file_size:
                try:
                    conn2=_connect()
                    conn2.execute("UPDATE messages SET local_path=?, file_size=? WHERE id=?", (local_path, file_size, mid))
                    conn2.commit(); conn2.close()
                except Exception as e:
                    log.warning(f"media backup DB update fail for msg {mid}: {e}")
        Thread(target=_backup_and_update, daemon=True).start()
    return mid

def get_senders(offset=0, limit=PAGE_SIZE, search=None, filter_type=None):
    conn=_connect(); c=conn.cursor()
    q="SELECT user_id, first_name, username, COUNT(*) as cnt, MAX(timestamp) as last_ts, SUM(CASE WHEN is_read=0 THEN 1 ELSE 0 END) FROM messages"
    where=[]; params=[]
    if search: where.append("(first_name LIKE ? OR username LIKE ? OR CAST(user_id AS TEXT) LIKE ?)"); params.extend([f"%{search}%",f"%{search}%",f"%{search}%"])
    if filter_type: where.append("content_type=?"); params.append(filter_type)
    if where: q+=" WHERE "+" AND ".join(where)
    q+=" GROUP BY user_id ORDER BY last_ts DESC"
    c.execute(f"SELECT COUNT(*) FROM ({q})", params)
    total=c.fetchone()[0]
    c.execute(q+" LIMIT ? OFFSET ?", params+[limit, offset])
    rows=c.fetchall(); conn.close(); return rows, total

def get_user_messages(uid, offset=0, limit=PAGE_SIZE):
    conn=_connect(); c=conn.cursor()
    c.execute("SELECT COUNT(*) FROM messages WHERE user_id=?",(uid,)); total=c.fetchone()[0]
    c.execute("SELECT id,content_type,text_content,caption,timestamp,is_read FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?",(uid,limit,offset))
    rows=[(mid,ctype,dec_field(txt),dec_field(cap),ts,rd) for mid,ctype,txt,cap,ts,rd in c.fetchall()]
    conn.close(); return rows, total

def mark_read(uid):
    conn=_connect(); conn.execute("UPDATE messages SET is_read=1 WHERE user_id=?",(uid,)); conn.commit(); conn.close()

def get_stats():
    conn=_connect(); c=conn.cursor()
    c.execute("SELECT COUNT(*) FROM messages"); total=c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT user_id) FROM messages"); uniq=c.fetchone()[0]
    c.execute("SELECT content_type, COUNT(*) FROM messages GROUP BY content_type ORDER BY COUNT(*) DESC"); by_type=c.fetchall()
    c.execute("SELECT user_id, first_name, username, COUNT(*) FROM messages GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT 5"); top=c.fetchall()
    try:
        c.execute("SELECT tag, SUM(count) as total FROM hashtags GROUP BY lower(tag) ORDER BY total DESC LIMIT 10"); top_tags=c.fetchall()
    except: top_tags=[]
    conn.close(); return {"total":total,"unique":uniq,"by_type":by_type,"top_senders":top,"top_tags":top_tags}

# ---------- Formatting Core ----------
def apply_wrapper(text, wrapper_key):
    tpl=WRAPPERS.get(wrapper_key, WRAPPERS["code"])[1]
    # escape only if needed, keep html safe
    if wrapper_key in ("plain",): return tpl.format(text)
    # for code/pre we escape
    if wrapper_key in ("code","mono","pre"):
        return tpl.format(html.escape(text))
    else:
        return tpl.format(html.escape(text))

def smart_chunk(text, max_len, smart=True):
    """Smart split by paragraphs then sentences then chars"""
    if not smart:
        chunks=[]; i=0
        while i < len(text):
            chunks.append(text[i:i+max_len]); i+=max_len
        return chunks
    # first split by double newline
    paras = re.split(r'\n\s*\n', text)
    chunks=[]; cur=""
    for para in paras:
        if len(cur)+len(para)+2 <= max_len:
            cur += ("\n\n" if cur else "") + para
        else:
            if cur: chunks.append(cur); cur=""
            if len(para) <= max_len:
                cur=para
            else:
                # split para by sentences
                sentences=re.split(r'(?<=[.!?।])\s+', para)
                tmp=""
                for s in sentences:
                    if len(tmp)+len(s)+1 <= max_len:
                        tmp+=(" " if tmp else "")+s
                    else:
                        if tmp: chunks.append(tmp)
                        if len(s) > max_len:
                            # hard split
                            for i in range(0,len(s),max_len):
                                chunks.append(s[i:i+max_len])
                            tmp=""
                        else:
                            tmp=s
                if tmp: cur=tmp
    if cur: chunks.append(cur)
    return chunks

def split_by_sentence(text):
    """Intelligent sentence splitter for Bangla/English"""
    # Split by . ! ? । (Bangla danda) and newlines, keep delimiter
    import re
    # Protect abbreviations? simple approach
    # Use regex to split but keep sentences
    sentences = re.split(r'(?<=[.!?\u0964\u09F7])\s+|\n+', text.strip())
    # Filter empty
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return [text]
    return sentences

def get_chunks_mono_word(text, wrapper, chunk_size, shrink, smart_split, preserve_hashtags, hashtag_mode):
    """Monospace Word by Word - each word separate message"""
    import re, html
    words = re.findall(r'\S+', text)
    chunks = []
    for w in words:
        if preserve_hashtags and HASHTAG_PATTERN.match(w):
            # hashtag as separate clickable chunk
            chunks.append(f"<b>{html.escape(w)}</b>")
        else:
            if wrapper == 'plain':
                chunks.append(html.escape(w))
            else:
                c = format_preserve_hashtags(w, wrapper, hashtag_mode) if preserve_hashtags else apply_wrapper(w, wrapper)
                if shrink:
                    c = f"<blockquote expandable>{c}</blockquote>"
                chunks.append(c)
    return chunks

def get_chunks_mono_para(text, wrapper, chunk_size, shrink, smart_split, preserve_hashtags, hashtag_mode):
    """Monospace Paragraph by Paragraph"""
    import re
    paras = [p.strip() for p in re.split(r'\n\s*\n', text.strip()) if p.strip()]
    if not paras:
        paras = [text]
    chunks = []
    for para in paras:
        if preserve_hashtags:
            chunks.append(format_preserve_hashtags(para, wrapper, hashtag_mode))
        else:
            chunks.append(apply_wrapper(para, wrapper))
        if shrink:
            chunks[-1] = f"<blockquote expandable>{chunks[-1]}</blockquote>"
    return chunks

def get_chunks_mono_sentence(text, wrapper, chunk_size, shrink, smart_split, preserve_hashtags, hashtag_mode):
    """Monospace Sentence by Sentence"""
    sentences = split_by_sentence(text)
    chunks = []
    for sent in sentences:
        if preserve_hashtags:
            chunks.append(format_preserve_hashtags(sent, wrapper, hashtag_mode))
        else:
            chunks.append(apply_wrapper(sent, wrapper))
        if shrink:
            chunks[-1] = f"<blockquote expandable>{chunks[-1]}</blockquote>"
        # If sentence longer than chunk_size, smart split it
        if len(sent) > chunk_size:
            # re-split long sentence
            sub = smart_chunk(sent, chunk_size, smart=smart_split)
            chunks.pop()
            for s in sub:
                if preserve_hashtags:
                    chunks.append(format_preserve_hashtags(s, wrapper, hashtag_mode))
                else:
                    chunks.append(apply_wrapper(s, wrapper))
    return chunks

def get_chunks(text, mode, wrapper, chunk_size, shrink, smart_split, preserve_hashtags=True, hashtag_mode='keep'):
    # hashtag analysis for logging
    hashtag_info = analyze_hashtags(text) if preserve_hashtags else {'tags': []}

    # handle syntax mode
    if mode=="syntax":
        # detect code blocks ```lang\ncode```
        code_blocks=re.findall(r'```(\w+)?\n?(.*?)```', text, re.DOTALL)
        if code_blocks:
            all_chunks=[]
            for lang, code in code_blocks:
                lang=lang or ""
                # use <pre> wrapper
                safe=f"<pre language=\"{html.escape(lang)}\">{html.escape(code.strip())}</pre>"
                all_chunks.append(safe if not shrink else f"<blockquote expandable>{safe}</blockquote>")
            return all_chunks
        else:
            # fallback to full
            mode="full"
    if mode=="reverse":
        # strip tags
        clean=re.sub(r'<[^>]+>', '', text)
        clean=html.unescape(clean)
        clean=re.sub(r'```','',clean)
        return [apply_wrapper(clean, "plain")]

    # NEW MONO MODES
    if mode == "mono_word":
        return get_chunks_mono_word(text, wrapper, chunk_size, shrink, smart_split, preserve_hashtags, hashtag_mode)
    if mode == "mono_para":
        return get_chunks_mono_para(text, wrapper, chunk_size, shrink, smart_split, preserve_hashtags, hashtag_mode)
    if mode == "mono_sentence" or mode == "sentence":
        return get_chunks_mono_sentence(text, wrapper, chunk_size, shrink, smart_split, preserve_hashtags, hashtag_mode)
    if mode == "para":
        return get_chunks_mono_para(text, wrapper, chunk_size, shrink, smart_split, preserve_hashtags, hashtag_mode)


    if mode=="word":
        tokens=re.findall(r'\S+|\s+', text)
        chunks, cur, cur_len=[], "", 0
        for token in tokens:
            raw_len=len(token)
            if cur_len+raw_len>chunk_size:
                if cur: chunks.append(cur)
                cur=token; cur_len=raw_len
            else:
                cur+=token; cur_len+=raw_len
        if cur: chunks.append(cur)
        wrapped=[]
        for ch in chunks:
            if preserve_hashtags and HASHTAG_PATTERN.search(ch):
                # protect hashtags inside this chunk
                wrapped.append(format_preserve_hashtags(ch, wrapper, hashtag_mode))
            else:
                if wrapper=="code":
                    tokens2=re.findall(r'\S+|\s+', ch)
                    w_parts=[]
                    for t in tokens2:
                        if t.isspace():
                            w_parts.append(html.escape(t))
                        else:
                            if preserve_hashtags and HASHTAG_PATTERN.match(t):
                                w_parts.append(f"<b>{html.escape(t)}</b>")
                            else:
                                w_parts.append(f"<code>{html.escape(t)}</code>")
                    w=''.join(w_parts)
                    wrapped.append(w if not shrink else f"<blockquote expandable>{w}</blockquote>")
                else:
                    wrapped.append(apply_wrapper(ch, wrapper) if not shrink else f"<blockquote expandable>{apply_wrapper(ch, wrapper)}</blockquote>")
        return wrapped
    elif mode=="mono_para":
        paras=[p for p in re.split(r'\n\s*\n', text.strip()) if p.strip()]
        chunks=smart_chunk("\n\n".join(paras), chunk_size, smart=smart_split)
        out=[]
        for c in chunks:
            if preserve_hashtags:
                out.append(format_preserve_hashtags(c, wrapper, hashtag_mode) if not shrink else f"<blockquote expandable>{format_preserve_hashtags(c, wrapper, hashtag_mode)}</blockquote>")
            else:
                out.append((apply_wrapper(c, wrapper) if not shrink else f"<blockquote expandable>{apply_wrapper(c, wrapper)}</blockquote>"))
        return out
    else: # full
        chunks=smart_chunk(text, chunk_size, smart=smart_split)
        out=[]
        for c in chunks:
            if preserve_hashtags:
                out.append(format_preserve_hashtags(c, wrapper, hashtag_mode) if not shrink else f"<blockquote expandable>{format_preserve_hashtags(c, wrapper, hashtag_mode)}</blockquote>")
            else:
                out.append((apply_wrapper(c, wrapper) if not shrink else f"<blockquote expandable>{apply_wrapper(c, wrapper)}</blockquote>"))
        return out

def deformat_text(text):
    # remove all html tags and backticks
    t=re.sub(r'<[^>]+>', '', text)
    t=html.unescape(t)
    t=re.sub(r'`{1,3}','',t)
    return t.strip()

# ---------- Keyboards ----------
def main_reply_keyboard(is_admin=False, is_premium=False):
    mk=ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    mk.add(KeyboardButton("⚙️ Settings"), KeyboardButton("🎨 Styles"), KeyboardButton("📜 History"))
    mk.add(KeyboardButton("⭐ Favorites"), KeyboardButton("🔤 Chunk Size"), KeyboardButton("🗑️ Auto-Delete"))
    mk.add(KeyboardButton("🧠 Smart Split"), KeyboardButton("📝 Mono Para"), KeyboardButton("🔠 Word Mode"))
    mk.add(KeyboardButton("🧱 Full Block"), KeyboardButton("💻 Syntax Mode"), KeyboardButton("↩️ Reverse"))
    mk.add(KeyboardButton("📄 Mono Word"), KeyboardButton("📄 Mono Sentence"), KeyboardButton("📃 Sentence Mode"))
    mk.add(KeyboardButton("#️⃣ Hashtags"), KeyboardButton("❓ Help"), KeyboardButton("🔒 Logout"))
    if is_admin:
        mk.add(KeyboardButton("📥 Inbox"), KeyboardButton("🛠 Admin"), KeyboardButton("📊 Stats"))
        mk.add(KeyboardButton("📢 Broadcast"), KeyboardButton("🔍 Search Inbox"), KeyboardButton("💎 Premium Users"))
    return mk

def settings_inline(uid, is_admin=False):
    s=get_user_settings_v2(uid); m=s["mode"]; w=s["wrapper"]; cs=s["chunk_size"]; ad=s["auto_delete"]; smart=s["smart_split"]; shrink=s["shrink"]
    mk=InlineKeyboardMarkup()
    mk.row(InlineKeyboardButton(f"🔠 Word {'✅' if m=='word' else ''}", callback_data="setmode_word"),
           InlineKeyboardButton(f"🧱 Full {'✅' if m=='full' else ''}", callback_data="setmode_full"))
    mk.row(InlineKeyboardButton(f"📝 Mono {'✅' if m=='mono_para' else ''}", callback_data="setmode_mono_para"),
           InlineKeyboardButton(f"💻 Syntax {'✅' if m=='syntax' else ''}", callback_data="setmode_syntax"))
    mk.row(InlineKeyboardButton(f"↩️ Reverse {'✅' if m=='reverse' else ''}", callback_data="setmode_reverse"))
    mk.row(InlineKeyboardButton("➖ Styles ➖", callback_data="ignore"))
    # wrapper row 2 per row
    wrappers_list=list(WRAPPERS.keys())
    for i in range(0,len(wrappers_list),3):
        row=[]
        for wk in wrappers_list[i:i+3]:
            label=WRAPPERS[wk][0]+ (" ✅" if w==wk else "")
            row.append(InlineKeyboardButton(label, callback_data=f"setwrap_{wk}"))
        mk.row(*row)
    preserve_ht = s.get("preserve_hashtags",1)
    ht_mode = s.get("hashtag_mode","keep")
    mk.row(InlineKeyboardButton(f"🧠 Smart Split: {'ON' if smart else 'OFF'}", callback_data="toggle_smart"),
           InlineKeyboardButton(f"🗜️ Shrink: {'ON' if shrink else 'OFF'}", callback_data="toggle_shrink"))
    mk.row(InlineKeyboardButton(f"#️⃣ Hashtag Keep: {'ON' if preserve_ht else 'OFF'}", callback_data="toggle_hashtag"),
           InlineKeyboardButton(f"#️⃣ Mode: {ht_mode}", callback_data="cycle_hashtag_mode"))
    mk.row(InlineKeyboardButton(f"🔤 Chunk: {cs}", callback_data="chunk_menu"),
           InlineKeyboardButton(f"🗑️ Auto-Del: {ad}s" if ad else "🗑️ Auto-Del: OFF", callback_data="autodel_menu"))
    mk.row(InlineKeyboardButton("➖ MONO SPACE MODES ➖", callback_data="ignore"))
    mk.row(InlineKeyboardButton(f"📄 Mono Word {'✅' if m=='mono_word' else ''}", callback_data="setmode_mono_word"),
           InlineKeyboardButton(f"📄 Mono Para {'✅' if m=='mono_para' else ''}", callback_data="setmode_mono_para"))
    mk.row(InlineKeyboardButton(f"📄 Mono Sent {'✅' if m=='mono_sentence' else ''}", callback_data="setmode_mono_sentence"),
           InlineKeyboardButton(f"📃 Sentence {'✅' if m=='sentence' else ''}", callback_data="setmode_sentence"))
    mk.row(InlineKeyboardButton("Combo Modes ➖", callback_data="ignore"))
    mk.row(InlineKeyboardButton("1️⃣ Word|Full(Shr)", callback_data="setmode_combo_1"),
           InlineKeyboardButton("2️⃣ Full|Word(Shr)", callback_data="setmode_combo_2"))
    mk.row(InlineKeyboardButton("3️⃣ Word|Full", callback_data="setmode_combo_3"),
           InlineKeyboardButton("4️⃣ Word(Shr)|Full(Shr)", callback_data="setmode_combo_4"))
    mk.row(InlineKeyboardButton("📜 History", callback_data="show_history"),
           InlineKeyboardButton("⭐ Favorites", callback_data="show_fav"))
    if is_admin:
        mk.row(InlineKeyboardButton("📥 Inbox", callback_data="pg_users:0"),
               InlineKeyboardButton("🛠 Admin", callback_data="admin_panel"))
    mk.row(InlineKeyboardButton("❌ Close", callback_data="close_menu"))
    return mk

def chunk_menu_kb():
    mk=InlineKeyboardMarkup()
    for sz in CHUNK_OPTIONS:
        mk.row(InlineKeyboardButton(f"{sz} chars {'(TG max)' if sz==3900 else ''}", callback_data=f"setchunk_{sz}"))
    mk.row(InlineKeyboardButton("🔙 Back", callback_data="cmd_settings"))
    return mk

def autodel_menu_kb():
    mk=InlineKeyboardMarkup()
    for sec in AUTO_DELETE_OPTIONS:
        label="OFF" if sec==0 else f"{sec}s" if sec<60 else f"{sec//60}m"
        mk.row(InlineKeyboardButton(f"🗑️ {label}", callback_data=f"setautodel_{sec}"))
    mk.row(InlineKeyboardButton("🔙 Back", callback_data="cmd_settings"))
    return mk

def favorites_kb(uid):
    conn=_connect(); c=conn.cursor(); c.execute("SELECT id,title FROM favorites WHERE user_id=? ORDER BY id DESC LIMIT 10",(uid,)); rows=c.fetchall(); conn.close()
    mk=InlineKeyboardMarkup()
    for fid,title in rows:
        mk.row(InlineKeyboardButton(f"⭐ {title[:25]}", callback_data=f"fav_use:{fid}"),
               InlineKeyboardButton("❌", callback_data=f"fav_del:{fid}"))
    if not rows: mk.row(InlineKeyboardButton("No favorites yet", callback_data="ignore"))
    mk.row(InlineKeyboardButton("➕ Save current as Fav", callback_data="fav_save_prompt"),
           InlineKeyboardButton("🔙 Back", callback_data="cmd_settings"))
    return mk

def history_kb(uid):
    conn=_connect(); c=conn.cursor(); c.execute("SELECT id,input_text,created_at FROM history WHERE user_id=? ORDER BY id DESC LIMIT 12",(uid,)); rows=c.fetchall(); conn.close()
    mk=InlineKeyboardMarkup()
    for hid, txt, ts in rows:
        txt=dec_field(txt)
        preview=(txt[:20]+"…") if len(txt)>20 else txt
        preview=preview.replace("\n"," ")
        mk.row(InlineKeyboardButton(f"📜 {preview}", callback_data=f"hist_use:{hid}"))
    if not rows: mk.row(InlineKeyboardButton("No history", callback_data="ignore"))
    mk.row(InlineKeyboardButton("🧹 Clear History", callback_data="hist_clear"),
           InlineKeyboardButton("🔙 Back", callback_data="cmd_settings"))
    return mk

def inbox_kb(offset=0, search=None, filter_type=None):
    rows,total=get_senders(offset, PAGE_SIZE, search, filter_type)
    mk=InlineKeyboardMarkup()
    for uid, fn, un, cnt, last_ts, unread in rows:
        safe_fn=(fn or "Unknown")[:18]
        badge=f" 🔴{unread}" if unread and unread>0 else ""
        label=f"{safe_fn} ({un or 'no_user'}) — {cnt}{badge}"
        mk.row(InlineKeyboardButton(label[:60], callback_data=f"view_user:{uid}:0"))
    if not rows: mk.row(InlineKeyboardButton("📭 Empty", callback_data="ignore"))
    nav=[]
    if offset>0: nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"pg_users:{max(0,offset-PAGE_SIZE)}"))
    if offset+PAGE_SIZE<total: nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"pg_users:{offset+PAGE_SIZE}"))
    if nav: mk.row(*nav)
    mk.row(InlineKeyboardButton("🔍 Search", callback_data="inbox_search"),
           InlineKeyboardButton("🎯 Filter", callback_data="inbox_filter"))
    mk.row(InlineKeyboardButton("🛠 Admin", callback_data="admin_panel"),
           InlineKeyboardButton("❌ Close", callback_data="close_menu"))
    return mk,total

def user_msgs_kb(uid, offset=0):
    rows,total=get_user_messages(uid, offset, PAGE_SIZE)
    mk=InlineKeyboardMarkup()
    for mid, ctype, txt, cap, ts, is_read in rows:
        src=txt or cap or f"[{ctype}]"
        preview=(src[:28]+"…") if len(src)>28 else src
        preview=preview.replace("\n"," ")
        status="🔴" if not is_read else "⚪"
        mk.row(InlineKeyboardButton(f"{status} {ts[-8:]} — {preview}", callback_data=f"view_msg:{mid}"))
    if not rows: mk.row(InlineKeyboardButton("No messages", callback_data="ignore"))
    nav=[]
    if offset>0: nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"view_user:{uid}:{max(0,offset-PAGE_SIZE)}"))
    if offset+PAGE_SIZE<total: nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"view_user:{uid}:{offset+PAGE_SIZE}"))
    if nav: mk.row(*nav)
    # action buttons
    mk.row(InlineKeyboardButton("💬 Reply", callback_data=f"reply_user:{uid}"),
           InlineKeyboardButton("🚫 Ban", callback_data=f"ban_user:{uid}"),
           InlineKeyboardButton("✅ Unban", callback_data=f"unban_user:{uid}"))
    mk.row(InlineKeyboardButton("🏷️ Label", callback_data=f"label_user:{uid}"),
           InlineKeyboardButton("📝 Note", callback_data=f"note_user:{uid}"),
           InlineKeyboardButton("💎 Premium", callback_data=f"premium_user:{uid}"))
    mk.row(InlineKeyboardButton("🔙 Senders", callback_data="pg_users:0"),
           InlineKeyboardButton("❌ Close", callback_data="close_menu"))
    return mk,total

def admin_panel_kb():
    # This function now includes notify status in keyboard
    auth_on=get_setting('auth_enabled','0')=='1'; pass_set=password_is_set()
    mk=InlineKeyboardMarkup()
    mk.row(InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
           InlineKeyboardButton("📥 Inbox", callback_data="pg_users:0"))
    mk.row(InlineKeyboardButton("🔑 Set Password", callback_data="admin_setpass"))
    mk.row(InlineKeyboardButton(f"🔐 Auth: {'ON ✅' if auth_on else 'OFF ⛔'}", callback_data="admin_toggleauth"))
    mk.row(InlineKeyboardButton("🔓 Logged-in", callback_data="admin_loggedin"),
           InlineKeyboardButton("👑 Admins", callback_data="admin_manage_admins"))
    if CRYPTO_AVAILABLE:
        mk.row(InlineKeyboardButton("📤 Export", callback_data="admin_export"),
               InlineKeyboardButton("📥 Import", callback_data="admin_import"),
               InlineKeyboardButton("💾 Auto Backup", callback_data="admin_autobackup"))
    notify_on = get_setting('admin_notify_enabled', '0') == '1'
    mk.row(InlineKeyboardButton(f"🔔 Notify: {'ON ✅' if notify_on else 'OFF 🔕'}", callback_data="toggle_notify"),
           InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"))
    mk.row(InlineKeyboardButton("🔍 Search", callback_data="inbox_search"),
           InlineKeyboardButton("🧹 Clear Inbox", callback_data="clear_inbox"))
    mk.row(InlineKeyboardButton("⚙️ Settings", callback_data="cmd_settings"),
           InlineKeyboardButton("❌ Close", callback_data="close_menu"))
    return mk

def filter_kb():
    mk=InlineKeyboardMarkup()
    mk.row(InlineKeyboardButton("All", callback_data="filter_all"),
           InlineKeyboardButton("Text", callback_data="filter_text"),
           InlineKeyboardButton("Photo", callback_data="filter_photo"))
    mk.row(InlineKeyboardButton("Video", callback_data="filter_video"),
           InlineKeyboardButton("Document", callback_data="filter_document"),
           InlineKeyboardButton("Voice", callback_data="filter_voice"))
    mk.row(InlineKeyboardButton("🔙 Back", callback_data="pg_users:0"))
    return mk

def safe_edit(cid,mid,text,rm=None,parse='HTML'):
    try:
        bot.edit_message_text(text,cid,mid,parse_mode=parse,reply_markup=rm,disable_web_page_preview=True)
        return True
    except telebot.apihelper.ApiTelegramException as e:
        if "not modified" in str(e): return True
        try: bot.send_message(cid,text,parse_mode=parse,reply_markup=rm,disable_web_page_preview=True)
        except: pass
        return False
    except Exception:
        try: bot.send_message(cid,text,parse_mode=parse,reply_markup=rm,disable_web_page_preview=True)
        except: pass
        return False
def safe_answer(call, text=None, alert=False):
    try: bot.answer_callback_query(call.id, text=text, show_alert=alert)
    except: pass

# ---------- Flask ----------
@app.route('/')
def home(): return "Bot Ultimate Edition Running!"
@app.route('/health')
def health(): return {"ok":True,"time":datetime.datetime.now().isoformat()}
@app.route('/dashboard')
def dashboard():
    stats=get_stats()
    html_page="""
    <html><head><title>Bot Dashboard</title><style>body{font-family:sans-serif;padding:20px} .card{border:1px solid #ccc;padding:15px;margin:10px;border-radius:10px}</style></head>
    <body><h1>🤖 Bot Dashboard</h1>
    <div class="card"><h3>Stats</h3><p>Total: {{total}} | Unique Users: {{uniq}}</p></div>
    <div class="card"><h3>By Type</h3><ul>{% for t,c in by_type %}<li>{{t}}: {{c}}</li>{% endfor %}</ul></div>
    <div class="card"><h3>Top Senders</h3><ul>{% for uid,fn,un,cnt in top %}<li>{{fn}} ({{un}}) - {{cnt}}</li>{% endfor %}</ul></div>
    <div class="card"><h3>Top Hashtags</h3><ul>{% for tag,cnt in top_tags %}<li>{{tag}} - {{cnt}}</li>{% endfor %}</ul></div>
    </body></html>
    """
    return render_template_string(html_page, total=stats["total"], uniq=stats["unique"], by_type=stats["by_type"], top=stats["top_senders"], top_tags=stats.get("top_tags",[]))

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    if request.headers.get('content-type')=='application/json':
        json_string=request.get_data().decode('utf-8')
        update=telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    return 'ok'

def run_server():
    if WEBHOOK_URL:
        try: bot.remove_webhook(); bot.set_webhook(url=f"{WEBHOOK_URL}/{BOT_TOKEN}"); log.info(f"Webhook set to {WEBHOOK_URL}")
        except Exception as e: log.error(f"Webhook fail {e}")
    app.run(host="0.0.0.0", port=PORT, use_reloader=False, threaded=True)

# ---------- Auto Backup Thread ----------
def auto_backup_loop():
    while True:
        time.sleep(86400) # 24h
        if not CRYPTO_AVAILABLE: continue
        pw=get_setting('autobackup_pw')
        if not pw: continue
        try:
            include_media = get_setting('autobackup_include_media', '0') == '1'
            data=build_export_bytes(pw, include_media=include_media)
            fname=f"/tmp/auto_backup_{datetime.datetime.now().strftime('%Y%m%d')}.enc"
            with open(fname,"wb") as f: f.write(data)
            cap="♻️ Auto Backup 24h" + (" (media included)" if include_media else " (text/refs only)")
            for admin_id in get_all_admin_chat_ids():
                try:
                    with open(fname,"rb") as f:
                        bot.send_document(admin_id, f, caption=cap)
                except: pass
            os.remove(fname)
        except Exception as e: log.error(f"Auto backup err {e}")

# ---------- Handlers ----------
@bot.message_handler(commands=['start'])
def handle_start_cmd(message):
    uid=message.from_user.id
    ensure_user_meta(uid)
    # referral
    if message.text and len(message.text.split())>1:
        arg=message.text.split()[1]
        if arg.startswith("ref_"):
            try:
                ref_id=int(arg[4:])
                if ref_id!=uid: set_referral(uid, ref_id)
            except: pass
    if not is_admin_chat(message) and auth_required() and not is_user_authenticated(uid):
        bot.send_message(message.chat.id, "🔒 Password protected. Send password to continue.")
        return
    if is_banned(uid):
        bot.send_message(message.chat.id, "🚫 You are banned.")
        return
    lang=detect_lang(message.text or "")
    s=get_user_settings_v2(uid)
    welcome=f"👋 <b>Welcome!</b>\n\nI format text with superpowers.\n\n<b>Your settings:</b>\nMode: {s['mode']}\nStyle: {s['wrapper']}\nChunk: {s['chunk_size']}\n\nSend any text now!\n\nUse buttons below — no commands needed."
    bot.send_message(message.chat.id, welcome, parse_mode='HTML', reply_markup=main_reply_keyboard(is_admin_chat(message), is_premium(uid)))
    bot.send_message(message.chat.id, "⚙️ <b>Quick Settings</b>", parse_mode='HTML', reply_markup=settings_inline(uid, is_admin_chat(message)))

# Help via button and command
def send_help(chat_id):
    txt="""ℹ️ <b>Help — Button Guide</b>

<b>User Buttons:</b>
⚙️ Settings – Open formatting settings
🎨 Styles – Change wrapper style
📜 History – Last 15 formatted texts
⭐ Favorites – Save & reuse
🔤 Chunk Size – Set split size
🗑️ Auto-Delete – Auto delete after time
🧠 Smart Split – ON/OFF smart paragraph split
🔠 Word Mode – Word by word
🧱 Full Block – Full block
📝 Mono Para – Paragraph mode
💻 Syntax Mode – Highlight code blocks
↩️ Reverse – Remove formatting

<b>Quick Prefix:</b>
!w text = Word mode
!f text = Full mode
!m text = Mono
!s text = Syntax
!r text = Reverse

<b>Admin Buttons:</b>
📥 Inbox – View users
🛠 Admin – Admin panel
📊 Stats – Statistics
📢 Broadcast – Send to all
🔍 Search Inbox – Search users
"""
    bot.send_message(chat_id, txt, parse_mode='HTML')

@bot.message_handler(func=lambda m: m.text in ["❓ Help","Help"])
def handle_help_btn(message): send_help(message.chat.id)

# Settings buttons
@bot.message_handler(func=lambda m: m.text in ["⚙️ Settings","🎨 Styles","🔤 Chunk Size","🗑️ Auto-Delete","🧠 Smart Split","📝 Mono Para","🔠 Word Mode","🧱 Full Block","💻 Syntax Mode","↩️ Reverse"])
def handle_setting_buttons(message):
    uid=message.from_user.id
    if is_banned(uid): return
    if not is_admin_chat(message) and auth_required() and not is_user_authenticated(uid):
        bot.send_message(message.chat.id, "🔒 Send password first."); return
    txt=message.text
    mapping={
        "🔠 Word Mode":"word",
        "🧱 Full Block":"full",
        "📝 Mono Para":"mono_para",
        "📄 Mono Word":"mono_word",
        "📄 Mono Sentence":"mono_sentence",
        "📃 Sentence Mode":"sentence",
        "💻 Syntax Mode":"syntax",
        "↩️ Reverse":"reverse",
    }
    if txt in mapping:
        set_user_settings_v2(uid, mode=mapping[txt])
        bot.send_message(message.chat.id, f"✅ Mode set to {mapping[txt]}", reply_markup=main_reply_keyboard(is_admin_chat(message), is_premium(uid)))
        return
    if txt=="🧠 Smart Split":
        cur=get_user_settings_v2(uid); set_user_settings_v2(uid, smart_split=0 if cur["smart_split"] else 1)
        bot.send_message(message.chat.id, f"🧠 Smart Split {'ON' if not cur['smart_split'] else 'OFF'}", reply_markup=main_reply_keyboard(is_admin_chat(message)))
        return
    # open settings panel for others
    bot.send_message(message.chat.id, "⚙️ <b>Settings Panel</b>", parse_mode='HTML', reply_markup=settings_inline(uid, is_admin_chat(message)))

@bot.message_handler(func=lambda m: m.text in ["#️⃣ Hashtags"])
def handle_hashtag_btn(message):
    uid=message.from_user.id
    if is_banned(uid): return
    mk=InlineKeyboardMarkup()
    mk.row(InlineKeyboardButton("#️⃣ Keep Clickable (Inline)", callback_data="set_hashtag_mode_keep"),
           InlineKeyboardButton("#️⃣ Footer Mode", callback_data="set_hashtag_mode_footer"))
    mk.row(InlineKeyboardButton("#️⃣ Strip Hashtags", callback_data="set_hashtag_mode_strip"),
           InlineKeyboardButton("🔍 Analyze Last Text", callback_data="analyze_hashtags"))
    mk.row(InlineKeyboardButton("⚙️ Settings", callback_data="cmd_settings"))
    bot.send_message(message.chat.id, "#️⃣ <b>Hashtag Engine</b>\n\nIntelligent detector supports:\n• English #hello\n• Bangla #বাংলা\n• Avoids URLs\n• Keeps hashtags clickable outside monospace\n• Modes: Keep / Footer / Strip", parse_mode='HTML', reply_markup=mk)

@bot.message_handler(func=lambda m: m.text in ["📜 History"])
def handle_history_btn(message):
    uid=message.from_user.id
    bot.send_message(message.chat.id, "📜 <b>Your History</b>", parse_mode='HTML', reply_markup=history_kb(uid))

@bot.message_handler(func=lambda m: m.text in ["⭐ Favorites"])
def handle_fav_btn(message):
    uid=message.from_user.id
    bot.send_message(message.chat.id, "⭐ <b>Favorites</b>", parse_mode='HTML', reply_markup=favorites_kb(uid))

@bot.message_handler(func=lambda m: m.text in ["📥 Inbox"])
def handle_inbox_btn(message):
    if not is_admin_chat(message): return
    mk,total=inbox_kb(0); bot.send_message(message.chat.id, f"📥 <b>Inbox</b> — {total} senders", parse_mode='HTML', reply_markup=mk)

@bot.message_handler(func=lambda m: m.text in ["🛠 Admin"])
def handle_admin_btn(message):
    if not is_admin_chat(message): return
    bot.send_message(message.chat.id, "🛠 <b>Admin Panel</b>", parse_mode='HTML', reply_markup=admin_panel_kb())

@bot.message_handler(func=lambda m: m.text in ["📊 Stats"])
def handle_stats_btn(message):
    if not is_admin_chat(message): return
    stats=get_stats(); lines=[f"📊 <b>Stats</b>\nTotal: {stats['total']}\nUsers: {stats['unique']}"]
    bot.send_message(message.chat.id, "\n".join(lines), parse_mode='HTML')

@bot.message_handler(func=lambda m: m.text in ["📢 Broadcast"])
def handle_broadcast_btn(message):
    if not is_admin_chat(message): return
    ADMIN_PENDING[message.chat.id]={"action":"broadcast"}
    bot.send_message(message.chat.id, "📢 Send broadcast message (text) or type Cancel")

@bot.message_handler(func=lambda m: m.text in ["🔍 Search Inbox"])
def handle_search_btn(message):
    if not is_admin_chat(message): return
    ADMIN_PENDING[message.chat.id]={"action":"inbox_search"}
    bot.send_message(message.chat.id, "🔍 Send keyword to search (username/name/id) or Cancel")

@bot.message_handler(func=lambda m: m.text in ["🔒 Logout"])
def handle_logout_btn(message):
    if is_admin_chat(message): bot.send_message(message.chat.id, "Admin can't logout via button. Use /cancel"); return
    revoke_user(message.from_user.id); bot.send_message(message.chat.id, "🔒 Logged out.", reply_markup=ReplyKeyboardRemove())

# Admin pending must be before catch-all
@bot.message_handler(func=lambda m: is_admin_chat(m) and m.chat.id in ADMIN_PENDING,
                      content_types=['text','document','photo','video','audio','voice','video_note','animation','sticker'])
def handle_admin_pending(message):
    chat_id=message.chat.id
    with _STATE_LOCK: pending=ADMIN_PENDING.get(chat_id)
    if not pending: return
    action=pending["action"]
    if message.content_type=='text' and message.text.lower() in ('cancel','/cancel','❌ close'):
        with _STATE_LOCK: ADMIN_PENDING.pop(chat_id,None)
        bot.send_message(chat_id, "✅ Cancelled.", reply_markup=main_reply_keyboard(True))
        return
    # Only reply_user (in "media" mode) and import_file (expects a .enc document) accept
    # non-text content. Every other pending action still requires plain text input.
    if message.content_type!='text' and not (action=="reply_user" and pending.get("reply_mode")=="media") and action!="import_file":
        bot.reply_to(message, "⚠️ Please send text for this."); return
    if action=="set_password":
        if message.content_type!='text': bot.reply_to(message,"Send password as text"); return
        if len(message.text.strip())<3: bot.reply_to(message,"Too short"); return
        set_password(message.text.strip())
        with _STATE_LOCK: ADMIN_PENDING.pop(chat_id,None)
        try: bot.delete_message(chat_id,message.message_id)
        except: pass
        bot.send_message(chat_id,"✅ Password updated")
    elif action=="export_password":
        pw=message.text.strip()
        include_media=pending.get("include_media", False)
        try: bot.delete_message(chat_id,message.message_id)
        except: pass
        with _STATE_LOCK: ADMIN_PENDING.pop(chat_id,None)
        try:
            data=build_export_bytes(pw, include_media=include_media)
            fname=f"backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.enc"
            path=f"/tmp/{fname}"
            with open(path,"wb") as f: f.write(data)
            cap="🔒 Encrypted backup" + (" (media included)" if include_media else " (text/refs only)")
            with open(path,"rb") as f: bot.send_document(chat_id,f,visible_file_name=fname,caption=cap)
            os.remove(path)
        except Exception as e: bot.send_message(chat_id,f"Export failed: {e}")
    elif action=="add_admin":
        if not message.text.strip().lstrip('-').isdigit(): bot.reply_to(message,"Send numeric ID"); return
        add_admin(int(message.text.strip()), None, chat_id)
        with _STATE_LOCK: ADMIN_PENDING.pop(chat_id,None)
        bot.send_message(chat_id,"✅ Admin added")
    elif action=="inbox_search":
        kw=message.text.strip()
        with _STATE_LOCK: ADMIN_PENDING.pop(chat_id,None)
        mk,total=inbox_kb(0, search=kw)
        bot.send_message(chat_id,f"🔍 Results for '{html.escape(kw)}' — {total}", parse_mode='HTML', reply_markup=mk)
    elif action=="broadcast":
        text=message.text
        with _STATE_LOCK: ADMIN_PENDING.pop(chat_id,None)
        conn=_connect(); c=conn.cursor(); c.execute("SELECT DISTINCT user_id FROM messages"); users=[r[0] for r in c.fetchall()]; conn.close()
        bot.send_message(chat_id,f"📢 Broadcasting to {len(users)} users...")
        sent=0; failed=0
        for uid in users:
            try: bot.send_message(uid, f"📢 <b>Broadcast</b>\n\n{text}", parse_mode='HTML'); sent+=1; time.sleep(0.05)
            except: failed+=1
        bot.send_message(chat_id,f"✅ Broadcast done: {sent} sent, {failed} failed")
    elif action=="reply_user":
        target=pending.get("target_id")
        reply_mode=pending.get("reply_mode","text")
        with _STATE_LOCK: ADMIN_PENDING.pop(chat_id,None)
        try:
            if reply_mode=="media" and message.content_type!='text':
                # copy_message preserves the message exactly as the admin sent it
                # (same media type, same caption position relative to the media),
                # so the recipient sees the identical layout the admin composed.
                original_caption=message.caption or ""
                new_caption=(f"💬 Admin Reply:\n\n{original_caption}" if original_caption else "💬 Admin Reply")[:1024]
                bot.copy_message(target, chat_id, message.message_id, caption=new_caption)
            else:
                bot.send_message(target, f"💬 <b>Admin Reply:</b>\n\n{message.text}", parse_mode='HTML')
            bot.send_message(chat_id,f"✅ Replied to {target}")
        except Exception as e: bot.send_message(chat_id,f"Failed: {e}")
    elif action=="note_user":
        target=pending.get("target_id")
        add_note(target, message.text, chat_id)
        with _STATE_LOCK: ADMIN_PENDING.pop(chat_id,None)
        bot.send_message(chat_id,f"📝 Note added for {target}")
    elif action=="label_user":
        target=pending.get("target_id")
        set_label(target, message.text.strip())
        with _STATE_LOCK: ADMIN_PENDING.pop(chat_id,None)
        bot.send_message(chat_id,f"🏷️ Label set for {target}: {message.text}")
    elif action=="fav_save":
        uid=pending.get("user_id"); content=pending.get("content")
        title=message.text.strip()[:30]
        conn=_connect(); conn.execute("INSERT INTO favorites (user_id,title,content,wrapper,created_at) VALUES (?,?,?,?,?)",
            (uid, title, enc_field(content), pending.get("wrapper","code"), datetime.datetime.now().isoformat())); conn.commit(); conn.close()
        with _STATE_LOCK: ADMIN_PENDING.pop(chat_id,None)
        bot.send_message(chat_id,f"⭐ Saved as '{title}'")
    elif action=="import_file":
        if message.content_type!='document': bot.reply_to(message,"Send .enc file"); return
        # need password second step? we already have file, ask pw
        ADMIN_PENDING[chat_id]={"action":"import_pw","file_id":message.document.file_id}
        bot.send_message(chat_id,"Now send decryption password")
    elif action=="import_pw":
        pw=message.text.strip()
        fid=pending.get("file_id")
        try:
            file_info=bot.get_file(fid); downloaded=bot.download_file(file_info.file_path)
            from cryptography.fernet import Fernet
            # restore logic
            if len(downloaded)<17: raise ValueError("Invalid file")
            salt, token = downloaded[:16], downloaded[16:]
            key=derive_key(pw,salt)
            payload=Fernet(key).decrypt(token)
            obj=json.loads(payload.decode())
            msgs=obj.get("messages",[])
            conn=_connect()
            restored_media=0
            if MEDIA_BACKUP_ENABLED: os.makedirs(MEDIA_DIR, exist_ok=True)
            for m in msgs:
                local_path=m.get("local_path")
                b64=m.get("media_b64")
                if b64:
                    # don't trust the exporting server's local_path — write to a fresh file here
                    try:
                        raw=base64.b64decode(b64)
                        ext=os.path.splitext(local_path or "")[1] or ""
                        fname=f"restored_{secrets.token_hex(6)}_{m.get('content_type','file')}{ext}"
                        local_path=os.path.join(MEDIA_DIR, fname)
                        with open(local_path,"wb") as mf: mf.write(raw)
                        restored_media+=1
                    except Exception as e:
                        log.warning(f"import media restore fail: {e}")
                        local_path=None
                elif local_path and not os.path.exists(local_path):
                    # old reference from the exporting server that doesn't exist on this machine
                    local_path=None
                conn.execute("INSERT INTO messages (user_id,username,first_name,content_type,text_content,file_id,caption,timestamp,local_path) VALUES (?,?,?,?,?,?,?,?,?)",
                    (m.get("user_id"),m.get("username"),m.get("first_name"),m.get("content_type"),enc_field(m.get("text_content")),m.get("file_id"),enc_field(m.get("caption")),m.get("timestamp"),local_path))
            conn.commit(); conn.close()
            summary=f"✅ Imported {len(msgs)} messages"
            if obj.get("media_embedded"): summary+=f" ({restored_media} media files restored to disk)"
            bot.send_message(chat_id,summary)
        except Exception as e:
            bot.send_message(chat_id,f"Import failed: {e}")
        with _STATE_LOCK: ADMIN_PENDING.pop(chat_id,None)
    elif action=="autobackup_pw":
        set_setting('autobackup_pw', message.text.strip())
        with _STATE_LOCK: ADMIN_PENDING.pop(chat_id,None)
        try: bot.delete_message(chat_id,message.message_id)
        except: pass
        bot.send_message(chat_id,"✅ Auto-backup password set. Backups every 24h")

# ---------- Callback Query ----------
@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    data=call.data; cid=call.message.chat.id; mid=call.message.message_id; uid=call.from_user.id
    # settings
    if data.startswith("setmode_"):
        mode=data.replace("setmode_","")
        set_user_settings_v2(uid, mode=mode)
        safe_answer(call, f"Mode: {mode}")
        safe_edit(cid,mid,f"⚙️ Settings — mode set to {mode}", settings_inline(uid, is_admin_chat_id(cid)))
    elif data.startswith("setwrap_"):
        wrap=data.replace("setwrap_","")
        set_user_settings_v2(uid, wrapper=wrap)
        safe_answer(call, f"Style: {wrap}")
        safe_edit(cid,mid,f"🎨 Style set to {wrap}", settings_inline(uid, is_admin_chat_id(cid)))
    elif data=="toggle_smart":
        cur=get_user_settings_v2(uid); set_user_settings_v2(uid, smart_split=0 if cur["smart_split"] else 1)
        safe_edit(cid,mid,"⚙️ Settings", settings_inline(uid, is_admin_chat_id(cid))); safe_answer(call)
    elif data=="toggle_shrink":
        cur=get_user_settings_v2(uid); set_user_settings_v2(uid, shrink=0 if cur["shrink"] else 1)
        safe_edit(cid,mid,"⚙️ Settings", settings_inline(uid, is_admin_chat_id(cid))); safe_answer(call)
    elif data=="chunk_menu":
        safe_edit(cid,mid,"🔤 Choose chunk size:", chunk_menu_kb())
    elif data.startswith("setchunk_"):
        sz=int(data.split("_")[1]); set_user_settings_v2(uid, chunk_size=sz)
        safe_edit(cid,mid,f"✅ Chunk set to {sz}", settings_inline(uid, is_admin_chat_id(cid))); safe_answer(call)
    elif data=="autodel_menu":
        safe_edit(cid,mid,"🗑️ Auto-delete timer:", autodel_menu_kb())
    elif data.startswith("setautodel_"):
        sec=int(data.split("_")[1]); set_user_settings_v2(uid, auto_delete=sec)
        safe_edit(cid,mid,f"✅ Auto-delete {sec}s", settings_inline(uid, is_admin_chat_id(cid))); safe_answer(call)
    elif data=="cmd_settings":
        safe_edit(cid,mid,"⚙️ <b>Formatting Settings</b>", settings_inline(uid, is_admin_chat_id(cid)), parse='HTML')
    elif data=="toggle_hashtag":
        cur=get_user_settings_v2(uid); set_user_settings_v2(uid, preserve_hashtags=0 if cur.get("preserve_hashtags",1) else 1)
        safe_edit(cid,mid,"⚙️ Settings", settings_inline(uid, is_admin_chat_id(cid))); safe_answer(call)
    elif data=="cycle_hashtag_mode":
        cur=get_user_settings_v2(uid); modes=['keep','footer','strip']; idx=modes.index(cur.get('hashtag_mode','keep')); nxt=modes[(idx+1)%len(modes)]; set_user_settings_v2(uid, hashtag_mode=nxt)
        safe_edit(cid,mid,f"#️⃣ Hashtag mode -> {nxt}", settings_inline(uid, is_admin_chat_id(cid))); safe_answer(call)
    elif data=="close_menu":
        try: bot.delete_message(cid,mid)
        except: pass
        safe_answer(call)
    elif data=="show_history":
        safe_edit(cid,mid,"📜 History", history_kb(uid))
    elif data=="show_fav":
        safe_edit(cid,mid,"⭐ Favorites", favorites_kb(uid))
    elif data.startswith("hist_use:"):
        hid=int(data.split(":")[1])
        conn=_connect(); c=conn.cursor(); c.execute("SELECT input_text FROM history WHERE id=? AND user_id=?",(hid,uid)); r=c.fetchone(); conn.close()
        if r:
            # simulate formatting
            s=get_user_settings_v2(uid)
            # reuse handle_all_content logic via sending? For now just send formatting
            text=dec_field(r[0]); bot.send_message(cid, f"↩️ Re-formatting from history:\n<code>{html.escape(text[:100])}</code>", parse_mode='HTML')
            # process
            process_text_message(bot, cid, uid, text, s, call)
        safe_answer(call)
    elif data=="hist_clear":
        conn=_connect(); conn.execute("DELETE FROM history WHERE user_id=?",(uid,)); conn.commit(); conn.close()
        safe_edit(cid,mid,"🧹 History cleared", history_kb(uid)); safe_answer(call)
    elif data.startswith("fav_use:"):
        fid=int(data.split(":")[1])
        conn=_connect(); c=conn.cursor(); c.execute("SELECT content,wrapper FROM favorites WHERE id=? AND user_id=?",(fid,uid)); r=c.fetchone(); conn.close()
        if r:
            content,wrapper=r
            content=dec_field(content)
            bot.send_message(cid, apply_wrapper(content[:3900], wrapper), parse_mode='HTML')
        safe_answer(call)
    elif data.startswith("fav_del:"):
        fid=int(data.split(":")[1]); conn=_connect(); conn.execute("DELETE FROM favorites WHERE id=? AND user_id=?",(fid,uid)); conn.commit(); conn.close()
        safe_edit(cid,mid,"⭐ Favorites", favorites_kb(uid)); safe_answer(call)
    elif data=="fav_save_prompt":
        # need last text from history
        conn=_connect(); c=conn.cursor(); c.execute("SELECT input_text FROM history WHERE user_id=? ORDER BY id DESC LIMIT 1",(uid,)); r=c.fetchone(); conn.close()
        if not r: safe_answer(call,"No recent text"); return
        ADMIN_PENDING[cid]={"action":"fav_save","user_id":uid,"content":dec_field(r[0]),"wrapper":get_user_settings_v2(uid)["wrapper"]}
        bot.send_message(cid,"⭐ Send title for this favorite (max 30 chars) or Cancel")
        safe_answer(call)
    elif data.startswith("pg_users:"):
        off=int(data.split(":")[1]); mk,_=inbox_kb(off); safe_edit(cid,mid,f"📥 Inbox — page {off//PAGE_SIZE+1}", mk)
    elif data.startswith("view_user:"):
        parts=data.split(":"); uid_target=int(parts[1]); off=int(parts[2]) if len(parts)>2 else 0
        mark_read(uid_target)
        mk,_=user_msgs_kb(uid_target, off)
        # show notes/label
        notes=get_notes(uid_target); label_text=""
        conn=_connect(); c=conn.cursor(); c.execute("SELECT label,premium,banned FROM user_meta WHERE user_id=?",(uid_target,)); meta=c.fetchone(); conn.close()
        if meta: label_text=f"\n🏷️ Label: {meta[0] or 'none'} | Premium: {'Yes' if meta[1] else 'No'} | Banned: {'Yes' if meta[2] else 'No'}"
        safe_edit(cid,mid,f"👤 User {uid_target}{label_text}\nNotes: {len(notes)}", mk)
    elif data.startswith("view_msg:"):
        mid_msg=int(data.split(":")[1])
        conn=_connect(); c=conn.cursor(); c.execute("SELECT user_id,content_type,text_content,caption,file_id,timestamp,local_path FROM messages WHERE id=?",(mid_msg,)); r=c.fetchone(); conn.close()
        if not r: safe_answer(call,"Not found"); return
        uid_t, ctype, txt, cap, fid, ts, local_path = r
        txt=dec_field(txt); cap=dec_field(cap)
        preview=txt or cap or f"[{ctype}]"
        mk=InlineKeyboardMarkup()
        mk.row(InlineKeyboardButton("💬 Reply", callback_data=f"reply_user:{uid_t}"), InlineKeyboardButton("🔙 Back", callback_data=f"view_user:{uid_t}:0"))
        safe_edit(cid,mid,f"📩 Msg {mid_msg} from {uid_t} at {ts}\nType: {ctype}\n\n{html.escape((preview[:800] or '') )}", mk)
        # send backed-up media file if we have it locally; else fall back to forwarding by file_id
        if local_path and os.path.exists(local_path):
            try:
                with open(local_path, "rb") as f:
                    bot.send_document(cid, f, caption=f"📎 Local backup ({ctype})")
            except Exception as e:
                log.warning(f"local media send fail {e}")
        elif fid:
            try: bot.send_message(cid, f"📎 Media file_id: <code>{fid}</code>", parse_mode='HTML')
            except: pass
    elif data.startswith("reply_user:"):
        target=int(data.split(":")[1])
        mk=InlineKeyboardMarkup()
        mk.row(InlineKeyboardButton("📝 Text Only", callback_data=f"reply_mode:text:{target}"),
               InlineKeyboardButton("📎 With Media", callback_data=f"reply_mode:media:{target}"))
        bot.send_message(cid, f"💬 How do you want to reply to {target}?", reply_markup=mk)
        safe_answer(call)
    elif data.startswith("reply_mode:"):
        _, mode, target_s = data.split(":")
        target=int(target_s)
        ADMIN_PENDING[cid]={"action":"reply_user","target_id":target,"reply_mode":mode}
        if mode=="media":
            bot.send_message(cid, f"📎 Send the photo/video/document/etc. (caption optional) to reply to {target}, or Cancel.\nIt will be delivered to them exactly as you send it, with the caption in the same position.")
        else:
            bot.send_message(cid, f"💬 Send text reply to {target}, or Cancel")
        safe_answer(call)
    elif data.startswith("ban_user:"):
        target=int(data.split(":")[1]); set_banned(target, True); safe_answer(call,f"Banned {target}"); safe_edit(cid,mid,f"🚫 Banned {target}", user_msgs_kb(target)[0])
    elif data.startswith("unban_user:"):
        target=int(data.split(":")[1]); set_banned(target, False); safe_answer(call,f"Unbanned {target}")
    elif data.startswith("note_user:"):
        target=int(data.split(":")[1]); ADMIN_PENDING[cid]={"action":"note_user","target_id":target}
        bot.send_message(cid,f"📝 Send note for {target}"); safe_answer(call)
    elif data.startswith("label_user:"):
        target=int(data.split(":")[1]); ADMIN_PENDING[cid]={"action":"label_user","target_id":target}
        bot.send_message(cid,f"🏷️ Send label for {target} (e.g., VIP, Spammer)"); safe_answer(call)
    elif data.startswith("premium_user:"):
        target=int(data.split(":")[1]); set_premium(target, not is_premium(target)); safe_answer(call,"Toggled premium")
    elif data=="admin_panel":
        safe_edit(cid,mid,"🛠 <b>Admin Panel</b>", admin_panel_kb(), parse='HTML')
    elif data=="admin_stats":
        st=get_stats(); txt=f"📊 <b>Stats</b>\nTotal: {st['total']}\nUnique: {st['unique']}\n"
        for t,c in st['by_type']: txt+=f"{t}: {c}\n"
        mk=InlineKeyboardMarkup(); mk.row(InlineKeyboardButton("🔄 Refresh", callback_data="admin_stats"), InlineKeyboardButton("🛠 Admin", callback_data="admin_panel"))
        safe_edit(cid,mid,txt,mk, parse='HTML')
    elif data=="admin_setpass":
        ADMIN_PENDING[cid]={"action":"set_password"}; bot.send_message(cid,"🔑 Send new password (will be deleted after)"); safe_answer(call)
    elif data=="admin_toggleauth":
        cur=get_setting('auth_enabled','0'); set_setting('auth_enabled','0' if cur=='1' else '1')
        safe_edit(cid,mid,f"🔐 Auth now {'ON' if get_setting('auth_enabled')=='1' else 'OFF'}", admin_panel_kb()); safe_answer(call)
    elif data=="admin_loggedin":
        rows=get_authenticated_users_info()
        mk=InlineKeyboardMarkup()
        for uid_t,fn,un in rows: mk.row(InlineKeyboardButton(f"Logout {fn or uid_t}", callback_data=f"revoke_user:{uid_t}"))
        if not rows: mk.row(InlineKeyboardButton("None", callback_data="ignore"))
        mk.row(InlineKeyboardButton("🛠 Admin", callback_data="admin_panel"))
        safe_edit(cid,mid,f"🔓 Logged in: {len(rows)}", mk)
    elif data.startswith("revoke_user:"):
        target=int(data.split(":")[1]); revoke_user(target); safe_answer(call,f"Logged out {target}")
    elif data=="admin_manage_admins":
        admins=get_admins(); mk=InlineKeyboardMarkup()
        for uid_t,uname,_ in admins: mk.row(InlineKeyboardButton(f"Remove {uname or uid_t}", callback_data=f"remove_admin:{uid_t}"))
        mk.row(InlineKeyboardButton("➕ Add", callback_data="admin_addadmin"), InlineKeyboardButton("🛠 Panel", callback_data="admin_panel"))
        safe_edit(cid,mid,"👑 Admins", mk)
    elif data=="admin_addadmin":
        ADMIN_PENDING[cid]={"action":"add_admin"}; bot.send_message(cid,"Send numeric user ID to add as admin"); safe_answer(call)
    elif data.startswith("remove_admin:"):
        remove_admin(int(data.split(":")[1])); safe_answer(call,"Removed")
    elif data=="admin_export":
        mk=InlineKeyboardMarkup()
        mk.row(InlineKeyboardButton("📎 Include media files", callback_data="export_media:1"),
               InlineKeyboardButton("📄 Text/refs only", callback_data="export_media:0"))
        bot.send_message(cid,"Include backed-up media files in this export?\n\n"
                              "• Include: fully self-contained, works on any machine, but the file can get large "
                              "(each photo/video adds up to its full size).\n"
                              "• Text/refs only: small file, but media only restores if media_backup/ from this "
                              "server is still present.", reply_markup=mk)
    elif data.startswith("export_media:"):
        include_media = data.split(":")[1]=="1"
        ADMIN_PENDING[cid]={"action":"export_password","include_media":include_media}
        bot.send_message(cid,"Send password for encrypted export")
    elif data=="admin_import":
        ADMIN_PENDING[cid]={"action":"import_file"}; bot.send_message(cid,"Send .enc backup file")
    elif data=="admin_autobackup":
        mk=InlineKeyboardMarkup()
        mk.row(InlineKeyboardButton("📎 Include media files", callback_data="autobackup_media:1"),
               InlineKeyboardButton("📄 Text/refs only", callback_data="autobackup_media:0"))
        bot.send_message(cid,"Include backed-up media files in the daily auto backup?\n\n"
                              "• Include: each day's backup is self-contained, but file size grows with however "
                              "much media came in that day.\n"
                              "• Text/refs only: small, fast daily backups; media only restores if media_backup/ "
                              "on this server is still present.", reply_markup=mk)
    elif data.startswith("autobackup_media:"):
        include_media = data.split(":")[1]=="1"
        set_setting('autobackup_include_media', '1' if include_media else '0')
        ADMIN_PENDING[cid]={"action":"autobackup_pw"}; bot.send_message(cid,"Send password for auto daily backup (saved securely)")
    elif data=="toggle_notify":
        cur = get_setting('admin_notify_enabled', '0')
        set_setting('admin_notify_enabled', '0' if cur == '1' else '1')
        status = "ON ✅ - will send alerts" if get_setting('admin_notify_enabled') == '1' else "OFF 🔕 - silent storage only"
        safe_answer(call, f"Notifications {status}")
        safe_edit(cid, mid, f"🔔 Admin Notify: {status}", admin_panel_kb())
    elif data=="clear_inbox":
        conn=_connect(); c=conn.cursor()
        c.execute("SELECT local_path FROM messages WHERE local_path IS NOT NULL")
        paths=[r[0] for r in c.fetchall()]
        conn.execute("DELETE FROM messages"); conn.commit(); conn.close()
        removed=0
        for p in paths:
            try:
                os.remove(p); removed+=1
            except Exception as e: log.debug(f"media cleanup skip {p}: {e}")
        safe_answer(call, "Inbox cleared")
        safe_edit(cid, mid, f"🧹 Inbox cleared - all messages deleted ({removed} backed-up media files removed) but settings kept", admin_panel_kb())
    elif data=="admin_broadcast":
        ADMIN_PENDING[cid]={"action":"broadcast"}; bot.send_message(cid,"📢 Send broadcast text")
    elif data=="inbox_search":
        ADMIN_PENDING[cid]={"action":"inbox_search"}; bot.send_message(cid,"🔍 Send keyword")
    elif data=="inbox_filter":
        safe_edit(cid,mid,"🎯 Filter by type:", filter_kb())
    elif data.startswith("set_hashtag_mode_"):
        mode=data.replace("set_hashtag_mode_",""); set_user_settings_v2(uid, hashtag_mode=mode, preserve_hashtags=1 if mode!='strip' else 0)
        safe_edit(cid,mid,f"#️⃣ Hashtag mode set to {mode}", settings_inline(uid, is_admin_chat_id(cid))); safe_answer(call)
    elif data=="analyze_hashtags":
        conn=_connect(); c=conn.cursor(); c.execute("SELECT input_text FROM history WHERE user_id=? ORDER BY id DESC LIMIT 1",(uid,)); r=c.fetchone(); conn.close()
        if not r:
            safe_answer(call, "No history"); return
        last_text=dec_field(r[0])
        info=analyze_hashtags(last_text)
        txt=f"#️⃣ <b>Hashtag Analysis</b>\n\nText: <code>{__import__('html').escape(last_text[:100])}</code>\n\nFound: {info['count']}\nTags: {info['text'] or 'none'}"
        bot.send_message(cid, txt, parse_mode='HTML'); safe_answer(call)
    elif data.startswith("filter_"):
        ftype=data.replace("filter_","")
        if ftype=="all": mk,_=inbox_kb(0); safe_edit(cid,mid,"📥 Inbox — all", mk)
        else: mk,_=inbox_kb(0, filter_type=ftype); safe_edit(cid,mid,f"📥 Filter: {ftype}", mk)
    elif data=="ignore":
        safe_answer(call)

# ---------- Inline Mode ----------
@bot.inline_handler(func=lambda q: True)
def inline_query(query):
    text=query.query.strip()
    if not text: return
    uid=query.from_user.id
    s=get_user_settings_v2(uid)
    results=[]
    for mode in ["full","word","mono_para","mono_word","mono_sentence","sentence"]:
        chunks=get_chunks(text, mode, s["wrapper"], s["chunk_size"], s["shrink"], s["smart_split"])
        first=chunks[0] if chunks else ""
        # strip html for preview
        preview=html.escape(text[:50])
        results.append(InlineQueryResultArticle(
            id=f"{mode}_{hash(text)}", title=f"{mode.title()} Mode",
            description=preview,
            input_message_content=InputTextMessageContent(message_text=first, parse_mode='HTML')
        ))
    try: bot.answer_inline_query(query.id, results, cache_time=1)
    except Exception as e: log.warning(f"inline fail {e}")

# ---------- Core Processing ----------
def process_text_message(bot_obj, chat_id, user_id, text, settings, call_or_msg=None):
    # anti-spam
    now=time.time()
    RATE_LIMIT[user_id].append(now)
    q=RATE_LIMIT[user_id]
    if len(q)>=6:
        # if 6 msgs within 10 sec
        if now - q[-6] < 10:
            bot_obj.send_message(chat_id, "⚠️ Slow down! Anti-spam protection (6 msgs / 10s)")
            return
    # prefix mode
    mode_override=None
    for pref, m in PREFIX_MAP.items():
        if text.lower().startswith(pref+" "):
            mode_override=m; text=text[len(pref)+1:].strip(); break
    mode=settings["mode"] if not mode_override else mode_override
    wrapper=settings["wrapper"]; chunk_size=settings["chunk_size"]; auto_del=settings["auto_delete"]
    smart=settings["smart_split"]; shrink=settings["shrink"]

    # handle combo
    combos={
        "combo_1": [("word",False),("full",True)],
        "combo_2": [("full",False),("word",True)],
        "combo_3": [("word",False),("full",False)],
        "combo_4": [("word",True),("full",True)],
    }
    preserve_ht = settings.get("preserve_hashtags",1)
    ht_mode = settings.get("hashtag_mode","keep")
    all_chunks=[]
    if mode in combos:
        for m, shr in combos[mode]:
            all_chunks.extend(get_chunks(text, m, wrapper, chunk_size, shr, smart, preserve_ht, ht_mode))
    else:
        all_chunks=get_chunks(text, mode, wrapper, chunk_size, shrink, smart, preserve_ht, ht_mode)

    # save history
    try:
        conn=_connect(); conn.execute("INSERT INTO history (user_id,input_text,output_preview,created_at) VALUES (?,?,?,?)",
            (user_id, enc_field(text), enc_field(all_chunks[0][:200] if all_chunks else ""), datetime.datetime.now().isoformat()))
        # keep only HISTORY_LIMIT
        conn.execute("DELETE FROM history WHERE id NOT IN (SELECT id FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?)", (user_id, HISTORY_LIMIT))
        conn.commit(); conn.close()
    except Exception as e: log.warning(f"history save fail {e}")

    # file export if too many chunks
    if len(all_chunks)>10:
        fname=f"/tmp/format_{user_id}_{int(time.time())}.html"
        with open(fname,"w",encoding="utf-8") as f:
            f.write("<html><body>"+"\n<hr>\n".join(all_chunks)+"</body></html>")
        try:
            with open(fname,"rb") as f:
                bot_obj.send_document(chat_id, f, caption=f"📄 Too many chunks ({len(all_chunks)}), sent as file. First chunk below:")
            os.remove(fname)
            # send first chunk only
            sent=bot_obj.send_message(chat_id, all_chunks[0], parse_mode='HTML', disable_web_page_preview=True)
        except Exception as e:
            log.warning(f"file export fail {e}")
            sent=None
        # then rest? send rest as messages but limited
        chunks_to_send=all_chunks[1:6] # limit
    else:
        chunks_to_send=all_chunks
        sent=None

    sent_ids=[]
    for chunk in chunks_to_send:
        try:
            m=bot_obj.send_message(chat_id, chunk, parse_mode='HTML', disable_web_page_preview=True)
            sent_ids.append(m.message_id)
        except Exception as e:
            # fallback plain
            try:
                m=bot_obj.send_message(chat_id, html.escape(chunk[:3900]), disable_web_page_preview=True)
                sent_ids.append(m.message_id)
            except Exception as e2:
                log.error(f"send fail {e2}")

    # auto delete
    if auto_del>0 and sent_ids:
        def delete_later(c_id, m_ids, delay):
            time.sleep(delay)
            for mid in m_ids:
                try: bot_obj.delete_message(c_id, mid)
                except: pass
        Thread(target=delete_later, args=(chat_id, sent_ids, auto_del), daemon=True).start()

# ---------- Catch-all ----------
@bot.message_handler(content_types=['text','photo','video','document','audio','voice','video_note','animation','sticker','contact','location','venue','dice','poll'])
def handle_all(message):
    try:
        uid=message.from_user.id; ensure_user_meta(uid)
        if is_banned(uid):
            bot.send_message(message.chat.id, "🚫 Banned."); return
        if not is_admin_chat(message) and auth_required() and not is_user_authenticated(uid):
            # try password
            if message.content_type=='text' and verify_password(message.text.strip()):
                mark_authenticated(uid, hours=24)
                bot.send_message(message.chat.id, "✅ Authenticated for 24h!", reply_markup=main_reply_keyboard(False))
                return
            else:
                if message.content_type=='text':
                    # if password set, prompt
                    bot.send_message(message.chat.id, "🔒 Send correct password to continue.")
                return
        # admin pending handled earlier by separate handler, but if text is button ignore?
        # button texts handled earlier? We have handlers for exact button texts with func, they have higher priority? In telebot, order matters. We registered earlier handlers first.
        # To avoid double processing, check if text is known button - if so, let those handlers handle (they are already matched before this handler? Actually telebot checks in order, so this catch-all last)
        # So if message is a button, return to avoid formatting
        if message.content_type=='text' and message.text in ["⚙️ Settings","🎨 Styles","📜 History","⭐ Favorites","🔤 Chunk Size","🗑️ Auto-Delete","🧠 Smart Split","📝 Mono Para","🔠 Word Mode","🧱 Full Block","💻 Syntax Mode","↩️ Reverse","📄 Mono Word","📄 Mono Sentence","📃 Sentence Mode","#️⃣ Hashtags","❓ Help","🔒 Logout","📥 Inbox","🛠 Admin","📊 Stats","📢 Broadcast","🔍 Search Inbox","💎 Premium Users","Help"]:
            return

        # save hashtags to DB
        try:
            tags=extract_hashtags_intelligent(message.text or message.caption or "")
            if tags:
                conn=_connect(); c=conn.cursor()
                for tag in tags:
                    c.execute("SELECT id,count FROM hashtags WHERE lower(tag)=lower(?) AND user_id=?",(tag, message.from_user.id))
                    row=c.fetchone()
                    if row:
                        c.execute("UPDATE hashtags SET count=count+1, last_used=? WHERE id=?",(datetime.datetime.now().isoformat(), row[0]))
                    else:
                        c.execute("INSERT INTO hashtags (tag,user_id,last_used) VALUES (?,?,?)",(tag, message.from_user.id, datetime.datetime.now().isoformat()))
                conn.commit(); conn.close()
        except Exception as e:
            log.debug(f"hashtag save fail {e}")
        save_message(message, is_read=0, bot_obj=bot)
        settings=get_user_settings_v2(uid)
        username=f"@{message.from_user.username}" if message.from_user.username else "N/A"
        first_name=html.escape(message.from_user.first_name or "Unknown")
        # Silent storage only - no admin spam (toggleable)
        # Check if admin notifications are enabled
        notify_enabled = get_setting('admin_notify_enabled', '0') == '1'
        if notify_enabled:
            alert_markup=InlineKeyboardMarkup(); alert_markup.add(InlineKeyboardButton("👁 View", callback_data=f"view_user:{uid}:0"))
            alert_text=f"📩 New <b>{html.escape(message.content_type)}</b>\n👤 {first_name} ({html.escape(username)})\n🆔 <code>{uid}</code>"
            for admin_id in get_all_admin_chat_ids():
                try: bot.send_message(admin_id, alert_text, parse_mode='HTML', reply_markup=alert_markup)
                except Exception as e: log.debug(f"admin alert fail {e}")
        else:
            log.debug(f"Message from {uid} stored silently (no admin alert)")

        text_to_copy=message.text or message.caption
        if text_to_copy and text_to_copy.strip():
            process_text_message(bot, message.chat.id, uid, text_to_copy, settings, message)
            if message.content_type != 'text':
                # it's media with a caption — the caption above was formatted and sent as text,
                # but the media itself still needs to go back, or it just gets silently dropped.
                # Strip the caption on the copy so we don't also duplicate the raw, unformatted text.
                try: bot.copy_message(message.chat.id, message.chat.id, message.message_id, caption="")
                except Exception as e: log.warning(f"media copy (with caption) fail {e}")
        else:
            try: bot.copy_message(message.chat.id, message.chat.id, message.message_id)
            except Exception as e: log.warning(f"copy fail {e}")
        try: bot.delete_message(message.chat.id, message.message_id)
        except: pass
    except Exception as e:
        log.error(f"handle_all error {e}\n{traceback.format_exc()}")
        try: bot.send_message(message.chat.id, "⚠️ Something went wrong.")
        except: pass

# ---------- Main ----------
if __name__=="__main__":
    init_db()
    log.info("DB ready")
    Thread(target=run_server, daemon=True).start()
    log.info("Flask started")
    Thread(target=auto_backup_loop, daemon=True).start()
    try:
        bot.set_my_commands([
            BotCommand("start","🏠 Start / Welcome - Button UI"),
        ])
    except: pass
    log.info("Bot up. Polling...")
    if WEBHOOK_URL:
        # keep alive
        while True: time.sleep(3600)
    else:
        while True:
            try:
                bot.infinity_polling(timeout=20, long_polling_timeout=10, skip_pending=False, logger_level=logging.WARNING)
            except Exception as e:
                log.error(f"Polling crash {e} — restart 5s"); time.sleep(5)
