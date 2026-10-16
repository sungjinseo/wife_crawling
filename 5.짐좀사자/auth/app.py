"""로그인 서비스 (컨테이너 하나, 포트 8080)

Traefik 이 /auth/* 를 이리로 보내고(로그인 화면), 브리핑·챗봇 요청마다 /internal/verify 로 로그인 여부를 물어봄(forwardAuth).
로그인돼 있으면 응답 헤더 X-User-Id 를 Traefik 이 원래 요청에 붙여 nginx·챗봇으로 넘김.

GET  /internal/verify         forwardAuth: 세션 쿠키 확인 → 200 + X-User-Id / 화면 요청은 로그인으로 302 / API 는 401
GET  /internal/upload-user    업로드 API(pipeline)가 X-Upload-Token 으로 사용자 확인
GET/POST /auth/login, GET /auth/logout
GET  /auth/me                 내 계정: 휴대폰 업로드 토큰 발급·폐기, 비밀번호 변경
/internal/* 은 Traefik 경로(/auth)에 없으므로 외부에서는 부를 수 없음
"""
import html
import os
import time
from collections import defaultdict, deque
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import store

COOKIE = "gh_session"
SECURE = os.environ.get("COOKIE_SECURE", "1") != "0"
MAX_FAIL, FAIL_WINDOW = 8, 600           # 10분에 8번 틀리면 잠시 막음

app = FastAPI(title="getting-home auth", docs_url=None, redoc_url=None, openapi_url=None)
_fails: dict[str, deque] = defaultdict(deque)


@app.on_event("startup")
def _startup():
    store.init()
    n = len(store.users())
    print(f"[auth] 시작: 사용자 {n}명" + ("" if n else " → python manage.py add <아이디> 로 계정을 만드세요"), flush=True)


# ---------------- 공통 ----------------
def client_ip(req: Request) -> str:
    return (req.headers.get("cf-connecting-ip") or req.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (req.client.host if req.client else "-"))


def current_user(req: Request):
    return store.session_user(req.cookies.get(COOKIE, ""))


def safe_next(nxt: str) -> str:
    """로그인 뒤 돌아갈 주소: 같은 사이트 경로만 (//다른사이트, /auth 반복 방지)"""
    if not nxt or not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt or nxt.startswith("/auth"):
        return "/"
    return nxt


def same_origin(req: Request) -> bool:
    """폼 전송이 우리 화면에서 온 것인지 (다른 사이트에서 몰래 보내는 요청 차단)"""
    origin = req.headers.get("origin") or req.headers.get("referer") or ""
    if not origin:
        return True                      # 일부 WebView 는 Origin 을 안 보냄. 쿠키가 SameSite=Lax 라 교차 사이트 POST 엔 세션이 없음
    host = req.headers.get("x-forwarded-host") or req.headers.get("host", "")
    return origin.split("://", 1)[-1].split("/", 1)[0] == host


async def form(req: Request) -> dict:
    body = (await req.body()).decode("utf-8", errors="replace")
    return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}


CSS = """:root{--bg:#f6f7f9;--card:#fff;--fg:#1d2733;--sub:#5b6775;--line:#dde2e8;--acc:#2563eb;--bad:#c62828;--ok:#2e7d32}
@media (prefers-color-scheme:dark){:root{--bg:#12161c;--card:#1b2129;--fg:#e6ebf1;--sub:#97a3b1;--line:#2c3540;--acc:#5b8def;--bad:#ef6b6b;--ok:#5fbf6a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,"Noto Sans KR",sans-serif}
main{max-width:440px;margin:0 auto;padding:40px 16px}h1{font-size:1.4em;margin:0 0 4px}h2{font-size:1.05em;margin:0 0 12px}
.sub{color:var(--sub);font-size:.9em;margin:0 0 24px}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:16px}
label{display:block;font-size:.9em;color:var(--sub);margin:12px 0 4px}input{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);font-size:16px}
button,.btn{display:inline-block;margin-top:16px;padding:11px 16px;border:0;border-radius:8px;background:var(--acc);color:#fff;font-size:15px;text-decoration:none;cursor:pointer}
button.full{width:100%}button.link{background:none;color:var(--bad);padding:0;margin:0;font-size:.9em}
.err{color:var(--bad);font-size:.92em;margin:12px 0 0}.ok{color:var(--ok);font-size:.92em;margin:12px 0 0}
.tok{font-family:ui-monospace,monospace;word-break:break-all;background:var(--bg);border:1px dashed var(--acc);border-radius:8px;padding:10px;margin-top:8px}
table{width:100%;border-collapse:collapse;font-size:.9em}td{padding:8px 4px;border-top:1px solid var(--line);vertical-align:top}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:8px}.top a{color:var(--sub);font-size:.9em}"""


