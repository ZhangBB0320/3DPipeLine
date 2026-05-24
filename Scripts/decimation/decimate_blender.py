#!/usr/bin/env python3
"""
Blender 内减面脚本 — 复制高模 → Decimate COLLAPSE → 二分法精确控面数 → 可选流形修复。

不负责导入、烘焙、导出，只做减面。适用于：
  - 已在 Blender 场景中的高模对象
  - 需要精确控制目标面数的减面
  - 需要拓扑干净的流形网格（3D 打印、物理引擎等）

减面流程从 bake.py 的 _decimate_with_retry() 提取，
去掉烘焙相关步骤，仅保留：
  复制高模 → Decimate modifier → 二分法调整 ratio → 流形修复(可选) → 清理材质 → 返回低模

用法：
    from decimate_blender import decimate_blender

    # 场景中已有 building1 高模（默认启用流形修复）
    low_name, actual_faces = decimate_blender("building1", target_faces=3000)

    # 自定义低模名称
    decimate_blender("building1", target_faces=3000, name="MyHouse")

    # 禁用流形修复（旧行为）
    decimate_blender("building1", target_faces=3000, repair_mesh=False)

核心规则（同 bake.py）：
- 使用 COLLAPSE 模式
- 面数不精确时自动二分法搜索
- 连续 3 次面数相同则提前退出（COLLAPSE 已到拓扑下限）
- 减面后可选流形修复：删除退化面 / 删除非流形边 / 删除松散几何 / 合并重叠顶点 / 重算法线
- 减面后清空低模材质（烘焙时会重新赋材质）
"""

import sys

# 统一参数
DEFAULT_TARGET_FACES = 3000
DEFAULT_TOLERANCE = 0.1   # 面数允许误差 10%
DEFAULT_MERGE_DISTANCE = 0.0001  # 流形修复：合并重叠顶点距离阈值


def decimate_blender(
    high_obj_name: str,
    target_faces: int = DEFAULT_TARGET_FACES,
    tolerance: float = DEFAULT_TOLERANCE,
    name: str = None,
    repair_mesh: bool = True,
    merge_distance: float = DEFAULT_MERGE_DISTANCE,
):
    """
    Blender 内减面：复制高模 → Decimate COLLAPSE → 二分法调整 → 可选流形修复。

    Parameters
    ----------
    high_obj_name : str
        场景中高模对象的名称。
    target_faces : int
        目标面数（默认 3000）。
    tolerance : float
        允许的面数误差比例（默认 0.1 = 10%）。
    name : str
        低模名称前缀。若不提供，则用高模名去掉可能的 _High 后缀。
    repair_mesh : bool
        减面后是否进行流形修复（默认 True）。
        包括：删除退化面 / 删除非流形边 / 删除松散几何 / 合并重叠顶点 / 重算法线。
    merge_distance : float
        合并重叠顶点的距离阈值（默认 0.0001），仅在 repair_mesh=True 时生效。

    Returns
    -------
    (str, int)
        低模对象名和实际面数。
    """
    import bpy

    # === 1. 获取高模 ===
    print(f"\n{'='*70}")
    print(f"DECIMATE (Blender)")
    print(f"  High:   {high_obj_name}")
    print(f"  Target: {target_faces} faces (tolerance={tolerance:.0%})")
    print(f"  Repair: {'ON' if repair_mesh else 'OFF'}")
    print(f"{'='*70}\n")

    obj_high = bpy.data.objects.get(high_obj_name)
    if obj_high is None:
        raise RuntimeError(f"场景中未找到对象 '{high_obj_name}'")
    if obj_high.type != "MESH":
        raise RuntimeError(f"对象 '{high_obj_name}' 不是 MESH 类型（{obj_high.type}）")

    # 确保 high 可见可选中
    obj_high.hide_set(False)
    obj_high.hide_viewport = False

    high_faces = len(obj_high.data.polygons)
    print(f"[1] High-poly: {len(obj_high.data.vertices)} verts, {high_faces} faces")

    if name is None:
        name = high_obj_name.replace("_High", "").replace("_high", "")

    # === 2. 预清理：删除残留的同名低模 ===
    print("[2] Pre-cleanup...")
    for obj in list(bpy.data.objects):
        if obj.name == f"{name}_Low" or obj.name == f"{name}_Low_tmp":
            mesh_data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh_data.users == 0:
                bpy.data.meshes.remove(mesh_data)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    print("    Pre-cleanup done")

    # === 3. 二分法减面 ===
    print(f"[3] Decimating (target={target_faces}, tolerance={tolerance:.0%})...")
    obj_low, actual_faces = _decimate_with_retry(
        obj_high, target_faces=target_faces, tolerance=tolerance, name=name
    )

    # === 4. 流形修复（可选）===
    if repair_mesh:
        print("[4] Repairing mesh (manifold cleanup)...")
        actual_faces = _repair_mesh(obj_low, merge_distance=merge_distance)
    else:
        print("[4] Mesh repair: SKIPPED")

    # === 5. 清空低模材质（烘焙时会重新赋材质）===
    obj_low.data.materials.clear()

    # === 6. 完成 ===
    print(f"\n[DONE] Decimation complete:")
    print(f"    Low-poly: '{obj_low.name}' — {actual_faces} faces")
    print(f"    High-poly: '{obj_high.name}' — {high_faces} faces (preserved)")

    return obj_low.name, actual_faces


