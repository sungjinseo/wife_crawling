"""
분당 5개 동 브리핑 생성기: 공공데이터(추이·입지) + 네이버(현재 매물) → briefing_YYYY-MM-DD.html

필요 파일 (기본 위치, 옵션으로 변경 가능)
  data/bundang.db                 collect_public.py 결과 (실거래, 지오코딩, R-ONE)
  data/public/boundaries.geojson  법정동 경계
  data/public/schools.json        학교 좌표 (초·중·고)
  data/public/stations.json       역 좌표
  data/naver_YYYY-MM-DD.json      collect_naver.py 결과 (PC에서 수집 후 복사)
  .env 의 VWORLD_KEY              배경지도 (없으면 Esri 회색 지도)

실행: python build_briefing.py
      python build_briefing.py --naver data/naver_2026-10-04.json --data data
"""
import argparse
import datetime as dt
import json
import math
import sqlite3
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

import os
ROOT = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
OUT_DIR = Path(os.environ.get("OUT_DIR", ROOT))
DONGS = ["수내동", "정자동", "서현동", "이매동", "야탑동"]
PYEONG = 3.3058
SUPPLY_MIN, SUPPLY_MAX = 24 * PYEONG, 40 * PYEONG
FILTER = (24, 40, 0, 15)                 # 기본 필터: 공급 24평 이상 40평 미만, 0~15억 (브리핑 화면에서 변경 가능)
SUPPLY_RATIO = 1.33                      # 평형 정보가 없을 때 전용→공급 근사 (분당 구축 평균)
# 시설별 (도보권 기준거리 m, 가중치). 기준거리 × 0.6 안은 만점, 기준거리 × 1.5 에서 0점
# 브리핑 화면에서 기준거리·가중치를 바꾸면 브라우저에서 다시 계산됨 (여기 값은 기본값)
FACILITIES = {"station": (700, 40), "elementary": (400, 30), "middle": (1000, 15), "high": (1000, 15)}
FULL_RATIO, ZERO_RATIO = 0.6, 1.5
FAC_LABEL = {"station": "역", "elementary": "초", "middle": "중", "high": "고"}
GRADES = [("상", 60), ("중", 30), ("하", -1)]
PROVISIONAL = 2                          # 최근 N개월은 신고 지연으로 잠정 표시
BRIEF_MONTHS = int(os.environ.get("BRIEF_MONTHS", "36"))   # 단지별 화면에 넣을 실거래 기간 (연도별 비교는 DB 전체 사용)
RONE_CHART_MONTHS = 36                   # 분당구 기준선 차트에 보여줄 기간


# ---------------- 공통 ----------------
def env_key():
    """.env 의 VWORLD_KEY (BOM, 공백, export, 따옴표, 주석 허용). 없으면 환경변수"""
    if os.environ.get("VWORLD_KEY"):
        return os.environ["VWORLD_KEY"]
    for p in (ROOT / ".env", Path.cwd() / ".env"):
        if p.exists():
            for line in p.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:].strip()
                k, _, v = line.partition("=")
                if k.strip() == "VWORLD_KEY":
                    return v.split("#")[0].strip().strip('"').strip("'")
    return os.environ.get("VWORLD_KEY", "")


