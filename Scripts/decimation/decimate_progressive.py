#!/usr/bin/env python3
"""
渐进减面脚本 — 从超高模逐步降至目标面数。

与 decimate_blender 一步到位不同，此脚本采用多步渐进策略：
  - 每步只减少一定比例的面数（默认 65%）
  - 每步之间自动标记锐边为折痕（Shrinkwrap + Edge Split 思路）
  - 渐进减面让 COLLAPSE 每次决策的误差更小，保留更多特征

适用于：
  - 超高精度模型（100W+ 面）降至低模（3K 面）
  - 一步减面效果差、细节丢失严重的场景

用法：
    from decimate_progressive import decimate_progressive

    # 场景中已有 node_0（148W 面）
    low_name, n = decimate_progressive("node_0", target_faces=3000, name="Building")

流程：
    1.48M → ~518K → ~181K → ~63K → ~22K → ~7.7K → ~3K
    每步 COLLAPSE ratio = step_ratio，逐步收敛
"""

import sys

# 默认参数
DEFAULT_TARGET_FACES = 3000
DEFAULT_STEP_RATIO = 0.35   # 每步保留 35% 的面（减少 65%）
DEFAULT_TOLERANCE = 0.15    # 面数允许误差 15%
MIN_STEP_FACES = 4          # 每步最少面数下限


def decimate_progressive(
    high_obj_name: str,
    target_faces: int = DEFAULT_TARGET_FACES,
    step_ratio: float = DEFAULT_STEP_RATIO,
    tolerance: float = DEFAULT_TOLERANCE,
    name: str = None,
    crease_angle: float = 30.0,
    verbose: bool = True,
):
    """
    渐进减面：复制高模 → 多步 COLLAPSE → 最终精确二分法微调。

    Parameters
    ----------
    high_obj_name : str
        场景中高模对象的名称。
    target_faces : int
        最终目标面数（默认 3000）。
    step_ratio : float
        每步保留的面数比例（默认 0.35 = 保留 35%，减少 65%）。
        值越大步子越小（0.5=每步减半），值越小步子越大但误差更多。
    tolerance : float
        最终面数允许误差（默认 0.15 = 15%）。
    name : str
        低模名称前缀。
    crease_angle : float
        折痕角度阈值（度），每步减面后自动标记大于此角度的边为锐边。
        设为 0 禁用折痕保护。
    verbose : bool
        是否输出详细日志。

    Returns
    -------
    (str, int)
        低模对象名和实际面数。
    """
    import bpy

    # === 0. 参数校验 ===
    if not (0.01 <= step_ratio <= 0.99):
        raise ValueError(f"step_ratio 必须在 (0.01, 0.99) 范围内，当前 {step_ratio}")

    # === 1. 获取高模 ===
    obj_high = bpy.data.objects.get(high_obj_name)
    if obj_high is None:
        raise RuntimeError(f"场景中未找到对象 '{high_obj_name}'")
    if obj_high.type != "MESH":
        raise RuntimeError(f"对象 '{high_obj_name}' 不是 MESH 类型（{obj_high.type}）")

    obj_high.hide_set(False)
    obj_high.hide_viewport = False

    high_faces = len(obj_high.data.polygons)
    if name is None:
        name = high_obj_name.replace("_High", "").replace("_high", "")

    if verbose:
        print(f"\n{'='*70}")
        print(f"PROGRESSIVE DECIMATE")
        print(f"  High:     {high_obj_name} ({high_faces:,} faces)")
        print(f"  Target:   {target_faces:,} faces")
        print(f"  Step:     keep {step_ratio:.0%} per step")
        print(f"  Crease:   {crease_angle}°")
        # 预估步数
        steps = _estimate_steps(high_faces, target_faces, step_ratio)
        print(f"  Est. steps: {steps}")
        print(f"{'='*70}\n")

    # === 2. 预清理 ===
    _pre_cleanup(name)

    # === 3. 复制高模作为工作副本 ===
    obj_work = obj_high.copy()
    obj_work.data = obj_high.data.copy()
    obj_work.name = f"{name}_ProgWork"
    bpy.context.collection.objects.link(obj_work)

    current_faces = high_faces
    step = 0

    # === 4. 渐进减面循环 ===
    while current_faces > target_faces * (1 + tolerance):
        step += 1

        # 计算本步目标
        next_target = max(int(current_faces * step_ratio), target_faces)

        # 最后一步：直接瞄准最终目标
        if next_target <= target_faces * 1.2:
            next_target = target_faces

        if verbose:
            print(f"\n--- Step {step}: {current_faces:,} → {next_target:,} faces ---")

        # 标记锐边（折痕保护）
        if crease_angle > 0 and current_faces > target_faces * 5:
            _mark_sharp_edges(obj_work, crease_angle, verbose)

        # 执行 COLLAPSE 减面
        ratio = next_target / current_faces
        obj_work, actual = _apply_decimate_step(obj_work, ratio, name, step, verbose)

        if verbose:
            print(f"    Result: {actual:,} faces")

        # 安全阀：如果面数不变则退出
        if actual == current_faces:
            if verbose:
                print(f"    ⚠ 面数不再减少，提前退出")
            break

        current_faces = actual

    # === 5. 最终精确微调（二分法）===
    if abs(current_faces - target_faces) / target_faces > tolerance:
        if verbose:
            print(f"\n--- Final refinement: {current_faces:,} → {target_faces:,} ---")
        obj_work, current_faces = _final_refinement(
            obj_work, target_faces, tolerance, name, verbose
        )

    # === 6. 清理低模材质 ===
    obj_work.data.materials.clear()

    # === 7. 命名 ===
    obj_work.name = f"{name}_Low"

    # === 8. 清理临时数据 ===
    _cleanup_orphans()

    if verbose:
        print(f"\n{'='*70}")
        print(f"[DONE] Progressive decimation complete")
        print(f"  {high_faces:,} → {current_faces:,} faces ({1 - current_faces/high_faces:.1%} reduction)")
        print(f"  Low-poly: '{obj_work.name}'")
        print(f"  Steps: {step}")
        print(f"{'='*70}")

    return obj_work.name, current_faces


