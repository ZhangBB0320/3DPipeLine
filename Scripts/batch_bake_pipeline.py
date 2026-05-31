#!/usr/bin/env python3
"""
批量烘焙管线 — 扫描目录下所有 FBX 高模，逐一执行：
  导入 → 减面(COLLAPSE+二分法+流形修复) → 烘焙(Albedo+Normal) → 导出低模FBX(内嵌纹理)

每个模型处理完毕后自动清理场景，避免内存累积。
输出文件名与高模一致，放到独立输出目录。

用法（通过 blender_connect 推送到 Blender）：
    python3 /Users/zbb/3DPipeLine/Scripts/blender_connect.py --exec "
    import sys; sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts');
    sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts/decimation');
    from batch_bake_pipeline import batch_pipeline;
    batch_pipeline(input_dir='/Users/zbb/Downloads/BuildingHigh')
    "

    # 或在 Blender Python 控制台中：
    import sys
    sys.path.insert(0, "/Users/zbb/3DPipeLine/Scripts")
    sys.path.insert(0, "/Users/zbb/3DPipeLine/Scripts/decimation")
    from batch_bake_pipeline import batch_pipeline
    batch_pipeline(input_dir="/Users/zbb/Downloads/BuildingHigh")
"""

import os
import sys
import glob
import time

# 默认参数
DEFAULT_TARGET_FACES = 3000
DEFAULT_CAGE = 0.05
DEFAULT_RES = 4096
DEFAULT_SAMPLES = 64
DEFAULT_DEVICE = "GPU"


def _ensure_imports():
    """确保所有子模块可导入。"""
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    decimation_dir = os.path.join(scripts_dir, "decimation")
    for d in (scripts_dir, decimation_dir):
        if d not in sys.path:
            sys.path.insert(0, d)


def _cleanup_scene():
    """彻底清理场景：删除所有对象，清理孤立数据块。"""
    import bpy

    # 取消选中 + 退出编辑模式
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    bpy.ops.object.select_all(action="DESELECT")

    # 删除所有对象
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    # 清理孤立数据
    for _ in range(10):
        removed = 0
        for block_type in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.textures):
            for block in list(block_type):
                if block.users == 0 and not block.use_fake_user:
                    block_type.remove(block)
                    removed += 1
        if removed == 0:
            break

    print("    Scene cleaned")


