"""
캡차 수집 · 라벨링 · 학습 · 테스트 Gradio 앱 (CPU 전용)

데이터 폴더 (/data, 도커 볼륨)
  raw/       수집된 원본 (라벨 없음)
  labeled/   라벨링 완료.  labeled/<n>/<정답>.png  (같은 정답이 중복되면 하위 폴더 번호로 구분)
  trash/     라벨링 중 삭제한 이미지
  model/     weights.h5 + meta.json
"""
import io
import json
import os
import queue
import re
import shutil
import threading
import time
import zipfile
from glob import glob

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import gradio as gr
import numpy as np
import requests
from PIL import Image

DATA = os.environ.get("DATA_DIR", "/data")
RAW = os.path.join(DATA, "raw")
LABELED = os.path.join(DATA, "labeled")
TRASH = os.path.join(DATA, "trash")
MODEL_DIR = os.path.join(DATA, "model")
WEIGHTS = os.path.join(MODEL_DIR, "weights.h5")
META = os.path.join(MODEL_DIR, "meta.json")
for d in (RAW, LABELED, TRASH, MODEL_DIR):
    os.makedirs(d, exist_ok=True)

_model_cache = {"mtime": None, "model": None}
_train_lock = threading.Lock()


# ───────────────────────── 공통 ─────────────────────────
def to_white_l(img):
    """알파 채널(투명 배경)을 흰색으로 채운 뒤 흑백(L)으로 변환.
    실제 캡차는 투명 배경 PNG라서, 변환 없이 학습기/예측기에 넣으면
    투명 부분이 검정(0)으로 읽혀 이미지 전체가 까맣게 됨 → 항상 같은 예측값이 나옴."""
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    return img.convert("L")


def raw_files():
    return sorted(glob(os.path.join(RAW, "*.png")), key=os.path.getmtime)


def labeled_files():
    return glob(os.path.join(LABELED, "**", "*.png"), recursive=True)


def stats():
    return f"미라벨 {len(raw_files())}장 · 라벨 완료 {len(labeled_files())}장"


def get_model():
    """학습된 모델이 있으면 로드 (weights.h5가 바뀌면 다시 로드)"""
    if not (os.path.exists(WEIGHTS) and os.path.exists(META)):
        return None
    mtime = os.path.getmtime(WEIGHTS)
    if _model_cache["mtime"] != mtime:
        import CaptchaCracker as cc
        import tensorflow as tf
        tf.keras.utils.disable_interactive_logging()  # 예측마다 찍히는 1/1 [====] 로그 끄기
        with open(META, encoding="utf-8") as f:
            m = json.load(f)
        _model_cache["model"] = cc.ApplyModel(
            WEIGHTS, m["img_width"], m["img_height"], m["max_length"], set(m["characters"])
        )
        _model_cache["mtime"] = mtime
    return _model_cache["model"]


