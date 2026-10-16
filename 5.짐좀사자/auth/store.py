"""로그인 저장소 (SQLite): 사용자·세션·업로드 토큰

- 비밀번호는 scrypt 해시로만, 세션·업로드 토큰은 sha256 해시로만 저장 (DB가 새도 원문은 알 수 없음)
- 사용자 ID 는 폴더 이름으로도 쓰이므로 영문 소문자·숫자·_- 만 허용
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

DB = Path(os.environ.get("AUTH_DB", "/auth-data/auth.db"))
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "30"))
USER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
_lock = threading.Lock()


def _con():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init():
    with _lock, _con() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id TEXT PRIMARY KEY, name TEXT, pw TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS sessions(
            hash TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id) ON DELETE CASCADE, created REAL, expires REAL);
        CREATE TABLE IF NOT EXISTS upload_tokens(
            hash TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
            label TEXT, hint TEXT, created REAL, last_used REAL);
        """)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ---------------- 비밀번호 ----------------
def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(got.hex(), h)
    except (ValueError, AttributeError):
        return False


_DUMMY = hash_pw(secrets.token_hex(8))      # 없는 아이디도 같은 시간이 걸리도록


# ---------------- 사용자 ----------------
def add_user(uid: str, pw: str, name: str = ""):
    if not USER_ID.match(uid):
        raise ValueError("아이디는 영문 소문자·숫자·_- 로 2~32자")
    with _lock, _con() as con:
        con.execute("INSERT INTO users VALUES (?,?,?,?)", (uid, name or uid, hash_pw(pw), time.time()))


def set_pw(uid: str, pw: str) -> bool:
    with _lock, _con() as con:
        n = con.execute("UPDATE users SET pw=? WHERE id=?", (hash_pw(pw), uid)).rowcount
        con.execute("DELETE FROM sessions WHERE user_id=?", (uid,))       # 비밀번호를 바꾸면 모든 기기 로그아웃
    return n > 0


def del_user(uid: str) -> bool:
    with _lock, _con() as con:
        return con.execute("DELETE FROM users WHERE id=?", (uid,)).rowcount > 0


def users():
    with _lock, _con() as con:
        return con.execute("SELECT id, name, created FROM users ORDER BY id").fetchall()


def login(uid: str, pw: str):
    """맞으면 사용자 ID, 틀리면 None"""
    with _lock, _con() as con:
        row = con.execute("SELECT pw FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        check_pw(pw, _DUMMY)
        return None
    return uid if check_pw(pw, row[0]) else None


def user_name(uid: str) -> str:
    with _lock, _con() as con:
        row = con.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone()
    return row[0] if row else uid


# ---------------- 세션 ----------------
def new_session(uid: str) -> str:
    tok = secrets.token_urlsafe(32)
    now = time.time()
    with _lock, _con() as con:
        con.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        con.execute("INSERT INTO sessions VALUES (?,?,?,?)", (_sha(tok), uid, now, now + SESSION_DAYS * 86400))
    return tok


def session_user(tok: str):
    if not tok:
        return None
    with _lock, _con() as con:
        row = con.execute("SELECT user_id FROM sessions WHERE hash=? AND expires > ?", (_sha(tok), time.time())).fetchone()
    return row[0] if row else None


def end_session(tok: str):
    if tok:
        with _lock, _con() as con:
            con.execute("DELETE FROM sessions WHERE hash=?", (_sha(tok),))


# ---------------- 업로드 토큰 (휴대폰 앱용) ----------------
def new_upload_token(uid: str, label: str) -> str:
    tok = "gh_" + secrets.token_urlsafe(32)
    with _lock, _con() as con:
        con.execute("INSERT INTO upload_tokens VALUES (?,?,?,?,?,NULL)",
                    (_sha(tok), uid, (label or "휴대폰")[:40], tok[-4:], time.time()))
    return tok


def upload_tokens(uid: str):
    with _lock, _con() as con:
        return con.execute("SELECT hash, label, hint, created, last_used FROM upload_tokens WHERE user_id=? ORDER BY created",
                           (uid,)).fetchall()


def revoke_upload_token(uid: str, h: str):
    with _lock, _con() as con:
        con.execute("DELETE FROM upload_tokens WHERE user_id=? AND hash=?", (uid, h))


def upload_token_user(tok: str):
    if not tok:
        return None
    h = _sha(tok)
    with _lock, _con() as con:
        row = con.execute("SELECT user_id FROM upload_tokens WHERE hash=?", (h,)).fetchone()
        if row:
            con.execute("UPDATE upload_tokens SET last_used=? WHERE hash=?", (time.time(), h))
    return row[0] if row else None