def dist_m(a_lat, a_lng, b_lat, b_lng):
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    h = (math.sin((p2 - p1) / 2) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(math.radians(b_lng - a_lng) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(h))


def norm_jibun(j):
    j = str(j or "").strip().replace(" ", "")
    return j[:-2] if j.endswith("-0") else j


def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None


def ppp(price_won, supply):                       # 평당가(만원)
    return price_won / (supply / PYEONG) / 1e4 if price_won and supply else None


def write_atomic(path, text):
    """임시 파일에 다 쓴 뒤 교체 → nginx·챗봇이 반쯤 쓰인 파일을 읽는 일이 없음"""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def grade_of(score):
    return next(g for g, cut in GRADES if score >= cut)


# ---------------- 입력 ----------------
def load_inputs(a):
    data = Path(a.data)
    naver_path = Path(a.naver) if a.naver else max(data.glob("naver_*.json"), default=None)
    if not naver_path:
        raise SystemExit("네이버 수집 파일(data/naver_*.json)이 없습니다")
    naver = json.loads(naver_path.read_text(encoding="utf-8"))
    pub = data / "public"
    db = data / "bundang.db"
    # 파일 점검: sqlite3.connect 는 파일이 없으면 빈 DB를 새로 만들어 버리므로 먼저 확인
    problems = []
    if not db.exists() or db.stat().st_size == 0:
        problems.append(f"{db} 없음 또는 빈 파일")
    else:
        tables = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "trades" not in tables:
            problems.append(f"{db} 에 trades 테이블 없음 (서버의 data/bundang.db 가 아닌 파일)")
    for n in ("boundaries.geojson", "schools.json", "stations.json"):
        if not (pub / n).exists():
            problems.append(f"{pub / n} 없음")
    if problems:
        found = [str(x) for x in ROOT.rglob("*.db")] + [str(x) for x in ROOT.rglob("boundaries.geojson")]
        raise SystemExit("입력 파일 확인 필요:\n  - " + "\n  - ".join(problems)
                         + ("\n이 폴더 아래에서 찾은 관련 파일:\n  " + "\n  ".join(found) if found else "")
                         + "\n필요 구조: data/bundang.db, data/public/{boundaries.geojson,schools.json,stations.json}, data/naver_*.json")
    read = lambda n: json.loads((pub / n).read_text(encoding="utf-8")) if (pub / n).exists() else None
    con = sqlite3.connect(db)
    return (naver, read("boundaries.geojson"), read("schools.json") or [], read("stations.json") or [],
            read("rail_tracks.json") or [], read("custom_facilities.json") or {}, con)


def facilities(schools, stations, custom=None):
    """수집한 시설 + 브리핑 화면에서 내보낸 custom_facilities.json (추가·제외) 병합"""
    f = {"station": list(stations), "elementary": [], "middle": [], "high": []}
    for s in schools:
        lv = s.get("level", "")
        key = "elementary" if lv.startswith("초") else "middle" if lv.startswith("중") else "high" if lv.startswith("고") else None
        if key:
            f[key].append(s)
    custom = custom or {}
    drop = {(x["kind"], x["name"]) for x in custom.get("excluded", [])}
    for k in f:
        f[k] = [p for p in f[k] if (k, p["name"]) not in drop]
    for x in custom.get("added", []):
        if x.get("kind") in f and not any(p["name"] == x["name"] for p in f[x["kind"]]):
            f[x["kind"]].append({"name": x["name"], "lat": x["lat"], "lng": x["lng"], "custom": True})
    if custom:
        print(f"  사용자 시설 반영: 추가 {len(custom.get('added', []))}, 제외 {len(custom.get('excluded', []))}")
    return f


# ---------------- 단지 통합 (공공 aptSeq ↔ 네이버 complexNumber) ----------------
def unify(naver, con):
    nv = {}
    for c in naver["complexes"]:
        nv.setdefault((c["dong"], norm_jibun(c.get("jibun"))), c)
    rows = con.execute(f"""
        SELECT t.apt_seq, MAX(t.apt_nm), t.umd_nm, MAX(t.jibun), c.lat, c.lng, COUNT(*) AS n
        FROM trades t LEFT JOIN complexes c ON c.apt_seq = t.apt_seq
        WHERE t.umd_nm IN ({','.join('?' * len(DONGS))}) AND t.apt_seq IS NOT NULL
        GROUP BY t.apt_seq ORDER BY n DESC""", DONGS).fetchall()
    units, used, match = {}, set(), {}
    # 1차: 법정동+지번 정확 일치 / 2차: 본번만 같은 후보가 하나뿐일 때. 네이버 단지 하나는 한 번만 연결
    for exact in (True, False):
        for seq, nm, umd, jibun, lat, lng, _ in rows:
            if seq in match:
                continue
            key = (umd, norm_jibun(jibun))
            if exact:
                n = nv.get(key)
            else:
                cands = [c for k, c in nv.items() if k[0] == umd and k[1].split("-")[0] == key[1].split("-")[0]
                         and c["complexNumber"] not in used]
                n = cands[0] if len(cands) == 1 else None
            if n and n["complexNumber"] not in used:
                used.add(n["complexNumber"])
                match[seq] = n
    for seq, nm, umd, jibun, lat, lng, _ in rows:
        n = match.get(seq)
        units[f"P{seq}"] = {"id": f"P{seq}", "aptSeq": seq, "name": n["name"] if n else nm, "pubName": nm, "dong": umd,
                            "lat": (n or {}).get("lat") or lat, "lng": (n or {}).get("lng") or lng, "naver": n}
    for c in naver["complexes"]:
        if c["complexNumber"] not in used:
            units[f"N{c['complexNumber']}"] = {"id": f"N{c['complexNumber']}", "aptSeq": None, "name": c["name"],
                                               "dong": c["dong"], "lat": c.get("lat"), "lng": c.get("lng"), "naver": c}
    return units


def fill_rooms(naver, cache_dir):
    """평형별 방 수: 네이버 수집 파일에 없으면 수집기 캐시(cache/pyeong_단지번호.json)에서 보충"""
    filled = missing = 0
    for c in naver.get("complexes", []):
        pys = c.get("pyeongs", [])
        if not pys or all(p.get("rooms") is not None for p in pys):
            continue
        f = cache_dir / f"pyeong_{c['complexNumber']}.json"
        raw = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
        by_no = {r.get("number"): r for r in raw or []}
        for p in pys:
            r = by_no.get(p.get("number"))
            if r and r.get("roomCount") is not None:
                p["rooms"], p["baths"] = r.get("roomCount"), r.get("bathRoomCount")
                filled += 1
            else:
                missing += 1
    return filled, missing


def pyeong_of(exclu, n):
    """전용면적이 1㎡ 이내로 맞는 네이버 평형 (없으면 None)"""
    if n and n.get("pyeongs"):
        best = min(n["pyeongs"], key=lambda p: abs((p.get("exclusive") or 0) - exclu))
        if best.get("exclusive") and abs(best["exclusive"] - exclu) <= 1.0:
            return best
    return None


def supply_of(exclu, n):
    if n and n.get("pyeongs"):
        best = min(n["pyeongs"], key=lambda p: abs((p.get("exclusive") or 0) - exclu))
        if best.get("exclusive") and abs(best["exclusive"] - exclu) <= 1.0 and best.get("supply"):
            return best["supply"], True
    return exclu * SUPPLY_RATIO, False


# ---------------- 연도별 금리·집값 ----------------
def build_yearly(con, idx):
    """연도별: 한국은행 기준금리(연평균·연말), 주담대 금리(연평균), 분당구 매매가격지수 상승률(연말 대비),
       5개 동 실거래 전용 평당가 중앙값·건수(해제 제외). 올해는 '연초 대비, n월까지'로 표시"""
    ecos = defaultdict(dict)
    try:
        for sid, ym, v in con.execute("SELECT series, ym, value FROM ecos"):
            ecos[sid][ym] = v
    except sqlite3.OperationalError:
        pass                                            # 이전 버전 DB: ecos 테이블 없음
    ppp_y = defaultdict(list)
    for ym, exclu, amount in con.execute(f"""
            SELECT ym, exclu_ar, amount FROM trades
            WHERE umd_nm IN ({','.join('?' * len(DONGS))}) AND (cdeal_type IS NULL OR cdeal_type = '')
              AND exclu_ar > 0 AND amount > 0""", DONGS):
        ppp_y[ym[:4]].append(amount / (exclu / PYEONG))
    years = sorted({ym[:4] for d in (idx, ecos["base"], ecos["mortgage"]) for ym in d} | set(ppp_y))
    out, prev_idx, prev_ppp = [], None, None
    for y in years:
        ims = sorted(m for m in idx if m.startswith(y))
        last_m = ims[-1] if ims else None
        iv = idx[last_m] if last_m else None
        base = [v for m, v in ecos["base"].items() if m.startswith(y)]
        mort = [v for m, v in ecos["mortgage"].items() if m.startswith(y)]
        bend = ecos["base"][max(m for m in ecos["base"] if m.startswith(y))] if base else None
        pp = med(ppp_y.get(y, []))
        # 분당구 실거래는 최근 몇 년만 있으므로 건수가 적은 해(100건 미만)는 평당가를 비움
        if pp is not None and len(ppp_y[y]) < 100:
            pp = None
        row = {"y": int(y), "partial": bool(last_m and last_m[4:] != "12"), "lastMonth": int(last_m[4:]) if last_m else None,
               "idx": round(iv, 2) if iv else None,
               "idxChg": round((iv / prev_idx - 1) * 100, 1) if iv and prev_idx else None,
               "base": round(sum(base) / len(base), 2) if base else None, "baseEnd": bend,
               "mort": round(sum(mort) / len(mort), 2) if mort else None,
               "ppp": round(pp) if pp else None,
               "pppChg": round((pp / prev_ppp - 1) * 100, 1) if pp and prev_ppp else None,
               "cnt": len(ppp_y.get(y, []))}
        out.append(row)
        prev_idx = iv or prev_idx
        prev_ppp = pp or prev_ppp
    return [r for r in out if r["idx"] or r["base"] or r["ppp"]]


# ---------------- 계산 ----------------
# ---------------- 재건축 특별정비구역 (선도지구) 선정·탈락 ----------------
# 국토부 단지번호(aptSeq)로 맞추고, 없으면 '동 + 이름 키워드'로 맞춤 (네이버에만 있는 묶음 단지 이름도 잡힘). data/public/renewal_zones.json 이 있으면 그걸 씀.
# 탈락은 언론 보도로 확인된 신청 구역 기준 (2026-10 현재).
RZ = None
RENEWAL_DEFAULT = {
    "asof": "2026-10-07",
    "zones": [
        {"round": 1, "status": "선정", "zone": "양지마을", "date": "2024-11-27",
         "seqs": ["41135-71", "41135-72", "41135-247", "41135-73", "41135-248", "41135-562", "41135-586"],
         "match": [["수내동", "양지"]]},
        {"round": 1, "status": "선정", "zone": "시범단지 현대우성", "date": "2024-11-27", "seqs": ["41135-99", "41135-100"],
         "match": [["서현동", "시범", "현대"], ["서현동", "시범", "우성"]]},
        {"round": 1, "status": "선정", "zone": "샛별마을", "date": "2024-11-27",
         "seqs": ["41135-18", "41135-19", "41135-20", "41135-21"], "match": [["분당동", "샛별"]]},
        {"round": 2, "status": "선정", "zone": "시범단지1", "date": "2026-09-29", "note": "1차 탈락 → 2차 선정",
         "seqs": ["41135-98", "41135-102", "41135-103"],
         "match": [["서현동", "시범", "삼성"], ["서현동", "시범", "한신"], ["서현동", "시범", "한양"]]},
        {"round": 2, "status": "선정", "zone": "파크타운", "date": "2026-09-29", "note": "1차 탈락 → 2차 선정",
         "seqs": ["41135-74", "41135-75", "41135-76", "41135-77"], "match": [["수내동", "파크타운"]]},
        {"round": 2, "status": "선정", "zone": "푸른마을", "date": "2026-09-29", "seqs": ["41135-78", "41135-79", "41135-80"],   # 벽산·신성·쌍용
         "match": [["수내동", "푸른"]]},             # 네이버 묶음 이름: 푸른벽산,신성,쌍용
        {"round": 2, "status": "선정", "zone": "한솔마을2", "date": "2026-09-29", "note": "1차 탈락 → 2차 선정",
         "seqs": ["41135-66", "41135-68", "41135-69"],
         "match": [["정자동", "한솔", "1단지"], ["정자동", "한솔", "2단지"], ["정자동", "한솔", "3단지"], ["정자동", "한솔청구"], ["정자동", "한솔LG"], ["정자동", "한솔한일"]]},
        {"round": 2, "status": "선정", "zone": "이매촌1", "date": "2026-09-29", "seqs": ["41135-92", "41135-93"],
         "match": [["이매동", "이매촌", "삼성"], ["이매동", "이매촌", "삼환"], ["이매동", "이매삼성"], ["이매동", "이매삼환"]]},
        # 탈락 (3차 재도전 후보)
        {"round": 2, "status": "탈락", "zone": "효자촌", "note": "1·2차 탈락", "match": [["서현동", "효자촌"]]},
        {"round": 2, "status": "탈락", "zone": "정든마을", "note": "1·2차 탈락", "match": [["정자동", "정든"]]},
        {"round": 2, "status": "탈락", "zone": "아름마을 풍림·선경·효성", "note": "1·2차 탈락",
         "match": [["이매동", "아름", "풍림"], ["이매동", "아름", "선경"], ["이매동", "아름", "효성"]]},
        {"round": 2, "status": "탈락", "zone": "상록마을 우성", "note": "1·2차 탈락", "match": [["정자동", "상록", "우성"]]},
        {"round": 2, "status": "탈락", "zone": "까치마을1", "note": "1·2차 탈락", "match": [["구미동", "까치", "1단지"]]},
        {"round": 2, "status": "탈락", "zone": "한솔마을 4단지", "match": [["정자동", "한솔", "4단지"]]},
        {"round": 2, "status": "탈락", "zone": "이매촌 (1구역 외)", "match": [["이매동", "이매촌"]]},
    ],
}


def renewal_of(u, zones):
    """단지의 선정·탈락 정보 (선정이 탈락보다 우선)"""
    seq = u.get("aptSeq") or ""
    for z in zones:
        if seq and seq in z.get("seqs", []):
            return z
    names = [(u.get(k) or "").replace(" ", "") for k in ("name", "pubName")]
    for z in zones:
        for m in z.get("match", []):
            dong, *kws = m
            if u.get("dong") == dong and any(nm and all(k in nm for k in kws) for nm in names):
                return z
    return None


def rz_short(z):
    if not z:
        return None
    return {"r": z["round"], "s": z["status"], "z": z["zone"], "d": z.get("date"), "note": z.get("note")}


def build(a):
    naver, bounds, schools, stations, tracks, custom, con = load_inputs(a)
    today = dt.date.fromisoformat(naver["date"])
    fac = facilities(schools, stations, custom)
    filled, missing = fill_rooms(naver, Path(a.cache))
    units = unify(naver, con)

    # 실거래: 평형·가격 필터 없이 전부 (필터는 브리핑 화면에서 적용)
    seq2unit = {u["aptSeq"]: u for u in units.values() if u["aptSeq"]}
    trades = []
    for seq, ym, exclu, amount, ddate, floor in con.execute(f"""
            SELECT apt_seq, ym, exclu_ar, amount, deal_date, floor FROM trades
            WHERE umd_nm IN ({','.join('?' * len(DONGS))}) AND (cdeal_type IS NULL OR cdeal_type = '')
              AND exclu_ar IS NOT NULL AND amount IS NOT NULL""", DONGS):
        u = seq2unit.get(seq)
        if u:
            sup, exact = supply_of(exclu, u["naver"])
            pm = pyeong_of(exclu, u["naver"])
            trades.append({"u": u, "ym": ym, "exclu": exclu, "amount": amount, "supply": sup, "exact": exact,
                           "date": ddate, "floor": floor, "rooms": pm.get("rooms") if pm else None})
    all_months = sorted({t["ym"] for t in trades})
    keep = set(all_months[-BRIEF_MONTHS:])
    trades = [t for t in trades if t["ym"] in keep]
    months = sorted(keep)
    mi = {m: i for i, m in enumerate(months)}
    tr_by = defaultdict(list)
    for t in trades:        # [월 인덱스, 거래가(만원), 전용㎡, 공급㎡, 계약일, 층, 방 수]
        tr_by[t["u"]["id"]].append([mi[t["ym"]], t["amount"], round(t["exclu"], 1), round(t["supply"], 1), t["date"], t["floor"], t["rooms"]])

    built_by_seq = dict(con.execute("SELECT apt_seq, MAX(build_year) FROM trades GROUP BY apt_seq"))
    out_units, n_list, old_naver = [], 0, 0
    global RZ
    rzf = Path(a.data) / "public" / "renewal_zones.json"
    RZ = json.loads(rzf.read_text(encoding="utf-8")) if rzf.exists() else RENEWAL_DEFAULT
    for u in units.values():
        n = u["naver"] or {}
        py = [[p["supply"], p.get("exclusive"), p.get("households") or 0, p.get("name"), p.get("rooms")]
              for p in n.get("pyeongs", []) if p.get("supply")]
        ar = []
        for x in n.get("articles", []):
            if not x.get("price") or not x.get("supply"):
                continue
            days = (today - dt.date.fromisoformat(x["exposure"])).days if x.get("exposure") else None
            pm = pyeong_of(x["exclusive"], n) if x.get("exclusive") else None
            ar.append([round(x["price"] / 1e4), x["supply"], x.get("exclusive"), days, x.get("realtors", 1),
                       x.get("floor"), x.get("dong"), pm.get("rooms") if pm else None])   # [..., 층, 동, 방 수]
        n_list += len(ar)
        if n and n.get("scope") != "all":
            old_naver += 1
        appr = str(n.get("approval") or "")
        built = int(appr[:4]) if appr[:4].isdigit() else built_by_seq.get(u["aptSeq"])
        out_units.append({"id": u["id"], "name": u["name"], "dong": u["dong"], "lat": u["lat"], "lng": u["lng"],
                          "built": built, "age": (today.year - built + 1) if built else None,     # 년차 (네이버 표기와 같이 +1)
                          "households": n.get("households"), "cn": n.get("complexNumber"),      # 네이버 단지번호 (바로가기용)
                          "naver": bool(n), "py": py, "ar": ar, "tr": tr_by.get(u["id"], []),
                          "rz": rz_short(renewal_of(u, RZ["zones"]))})

    rone = defaultdict(dict)
    for name, ym, item, val in con.execute("SELECT name, ym, item, value FROM rone"):
        if (name, item) in (("매매가격지수", "지수"), ("아파트매매거래현황", "동(호)수")):
            rone[name][ym] = val
    rone_months = sorted(set(rone["매매가격지수"]) | set(rone["아파트매매거래현황"]))
    yearly = build_yearly(con, rone["매매가격지수"])
    rone_months = rone_months[-RONE_CHART_MONTHS:]

    payload = {
        "version": 9, "date": naver["date"], "months": months, "provisional": PROVISIONAL,
        "units": out_units, "dongs": DONGS,
        "boundaries": bounds, "tracks": tracks, "facilities": {k: [{"name": p["name"], "lat": p["lat"], "lng": p["lng"],
                                                  "lines": p.get("lines"), "custom": p.get("custom", False)} for p in v] for k, v in fac.items()},
        "defaults": {"walk": {k: v[0] for k, v in FACILITIES.items()}, "weight": {k: v[1] for k, v in FACILITIES.items()},
                     "cuts": [GRADES[0][1], GRADES[1][1]], "fullRatio": FULL_RATIO, "zeroRatio": ZERO_RATIO,
                     "filter": {"areaMin": FILTER[0], "areaMax": FILTER[1], "priceMin": FILTER[2], "priceMax": FILTER[3]}},
        "yearly": yearly,
        "rone": {"months": rone_months,
                 "index": [rone["매매가격지수"].get(m) for m in rone_months],
                 "volume": [rone["아파트매매거래현황"].get(m) for m in rone_months]},
        "vworldKey": env_key() if not a.no_vworld else "",
        "matched": sum(1 for u in units.values() if u["aptSeq"] and u["naver"]),
        "exactArea": round(sum(t["exact"] for t in trades) / len(trades) * 100) if trades else 0,
    }
    tpl = (ROOT / "briefing_template.html").read_text(encoding="utf-8")
    html = tpl.replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")).replace("__DATE__", naver["date"])
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"briefing_{naver['date']}.html"
    write_atomic(out, html)
    write_atomic(out_dir / "index.html", html)                        # 웹에서 바로 열리는 최신본
    # 챗봇(chat/)이 화면과 같은 계산을 하도록 같은 데이터를 저장 (지도용 경계·키 제외)
    chat_payload = {k: v for k, v in payload.items() if k not in ("boundaries", "tracks", "vworldKey")}
    payload_path = Path(a.payload) if a.payload else Path(a.data) / "briefing_payload.json"
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(payload_path, json.dumps(chat_payload, ensure_ascii=False))

    # 콘솔 요약 (기본 필터 기준)
    amin, amax, pmin, pmax = FILTER[0] * PYEONG, FILTER[1] * PYEONG, FILTER[2] * 1e4, FILTER[3] * 1e4
    f_list = sum(1 for u in out_units for x in u["ar"] if amin <= x[1] < amax and pmin <= x[0] <= pmax)
    f_tr = sum(1 for t in trades if amin <= t["supply"] < amax and pmin <= t["amount"] <= pmax)
    k = payload["vworldKey"]
    print(f"생성: {out_dir / 'index.html'} (최신본, 웹 첫 화면)")
    print(f"      {out} (날짜별 보관본)")
    print(f"  브이월드 키: {k[:8] + '…' if k else '없음 → .env 확인 (배경지도는 OSM/Esri로 대체)'}")
    print(f"  단지 {len(units)}개 (공공·네이버 매칭 {payload['matched']}), 좌표 없음 {sum(1 for u in units.values() if u['lat'] is None)}개")
    print(f"  학교 초{len(fac['elementary'])} 중{len(fac['middle'])} 고{len(fac['high'])}, 역 {len(fac['station'])}, 선로 {len(tracks)}구간")
    if not fac["station"] or not fac["elementary"]:
        print("  ! 역·학교 데이터가 비어 있음 → 서버에서 collect_public.py --only facilities 실행 후 data/public 다시 복사")
    print(f"  실거래 전체 {len(trades)}건 ({months[0] if months else '-'}~{months[-1] if months else '-'}), 평형 정확매칭 {payload['exactArea']}%")
    print(f"  현재 매물 전체 {n_list}건")
    rk = [t["rooms"] for t in trades]
    print(f"  방 수: 실거래 {sum(r is not None for r in rk)}/{len(rk)}건 확인"
          + (f" (네이버 캐시에서 평형 {filled}개 보충)" if filled else "")
          + (f", 방 정보 없는 평형 {missing}개" if missing else ""))
    print(f"  기본 필터({FILTER[0]}~{FILTER[1]}평, {FILTER[2]}~{FILTER[3]}억): 매물 {f_list}건, 실거래 {f_tr}건 (거래 건별 기준)")
    rzc = Counter(f"{u['rz']['r']}차 {u['rz']['s']}" for u in out_units if u.get("rz"))
    print("  재건축 구역 표시: " + (", ".join(f"{k} {v}개 단지" for k, v in sorted(rzc.items())) or "없음"))
    yr = [r for r in payload["yearly"] if r["idxChg"] is not None]
    print(f"  연도별 비교: {yr[0]['y'] if yr else '-'}~{yr[-1]['y'] if yr else '-'}년 "
          f"(금리 {'있음' if any(r['base'] for r in payload['yearly']) else '없음 → collect_public.py --only ecos'})")
    if old_naver:
        print(f"  ! 네이버 데이터 {old_naver}개 단지가 24~40평만 수집된 이전 방식 → 전체 매물 수가 작게 나옵니다. "
              f"collect_naver.py 최신본으로 다시 수집하세요 (같은 날이면 data/progress_*.json 삭제 후)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DATA_DIR))
    ap.add_argument("--naver")
    ap.add_argument("--out", default=str(OUT_DIR), help="브리핑 HTML 폴더 (사용자별: output/users/<아이디>)")
    ap.add_argument("--payload", help="챗봇용 데이터 파일 (기본: --data/briefing_payload.json)")
    ap.add_argument("--no-vworld", action="store_true", help="브이월드 대신 Esri 배경지도 사용")
    ap.add_argument("--cache", default=os.environ.get("CACHE_DIR", str(DATA_DIR / "cache")),
                    help="collect_naver.py 의 cache 폴더 (방 수 보충용)")
    build(ap.parse_args())
