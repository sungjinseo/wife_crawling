"""실거래 DB(bundang.db 등) 분석 도구 + 브리핑 화면과 같은 계산(screen_*) 도구. 읽기 전용.

데이터 디렉터리(/data)를 읽어 DuckDB 메모리에 정리된 테이블을 만들고,
추이 질문에 바로 답할 수 있는 도구를 제공합니다.

  /data/bundang.db             SQLite: trades(실거래), complexes(좌표), rone(부동산원), ecos(금리)
  /data/public/stations.json   역 좌표
  /data/public/schools.json    학교 좌표
  /data/users/<아이디>/naver_YYYY-MM-DD.json   그 사용자의 네이버 단지·평형·매물 (가장 최신 파일 자동 선택)
  /data/users/<아이디>/briefing_payload.json   그 사용자 브리핑 화면과 같은 데이터 (screen_* 도구)

어느 사용자 데이터를 볼지는 요청마다 USER(ContextVar)로 정함 (app.py 가 로그인 사용자로 설정).
사용자마다 따로 적재하고, 파일이 바뀌면 다음 호출 때 자동으로 다시 적재합니다.
"""
import contextvars
import json
import math
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal, Optional

import duckdb
from dashboard_core import Dashboard
from registry import tool

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
USER = contextvars.ContextVar("user", default="")        # 지금 요청한 로그인 사용자 ID
MAX_ROWS = int(os.environ.get("MAX_ROWS", "200"))
TIMEOUT_S = int(os.environ.get("QUERY_TIMEOUT_MS", "10000")) / 1000
PYEONG = 3.3058
PROVISIONAL_MONTHS = 2  # 최근 2개월은 신고 지연으로 잠정 → 비교 구간에서 제외

# ---------------------------------------------------------------- 유틸
def _dump(o) -> str:
    return json.dumps(o, ensure_ascii=False, default=str)


def _haversine(lat1, lng1, lat2, lng2) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _norm_jibun(j) -> str:
    return re.sub(r"(번지|\s)", "", str(j or ""))


def _norm(s) -> str:
    return re.sub(r"[\s()\[\],·.\-]", "", str(s or "")).lower()


def _r(x, n=2):
    return None if x is None else round(float(x), n)


def _bulk(con, table, rows):
    """파이썬 행 목록을 임시 NDJSON 으로 써서 DuckDB 가 한 번에 읽게 함 (executemany 보다 수십 배 빠름)."""
    if not rows:
        return
    schema = con.execute(f"DESCRIBE {table}").fetchall()
    names = [c[0] for c in schema]
    cols = "{" + ", ".join(f"'{c[0]}': '{c[1]}'" for c in schema) + "}"
    with tempfile.NamedTemporaryFile("w", suffix=".ndjson", delete=False, encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(dict(zip(names, r)), ensure_ascii=False, default=str) + "\n")
        path = f.name
    try:
        con.execute(f"INSERT INTO {table} SELECT * FROM read_json('{path}', format='newline_delimited', columns={cols})")
    finally:
        os.unlink(path)


_READ_ONLY = re.compile(r"^\s*(select|with)\b", re.I)