def _process_single(
    fbx_path: str,
    output_dir: str,
    target_faces: int,
    cage: float,
    res: int,
    samples: int,
    device: str,
):
    """处理单个 FBX：导入 → 减面 → 烘焙 → 导出。"""
    import bpy

    _ensure_imports()
    from import_fbx import safe_import
    from decimate_blender import decimate_blender
    from bake_scene import bake_scene
    from export_model import export_model

    stem = os.path.splitext(os.path.basename(fbx_path))[0]
    output_path = os.path.join(output_dir, f"{stem}.fbx")

    # 断点续跑：输出已存在则跳过
    if os.path.exists(output_path):
        print(f"\n[SKIP] {stem} — 输出已存在: {output_path}")
        return {"stem": stem, "status": "skipped", "path": output_path}

    t0 = time.time()
    print(f"\n{'#'*70}")
    print(f"# PROCESSING: {stem}")
    print(f"# Input:  {fbx_path}")
    print(f"# Output: {output_path}")
    print(f"{'#'*70}")

    # === 清理场景 ===
    _cleanup_scene()

    # === 1. 导入高模 ===
    print(f"\n[1/4] Importing high-poly: {os.path.basename(fbx_path)}")
    new_objs = safe_import(fbx_path, verbose=True, rename_to_stem=True)

    # 找第一个 mesh 对象
    high_name = None
    for nm in new_objs:
        obj = bpy.data.objects.get(nm)
        if obj and obj.type == "MESH":
            high_name = nm
            break
    if not high_name:
        raise RuntimeError(f"文件 '{fbx_path}' 没有 mesh 对象")

    high_obj = bpy.data.objects.get(high_name)
    high_faces = len(high_obj.data.polygons)
    print(f"    High-poly: '{high_name}' — {high_faces:,} faces")

    # === 2. 减面 ===
    print(f"\n[2/4] Decimating: {high_faces:,} → ~{target_faces:,} faces")
    low_name, actual_faces = decimate_blender(
        high_obj_name=high_name,
        target_faces=target_faces,
        name=stem,
    )
    print(f"    Decimated: '{low_name}' — {actual_faces:,} faces")

    # === 3. 烘焙 ===
    print(f"\n[3/4] Baking (Albedo + Normal, {res}x{res}, {samples} samples)...")
    bake_scene(
        high_obj_name=high_name,
        low_obj_name=low_name,
        name=stem,
        cage=cage,
        res=res,
        samples=samples,
        device=device,
    )

    # === 4. 导出低模 FBX（内嵌纹理）===
    print(f"\n[4/4] Exporting low-poly FBX (embedded textures)...")
    result = export_model(
        obj_names=[low_name],
        output_path=output_path,
        fmt="fbx",
    )

    elapsed = time.time() - t0
    print(f"\n✓ {stem} DONE in {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"    {high_faces:,} → {actual_faces:,} faces")
    print(f"    → {result['path']}")

    return {
        "stem": stem,
        "status": "ok",
        "high_faces": high_faces,
        "low_faces": actual_faces,
        "path": result["path"],
        "elapsed_s": elapsed,
    }


def batch_pipeline(
    input_dir: str,
    output_dir: str = None,
    target_faces: int = DEFAULT_TARGET_FACES,
    cage: float = DEFAULT_CAGE,
    res: int = DEFAULT_RES,
    samples: int = DEFAULT_SAMPLES,
    device: str = DEFAULT_DEVICE,
    continue_on_error: bool = True,
):
    """
    批量烘焙管线：扫描 input_dir 下所有 FBX，逐一处理。

    Parameters
    ----------
    input_dir : str
        高模 FBX 所在目录
    output_dir : str
        低模 FBX 输出目录（默认 = input_dir 同级目录 + "_Low"）
    target_faces : int
        目标面数（默认 3000）
    cage : float
        烘焙 cage 距离（默认 0.1）
    res : int
        烘焙分辨率（默认 4096）
    samples : int
        烘焙采样数（默认 64）
    device : str
        "GPU" 或 "CPU"
    continue_on_error : bool
        单个模型失败是否继续处理后续模型
    """
    if output_dir is None:
        output_dir = input_dir.rstrip("/") + "_Low"

    os.makedirs(output_dir, exist_ok=True)

    # 扫描所有 FBX（不区分大小写排序）
    fbx_files = sorted(
        glob.glob(os.path.join(input_dir, "*.fbx")),
        key=lambda p: os.path.basename(p).lower(),
    )
    if not fbx_files:
        print(f"[ERROR] 目录 '{input_dir}' 下没有 FBX 文件")
        return []

    print(f"\n{'='*70}")
    print(f"BATCH BAKE PIPELINE")
    print(f"  Input:    {input_dir} ({len(fbx_files)} FBX files)")
    print(f"  Output:   {output_dir}")
    print(f"  Target:   {target_faces} faces")
    print(f"  Bake:     {res}x{res}, {samples} samples, {device}")
    print(f"  Files:")
    for f in fbx_files:
        print(f"    - {os.path.basename(f)}")
    print(f"{'='*70}")

    t_total = time.time()
    results = []

    for idx, fbx_path in enumerate(fbx_files):
        print(f"\n>>> [{idx+1}/{len(fbx_files)}] {os.path.basename(fbx_path)} <<<")
        try:
            result = _process_single(
                fbx_path=fbx_path,
                output_dir=output_dir,
                target_faces=target_faces,
                cage=cage,
                res=res,
                samples=samples,
                device=device,
            )
            results.append(result)
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            print(f"\n[ERROR] {os.path.basename(fbx_path)}: {e}\n{err}")
            results.append({
                "stem": os.path.splitext(os.path.basename(fbx_path))[0],
                "status": "error",
                "error": str(e),
            })
            # 出错后也清理场景，防止残留影响下一个
            try:
                _cleanup_scene()
            except Exception:
                pass
            if not continue_on_error:
                print("[ABORT] continue_on_error=False, stopping batch")
                break

    # 最终清理
    try:
        _cleanup_scene()
    except Exception:
        pass

    # 摘要
    total_elapsed = time.time() - t_total
    n_ok = sum(1 for r in results if r["status"] == "ok")
    n_skip = sum(1 for r in results if r["status"] == "skipped")
    n_err = sum(1 for r in results if r["status"] == "error")

    print(f"\n{'='*70}")
    print(f"BATCH COMPLETE — {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)")
    print(f"  Total:   {len(fbx_files)}")
    print(f"  OK:      {n_ok}")
    print(f"  Skipped: {n_skip}")
    print(f"  Error:   {n_err}")
    print(f"{'='*70}")
    for r in results:
        flag = {"ok": "✓", "skipped": "⊘", "error": "✗"}[r["status"]]
        if r["status"] == "ok":
            info = f"{r['high_faces']:,} → {r['low_faces']:,} faces → {r['path']}"
        elif r["status"] == "skipped":
            info = f"already exists: {r['path']}"
        else:
            info = f"ERROR: {r.get('error', 'unknown')}"
        print(f"  [{flag}] {r['stem']}: {info}")

    return results


# CLI
def main():
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1:]
    else:
        argv = []

    import argparse
    p = argparse.ArgumentParser(description="批量烘焙管线：导入→减面→烘焙→导出")
    p.add_argument("--input_dir", required=True, help="高模 FBX 目录")
    p.add_argument("--output_dir", default=None, help="低模输出目录（默认=input_dir_Low）")
    p.add_argument("--target_faces", type=int, default=DEFAULT_TARGET_FACES,
                   help=f"目标面数（默认 {DEFAULT_TARGET_FACES}）")
    p.add_argument("--cage", type=float, default=DEFAULT_CAGE)
    p.add_argument("--res", type=int, default=DEFAULT_RES)
    p.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    p.add_argument("--device", default=DEFAULT_DEVICE, choices=["CPU", "GPU"])
    args = p.parse_args(argv)

    batch_pipeline(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        target_faces=args.target_faces,
        cage=args.cage,
        res=args.res,
        samples=args.samples,
        device=args.device,
    )


if __name__ == "__main__":
    main()
