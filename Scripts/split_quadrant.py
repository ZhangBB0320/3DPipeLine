# -*- coding: utf-8 -*-
"""
田字图拆分脚本 —— 将一张 2x2 田字图拆成 前/后/左/右 四张独立视角图。

映射规则:
    左上 -> 前      右上 -> 后
    左下 -> 左      右下 -> 右

核心保证:
    1. 自动检测贯穿画面的黑色分隔线（不假设它在图像正中心），沿黑线切分，
       黑线本身被完全丢弃 —— 输出图不含任何黑边/黑线。
    2. 若检测到黑线不存在，则退化为几何中线等分。
    3. 可选裁掉各视角边缘残留的纯黑细边（默认开启）。

用法:
    python split_quadrant.py <输入图>
    python split_quadrant.py <输入图> --output <目录>
    python split_quadrant.py <输入图> --threshold 40 --no-trim

默认把 前.png/后.png/左.png/右.png 写到输入图所在目录（与输入同名扩展名）。
"""
import argparse
from pathlib import Path

from PIL import Image

# 黑线判定: 像素灰度 < threshold 视为暗（黑）
DEFAULT_THRESHOLD = 40
# 判定"贯穿性暗线"所需的暗像素占比
LINE_RATIO = 0.85
# 暗线搜索窗口: 只在画面中心 ±30% 范围内寻找分隔线
CENTER_WINDOW = 0.30
# 边缘裁黑边时, 判定一整行/列是纯黑边所需的暗像素占比
EDGE_RATIO = 0.99


QUADRANTS = [
    ("前", 0, 0),  # (名称, 列号0=左/1=右, 行号0=上/1=下)
    ("后", 1, 0),
    ("左", 0, 1),
    ("右", 1, 1),
]


def _dark_counts(im, threshold):
    """返回逐行、逐列的暗像素数量。"""
    w, h = im.size
    gray = im.convert("L")
    px = gray.load()
    # 逐像素访问，避免使用已弃用的 getdata()
    row_counts = [0] * h
    col_counts = [0] * w
    for y in range(h):
        rc = 0
        for x in range(w):
            if px[x, y] < threshold:
                rc += 1
                col_counts[x] += 1
        row_counts[y] = rc
    return row_counts, col_counts


def _find_dark_runs(counts, total, ratio):
    """找出连续的高暗度区间 [(start, end), ...]（闭区间）。"""
    runs = []
    start = None
    need = total * ratio
    for i, c in enumerate(counts):
        if c >= need:
            if start is None:
                start = i
        else:
            if start is not None:
                runs.append((start, i - 1))
                start = None
    if start is not None:
        runs.append((start, len(counts) - 1))
    return runs


def _pick_central(runs, length):
    """从区间中挑选最靠近画面中心的一条（分隔线），返回 (start, end) 或 None。"""
    if not runs:
        return None
    center = length / 2.0
    window = length * CENTER_WINDOW
    candidates = [r for r in runs if center - window <= (r[0] + r[1]) / 2.0 <= center + window]
    pool = candidates if candidates else runs
    return min(pool, key=lambda r: abs((r[0] + r[1]) / 2.0 - center))


def find_split_lines(im, threshold):
    """定位十字分隔线。返回 (x_start, x_end, y_start, y_end)。
    未检测到对应方向暗线时，用几何中线（宽度为0的空线）代替。"""
    w, h = im.size
    row_counts, col_counts = _dark_counts(im, threshold)
    # 横线: 沿整行宽度统计; 竖线: 沿整列高度统计
    h_runs = _find_dark_runs(row_counts, w, LINE_RATIO)
    v_runs = _find_dark_runs(col_counts, h, LINE_RATIO)
    h_line = _pick_central(h_runs, h)  # (y0, y1)
    v_line = _pick_central(v_runs, w)  # (x0, x1)

    if h_line is None:
        mid = h // 2
        y0 = y1 = mid  # 空线: 上半 [0,mid), 下半 [mid,h)
    else:
        y0, y1 = h_line
    if v_line is None:
        mid = w // 2
        x0 = x1 = mid
    else:
        x0, x1 = v_line
    return x0, x1, y0, y1


def _trim_black_edges(tile, threshold):
    """裁掉四边连续的纯黑细边，返回裁剪后的图。"""
    w, h = tile.size
    if w == 0 or h == 0:
        return tile
    row_counts, col_counts = _dark_counts(tile, threshold)

    top = 0
    while top < h and row_counts[top] >= w * EDGE_RATIO:
        top += 1
    bottom = h - 1
    while bottom > top and row_counts[bottom] >= w * EDGE_RATIO:
        bottom -= 1
    left = 0
    while left < w and col_counts[left] >= h * EDGE_RATIO:
        left += 1
    right = w - 1
    while right > left and col_counts[right] >= h * EDGE_RATIO:
        right -= 1

    if top == 0 and bottom == h - 1 and left == 0 and right == w - 1:
        return tile
    return tile.crop((left, top, right + 1, bottom + 1))


def split(image_path, output_dir=None, threshold=DEFAULT_THRESHOLD, trim=True):
    """拆分一张田字图。返回 {名称: 输出路径}。"""
    src = Path(image_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"输入图片不存在: {src}")

    out_dir = Path(output_dir).resolve() if output_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    im = Image.open(src)
    x0, x1, y0, y1 = find_split_lines(im.convert("RGB"), threshold)
    print(f"检测到分隔线: 竖线 x=[{x0},{x1}], 横线 y=[{y0},{y1}]  (图像 {im.size[0]}x{im.size[1]})")

    # 四个象限范围: 右半从 x1+1 起, 下半从 y1+1 起 —— 黑线被完全排除
    xs = [(0, x0), (x1 + 1, im.width)]
    ys = [(0, y0), (y1 + 1, im.height)]

    ext = src.suffix or ".png"
    results = {}
    for name, col, row in QUADRANTS:
        left, right = xs[col]
        top, bottom = ys[row]
        tile = im.crop((left, top, right, bottom))
        if trim:
            tile = _trim_black_edges(tile, threshold)
        dst = out_dir / f"{name}{ext}"
        tile.save(dst)
        results[name] = dst
        print(f"  {name}: crop=({left},{top},{right},{bottom}) -> {tile.size}  {dst.name}")
    return results


def main():
    parser = argparse.ArgumentParser(
        description="把一张田字图拆成 前/后/左/右 四张视角图（自动去黑边）"
    )
    parser.add_argument("input", help="输入田字图路径")
    parser.add_argument("--output", default=None, help="输出目录（默认: 输入图所在目录）")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                        help=f"黑线灰度阈值 (默认: {DEFAULT_THRESHOLD})")
    parser.add_argument("--no-trim", action="store_true", help="关闭边缘黑边裁剪")
    args = parser.parse_args()

    results = split(args.input, args.output, args.threshold, trim=not args.no_trim)
    print("\n完成:", ", ".join(f"{k}={v.name}" for k, v in results.items()))


if __name__ == "__main__":
    main()