# ---------------------------------------------------------------- 적재
class Store:
    def __init__(self, user_dir: Path):
        self.user_dir = user_dir
        self.lock = threading.Lock()
        self.sig = None
        self.con = None
        self.meta = {}

    def _files(self):
        db = DATA_DIR / "bundang.db"
        navers = sorted(self.user_dir.glob("naver_*.json"))
        naver = navers[-1] if navers else None
        pub = [DATA_DIR / "public" / "stations.json", DATA_DIR / "public" / "schools.json"]
        return db, naver, pub

    def _signature(self):
        db, naver, pub = self._files()
        sig = []
        for p in [db, naver, *pub]:
            if p and p.exists():
                st = p.stat()
                sig.append((str(p), st.st_mtime, st.st_size))
        return tuple(sig)

    def _rebuild(self, sig):
        db, naver, pub = self._files()
        con = duckdb.connect(":memory:")
        con.execute("LOAD sqlite")

        # 1) SQLite 원본 복사
        con.execute(f"ATTACH '{str(db).replace(chr(39), chr(39) * 2)}' AS src (TYPE sqlite, READ_ONLY)")
        for t in ("trades", "complexes", "rone", "ecos"):
            con.execute(f"CREATE TABLE raw_{t} AS SELECT * FROM src.{t}")
        con.execute("DETACH src")

        # 2) 실거래 정리: 해제 거래 제외, 단위 컬럼 추가
        con.execute(f"""
        CREATE TABLE trades AS
        SELECT row_number() OVER (ORDER BY deal_date, apt_seq) AS tid, apt_seq, apt_nm, umd_nm AS dong, jibun,
               CAST(deal_date AS DATE) AS deal_date, ym,
               exclu_ar AS area_m2, round(exclu_ar / {PYEONG}, 1) AS area_py,
               CASE WHEN exclu_ar <= 60 THEN '~60㎡' WHEN exclu_ar <= 85 THEN '60~85㎡'
                    WHEN exclu_ar <= 135 THEN '85~135㎡' ELSE '135㎡~' END AS size_class,
               amount AS price_man, round(amount / 10000.0, 2) AS price_eok,
               round(amount / (exclu_ar / {PYEONG})) AS ppy_man,
               floor, build_year, NULLIF(dealing_gbn, '') AS deal_type
        FROM raw_trades
        WHERE coalesce(cdeal_type, '') = ''
        """)

        # 3) 역·학교
        stations, schools = [], []
        if pub[0].exists():
            stations = json.loads(pub[0].read_text(encoding="utf-8"))
        if pub[1].exists():
            schools = json.loads(pub[1].read_text(encoding="utf-8"))
        con.execute("CREATE TABLE stations(name VARCHAR, lat DOUBLE, lng DOUBLE)")
        if stations:
            _bulk(con, "stations", [(s["name"], s["lat"], s["lng"]) for s in stations])
        con.execute("CREATE TABLE schools(name VARCHAR, level VARCHAR, lat DOUBLE, lng DOUBLE)")
        if schools:
            _bulk(con, "schools", [(s["name"], s["level"], s["lat"], s["lng"]) for s in schools])

        # 4) 실거래 단지 목록
        tc = con.execute("""
            SELECT t.apt_seq, arg_max(t.apt_nm, t.deal_date), arg_max(t.dong, t.deal_date),
                   arg_max(t.jibun, t.deal_date), max(t.build_year), count(*), max(t.deal_date),
                   any_value(c.lat), any_value(c.lng)
            FROM trades t LEFT JOIN raw_complexes c USING (apt_seq)
            GROUP BY t.apt_seq
        """).fetchall()
        cx = {r[0]: dict(apt_seq=r[0], name=r[1], dong=r[2], jibun=r[3], build_year=r[4],
                         trade_count=r[5], last_deal=r[6], lat=r[7], lng=r[8],
                         naver_names=[], households=None, naver_nos=[]) for r in tc}
        by_jibun = {(v["dong"], _norm_jibun(v["jibun"])): k for k, v in cx.items()}

        # 5) 네이버: 단지/평형/매물 펼치기 + 실거래 단지 매칭(동+지번 → 좌표 근접)
        naver_date = None
        n_cx, n_py, n_art = [], [], []
        if naver:
            nv = json.loads(naver.read_text(encoding="utf-8"))
            naver_date = nv.get("date") or naver.stem.replace("naver_", "")
            for c in nv.get("complexes") or []:
                key = (c.get("dong"), _norm_jibun(c.get("jibun")))
                seq, how = by_jibun.get(key), "지번"
                if not seq and c.get("lat"):
                    best = None
                    for k, v in cx.items():
                        if v["lat"] and v["dong"] == c.get("dong"):
                            d = _haversine(c["lat"], c["lng"], v["lat"], v["lng"])
                            if d < 150 and (best is None or d < best[0]):
                                best = (d, k)
                    if best:
                        seq, how = best[1], "좌표"
                if seq:
                    v = cx[seq]
                    v["naver_names"].append(c.get("name"))
                    v["naver_nos"].append(c.get("complexNumber"))
                    v["households"] = (v["households"] or 0) + (c.get("households") or 0)
                    if not v["lat"]:
                        v["lat"], v["lng"] = c.get("lat"), c.get("lng")
                    if not v["build_year"] and c.get("approval"):
                        v["build_year"] = int(str(c["approval"])[:4])
                n_cx.append((c.get("complexNumber"), c.get("name"), c.get("dong"), c.get("jibun"),
                             c.get("road"), c.get("lat"), c.get("lng"), c.get("households"),
                             c.get("buildings"), str(c.get("approval") or ""), seq, how if seq else None))
                for p in c.get("pyeongs") or []:
                    n_py.append((c.get("complexNumber"), p.get("name"), p.get("supply"),
                                 p.get("exclusive"), p.get("households"), p.get("rooms"), p.get("baths")))
                for a in c.get("articles") or []:
                    fl = str(a.get("floor") or "")
                    f, _, tot = fl.partition("/")
                    price = a.get("price")
                    n_art.append((a.get("articleNumber"), c.get("complexNumber"), c.get("name"), seq,
                                  c.get("dong"), a.get("dong"), f or None, tot or None,
                                  a.get("exclusive"), a.get("supply"),
                                  round(price / 10000) if price else None,
                                  round(price / 1e8, 2) if price else None,
                                  a.get("exposure"), a.get("realtors")))

        con.execute("""CREATE TABLE naver_complexes(naver_no BIGINT, name VARCHAR, dong VARCHAR, jibun VARCHAR,
            road VARCHAR, lat DOUBLE, lng DOUBLE, households INT, buildings INT, approval VARCHAR,
            apt_seq VARCHAR, match_by VARCHAR)""")
        con.execute("""CREATE TABLE naver_pyeongs(naver_no BIGINT, type_name VARCHAR, supply_m2 DOUBLE,
            area_m2 DOUBLE, households INT, rooms INT, baths INT)""")
        con.execute("""CREATE TABLE listings(article_no VARCHAR, naver_no BIGINT, naver_name VARCHAR,
            apt_seq VARCHAR, dong VARCHAR, building VARCHAR, floor VARCHAR, total_floor VARCHAR,
            area_m2 DOUBLE, supply_m2 DOUBLE, ask_man BIGINT, ask_eok DOUBLE, exposed VARCHAR, realtors INT)""")
        if n_cx:
            _bulk(con, "naver_complexes", n_cx)
        if n_py:
            _bulk(con, "naver_pyeongs", n_py)
        if n_art:
            _bulk(con, "listings", n_art)

        # 6) 단지 차원 테이블 (역·초등학교 거리 포함)
        elem = [s for s in schools if s.get("level") == "초등학교"]
        rows = []
        for v in cx.values():
            st_name = st_m = el_name = el_m = None
            if v["lat"]:
                if stations:
                    d, s = min((_haversine(v["lat"], v["lng"], s["lat"], s["lng"]), s) for s in stations)
                    st_name, st_m = s["name"] + "역", round(d)
                if elem:
                    d, s = min((_haversine(v["lat"], v["lng"], s["lat"], s["lng"]), s["name"]) for s in elem)
                    el_name, el_m = s, round(d)
            rows.append((v["apt_seq"], v["name"], v["dong"], v["jibun"], v["build_year"], v["households"],
                         ", ".join(v["naver_names"]) or None, v["trade_count"], v["last_deal"],
                         v["lat"], v["lng"], st_name, st_m, el_name, el_m,
                         _norm(v["name"]) + "|" + _norm(" ".join(v["naver_names"]))))
        con.execute("""CREATE TABLE complex_info(apt_seq VARCHAR, name VARCHAR, dong VARCHAR, jibun VARCHAR,
            build_year INT, households INT, naver_name VARCHAR, trade_count INT, last_deal DATE,
            lat DOUBLE, lng DOUBLE, nearest_station VARCHAR, station_m INT,
            nearest_elementary VARCHAR, elementary_m INT, search_key VARCHAR)""")
        _bulk(con, "complex_info", rows)

        # 7) 시장 지표 (월별 피벗)
        con.execute("""
        CREATE TABLE market_monthly AS
        WITH r AS (
            SELECT ym, avg(value) FILTER (WHERE name = '매매가격지수') AS price_index,
                   sum(value) FILTER (WHERE name = '아파트매매거래현황' AND unit = '호수') AS rone_deals
            FROM raw_rone GROUP BY ym),
        e AS (
            SELECT ym, any_value(value) FILTER (WHERE series = 'base') AS base_rate,
                   any_value(value) FILTER (WHERE series = 'mortgage') AS mortgage_rate
            FROM raw_ecos GROUP BY ym),
        t AS (SELECT ym, count(*) AS molit_deals FROM trades GROUP BY ym)
        SELECT coalesce(r.ym, e.ym) AS ym, r.price_index, r.rone_deals,
               coalesce(t.molit_deals, 0) AS molit_deals, e.base_rate, e.mortgage_rate
        FROM r FULL JOIN e ON r.ym = e.ym LEFT JOIN t ON t.ym = coalesce(r.ym, e.ym)
        ORDER BY 1
        """)

        meta = con.execute("SELECT min(deal_date), max(deal_date), count(*) FROM trades").fetchone()
        matched = con.execute("SELECT count(*) FILTER (WHERE apt_seq IS NOT NULL), count(*) FROM naver_complexes").fetchone()
        self.meta = {
            "실거래_기간": f"{meta[0]} ~ {meta[1]}", "실거래_건수(해제제외)": meta[2],
            "네이버_기준일": naver_date, "네이버_단지_매칭": f"{matched[0]}/{matched[1]}",
            "적재시각": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        for t in ("raw_trades", "raw_complexes"):
            con.execute(f"DROP TABLE {t}")
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        if self.con is not None:
            self.con.close()
        self.con, self.sig = con, sig

    def query(self, sql, params=None, limit=None):
        with self.lock:
            sig = self._signature()
            if sig != self.sig:
                try:
                    self._rebuild(sig)
                except Exception as e:
                    # 수집기가 쓰는 중이라 읽기에 실패하면, 이전 데이터가 있으면 그대로 사용
                    if self.con is None:
                        raise
                    print(f"[reload skipped] {e}", flush=True)
            timer = threading.Timer(TIMEOUT_S, self.con.interrupt)
            timer.start()
            try:
                cur = self.con.execute(sql, params or [])
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchmany(limit + 1) if limit else cur.fetchall()
            finally:
                timer.cancel()
        return cols, rows

    def dicts(self, sql, params=None, limit=None):
        cols, rows = self.query(sql, params, limit)
        return [dict(zip(cols, r)) for r in rows]


class _PerUser:
    """사용자별 객체를 USER 에 따라 골라 주는 대리자 (store.dicts(...), DASH.lead(...) 처럼 그대로 씀)"""
    def __init__(self, make):
        self._make, self._objs, self._lock = make, {}, threading.Lock()

    def current(self):
        uid = USER.get()
        if not uid:
            raise PermissionError("로그인 사용자를 알 수 없습니다")
        with self._lock:
            if uid not in self._objs:
                self._objs[uid] = self._make(DATA_DIR / "users" / uid)
            return self._objs[uid]

    def __getattr__(self, name):
        return getattr(self.current(), name)


store = _PerUser(Store)

# ---------------------------------------------------------------- 단지 찾기
def _find(keyword: str, dong: Optional[str] = None, limit: int = 10):
    kw = (keyword or "").strip()
    if re.fullmatch(r"\d{5}-\d+", kw):
        return store.dicts("SELECT * FROM complex_info WHERE apt_seq=?", [kw])
    tokens = [_norm(t) for t in kw.split() if _norm(t)]
    if not tokens:
        return []
    where = " AND ".join(["search_key LIKE ?"] * len(tokens))
    params = [f"%{t}%" for t in tokens]
    if dong:
        where += " AND dong = ?"
        params.append(dong)
    return store.dicts(
        f"SELECT * FROM complex_info WHERE {where} ORDER BY trade_count DESC LIMIT {int(limit)}", params)


def _brief(c):
    return {
        "단지코드": c["apt_seq"], "단지명": c["name"], "동": c["dong"], "네이버명": c["naver_name"],
        "준공": c["build_year"], "세대수": c["households"], "누적거래": c["trade_count"],
        "최근거래일": c["last_deal"],
        "최근접역": f'{c["nearest_station"]} {c["station_m"]}m' if c["nearest_station"] else None,
        "최근접초등학교": f'{c["nearest_elementary"]} {c["elementary_m"]}m' if c["nearest_elementary"] else None,
    }


def _resolve(complex_name: str):
    """(단지 dict, None) 또는 (None, 오류/후보 dict)"""
    found = _find(complex_name, limit=8)
    if not found:
        return None, {"error": f"'{complex_name}'에 해당하는 단지를 찾지 못했습니다. find_complex 로 다른 키워드를 시도하세요."}
    exact = [c for c in found if _norm(c["name"]) == _norm(complex_name)]
    if len(found) == 1 or len(exact) == 1:
        return (exact or found)[0], None
    return None, {"ambiguous": True, "message": "여러 단지가 검색됐습니다. 사용자에게 어느 단지인지 확인하거나 단지코드로 다시 호출하세요.",
                  "candidates": [_brief(c) for c in found]}


def _area_filter(area_m2, tol, col="area_m2"):
    if area_m2:
        return f" AND abs({col} - ?) <= ?", [float(area_m2), float(tol)]
    return "", []


def _period_expr(interval):
    return {
        "month": "strftime(deal_date, '%Y-%m')",
        "quarter": "strftime(deal_date, '%Y') || '-Q' || quarter(deal_date)",
        "year": "strftime(deal_date, '%Y')",
    }[interval]


# ---------------------------------------------------------------- 대시보드 필터 · 비교 구간
def _ym_shift(ym: str, k: int) -> str:
    y, m = int(ym[:4]), int(ym[4:])
    t = y * 12 + (m - 1) + k
    return f"{t // 12}{t % 12 + 1:02d}"


def _windows(months: int):
    """대시보드와 같은 규칙: 최근 PROVISIONAL_MONTHS 개월은 잠정이라 제외하고, 확정된 마지막 달까지 N개월 vs 직전 N개월."""
    today = time.strftime("%Y%m")
    data_last = store.dicts("SELECT max(ym) AS m FROM trades")[0]["m"]
    end = min(_ym_shift(today, -(PROVISIONAL_MONTHS + 1)), data_last)
    m = int(months)
    rs, ps, pe = _ym_shift(end, -(m - 1)), _ym_shift(end, -(2 * m - 1)), _ym_shift(end, -m)
    fmt = lambda x: f"{x[:4]}-{x[4:]}"
    return {"recent": (rs, end), "prev": (ps, pe), "provisional_from": _ym_shift(end, 1),
            "label": f"최근 {m}개월({fmt(rs)}~{fmt(end)}) vs 직전 {m}개월({fmt(ps)}~{fmt(pe)}), "
                     f"{fmt(_ym_shift(end, 1))} 이후는 신고 지연으로 잠정 제외"}


LAG_NOTE = ("최근 1~2개월은 실거래 신고 기한(계약 후 30일) 때문에 거래 건수가 실제보다 적을 수 있습니다. "
            "첫·마지막 구간은 조회 기간 경계 때문에 일부 월만 포함될 수 있습니다.")

# ---------------------------------------------------------------- 도구

# ---------------------------------------------------------------- 브리핑 화면과 같은 계산
DASH = _PerUser(lambda d: Dashboard(d / "briefing_payload.json"))
DEFAULT_SCREEN = {"dong": "전체", "scope": "filt",
                  "filter": {"areaMin": 24, "areaMax": 40, "priceMin": 0, "priceMax": 15, "rooms": [],
                             "hhMin": None, "hhMax": None, "ageMin": None}}


def _screen(screen, dong=None, scope=None):
    sc = {**DEFAULT_SCREEN, **(screen or {})}
    f = {**DEFAULT_SCREEN["filter"], **(sc.get("filter") or {})}
    f["rooms"] = [int(r) for r in (f.get("rooms") or [])]
    for k in ("hhMin", "hhMax", "ageMin"):                    # 빈칸·없음 = 제한 없음 (년차는 이상만)
        f[k] = float(f[k]) if f.get(k) not in (None, "") else None
    d = dong or sc.get("dong") or "전체"
    if d in ("5개 동", "5개동", "all"):
        d = "전체"
    return d, scope or sc.get("scope") or "filt", f, sc.get("cfg")


def _dash_guard():
    if not DASH.path.exists():
        raise FileNotFoundError("briefing_payload.json 이 없습니다. 브리핑을 한 번 다시 생성하세요 (build_briefing.py).")


SCREEN_DOC = "screen: 앱이 자동으로 채우는 현재 화면 상태 (지역·보기·필터·접근성 설정). 직접 넣지 마세요."


@tool
def screen_summary(dong: Optional[str] = None, screen: Optional[dict] = None) -> str:
    """브리핑 화면 상단 요약과 똑같은 숫자: 전체 거래·조건 거래(최근 3개월 건수, 직전 3개월 대비 건수 변동률),
    매물(전체·조건·실매매율·호가 괴리), 입지 유형별 단지 수. '여기', '이 조건', 화면 수치를 물으면 먼저 호출.

    Args:
        dong: 다른 동을 보고 싶을 때만 (예: "야탑동", "전체"). 생략하면 화면에서 선택한 지역
        screen: 앱이 자동으로 채움
    """
    try:
        _dash_guard()
        d, sc, f, cfg = _screen(screen, dong)
        return _dump(DASH.lead(d, sc, f, cfg))
    except Exception as e:
        return _dump({"error": str(e)[:400]})


@tool
def screen_price_change(dong: Optional[str] = None, months: int = 12,
                        scope: Optional[Literal["filt", "area", "all"]] = None, screen: Optional[dict] = None) -> str:
    """화면과 같은 조건(지역·필터·보기)으로 가격(공급 평당가 중앙값)과 거래 건수가 어떻게 변했는지:
    최근 N개월 vs 직전 N개월, 같은 단지 기준 변동, 상승단지 비율, 상승 상위·하위 단지, 월별 경로.
    "작년 대비" → months=12. 최근 36개월 데이터만 있어 months 는 최대 17.

    Args:
        dong: 다른 동을 볼 때만. 생략하면 화면의 지역
        months: 비교 기간 (기본 12, 최대 17)
        scope: 보기 범위를 바꿀 때만 (filt=필터 조건, area=평형·방만, all=전체)
        screen: 앱이 자동으로 채움
    """
    try:
        _dash_guard()
        d, sc, f, cfg = _screen(screen, dong, scope)
        return _dump(DASH.price_change(d, sc, f, max(1, min(int(months), 17)), cfg))
    except Exception as e:
        return _dump({"error": str(e)[:400]})


@tool
def screen_unit(name: Optional[str] = None, screen: Optional[dict] = None) -> str:
    """단지 하나의 화면 지표: 접근점수·등급·가까운 시설, 필터 조건/전체 매물 수, 평당 호가, 최근 6개월 평당 실거래가,
    호가 괴리, 호가 범위, 최근 6개월 vs 직전 6개월 거래 건수.

    Args:
        name: 단지명 일부. 생략하면 화면에서 열어 둔 단지
        screen: 앱이 자동으로 채움
    """
    try:
        _dash_guard()
        _, _, f, cfg = _screen(screen)
        target = name or (screen or {}).get("unit")
        if not target:
            return _dump({"error": "단지 이름이 필요합니다. 화면에서 단지를 열거나 이름을 알려주세요."})
        return _dump(DASH.unit_detail(target, f, cfg))
    except Exception as e:
        return _dump({"error": str(e)[:400]})


@tool
def find_complex(keyword: str, dong: Optional[str] = None) -> str:
    """단지 이름 일부로 분당구 아파트 단지를 찾습니다. 띄어쓰기로 여러 단어를 주면 모두 포함된 단지만 찾습니다.
    결과의 단지코드를 다른 도구에 넘기면 정확히 지정됩니다.

    Args:
        keyword: 단지명 일부 (예: "파크뷰", "양지 금호", "시범 한신")
        dong: 동 이름으로 좁히기 (예: "정자동"), 선택
    """
    try:
        rows = _find(keyword, dong)
        if not rows:
            return _dump({"result": [], "message": "검색 결과가 없습니다. 더 짧은 키워드로 다시 시도하세요."})
        return _dump({"result": [_brief(c) for c in rows]})
    except Exception as e:
        return _dump({"error": str(e)[:400]})


@tool
def price_trend(complex_name: str, area_m2: Optional[float] = None, months: int = 36,
                interval: Literal["month", "quarter", "year"] = "quarter", area_tolerance: float = 3) -> str:
    """한 단지의 실거래가 추이(기간별 건수·중위가·평균·최저·최고·평당가)와 최근 거래 5건을 반환합니다.

    Args:
        complex_name: 단지명 또는 단지코드 (예: "파크뷰", "41135-250")
        area_m2: 전용면적(㎡). 지정하면 ±area_tolerance 범위만 집계 (예: 84.9). 생략하면 전 평형
        months: 최근 몇 개월을 볼지 (기본 36)
        interval: 집계 단위 month / quarter / year (기본 quarter)
        area_tolerance: 면적 허용 오차 ㎡ (기본 3)
    """
    try:
        c, err = _resolve(complex_name)
        if err:
            return _dump(err)
        af, ap = _area_filter(area_m2, area_tolerance)
        per = _period_expr(interval)
        base = f"""FROM trades WHERE apt_seq = ?{af}
                    AND deal_date >= (SELECT max(deal_date) FROM trades) - INTERVAL {int(months)} MONTH"""
        params = [c["apt_seq"], *ap]
        trend = store.dicts(f"""
            SELECT {per} AS 기간, count(*) AS 건수,
                   round(median(price_eok), 2) AS 중위가_억, round(avg(price_eok), 2) AS 평균가_억,
                   min(price_eok) AS 최저_억, max(price_eok) AS 최고_억,
                   CAST(median(ppy_man) AS INT) AS 중위_평당가_만원
            {base} GROUP BY 1 ORDER BY 1""", params)
        recent = store.dicts(f"""
            SELECT deal_date AS 계약일, area_m2 AS 전용㎡, floor AS 층, price_eok AS 거래가_억, deal_type AS 거래유형
            {base} ORDER BY deal_date DESC LIMIT 5""", params)
        sizes = store.dicts(f"""
            SELECT round(area_m2, 1) AS 전용㎡, count(*) AS 건수 {base.replace(af, '')}
            GROUP BY 1 ORDER BY 2 DESC LIMIT 6""", [c["apt_seq"]])
        out = {"단지": _brief(c),
               "조건": {"전용면적": f"{area_m2}㎡ ±{area_tolerance}" if area_m2 else "전 평형 (평당가 기준으로 해석 권장)",
                        "기간": f"최근 {months}개월", "집계단위": interval},
               "추이": trend, "최근거래": recent}
        if not area_m2:
            out["기간내_면적별_거래건수"] = sizes
        if len(trend) >= 2:
            a, b = trend[0], trend[-1]
            key = "중위가_억" if area_m2 else "중위_평당가_만원"
            if a[key] and b[key]:
                out["요약"] = {"비교": f"{a['기간']} → {b['기간']}", "지표": key,
                             "처음": a[key], "마지막": b[key],
                             "변동률_%": round((b[key] - a[key]) / a[key] * 100, 1)}
        if not trend:
            out["message"] = "조건에 맞는 거래가 없습니다. 면적이나 기간을 넓혀보세요."
        out["주의"] = LAG_NOTE
        return _dump(out)
    except Exception as e:
        return _dump({"error": str(e)[:400]})


@tool
def region_trend(dong: Optional[str] = None, months: int = 60,
                 interval: Literal["month", "quarter", "year"] = "quarter") -> str:
    """분당구 13개 동 어디든, 최대 10년의 실거래 추이(거래건수, 중위 평당가, 중위가). 화면 필터는 적용하지 않습니다
    (화면 조건 기준 분석은 screen_price_change).

    Args:
        dong: 동 이름 (예: "정자동"). 생략하면 분당구 전체
        months: 최근 몇 개월 (기본 60)
        interval: month / quarter / year (기본 quarter)
    """
    try:
        per = _period_expr(interval)
        where, params = ("t.dong = ?", [dong]) if dong else ("TRUE", [])
        desc = dong or "분당구 전체"
        trend = store.dicts(f"""
            SELECT {per} AS 기간, count(*) AS 거래건수,
                   CAST(median(ppy_man) AS INT) AS 중위_평당가_만원,
                   round(median(price_eok), 2) AS 중위가_억,
                   round(median(price_eok) FILTER (WHERE area_m2 BETWEEN 80 AND 86), 2) AS 전용84급_중위가_억
            FROM trades t
            WHERE deal_date >= (SELECT max(deal_date) FROM trades) - INTERVAL {int(months)} MONTH AND {where}
            GROUP BY 1 ORDER BY 1""", params)
        if not trend:
            dongs = [r["dong"] for r in store.dicts("SELECT DISTINCT dong FROM trades ORDER BY 1")]
            return _dump({"error": "조건에 맞는 거래가 없습니다.", "조건": desc, "가능한_동": dongs})
        out = {"조건": desc, "기간": f"최근 {months}개월", "추이": trend}
        if len(trend) >= 2 and trend[0]["중위_평당가_만원"] and trend[-1]["중위_평당가_만원"]:
            a, b = trend[0], trend[-1]
            out["요약"] = {"비교": f"{a['기간']} → {b['기간']}", "평당가_변동률_%":
                         round((b["중위_평당가_만원"] - a["중위_평당가_만원"]) / a["중위_평당가_만원"] * 100, 1)}
        out["주의"] = LAG_NOTE
        return _dump(out)
    except Exception as e:
        return _dump({"error": str(e)[:400]})

@tool
def market_indicators(months: int = 36, interval: Literal["month", "quarter", "year"] = "quarter") -> str:
    """분당구 시장 배경 지표: 부동산원 매매가격지수, 거래량(부동산원·국토부), 기준금리, 주택담보대출 금리.

    Args:
        months: 최근 몇 개월 (기본 36)
        interval: month / quarter / year (기본 quarter)
    """
    try:
        per = {"month": "substr(ym,1,4)||'-'||substr(ym,5,2)",
               "quarter": "substr(ym,1,4)||'-Q'||CAST(ceil(CAST(substr(ym,5,2) AS INT)/3.0) AS INT)",
               "year": "substr(ym,1,4)"}[interval]
        rows = store.dicts(f"""
            WITH last AS (SELECT max(ym) AS ym FROM market_monthly WHERE price_index IS NOT NULL),
            r AS (SELECT * FROM market_monthly
                  WHERE strptime(ym, '%Y%m') > strptime((SELECT ym FROM last), '%Y%m') - INTERVAL {int(months)} MONTH)
            SELECT {per} AS 기간,
                   round(arg_max(price_index, ym) FILTER (WHERE price_index IS NOT NULL), 2) AS 매매가격지수_기말,
                   CAST(sum(rone_deals) AS INT) AS 부동산원_거래호수,
                   sum(molit_deals) AS 국토부_실거래건수,
                   round(arg_max(base_rate, ym) FILTER (WHERE base_rate IS NOT NULL), 2) AS 기준금리_기말,
                   round(avg(mortgage_rate), 2) AS 주담대금리_평균
            FROM r GROUP BY 1 ORDER BY 1""")
        return _dump({"지역": "성남시 분당구", "기간": f"최근 {months}개월", "지표": rows,
                      "설명": "매매가격지수는 기준시점=100 지수. 금리는 %. 국토부 실거래건수는 해제거래 제외."})
    except Exception as e:
        return _dump({"error": str(e)[:400]})


@tool
def listings(complex_name: str, area_m2: Optional[float] = None, area_tolerance: float = 3) -> str:
    """네이버 현재 매물 호가와 최근 6개월 실거래가를 비교합니다 (수내·정자·서현·이매·야탑동만 매물 데이터 있음).

    Args:
        complex_name: 단지명 또는 단지코드
        area_m2: 전용면적(㎡), 선택
        area_tolerance: 면적 허용 오차 ㎡ (기본 3)
    """
    try:
        c, err = _resolve(complex_name)
        if err:
            return _dump(err)
        af, ap = _area_filter(area_m2, area_tolerance)
        p = [c["apt_seq"], *ap]
        ask = store.dicts(f"""
            SELECT round(area_m2, 1) AS 전용㎡, count(*) AS 매물수, min(ask_eok) AS 최저호가_억,
                   round(median(ask_eok), 2) AS 중위호가_억, max(ask_eok) AS 최고호가_억
            FROM listings WHERE apt_seq = ?{af} GROUP BY 1 ORDER BY 1""", p)
        deals = store.dicts(f"""
            SELECT round(area_m2, 1) AS 전용㎡, count(*) AS 거래건수, round(median(price_eok), 2) AS 중위거래가_억,
                   max(price_eok) AS 최고거래가_억, max(deal_date) AS 최근거래일
            FROM trades WHERE apt_seq = ?{af}
              AND deal_date >= (SELECT max(deal_date) FROM trades) - INTERVAL 6 MONTH
            GROUP BY 1 ORDER BY 1""", p)
        dmap = {d["전용㎡"]: d for d in deals}
        for a in ask:
            d = min(dmap.values(), key=lambda x: abs(x["전용㎡"] - a["전용㎡"]), default=None)
            if d and abs(d["전용㎡"] - a["전용㎡"]) <= area_tolerance and d["중위거래가_억"]:
                a["비교_실거래중위_억"] = d["중위거래가_억"]
                a["호가_실거래_차이_%"] = round((a["중위호가_억"] - d["중위거래가_억"]) / d["중위거래가_억"] * 100, 1)
        items = store.dicts(f"""
            SELECT building AS 동, floor || '/' || total_floor AS 층, area_m2 AS 전용㎡, ask_eok AS 호가_억, exposed AS 노출일
            FROM listings WHERE apt_seq = ?{af} ORDER BY ask_eok LIMIT 10""", p)
        out = {"단지": _brief(c), "네이버_기준일": store.meta.get("네이버_기준일"),
               "호가_요약": ask, "최근6개월_실거래": deals, "매물_예시(낮은가격순)": items}
        if not ask:
            total = store.dicts("SELECT count(*) AS n FROM listings WHERE apt_seq = ?", [c["apt_seq"]])[0]["n"]
            out["message"] = (f"이 면적의 매물은 없습니다 (단지 전체 매물 {total}건). area_m2 를 빼고 다시 조회해보세요."
                              if total and area_m2 else "이 단지는 네이버 매물 데이터가 없거나 매칭되지 않았습니다.")
        return _dump(out)
    except Exception as e:
        return _dump({"error": str(e)[:400]})


@tool
def compare_complexes(complex_names: list[str], area_m2: Optional[float] = None, months: int = 12) -> str:
    """여러 단지(2~5개)를 최근 실거래가, 1년 전 대비 변동, 호가, 세대수, 준공, 역 거리로 비교합니다.

    Args:
        complex_names: 단지명 또는 단지코드 목록 (예: ["파크뷰", "느티마을"])
        area_m2: 전용면적(㎡), 선택. 생략하면 평당가 위주 비교
        months: 최근 비교 기간 개월 (기본 12). 같은 길이의 직전 기간과 비교
    """
    try:
        out, issues = [], []
        for name in complex_names[:5]:
            c, err = _resolve(name)
            if err:
                issues.append({"입력": name, **err})
                continue
            af, ap = _area_filter(area_m2, 3)
            m = int(months)
            r = store.dicts(f"""
                WITH mx AS (SELECT max(deal_date) d FROM trades)
                SELECT count(*) FILTER (WHERE deal_date > (SELECT d FROM mx) - INTERVAL {m} MONTH) AS 최근_건수,
                       round(median(price_eok) FILTER (WHERE deal_date > (SELECT d FROM mx) - INTERVAL {m} MONTH), 2) AS 최근_중위가_억,
                       CAST(median(ppy_man) FILTER (WHERE deal_date > (SELECT d FROM mx) - INTERVAL {m} MONTH) AS INT) AS 최근_중위평당가_만원,
                       CAST(median(ppy_man) FILTER (WHERE deal_date <= (SELECT d FROM mx) - INTERVAL {m} MONTH
                                                     AND deal_date > (SELECT d FROM mx) - INTERVAL {2 * m} MONTH) AS INT) AS 직전기간_중위평당가_만원
                FROM trades WHERE apt_seq = ?{af}""", [c["apt_seq"], *ap])[0]
            if r["최근_중위평당가_만원"] and r["직전기간_중위평당가_만원"]:
                r["평당가_변동률_%"] = round((r["최근_중위평당가_만원"] - r["직전기간_중위평당가_만원"])
                                        / r["직전기간_중위평당가_만원"] * 100, 1)
            a = store.dicts(f"SELECT count(*) AS 매물수, round(median(ask_eok),2) AS 중위호가_억 FROM listings WHERE apt_seq = ?{af}",
                            [c["apt_seq"], *ap])[0]
            out.append({**_brief(c), **r, **a})
        res = {"조건": {"전용면적": f"{area_m2}㎡ ±3" if area_m2 else "전 평형", "기간": f"최근 {months}개월 vs 직전 {months}개월"},
               "비교": out}
        if issues:
            res["확인필요"] = issues
        return _dump(res)
    except Exception as e:
        return _dump({"error": str(e)[:400]})


@tool
def explain_change(dong: Optional[str] = None, months: int = 12, area_m2: Optional[float] = None) -> str:
    """'왜 올랐나/내렸나'의 데이터 근거 (전 평형·10년 DB 기준, 화면 필터 무관). 확정된 최근 N개월과 직전 N개월 비교:
    전체 변동, 같은 단지 기준 변동(광범위한 상승인지), 거래 구성 효과, 준공연대별, 상승 상위 단지,
    월별 경로와 상승이 가속된 시점(→ 이 시기로 search_news 검색 권장), 분당구 전체 대비.
    "작년 대비"는 months=12. 화면 조건 그대로의 변화는 screen_price_change 를 쓰세요.

    Args:
        dong: 동 이름 (예: "야탑동"). 생략하면 분당구 전체
        months: 비교 기간 개월 (기본 12)
        area_m2: 전용면적(㎡) ±3 필터, 선택
    """
    try:
        w = _windows(months)
        (rs, re_), (ps, pe) = w["recent"], w["prev"]
        fw, fp = ("t.dong = ?", [dong]) if dong else ("TRUE", [])
        desc = dong or "분당구 전체"
        af, ap = _area_filter(area_m2, 3)
        base = f"""WITH w AS (SELECT *, CASE WHEN ym BETWEEN '{rs}' AND '{re_}' THEN 'B'
                                          WHEN ym BETWEEN '{ps}' AND '{pe}' THEN 'A' END AS p
                             FROM trades t WHERE ym BETWEEN '{ps}' AND '{re_}' AND {fw}{af})"""
        params = [*fp, *ap]
        pct = lambda a, b: round((b - a) / a * 100, 1) if a else None

        tot = {r["p"]: r for r in store.dicts(f"{base} SELECT p, count(*) n, median(ppy_man) ppy, median(price_eok) pr FROM w GROUP BY p", params)}
        if "A" not in tot or "B" not in tot:
            return _dump({"error": "비교할 거래가 부족합니다. 기간을 늘리거나 조건을 넓히세요.", "조건": desc, "구간": w["label"]})
        overall = {"직전_건수": tot["A"]["n"], "최근_건수": tot["B"]["n"], "거래량_변동률_%": pct(tot["A"]["n"], tot["B"]["n"]),
                   "직전_중위평당가_만원": int(tot["A"]["ppy"]), "최근_중위평당가_만원": int(tot["B"]["ppy"]),
                   "평당가_변동률_%": pct(tot["A"]["ppy"], tot["B"]["ppy"]),
                   "직전_중위가_억": _r(tot["A"]["pr"]), "최근_중위가_억": _r(tot["B"]["pr"])}

        cx = store.dicts(f"""{base}
            SELECT apt_nm AS 단지명, count(*) FILTER (WHERE p='A') AS 직전_건수, count(*) FILTER (WHERE p='B') AS 최근_건수,
                   CAST(median(ppy_man) FILTER (WHERE p='A') AS INT) AS 직전_평당가,
                   CAST(median(ppy_man) FILTER (WHERE p='B') AS INT) AS 최근_평당가, max(build_year) AS 준공
            FROM w GROUP BY apt_nm
            HAVING count(*) FILTER (WHERE p='A') >= 2 AND count(*) FILTER (WHERE p='B') >= 2""", params)
        for c in cx:
            c["변동률_%"] = pct(c["직전_평당가"], c["최근_평당가"])
        if cx:
            wsum = sum(c["최근_건수"] for c in cx)
            same = round(sum(c["변동률_%"] * c["최근_건수"] for c in cx) / wsum, 1)
            overall["동일단지_가중평균_변동률_%"] = same
            overall["구성효과_추정_%p"] = round(overall["평당가_변동률_%"] - same, 1)
            overall["상승단지_비율"] = f"{sum(c['변동률_%'] > 0 for c in cx)}/{len(cx)}"
            overall["20%이상_상승단지"] = sum(c["변동률_%"] >= 20 for c in cx)
        cx.sort(key=lambda c: c["변동률_%"], reverse=True)

        era = store.dicts(f"""{base}
            SELECT CASE WHEN build_year <= 1995 THEN '1995년 이전(1기 신도시)' WHEN build_year <= 2009 THEN '1996~2009'
                        ELSE '2010년 이후' END AS 준공연대,
                   count(*) FILTER (WHERE p='B') AS 최근_건수,
                   CAST(median(ppy_man) FILTER (WHERE p='A') AS INT) AS 직전_평당가,
                   CAST(median(ppy_man) FILTER (WHERE p='B') AS INT) AS 최근_평당가
            FROM w GROUP BY 1 ORDER BY 1""", params)
        for e in era:
            e["변동률_%"] = pct(e["직전_평당가"], e["최근_평당가"])

        path = store.dicts(f"""
            SELECT strftime(deal_date, '%Y-%m') AS 월, count(*) AS 건수, CAST(median(ppy_man) AS INT) AS 중위평당가,
                   ym >= '{w["provisional_from"]}' AS 잠정
            FROM trades t WHERE ym >= '{ps}' AND {fw}{af} GROUP BY 1, 4 ORDER BY 1""", params)
        conf = [x for x in path if not x["잠정"]]
        accel = None
        for i in range(5, len(conf)):
            prev = sorted(x["중위평당가"] for x in conf[i - 5:i - 2])[1]
            cur = sorted(x["중위평당가"] for x in conf[i - 2:i + 1])[1]
            jump = pct(prev, cur)
            if jump is not None and (accel is None or jump > accel["3개월_중위_상승률_%"]):
                accel = {"시점": conf[i - 2]["월"], "3개월_중위_상승률_%": jump}

        out = {"조건": desc, "구간": w["label"], "전체": overall, "준공연대별": era,
               "상승_상위단지": cx[:6], "상승_하위단지": cx[-3:] if len(cx) > 6 else [],
               "월별_경로": path, "상승_가속시점": accel}
        b = {r["p"]: r["ppy"] for r in store.dicts(f"{base.replace(' AND ' + fw + af, '')} SELECT p, median(ppy_man) ppy FROM w GROUP BY p")}
        out["분당구_전체_평당가_변동률_%"] = pct(b.get("A"), b.get("B"))
        if accel:
            y, mo = map(int, accel["시점"].split("-"))
            s_ym, e_ym = _ym_shift(f"{y}{mo:02d}", -3), _ym_shift(f"{y}{mo:02d}", 1)
            place = dong or "분당"
            out["뉴스검색_추천"] = {"start_date": f"{s_ym[:4]}-{s_ym[4:]}", "end_date": f"{e_ym[:4]}-{e_ym[4:]}",
                                 "검색어_예": [f"{str(place).replace('동', '')} 재건축", f"{place} 아파트", "분당 집값", "분당 정비사업"]}
        out["해석_가이드"] = ("동일단지 변동률이 전체와 비슷하면 실제 가격 상승, 전체가 훨씬 크면 비싼 단지 거래가 늘어난 구성 효과. "
                         "상승단지 비율이 높으면 지역 전반 요인(금리·정책), 일부 단지만 크게 오르면 개별 호재(재건축 등). "
                         "거래량_변동률은 거래 건수 변화이고 평당가_변동률은 가격 변화이니 구분해서 말할 것. "
                         "이 도구의 평당가는 전용면적 기준이라 화면(공급평 기준) 평당가와 수치가 다름.")
        return _dump(out)
    except Exception as e:
        return _dump({"error": str(e)[:400]})


@tool
def describe_data() -> str:
    """run_sql 에서 쓸 수 있는 테이블·컬럼 설명과 데이터 범위를 반환합니다."""
    store.query("SELECT 1")
    return _dump({
        "범위": store.meta,
        "테이블": {
            "trades": "실거래(해제 제외). apt_seq 단지코드, apt_nm, dong, deal_date DATE, ym 'YYYYMM', area_m2 전용㎡, area_py 전용평, "
                      "size_class, price_man 만원, price_eok 억, ppy_man 전용평당가 만원, floor, build_year, deal_type",
            "complex_info": "단지 정보. apt_seq, name, dong, build_year, households 세대수, naver_name, trade_count, last_deal, "
                            "nearest_station, station_m 미터, nearest_elementary, elementary_m",
            "listings": "네이버 현재 매물. apt_seq, naver_name, dong, building 동, floor, area_m2, ask_man 만원, ask_eok 억, exposed",
            "naver_pyeongs": "네이버 평형 정보. naver_no, type_name, supply_m2, area_m2, households, rooms, baths",
            "naver_complexes": "네이버 단지 원본. naver_no, name, dong, households, approval, apt_seq(매칭), match_by",
            "market_monthly": "월별 지표. ym, price_index 매매가격지수, rone_deals, molit_deals, base_rate %, mortgage_rate %",
            "stations / schools": "역·학교 이름과 좌표",
        },
        "주의": ["실거래 금액은 만원, 호가는 ask_man(만원)/ask_eok(억)로 이미 단위 통일됨",
               "네이버 매물은 수내·정자·서현·이매·야탑동만 있음", LAG_NOTE],
    })


@tool
def run_sql(sql: str, max_rows: int = 50) -> str:
    """다른 도구로 안 되는 질문에만 쓰는 읽기 전용 SQL(DuckDB 문법). 먼저 describe_data 를 확인하세요.

    Args:
        sql: SELECT 또는 WITH 로 시작하는 한 문장
        max_rows: 최대 반환 행 수 (기본 50)
    """
    try:
        s = sql.strip().rstrip(";").strip()
        if not _READ_ONLY.match(s) or ";" in s:
            return _dump({"error": "SELECT/WITH 로 시작하는 한 문장만 허용됩니다."})
        limit = max(1, min(int(max_rows), MAX_ROWS))
        cols, rows = store.query(s, limit=limit)
        return _dump({"columns": cols, "rows": [list(r) for r in rows[:limit]],
                      "truncated": len(rows) > limit})
    except Exception as e:
        return _dump({"error": str(e)[:500], "hint": "describe_data 로 테이블·컬럼명을 확인하세요."})