def _repair_mesh(obj, merge_distance=DEFAULT_MERGE_DISTANCE, verbose=True):
    """3D 打印工具集 — 流形修复。

    减面完成后调用，依次执行：
      1. 删除退化面（零面积 / 零法线）
      2. 删除非流形边（>2 面共享的边 + 松散边）
      3. 删除松散顶点和松散边
      4. 合并重叠顶点（by distance）
      5. 重新计算法线

    Returns
    -------
    int : 修复后的面数
    """
    import bpy
    import bmesh

    faces_before = len(obj.data.polygons)

    # 确保对象可选、可见、OBJECT 模式
    obj.hide_set(False)
    obj.hide_viewport = False
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    bm = bmesh.new()
    bm.from_mesh(obj.data)

    # --- 1. 删除退化面（面积≈0 或 法线≈0）---
    degenerate_faces = []
    for face in bm.faces:
        if face.calc_area() < 1e-12 or face.normal.length < 1e-6:
            degenerate_faces.append(face)
    if degenerate_faces:
        bmesh.ops.delete(bm, geom=degenerate_faces, context='FACES')
        if verbose:
            print(f"    [Repair] Removed {len(degenerate_faces)} degenerate faces")

    # --- 2. 删除非流形边（>2 面共享的边 + 只连 1 面的内部边）---
    non_manifold_edges = []
    for edge in bm.edges:
        if not edge.is_manifold and not edge.is_boundary:
            non_manifold_edges.append(edge)
    if non_manifold_edges:
        nm_faces = set()
        for edge in non_manifold_edges:
            for face in edge.link_faces:
                nm_faces.add(face)
        if nm_faces:
            bmesh.ops.delete(bm, geom=list(nm_faces), context='FACES')
        if verbose:
            print(f"    [Repair] Removed {len(non_manifold_edges)} non-manifold edges "
                  f"({len(nm_faces)} faces)")

    # --- 3. 删除松散几何（无面引用的边和顶点）---
    loose_verts = []
    for vert in bm.verts:
        if not vert.link_faces:
            loose_verts.append(vert)
    loose_edges = []
    for edge in bm.edges:
        if not edge.link_faces:
            loose_edges.append(edge)

    if loose_edges:
        bmesh.ops.delete(bm, geom=loose_edges, context='EDGES')
    if loose_verts:
        remaining_loose = [v for v in bm.verts if not v.link_faces and not v.link_edges]
        if remaining_loose:
            bmesh.ops.delete(bm, geom=remaining_loose, context='VERTS')
    loose_total = len(loose_verts) + len(loose_edges)
    if verbose and loose_total > 0:
        print(f"    [Repair] Removed {len(loose_edges)} loose edges, {len(loose_verts)} loose verts")

    # --- 4. 合并重叠顶点（by distance）---
    if merge_distance > 0:
        verts_before_merge = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=merge_distance)
        merged = verts_before_merge - len(bm.verts)
        if verbose and merged > 0:
            print(f"    [Repair] Merged {merged} overlapping verts (dist={merge_distance})")

    # --- 5. 重新计算法线 ---
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    # 写回 mesh
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    faces_after = len(obj.data.polygons)
    if verbose and faces_after != faces_before:
        print(f"    [Repair] Faces: {faces_before:,} → {faces_after:,} "
              f"({faces_before - faces_after:,} removed)")

    return faces_after


