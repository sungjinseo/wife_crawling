"""도구 등록소: 파이썬 함수 → LLM function-calling 스키마.

    @tool
    def screen_summary(dong: Optional[str] = None, screen: Optional[dict] = None) -> str:
        \"\"\"설명 ...

        Args:
            dong: 인자 설명
        \"\"\"

- 타입 힌트와 docstring 의 Args 로 스키마를 만듭니다.
- `screen` 인자는 서버가 화면 상태로 채우므로 모델에게 보여주지 않습니다.
"""
import inspect
import re
import typing
from typing import Any, Callable, Literal, Optional, Union

HIDDEN = {"screen"}
TOOLS: dict[str, Callable] = {}


def tool(fn: Callable) -> Callable:
    TOOLS[fn.__name__] = fn
    return fn


def _doc(fn):
    doc = inspect.getdoc(fn) or ""
    head, _, args = doc.partition("Args:")
    descs, cur = {}, None
    for line in args.splitlines():
        m = re.match(r"^ {4}(\w+):\s*(.*)", line)        # "    인자: 설명" (들여쓰기 4칸)
        if m:
            cur = m.group(1)
            descs[cur] = m.group(2).strip()
        elif cur and line.strip():
            descs[cur] += " " + line.strip()
    return " ".join(head.split()), descs


def _type_schema(tp) -> dict:
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if origin is Union:                                   # Optional[X] → X (작은 모델은 단순한 스키마를 잘 따름)
        rest = [a for a in args if a is not type(None)]
        return _type_schema(rest[0]) if len(rest) == 1 else {}
    if origin is Literal:
        vals = list(args)
        return {"type": "string" if isinstance(vals[0], str) else "number", "enum": vals}
    if origin in (list, typing.List):
        return {"type": "array", "items": _type_schema(args[0]) if args else {}}
    return {str: {"type": "string"}, int: {"type": "integer"}, float: {"type": "number"},
            bool: {"type": "boolean"}, dict: {"type": "object"}}.get(tp, {})


def schemas(hide: set[str] = frozenset()) -> list[dict]:
    out = []
    for name, fn in TOOLS.items():
        if name in hide:
            continue
        desc, adesc = _doc(fn)
        hints = typing.get_type_hints(fn)
        props, required = {}, []
        for p in inspect.signature(fn).parameters.values():
            if p.name in HIDDEN:
                continue
            s = _type_schema(hints.get(p.name, str))
            if p.name in adesc:
                s["description"] = adesc[p.name]
            if p.default is inspect.Parameter.empty:
                required.append(p.name)
            elif p.default is not None:
                s["default"] = p.default
            props[p.name] = s
        out.append({"type": "function", "function": {
            "name": name, "description": desc[:1000],
            "parameters": {"type": "object", "properties": props, "required": required}}})
    return out


def call(name: str, args: dict, screen: Optional[dict]) -> Any:
    fn = TOOLS.get(name)
    if not fn:
        return {"error": f"알 수 없는 도구: {name}"}
    params = inspect.signature(fn).parameters
    clean = {k: v for k, v in (args or {}).items() if k in params and k not in HIDDEN}
    if "screen" in params:
        clean["screen"] = screen
    try:
        return fn(**clean)
    except TypeError as e:                                 # 모델이 인자를 잘못 준 경우 → 모델이 고칠 수 있게 돌려줌
        return {"error": f"인자 오류: {e}"}