def page(title: str, body: str, code: int = 200) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>{CSS}</style></head>
<body><main>{body}</main></body></html>""", status_code=code, headers={"Cache-Control": "no-store"})


def e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def fmt_t(t) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "-"


# ---------------- Traefik forwardAuth / 내부용 ----------------
@app.get("/internal/verify")
def verify(req: Request):
    uid = current_user(req)
    if uid:
        return Response(status_code=200, headers={"X-User-Id": uid})
    uri = req.headers.get("x-forwarded-uri", "/")
    method = req.headers.get("x-forwarded-method", "GET")
    if method == "GET" and not uri.startswith("/chat-api") and "text/html" in req.headers.get("accept", ""):
        return RedirectResponse("/auth/login?next=" + quote(safe_next(uri), safe=""), status_code=302)
    return JSONResponse({"error": "로그인이 필요합니다"}, status_code=401)


@app.get("/internal/upload-user")
def upload_user(req: Request):
    uid = store.upload_token_user(req.headers.get("x-upload-token", ""))
    return JSONResponse({"user": uid}) if uid else JSONResponse({"error": "토큰이 맞지 않음"}, status_code=401)


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---------------- 로그인 ----------------
def login_page(nxt: str, msg: str = "", uid: str = "", code: int = 200):
    return page("로그인 · 분당 브리핑", f"""<h1>분당 브리핑</h1><p class="sub">로그인하면 내 매물로 만든 브리핑을 볼 수 있어요.</p>
<form class="card" method="post" action="/auth/login">
<input type="hidden" name="next" value="{e(nxt)}">
<label for="u">아이디</label><input id="u" name="username" value="{e(uid)}" autocomplete="username" autocapitalize="none" required autofocus>
<label for="p">비밀번호</label><input id="p" name="password" type="password" autocomplete="current-password" required>
{f'<p class="err">{e(msg)}</p>' if msg else ''}
<button class="full">로그인</button></form>
<p class="sub">계정은 관리자에게 요청하세요.</p>""", code)


@app.get("/auth/login")
def login_form(req: Request, next: str = "/"):
    if current_user(req):
        return RedirectResponse(safe_next(next), status_code=302)
    return login_page(safe_next(next))


@app.post("/auth/login")
async def login(req: Request):
    f = await form(req)
    uid = (f.get("username") or "").strip().lower()
    nxt = safe_next(f.get("next", "/"))
    if not same_origin(req):
        return login_page(nxt, "잘못된 요청입니다. 다시 시도해 주세요.", uid, 400)
    now = time.time()
    keys = [f"ip:{client_ip(req)}", f"u:{uid}"]
    for k in keys:
        q = _fails[k]
        while q and now - q[0] > FAIL_WINDOW:
            q.popleft()
        if len(q) >= MAX_FAIL:
            return login_page(nxt, "로그인 실패가 많아 잠시 막았어요. 10분 뒤 다시 시도하세요.", uid, 429)
    if not store.login(uid, f.get("password", "")):
        for k in keys:
            _fails[k].append(now)
        print(f"[auth] 로그인 실패: {uid or '-'} ({client_ip(req)})", flush=True)
        return login_page(nxt, "아이디 또는 비밀번호가 맞지 않습니다.", uid, 401)
    for k in keys:
        _fails.pop(k, None)
    res = RedirectResponse(nxt, status_code=303)
    res.set_cookie(COOKIE, store.new_session(uid), max_age=store.SESSION_DAYS * 86400, path="/",
                   httponly=True, secure=SECURE, samesite="lax")
    print(f"[auth] 로그인: {uid} ({client_ip(req)})", flush=True)
    return res


@app.get("/auth/logout")
def logout(req: Request):
    store.end_session(req.cookies.get(COOKIE, ""))
    res = RedirectResponse("/auth/login", status_code=303)
    res.delete_cookie(COOKIE, path="/")
    return res


# ---------------- 내 계정 ----------------
def me_page(uid: str, msg: str = "", err: str = "", new_tok: str = ""):
    rows = "".join(f"""<tr><td>{e(label)}<br><span class="sub" style="font-size:.85em">…{e(hint)} · 발급 {fmt_t(created)} · 마지막 사용 {fmt_t(used)}</span></td>
