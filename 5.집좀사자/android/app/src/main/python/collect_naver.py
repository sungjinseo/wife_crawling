"""
네이버 부동산 현재 매물 수집 (집 PC에서 하루 1회 실행)

대상: 분당구 수내동·정자동·서현동·이매동·야탑동
수집: 동 경계/좌표, 단지(좌표·지번·세대수·매물수), 전체 평형, 전체 매매 매물(호가·중개사수·노출일),
      지하철역·초등학교 좌표
결과: data/naver_YYYY-MM-DD.json  →  build_report.py 로 HTML 생성

실행: pip install requests  &&  python collect_naver.py
옵션: seeds.json 에 {"수내동": [1994, ...]} 형태로 누락 단지를 직접 추가할 수 있음
"""
import datetime as dt
import json
import random
import re
import sys
import time
from pathlib import Path

import requests

BASE = "https://fin.land.naver.com"
DONGS = ["수내동", "정자동", "서현동", "이매동", "야탑동"]
PYEONG = 3.3058
# 평형·가격 필터는 수집 단계에서 걸지 않음 (전체 매물 수집 → 브리핑 화면에서 필터)
APT_TYPES = {"A01", "A04", "B01"}                        # 아파트 / 재건축 / 분양권(필터 캡처 기준)
DELAY = 3.5                 # 기본 간격(초) + 0~2초 무작위
REST_EVERY, REST_SEC = 25, 90   # 단지 25개마다 90초 휴식
WAITS = [30, 120, 300]      # 타임아웃·연결 오류 시 대기 후 재시도
STATIC_CACHE_DAYS = 30

import os
ROOT = Path(__file__).parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
CACHE = Path(os.environ.get("CACHE_DIR", DATA / "cache"))
CACHE.mkdir(parents=True, exist_ok=True)
DATA.mkdir(parents=True, exist_ok=True)
TODAY = dt.date.today().isoformat()

S = requests.Session()
S.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Referer": BASE + "/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9",
})


# ---------------- HTTP ----------------
class NaverBlocked(Exception):
    """재시도해도 응답이 없을 때: 진행분을 저장하고 중단"""


def req(method, path, params=None, body=None):
    for attempt in range(len(WAITS) + 1):
        time.sleep(DELAY + random.uniform(0, 2))
        try:
            r = S.request(method, BASE + path, params=params, json=body, timeout=30)
            if r.status_code != 429:
                break
            reason = "429"
        except (requests.Timeout, requests.ConnectionError) as e:
            reason = e.__class__.__name__
        if attempt == len(WAITS):
            raise NaverBlocked(f"{reason} 반복 ({path})")
        print(f"  ! {reason} → {WAITS[attempt]}초 대기 후 재시도 ({attempt + 1}/{len(WAITS)})")
        time.sleep(WAITS[attempt])
    r.raise_for_status()
    d = r.json()
    if isinstance(d, dict) and d.get("isSuccess") is False:
        print(f"  ! 실패 응답 {path}: {d.get('detailCode')} {d.get('message')}")
        return None
    return d.get("result") if isinstance(d, dict) else d


def cached(name, fn):
    p = CACHE / f"{name}.json"
    if p.exists() and time.time() - p.stat().st_mtime < STATIC_CACHE_DAYS * 86400:
        return json.loads(p.read_text(encoding="utf-8"))
    v = fn()
    if v:                                   # 빈 결과·실패 응답은 캐시하지 않음
        p.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    return v


def autocomplete(kind, keyword, size=None, max_pages=20):
    out = []
    for page in range(max_pages):
        params = {"keyword": keyword, "page": page}
        if size:
            params["size"] = size
        res = req("GET", f"/front-api/v1/search/autocomplete/{kind}", params)
        if isinstance(res, list):
            out += res
            break
        out += (res or {}).get("list", [])
        if not (res or {}).get("hasNextPage"):
            break
    return out


# ---------------- 응답 구조를 모르는 항목용 범용 파서 ----------------
def find_coord(o):
    """dict 안 어딘가의 좌표 → (lng, lat)"""
    if isinstance(o, dict):
        for kx, ky in (("xCoordinate", "yCoordinate"), ("longitude", "latitude"), ("lng", "lat")):
            if isinstance(o.get(kx), (int, float)) and isinstance(o.get(ky), (int, float)):
                return [o[kx], o[ky]]
        for v in o.values():
            c = find_coord(v)
            if c:
                return c
    elif isinstance(o, list):
        for v in o:
            c = find_coord(v)
            if c:
                return c
    return None


def find_name(o):
    for k in ("name", "complexName", "stationName", "schoolName", "legalDivisionName", "title"):
        if isinstance(o.get(k), str) and o[k]:
            return o[k]
    for k, v in o.items():
        if "name" in k.lower() and isinstance(v, str) and v:
            return v
    return None


def find_values(o, key):
    vals = []
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key and isinstance(v, (int, str)):
                vals.append(v)
            else:
                vals += find_values(v, key)
    elif isinstance(o, list):
        for v in o:
            vals += find_values(v, key)
    return vals