def _estimate_steps(current, target, ratio):
    """预估需要的减面步数。"""
    steps = 0
    while current > target:
        current = max(int(current * ratio), 1)
        steps += 1
        if steps > 50:
            break
    return steps


def _pre_cleanup(name):
    """删除残留的同名低模。"""
    import bpy
    for obj in list(bpy.data.objects):
        if obj.name in (f"{name}_Low", f"{name}_ProgWork") or obj.name.startswith(f"{name}_Step"):
            mesh_data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh_data and mesh_data.users == 0:
                bpy.data.meshes.remove(mesh_data)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _mark_sharp_edges(obj, angle_deg, verbose):
    """标记大于指定角度的边为锐边（折痕保护）。

    这让 COLLAPSE 减面时更不容易塌陷这些特征边。
    直接在 OBJECT 模式下操作 bmesh 数据。
    """
    import bpy
    import bmesh
    import math

    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    # 用 bmesh 操作，比直接操作 mesh 更可靠
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()

    angle_rad = math.radians(angle_deg)
    sharp_count = 0

    for edge in bm.edges:
        if edge.is_boundary:
            edge.smooth = False
            sharp_count += 1
            continue
        if len(edge.link_faces) == 2:
            f1, f2 = edge.link_faces
            n1, n2 = f1.normal, f2.normal
            if n1.length < 1e-6 or n2.length < 1e-6:
                continue
            angle = n1.angle(n2)
            if angle > angle_rad:
                edge.smooth = False
                sharp_count += 1

    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    if verbose and sharp_count > 0:
        print(f"    Marked {sharp_count} sharp edges (>{angle_deg}°)")


