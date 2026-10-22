"""부동산 뉴스 검색 도구.

- search_news : 기간을 지정한 뉴스 검색
    * Google News RSS (키 불필요, 기간 필터 정확) — 기본
    * 네이버 뉴스 검색 API — 원문 링크·요약 제공. 키 둘 중 하나:
        NAVER_APIHUB_KEY_ID / NAVER_APIHUB_KEY   네이버 클라우드 NAVER API HUB (2026-07-31 이후 신규 발급은 여기만)
        NAVER_CLIENT_ID / NAVER_CLIENT_SECRET    기존 개발자센터 키 (2027-06-30 까지 동작)
- read_article : 기사 URL 본문 추출 (네이버 결과의 원문 링크 등)
"""
import email.utils
import html
import ipaddress
import json
import os
import re
import socket
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Literal, Optional
from urllib.parse import urlparse

import httpx
import trafilatura
from registry import tool

HUB_ID = os.environ.get("NAVER_APIHUB_KEY_ID", "")
HUB_KEY = os.environ.get("NAVER_APIHUB_KEY", "")
LEGACY_ID = os.environ.get("NAVER_CLIENT_ID", "")
LEGACY_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
if HUB_ID and HUB_KEY:          # NAVER API HUB (파라미터·응답 형식은 기존과 같고 주소·헤더만 다름)
    NAVER_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
    NAVER_HEADERS = {"X-NCP-APIGW-API-KEY-ID": HUB_ID, "X-NCP-APIGW-API-KEY": HUB_KEY}
elif LEGACY_ID and LEGACY_SECRET:
    NAVER_URL = "https://openapi.naver.com/v1/search/news.json"
    NAVER_HEADERS = {"X-Naver-Client-Id": LEGACY_ID, "X-Naver-Client-Secret": LEGACY_SECRET}
else:
    NAVER_URL, NAVER_HEADERS = None, None
UA = {"User-Agent": "Mozilla/5.0 (compatible; realestate-news-bot/1.0)"}
TIMEOUT = 15

def _dump(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _parse_date(s) -> Optional[date]:
    try:
        return email.utils.parsedate_to_datetime(s).date()
    except Exception:
        return None


def _to_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y%m%d", "%Y%m"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"날짜 형식을 이해할 수 없습니다: {s} (YYYY-MM-DD 또는 YYYY-MM)")


def _norm_title(t):
    t = re.sub(r"\s+-\s+[^-]+$", "", t)  # Google 제목 끝의 " - 언론사" 제거
    return re.sub(r"[\W_]+", "", t)[:40]


def _google(query, start, end, limit):
    q = query
    if start:
        q += f" after:{(start - timedelta(days=1)).isoformat()}"
    if end:
        q += f" before:{(end + timedelta(days=1)).isoformat()}"
    r = httpx.get("https://news.google.com/rss/search",
                  params={"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"},
                  headers=UA, timeout=TIMEOUT, follow_redirects=True)
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.text).iter("item"):
        title = _clean(it.findtext("title"))
        src = it.find("source")
        source = src.text if src is not None else ""
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        out.append({"date": _parse_date(it.findtext("pubDate")), "title": title, "source": source,
                    "url": it.findtext("link"), "summary": None, "provider": "google"})
    return out


def _naver(query, start, end, limit):
    out, headers = [], NAVER_HEADERS
    # 네이버 API는 기간 필터가 없어 관련도순으로 최대 5페이지를 훑으며 날짜로 거름
    for page in range(5):
        r = httpx.get(NAVER_URL, headers=headers, timeout=TIMEOUT,
                      params={"query": query, "display": 100, "start": page * 100 + 1,
                              "sort": "sim" if start else "date"})
        r.raise_for_status()
        items = r.json().get("items", [])
        for it in items:
            d = _parse_date(it.get("pubDate"))
            if (start and d and d < start) or (end and d and d > end):
                continue
            url = it.get("originallink") or it.get("link")
            out.append({"date": d, "title": _clean(it.get("title")), "source": urlparse(url).netloc.replace("www.", ""),
                        "url": url, "summary": _clean(it.get("description")), "provider": "naver"})
        if len(out) >= limit * 2 or len(items) < 100:
            break
    return out



