"""브리핑 화면 내장 챗봇 (컨테이너 하나).

브리핑 화면의 위젯이 질문과 '지금 화면 상태'를 보내면
  1) 시스템 프롬프트에 화면 상태(지역·보기·필터·상단 요약)를 넣고
  2) screen 인자를 받는 도구에는 화면 상태를 서버가 직접 넣고 (모델에게는 숨김)
  3) NVIDIA NIM ↔ 도구 루프를 돌며 SSE 로 스트리밍합니다.

POST /api/chat   {"conversation_id": "...", "message": "질문", "context": briefingContext()}
  → event: meta(conversation_id) / tool / source / delta / done / error
  (예전 방식 {"messages": [...]} 도 그대로 받음: 저장 없이 답만)
GET/DELETE /api/conversations/{id}   저장된 대화 불러오기·지우기
로그인: Traefik forwardAuth 가 붙여 주는 X-User-Id 헤더로 사용자를 구분 (없으면 401).
  도구는 그 사용자의 데이터(data/users/<아이디>)로 계산하고, 대화도 사용자별로 저장
GET  /widget.js, /demo, /api/health

컨텍스트 관리
  1) 도구 결과 압축: 긴 목록은 앞부분만, 소수점 정리, TOOL_RESULT_CHARS(기본 4000자)로 제한. 같은 도구·인자는 재사용(캐시)
  2) 이전 대화는 개수가 아니라 길이 예산 HISTORY_CHARS(기본 6000자)로 자름
  3) 예산을 넘친 오래된 대화는 요약(SUMMARY_CHARS, 기본 700자)으로 압축해 시스템 프롬프트에 넣음
  4) 대화·요약은 SQLite(CHAT_DB)에 저장 → 새로고침해도 이어짐
"""
import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import data_tools  # noqa: F401  (도구 등록)
import news_tools  # noqa: F401
import llm
import memory
import registry

MAX_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", "6"))
MAX_HISTORY = int(os.environ.get("MAX_HISTORY", "12"))
TOOL_TIMEOUT = int(os.environ.get("TOOL_TIMEOUT", "60"))
TOOL_RESULT_CHARS = int(os.environ.get("TOOL_RESULT_CHARS", "4000"))   # 도구 결과 하나의 최대 길이
TOOL_LIST_MAX = int(os.environ.get("TOOL_LIST_MAX", "25"))             # 결과 안 목록은 앞에서부터 이 개수만
TOOL_CACHE_SEC = int(os.environ.get("TOOL_CACHE_SEC", "600"))          # 같은 도구·인자·화면이면 이 시간 동안 재사용
HISTORY_CHARS = int(os.environ.get("HISTORY_CHARS", "6000"))           # 원문으로 보낼 이전 대화 길이 예산
SUMMARY_CHARS = int(os.environ.get("SUMMARY_CHARS", "700"))            # 오래된 대화 요약 길이
HIDE_TOOLS = {t.strip() for t in os.environ.get("HIDE_TOOLS", "describe_data,run_sql").split(",") if t.strip()}
STATIC = Path(__file__).parent / "static"
USER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")

app = FastAPI(title="getting-home chat")

TOOL_LABELS = {
    "screen_summary": "화면 요약 확인", "screen_price_change": "가격 변화 계산", "screen_unit": "단지 지표 확인", "explain_change": "상승 시점 분석",
    "region_trend": "지역 추이 조회", "price_trend": "단지 시세 추이 조회",
    "market_indicators": "금리·가격지수 조회", "listings": "매물 호가 조회",
    "compare_complexes": "단지 비교", "find_complex": "단지 찾기",
    "search_news": "뉴스 검색", "read_article": "기사 읽기",
    "describe_data": "데이터 구조 확인", "run_sql": "데이터 직접 조회",
}

