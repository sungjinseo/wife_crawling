"""학교알리미 '4-나. 교과별 학업성취 사항'(중학교) 수집 로직"""
import json, os, random, re, time
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

import model

BASE = "https://www.schoolinfo.go.kr"
HEADERS = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest", "Referer": BASE + "/"}
KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
FNDN_NM = {"1": "국립", "2": "공립", "3": "사립"}


# ---------- 지역/연도 코드 (지역별 공시정보 페이지에서 추출, 실패 시 codes.json) ----------
def load_codes():
    try:
        r = requests.get(BASE + "/ei/ss/pneiss_a05_s0.do", headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        t = r.content.decode("euc-kr", errors="ignore")
        sel = lambda i: re.search(r'<select[^>]*id="%s".*?</select>' % i, t, re.S).group(0)
        opt = r'<option value="(\d+)"[^>]*>\s*([^<]+?)\s*</option>'
        func = t[t.index("function setSigungu"):]
        func = func[:func.index("$(\"#sigunguCode\").val(")] if "$(\"#sigunguCode\").val(" in func else func[:300000]
        codes = {
            "sido": [{"code": c, "name": n} for c, n in re.findall(opt, sel("sidoCode"))],
            "sigungu": [{"code": c, "name": n} for c, n in
                        re.findall(r"var addrCdId = '(\d+)';\s*var adrcdIdLastNm = '([^']+)';", func)],
            "years": [c for c, _ in re.findall(opt, sel("gsYear"))],
            "source": "live",
        }
        if codes["sido"] and codes["sigungu"] and codes["years"]:
            return codes
    except Exception:
        pass
    codes = json.load(open(os.path.join(HERE, "codes.json"), encoding="utf-8"))
    codes["source"] = "codes.json"
    return codes


def sigungu_of(codes, sido):
    seen, out = set(), []
    for x in codes["sigungu"]:
        if x["code"][:2] == sido[:2] and x["code"] not in seen:
            seen.add(x["code"]); out.append(x)
    return sorted(out, key=lambda x: x["name"])


# ---------- 학교 목록 ----------
def list_schools(sido, sigungu, year, fndn=("1", "2", "3"), kind="03"):
    data = [("HG_JONGRYU_GB", kind), ("SIDO_CODE", sido), ("SIGUNGU_CODE", sigungu)]
    data += [("SULRIP_GB", g) for g in fndn]
    data += [("GS_HANGMOK_CD", "44"), ("PNF_YR", str(year)), ("JG_HANGMOK_CD", "15")]
    r = requests.post(BASE + "/ei/ss/pneiss_a05_s0/selectSchoolListLocation.do", data=data, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json().get("schoolList", [])


# ---------- 캡차 통과 후 결과 HTML ----------
def fetch_result(shl, year, chasu, retry=5, delay=1.0):
    s = requests.Session(); s.headers.update(HEADERS)
    p1 = {"GS_HANGMOK_CD": "44", "GS_HANGMOK_NO": "4-나", "GS_HANGMOK_NM": "교과별 학업성취 사항",
          "GS_BURYU_CD": "JG220", "JG_BURYU_CD": "JG040", "JG_HANGMOK_CD": "15", "JG_GUBUN": "1",
          "JG_YEAR2": str(year), "HG_NM": shl["SHL_NM"], "SHL_IDF_CD": shl["SHL_IDF_CD"], "GS_TYPE": "Y",
          "JG_YEAR": str(year), "CHOSEN_JG_YEAR": str(year), "PRE_JG_YEAR": str(year), "LOAD_TYPE": "single"}
    r = s.get(BASE + "/ei/pp/Pneipp_b44_s0p.do", params=p1, timeout=30); r.encoding = "euc-kr"
    form = BeautifulSoup(r.text, "html.parser").find("form", id="srcForm")
    if form is None:
        raise RuntimeError("공시 페이지 폼을 찾지 못함")
    fields = [(i["name"], str(chasu) if i["name"] == "JG_CHASU" else i.get("value", ""))
              for i in form.find_all("input", type="hidden")]

    for attempt in range(1, retry + 1):
        img = s.get(BASE + "/captcha/CaptChaImg.jsp",
                    params={"rand": str(random.random()), "gsHangmokCd": "44"}, timeout=30).content
        ans, _ = model.predict(img)
        s.post(BASE + "/ei/pp/Pneipp_b44_s0p.do", data={"passLine": ans}, timeout=30)
        r = s.post(BASE + "/ei/pp/Pneipp_b44_s0p.do", data=fields + [("passLine", ans)], timeout=30)
        r.encoding = "euc-kr"
        if "passLine44" not in r.text:
            return r.text, attempt
        time.sleep(delay)
    raise RuntimeError(f"캡차 {retry}회 실패")


# ---------- 결과 HTML 파싱 ----------
def _num(t):
    t = (t or "").strip()
    try:
        return float(t) if t else None
    except ValueError:
        return None


def parse_records(html):
    """{학년도: [record]} — GRADE_n 표, 행=과목, 열=[1학기 평균,A~E, 2학기 평균,A~E]"""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for div in soup.select("div.schoolinfo_table[id^=GRADE_]"):
        grade = int(div["id"].split("_")[1])
        thead = div.find("thead")
        ym = re.search(r"(\d{4})학년도", thead.get_text() if thead else "")
        year = int(ym.group(1)) if ym else None
        for tr in div.select("tbody tr"):
            th = tr.find("th")
            tds = [_num(td.get_text()) for td in tr.find_all("td")]
            if not th or len(tds) < 12:
                continue
            for term, cells in ((1, tds[0:6]), (2, tds[6:12])):
                dist = {k: v for k, v in zip("ABCDE", cells[1:]) if v is not None}
                if cells[0] is None and not dist:
                    continue
                out.setdefault(year, []).append({
                    "grade": grade, "term": term, "subject": th.get_text(strip=True),
                    "subject_code": th.get("data-sbjt-cd"), "avg": cells[0], "dist": dist})
    for recs in out.values():
        recs.sort(key=lambda x: (x["grade"], x["term"]))
    return out


# ---------- NEIS: 교육지원청명, 표준학교코드 ----------
def neis_info(shl, key=None):
    atpt = (shl.get("CTE_CDDC_CD") or "")[:3]          # 예: J100000001 → J10 (경기)
    params = {"Type": "json", "pIndex": 1, "pSize": 100, "SCHUL_NM": shl["SHL_NM"]}
    if atpt:
        params["ATPT_OFCDC_SC_CODE"] = atpt
    if key:
        params["KEY"] = key
    try:
        rows = requests.get("https://open.neis.go.kr/hub/schoolInfo", params=params, timeout=20).json()["schoolInfo"][1]["row"]
    except Exception:
        return None, None
    rows = [x for x in rows if x["SCHUL_NM"] == shl["SHL_NM"]] or rows
    if len(rows) > 1:
        addr = shl.get("SHL_ROAD_NM_ADDR", "")
        rows = [x for x in rows if x.get("ORG_RDNMA") and x["ORG_RDNMA"] in addr] or rows[:1]
    return rows[0]["JU_ORG_NM"], rows[0]["SD_SCHUL_CODE"]


def collect_school(shl, year, chasu, neis_key=None, retry=5, delay=1.0):
    """한 학교 → (achievement/v1 school 항목 리스트, 캡차 시도 횟수)"""
    html, tries = fetch_result(shl, year, chasu, retry, delay)
    by_year = parse_records(html)
    office, neis = neis_info(shl, neis_key)
    items = [{"name": shl["SHL_NM"], "office": office, "school_id": shl["SHL_CD"], "neis_code": neis,
              "year": y, "records": recs} for y, recs in sorted(by_year.items(), key=lambda kv: kv[0] or 0)]
    return items, tries


def fill_office(schools_out, school_list):
    """NEIS에 없는 학교(폐교 등)는 같은 교육지원청 코드 학교의 office로 보완"""
    dist = {x["SHL_CD"]: x.get("CTE_DSTRT_OFCDC_CD") for x in school_list}
    by_dist = {dist.get(s["school_id"]): s["office"] for s in schools_out if s["office"]}
    for s in schools_out:
        if not s["office"]:
            s["office"] = by_dist.get(dist.get(s["school_id"]))


def new_result():
    return {"schema": "achievement/v1", "collected_at": datetime.now(KST).isoformat(timespec="seconds"), "schools": []}