@tool
def search_news(query: str, start_date: Optional[str] = None, end_date: Optional[str] = None,
                max_results: int = 15, provider: Literal["auto", "google", "naver"] = "auto") -> str:
    """기간을 지정해 한국 뉴스를 검색합니다. 같은 사건의 중복 기사는 묶어서 건수로 보여줍니다.

    Args:
        query: 짧은 검색어 (예: "야탑 재건축", "분당 고도제한", "분당 집값")
        start_date: 시작일 YYYY-MM-DD 또는 YYYY-MM (선택)
        end_date: 종료일 YYYY-MM-DD 또는 YYYY-MM (선택)
        max_results: 최대 결과 수 (기본 15, 최대 40)
        provider: auto(네이버 키 있으면 둘 다) / google / naver
    """
    try:
        start, end = _to_date(start_date), _to_date(end_date)
        if end and end_date and len(end_date.strip()) <= 7:  # YYYY-MM 이면 그 달 말일까지
            end = (end.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        limit = max(1, min(int(max_results), 40))
        results, errors = [], []
        use_naver = provider == "naver" or (provider == "auto" and NAVER_URL)
        use_google = provider in ("google", "auto")
        if use_naver:
            if not NAVER_URL:
                errors.append("네이버 키(NAVER_APIHUB_KEY_ID/KEY)가 설정되지 않았습니다.")
            else:
                try:
                    results += _naver(query, start, end, limit)
                except Exception as e:
                    errors.append(f"naver: {e}")
        if use_google:
            try:
                results += _google(query, start, end, limit)
            except Exception as e:
                errors.append(f"google: {e}")

        # 같은 사건(제목 유사) 묶기 — 네이버 결과(원문 링크·요약 있음)를 대표로
        groups = {}
        for r in sorted(results, key=lambda x: (x["provider"] != "naver", str(x["date"]))):
            k = _norm_title(r["title"])
            if k in groups:
                groups[k]["same_story_count"] += 1
                if r["source"] and r["source"] not in groups[k]["other_sources"] and r["source"] != groups[k]["source"]:
                    groups[k]["other_sources"].append(r["source"])
            else:
                groups[k] = {**r, "same_story_count": 1, "other_sources": []}
        items = sorted(groups.values(), key=lambda x: str(x["date"] or ""), reverse=True)[:limit]
        for it in items:
            it["other_sources"] = it["other_sources"][:5]
            if it["provider"] == "google":
                it["note"] = "google 링크는 본문 추출이 어려움. 본문이 필요하면 제목으로 provider=naver 재검색"

        # 날짜별 기사 수 (사건이 몰린 시점 파악용)
        by_month = {}
        for r in results:
            if r["date"]:
                m = r["date"].strftime("%Y-%m")
                by_month[m] = by_month.get(m, 0) + 1

        out = {"query": query, "기간": f"{start or '-'} ~ {end or '-'}", "총_기사수": len(results),
               "월별_기사수": dict(sorted(by_month.items())), "results": items}
        if errors:
            out["errors"] = errors
        return _dump(out)
    except Exception as e:
        return _dump({"error": str(e)[:400]})


def _safe_url(url: str):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("http(s) URL 만 허용됩니다.")
    for info in socket.getaddrinfo(p.hostname, None):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("내부 주소는 접근할 수 없습니다.")


@tool
def read_article(url: str, max_chars: int = 3000) -> str:
    """기사 URL의 본문을 추출합니다. 원인을 구체적으로 확인해야 할 때 핵심 기사 1~3개만 읽으세요.

    Args:
        url: 기사 원문 URL (네이버 검색 결과의 url 권장)
        max_chars: 최대 글자 수 (기본 3000, 최대 6000)
    """
    try:
        _safe_url(url)
        with httpx.Client(headers=UA, timeout=TIMEOUT, follow_redirects=True) as c:
            r = c.get(url)
            _safe_url(str(r.url))  # 리다이렉트 후 주소도 확인
        r.raise_for_status()
        text = trafilatura.extract(r.text, include_comments=False, include_tables=False) or ""
        meta = trafilatura.extract_metadata(r.text)
        if not text:
            return _dump({"error": "본문을 추출하지 못했습니다.", "url": str(r.url)})
        n = max(500, min(int(max_chars), 6000))
        return _dump({"url": str(r.url), "title": getattr(meta, "title", None), "date": getattr(meta, "date", None),
                      "source": getattr(meta, "sitename", None), "text": text[:n], "truncated": len(text) > n})
    except Exception as e:
        return _dump({"error": str(e)[:400], "url": url})
