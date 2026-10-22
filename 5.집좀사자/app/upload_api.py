"""
휴대폰 업로드 API (scheduler.py 가 같은 프로세스에서 스레드로 띄움, 포트 UPLOAD_PORT 기본 8090)

Traefik 이 https://BRIEFING_HOST/upload-api/... 를 접두어를 떼고 이리로 보냄
- POST /naver  : 네이버 매물 JSON 받기 (gzip 가능). 헤더 X-Upload-Token 필수 → data/users/<사용자>/naver_{date}.json 저장
- GET  /status : 그 사용자의 상태 (최신 네이버 파일·업로드 시각·브리핑 갱신·공공 수집일)
토큰은 사용자별: 웹 '내 계정'에서 발급 → 로그인 서비스(AUTH_URL)에 물어 사용자를 알아냄
예전 공용 토큰(UPLOAD_TOKEN)은 OWNER_USER 가 있으면 그 사용자로 받음 (기존 휴대폰 설정 호환)
"""
import datetime as dt
import gzip
import hmac
import io
import json
import os
import re
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", "/data"))
OUT = Path(os.environ.get("OUT_DIR", "/output"))
TOKEN = os.environ.get("UPLOAD_TOKEN", "")
OWNER = os.environ.get("OWNER_USER", "")
AUTH_URL = os.environ.get("AUTH_URL", "http://getting-home-auth:8080").rstrip("/")
USER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
PORT = int(os.environ.get("UPLOAD_PORT", "8090"))
MAX_BODY = 50 << 20          # 받는 크기 (압축 상태) 50MB
MAX_RAW = 300 << 20          # 압축 푼 크기 300MB


def log(msg):
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def naver_problem(d):
    """네이버 파일 형식 확인. 문제 없으면 빈 문자열"""
    cx = d.get("complexes") if isinstance(d, dict) else None
    if not isinstance(d, dict) or not isinstance(d.get("date"), str) or not isinstance(cx, list) or not cx:
        return "date 또는 complexes 항목이 없음"
    if not all(isinstance(c, dict) and "complexNumber" in c and "dong" in c for c in cx[:20]):
        return "complexes 항목에 complexNumber/dong 이 없음"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["date"]):
        return f"date 형식 오류: {d['date'][:20]}"
    return ""


def _mtime(p):
    return dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if p.exists() else "-"


def user_dirs(uid):
    """(수집 데이터 폴더, 브리핑 폴더)"""
    return DATA / "users" / uid, OUT / "users" / uid


def token_user(tok):
    """업로드 토큰 → 사용자 ID. 틀리면 None, 로그인 서비스에 닿지 않으면 예외"""
    if not tok:
        return None
    if TOKEN and OWNER and hmac.compare_digest(tok.encode(), TOKEN.encode()):
        return OWNER
    req = urllib.request.Request(AUTH_URL + "/internal/upload-user", headers={"X-Upload-Token": tok})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            uid = json.load(r).get("user") or ""
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return None
        raise
    return uid if USER_ID.match(uid) else None


def status(uid):
    data, out = user_dirs(uid)
    files = sorted(data.glob("naver_*.json"), reverse=True)
    stamp = DATA / ".last_public"
    return {
        "user": uid,
        "naver_file": files[0].name if files else "-",
        "naver_uploaded": _mtime(files[0]) if files else "-",
        "briefing_updated": _mtime(out / "index.html"),
        "public_collected": stamp.read_text().strip() if stamp.exists() else "-",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "upload-api"
    on_upload = None          # 업로드 성공 시 부를 함수 (스케줄러 깨우기)

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _user(self):
        """토큰의 사용자 ID. 실패하면 응답을 보내고 None"""
        try:
            uid = token_user(self.headers.get("X-Upload-Token", "").strip())
        except (OSError, ValueError) as e:
            log(f"! 로그인 서비스 연결 실패: {e}")
            self._send(503, {"error": "로그인 서비스에 연결할 수 없음. 잠시 후 다시 시도"})
            return None
        if not uid:
            self._send(401, {"error": "토큰이 맞지 않음 (웹 '내 계정'에서 발급한 토큰을 넣으세요)"})
        return uid

    def do_GET(self):
        if self.path.rstrip("/") == "/healthz":
            return self._send(200, {"ok": True})
        if self.path.rstrip("/") != "/status":
            return self._send(404, {"error": "없는 주소"})
        uid = self._user()
        if uid:
            self._send(200, status(uid))

    def do_POST(self):
        if self.path.rstrip("/") != "/naver":
            return self._send(404, {"error": "없는 주소"})
        uid = self._user()
        if not uid:
            return
        size = int(self.headers.get("Content-Length") or 0)
        if not 0 < size <= MAX_BODY:
            return self._send(413, {"error": f"크기 오류 ({size} 바이트)"})
        raw = self.rfile.read(size)
        try:
            if self.headers.get("Content-Encoding", "").lower() == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(MAX_RAW + 1)
                if len(raw) > MAX_RAW:
                    return self._send(413, {"error": "압축 푼 크기가 너무 큼"})
            d = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as e:
            return self._send(400, {"error": f"읽기 오류: {e}"})
        why = naver_problem(d)
        if why:
            log(f"! 업로드 거절 ({uid}, {self.headers.get('X-File-Name', '-')}): {why}")
            return self._send(400, {"error": why})
        # 파일 이름은 보낸 쪽 이름 대신 기준일로 만듦. 임시 파일에 쓰고 바꿔치기 (스케줄러가 반쯤 쓴 파일을 읽지 않도록)
        folder = user_dirs(uid)[0]
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"naver_{d['date']}.json"
        tmp = dest.with_suffix(".json.part")
        tmp.write_bytes(raw)
        tmp.replace(dest)
        n_art = sum(len(c.get("articles", [])) for c in d["complexes"])
        log(f"휴대폰 업로드: {uid}/{dest.name} ({len(raw) // 1024}KB, 단지 {len(d['complexes'])}개, 매물 {n_art}건)")
        self._send(200, {"ok": True, "file": dest.name, "complexes": len(d["complexes"]), "articles": n_art})
        if Handler.on_upload:
            Handler.on_upload()

    def log_message(self, fmt, *args):      # 기본 접근 로그는 끔 (업로드만 위에서 기록)
        pass


def serve(on_upload=None):
    """백그라운드 스레드로 업로드 API 시작"""
    Handler.on_upload = on_upload
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True, name="upload-api").start()
    log(f"업로드 API 시작: :{PORT} (사용자 확인 {AUTH_URL}"
        + (f", 공용 토큰은 {OWNER} 로 받음)" if TOKEN and OWNER else ")"))
    return srv