def _decimate_with_retry(obj_high, target_faces=DEFAULT_TARGET_FACES,
                         tolerance=DEFAULT_TOLERANCE, name="bake"):
    """在 Blender 内迭代减面，自动用二分法调整 ratio 直到面数误差在 tolerance 以内。

    每次尝试：复制高模 → Decimate COLLAPSE → 检查面数
    不达标则删除副本，调整 ratio 后重试。
    达标则保留该副本作为最终低模，删除所有中间临时对象。

    当检测到停滞（连续 3 次面数相同）时提前退出，
    说明 Decimate COLLAPSE 已到达该拓扑的减面下限。
    """
    import bpy

    high_faces = len(obj_high.data.polygons)
    target_faces = max(min(target_faces, high_faces), 4)

    # 二分搜索边界
    lo_ratio = 4.0 / high_faces   # 最小 ratio（~4面）
    hi_ratio = 1.0                 # 最大 ratio（=高模本身）
    ratio = target_faces / high_faces  # 初始估计

    max_attempts = 20
    last_obj = None
    stagnation_count = 0
    last_face_count = None

    for attempt in range(1, max_attempts + 1):
        # 清理上一次尝试的临时对象
        if last_obj is not None:
            mesh_data = last_obj.data
            bpy.data.objects.remove(last_obj, do_unlink=True)
            if mesh_data.users == 0:
                bpy.data.meshes.remove(mesh_data)
            last_obj = None

        # 复制高模并减面
        obj_low = obj_high.copy()
        obj_low.data = obj_high.data.copy()
        obj_low.name = f"{name}_Low_tmp"
        bpy.context.collection.objects.link(obj_low)

        # 选择低模（不用 select_all，避免 MCP 兼容性问题）
        for o in bpy.context.scene.objects:
            o.select_set(False)
        obj_low.select_set(True)
        bpy.context.view_layer.objects.active = obj_low

        decimate = obj_low.modifiers.new(name="DecimateBake", type='DECIMATE')
        decimate.decimate_type = 'COLLAPSE'
        decimate.ratio = ratio
        bpy.ops.object.modifier_apply(modifier=decimate.name)

        actual_faces = len(obj_low.data.polygons)
        diff = abs(actual_faces - target_faces) / target_faces

        print(f"    Attempt {attempt}: ratio={ratio:.6f} → {actual_faces} faces "
              f"(target={target_faces}, diff={diff:.1%})")

        if diff <= tolerance:
            print(f"    ✓ 面数达标: {actual_faces} (误差 {diff:.1%} ≤ {tolerance:.0%})")
            obj_low.name = f"{name}_Low"
            return obj_low, actual_faces

        # 检测停滞：连续面数相同说明 COLLAPSE 已到下限
        if actual_faces == last_face_count:
            stagnation_count += 1
            if stagnation_count >= 3:
                print(f"    ⚠ 检测到停滞：连续 {stagnation_count} 次面数相同 ({actual_faces})，"
                      f"COLLAPSE 已达该拓扑的减面下限")
                break
        else:
            stagnation_count = 0
        last_face_count = actual_faces

        # 未达标，调整二分搜索边界
        if actual_faces > target_faces:
            hi_ratio = ratio
        else:
            lo_ratio = ratio

        last_obj = obj_low
        ratio = (lo_ratio + hi_ratio) / 2.0

    # 使用最后一次结果（可能是停滞退出或最大尝试次数）
    if last_obj is None:
        last_obj = obj_low
    actual_faces = len(last_obj.data.polygons)
    diff = abs(actual_faces - target_faces) / target_faces
    print(f"    最终结果: {actual_faces} faces (误差 {diff:.1%})")
    last_obj.name = f"{name}_Low"
    return last_obj, actual_faces


# ============ CLI 入口 ============
def main():
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1:]
    else:
        argv = []

    import argparse
    p = argparse.ArgumentParser(description="Blender 内减面脚本")
    p.add_argument("--high", required=True, help="场景中高模对象名")
    p.add_argument("--target_faces", type=int, default=DEFAULT_TARGET_FACES,
                   help=f"目标面数（默认 {DEFAULT_TARGET_FACES}）")
    p.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                   help=f"面数允许误差（默认 {DEFAULT_TOLERANCE}）")
    p.add_argument("--name", default=None, help="低模名称前缀（默认=高模名去掉_High）")
    p.add_argument("--no_repair", action="store_true",
                   help="禁用流形修复（默认启用）")
    p.add_argument("--merge_distance", type=float, default=DEFAULT_MERGE_DISTANCE,
                   help=f"合并重叠顶点距离阈值（默认 {DEFAULT_MERGE_DISTANCE}）")
    args = p.parse_args(argv)

    decimate_blender(
        high_obj_name=args.high,
        target_faces=args.target_faces,
        tolerance=args.tolerance,
        name=args.name,
        repair_mesh=not args.no_repair,
        merge_distance=args.merge_distance,
    )


if __name__ == "__main__":
    main()
