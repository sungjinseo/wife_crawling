"""
서버 파이프라인 (pipeline 컨테이너의 기본 실행 명령)

- 매일 PUBLIC_AT(기본 06:00, Asia/Seoul)에 collect_public.py 실행
- CHECK_MIN(기본 10)분마다 사용자별로 확인해서, 네이버 파일이 새로 올라왔거나 공공데이터가 갱신됐으면 build_briefing.py 실행
- 사용자별 폴더: data/users/<아이디>/naver_YYYY-MM-DD.json (+ 챗봇용 briefing_payload.json) → output/users/<아이디>/
  공공데이터(bundang.db, public/)는 모두가 같이 씀
- 네이버 파일은 휴대폰 앱이 업로드 API(upload_api.py, :8090)로 보내거나 서버의 사용자 폴더에 직접 올림
  형식이 잘못된 파일은 건너뛰고 직전 정상 파일 사용. 업로드가 오면 기다리지 않고 바로 확인
- 브리핑이 아직 없는 사용자에게는 output/_empty.html(안내 페이지)을 보여줌 (nginx)
- 예전 방식(data/ 바로 아래 naver_*.json)은 OWNER_USER 가 있으면 그 사용자 폴더로 옮김
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from upload_api import naver_problem, serve

APP = Path(__file__).parent
DATA = Path(os.environ.get("DATA_DIR", "/data"))
OUT = Path(os.environ.get("OUT_DIR", "/output"))
PUBLIC_AT = os.environ.get("PUBLIC_AT", "06:00")
CHECK_MIN = int(os.environ.get("CHECK_MIN", "10"))
STAMP = DATA / ".last_public"
USERS = DATA / "users"
OWNER = os.environ.get("OWNER_USER", "")
USER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")


def log(msg):
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def run(script, *args):
    log(f"실행: {script} {' '.join(args)}")
    r = subprocess.run([sys.executable, str(APP / script), *args])
    log(f"종료: {script} (코드 {r.returncode})")
    return r.returncode == 0


_checked = {}


def valid_naver(path):
    """올라온 네이버 파일 형식 확인 (같은 파일은 수정 시각이 바뀔 때만 다시 확인)"""
    key = (path.name, path.stat().st_mtime)
    if key in _checked:
        return _checked[key]
    ok, why = False, ""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        why = naver_problem(d)
        if not why:
            ok = True
            cx = d["complexes"]
            n_art = sum(len(c.get("articles", [])) for c in cx)
            old = sum(1 for c in cx if c.get("scope") != "all")
            log(f"네이버 파일 확인: {path.name} (기준일 {d['date']}, 단지 {len(cx)}개, 매물 {n_art}건"
                + (f", 24~40평만 수집된 단지 {old}개" if old else "") + ")")
    except (ValueError, UnicodeDecodeError) as e:
        why = f"JSON 오류: {e}"
    if not ok:
        log(f"! 네이버 파일 건너뜀: {path.name} ({why})")
    _checked[key] = ok
    return ok


def user_ids():
    return sorted(p.name for p in USERS.iterdir() if p.is_dir() and USER_ID.match(p.name)) if USERS.exists() else []


def user_dirs(uid):
    """(수집 데이터 폴더, 브리핑 폴더)"""
    return USERS / uid, OUT / "users" / uid


def latest_naver(folder):
    for f in sorted(folder.glob("naver_*.json"), reverse=True):      # 최신 정상 파일
        if valid_naver(f):
            return f
    return None


def needs_build(uid):
    data, out = user_dirs(uid)
    nv = latest_naver(data)
    if not nv or not (DATA / "bundang.db").exists():
        return False
    idx = out / "index.html"
    if not idx.exists():
        return True
    if not (data / "briefing_payload.json").exists():      # 챗봇용 데이터가 없으면 (업데이트 직후) 한 번 다시 생성
        return True
    inputs = [nv, DATA / "bundang.db", STAMP, *(DATA / "public").glob("*.json*")]
    newest = max(p.stat().st_mtime for p in inputs if p.exists())
    return newest > idx.stat().st_mtime


def history_missing():
    """금리(ecos)나 과거 지수가 비어 있으면 06:00 을 기다리지 않고 바로 수집 (새 기능 추가 후 첫 실행 대비)"""
    import sqlite3
    db = DATA / "bundang.db"
    if not db.exists():
        return True
    try:
        con = sqlite3.connect(db)
        n_ecos = con.execute("SELECT COUNT(*) FROM ecos").fetchone()[0]
        first_rone = con.execute("SELECT MIN(ym) FROM rone WHERE name='매매가격지수'").fetchone()[0]
        first_trade = con.execute("SELECT MIN(ym) FROM trades").fetchone()[0]
    except sqlite3.OperationalError:
        log("과거 자료 보강 필요 (금리 테이블 없음) → 지금 수집")
        return True                                   # 이전 버전 DB: 테이블 없음
    start = os.environ.get("HISTORY_FROM", "200601")
    months = int(os.environ.get("TRADE_MONTHS", "120"))
    want_trade = (dt.date.today().replace(day=1) - dt.timedelta(days=31 * (months - 2))).strftime("%Y%m")
    missing = n_ecos == 0 or not first_rone or first_rone > start[:4] + "12" or not first_trade or first_trade > want_trade
    if missing:
        log(f"과거 자료 보강 필요 (금리 {n_ecos}건, 지수 시작 {first_rone}, 실거래 시작 {first_trade}) → 지금 수집")
    return missing


TRY = DATA / ".history_try"


def public_due(now):
    # 과거 자료 보강은 6시간에 한 번만 시도 (받을 수 없는 달 때문에 매번 반복하지 않도록)
    recent_try = TRY.exists() and time.time() - TRY.stat().st_mtime < 6 * 3600
    if not recent_try and history_missing():
        TRY.write_text(now.isoformat())
        return True
    hh, mm = map(int, PUBLIC_AT.split(":"))
    last = STAMP.read_text().strip() if STAMP.exists() else ""
    return not (DATA / "bundang.db").exists() or (now.strftime("%H:%M") >= f"{hh:02d}:{mm:02d}" and last != now.date().isoformat())


def migrate_legacy():
    """예전 방식(data/ 바로 아래)의 네이버 파일·브리핑을 OWNER_USER 폴더로 옮김 (한 번만)"""
    old = list(DATA.glob("naver_*.json"))
    if not old:
        return
    if not OWNER or not USER_ID.match(OWNER):
        log(f"! data/ 바로 아래 예전 네이버 파일 {len(old)}개: .env 에 OWNER_USER=<아이디> 를 넣으면 그 사용자 폴더로 옮깁니다")
        return
    data, out = user_dirs(OWNER)
    data.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    for f in [*old, DATA / "briefing_payload.json"]:
        if f.exists() and not (data / f.name).exists():
            shutil.move(str(f), data / f.name)
    for f in [*OUT.glob("briefing_*.html"), OUT / "index.html"]:
        if f.exists() and not (out / f.name).exists():
            shutil.move(str(f), out / f.name)
    log(f"예전 네이버 파일 {len(old)}개와 브리핑을 {OWNER} 사용자로 옮김")


PLACEHOLDER = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60"><title>브리핑 준비 중</title>
<style>body{font-family:system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;color:#1d2733;background:#f6f7f9}
main{max-width:34em;padding:2em;line-height:1.6}code{background:#e8ecf1;padding:2px 6px;border-radius:4px}</style></head>
<body><main><h1>브리핑 준비 중</h1><p>아직 내 브리핑이 만들어지지 않았어요.</p>
<p>1) <a href="/auth/me">내 계정</a>에서 업로드 토큰을 발급해 휴대폰 앱 설정에 넣고<br>
2) 앱에서 매물을 수집하면, 업로드 후 몇 분 안에 브리핑이 생성되고 이 페이지가 바뀝니다.</p>
<p style="color:#5b6775;font-size:.9em">{state} · <a href="/auth/logout">로그아웃</a></p></main></body></html>"""


