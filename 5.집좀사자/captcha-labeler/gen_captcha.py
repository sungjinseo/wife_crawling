"""
숫자 캡차 합성 이미지 생성기 — 실제 수집 캡차(150×66) 분석값 기준

실제 이미지 분석 결과 (라벨된 100장 기준)
  - 크기 150×66, 투명 배경 + 검정 글자, 1px 검정 테두리
  - 글꼴 DejaVu Sans 40px, 시작 x=8, 기준선 y=49 (모든 이미지 동일, 흔들림 없음)
  - 글자 간격은 숫자별로 고정: 0:20 1:17 2:18 3:19 4:21 5:18 6:20 7:18 8:20 9:20
  - 선 2개 모두 같은 방식의 곡선:
      3차 베지어 P0=(0.10W, 랜덤y) P1=(0.10W, 랜덤y) P2=(0.25W, 랜덤y) P3=(0.90W, 랜덤y)
      → 왼쪽 끝이 세로로 꺾이는 갈고리 모양, 오른쪽 끝은 x≈135
      → 곡선을 꺾은선으로 근사해서 두께 3px로 그림

파일명 = 숫자 그대로, 앞자리 0 유지 (예: 026741.png)

사용 예
  python gen_captcha.py --count 30000 --out ./data/labeled/synth
  python gen_captcha.py --digits 5 --lines 3 --out ./synth5
  python gen_captcha.py --lines 1-3 --count 10 --out ./preview
  python gen_captcha.py --rgba --count 10 --out ./preview   # 원본처럼 투명 배경
"""
import argparse
import os
import random
from multiprocessing import Pool

from PIL import Image, ImageDraw, ImageFont

W, H = 150, 66
FONT_SIZE = 40
X0, BASELINE = 8, 49
ADV = {"0": 20, "1": 17, "2": 18, "3": 19, "4": 21, "5": 18, "6": 20, "7": 18, "8": 20, "9": 20}
SS = 4  # 4배 크게 그린 뒤 축소 → 안티앨리어싱 (원본도 안티앨리어싱 적용됨)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",   # 도커/리눅스 (원본과 같은 글꼴)
    "C:/Windows/Fonts/DejaVuSans.ttf",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "DejaVuSans.ttf"),
]

_args = None
_font_path = None


def find_font(user_font):
    for p in ([user_font] if user_font else []) + FONT_CANDIDATES:
        if p and os.path.exists(p):
            return p
    raise SystemExit("DejaVuSans.ttf 를 찾을 수 없습니다. 도커 안에서 실행하거나 --font 로 경로를 지정하세요.\n"
                     "(실제 캡차와 같은 글꼴이어야 정확도가 나옵니다)")


def parse_lines(v):
    lo, _, hi = v.partition("-")
    lo, hi = int(lo), int(hi or lo)
    if not 0 <= lo <= hi:
        raise argparse.ArgumentTypeError("선 개수는 '2' 또는 '1-3' 형식")
    return lo, hi


def _flatten_cubic(p0, p1, p2, p3, flatness=2.0, depth=0):
    """3차 베지어를 꺾은선으로 근사 (평탄도 2px 이내가 될 때까지 반으로 분할)"""
    def dist(p, a, b):
        (x, y), (x1, y1), (x2, y2) = p, a, b
        dx, dy = x2 - x1, y2 - y1
        n = (dx * dx + dy * dy) ** 0.5
        if n == 0:
            return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
        return abs(dy * x - dx * y + x2 * y1 - y2 * x1) / n

    if depth >= 10 or max(dist(p1, p0, p3), dist(p2, p0, p3)) <= flatness:
        return [p0, p3]
    mid = lambda a, b: ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    p01, p12, p23 = mid(p0, p1), mid(p1, p2), mid(p2, p3)
    p012, p123 = mid(p01, p12), mid(p12, p23)
    m = mid(p012, p123)
    left = _flatten_cubic(p0, p01, p012, m, flatness, depth + 1)
    right = _flatten_cubic(m, p123, p23, p3, flatness, depth + 1)
    return left[:-1] + right


