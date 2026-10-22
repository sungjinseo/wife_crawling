"""학교알리미 중학교 교과별 학업성취 수집기 (Gradio)"""
import json, os, time
from datetime import datetime

import gradio as gr

import collector as C

OUT_DIR = os.environ.get("OUT_DIR", "/app/outputs" if os.path.isdir("/app") else "./outputs")
os.makedirs(OUT_DIR, exist_ok=True)
CODES = C.load_codes()
SIDO = [(x["name"], x["code"]) for x in CODES["sido"]]
DEFAULT_SIDO = "4100000000" if any(c == "4100000000" for _, c in SIDO) else SIDO[0][1]
NAME = {x["code"]: x["name"] for x in CODES["sido"] + CODES["sigungu"]}
CHASU = [("1차 (4월) · 전년도 1·2학기 성적", "1"), ("3차 (9월) · 당해 1학기 성적", "3")]


def sigungu_choices(sido):
    return [(x["name"], x["code"]) for x in C.sigungu_of(CODES, sido)]


def on_sido(sido):
    ch = sigungu_choices(sido)
    return gr.update(choices=ch, value=[ch[0][1]] if ch else [])


def load_schools(sido, sigungus, fndn, year):
    if not sigungus:
        raise gr.Error("시·군·구를 하나 이상 선택하세요.")
    if not fndn:
        raise gr.Error("설립구분을 하나 이상 선택하세요.")
    found = {}
    for sg in sigungus:
        for s in C.list_schools(sido, sg, year, fndn):
            found.setdefault(s["SHL_CD"], s)          # 시 + 구 중복 선택 시 중복 제거
    schools = sorted(found.values(), key=lambda s: s["SHL_NM"])
    choices = [(f"{s['SHL_NM']} ({C.FNDN_NM.get(s['FNDN_SC_CD'], '?')})", s["SHL_CD"]) for s in schools]
    msg = f"{len(schools)}개 학교를 찾았습니다. 수집할 학교를 고른 뒤 '수집 시작'을 누르세요."
    return gr.update(choices=choices, value=[c for _, c in choices]), schools, msg


def run(sido, sigungus, year, chasu, schools, selected, delay, retry, neis_key):
    targets = [s for s in (schools or []) if s["SHL_CD"] in set(selected or [])]
    if not targets:
        raise gr.Error("먼저 '학교 목록 불러오기'를 하고 학교를 선택하세요.")

    region = "_".join(NAME.get(c, c).replace(" ", "") for c in [sido] + list(sigungus or [])[:2])
    path = os.path.join(OUT_DIR, f"achievement_{region}_{year}_{chasu}차_{datetime.now():%Y%m%d_%H%M%S}.json")
    result = C.new_result()
    result["query"] = {"sido": sido, "sigungu": list(sigungus or []), "pnf_year": int(year), "chasu": int(chasu)}
    rows, log = [], []

    def save():
        C.fill_office(result["schools"], targets)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    for i, shl in enumerate(targets, 1):
        name = shl["SHL_NM"]
        try:
            items, tries = C.collect_school(shl, year, chasu, neis_key or os.environ.get("NEIS_KEY"),
                                            int(retry), float(delay))
            result["schools"].extend(items)
            n = sum(len(x["records"]) for x in items)
            years = ", ".join(str(x["year"]) for x in items) or "-"
            status = "완료" if n else "성적 없음"
            rows.append([name, years, n, tries, status])
            log.append(f"[{i}/{len(targets)}] {name}: {n}건 (학년도 {years}, 캡차 {tries}회)")
        except Exception as e:
            rows.append([name, "-", 0, int(retry), f"실패: {e}"])
            log.append(f"[{i}/{len(targets)}] {name}: 실패 - {e}")
        save()                                         # 중간에 멈춰도 그때까지 받은 파일 유지
        yield "\n".join(log[-200:]), rows, path
        time.sleep(float(delay))

    ok = sum(1 for r in rows if r[4] == "완료")
    log.append(f"\n끝: {ok}/{len(targets)}개 학교, 레코드 {sum(r[2] for r in rows)}건 → {os.path.basename(path)}")
    yield "\n".join(log[-200:]), rows, path


with gr.Blocks(title="학교알리미 학업성취 수집기") as demo:
    gr.Markdown("## 학교알리미 · 중학교 교과별 학업성취 사항 수집\n"
                f"지역 코드: 학교알리미 지역별 공시정보 페이지 ({'실시간' if CODES['source'] == 'live' else '내장 codes.json'})")
    schools_state = gr.State([])

    with gr.Row():
        sido = gr.Dropdown(SIDO, value=DEFAULT_SIDO, label="시·도")
        sg0 = sigungu_choices(DEFAULT_SIDO)
        sigungu = gr.Dropdown(sg0, value=["4113500000"] if any(c == "4113500000" for _, c in sg0) else [sg0[0][1]],
                              multiselect=True, label="시·군·구 (여러 개 선택 가능)")
    with gr.Row():
        fndn = gr.CheckboxGroup([("국립", "1"), ("공립", "2"), ("사립", "3")], value=["1", "2", "3"], label="설립구분")
        year = gr.Dropdown(CODES["years"], value=CODES["years"][0], label="공시년도")
        chasu = gr.Radio(CHASU, value="1", label="공시차수")

    with gr.Accordion("고급 설정", open=False):
        with gr.Row():
            delay = gr.Slider(0.5, 5, value=1.0, step=0.5, label="요청 간격(초)")
            retry = gr.Slider(1, 10, value=5, step=1, label="캡차 재시도")
            neis_key = gr.Textbox(label="NEIS Open API 키 (선택, 환경변수 NEIS_KEY도 가능)", type="password")

    btn_list = gr.Button("1. 학교 목록 불러오기")
    msg = gr.Markdown()
    school_sel = gr.CheckboxGroup([], label="수집할 학교")

    with gr.Row():
        btn_run = gr.Button("2. 수집 시작", variant="primary")
        btn_stop = gr.Button("중지")

    with gr.Row():
        log = gr.Textbox(label="진행 로그", lines=14, max_lines=14, autoscroll=True)
        table = gr.Dataframe(headers=["학교", "학년도", "레코드", "캡차 시도", "상태"], label="결과", wrap=True)
    out_file = gr.File(label="JSON 다운로드 (achievement/v1)")

    sido.change(on_sido, sido, sigungu)
    btn_list.click(load_schools, [sido, sigungu, fndn, year], [school_sel, schools_state, msg])
    ev = btn_run.click(run, [sido, sigungu, year, chasu, schools_state, school_sel, delay, retry, neis_key],
                       [log, table, out_file])
    btn_stop.click(None, cancels=[ev])

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(
        server_name="0.0.0.0", server_port=int(os.environ.get("PORT", 7860)), allowed_paths=[OUT_DIR])
