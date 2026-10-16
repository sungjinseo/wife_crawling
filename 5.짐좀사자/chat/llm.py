"""NVIDIA NIM (OpenAI 호환) 호출: 스트리밍 + 429/5xx 재시도 + 모델 폴백.

NIM_MODELS 에 여러 모델을 쉼표로 적으면 앞에서부터 쓰고, 한 모델이 한도(429)·오류로 막히면 다음 모델로 넘어갑니다.
막힌 모델은 잠시(COOLDOWN 초) 건너뜁니다. 무료 엔드포인트는 모델별로 분당 약 40회 한도.
"""
import asyncio
import os
import time

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

BASE_URL = os.environ.get("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
API_KEY = os.environ.get("NVIDIA_NIM_API_KEY", "")
MODELS = [m.strip() for m in os.environ.get(
    "NIM_MODELS", "nvidia/nemotron-3-super-120b-a12b,openai/gpt-oss-120b").split(",") if m.strip()]
RETRIES = int(os.environ.get("LLM_RETRIES", "2"))          # 같은 모델 재시도 횟수
COOLDOWN = int(os.environ.get("LLM_COOLDOWN", "60"))       # 한도에 걸린 모델을 쉬게 하는 시간(초)

client = AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY or "none", timeout=120, max_retries=0)
_blocked: dict[str, float] = {}


class LLMError(Exception):
    pass


def _order():
    now = time.time()
    ok = [m for m in MODELS if _blocked.get(m, 0) <= now]
    return ok or MODELS                                       # 다 막혔으면 그래도 순서대로 시도


async def stream_chat(messages, tools=None, temperature=0.2):
    """async generator: ("model", 이름) 한 번, 이후 ("delta", 텍스트) / ("tool_calls", [...]) 를 내보냄.
    첫 글자를 내보내기 전에 실패하면 재시도·다음 모델로 넘어가고, 도중에 끊기면 LLMError."""
    errors = []
    for model in _order():
        for attempt in range(RETRIES + 1):
            started = False
            try:
                stream = await client.chat.completions.create(
                    model=model, messages=messages, tools=tools or None, stream=True,
                    temperature=temperature, max_tokens=2048)
                calls = {}
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    d = chunk.choices[0].delta
                    if not started:
                        started = True
                        yield ("model", model)
                    if d.content:
                        yield ("delta", d.content)
                    for tc in d.tool_calls or []:
                        c = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        c["id"] = tc.id or c["id"]
                        if tc.function and tc.function.name:
                            c["name"] += tc.function.name
                        if tc.function and tc.function.arguments:
                            c["args"] += tc.function.arguments
                if not started:
                    yield ("model", model)
                if calls:
                    yield ("tool_calls", [calls[i] for i in sorted(calls)])
                return
            except (APIStatusError, APIConnectionError, APITimeoutError) as e:
                if started:
                    raise LLMError(f"{model} 응답이 중간에 끊겼습니다: {e}") from e
                status = getattr(e, "status_code", None)
                errors.append(f"{model}: {status or type(e).__name__}")
                if status in (400, 404, 422):                 # 요청 자체 문제(모델명·도구 미지원 등) → 다음 모델
                    break
                if status == 429:
                    _blocked[model] = time.time() + COOLDOWN
                    retry_after = float(e.response.headers.get("retry-after", 0) or 0) if e.response is not None else 0
                    if attempt < RETRIES and retry_after and retry_after <= 10:
                        await asyncio.sleep(retry_after)
                        continue
                    break                                     # 한도 → 바로 다음 모델
                if attempt < RETRIES:
                    await asyncio.sleep(1.5 * (attempt + 1))
    raise LLMError("모든 모델 호출 실패: " + ", ".join(errors[-4:]))
