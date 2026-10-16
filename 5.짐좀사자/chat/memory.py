"""대화 저장 (SQLite).

- conversations: 대화 하나 = 위젯이 기억하는 conversation_id 하나. 오래된 대화를 압축한 요약(summary)과
  요약에 포함된 메시지 수(summarized)를 함께 보관
- messages: 사용자 질문과 최종 답변만 저장 (도구 결과는 저장하지 않음 → 다음 질문 때 컨텍스트가 커지지 않음)
- 대화마다 주인(user_id, 로그인 사용자)을 기록하고, 주인이 아니면 불러오기·이어쓰기·지우기를 할 수 없음
- CHAT_KEEP_DAYS(기본 30일) 동안 갱신이 없는 대화는 지움
"""
import os
import re
import tempfile
import sqlite3
import threading
import time
import uuid
from pathlib import Path

DB = Path(os.environ.get("CHAT_DB", "/chat-data/chat.db"))
PERSISTENT = True          # 저장소에 쓸 수 없어 임시 폴더로 바꾸면 False (컨테이너를 다시 만들면 대화가 사라짐)
STORAGE_ERROR = ""
KEEP_DAYS = int(os.environ.get("CHAT_KEEP_DAYS", "30"))
_ID = re.compile(r"^[0-9a-f]{32}$")
_lock = threading.Lock()
_last_cleanup = 0.0


def _con():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init():
    """저장소에 쓸 수 없으면(볼륨 권한 등) 임시 폴더로 바꿔서라도 챗봇은 계속 동작"""
    global DB, PERSISTENT, STORAGE_ERROR
    try:
        _create()
    except (sqlite3.Error, OSError) as e:
        STORAGE_ERROR = f"{DB}: {e}"
        DB, PERSISTENT = Path(tempfile.gettempdir()) / "chat.db", False
        print(f"[memory] 대화 저장소를 쓸 수 없어 임시 저장소({DB})를 사용합니다: {STORAGE_ERROR}", flush=True)
        _create()


def _create():
    with _lock, _con() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS conversations(
            id TEXT PRIMARY KEY, created REAL, updated REAL, summary TEXT DEFAULT '', summarized INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS messages(
            conv_id TEXT, seq INTEGER, role TEXT, content TEXT, ts REAL, meta TEXT,
            PRIMARY KEY(conv_id, seq));
        """)
        cols = [r[1] for r in con.execute("PRAGMA table_info(conversations)")]
        if "user_id" not in cols:                 # 로그인 도입 전 대화: 주인이 없어 누구도 다시 열 수 없음 (30일 뒤 정리)
            con.execute("ALTER TABLE conversations ADD COLUMN user_id TEXT DEFAULT ''")


def valid(cid) -> bool:
    return bool(cid) and bool(_ID.match(str(cid)))


def ensure(cid, user: str) -> str:
    """그 사용자의 대화면 그 id, 없거나 잘못됐거나 남의 대화면 새 대화를 만들어 id 반환"""
    cleanup()
    now = time.time()
    with _lock, _con() as con:
        if valid(cid) and con.execute("SELECT 1 FROM conversations WHERE id=? AND user_id=?", (cid, user)).fetchone():
            return cid
        cid = uuid.uuid4().hex
        con.execute("INSERT INTO conversations(id, created, updated, user_id) VALUES (?,?,?,?)", (cid, now, now, user))
        return cid


def add(cid: str, role: str, content: str, meta: str = None):
    with _lock, _con() as con:
        seq = con.execute("SELECT COALESCE(MAX(seq), -1) + 1 FROM messages WHERE conv_id=?", (cid,)).fetchone()[0]
        con.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (cid, seq, role, content, time.time(), meta))
        con.execute("UPDATE conversations SET updated=? WHERE id=?", (time.time(), cid))


def load(cid: str, user: str):
    """(요약, 요약에 포함된 메시지 수, [{"role","content"}...]) 또는 그 사용자의 대화가 없으면 None"""
    if not valid(cid):
        return None
    with _lock, _con() as con:
        row = con.execute("SELECT summary, summarized FROM conversations WHERE id=? AND user_id=?", (cid, user)).fetchone()
        if not row:
            return None
        msgs = [{"role": r, "content": c} for r, c in
                con.execute("SELECT role, content FROM messages WHERE conv_id=? ORDER BY seq", (cid,))]
    return row[0] or "", row[1] or 0, msgs


def set_summary(cid: str, summary: str, summarized: int):
    with _lock, _con() as con:
        con.execute("UPDATE conversations SET summary=?, summarized=? WHERE id=?", (summary, summarized, cid))


def delete(cid: str, user: str):
    if not valid(cid):
        return
    with _lock, _con() as con:
        if not con.execute("SELECT 1 FROM conversations WHERE id=? AND user_id=?", (cid, user)).fetchone():
            return
        con.execute("DELETE FROM messages WHERE conv_id=?", (cid,))
        con.execute("DELETE FROM conversations WHERE id=?", (cid,))


def cleanup():
    """한 시간에 한 번만: 오래된 대화 정리"""
    global _last_cleanup
    if time.time() - _last_cleanup < 3600:
        return
    _last_cleanup = time.time()
    cut = time.time() - KEEP_DAYS * 86400
    with _lock, _con() as con:
        old = [r[0] for r in con.execute("SELECT id FROM conversations WHERE updated < ?", (cut,))]
        for cid in old:
            con.execute("DELETE FROM messages WHERE conv_id=?", (cid,))
            con.execute("DELETE FROM conversations WHERE id=?", (cid,))
