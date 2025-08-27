import os
import sqlite3
import time
import secrets
from contextlib import contextmanager
from typing import Optional

DB_PATH = os.getenv("DATABASE_PATH", "data/bot.db")
MIGRATIONS_DIR = "."
TTL_DAYS = int(os.getenv("TTL_DAYS", "30"))
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "900"))

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

def _now() -> int:
    return int(time.time())

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def migrate():
    with get_conn() as conn:
        cur = conn.cursor()
        with open(os.path.join(MIGRATIONS_DIR, "001_init.sql"), "r", encoding="utf-8") as f:
            cur.executescript(f.read())

def ensure_user_by_tg(tg_id: int) -> sqlite3.Row:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
        row = cur.fetchone()
        if row:
            return row
        token = secrets.token_urlsafe(8)
        now = _now()
        cur.execute(
            "INSERT INTO users(tg_id, token, created_at, consent_accepted, accepts_questions, last_active) VALUES (?, ?, ?, 0, 1, ?)",
            (tg_id, token, now, now)
        )
        cur.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
        return cur.fetchone()

def get_user_by_token(token: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE token=?", (token,))
        return cur.fetchone()

def get_user_by_id(user_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
        return cur.fetchone()

def get_user_by_tg_id(tg_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
        return cur.fetchone()

def mark_consent(user_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE users SET consent_accepted=1 WHERE id=?", (user_id,))

def update_last_active(user_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE users SET last_active=? WHERE id=?", (_now(), user_id,))

def is_blocked(blocker_id: int, blocked_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM blocks WHERE blocker=? AND blocked=?", (blocker_id, blocked_id))
        return cur.fetchone() is not None

def block_user(blocker_id: int, blocked_id: int):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO blocks(blocker, blocked) VALUES (?, ?)", (blocker_id, blocked_id))

def create_question(from_user: int, to_user: int, text: Optional[str], media_type: Optional[str], file_id: Optional[str]) -> int:
    now = _now()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO questions(to_user, from_user, text, media_type, file_id, created_at, answered, archived) VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
            (to_user, from_user, text, media_type, file_id, now)
        )
        return cur.lastrowid

def set_question_msg(to_user: int, question_id: int, msg_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE questions SET msg_id=? WHERE id=? AND to_user=?", (msg_id, question_id, to_user))

def mark_read_by_msg(to_user: int, msg_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE questions SET read_at=? WHERE to_user=? AND msg_id=? AND read_at IS NULL", (_now(), to_user, msg_id))

def get_question_by_reply(to_user: int, msg_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM questions WHERE to_user=? AND msg_id=?", (to_user, msg_id))
        return cur.fetchone()

def get_question_by_id(qid: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM questions WHERE id=?", (qid,))
        return cur.fetchone()

def create_answer(question_id: int, from_user: int, text: Optional[str], media_type: Optional[str], file_id: Optional[str]):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO answers(question_id, from_user, text, media_type, file_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (question_id, from_user, text, media_type, file_id, _now())
        )
        conn.execute("UPDATE questions SET answered=1 WHERE id=?", (question_id,))

def list_sent_questions(from_user: int, limit: int = 10):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT q.*, u.tg_id AS to_tg FROM questions q JOIN users u ON u.id=q.to_user WHERE q.from_user=? ORDER BY q.created_at DESC LIMIT ?",
            (from_user, limit)
        )
        return cur.fetchall()

def create_session(user_id: int, target_user_id: Optional[int], typ: str):
    with get_conn() as conn:
        expires = _now() + SESSION_TTL_SECONDS
        conn.execute(
            "INSERT INTO sessions(user_id, target_user_id, type, expires_at) VALUES (?, ?, ?, ?)",
            (user_id, target_user_id, typ, expires)
        )

def pop_session(user_id: int, typ: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        cur = conn.cursor()
        now = _now()
        cur.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        cur.execute("SELECT * FROM sessions WHERE user_id=? AND type=? ORDER BY id DESC LIMIT 1", (user_id, typ))
        row = cur.fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM sessions WHERE id=?", (row["id"],))
        return row

def cleanup_old_and_archive():
    threshold = _now() - TTL_DAYS * 86400
    with get_conn() as conn:
        conn.execute("UPDATE questions SET archived=1 WHERE archived=0 AND created_at < ?", (threshold,))
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_now(),))

def add_metric(key: str, inc: int = 1):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM metrics WHERE key=?", (key,))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE metrics SET value=value+? WHERE key=?", (inc, key))
        else:
            cur.execute("INSERT INTO metrics(key, value) VALUES (?, ?)", (key, inc))

def get_metrics():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM metrics ORDER BY key")
        return cur.fetchall()

def create_report(reporter: int, target_user: int, question_id: Optional[int], reason: Optional[str]):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO reports(reporter, target_user, question_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (reporter, target_user, question_id, reason, _now())
        )

def count_users() -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM users")
        row = cur.fetchone()

        return int(row["c"]) if row else 0
