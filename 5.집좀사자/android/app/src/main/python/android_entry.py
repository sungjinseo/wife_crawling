"""앱(Kotlin)에서 부르는 진입점.

run(data_dir, log_path)   collect_naver.main() 을 실행. 출력은 로그 파일로. 결과를 JSON 문자열로 돌려줌
upload(url, token, path)  수집 결과를 gzip 으로 서버에 업로드
status(url, token)        서버 상태(최근 업로드·브리핑 갱신 시각)
tail(log_path, n)         로그 마지막 n줄 (화면 표시용)
"""
import fcntl
import gzip
import json
import os
import sys
import traceback
import urllib.parse


class _Resp:
    """requests.Response 흉내 (collect_naver 가 쓰는 부분만)"""
    def __init__(self, code, text):
        self.status_code, self.text = code, text

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        import requests
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} {self.text[:150]}")


class WebSession:
    """collect_naver.S 대신 쓰는 통신: 휴대폰 브라우저 엔진(WebView)의 fetch() 로 요청"""
    def __init__(self, web):
        self.web, self.headers = web, {}

    def request(self, method, url, params=None, json=None, timeout=30, **kw):
        import requests
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        body = None if json is None else globals()["json"].dumps(json, ensure_ascii=False)
        res = self.web.request(method.upper(), url, body, int(timeout * 1000))
        code, text = int(res.getStatus()), str(res.getText())
        if code == -2:
            raise requests.Timeout(text)
        if code < 0:
            raise requests.ConnectionError(text)
        return _Resp(code, text)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)


def run(data_dir, log_path, use_web=True):
    os.makedirs(data_dir, exist_ok=True)
    # 자동 예약과 '지금 수집'이 겹치면 요청이 두 배가 되어 더 쉽게 막힘 → 하나만 실행
    lock = open(os.path.join(data_dir, ".collect.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return json.dumps({"status": "busy", "file": "", "msg": "이미 수집 중"}, ensure_ascii=False)
    try:
        return _run(data_dir, log_path, use_web)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def _run(data_dir, log_path, use_web):
    os.environ["DATA_DIR"] = data_dir
    os.environ["CACHE_DIR"] = os.path.join(data_dir, "cache")
    f = open(log_path, "a", encoding="utf-8", buffering=1)
    old = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = f
    try:
        # 앱 프로세스가 며칠 살아 있어도 날짜(TODAY)·경로를 새로 읽도록 매번 다시 불러옴
        sys.modules.pop("collect_naver", None)
        import collect_naver as cn
        out = os.path.join(data_dir, f"naver_{cn.TODAY}.json")
        if use_web:
            try:
                from java import jclass
                web = jclass("dev.greatseo.naver.NaverWeb").INSTANCE
                if web.ensureReady(40000):
                    cn.S = WebSession(web)
                    print("통신: 휴대폰 브라우저 엔진(WebView)")
                else:
                    print("! 브라우저 엔진 준비 실패 → 기본 통신으로 진행")
            except Exception as e:
                print(f"! 브라우저 엔진 사용 불가({e}) → 기본 통신으로 진행")
        status, msg = "ok", ""
        try:
            cn.main()
        except SystemExit as e:                    # 네이버가 막아서 중단: 진행분은 저장돼 있어 다음에 이어받음
            status, msg = "blocked", str(e)
        except Exception as e:
            status, msg = "error", f"{type(e).__name__}: {e}"
            traceback.print_exc()
        if status == "ok" and not os.path.exists(out):
            status, msg = "error", "결과 파일이 만들어지지 않았습니다"
        return json.dumps({"status": status, "file": out, "msg": msg[-600:]}, ensure_ascii=False)
    finally:
        sys.stdout, sys.stderr = old
        f.close()


def upload(url, token, path):
    import requests
    raw = open(path, "rb").read()
    try:
        r = requests.post(url, data=gzip.compress(raw), timeout=180, headers={
            "Content-Type": "application/json", "Content-Encoding": "gzip",
            "X-Upload-Token": token, "X-File-Name": os.path.basename(path)})
        return json.dumps({"ok": r.ok, "code": r.status_code, "body": r.text[:500],
                           "kb": len(raw) // 1024}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "code": 0, "body": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


def status(url, token):
    import requests
    try:
        r = requests.get(url, headers={"X-Upload-Token": token}, timeout=20)
        return r.text if r.ok else json.dumps({"error": f"{r.status_code} {r.text[:200]}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


def tail(log_path, n=40):
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-int(n):])
    except FileNotFoundError:
        return ""