def make_image(text, font_path, rng, lines=(2, 2), line_width=3.0, rgba=False):
    # 자릿수가 많아 폭을 넘으면 비율 축소 (실제 6자리는 축소 없음)
    total = sum(ADV.get(c, 20) for c in text)
    scale = min(1.0, (W - X0 - 4) / total)

    big = Image.new("L", (W * SS, H * SS), 0)  # 255 = 잉크
    d = ImageDraw.Draw(big)

    font = ImageFont.truetype(font_path, int(round(FONT_SIZE * scale * SS)))
    x = X0
    for c in text:
        d.text((x * SS, BASELINE * SS), c, font=font, fill=255, anchor="ls")
        x += ADV.get(c, 20) * scale

    for _ in range(rng.randint(*lines)):
        ys = [H * rng.random() for _ in range(4)]
        pts = _flatten_cubic((W * .10, ys[0]), (W * .10, ys[1]), (W * .25, ys[2]), (W * .90, ys[3]))
        pts = [(int(px), int(py)) for px, py in pts]  # 원본도 정수 좌표로 그림
        for a, b in zip(pts, pts[1:]):
            d.line([(a[0] * SS, a[1] * SS), (b[0] * SS, b[1] * SS)], fill=255, width=int(line_width * SS))
        r = line_width * SS / 2
        for p in pts[1:-1]:  # 꺾이는 지점 이음새 메우기
            d.ellipse([p[0] * SS - r, p[1] * SS - r, p[0] * SS + r, p[1] * SS + r], fill=255)

    ink = big.resize((W, H), Image.BOX)
    ImageDraw.Draw(ink).rectangle([0, 0, W - 1, H - 1], outline=255, width=1)

    if rgba:
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        out.putalpha(ink)
        return out
    return Image.eval(ink, lambda v: 255 - v)  # 흰 배경 흑백 (학습/테스트 입력과 같은 형태)


def _init(args, font_path):
    global _args, _font_path
    _args, _font_path = args, font_path


def _work(n):
    text = str(n).zfill(_args.digits)
    path = os.path.join(_args.out, f"{text}.png")
    if os.path.exists(path) and not _args.overwrite:
        return
    rng = random.Random(n if _args.seed is None else hash((_args.seed, n)))
    make_image(text, _font_path, rng, _args.lines, _args.line_width, _args.rgba).save(path, "PNG", optimize=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--digits", type=int, default=6, help="자릿수 (5 → 00000~99999)")
    ap.add_argument("--lines", type=parse_lines, default=(2, 2), help="곡선 개수: '2' 또는 범위 '1-3'")
    ap.add_argument("--line-width", type=float, default=3.0)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, help="포함 (기본: 자릿수 최대값)")
    ap.add_argument("--count", type=int, help="범위 안에서 무작위로 N장만 생성")
    ap.add_argument("--out", default="./synth")
    ap.add_argument("--font", help="DejaVuSans.ttf 경로 (미지정 시 자동 탐색)")
    ap.add_argument("--rgba", action="store_true", help="원본처럼 투명 배경으로 저장")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--seed", type=int, help="같은 seed면 같은 이미지 재현")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    if not 1 <= args.digits <= 12:
        raise SystemExit("--digits 는 1~12")
    max_n = 10 ** args.digits - 1
    if args.end is None:
        args.end = max_n
    if not (0 <= args.start <= args.end <= max_n):
        raise SystemExit(f"{args.digits}자리 범위는 0~{max_n} 입니다.")
    os.makedirs(args.out, exist_ok=True)
    font_path = find_font(args.font)

    nums = range(args.start, args.end + 1)
    if args.count:
        nums = random.Random(args.seed).sample(nums, min(args.count, len(nums)))
    total = len(nums)
    lo, hi = args.lines
    print(f"{args.digits}자리 {str(args.start).zfill(args.digits)}~{str(args.end).zfill(args.digits)}, "
          f"곡선 {lo if lo == hi else f'{lo}~{hi}'}개 · {total:,}장 → {args.out}")

    with Pool(args.workers, initializer=_init, initargs=(args, font_path)) as pool:
        for i, _ in enumerate(pool.imap_unordered(_work, nums, chunksize=500), 1):
            if i % 10000 == 0 or i == total:
                print(f"  {i:,}/{total:,}")
    print("완료")


if __name__ == "__main__":
    main()
