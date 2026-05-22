#!/usr/bin/env python3
"""
Blender 内减面脚本 — Voxel Remesh（体素化重拓扑）。

使用 Blender 的 Voxel Remesh 修改器，先体素化再重拓扑，
生成均匀面片分布，保体积能力强。与边折叠（COLLAPSE/Quadric）
思路完全不同，适合对比测试。

通过调整 voxel_size 控制面数：voxel_size 越大，面数越少。
使用二分法搜索合适的 voxel_size 以达到目标面数。

用法：
    from decimate_voxel import decimate_voxel

    # 场景中已有 building1 高模
    low_name, actual_faces = decimate_voxel("building1", target_faces=3000)

核心规则：
- 使用 VOXEL 重拓扑模式
- 自动二分法搜索 voxel_size 以达到目标面数
- 减面后清空低模材质（烘焙时会重新赋材质）
"""

import sys

# 统一参数
DEFAULT_TARGET_FACES = 3000
DEFAULT_TOLERANCE = 0.1  # 面数允许误差 10%


def decimate_voxel(
    high_obj_name: str,
    target_faces: int = DEFAULT_TARGET_FACES,
    tolerance: float = DEFAULT_TOLERANCE,
    name: str = None,
    adaptivity: float = 0.0,
):
    """
    Blender Voxel Remesh 减面：复制高模 → 体素化重拓扑 → 二分法调整 voxel_size。

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
    adaptivity : float
        Voxel Remesh 的 adaptivity 参数（0~1），值越大越激进地简化平面区域。

    Returns
    -------
    (str, int)
        低模对象名和实际面数。
    """
    import bpy

    print(f"\n{'='*70}")
    print(f"DECIMATE (Voxel Remesh)")
    print(f"  High:       {high_obj_name}")
    print(f"  Target:     {target_faces} faces (tolerance={tolerance:.0%})")
    print(f"  Adaptivity: {adaptivity}")
    print(f"{'='*70}\n")

    obj_high = bpy.data.objects.get(high_obj_name)
    if obj_high is None:
        raise RuntimeError(f"场景中未找到对象 '{high_obj_name}'")
    if obj_high.type != "MESH":
        raise RuntimeError(f"对象 '{high_obj_name}' 不是 MESH 类型（{obj_high.type}）")

    obj_high.hide_set(False)
    obj_high.hide_viewport = False

    high_faces = len(obj_high.data.polygons)
    print(f"[1] High-poly: {len(obj_high.data.vertices)} verts, {high_faces} faces")

    if name is None:
        name = high_obj_name.replace("_High", "").replace("_high", "")

    # === 2. 预清理 ===
    print("[2] Pre-cleanup...")
    for obj in list(bpy.data.objects):
        if obj.name == f"{name}_VoxelLow" or obj.name == f"{name}_VoxelLow_tmp":
            mesh_data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh_data.users == 0:
                bpy.data.meshes.remove(mesh_data)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    print("    Pre-cleanup done")

    # === 3. 估算初始 voxel_size ===
    # BBox 对角线长度 / sqrt(target_faces) 作为初始估计
    import math
    bbox = [obj_high.matrix_world @ v.co for v in obj_high.data.vertices]
    if not bbox:
        raise RuntimeError(f"对象 '{high_obj_name}' 无顶点")
    xs = [v.x for v in bbox]
    ys = [v.y for v in bbox]
    zs = [v.z for v in bbox]
    diag = math.sqrt(
        (max(xs) - min(xs)) ** 2 +
        (max(ys) - min(ys)) ** 2 +
        (max(zs) - min(zs)) ** 2
    )
    # 粗略估计：面数 ∝ (diag / voxel_size)^2
    initial_voxel = diag / math.sqrt(target_faces * 2)
    print(f"[3] Estimated initial voxel_size: {initial_voxel:.6f} (diag={diag:.4f})")

    # === 4. 二分法搜索 voxel_size ===
    print(f"[4] Binary search for voxel_size (target={target_faces})...")

    lo_voxel = initial_voxel * 0.01   # 很小的 voxel → 面数很多
    hi_voxel = initial_voxel * 10.0   # 很大的 voxel → 面数很少
    voxel_size = initial_voxel

    max_attempts = 20
    last_obj = None
    stagnation_count = 0
    last_face_count = None

    for attempt in range(1, max_attempts + 1):
        # 清理上一次
        if last_obj is not None:
            mesh_data = last_obj.data
            bpy.data.objects.remove(last_obj, do_unlink=True)
            if mesh_data.users == 0:
                bpy.data.meshes.remove(mesh_data)
            last_obj = None

        # 复制高模
        obj_low = obj_high.copy()
        obj_low.data = obj_high.data.copy()
        obj_low.name = f"{name}_VoxelLow_tmp"
        bpy.context.collection.objects.link(obj_low)

        # 选择低模
        for o in bpy.context.scene.objects:
            o.select_set(False)
        obj_low.select_set(True)
        bpy.context.view_layer.objects.active = obj_low

        # 应用 Voxel Remesh
        remesh = obj_low.modifiers.new(name="VoxelRemesh", type='REMESH')
        remesh.mode = 'VOXEL'
        remesh.voxel_size = voxel_size
        remesh.adaptivity = adaptivity
        remesh.use_smooth_shade = True
        bpy.ops.object.modifier_apply(modifier=remesh.name)

        # Shade smooth
        bpy.ops.object.shade_smooth()

        actual_faces = len(obj_low.data.polygons)
        diff = abs(actual_faces - target_faces) / max(target_faces, 1)

        print(f"    Attempt {attempt}: voxel_size={voxel_size:.6f} → {actual_faces} faces "
              f"(target={target_faces}, diff={diff:.1%})")

        if diff <= tolerance:
            print(f"    ✓ 面数达标: {actual_faces} (误差 {diff:.1%} ≤ {tolerance:.0%})")
            obj_low.name = f"{name}_VoxelLow"
            break

        # 检测停滞
        if actual_faces == last_face_count:
            stagnation_count += 1
            if stagnation_count >= 3:
                print(f"    ⚠ 检测到停滞：连续 {stagnation_count} 次面数相同 ({actual_faces})")
                obj_low.name = f"{name}_VoxelLow"
                break
        else:
            stagnation_count = 0
        last_face_count = actual_faces

        # 调整二分边界
        # voxel_size 越大 → 面数越少; voxel_size 越小 → 面数越多
        if actual_faces > target_faces:
            lo_voxel = voxel_size  # 面太多，增大 voxel
        else:
            hi_voxel = voxel_size  # 面太少，减小 voxel
        voxel_size = (lo_voxel + hi_voxel) / 2.0

        last_obj = obj_low
    else:
        # 达到最大尝试次数
        if last_obj is None:
            last_obj = obj_low
        last_obj.name = f"{name}_VoxelLow"
        obj_low = last_obj

    # === 5. 清空低模材质 ===
    obj_low.data.materials.clear()

    actual_faces = len(obj_low.data.polygons)
    diff = abs(actual_faces - target_faces) / max(target_faces, 1)
    print(f"\n[DONE] Voxel Remesh complete:")
    print(f"    Low-poly: '{obj_low.name}' — {actual_faces} faces (diff={diff:.1%})")
    print(f"    High-poly: '{obj_high.name}' — {high_faces} faces (preserved)")

    return obj_low.name, actual_faces


# ============ CLI 入口 ============
def main():
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1:]
    else:
        argv = []

    import argparse
    p = argparse.ArgumentParser(description="Blender Voxel Remesh 减面脚本")
    p.add_argument("--high", required=True, help="场景中高模对象名")
    p.add_argument("--target_faces", type=int, default=DEFAULT_TARGET_FACES,
                   help=f"目标面数（默认 {DEFAULT_TARGET_FACES}）")
    p.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                   help=f"面数允许误差（默认 {DEFAULT_TOLERANCE}）")
    p.add_argument("--name", default=None, help="低模名称前缀")
    p.add_argument("--adaptivity", type=float, default=0.0,
                   help="Voxel Remesh adaptivity (0~1, 默认 0.0)")
    args = p.parse_args(argv)

    decimate_voxel(
        high_obj_name=args.high,
        target_faces=args.target_faces,
        tolerance=args.tolerance,
        name=args.name,
        adaptivity=args.adaptivity,
    )


if __name__ == "__main__":
    main()