BASE_PROMPT = """너는 '분당 5개 동 아파트 매매 브리핑' 화면 옆에 붙어 있는 분석 비서다. 사용자는 짧게 묻는다.
아래 [현재 화면 상태]가 사용자가 지금 보고 있는 화면이다. "여기", "이 조건", "이거", "왜 이렇게 올랐어" 같은 말은 이 화면을 가리킨다.
사용자가 조건을 말하지 않으면 화면의 지역·보기·필터(평형·가격·방·세대수·년차)로 해석한다.
세대수·년차(N년차 이상)는 단지 조건이라 '필터 조건' 보기에만 적용된다. 방 수·세대수 정보가 없는 거래·매물·단지는 조건으로 거르지 않고 포함된다.
screen_summary 에 '정보없음_포함'이 있으면 답변 끝에 "방/세대수 정보가 없어 포함된 것이 N건 있으니 확인하세요"라고 한 줄 알린다. 화면 상태는 screen_* 도구에 자동 적용되므로 따로 넘기지 않는다.

도구 사용:
- 화면 수치·현재 조건 → screen_summary (화면 상단 요약과 같은 숫자)
- "왜 올랐어/얼마나 올랐어/작년 대비" → screen_price_change(작년 대비 months=12)로 가격·건수 변화와 상승 단지 확인
  → 원인 질문이면 explain_change(같은 동)의 상승_가속시점·뉴스검색_추천 확인 → market_indicators(금리·지수)
  → search_news 2~3회 (지역 재건축·정책·분당 전체). 기사가 몰린 사건은 필요하면 read_article
- 화면에서 연 단지 또는 단지명 → screen_unit, 더 긴 추이는 price_trend
- 10년 추이, 5개 동 밖(판교·구미 등) → region_trend / explain_change / price_trend
- 도구 결과의 숫자만 쓴다. 추측하지 않는다.

답변 형식 (한국어, 짧게):
1) 한 줄 결론
2) 근거: 데이터로 확인된 것 (기간·건수·변동률, 작은 표 또는 2~4줄)
3) 외부 요인 (원인 질문일 때): 뉴스가 제시하는 원인을 언론사·날짜와 함께. 시점이 맞는다는 것만으로 인과를 단정하지 않는다.
4) 필요하면 한 줄 주의 (잠정 데이터, 표본 적음 등)
채팅창이 좁다: 표는 2~3열까지만, 첫 열 이름은 8자 안팎으로 짧게(예: "필터 평당가", "전체 거래"), 셀에는 숫자와 단위만.
조건(평형·가격 범위)은 표 안에 넣지 말고 표 위에 한 줄로 쓴다. 긴 설명은 표 밖 문장으로. 제목은 굵은 글씨 한 줄이면 충분하다.
중요: '거래 건수 변동률'과 '가격(평당가) 변동률'을 절대 섞지 않는다. 화면 요약의 '직전 3개월 대비 +N%'는 건수 변동이다.
사용자가 말한 수치가 도구 결과와 다르면, 그 수치가 건수인지 가격인지 어떤 조건인지 짚어 준다."""



class ChatRequest(BaseModel):
    message: Optional[str] = None                      # 새 방식: 이번 질문만 보냄 (이전 대화는 서버가 저장)
    conversation_id: Optional[str] = None
    messages: Optional[list[dict]] = None              # 예전 방식: 대화 전체를 보냄 (저장 안 함)
    context: Optional[dict] = None


SCOPE_NAME = {"filt": "필터 조건", "area": "평형·방만", "all": "전체"}


def build_screen(ctx: dict) -> dict:
    """브리핑 화면의 briefingContext() → screen_* 도구 인자. 필드 이름이 바뀌면 여기만 고치면 됨."""
    f = ctx.get("filter") or {}
    return {"dong": ctx.get("dong") or "전체", "scope": ctx.get("scope") or "filt",
            "filter": {"areaMin": f.get("areaMin", 24), "areaMax": f.get("areaMax", 40),
                       "priceMin": f.get("priceMin", 0), "priceMax": f.get("priceMax", 15),
                       "rooms": f.get("rooms") or [],
                       "hhMin": f.get("hhMin"), "hhMax": f.get("hhMax"), "ageMin": f.get("ageMin")},
            "cfg": ctx.get("cfg"), "unit": ctx.get("unit")}


