"""
공공데이터 수집 (Oracle 서버용, 하루 1회 cron 권장)

  1) 국토부 아파트 매매 실거래 상세  → SQLite (분당구 전체, 최근 3개월은 매번 갱신해 해제·지연신고 반영)
  2) 브이월드 법정동 경계 (5개 동)    → boundaries.geojson
  3) 브이월드 지오코딩 (단지 지번 → 좌표) → SQLite complexes 테이블
  4) 한국부동산원 R-ONE 분당구 기준선  → SQLite rone 테이블 (매매가격지수, 아파트매매거래현황)
  5) 역·선로(OpenStreetMap), 학교(공공데이터포털 API → 학구도안내서비스 공식 파일 → OSM) → stations.json, rail_tracks.json, schools.json
     data/raw 에 학교·역 파일을 직접 넣으면 그것을 우선 사용

준비:
  pip install requests openpyxl
  .env 파일 (이 스크립트와 같은 폴더):
      DATA_GO_KR_KEY=...
      VWORLD_KEY=...
      VWORLD_DOMAIN=                 # 웹사이트 유형 키만 입력, APP 유형이면 비워 둠
      RONE_KEY=...
  data/raw/ 에 학교 위치 CSV, 도시철도역사정보 XLSX(또는 CSV)를 넣어 두기

실행:
  python collect_public.py                 # 전체, 최근 24개월
  python collect_public.py --months 36
  python collect_public.py --only trades rone
"""
import argparse
import csv
import datetime as dt
import json
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

import os
ROOT = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))      # 컨테이너에서는 /data
OUT = DATA_DIR / "public"
RAW = DATA_DIR / "raw"
DB = DATA_DIR / "bundang.db"
OUT.mkdir(parents=True, exist_ok=True)
RAW.mkdir(parents=True, exist_ok=True)

SGG = "41135"                                   # 성남시 분당구
DONGS = {"수내동": "4113510200", "정자동": "4113510300", "서현동": "4113510500",
         "이매동": "4113510600", "야탑동": "4113510700"}
REFRESH_RECENT = 3                              # 최근 N개월은 매번 다시 받음
BBOX_FALLBACK = (37.330, 127.070, 37.425, 127.175)   # 남, 서, 북, 동 (경계 없을 때)

TRADE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
VWORLD_DATA = "https://api.vworld.kr/req/data"
VWORLD_ADDR = "https://api.vworld.kr/req/address"
RONE_URL = "https://www.reb.or.kr/r-one/openapi/SttsApiTblData.do"
RONE_TABLES = {"A_2024_00045": "매매가격지수", "A_2024_00554": "아파트매매거래현황"}


def load_env():
    """환경변수(도커 env_file) 우선, 없으면 스크립트 폴더의 .env"""
    env = {k: v for k, v in os.environ.items() if k in ("DATA_GO_KR_KEY", "VWORLD_KEY", "VWORLD_DOMAIN", "RONE_KEY", "ECOS_KEY")}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().split("#")[0].strip())
    return env


ENV = load_env()


def need(key):
    if not ENV.get(key):
        sys.exit(f".env 에 {key} 가 없습니다")
    return ENV[key]