def to_latlng(a, b):
    return [b, a] if a > 90 else [a, b]          # 127.x(경도)/37.x(위도) 구분


def extract_rings(o):
    """폴리곤 응답(형식 미확인)에서 [lat,lng] 고리 목록 추출: 배열/WKT/좌표 dict 모두 대응"""
    rings = []
    if isinstance(o, str) and "POLYGON" in o.upper():
        for part in re.findall(r"\(([^()]+)\)", o):
            pts = [to_latlng(float(a), float(b))
                   for a, b in re.findall(r"(-?\d+\.?\d*)\s+(-?\d+\.?\d*)", part)]
            if len(pts) >= 3:
                rings.append(pts)
        return rings
    if isinstance(o, list) and len(o) >= 3:
        if all(isinstance(p, list) and len(p) >= 2 and all(isinstance(x, (int, float)) for x in p[:2]) for p in o):
            return [[to_latlng(p[0], p[1]) for p in o]]
        if all(isinstance(p, dict) for p in o):
            pts = [find_coord(p) for p in o]
            if all(pts):
                return [[to_latlng(*p) for p in pts]]
    if isinstance(o, dict):
        for v in o.values():
            rings += extract_rings(v)
    elif isinstance(o, list):
        for v in o:
            rings += extract_rings(v)
    return rings


def points(items):
    out = []
    for it in items:
        c, n = find_coord(it), find_name(it) if isinstance(it, dict) else None
        if c and n:
            out.append({"name": n, "lng": c[0], "lat": c[1]})
    return list({p["name"]: p for p in out}.values())


# ---------------- 수집 단계 ----------------
def collect_dongs():
    items = cached("legalDivisions_bundang", lambda: autocomplete("legalDivisions", "분당구"))
    dongs = []
    for name in DONGS:
        t = next((i for i in items if i.get("legalDivisionName", "").endswith(f"분당구 {name}")), None)
        if not t:
            print(f"  ! {name} 법정동 코드를 찾지 못함")
            continue
        x, y = t["coordinates"]["xCoordinate"], t["coordinates"]["yCoordinate"]
        raw = cached(f"polygon_{t['legalDivisionNumber']}", lambda: req(
            "GET", "/front-api/v1/legalDivision/searchByCoordinate",
            {"longitude": x, "latitude": y, "type": "EUP", "needsPolygon": "true"}))
        rings = extract_rings(raw)
        print(f"  {name} {t['legalDivisionNumber']} 경계 고리 {len(rings)}개")
        dongs.append({"name": name, "code": t["legalDivisionNumber"],
                      "center": [y, x], "rings": rings})
    return dongs


def candidate_complexes(dong):
    nums = set()
    for kw in (dong, f"분당구 {dong}"):
        items = cached(f"complexes_{kw}", lambda kw=kw: autocomplete("complexes", kw))   # size 파라미터는 큰 값이면 실패 → 기본값 사용
        nums |= {int(v) for v in find_values(items, "complexNumber") if str(v).isdigit()}
    seeds = DATA / "seeds.json"
    if seeds.exists():
        nums |= set(json.loads(seeds.read_text(encoding="utf-8")).get(dong, []))
    return sorted(nums)


def collect_articles(cn, pyeong_nums):
    body = {"size": 30, "complexNumber": cn, "tradeTypes": ["A1"], "pyeongTypes": pyeong_nums,
            "dongNumbers": [], "userChannelType": "PC", "articleSortType": "RANKING_DESC", "lastInfo": []}
    seen, out = set(), []
    for _ in range(10):
        res = req("POST", "/front-api/v1/complex/article/list", body=body) or {}
        for g in res.get("list", []):
            a = g.get("representativeArticleInfo", {})
            dup = g.get("duplicatedArticleInfo", {})
            if a.get("articleNumber") in seen:
                continue
            seen.add(a.get("articleNumber"))
            sp = a.get("spaceInfo", {})
            rng = dup.get("representativePriceInfo", {}).get("dealPrice", {})
            exposures = [x.get("verificationInfo", {}).get("exposureStartDate")
                         for x in dup.get("articleInfoList", [])] or [a.get("verificationInfo", {}).get("exposureStartDate")]
            out.append({
                "articleNumber": a.get("articleNumber"),
                "dong": a.get("dongName"),
                "floor": a.get("articleDetail", {}).get("floorInfo"),
                "supply": sp.get("supplySpace"),
                "exclusive": sp.get("exclusiveSpace"),
                "price": a.get("priceInfo", {}).get("dealPrice"),
                "priceMin": rng.get("minPrice"),
                "priceMax": rng.get("maxPrice"),
                "realtors": dup.get("realtorCount", 1),
                "exposure": min(e for e in exposures if e) if any(exposures) else None,
                "priceChange": a.get("priceInfo", {}).get("priceChangeStatus"),
            })
        if not res.get("hasNextPage"):
            break
        body["lastInfo"] = res.get("lastInfo", [])
    return out


META_PATH = CACHE / "complex_meta.json"
META = json.loads(META_PATH.read_text(encoding="utf-8")) if META_PATH.exists() else {}


