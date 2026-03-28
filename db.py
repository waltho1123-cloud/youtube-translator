import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.db")


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
        minimax_group TEXT DEFAULT ''
    )''')
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


def update_user_keys(user_id, openai_key=None, minimax_key=None, minimax_group=None):
    conn = get_db()
    if openai_key is not None:
        conn.execute(
            "UPDATE users SET openai_key = ? WHERE id = ?", (openai_key, user_id)
        )
    if minimax_key is not None:
        conn.execute(
            "UPDATE users SET minimax_key = ? WHERE id = ?", (minimax_key, user_id)
        )
    if minimax_group is not None:
        conn.execute(
            "UPDATE users SET minimax_group = ? WHERE id = ?", (minimax_group, user_id)
        )
    conn.commit()
    conn.close()


def get_user_keys(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT openai_key, minimax_key, minimax_group FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else {}