def write_placeholder(state):
    """브리핑이 아직 없는 사용자에게 보여줄 안내 페이지 (nginx 가 index.html 이 없을 때 대신 보여줌)"""
    (OUT / "_empty.html").write_text(PLACEHOLDER.replace("{state}", state), encoding="utf-8")


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    waiting = False
    wake = threading.Event()
    serve(on_upload=wake.set)
    migrate_legacy()
    log(f"파이프라인 시작: 공공 수집 매일 {PUBLIC_AT}, 확인 주기 {CHECK_MIN}분, 데이터 {DATA}, 출력 {OUT}")
    while True:
        now = dt.datetime.now()
        if public_due(now):
            if run("collect_public.py"):
                STAMP.write_text(now.date().isoformat())
        users = user_ids()
        for uid in users:
            if needs_build(uid):
                data, out = user_dirs(uid)
                log(f"브리핑 생성: {uid}")
                run("build_briefing.py", "--naver", str(latest_naver(data)), "--out", str(out),
                    "--payload", str(data / "briefing_payload.json"))
        write_placeholder(f"공공데이터: {'있음' if (DATA / 'bundang.db').exists() else '수집 중'} · 확인 {dt.datetime.now():%m-%d %H:%M}")
        if not users and not waiting:
            log("아직 업로드한 사용자가 없습니다. 휴대폰 앱에서 업로드하면 브리핑을 만듭니다")
        waiting = not users
        wake.wait(CHECK_MIN * 60)          # 업로드가 오면 바로 깸
        wake.clear()


if __name__ == "__main__":
    main()