def db():
    con = sqlite3.connect(DB)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS trades(
        id TEXT PRIMARY KEY, ym TEXT, deal_date TEXT, apt_seq TEXT, apt_nm TEXT,
        umd_nm TEXT, umd_cd TEXT, jibun TEXT, build_year INT, exclu_ar REAL,
        amount INT, floor INT, apt_dong TEXT, cdeal_type TEXT, cdeal_day TEXT,
        rgst_date TEXT, dealing_gbn TEXT, buyer_gbn TEXT, sler_gbn TEXT, fetched_at TEXT);
    CREATE INDEX IF NOT EXISTS ix_trades_ym ON trades(ym);
    CREATE INDEX IF NOT EXISTS ix_trades_apt ON trades(apt_seq);
    CREATE TABLE IF NOT EXISTS fetched_months(ym TEXT PRIMARY KEY, rows INT, fetched_at TEXT);
    CREATE TABLE IF NOT EXISTS complexes(
        apt_seq TEXT PRIMARY KEY, apt_nm TEXT, umd_nm TEXT, jibun TEXT,
        lat REAL, lng REAL, geo_status TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS ecos(
        series TEXT, ym TEXT, value REAL, PRIMARY KEY(series, ym));
    CREATE TABLE IF NOT EXISTS rone(
        statbl TEXT, name TEXT, ym TEXT, region TEXT, item TEXT, value REAL, unit TEXT,
        PRIMARY KEY(statbl, ym, region, item));
    """)
    return con


HISTORY_FROM = os.environ.get("HISTORY_FROM", "200601")        # 금리·지수 과거 자료 시작월
TRADE_MONTHS = int(os.environ.get("TRADE_MONTHS", "120"))      # 실거래를 받을 기간(개월), 연도별 비교용으로 10년


def months_since(start):
    out, (y, m) = [], (int(start[:4]), int(start[4:]))
    today = dt.date.today()
    while (y, m) <= (today.year, today.month):
        out.append(f"{y}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def months_back(n):
    today = dt.date.today().replace(day=1)
    out = []
    for i in range(n):
        y, m = divmod(today.year * 12 + today.month - 1 - i, 12)
        out.append(f"{y}{m + 1:02d}")
    return sorted(out)


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


# ---------------- 1) 실거래 ----------------
def fetch_trade_month(key, ym):
    rows, page = [], 1
    while True:
        r = requests.get(TRADE_URL, params={"serviceKey": key, "LAWD_CD": SGG, "DEAL_YMD": ym,
                                            "pageNo": page, "numOfRows": 1000}, timeout=60)
        root = ET.fromstring(r.content)
        code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode")
        if code not in ("000", "00"):
            raise RuntimeError(f"{ym} 실거래 오류: {code} {root.findtext('.//resultMsg') or root.findtext('.//returnAuthMsg')}")
        items = root.findall(".//item")
        rows += [{c.tag: (c.text or "").strip() for c in it} for it in items]
        total = int(root.findtext(".//totalCount") or 0)
        if len(rows) >= total or not items:
            return rows
        page += 1
        time.sleep(0.3)


def collect_trades(con, n_months):
    key = need("DATA_GO_KR_KEY")
    fetched = {r[0] for r in con.execute("SELECT ym FROM fetched_months")}
    months = months_back(n_months)
    recent = set(months[-REFRESH_RECENT:])
    for ym in months:
        if ym in fetched and ym not in recent:
            continue
        rows = fetch_trade_month(key, ym)
        con.execute("DELETE FROM trades WHERE ym=?", (ym,))
        seen = {}
        for t in rows:
            date = f"{t['dealYear']}-{int(t['dealMonth']):02d}-{int(t['dealDay']):02d}"
            base = f"{t.get('aptSeq')}|{date}|{t.get('floor')}|{t.get('excluUseAr')}|{t.get('dealAmount')}|{t.get('aptDong')}"
            seen[base] = seen.get(base, 0) + 1
            con.execute("INSERT OR REPLACE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                f"{base}|{seen[base]}", ym, date, t.get("aptSeq"), t.get("aptNm"),
                t.get("umdNm"), t.get("umdCd"), t.get("jibun"),
                int(t["buildYear"]) if t.get("buildYear", "").isdigit() else None,
                float(t["excluUseAr"]) if t.get("excluUseAr") else None,
                int(t["dealAmount"].replace(",", "")) if t.get("dealAmount") else None,
                int(t["floor"]) if t.get("floor", "").lstrip("-").isdigit() else None,
                t.get("aptDong"), t.get("cdealType"), t.get("cdealDay"), t.get("rgstDate"),
                t.get("dealingGbn"), t.get("buyerGbn"), t.get("slerGbn"), now()))
        con.execute("INSERT OR REPLACE INTO fetched_months VALUES (?,?,?)", (ym, len(rows), now()))
        con.commit()
        print(f"  {ym}: {len(rows)}건{' (갱신)' if ym in fetched else ''}")


# ---------------- 2) 법정동 경계 ----------------
def collect_boundaries():
    key, domain = need("VWORLD_KEY"), ENV.get("VWORLD_DOMAIN", "")
    features = []
    for name, code in DONGS.items():
        params = {"service": "data", "request": "GetFeature", "data": "LT_C_ADEMD_INFO",
                  "key": key, "format": "json", "geometry": "true",
                  "crs": "EPSG:4326", "attrFilter": f"emd_cd:=:{code[:8]}"}
        if domain:                       # 웹사이트 유형 키일 때만 필요 (APP 유형은 비워 둠)
            params["domain"] = domain
        r = requests.get(VWORLD_DATA, params=params, timeout=60).json()
        resp = r.get("response", {})
        if resp.get("status") != "OK":
            print(f"  ! {name} 경계 실패: {resp.get('status')} {resp.get('error')}")
            continue
        for f in resp["result"]["featureCollection"]["features"]:
            f["properties"] = {"name": name, "code": code, **f.get("properties", {})}
            features.append(f)
        print(f"  {name}: OK")
        time.sleep(0.3)
    (OUT / "boundaries.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": features}, ensure_ascii=False), encoding="utf-8")


def bbox(margin=0.012):
    p = OUT / "boundaries.geojson"
    if not p.exists():
        return BBOX_FALLBACK
    pts = []

    def walk(c):
        if isinstance(c, list) and c and isinstance(c[0], (int, float)):
            pts.append(c)
        elif isinstance(c, list):
            for x in c:
                walk(x)
    for f in json.loads(p.read_text(encoding="utf-8"))["features"]:
        walk(f["geometry"]["coordinates"])
    if not pts:
        return BBOX_FALLBACK
    lngs, lats = [p[0] for p in pts], [p[1] for p in pts]
    return (min(lats) - margin, min(lngs) - margin, max(lats) + margin, max(lngs) + margin)


# ---------------- 3) 단지 지오코딩 ----------------
def collect_geocode(con):
    key = need("VWORLD_KEY")
    dongs = tuple(DONGS)
    rows = con.execute(f"""
        SELECT apt_seq, MAX(apt_nm), umd_nm, jibun FROM trades
        WHERE umd_nm IN ({','.join('?' * len(dongs))}) AND apt_seq IS NOT NULL
          AND apt_seq NOT IN (SELECT apt_seq FROM complexes WHERE geo_status='OK')
        GROUP BY apt_seq""", dongs).fetchall()
    ok = 0
    for seq, nm, umd, jibun in rows:
        lat = lng = None
        status = "NOT_FOUND"
        try:
            r = requests.get(VWORLD_ADDR, params={
                "service": "address", "request": "getcoord", "version": "2.0", "crs": "epsg:4326",
                "address": f"경기도 성남시 분당구 {umd} {jibun}", "refine": "true", "simple": "false",
                "format": "json", "type": "parcel", "key": key}, timeout=30).json()
            resp = r.get("response", {})
            status = resp.get("status", "ERROR")
            if status == "OK":
                pt = resp["result"]["point"]
                lng, lat = float(pt["x"]), float(pt["y"])
                ok += 1
        except Exception as e:
            status = f"ERROR {e.__class__.__name__}"
        con.execute("INSERT OR REPLACE INTO complexes VALUES (?,?,?,?,?,?,?,?)",
                    (seq, nm, umd, jibun, lat, lng, status, now()))
        time.sleep(0.2)
    con.commit()
    total = con.execute("SELECT COUNT(*), SUM(geo_status='OK') FROM complexes").fetchone()
    print(f"  이번 실행 {ok}/{len(rows)} 성공, 누적 {total[1] or 0}/{total[0]} 단지 좌표 확보")


# ---------------- 4) R-ONE ----------------
def collect_rone(con, n_months):
    key = need("RONE_KEY")
    months = months_since(HISTORY_FROM)
    for statbl, name in RONE_TABLES.items():
        got = 0
        have = {r[0] for r in con.execute("SELECT DISTINCT ym FROM rone WHERE statbl=?", (statbl,))}
        for ym in months:
            if ym in have and ym not in months[-3:]:
                continue
            j = requests.get(RONE_URL, params={"KEY": key, "Type": "json", "STATBL_ID": statbl,
                                               "DTACYCLE_CD": "MM", "WRTTIME_IDTFR_ID": ym,
                                               "pIndex": 1, "pSize": 1000}, timeout=60).json()
            k = next(iter(j))
            if k == "RESULT" or len(j[k]) < 2:
                continue                                    # 아직 공표 전인 달
            for r in j[k][1]["row"]:
                region = r.get("CLS_FULLNM") or ""
                if region.endswith("성남시>분당구"):
                    con.execute("INSERT OR REPLACE INTO rone VALUES (?,?,?,?,?,?,?)",
                                (statbl, name, ym, region, r.get("ITM_NM"),
                                 float(r["DTA_VAL"]) if r.get("DTA_VAL") not in (None, "") else None,
                                 r.get("UI_NM")))
                    got += 1
            con.commit()                                    # 중간에 끊겨도 받은 달은 남김
            time.sleep(0.1)
        print(f"  {name}: {got}행")


# ---------------- 4-2) 한국은행 ECOS 금리 ----------------
ECOS_URL = "https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/{a}/{b}/{stat}/M/{start}/{end}/{item}"
ECOS_SERIES = {"base": ("722Y001", "0101000", "한국은행 기준금리"),
               "mortgage": ("121Y006", "BECBLA0302", "주택담보대출 금리(예금은행 신규취급액)")}


def collect_ecos(con):
    """ECOS_KEY 가 없으면 공개 샘플 키로 10건씩 나눠 받음 (느리지만 동작)"""
    key = ENV.get("ECOS_KEY") or "sample"
    step = 10 if key == "sample" else 1000
    end = dt.date.today().strftime("%Y%m")
    for sid, (stat, item, label) in ECOS_SERIES.items():
        last = con.execute("SELECT MAX(ym) FROM ecos WHERE series=?", (sid,)).fetchone()[0]
        start = HISTORY_FROM if not last else months_back(4)[0]          # 처음엔 전체, 이후엔 최근 몇 달만
        got, a = 0, 1
        while True:
            url = ECOS_URL.format(key=key, a=a, b=a + step - 1, stat=stat, start=start, end=end, item=item)
            try:
                j = requests.get(url, timeout=30).json()
            except (requests.RequestException, ValueError):
                print(f"  ! {label}: 응답 오류"); break
            body = j.get("StatisticSearch")
            if not body:
                msg = j.get("RESULT", {}).get("MESSAGE", "")
                if got == 0 and "해당하는 데이터가 없습니다" not in msg:
                    print(f"  ! {label}: {msg or j}")
                break
            for r in body["row"]:
                if r.get("DATA_VALUE") not in (None, ""):
                    con.execute("INSERT OR REPLACE INTO ecos VALUES (?,?,?)", (sid, r["TIME"], float(r["DATA_VALUE"])))
                    got += 1
            if a + step - 1 >= int(body["list_total_count"]):
                break
            a += step
            time.sleep(0.1)
        con.commit()
        print(f"  {label}: {got}개월" + (" (샘플 키 사용, ECOS_KEY 발급 권장)" if key == "sample" else ""))


# ---------------- 5) 학교·역 파일 ----------------
def read_table(path):
    if path.suffix.lower() == ".xlsx":
        import openpyxl
        ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
        rows = list(ws.iter_rows(values_only=True))
        head = next(i for i, r in enumerate(rows) if r and sum(1 for c in r if c) >= 3)
        cols = [str(c or "").strip() for c in rows[head]]
        return [dict(zip(cols, r)) for r in rows[head + 1:] if r and any(r)]
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            with open(path, encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"인코딩을 읽지 못함: {path}")


def col(row, *words, exclude=()):
    for k in row:
        if all(w in str(k) for w in words) and not any(x in str(k) for x in exclude):
            return k
    return None


OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]
SCHOOL_API = "http://api.data.go.kr/openapi/tn_pubr_public_elesch_mskul_lc_api"


def overpass(query):
    for attempt in range(2):
        for url in OVERPASS:
            host = url.split("/")[2]
            try:
                r = requests.post(url, data={"data": query}, timeout=90,
                                  headers={"User-Agent": "bundang-briefing/1.0 (personal research)"})
                if r.ok and r.text.lstrip().startswith("{"):
                    return r.json()["elements"]
                print(f"  · {host}: HTTP {r.status_code} {r.text[:60].strip()!r}")
            except (requests.RequestException, ValueError) as e:
                print(f"  · {host}: {e.__class__.__name__}")
        time.sleep(10)
    raise RuntimeError("Overpass 서버 응답 없음")


# 분당 일대 역 목록 (Overpass를 못 쓸 때 브이월드 장소 검색으로 좌표를 찾음, 범위 밖은 자동 제외)
STATION_LIST = [("야탑", ["수인분당선"]), ("이매", ["수인분당선", "경강선"]), ("서현", ["수인분당선"]),
                ("수내", ["수인분당선"]), ("정자", ["수인분당선", "신분당선"]), ("미금", ["수인분당선", "신분당선"]),
                ("오리", ["수인분당선"]), ("판교", ["신분당선", "경강선"]), ("성남", ["경강선", "GTX-A"]),
                ("동천", ["신분당선"]), ("모란", ["수인분당선", "8호선"])]
VWORLD_SEARCH = "https://api.vworld.kr/req/search"


def stations_vworld(inside):
    key = need("VWORLD_KEY")
    out = []
    for name, lines in STATION_LIST:
        try:
            r = requests.get(VWORLD_SEARCH, params={
                "service": "search", "request": "search", "version": "2.0", "crs": "EPSG:4326",
                "size": 10, "page": 1, "query": f"{name}역", "type": "place", "format": "json",
                "errorformat": "json", "key": key}, timeout=30).json()
            items = r.get("response", {}).get("result", {}).get("items", []) or []
        except (requests.RequestException, ValueError):
            items = []
        hit = None
        for it in items:
            title = it.get("title", "").replace(" ", "")
            pt = it.get("point", {})
            try:
                lat, lng = float(pt["y"]), float(pt["x"])
            except (KeyError, TypeError, ValueError):
                continue
            if title.startswith(f"{name}역") and inside(lat, lng):
                hit = {"name": name, "lat": lat, "lng": lng, "lines": lines}
                break
        if hit:
            out.append(hit)
        time.sleep(0.2)
    return out


def school_level(name, se=""):
    t = f"{se} {name}"
    return "초등학교" if "초등" in t else "중학교" if "중학" in t else "고등학교" if "고등" in t else ""


def schools_official(s, w, n, e):
    """공공데이터포털 전국초중등학교위치표준데이터 (활용신청 필요). 실패하면 None"""
    key = ENV.get("DATA_GO_KR_KEY")
    out, page = [], 1
    while key:
        r = requests.get(SCHOOL_API, params={"serviceKey": key, "pageNo": page, "numOfRows": 1000, "type": "json"}, timeout=60)
        try:
            body = r.json()["response"]["body"]
        except (ValueError, KeyError):
            print(f"  공식 학교 API 사용 불가 ({r.text[:80].strip()}...) → OSM 사용")
            return None
        items = body.get("items") or []
        items = items.get("item", items) if isinstance(items, dict) else items
        for it in items:
            lat = next((it[k] for k in it if "latitude" in k.lower()), None)
            lng = next((it[k] for k in it if "longitude" in k.lower()), None)
            try:
                lat, lng = float(lat), float(lng)
            except (TypeError, ValueError):
                continue
            if not (s <= lat <= n and w <= lng <= e):
                continue
            if "폐" in str(it.get("operSttus", "")):
                continue
            nm = it.get("schoolNm", "")
            out.append({"name": nm, "level": school_level(nm, it.get("schoolSe", "")), "lat": lat, "lng": lng})
        if page * 1000 >= int(body.get("totalCount", 0)):
            return out
        page += 1


SCHOOLZONE = "https://schoolzone.emac.kr"


def schools_schoolzone(inside):
    """학구도안내서비스 공개 파일 '초중고 학교 위치' 최신본 (키 불필요). 실패하면 None"""
    import io, re, zipfile
    try:
        html = requests.get(SCHOOLZONE + "/publicData/publicDataList.do", timeout=30).text
        for row in html.split("<tr")[1:]:
            if "학교 위치" not in row:
                continue
            m = re.search(r'data-nttId="(\d+)"\s+data-atchFileId="([^"]+)"\s+data-fileSn="(\d+)"', row)
            if not m:
                continue
            r = requests.get(SCHOOLZONE + "/publicData/publicDataFileDownload.do",
                             params={"nttId": m[1], "atchFileId": m[2], "fileSn": m[3]}, timeout=120)
            z = zipfile.ZipFile(io.BytesIO(r.content))
            raw = z.read(next(i for i in z.infolist() if i.filename.lower().endswith(".csv")))
            txt = next(raw.decode(enc) for enc in ("utf-8-sig", "cp949") if _decodes(raw, enc))
            out = []
            for x in csv.DictReader(io.StringIO(txt)):
                try:
                    lat, lng = float(x["위도"]), float(x["경도"])
                except (KeyError, ValueError):
                    continue
                if inside(lat, lng) and "운영" in x.get("운영상태", "운영"):
                    out.append({"name": x["학교명"], "level": x["학교급구분"], "lat": lat, "lng": lng})
            print(f"  학구도안내서비스 학교 위치 파일 사용 (기준일 {x.get('데이터기준일자', '?')})")
            return out
    except Exception as e:
        print(f"  학구도안내서비스 파일 실패: {e.__class__.__name__}")
    return None


def _decodes(raw, enc):
    try:
        raw.decode(enc)
        return True
    except UnicodeDecodeError:
        return False


def collect_facilities():
    s, w, n, e = bbox()
    box = f"({s},{w},{n},{e})"
    inside = lambda lat, lng: s <= lat <= n and w <= lng <= e

    # 역 + 선로: OpenStreetMap 우선, 실패하면 브이월드 장소 검색 (선로는 역 사이 직선 근사)
    stations, tracks, st_src = {}, [], "OpenStreetMap"
    try:
        el = overpass(f'[out:json][timeout:90];(node["railway"="station"]{box};'
                      f'node["public_transport"="station"]["subway"="yes"]{box};'
                      f'way["railway"~"^(subway|rail|light_rail)$"][!"service"]{box};);out geom;')
        for x in el:
            if x["type"] == "node" and x.get("tags", {}).get("name"):
                nm = x["tags"]["name"].strip()
                nm = nm[:-1] if nm.endswith("역") else nm
                stations.setdefault(nm, {"name": nm, "lat": x["lat"], "lng": x["lon"], "lines": []})
        tracks = [[[p["lat"], p["lon"]] for p in x["geometry"]] for x in el if x["type"] == "way" and x.get("geometry")]
    except RuntimeError:
        print("  Overpass 실패 → 브이월드 장소 검색으로 역 좌표 조회")
        st_src = "브이월드 검색"
        stations = {x["name"]: x for x in stations_vworld(inside)}

    # 학교: 공공데이터포털 API → 학구도안내서비스 공개 파일 → OpenStreetMap
    schools, src = schools_official(s, w, n, e), "공공데이터포털"
    if not schools:
        schools, src = schools_schoolzone(inside), "학구도안내서비스"
    if not schools:
        src = "OpenStreetMap"
        try:
            el = overpass(f'[out:json][timeout:90];nwr["amenity"="school"]{box};out center;')
        except RuntimeError:
            el, src = [], "없음"
            print("  ! 학교를 받을 곳이 없습니다. 공공데이터포털에서 '전국초중등학교위치표준데이터' 활용신청 후 재실행하세요.")
        schools = []
        for x in el:
            nm = x.get("tags", {}).get("name", "")
            lv = school_level(nm)
            lat, lng = (x.get("lat"), x.get("lon")) if x["type"] == "node" else (x["center"]["lat"], x["center"]["lon"])
            if lv and inside(lat, lng):
                schools.append({"name": nm, "level": lv, "lat": lat, "lng": lng})

    # data/raw 에 직접 넣은 파일이 있으면 그 내용으로 덮어씀
    manual_s, manual_st = collect_files(write=False)
    if manual_s:
        schools, src = manual_s, "직접 넣은 파일"
    if manual_st:
        stations = {x["name"]: x for x in manual_st}

    (OUT / "schools.json").write_text(json.dumps(schools, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "stations.json").write_text(json.dumps(list(stations.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "rail_tracks.json").write_text(json.dumps(tracks), encoding="utf-8")
    lv = {}
    for x in schools:
        lv[x["level"]] = lv.get(x["level"], 0) + 1
    print(f"  역 {len(stations)}곳 ({', '.join(sorted(stations))}), 선로 {len(tracks)}구간 (출처: {st_src})")
    print(f"  학교 {len(schools)}곳 {lv} (출처: {src})")
    if not stations or not schools:
        print("  ! 역 또는 학교가 0곳 → 브리핑에서 해당 시설은 접근점수에서 빠집니다")


def collect_files(write=True):
    s, w, n, e = bbox()
    inside = lambda lat, lng: s <= lat <= n and w <= lng <= e
    schools, stations = [], {}
    for path in sorted(RAW.glob("*")):
        if path.suffix.lower() not in (".csv", ".xlsx"):
            continue
        rows = read_table(path)
        if not rows:
            continue
        r0 = rows[0]
        c_lat, c_lng = col(r0, "위도"), col(r0, "경도")
        if not (c_lat and c_lng):
            continue
        c_school, c_station = col(r0, "학교명"), col(r0, "역사명") or col(r0, "역명", exclude=("영문", "한자"))
        for r in rows:
            try:
                lat, lng = float(r[c_lat]), float(r[c_lng])
            except (TypeError, ValueError):
                continue
            if not inside(lat, lng):
                continue
            if c_school:
                st = col(r0, "운영상태")
                if st and r.get(st) and "운영" not in str(r[st]):
                    continue
                lv = col(r0, "학교급")
                schools.append({"name": r[c_school], "level": str(r.get(lv) or ""), "lat": lat, "lng": lng})
            elif c_station:
                nm = str(r[c_station]).strip()
                ln = col(r0, "노선명")
                rec = stations.setdefault(nm, {"name": nm, "lat": lat, "lng": lng, "lines": []})
                if ln and r.get(ln) and r[ln] not in rec["lines"]:
                    rec["lines"].append(r[ln])
        print(f"  {path.name}: {'학교' if c_school else '역' if c_station else '알 수 없음'} 파일")
    if not write:
        return schools, list(stations.values())
    (OUT / "schools.json").write_text(json.dumps(schools, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "stations.json").write_text(json.dumps(list(stations.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  학교 {len(schools)}곳, 역 {len(stations)}곳")
    return schools, list(stations.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=TRADE_MONTHS, help="실거래를 받을 기간(개월)")
    ap.add_argument("--only", nargs="*", choices=["trades", "boundaries", "geocode", "rone", "ecos", "facilities", "files"])
    a = ap.parse_args()
    steps = a.only or ["trades", "boundaries", "geocode", "rone", "ecos", "facilities"]
    con = db()
    print(f"=== 공공데이터 수집 {now()} ===")
    if "trades" in steps:
        print("[1] 국토부 실거래");      collect_trades(con, a.months)
    if "boundaries" in steps:
        print("[2] 법정동 경계");        collect_boundaries()
    if "geocode" in steps:
        print("[3] 단지 지오코딩");      collect_geocode(con)
    if "rone" in steps:
        print("[4] R-ONE 기준선");       collect_rone(con, a.months)
    if "ecos" in steps:
        print("[4-2] 한국은행 금리");    collect_ecos(con)
    if "facilities" in steps or "files" in steps:
        print("[5] 역·선로·학교")
        f = OUT / "schools.json"
        fresh = (f.exists() and time.time() - f.stat().st_mtime < 30 * 86400
                 and json.loads(f.read_text(encoding="utf-8")))          # 비어 있지 않고 30일 이내
        if fresh and not a.only:
            print("  최근 30일 안에 받은 데이터가 있어 건너뜀 (강제 갱신: --only facilities)")
        else:
            collect_facilities()
    print("=== 완료 ===")


if __name__ == "__main__":
    main()
