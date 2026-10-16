"""브리핑 화면(briefing_template.html)의 계산을 파이썬으로 그대로 옮긴 것.

build_briefing.py 가 쓰는 data/briefing_payload.json (화면에 넣는 것과 같은 데이터)을 읽어
computeUnits / computeMetrics / agg / computeStats / leadText 와 같은 규칙으로 계산합니다.
→ 챗봇 도구가 내는 숫자가 화면 숫자와 일치합니다.

JS 와 규칙을 바꿀 때는 두 곳을 같이 고치세요 (아래 함수 이름이 JS 함수와 대응).
"""
import json
import math
from pathlib import Path
from statistics import median as _median

PY = 3.3058
SCOPES = ("filt", "area", "all")
SCOPE_NAME = {"filt": "필터 조건", "area": "평형·방만", "all": "전체"}
KINDS = ("station", "elementary", "middle", "high")


def med(xs):
    xs = [x for x in xs if x is not None]
    return _median(xs) if xs else None


def js_round(x):
    """JS Math.round (0.5 는 올림)"""
    return math.floor(x + 0.5)


def dist(a, b):
    r = math.pi / 180
    dlat, dlng = (b["lat"] - a["lat"]) * r, (b["lng"] - a["lng"]) * r
    h = math.sin(dlat / 2) ** 2 + math.cos(a["lat"] * r) * math.cos(b["lat"] * r) * math.sin(dlng / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


class Dashboard:
    def __init__(self, payload_path: Path):
        self.path = Path(payload_path)
        self.mtime = None
        self.D = None
        self._cache = {}

    def load(self):
        st = self.path.stat()
        if self.mtime != st.st_mtime:
            self.D = json.loads(self.path.read_text(encoding="utf-8"))
            self.mtime = st.st_mtime
            self._cache = {}
        return self.D

    # ---------- computeUnits: 접근점수·등급·입지 유형 (화면 설정 CFG 별로 캐시) ----------
    def grades(self, cfg=None):
        D = self.load()
        base = D.get("defaults") or {}
        cfg = {**base, **{k: v for k, v in (cfg or {}).items() if v is not None}}
        key = "cfg:" + json.dumps(cfg, sort_keys=True, ensure_ascii=False)
        if key in self._cache:
            return self._cache[key]
        # 화면에서 추가·제외한 시설(localStorage 'custom')도 화면과 같이 반영 (buildFacilities)
        custom = cfg.pop("custom", None) or {}
        ex = {(x.get("kind"), x.get("name")) for x in custom.get("excluded", [])}
        FAC = {}
        for k in KINDS:
            b = [p for p in (D.get("facilities") or {}).get(k, []) if (k, p["name"]) not in ex]
            names = {p["name"] for p in b}
            b += [x for x in custom.get("added", []) if x.get("kind") == k and x.get("name") not in names
                  and (k, x.get("name")) not in ex and x.get("lat") is not None]
            FAC[k] = b
        walk, weight = cfg.get("walk", {}), cfg.get("weight", {})
        full_r, zero_r, cuts = cfg.get("fullRatio", .6), cfg.get("zeroRatio", 1.5), cfg.get("cuts", [60, 30])
        avail = [k for k in KINDS if FAC.get(k) and weight.get(k, 0) > 0]
        wsum = sum(weight[k] for k in avail)
        out = {}
        for u in D["units"]:
            g = {"near": {}, "score": None, "grade": None, "type": None}
            out[u["id"]] = g
            if u.get("lat") is None or not avail:
                continue
            sc = 0
            for k in KINDS:
                if not FAC.get(k):
                    continue
                best, bd = None, float("inf")
                for p in FAC[k]:
                    d = dist(u, p)
                    if d < bd:
                        bd, best = d, p
                g["near"][k] = [best["name"], js_round(bd)]
                if k in avail:
                    full, zero = walk[k] * full_r, walk[k] * zero_r
                    sc += min(1, max(0, (zero - bd) / (zero - full))) * weight[k]
            g["score"] = js_round(sc / wsum * 100)
            g["grade"] = "상" if g["score"] >= cuts[0] else "중" if g["score"] >= cuts[1] else "하"
            st_ = g["near"].get("station") and g["near"]["station"][1] <= walk["station"]
            el = g["near"].get("elementary") and g["near"]["elementary"][1] <= walk["elementary"]
            g["type"] = "역+초교" if st_ and el else "역세권" if st_ else "초교 인접" if el else "일반"
        self._cache[key] = out
        return out

    # ---------- computeMetrics ----------
    def metrics(self, flt):
        """flt: {areaMin, areaMax, priceMin, priceMax, rooms}  (화면 FILTER 와 같은 이름·단위: 평, 억)"""
        D = self.load()
        key = json.dumps(flt, sort_keys=True)
        if key in self._cache:
            return self._cache[key]
        n = len(D["months"])
        cur = n - 1
        in6 = lambda i: n - 7 <= i < cur
        in3 = lambda i: n - 4 <= i < cur
        prev6 = lambda i: n - 13 <= i < n - 7
        a_min, a_max = flt["areaMin"] * PY, flt["areaMax"] * PY
        p_min, p_max = flt["priceMin"] * 1e4, flt["priceMax"] * 1e4
        rooms = flt.get("rooms") or []
        # 정보가 없으면(방 수·세대수·년차 미상) 조건으로 거르지 않고 포함 (화면 roomOK·between 과 같음)
        between = lambda x, lo, hi: x is None or ((lo is None or x >= lo) and (hi is None or x <= hi))
        unit_ok = lambda u: (between(u.get("households"), flt.get("hhMin"), flt.get("hhMax"))
                             and between(u.get("age"), flt.get("ageMin"), None))         # 년차는 'N년차 이상'만
        area_ok = lambda sup: a_min <= sup < a_max
        price_ok = lambda p: p_min <= p <= p_max
        room_ok = lambda r: not rooms or r is None or min(r, 4) in rooms
        res = {}
        for u in D["units"]:
            segP, segL = {}, {}
            for t in u["tr"]:
                if in6(t[0]):
                    segP.setdefault(js_round(t[2]), []).append(t[1])
            for x in u["ar"]:
                if x[2]:
                    segL.setdefault(js_round(x[2]), []).append(x[0])

            def seg_ok(ex):
                k = js_round(ex)
                v = med(segP[k]) if k in segP else med(segL[k]) if k in segL else None
                return v is not None and price_ok(v)

            u_ok = unit_ok(u)                           # '필터 조건' 보기에만 적용
            tr_in = {"filt": [], "area": [], "all": []}
            for t in u["tr"]:
                a = area_ok(t[3]) and room_ok(t[6] if len(t) > 6 else None)
                tr_in["all"].append(t)
                if a:
                    tr_in["area"].append(t)
                if a and u_ok and seg_ok(t[2]):
                    tr_in["filt"].append(t)
            m = {}
            for sc in SCOPES:
                arts = [x for x in u["ar"] if sc == "all" or (area_ok(x[1]) and room_ok(x[7] if len(x) > 7 else None)
                                                              and (sc == "area" or (u_ok and price_ok(x[0]))))]
                trs = tr_in[sc]
                hh = 0 if sc == "filt" and not u_ok else \
                    sum((x[2] or 0) for x in u["py"] if sc == "all" or (area_ok(x[0]) and room_ok(x[4] if len(x) > 4 else None)))
                lp = [p / (sup / PY) for p, sup, *_ in arts]
                tp = [t[1] / (t[3] / PY) for t in trs if in6(t[0])]
                L, T = med(lp), med(tp)
                now6 = sum(1 for t in trs if in6(t[0]))
                bef6 = sum(1 for t in trs if prev6(t[0]))
                m[sc] = {"households": hh, "listings": len(arts), "trades3": sum(1 for t in trs if in3(t[0])),
                         "listPpp": L, "tradePpp": T, "gap": round((L / T - 1) * 100, 1) if L and T else None,
                         "days": [x[3] for x in arts if x[3] is not None], "realtors": [x[4] or 1 for x in arts],
                         "listRange": [min(x[0] for x in arts), max(x[0] for x in arts)] if arts else None,
                         "unkRoomL": sum(1 for x in arts if (x[7] if len(x) > 7 else None) is None),
                         "now6": now6, "bef6": bef6, "trend": "up" if now6 > bef6 else "down" if now6 < bef6 else "flat"}
            res[u["id"]] = {"trIn": tr_in, "m": m,
                            "match": m["filt"]["listings"] > 0 or any(t[0] >= n - 13 for t in tr_in["filt"])}
        self._cache[key] = res
        return res

    # ---------- agg / computeStats ----------
    def agg(self, M, group, sc):
        c = lambda k, s=sc: sum(M[u["id"]]["m"][s][k] or 0 for u in group)
        hh, ls, t3 = c("households"), c("listings"), c("trades3")
        days = [d for u in group for d in M[u["id"]]["m"][sc]["days"]]
        rl = [r for u in group for r in M[u["id"]]["m"][sc]["realtors"]]
        gaps = [M[u["id"]]["m"][sc]["gap"] for u in group if M[u["id"]]["m"][sc]["gap"] is not None]
        lp = [M[u["id"]]["m"][sc]["listPpp"] for u in group if M[u["id"]]["m"][sc]["listPpp"]]
        return {"units": len(group), "households": hh, "listings": ls, "listingsAll": c("listings", "all"),
                "trades3": t3, "absorb": round(t3 / 3 / ls * 100, 1) if ls else None,
                "listingRate": round(ls / hh * 100, 2) if hh else None,
                "listPpp": js_round(med(lp)) if lp else None, "gap": round(med(gaps), 1) if gaps else None,
                "days": js_round(med(days)) if days else None,
                "realtors": round(sum(rl) / len(rl), 1) if rl else None}

    def series(self, M, group, sc, grade=None, G=None):
        n = len(self.D["months"])
        cnt, pp = [0] * n, [[] for _ in range(n)]
        for u in group:
            if grade and G[u["id"]]["grade"] != grade:
                continue
            for t in M[u["id"]]["trIn"][sc]:
                cnt[t[0]] += 1
                pp[t[0]].append(t[1] / (t[3] / PY))
        return {"count": cnt, "ppp": [js_round(med(a)) if a else None for a in pp]}

    def sum_last(self, arr, k, off=0):
        a = arr[: len(arr) - self.D["provisional"]]
        return sum(x or 0 for x in a[max(0, len(a) - off - k): len(a) - off])

    # ---------- leadText 의 숫자 ----------
    def lead(self, dong, scope, flt, cfg=None):
        D = self.load()
        M = self.metrics(flt)
        G = self.grades(cfg)
        grp = [u for u in D["units"] if dong == "전체" or u["dong"] == dong]
        s = self.series(M, grp, scope)
        s_all = self.series(M, grp, "all")
        S = self.agg(M, grp, scope)
        now3, prev3 = self.sum_last(s["count"], 3), self.sum_last(s["count"], 3, 3)
        tops = [self.sum_last(self.series(M, grp, scope, g, G)["count"], 3) for g in ("상", "중", "하")]
        all3, all_prev3 = self.sum_last(s_all["count"], 3), self.sum_last(s_all["count"], 3, 3)
        types = {}
        for u in grp:
            t = G[u["id"]]["type"]
            if t:
                types[t] = types.get(t, 0) + 1
        months = D["months"][: len(D["months"]) - D["provisional"]]
        fmt = lambda ym: f"{ym[:4]}-{ym[4:]}"
        out = {
            "지역": "5개 동" if dong == "전체" else dong, "보기": SCOPE_NAME[scope],
            "기간": f"최근 3개월 {fmt(months[-3])}~{fmt(months[-1])} vs 직전 3개월 {fmt(months[-6])}~{fmt(months[-4])} "
                  f"(그 뒤 {D['provisional']}개월은 신고 지연으로 잠정 제외)",
            "전체_거래": {"최근3개월_건수": all3, "직전3개월_건수": all_prev3,
                       "건수_변동률_%": js_round((all3 / all_prev3 - 1) * 100) if all_prev3 else None},
            "매물": {"전체": S["listingsAll"], "실매매율_%": S["absorb"], "호가괴리_%": S["gap"],
                   "실매매율_정의": "최근 3개월 월평균 거래 ÷ 현재 매물", "호가괴리_정의": "현재 평당 호가 ÷ 최근 6개월 평당 실거래가 − 1"},
            "입지_단지수": types,
        }
        if scope != "all":
            out["조건_거래"] = {"최근3개월_건수": now3, "전체대비_비중_%": js_round(now3 / all3 * 100) if all3 else None,
                            "직전3개월_건수": prev3, "건수_변동률_%": js_round((now3 / prev3 - 1) * 100) if prev3 else None,
                            "접근상위_단지_거래비중_%": js_round(tops[0] / sum(tops) * 100) if sum(tops) else None}
            out["매물"]["조건"] = S["listings"]
            out["매물"]["조건_비중_%"] = js_round(S["listings"] / S["listingsAll"] * 100) if S["listingsAll"] else None
        else:
            out["전체_거래"]["접근상위_단지_거래비중_%"] = js_round(tops[0] / sum(tops) * 100) if sum(tops) else None
        # 정보 없음으로 포함된 것 (화면 '확인' 줄과 같음)
        if scope != "all":
            room_on = bool(flt.get("rooms"))
            hh_on = scope == "filt" and (flt.get("hhMin") is not None or flt.get("hhMax") is not None)
            n_conf = len(D["months"]) - D["provisional"]
            unk = {}
            if room_on:
                unk["방정보없음_최근3개월_거래"] = sum(1 for u in grp for t in M[u["id"]]["trIn"][scope]
                                            if (t[6] if len(t) > 6 else None) is None and n_conf - 3 <= t[0] < n_conf)
                unk["방정보없음_매물"] = sum(M[u["id"]]["m"][scope]["unkRoomL"] for u in grp)
            if hh_on:
                unk["세대수정보없음_단지"] = sum(1 for u in grp if u.get("households") is None
                                         and (M[u["id"]]["m"]["filt"]["listings"] > 0 or M[u["id"]]["trIn"]["filt"]))
            if any(unk.values()):
                out["정보없음_포함"] = {**unk, "설명": "방 수·세대수 정보가 없어 조건으로 판단하지 못한 거래·매물·단지. 빼지 않고 포함했으니 사람이 확인 필요"}
        out["주의"] = "건수_변동률은 거래 '건수'의 변화입니다. 가격 변화는 screen_price_change 로 확인하세요."
        return out

    # ---------- 화면 기준 가격 변화 (12개월 등) ----------
    def price_change(self, dong, scope, flt, months=12, cfg=None):
        """화면과 같은 필터·범위로 평당가(공급평 기준) 중앙값 변화. 잠정 2개월 제외."""
        D = self.load()
        M = self.metrics(flt)
        G = self.grades(cfg)
        grp = [u for u in D["units"] if dong == "전체" or u["dong"] == dong]
        n = len(D["months"])
        end = n - D["provisional"]                      # 확정 구간 끝(미포함)
        rec = range(max(0, end - months), end)
        prv = range(max(0, end - 2 * months), max(0, end - months))
        pick = lambda rng: [(u, t) for u in grp for t in M[u["id"]]["trIn"][scope] if t[0] in rng]
        R, P = pick(rec), pick(prv)
        ppp = lambda xs: [t[1] / (t[3] / PY) for _, t in xs]
        fmt = lambda i: f"{D['months'][i][:4]}-{D['months'][i][4:]}"
        out = {"지역": "5개 동" if dong == "전체" else dong, "보기": SCOPE_NAME[scope],
               "기간": f"최근 {months}개월 {fmt(rec[0])}~{fmt(rec[-1])} vs 직전 {fmt(prv[0]) if len(prv) else '-'}~{fmt(prv[-1]) if len(prv) else '-'}"
                     + (" (데이터가 36개월뿐이라 직전 기간이 짧음)" if len(prv) < months else ""),
               "최근_건수": len(R), "직전_건수": len(P),
               "최근_평당가_중앙값_만원": js_round(med(ppp(R))) if R else None,
               "직전_평당가_중앙값_만원": js_round(med(ppp(P))) if P else None}
        if R and P:
            out["평당가_변동률_%"] = round((med(ppp(R)) / med(ppp(P)) - 1) * 100, 1)
            out["건수_변동률_%"] = round((len(R) / len(P) - 1) * 100, 1)
        # 단지별 (양쪽 기간 2건 이상)
        rows = []
        for u in grp:
            r = [t[1] / (t[3] / PY) for t in M[u["id"]]["trIn"][scope] if t[0] in rec]
            p = [t[1] / (t[3] / PY) for t in M[u["id"]]["trIn"][scope] if t[0] in prv]
            if len(r) >= 2 and len(p) >= 2:
                rows.append({"단지": u["name"], "동": u["dong"], "년차": u.get("age"), "입지": G[u["id"]]["type"],
                             "직전_건수": len(p), "최근_건수": len(r),
                             "평당가_변동률_%": round((med(r) / med(p) - 1) * 100, 1)})
        if rows:
            w = sum(x["최근_건수"] for x in rows)
            out["동일단지_가중평균_평당가_변동률_%"] = round(sum(x["평당가_변동률_%"] * x["최근_건수"] for x in rows) / w, 1)
            out["상승단지_비율"] = f"{sum(x['평당가_변동률_%'] > 0 for x in rows)}/{len(rows)}"
        rows.sort(key=lambda x: x["평당가_변동률_%"], reverse=True)
        out["상승_상위단지"] = rows[:5]
        out["상승_하위단지"] = rows[-3:] if len(rows) > 5 else []
        path = self.series(M, grp, scope)
        out["월별"] = [{"월": fmt(i), "건수": path["count"][i], "평당가_중앙값_만원": path["ppp"][i],
                       "잠정": i >= end} for i in range(max(0, end - 2 * months), n)]
        out["주의"] = "평당가는 공급평 기준(화면과 같음). 건수와 가격 변화를 구분해서 말할 것."
        return out

    def unit_detail(self, name_or_id, flt, cfg=None):
        D = self.load()
        M = self.metrics(flt)
        G = self.grades(cfg)
        q = str(name_or_id).replace(" ", "")
        cands = [u for u in D["units"] if u["id"] == name_or_id or q in u["name"].replace(" ", "")]
        if not cands:
            return {"error": f"'{name_or_id}' 단지를 찾지 못했습니다."}
        if len(cands) > 1 and not any(u["name"].replace(" ", "") == q for u in cands):
            return {"ambiguous": True, "candidates": [{"id": u["id"], "단지": u["name"], "동": u["dong"]} for u in cands[:8]]}
        u = next((u for u in cands if u["name"].replace(" ", "") == q), cands[0])
        m = M[u["id"]]["m"]
        f = lambda s: {"매물": m[s]["listings"], "최근3개월_거래": m[s]["trades3"], "평당호가_만원": js_round(m[s]["listPpp"]) if m[s]["listPpp"] else None,
                       "평당실거래_6개월_만원": js_round(m[s]["tradePpp"]) if m[s]["tradePpp"] else None, "호가괴리_%": m[s]["gap"],
                       "호가범위_억": [round(m[s]["listRange"][0] / 1e4, 2), round(m[s]["listRange"][1] / 1e4, 2)] if m[s]["listRange"] else None,
                       "최근6개월_vs_직전6개월_건수": f"{m[s]['now6']}/{m[s]['bef6']}"}
        return {"단지": u["name"], "동": u["dong"], "년차": u.get("age"), "세대수": u.get("households"),
                "입지": G[u["id"]]["type"], "접근점수": G[u["id"]]["score"], "접근등급": G[u["id"]]["grade"],
                "가까운_시설": {k: f"{v[0]} {v[1]}m" for k, v in G[u["id"]]["near"].items()},
                "필터_조건": f("filt"), "전체": f("all")}
