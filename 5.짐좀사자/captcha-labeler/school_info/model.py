"""캡차 숫자 인식 (CRNN + CTC). weights.h5 는 Keras 2.15 형식이라 구조를 다시 만들고 가중치를 직접 넣는다."""
import io, json, os, threading
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import h5py
import numpy as np
from PIL import Image
import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers

MD = os.environ.get("MODEL_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "model"))
meta = json.load(open(os.path.join(MD, "meta.json")))
CHARS, W, H = meta["characters"], meta["img_width"], meta["img_height"]
BLANK = len(CHARS)


def _build():
    inp = layers.Input((W, H, 1), name="image")
    x = layers.Conv2D(32, 3, activation="relu", padding="same", name="Conv1")(inp)
    x = layers.MaxPooling2D(2, name="pool1")(x)
    x = layers.Conv2D(64, 3, activation="relu", padding="same", name="Conv2")(x)
    x = layers.MaxPooling2D(2, name="pool2")(x)
    x = layers.Reshape((W // 4, (H // 4) * 64), name="reshape")(x)
    x = layers.Dense(64, activation="relu", name="dense1")(x)
    x = layers.Bidirectional(layers.LSTM(128, return_sequences=True), name="bidirectional")(x)
    x = layers.Bidirectional(layers.LSTM(64, return_sequences=True), name="bidirectional_1")(x)
    out = layers.Dense(BLANK + 1, activation="softmax", name="dense2")(x)
    m = keras.Model(inp, out)
    with h5py.File(os.path.join(MD, "weights.h5")) as f:
        g = lambda p: np.array(f[p])
        for n in ["Conv1", "Conv2", "dense1", "dense2"]:
            m.get_layer(n).set_weights([g(f"{n}/{n}/kernel:0"), g(f"{n}/{n}/bias:0")])
        for n, suf in [("bidirectional", ""), ("bidirectional_1", "_1")]:
            w = []
            for d in ["forward", "backward"]:
                b = f"{n}/{n}/{d}_lstm{suf}/lstm_cell/"
                w += [g(b + "kernel:0"), g(b + "recurrent_kernel:0"), g(b + "bias:0")]
            m.get_layer(n).set_weights(w)
    return m


model = _build()
_lock = threading.Lock()


def preprocess(img_bytes):
    """실제 캡차는 RGB=0, 글자는 알파에만 있음 → 흰 배경 합성(=255-alpha). 학습 데이터와 같은 형태."""
    im = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    bg.alpha_composite(im)
    g = np.asarray(bg.convert("L").resize((W, H), Image.BILINEAR), dtype=np.float32) / 255.0
    return g.T[..., None]  # (W, H, 1)


def predict(img_bytes):
    x = preprocess(img_bytes)[None]
    with _lock:
        p = model(x, training=False).numpy()[0]
    best = p.argmax(1)
    out, prev = [], None
    for k in best:                      # greedy CTC: 반복 제거 → blank 제거
        if k != prev and k != BLANK:
            out.append(CHARS[k])
        prev = k
    return "".join(out), float(np.prod(p.max(1)))