<td style="text-align:right"><form method="post" action="/auth/me/revoke" onsubmit="return confirm('이 토큰을 폐기할까요? 이 토큰을 쓰는 휴대폰은 업로드가 안 됩니다.')">
<input type="hidden" name="h" value="{e(h)}"><button class="link">폐기</button></form></td></tr>"""
                   for h, label, hint, created, used in store.upload_tokens(uid))
    return page("내 계정 · 분당 브리핑", f"""<div class="top"><h1>{e(store.user_name(uid))}</h1><a href="/">← 브리핑으로</a></div>
<p class="sub">아이디 {e(uid)}</p>
{f'<p class="ok">{e(msg)}</p>' if msg else ''}{f'<p class="err">{e(err)}</p>' if err else ''}
<section class="card"><h2>휴대폰 업로드 토큰</h2>
<p class="sub" style="margin:0">앱 → 설정의 '업로드 토큰'에 넣으면, 휴대폰에서 수집한 매물이 내 브리핑으로 올라와요.</p>
{f'<p class="ok">새 토큰입니다. 지금만 보이니 바로 복사해 앱에 넣으세요.</p><div class="tok">{e(new_tok)}</div>' if new_tok else ''}
{f'<table>{rows}</table>' if rows else '<p class="sub" style="margin:12px 0 0">발급한 토큰이 없습니다.</p>'}
<form method="post" action="/auth/me/token"><label for="l">이름 (예: 갤럭시)</label><input id="l" name="label" maxlength="40" placeholder="휴대폰">
<button>새 토큰 발급</button></form></section>
<form class="card" method="post" action="/auth/me/password"><h2>비밀번호 변경</h2>
<label for="o">지금 비밀번호</label><input id="o" name="old" type="password" autocomplete="current-password" required>
<label for="n">새 비밀번호 (8자 이상)</label><input id="n" name="new" type="password" autocomplete="new-password" minlength="8" required>
<label for="n2">새 비밀번호 확인</label><input id="n2" name="new2" type="password" autocomplete="new-password" minlength="8" required>
<button>변경</button><p class="sub" style="margin:8px 0 0">바꾸면 모든 기기에서 로그아웃됩니다.</p></form>
<a class="btn" href="/auth/logout" style="background:var(--sub)">로그아웃</a>""")


def need_login():
    return RedirectResponse("/auth/login?next=" + quote("/auth/me", safe=""), status_code=303)


@app.get("/auth/me")
def me(req: Request):
    uid = current_user(req)
    return me_page(uid) if uid else RedirectResponse("/auth/login", status_code=302)


@app.post("/auth/me/token")
async def me_token(req: Request):
    uid = current_user(req)
    if not uid or not same_origin(req):
        return need_login()
    f = await form(req)
    if len(store.upload_tokens(uid)) >= 10:
        return me_page(uid, err="토큰은 10개까지 만들 수 있어요. 안 쓰는 토큰을 먼저 폐기하세요.")
    tok = store.new_upload_token(uid, (f.get("label") or "").strip())
    print(f"[auth] 업로드 토큰 발급: {uid}", flush=True)
    return me_page(uid, new_tok=tok)


@app.post("/auth/me/revoke")
async def me_revoke(req: Request):
    uid = current_user(req)
    if not uid or not same_origin(req):
        return need_login()
    store.revoke_upload_token(uid, (await form(req)).get("h", ""))
    return me_page(uid, msg="토큰을 폐기했습니다.")


@app.post("/auth/me/password")
async def me_password(req: Request):
    uid = current_user(req)
    if not uid or not same_origin(req):
        return need_login()
    f = await form(req)
    if not store.login(uid, f.get("old", "")):
        return me_page(uid, err="지금 비밀번호가 맞지 않습니다.")
    if len(f.get("new", "")) < 8 or f.get("new") != f.get("new2"):
        return me_page(uid, err="새 비밀번호는 8자 이상이고, 확인 칸과 같아야 합니다.")
    store.set_pw(uid, f["new"])
    res = RedirectResponse("/auth/login", status_code=303)
    res.delete_cookie(COOKIE, path="/")
    return res