def predict_raw(path):
    """(예측문자열, 원본출력) 반환. 모델 없으면 None"""
    model = get_model()
    if model is None:
        return None
    tmp = os.path.join(DATA, f"_pred_{threading.get_ident()}.png")
    with Image.open(path) as im:
        to_white_l(im).save(tmp, "PNG")
    try:
        raw = model.predict(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return raw.replace("[UNK]", ""), raw


def predict_path(path):
    try:
        r = predict_raw(path)
    except Exception:
        return ""
    return r[0] if r else ""


# ───────────────────────── 1. 수집 ─────────────────────────
def collect(url, count, delay, progress=gr.Progress()):
    if not url.strip():
        return "URL을 입력하세요.", stats()
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    ok, fail = 0, 0
    for i in progress.tqdm(range(int(count)), desc="수집 중"):
        try:
            r = session.get(url.strip(), timeout=10)
            r.raise_for_status()
            img = to_white_l(Image.open(io.BytesIO(r.content)))
            name = f"img_{int(time.time() * 1000)}_{i}.png"
            img.save(os.path.join(RAW, name), "PNG")  # 학습기가 PNG만 읽으므로 변환 저장
            ok += 1
        except Exception:
            fail += 1
        time.sleep(float(delay))
    return f"완료: 성공 {ok}, 실패 {fail}", stats()


def upload_raw(files):
    """이미 받아둔 이미지(zip 또는 개별 파일)를 미라벨 폴더로 업로드"""
    n = 0
    for f in files or []:
        path = f if isinstance(f, str) else f.name
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                for member in z.namelist():
                    if member.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp")):
                        img = to_white_l(Image.open(io.BytesIO(z.read(member))))
                        img.save(os.path.join(RAW, f"up_{int(time.time()*1000)}_{n}.png"), "PNG")
                        n += 1
        else:
            img = to_white_l(Image.open(path))
            img.save(os.path.join(RAW, f"up_{int(time.time()*1000)}_{n}.png"), "PNG")
            n += 1
    return f"{n}장 추가", stats()


# ───────────────────────── 2. 라벨링 ─────────────────────────
def next_item():
    files = raw_files()
    if not files:
        return None, None, "", "라벨링할 이미지가 없습니다.", stats()
    path = files[0]
    guess = predict_path(path)
    hint = f"모델 예측: {guess} (맞으면 그대로 Enter)" if guess else "학습된 모델 없음"
    return path, path, guess, hint, stats()


def save_label(path, label, pattern):
    label = (label or "").strip()
    if not path or not os.path.exists(path):
        return next_item()
    if pattern and not re.fullmatch(pattern, label):
        return path, path, label, f"⚠️ 형식 불일치: '{label}' (규칙: {pattern})", stats()
    # 같은 정답이 이미 있으면 labeled/1, labeled/2 ... 로 분산 (파일명 = 정답 유지)
    k = 0
    while os.path.exists(os.path.join(LABELED, str(k), f"{label}.png")):
        k += 1
    dst_dir = os.path.join(LABELED, str(k))
    os.makedirs(dst_dir, exist_ok=True)
    shutil.move(path, os.path.join(dst_dir, f"{label}.png"))
    return next_item()


def skip(path):
    # 맨 뒤로 보내기 (mtime 갱신)
    if path and os.path.exists(path):
        os.utime(path, None)
    return next_item()


def trash(path):
    if path and os.path.exists(path):
        shutil.move(path, os.path.join(TRASH, os.path.basename(path)))
    return next_item()


# ───────────────────────── 3. 학습 ─────────────────────────
def train(epochs, width, height, earlystop):
    if not _train_lock.acquire(blocking=False):
        yield "이미 학습이 진행 중입니다."
        return
    try:
        import CaptchaCracker as cc
        import tensorflow as tf
        from tensorflow import keras

        paths = labeled_files()
        if len(paths) < 20:
            yield f"라벨된 이미지가 너무 적습니다 ({len(paths)}장)."
            return
        labels = [os.path.splitext(os.path.basename(p))[0] for p in paths]
        lengths = sorted({len(l) for l in labels})
        if len(lengths) != 1:
            yield f"라벨 길이가 섞여 있습니다: {lengths}. 길이가 다른 파일을 정리하세요."
            return

        # 투명 배경 PNG(실제 수집본)는 학습기가 읽으면 전체가 검정이 됨 → 흰 배경 흑백으로 변환
        fixed = 0
        for p in paths:
            with Image.open(p) as im:
                if im.mode == "L":
                    continue
                conv = to_white_l(im)
            conv.save(p, "PNG")
            fixed += 1
        if fixed:
            yield f"투명/컬러 이미지 {fixed}장을 흰 배경 흑백으로 변환했습니다."

        CM = cc.CreateModel(paths, int(width), int(height))
        log = [f"이미지 {len(paths)}장 · 라벨 길이 {CM.max_length} · "
               f"문자 {len(CM.characters)}종 ({''.join(sorted(CM.characters))})"]
        yield "\n".join(log)

        x_tr, x_va, y_tr, y_va = CM.split_data(np.array(CM.images), np.array(CM.labels))

        def ds(x, y):
            return (tf.data.Dataset.from_tensor_slices((x, y))
                    .map(CM.encode_single_sample, num_parallel_calls=tf.data.AUTOTUNE)
                    .batch(16).prefetch(tf.data.AUTOTUNE))

        model = CM.build_model()
        q = queue.Queue()
        cbs = [keras.callbacks.LambdaCallback(
            on_epoch_end=lambda e, l: q.put(
                f"epoch {e + 1}/{int(epochs)}  loss {l['loss']:.3f}  val_loss {l['val_loss']:.3f}"))]
        if earlystop:
            cbs.append(keras.callbacks.EarlyStopping(
                monitor="val_loss", patience=30, restore_best_weights=True))

        err = {}

        def run():
            try:
                model.fit(ds(x_tr, y_tr), validation_data=ds(x_va, y_va),
                          epochs=int(epochs), callbacks=cbs, verbose=0)
            except Exception as e:
                err["e"] = e
            q.put(None)

        threading.Thread(target=run, daemon=True).start()
        while (msg := q.get()) is not None:
            log.append(msg)
            yield "\n".join(log[-30:])
        if "e" in err:
            log.append(f"❌ 오류: {err['e']}")
            yield "\n".join(log[-30:])
            return

        model.save_weights(WEIGHTS)
        with open(META, "w", encoding="utf-8") as f:
            json.dump({"characters": sorted(CM.characters), "max_length": CM.max_length,
                       "img_width": int(width), "img_height": int(height)}, f,
                      ensure_ascii=False, indent=2)

        # 검증셋 정확도 (학습에 안 쓴 10%)
        am = get_model()
        hit = sum(am.predict(p) == y for p, y in zip(x_va, y_va))
        with open(META, encoding="utf-8") as f:
            meta = json.load(f)
        meta.update({"train_images": len(paths), "val_acc": round(hit / max(len(x_va), 1), 4),
                     "epochs_run": len(log) - 1, "trained_at": time.strftime("%Y-%m-%d %H:%M")})
        with open(META, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        log.append(f"✅ 저장 완료. 검증셋 정확도 {hit}/{len(x_va)} = {hit / max(len(x_va), 1):.1%}")
        yield "\n".join(log[-30:])
    finally:
        _train_lock.release()


def export_model():
    if not os.path.exists(WEIGHTS):
        return None
    out = os.path.join(DATA, "model_export.zip")
    with zipfile.ZipFile(out, "w") as z:
        z.write(WEIGHTS, "weights.h5")
        z.write(META, "meta.json")
    return out


def export_labeled():
    out = os.path.join(DATA, "labeled_export.zip")
    with zipfile.ZipFile(out, "w") as z:
        for p in labeled_files():
            z.write(p, os.path.relpath(p, LABELED))
    return out


# ───────────────────────── 4. 테스트 ─────────────────────────
def import_model(files):
    """weights.h5 + meta.json (또는 이 둘이 든 zip)을 받아 /data/model 에 설치.
    기존 모델은 model/backup_<시각>/ 으로 백업, 로드 실패 시 자동 복원."""
    if not files:
        return "파일을 선택하세요.", model_info()
    paths = [f if isinstance(f, str) else f.name for f in files]
    weights = meta = None
    for p in paths:
        if p.lower().endswith(".zip"):
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    if n.lower().endswith(".h5"):
                        weights = z.read(n)
                    elif n.lower().endswith(".json"):
                        meta = z.read(n)
        elif p.lower().endswith(".h5"):
            weights = open(p, "rb").read()
        elif p.lower().endswith(".json"):
            meta = open(p, "rb").read()
    if weights is None or meta is None:
        return "❌ weights.h5 와 meta.json 이 둘 다 필요합니다 (또는 둘이 든 zip).", model_info()
    try:
        m = json.loads(meta)
        missing = {"characters", "max_length", "img_width", "img_height"} - m.keys()
        if missing:
            return f"❌ meta.json 에 {sorted(missing)} 항목이 없습니다.", model_info()
    except Exception as e:
        return f"❌ meta.json 읽기 실패: {e}", model_info()

    backup = None
    if os.path.exists(WEIGHTS):
        backup = os.path.join(MODEL_DIR, "backup_" + time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(backup)
        for f in (WEIGHTS, META):
            if os.path.exists(f):
                shutil.move(f, backup)
    with open(WEIGHTS, "wb") as f:
        f.write(weights)
    with open(META, "wb") as f:
        f.write(meta)
    _model_cache["mtime"] = None
    try:
        get_model()
    except Exception as e:
        for f in (WEIGHTS, META):
            if os.path.exists(f):
                os.remove(f)
        if backup:
            for f in os.listdir(backup):
                shutil.move(os.path.join(backup, f), MODEL_DIR)
            os.rmdir(backup)
        _model_cache["mtime"] = None
        return f"❌ 모델 로드 실패 (기존 모델 복원함): {type(e).__name__}: {e}", model_info()
    msg = "✅ 모델 설치 완료" + (f" · 기존 모델은 {os.path.basename(backup)}/ 에 백업" if backup else "")
    return msg, model_info()


def model_info():
    if not os.path.exists(META):
        return "학습된 모델 없음 → 3. 학습 탭에서 먼저 학습하세요."
    with open(META, encoding="utf-8") as f:
        m = json.load(f)
    acc = f"{m['val_acc']:.1%}" if "val_acc" in m else "?"
    return (f"모델: {m.get('trained_at', '?')} 학습 · 이미지 {m.get('train_images', '?')}장 · "
            f"검증 정확도 {acc} · {m['max_length']}자리 · 입력 {m['img_width']}×{m['img_height']}")


def test_one(img_path):
    return (*_test_one(img_path), model_info())


def _test_one(img_path):
    if img_path is None:
        return "", ""
    if get_model() is None:
        return "", model_info()
    try:
        pred, raw = predict_raw(img_path)
    except Exception as e:
        return "", f"❌ 예측 오류: {type(e).__name__}: {e}"
    if not pred:
        return "", (f"⚠️ 모델이 아무 글자도 인식 못함 (원본 출력: {raw})\n"
                    f"→ 아직 학습이 덜 된 상태입니다. 데이터를 늘리고 다시 학습하세요.\n{model_info()}")
    return pred, f"원본 출력: {raw}\n{model_info()}"


# ───────────────────────── UI ─────────────────────────
with gr.Blocks(title="캡차 라벨러") as demo:
    gr.Markdown("## 캡차 수집 · 라벨링 · 학습 (CPU)")
    status = gr.Markdown(stats())

    with gr.Tab("1. 수집"):
        url = gr.Textbox(label="캡차 이미지 URL")
        with gr.Row():
            count = gr.Number(label="수집 개수", value=100, precision=0)
            delay = gr.Number(label="요청 간격(초)", value=1.0)
        btn_collect = gr.Button("수집 시작", variant="primary")
        collect_msg = gr.Markdown()
        gr.Markdown("---\n이미 받아둔 이미지가 있으면 업로드 (png/jpg 또는 zip)")
        up = gr.File(file_count="multiple")
        btn_up = gr.Button("업로드")
        btn_collect.click(collect, [url, count, delay], [collect_msg, status])
        btn_up.click(upload_raw, [up], [collect_msg, status])

    with gr.Tab("2. 라벨링"):
        cur = gr.State()
        img = gr.Image(type="filepath", label="현재 이미지", height=160, interactive=False, image_mode="RGBA")
        hint = gr.Markdown()
        with gr.Row():
            label = gr.Textbox(label="정답 (Enter = 저장)", scale=3)
            pattern = gr.Textbox(label="형식 검사 정규식", value=r"\d{6}", scale=1)
        with gr.Row():
            b_save = gr.Button("저장", variant="primary")
            b_skip = gr.Button("건너뛰기")
            b_del = gr.Button("삭제(판독불가)")
            b_load = gr.Button("불러오기 / 새로고침")
        outs = [cur, img, label, hint, status]
        b_load.click(next_item, None, outs)
        b_save.click(save_label, [cur, label, pattern], outs)
        label.submit(save_label, [cur, label, pattern], outs)
        b_skip.click(skip, [cur], outs)
        b_del.click(trash, [cur], outs)

    with gr.Tab("3. 학습"):
        with gr.Row():
            ep = gr.Number(label="epochs", value=100, precision=0)
            w = gr.Number(label="입력 너비", value=152, precision=0)
            h = gr.Number(label="입력 높이", value=64, precision=0)
            es = gr.Checkbox(label="EarlyStopping", value=True)
        gr.Markdown("너비·높이는 4의 배수 권장. 실제 캡차가 150×66이라 기본값 152×64")
        b_train = gr.Button("학습 시작", variant="primary")
        train_log = gr.Textbox(label="로그", lines=15)
        b_train.click(train, [ep, w, h, es], train_log)
        with gr.Row():
            b_exp_m = gr.Button("모델 내보내기")
            b_exp_l = gr.Button("라벨 데이터 내보내기")
        exp_file = gr.File(label="다운로드")
        b_exp_m.click(export_model, None, exp_file)
        b_exp_l.click(export_labeled, None, exp_file)

    with gr.Tab("4. 테스트"):
        t_info = gr.Markdown(model_info())
        with gr.Accordion("모델 가져오기 (weights.h5 + meta.json 또는 zip)", open=False):
            m_files = gr.File(file_count="multiple", file_types=[".zip", ".h5", ".json"])
            m_btn = gr.Button("이 모델로 교체")
            m_msg = gr.Markdown()
            m_btn.click(import_model, m_files, [m_msg, t_info])
        # image_mode 기본값이 "RGB"라 업로드 순간 알파 채널이 버려져 이미지가 전부 검정이 됨 → RGBA 유지
        t_img = gr.Image(type="filepath", label="캡차 이미지", image_mode="RGBA")
        t_out = gr.Textbox(label="예측")
        t_detail = gr.Textbox(label="상세", lines=3)
        t_img.change(test_one, t_img, [t_out, t_detail, t_info])

    demo.load(lambda: (stats(), model_info()), None, [status, t_info])

if __name__ == "__main__":
    demo.queue().launch(server_name="0.0.0.0", server_port=7860)