def _range(prefix, lo, hi, unit):
    g = lambda v: f"{float(v):,.0f}"
    if lo in (None, "") and hi in (None, ""):
        return ""
    if lo not in (None, "") and hi not in (None, ""):
        return f"{prefix}{g(lo)}~{g(hi)}{unit}"
    return f"{prefix}{g(lo)}{unit} 이상" if lo not in (None, "") else f"{prefix}{g(hi)}{unit} 이하"


def describe_context(ctx: dict, scr: dict) -> str:
    f = scr["filter"]
    rooms = f["rooms"]
    lines = [f"- 오늘: {time.strftime('%Y-%m-%d')} (브리핑 기준일 {ctx.get('date') or '-'})",
             f"- 지역: {'5개 동 전체' if scr['dong'] == '전체' else scr['dong']}",
             f"- 보기: {SCOPE_NAME.get(scr['scope'], scr['scope'])}",
             f"- 필터: 공급 {f['areaMin']}평 이상 {f['areaMax']}평 미만, {f['priceMin']}~{f['priceMax']}억"
             + (", 방 " + "·".join("4개 이상" if r >= 4 else f"{r}개" for r in rooms) if rooms else "")
             + _range(", 세대수 ", f.get("hhMin"), f.get("hhMax"), "세대")
             + _range(", ", f.get("ageMin"), None, "년차")]
    if scr.get("unit"):
        lines.append(f"- 열어 둔 단지: {scr['unit']}")
    if ctx.get("summary"):
        lines.append("- 화면 상단 요약(사용자가 보고 있는 문장): " + str(ctx["summary"])[:800])
    return "\n".join(lines)


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def compact(obj, depth=0):
    """도구 결과를 모델에 넣기 전에 줄임: 긴 목록은 앞부분만, 소수점 2자리, 긴 문자열 자르기"""
    if isinstance(obj, dict):
        return {k: compact(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        out = [compact(v, depth + 1) for v in obj[:TOOL_LIST_MAX]]
        if len(obj) > TOOL_LIST_MAX:
            out.append(f"…외 {len(obj) - TOOL_LIST_MAX}개 생략")
        return out
    if isinstance(obj, float):
        return round(obj, 2)
    if isinstance(obj, str) and len(obj) > 1500:
        return obj[:1500] + "…(생략)"
    return obj


_cache: dict[str, tuple[float, str]] = {}


async def run_tool(name: str, args: dict, scr: dict) -> tuple[str, bool]:
    """(모델에 넣을 결과 문자열, 캐시 재사용 여부). 데이터는 data_tools.USER 의 사용자 것"""
    key = json.dumps([data_tools.USER.get(), name, args, scr if name.startswith("screen") or name == "explain_change" else None],
                     ensure_ascii=False, sort_keys=True, default=str)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TOOL_CACHE_SEC:
        return hit[1], True
    try:
        res = await asyncio.wait_for(asyncio.to_thread(registry.call, name, args, scr), timeout=TOOL_TIMEOUT)
    except asyncio.TimeoutError:
        res = {"error": f"{name} 시간 초과"}
    except Exception as e:
        res = {"error": f"{name} 실행 실패: {e}"}
    if isinstance(res, str):
        text = res
    else:
        text = json.dumps(compact(res), ensure_ascii=False, default=str, separators=(",", ":"))
    if len(text) > TOOL_RESULT_CHARS:
        text = text[:TOOL_RESULT_CHARS] + "…(결과가 길어 일부 생략. 필요하면 범위를 좁혀 다시 조회)"
    if "error" not in text[:20]:
        if len(_cache) > 300:                              # 오래된 것부터 정리
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:100]:
                _cache.pop(k, None)
        _cache[key] = (time.time(), text)
    return text, False


# ---------------- 이전 대화: 길이 예산 + 요약 ----------------
SUMMARY_PROMPT = """아래는 부동산 브리핑 챗봇과 사용자의 지난 대화다. 이후 대화에 필요한 것만 한국어로 짧게 요약하라.
- 사용자가 관심 가진 지역·단지·조건(평형·가격 등)
- 이미 확인한 핵심 수치: 기간·조건과 함께, 숫자는 바꾸지 말 것. 거래 건수와 가격 변동률을 섞지 말 것
- 아직 답하지 못했거나 이어서 볼 질문
5줄 이내 글머리표로만 답하라."""


async def summarize(old_summary: str, msgs: list[dict]) -> str:
    convo = "\n".join(f"{'사용자' if m['role'] == 'user' else '비서'}: {m['content'][:1500]}" for m in msgs)
    user = (f"[기존 요약]\n{old_summary}\n\n" if old_summary else "") + f"[요약할 대화]\n{convo}"
    text = ""
    try:
        async for kind, val in llm.stream_chat([{"role": "system", "content": SUMMARY_PROMPT},
                                                {"role": "user", "content": user[:12000]}], None, temperature=0.1):
            if kind == "delta":
                text += val
    except llm.LLMError:
        text = ""
    if not text.strip():                                   # 요약 실패: 질문만 간추려 남김
        qs = [m["content"][:80] for m in msgs if m["role"] == "user"]
        text = (old_summary + "\n" if old_summary else "") + "\n".join("- 이전 질문: " + q for q in qs)
    return text.strip()[:SUMMARY_CHARS]


def split_by_budget(msgs: list[dict], budget: int) -> tuple[list[dict], list[dict]]:
    """뒤에서부터 예산 안에 드는 만큼을 원문으로, 나머지(앞쪽)는 요약 대상으로. 마지막 질문은 항상 원문."""
    keep, used = [], 0
    for m in reversed(msgs):
        n = len(m["content"] or "")
        if keep and used + n > budget:
            break
        keep.append(m)
        used += n
    keep.reverse()
    return msgs[:len(msgs) - len(keep)], keep


async def run_chat(req: ChatRequest, user: str):
    data_tools.USER.set(user)                          # 이 요청의 도구는 이 사용자 데이터로 (to_thread 에도 이어짐)
    ctx = req.context or {}
    scr = build_screen(ctx)
    system = BASE_PROMPT + "\n\n[현재 화면 상태]\n" + describe_context(ctx, scr)
    cid, summary = None, ""
    if req.message is not None:                        # 저장형: 서버가 이전 대화·요약을 관리
        cid = await asyncio.to_thread(memory.ensure, req.conversation_id, user)
        yield sse("meta", {"conversation_id": cid})
        await asyncio.to_thread(memory.add, cid, "user", req.message.strip())
        summary, summarized, all_msgs = await asyncio.to_thread(memory.load, cid, user)
        older, recent = split_by_budget(all_msgs[summarized:], HISTORY_CHARS)
        if older:                                      # 예산을 넘치면 앞쪽 대화를 요약으로 압축
            # 한 번 정리할 때 예산의 절반까지 줄여 둠 → 이후 몇 번의 질문은 요약 호출 없이 진행 (무료 API 호출 절약)
            older, recent = split_by_budget(all_msgs[summarized:], HISTORY_CHARS // 2)
            yield sse("tool", {"name": "summarize", "label": "이전 대화 정리", "args": {}})
            summary = await summarize(summary, older)
            await asyncio.to_thread(memory.set_summary, cid, summary, summarized + len(older))
        history = recent
    else:                                              # 예전 방식: 보낸 대화를 길이 예산으로만 자름
        msgs = [{"role": m["role"], "content": m["content"]} for m in (req.messages or [])
                if m.get("role") in ("user", "assistant") and m.get("content")][-MAX_HISTORY:]
        history = split_by_budget(msgs, HISTORY_CHARS)[1]
    if summary:
        system += "\n\n[이전 대화 요약]\n" + summary
    messages: list[dict] = [{"role": "system", "content": system}, *history]
    tools = registry.schemas(HIDE_TOOLS)
    used, model, answer = [], None, ""
    try:
        for round_ in range(MAX_ROUNDS + 1):
            last = round_ == MAX_ROUNDS                   # 마지막 라운드는 도구 없이 답만
            text, calls = "", []
            async for kind, val in llm.stream_chat(messages, None if last else tools):
                if kind == "model":
                    model = val
                elif kind == "delta":
                    text += val
                    answer += val
                    yield sse("delta", {"text": val})
                elif kind == "tool_calls":
                    calls = val
            if not calls:
                break
            messages.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": c["id"] or f"call_{round_}_{i}", "type": "function",
                 "function": {"name": c["name"], "arguments": c["args"] or "{}"}} for i, c in enumerate(calls)]})
            for i, c in enumerate(calls):
                try:
                    args = json.loads(c["args"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                result, cached = await run_tool(c["name"], args, scr)
                yield sse("tool", {"name": c["name"], "label": TOOL_LABELS.get(c["name"], c["name"]) + (" (재사용)" if cached else ""),
                                   "args": args})
                used.append({"name": c["name"], "args": args})
                if c["name"] == "search_news":
                    try:
                        for r in json.loads(result).get("results", [])[:8]:
                            yield sse("source", {k: r.get(k) for k in ("title", "source", "date", "url")})
                    except Exception:
                        pass
                messages.append({"role": "tool", "tool_call_id": c["id"] or f"call_{round_}_{i}", "content": result})
        if cid and answer.strip():
            await asyncio.to_thread(memory.add, cid, "assistant", answer.strip(),
                                    json.dumps({"tools": [u["name"] for u in used], "model": model}, ensure_ascii=False))
        yield sse("done", {"tools": used, "model": model, "screen": scr, "conversation_id": cid})
    except llm.LLMError as e:
        yield sse("error", {"message": "AI 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요. (" + str(e)[:200] + ")"})
    except Exception as e:
        yield sse("error", {"message": str(e)[:300]})


@app.on_event("startup")
async def _startup():
    try:
        memory.init()
    except Exception as e:                                 # 저장이 안 돼도 챗봇은 떠야 함
        print(f"[memory] 초기화 실패: {e}", flush=True)


def login_user(request: Request) -> str:
    uid = request.headers.get("x-user-id", "")
    if not USER_ID.match(uid):
        raise HTTPException(401, "로그인이 필요합니다")
    return uid


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    user = login_user(request)
    if req.message is not None:
        if not req.message.strip():
            raise HTTPException(400, "message required")
    elif not req.messages:
        raise HTTPException(400, "message or messages required")
    return StreamingResponse(run_chat(req, user), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/conversations/{cid}")
async def get_conversation(cid: str, request: Request):
    got = await asyncio.to_thread(memory.load, cid, login_user(request))
    if got is None:
        raise HTTPException(404, "conversation not found")
    summary, summarized, msgs = got
    return {"id": cid, "summary": summary, "summarized": summarized, "messages": msgs}


@app.delete("/api/conversations/{cid}")
async def delete_conversation(cid: str, request: Request):
    await asyncio.to_thread(memory.delete, cid, login_user(request))
    return {"ok": True}


@app.get("/api/health")
async def health(request: Request):
    uid = request.headers.get("x-user-id", "")
    payload = data_tools.DATA_DIR / "users" / uid / "briefing_payload.json" if USER_ID.match(uid) else None
    return {"ok": True, "models": llm.MODELS, "key": bool(llm.API_KEY), "tools": sorted(registry.TOOLS),
            "chat_db": str(memory.DB), "chat_db_persistent": memory.PERSISTENT, "chat_db_error": memory.STORAGE_ERROR,
            "user": uid or None, "briefing_payload": bool(payload and payload.exists()), "time": time.strftime("%Y-%m-%d %H:%M:%S")}


@app.get("/widget.js")
async def widget():
    return FileResponse(STATIC / "chat-widget.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/demo")
async def demo():
    """브리핑 연동 전 위젯 동작 확인용 페이지"""
    return FileResponse(STATIC / "demo.html", media_type="text/html")