def _apply_decimate_step(obj, ratio, name, step, verbose):
    """对对象执行一步 COLLAPSE 减面。"""
    import bpy

    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    # 添加 Decimate modifier
    decimate = obj.modifiers.new(name=f"DecStep{step}", type='DECIMATE')
    decimate.decimate_type = 'COLLAPSE'
    decimate.ratio = ratio
    bpy.ops.object.modifier_apply(modifier=decimate.name)

    actual = len(obj.data.polygons)
    return obj, actual


def _final_refinement(obj, target_faces, tolerance, name, verbose):
    """最终精确微调：二分法搜索精确面数。

    每次从工作副本的当前状态出发，用 COLLAPSE ratio 来逼近目标。
    ratio 的含义是"保留当前面数的比例"，所以 ratio = target / current。
    """
    import bpy

    current_faces = len(obj.data.polygons)
    lo_ratio = target_faces / current_faces   # 乐观估计（可能不到目标）
    hi_ratio = 1.0                             # 不减面

    max_attempts = 20
    stagnation = 0
    last_count = None
    best_obj = None
    best_diff = float('inf')

    for attempt in range(1, max_attempts + 1):
        ratio = (lo_ratio + hi_ratio) / 2.0

        for o in bpy.context.scene.objects:
            o.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        decimate = obj.modifiers.new(name=f"DecRef{attempt}", type='DECIMATE')
        decimate.decimate_type = 'COLLAPSE'
        decimate.ratio = ratio
        bpy.ops.object.modifier_apply(modifier=decimate.name)

        actual = len(obj.data.polygons)
        diff = abs(actual - target_faces) / target_faces

        if verbose:
            print(f"    Refine {attempt}: ratio={ratio:.6f} → {actual:,} (diff={diff:.1%})")

        # 记录最接近目标的结果
        if diff < best_diff:
            best_diff = diff
            best_obj = obj

        if diff <= tolerance:
            if verbose:
                print(f"    ✓ 达标: {actual:,} (误差 {diff:.1%})")
            return obj, actual

        # 停滞检测
        if actual == last_count:
            stagnation += 1
            if stagnation >= 5:
                if verbose:
                    print(f"    ⚠ 停滞，使用最接近结果 ({best_diff:.1%})")
                return best_obj, len(best_obj.data.polygons)
        else:
            stagnation = 0
        last_count = actual

        # 二分调整
        if actual > target_faces:
            hi_ratio = ratio
        else:
            lo_ratio = ratio

    return obj, len(obj.data.polygons)


def _cleanup_orphans():
    """清理孤立数据块。"""
    import bpy
    for _ in range(5):
        removed = 0
        for bt in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.textures):
            for b in list(bt):
                if b.users == 0 and not b.use_fake_user:
                    bt.remove(b)
                    removed += 1
        if removed == 0:
            break


# ============ CLI 入口 ============
def main():
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1:]
    else:
        argv = []

    import argparse
    p = argparse.ArgumentParser(description="渐进减面脚本 — 多步逐步降至目标面数")
    p.add_argument("--high", required=True, help="场景中高模对象名")
    p.add_argument("--target_faces", type=int, default=DEFAULT_TARGET_FACES,
                   help=f"目标面数（默认 {DEFAULT_TARGET_FACES}）")
    p.add_argument("--step_ratio", type=float, default=DEFAULT_STEP_RATIO,
                   help=f"每步保留面数比例（默认 {DEFAULT_STEP_RATIO}）")
    p.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                   help=f"面数允许误差（默认 {DEFAULT_TOLERANCE}）")
    p.add_argument("--name", default=None, help="低模名称前缀")
    p.add_argument("--crease_angle", type=float, default=30.0,
                   help="折痕角度阈值（度，默认30，0=禁用）")
    args = p.parse_args(argv)

    decimate_progressive(
        high_obj_name=args.high,
        target_faces=args.target_faces,
        step_ratio=args.step_ratio,
        tolerance=args.tolerance,
        name=args.name,
        crease_angle=args.crease_angle,
    )


if __name__ == "__main__":
    main()