def is_target(meta, dong):
    return meta.get("sector") == f"경기도 성남시 분당구 {dong}" and meta.get("type") in APT_TYPES


def collect_complex(cn, dong):
    m = META.get(str(cn))
    if m and time.time() - m.get("ts", 0) < STATIC_CACHE_DAYS * 86400 and not is_target(m, dong):
        return None                                    # 다른 동·비대상 유형: 요청 생략
    s = req("GET", "/front-api/v1/complex/mapComplexSummaryInfo", {"complexNumber": cn})
    if not s:
        return None
    ci = s.get("complexInfo", {})
    addr = ci.get("address", {})
    META[str(cn)] = {"sector": addr.get("sector"), "type": ci.get("type"), "ts": time.time()}
    META_PATH.write_text(json.dumps(META, ensure_ascii=False), encoding="utf-8")
    if not is_target(META[str(cn)], dong):
        return None
    pyeongs_raw = cached(f"pyeong_{cn}", lambda: req(
        "GET", "/front-api/v1/complex/pyeongList", {"complexNumber": cn})) or []
    pyeongs = [{"number": p.get("number"), "name": p.get("name"), "supply": p.get("supplyArea"),
                "exclusive": p.get("exclusiveArea"), "households": p.get("householdCount") or 0,
                "rooms": p.get("roomCount"), "baths": p.get("bathRoomCount")}
               for p in pyeongs_raw]
    target = [p for p in pyeongs if p.get("number") is not None]          # 전체 평형
    counts = s.get("articleCountInfoDto", {})
    articles = []
    if target and counts.get("dealCount"):
        articles = collect_articles(cn, [p["number"] for p in target])
    c = addr.get("coordinates", {})
    return {
        "complexNumber": cn, "name": ci.get("name"), "type": ci.get("type"), "dong": dong,
        "jibun": addr.get("jibun"), "road": addr.get("roadName"),
        "lat": c.get("yCoordinate"), "lng": c.get("xCoordinate"),
        "households": ci.get("totalHouseholdNumber"), "buildings": ci.get("buildingCount"),
        "approval": ci.get("useApprovalDate"),
        "dealCountAll": counts.get("dealCount", 0),
        "dealMin": s.get("articlePriceInfoDto", {}).get("dealMinPrice"),
        "dealMax": s.get("articlePriceInfoDto", {}).get("dealMaxPrice"),
        "pyeongs": pyeongs, "targetPyeongs": [p["number"] for p in target], "scope": "all",
        "articles": articles,
    }


def main():
    print(f"=== 네이버 수집 {TODAY} ===")
    S.get(BASE + "/", timeout=20)

    print("[1] 법정동 경계")
    dongs = collect_dongs()

    print("[2] 지하철역 / 초등학교")
    stations = points(cached("stations", lambda: autocomplete("subwayStations", "분당구")))
    schools = points(cached("elementary", lambda: autocomplete("elementarySchool", "분당구")))
    print(f"  역 {len(stations)}곳, 초등학교 {len(schools)}곳")

    print("[3] 단지")
    prog_path = DATA / f"progress_{TODAY}.json"          # 같은 날 재실행 시 이어서 수집
    progress = json.loads(prog_path.read_text(encoding="utf-8")) if prog_path.exists() else {}
    # 이전 방식(24~40평만)으로 모은 진행분은 버리고 다시 수집
    stale = [k for k, v in progress.items() if v and v.get("scope") != "all"]
    for k in stale:
        del progress[k]
    if stale:
        print(f"  이전 방식(24~40평만) 진행분 {len(stale)}개는 전체 매물로 다시 수집")
    if progress:
        print(f"  오늘 진행분 {len(progress)}개 이어서 진행")
    done_now = 0
    try:
        for d in dongs:
            cands = candidate_complexes(d["name"])
            print(f"  {d['name']}: 후보 {len(cands)}개")
            for cn in cands:
                key = f"{d['name']}:{cn}"
                if key in progress:
                    continue
                c = collect_complex(cn, d["name"])
                progress[key] = c
                prog_path.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")
                if c:
                    done_now += 1
                    print(f"    {c['name']:<16} 매매 {c['dealCountAll']:>3}건 | 수집 매물 {len(c['articles'])}건")
                    if done_now % REST_EVERY == 0:
                        print(f"  ...{REST_SEC}초 휴식")
                        time.sleep(REST_SEC)
    except NaverBlocked as e:
        n = sum(1 for v in progress.values() if v)
        sys.exit(f"\n중단: {e}\n단지 {n}개까지 {prog_path.name}에 저장됨. "
                 f"30분~몇 시간 뒤 다시 실행하면 이어서 수집합니다.")
    complexes = [v for v in progress.values() if v]

    out = DATA / f"naver_{TODAY}.json"
    out.write_text(json.dumps({"date": TODAY, "dongs": dongs, "stations": stations,
                               "schools": schools, "complexes": complexes},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"=== 완료: 단지 {len(complexes)}개 → {out} ===")


if __name__ == "__main__":
    main()
