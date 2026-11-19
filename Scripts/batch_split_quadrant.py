# -*- coding: utf-8 -*-
"""
批量田字图拆分 —— 遍历根目录下每个文件夹的「生成图」子目录，
把里面的田字图拆成 前/后/左/右；若四张结果图已存在则跳过。

用法:
    python batch_split_quadrant.py <根目录>
    python batch_split_quadrant.py <根目录> --force   # 忽略已存在，强制重做
"""
import argparse
from pathlib import Path

from split_quadrant import QUADRANTS, split

NAMES = [q[0] for q in QUADRANTS]
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def find_source(gen_dir):
    """在生成图目录里找到田字图（排除已生成的前后左右）。"""
    candidates = [f for f in gen_dir.iterdir()
                  if f.is_file() and f.suffix.lower() in IMAGE_EXTS and f.stem not in NAMES]
    if not candidates:
        return None, "未找到田字图"
    if len(candidates) > 1:
        return None, f"找到多张候选图，无法确定: {[c.name for c in candidates]}"
    return candidates[0], None


def outputs_exist(gen_dir):
    """四张结果图是否都已存在（按名称匹配任意扩展名）。"""
    return all(next(gen_dir.glob(f"{n}.*"), None) is not None for n in NAMES)


def batch(root, force=False):
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"根目录不存在: {root}")

    total = skipped = done = failed = 0
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        gen = sub / "生成图"
        if not gen.is_dir():
            print(f"[跳过] {sub.name}: 无「生成图」子目录")
            continue
        total += 1

        if outputs_exist(gen) and not force:
            skipped += 1
            print(f"[跳过] {sub.name}: 前/后/左/右 已存在")
            continue

        src, err = find_source(gen)
        if err:
            failed += 1
            print(f"[失败] {sub.name}: {err}")
            continue

        print(f"[处理] {sub.name}: {src.name}")
        try:
            split(src, output_dir=gen)
            done += 1
        except Exception as e:
            failed += 1
            print(f"[失败] {sub.name}: {e}")

    print(f"\n汇总: 共 {total} 个 | 处理 {done} | 跳过 {skipped} | 失败 {failed}")


def main():
    parser = argparse.ArgumentParser(description="批量拆分田字图（已存在前后左右则跳过）")
    parser.add_argument("root", help="根目录（其下每个文件夹含「生成图」子目录）")
    parser.add_argument("--force", action="store_true", help="忽略已存在，强制重新拆分")
    args = parser.parse_args()
    batch(args.root, args.force)


if __name__ == "__main__":
    main()
