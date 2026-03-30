import os
import sqlite3

# Use /data for persistent storage on Zeabur; fallback to app dir locally
_DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
os.makedirs(_DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(_DATA_DIR, "users.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    # Migration: if old table exists without google_sub column, drop and recreate
    try:
        cursor = conn.execute("PRAGMA table_info(users)")
        columns = [row['name'] for row in cursor.fetchall()]
        if columns and 'google_sub' not in columns:
            conn.execute("DROP TABLE users")
            conn.commit()
    except Exception:
        pass
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        google_sub TEXT UNIQUE NOT NULL,
        email TEXT NOT NULL,
        name TEXT DEFAULT '',
        avatar TEXT DEFAULT '',
        openai_key TEXT DEFAULT '',
        minimax_key TEXT DEFAULT '',
        minimax_group TEXT DEFAULT '',
        youtube_cookies TEXT DEFAULT '',
        apify_token TEXT DEFAULT ''
    )''')
    # Migration: add new columns if missing
    try:
        cursor = conn.execute("PRAGMA table_info(users)")
        columns = [row['name'] for row in cursor.fetchall()]
        if 'youtube_cookies' not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN youtube_cookies TEXT DEFAULT ''")
        if 'apify_token' not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN apify_token TEXT DEFAULT ''")
        if 'replicate_token' not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN replicate_token TEXT DEFAULT ''")
    except Exception:
        pass
    conn.commit()
    conn.close()


def find_or_create_google_user(google_sub, email, name, avatar):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE google_sub = ?", (google_sub,)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE users SET email = ?, name = ?, avatar = ? WHERE google_sub = ?",
            (email, name, avatar, google_sub),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE google_sub = ?", (google_sub,)
        ).fetchone()
        result = dict(row)
        conn.close()
        return result
    else:
        conn.execute(
            "INSERT INTO users (google_sub, email, name, avatar) VALUES (?, ?, ?, ?)",
            (google_sub, email, name, avatar),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE google_sub = ?", (google_sub,)
        ).fetchone()
        result = dict(row)
        conn.close()
        return result


def get_user_by_id(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_user_keys(user_id, openai_key=None, minimax_key=None, minimax_group=None,
                     apify_token=None, replicate_token=None):
    conn = get_db()
    for col, val in [("openai_key", openai_key), ("minimax_key", minimax_key),
                     ("minimax_group", minimax_group), ("apify_token", apify_token),
                     ("replicate_token", replicate_token)]:
        if val is not None:
            conn.execute(f"UPDATE users SET {col} = ? WHERE id = ?", (val, user_id))
    conn.commit()
    conn.close()


def update_youtube_cookies(user_id, cookies):
    conn = get_db()
    conn.execute(
        "UPDATE users SET youtube_cookies = ? WHERE id = ?", (cookies, user_id)
    )
    conn.commit()
    conn.close()


def get_user_keys(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT openai_key, minimax_key, minimax_group, youtube_cookies, apify_token, replicate_token FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else {}
