"""
统一烘焙脚本 — 高模(带纹理) → 低模 → 烘焙 Albedo+Normal → 结果留在 Blender 场景中

支持两种模式：
  模式 A（推荐）：Blender 内减面 — 导入高模 → 复制 → Decimate modifier → 烘焙
    优点：高低模同坐标系、同法线，无需对齐/法线修复，零色块风险
    自动重试：Decimate 无法精确控制面数 → 自动用二分法调整 ratio 直到误差<10%
  模式 B（兼容）：外部减面 — 导入高模 FBX + 低模 OBJ → 对齐 → 修复法线 → 烘焙

输出：烘焙结果（高模+低模+材质）直接留在 Blender 场景中，不输出 PNG 文件

用法：
    from bake import bake_high_to_low

    # 模式 A（推荐）：Blender 内减面
    bake_high_to_low(high_fbx="/path/to/high.fbx", name="MyModel",
                     target_faces=3000, cage=0.1)

    # 模式 B：外部低模 OBJ
    bake_high_to_low(high_fbx="/path/to/high.fbx", low_obj="/path/to/low.obj",
                     name="MyModel")

核心规则：
- Albedo 必须用 EMIT 烘焙（禁用 DIFFUSE）
- 烘焙图底色：白色 / 法线蓝，禁止黑色
- 参数：cage=0.1, res=4096, samples=64, Cycles+GPU
- Smart UV island_margin=0.01
- Blender 内减面用 COLLAPSE，面数不精确时自动二分法搜索
"""

import sys
import os
import math
from pathlib import Path

# ============ 统一参数 ============
DEFAULT_CAGE = 0.1          # cage extrusion 距离
DEFAULT_RES = 4096          # 4K
DEFAULT_SAMPLES = 64
DEFAULT_DEVICE = "GPU"
DEFAULT_TARGET_FACES = 3000 # 低模目标面数
DEFAULT_UV_MARGIN = 0.01    # Smart UV island_margin
DEFAULT_TOLERANCE = 0.1     # 面数允许误差 10%


def _validate_high_material(obj_high):
    """前置校验：高模材质是否有效（非 Blender 默认紫色）。
    返回 (ok, msg)"""
    if not obj_high.material_slots:
        return False, f"高模 '{obj_high.name}' 无材质槽"

    for i, slot in enumerate(obj_high.material_slots):
        m = slot.material
        if m is None:
            return False, f"高模 '{obj_high.name}' 材质槽 [{i}] 为空"

        if not m.use_nodes:
            dc = tuple(m.diffuse_color)
            if _is_default_purple(dc):
                return False, f"高模 '{obj_high.name}' 材质 '{m.name}' 是默认紫色 {dc}"
            continue

        bsdf = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue

        bc = bsdf.inputs["Base Color"]
        if not bc.is_linked:
            dc = tuple(bc.default_value)[:3]
            if _is_default_purple(dc):
                return False, (
                    f"高模 '{obj_high.name}' 材质 '{m.name}' BSDF Base Color "
                    f"无连接且为默认紫色 {dc:.3f}"
                )

    return True, "OK"


def _is_default_purple(color_rgb, tol=0.05):
    """检测 Blender 默认紫色 ≈ (0.8, 0.8, 1.0)"""
    if len(color_rgb) < 3:
        return False
    r, g, b = color_rgb[0], color_rgb[1], color_rgb[2]
    return (abs(r - 0.8) < tol and abs(g - 0.8) < tol and abs(b - 1.0) < tol)


def _validate_uv(obj_low):
    """校验低模 UV 是否有效。返回 (ok, msg)"""
    if not obj_low.data.uv_layers:
        return False, f"低模 '{obj_low.name}' 无 UV 层"

    uv_layer = obj_low.data.uv_layers.active
    uv_data = uv_layer.data

    in_range = 0
    total = len(uv_data)
    if total == 0:
        return False, f"低模 '{obj_low.name}' UV 数据为空"

    for loop_uv in uv_data:
        u, v = loop_uv.uv
        if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0:
            in_range += 1

    ratio = in_range / total
    if ratio < 0.3:
        return False, f"低模 '{obj_low.name}' UV 仅 {ratio:.1%} 在 [0,1] 范围内，可能未展开"

    return True, f"UV 有效 ({ratio:.1%} in [0,1])"


def _validate_bbox_alignment(obj_high, obj_low, tol=0.5):
    """校验高低模 BBox 是否对齐。返回 (ok, msg)"""
    import mathutils
    h_bb = [obj_high.matrix_world @ mathutils.Vector(c) for c in obj_high.bound_box]
    l_bb = [obj_low.matrix_world @ mathutils.Vector(c) for c in obj_low.bound_box]
    max_diff = max((h_bb[i] - l_bb[i]).length for i in range(8))
    ok = max_diff < tol
    return ok, f"BBox max diff={max_diff:.4f} ({'OK' if ok else 'MISMATCH'})"


def _align_low_to_high(obj_high, obj_low):
    """对齐低模到高模：
    1. 两者先 apply transform（清零旋转/缩放）
    2. 用 BBox 中心匹配计算平移偏移
    3. 如中心匹配后仍有偏差，用顶点采样精修
    """
    import bpy
    import mathutils
    import random

    for obj in (obj_high, obj_low):
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        obj.location = (0, 0, 0)
        obj.rotation_euler = (0, 0, 0)
        obj.scale = (1, 1, 1)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    def bbox_center(obj):
        bb = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
        return sum((bb[i] for i in range(8)), mathutils.Vector()) / 8.0

    high_c = bbox_center(obj_high)
    low_c = bbox_center(obj_low)
    offset = high_c - low_c
    print(f"    High center: {tuple(high_c)}")
    print(f"    Low center:  {tuple(low_c)}")
    print(f"    Translation offset: {tuple(offset)}")

    obj_low.location = offset
    bpy.ops.object.select_all(action="DESELECT")
    obj_low.select_set(True)
    bpy.context.view_layer.objects.active = obj_low
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    random.seed(42)
    high_verts = [obj_high.matrix_world @ v.co for v in obj_high.data.vertices]
    high_sample = random.sample(high_verts, min(500, len(high_verts)))

    from mathutils.kdtree import KDTree
    low_verts = [obj_low.matrix_world @ v.co for v in obj_low.data.vertices]
    kd = KDTree(len(low_verts))
    for i, v in enumerate(low_verts):
        kd.insert(v, i)
    kd.balance()

    total_dist = sum(kd.find(hv)[2] for hv in high_sample)
    avg_dist = total_dist / len(high_sample)
    print(f"    After alignment: avg vertex dist={avg_dist:.6f}")

    if avg_dist > 0.02:
        print(f"    尝试 24 种旋转搜索以检测轴向偏差 (avg_dist={avg_dist:.4f})...")
        angles = [0, math.pi/2, math.pi, 3*math.pi/2]
        candidates = [(rx, ry, rz) for rx in angles for ry in angles for rz in angles]

        obj_low.location = (0, 0, 0)
        obj_low.rotation_euler = (0, 0, 0)
        bpy.ops.object.select_all(action="DESELECT")
        obj_low.select_set(True)
        bpy.context.view_layer.objects.active = obj_low
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        best_rot, best_score = (0, 0, 0), float("inf")
        for rot in candidates:
            obj_low.rotation_euler = rot
            bpy.context.view_layer.update()
            low_c2 = bbox_center(obj_low)
            off2 = high_c - low_c2
            obj_low.location = off2
            bpy.context.view_layer.update()

            low_verts2 = [obj_low.matrix_world @ v.co for v in obj_low.data.vertices]
            kd2 = KDTree(len(low_verts2))
            for i, v in enumerate(low_verts2):
                kd2.insert(v, i)
            kd2.balance()
            score = sum(kd2.find(hv)[2] for hv in high_sample) / len(high_sample)
            if score < best_score:
                best_score = score
                best_rot = rot

            obj_low.location = (0, 0, 0)

        print(f"    Best rotation: ({best_rot[0]:.4f}, {best_rot[1]:.4f}, {best_rot[2]:.4f}) "
              f"avg_dist={best_score:.6f}")
        obj_low.rotation_euler = best_rot
        bpy.context.view_layer.update()
        low_c3 = bbox_center(obj_low)
        obj_low.location = high_c - low_c3
        bpy.ops.object.select_all(action="DESELECT")
        obj_low.select_set(True)
        bpy.context.view_layer.objects.active = obj_low
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def _setup_smart_uv(obj, margin=DEFAULT_UV_MARGIN):
    """给低模做 Smart UV Project。"""
    import bpy
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15191, island_margin=margin)
    bpy.ops.object.mode_set(mode="OBJECT")
    print(f"    Smart UV Project done (island_margin={margin})")


def _ensure_consistent_normals(obj, high_obj=None):
    """确保低模法线方向与高模一致，使烘焙射线能命中高模表面。"""
    import bpy

    if high_obj is None:
        print("    跳过法线检查（无高模参照）")
        return

    mesh = obj.data
    total = len(mesh.polygons)

    depsgraph = bpy.context.evaluated_depsgraph_get()
    sample_n = min(50, total)
    step = max(1, total // sample_n)
    hit_before = 0

    for i in range(0, total, step):
        poly = mesh.polygons[i]
        center = obj.matrix_world @ poly.center
        normal = (obj.matrix_world.to_3x3() @ poly.normal).normalized()
        start = center + normal * 0.001
        result = bpy.context.scene.ray_cast(depsgraph, start, start + normal * 2.0)
        if len(result) >= 3 and result[2] >= 0:
            hit_obj = result[3] if len(result) >= 4 else None
            if hit_obj == high_obj:
                hit_before += 1

    tested = total // step
    ratio = hit_before / max(tested, 1)
    print(f"    法线方向检测: {hit_before}/{tested} 面命中高模 ({ratio:.0%})")

    if ratio < 0.3:
        print(f"    ⚠️ 法线方向可能反转！尝试翻转...")

        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.flip_normals()
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.object.mode_set(mode="OBJECT")

        hit_after = 0
        for i in range(0, total, step):
            poly = mesh.polygons[i]
            center = obj.matrix_world @ poly.center
            normal = (obj.matrix_world.to_3x3() @ poly.normal).normalized()
            start = center + normal * 0.001
            result = bpy.context.scene.ray_cast(depsgraph, start, start + normal * 2.0)
            if len(result) >= 3 and result[2] >= 0:
                hit_obj = result[3] if len(result) >= 4 else None
                if hit_obj == high_obj:
                    hit_after += 1

        new_ratio = hit_after / max(tested, 1)
        print(f"    翻转后: {hit_after}/{tested} 面命中高模 ({new_ratio:.0%})")

        if new_ratio > ratio:
            print(f"    ✓ 法线翻转成功（{ratio:.0%} → {new_ratio:.0%}）")
        else:
            print(f"    ✗ 翻转没有改善，回滚...")
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.mesh.flip_normals()
            bpy.ops.mesh.select_all(action="DESELECT")
            bpy.ops.object.mode_set(mode="OBJECT")
    else:
        print(f"    ✓ 法线方向正确（无需翻转）")


def _decimate_with_retry(obj_high, target_faces=DEFAULT_TARGET_FACES,
                         tolerance=DEFAULT_TOLERANCE, name="bake"):
    """在 Blender 内迭代减面，自动用二分法调整 ratio 直到面数误差在 tolerance 以内。

    每次尝试：复制高模 → Decimate COLLAPSE → 检查面数
    不达标则删除副本，调整 ratio 后重试。
    达标则保留该副本作为最终低模，删除所有中间临时对象。

    当检测到停滞（连续 3 次面数相同）时提前退出，
    说明 Decimate COLLAPSE 已到达该拓扑的减面下限。

    参数:
        obj_high: 高模对象
        target_faces: 目标面数
        tolerance: 允许的面数误差比例（默认 0.1 = 10%）
        name: 低模名称前缀
    返回:
        (obj_low, actual_faces): 最终低模对象和实际面数
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

        bpy.ops.object.select_all(action="DESELECT")
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
            obj_low.data.materials.clear()
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
        # 极端情况：第一次就达标了（但前面已经 return 了，不应该到这里）
        last_obj = obj_low
    actual_faces = len(last_obj.data.polygons)
    diff = abs(actual_faces - target_faces) / target_faces
    print(f"    最终结果: {actual_faces} faces (误差 {diff:.1%})")
    last_obj.name = f"{name}_Low"
    last_obj.data.materials.clear()
    return last_obj, actual_faces


def _make_bake_image(name, colorspace, base_color, is_data, res):
    """创建烘焙图像（强制非黑底色）。"""
    import bpy
    import tempfile
    img = bpy.data.images.new(name, res, res, alpha=False, is_data=is_data)
    img.colorspace_settings.name = colorspace
    img.generated_color = base_color
    img.file_format = "PNG"
    # 必须在 scale() 之前设置可写的 filepath_raw，
    # 否则 Blender 默认用相对路径（当前目录可能只读）
    tmp_dir = tempfile.mkdtemp(prefix="bake_img_")
    img.filepath_raw = os.path.join(tmp_dir, f"{name}.png")
    img.scale(img.size[0], img.size[1])  # 强制刷新
    return img


def _emit_bake_albedo(obj_high, obj_low, img_albedo, nt, n_albedo):
    """EMIT 法烘焙 Albedo：备份→替换→烘焙→还原。"""
    import bpy

    backups = []
    for slot in obj_high.material_slots:
        m = slot.material
        if m is None or not m.use_nodes:
            continue
        m_nt = m.node_tree
        m_bsdf = next((n for n in m_nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        m_out = next((n for n in m_nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
        if m_bsdf is None or m_out is None:
            continue

        bc_input = m_bsdf.inputs["Base Color"]
        bc_src = bc_input.links[0].from_socket if bc_input.is_linked else None
        bc_default = tuple(bc_input.default_value)

        surf_input = m_out.inputs["Surface"]
        orig_surf_src = surf_input.links[0].from_socket if surf_input.is_linked else None

        emit = m_nt.nodes.new("ShaderNodeEmission")
        emit.label = "__bake_tmp__"
        if bc_src:
            m_nt.links.new(bc_src, emit.inputs["Color"])
        else:
            emit.inputs["Color"].default_value = bc_default

        for link in list(surf_input.links):
            m_nt.links.remove(link)
        m_nt.links.new(emit.outputs["Emission"], surf_input)

        backups.append((m_nt, surf_input, orig_surf_src, emit))

    bpy.ops.object.select_all(action="DESELECT")
    obj_high.select_set(True)
    obj_low.select_set(True)
    bpy.context.view_layer.objects.active = obj_low
    for n in nt.nodes:
        n.select = False
    n_alb = n_albedo
    n_alb.select = True
    nt.nodes.active = n_alb

    bpy.context.scene.cycles.bake_type = "EMIT"
    print("    BAKE START: Albedo (EMIT)")
    bpy.ops.object.bake(type="EMIT")
    print("    BAKE DONE: Albedo (EMIT)")

    for m_nt, surf_input, orig_surf_src, emit in backups:
        for link in list(surf_input.links):
            m_nt.links.remove(link)
        if orig_surf_src:
            m_nt.links.new(orig_surf_src, surf_input)
        m_nt.nodes.remove(emit)


def _bake_normal(obj_high, obj_low, img_normal, nt, n_norm):
    """烘焙 Normal 贴图。"""
    import bpy

    bpy.ops.object.select_all(action="DESELECT")
    obj_high.select_set(True)
    obj_low.select_set(True)
    bpy.context.view_layer.objects.active = obj_low
    for n in nt.nodes:
        n.select = False
    n_norm.select = True
    nt.nodes.active = n_norm

    bpy.context.scene.cycles.bake_type = "NORMAL"
    print("    BAKE START: Normal")
    bpy.ops.object.bake(type="NORMAL")
    print("    BAKE DONE: Normal")


def purge_bake_residue(verbose: bool = True):
    """彻底清除场景中所有"潜在污染源"，让场景回到可以干净导入新模型的状态。

    重点解决以下污染：
    - 隐藏的高低模对象（hide_viewport=True 时 select_all 漏选）
    - 它们引用的原始 PBR 纹理（被引用 → 后续导入同名贴图变 .001）
    - 烘焙残留的 bake_*_albedo / bake_*_normal 图像
    - 所有孤立 mesh/material/image/texture

    调用场景：
    - 烘焙完成后、即将导入新模型前
    - 任何 `import_scene.fbx` 之前
    """
    import bpy

    # 1. 强制删除所有 MESH 对象（包括 hidden）
    n_objs = 0
    for obj in list(bpy.data.objects):
        if obj.type == "MESH":
            bpy.data.objects.remove(obj, do_unlink=True)
            n_objs += 1
    if verbose:
        print(f"    Purged {n_objs} mesh objects (including hidden)")

    # 2. 多轮孤立清理（删 obj 后释放 mesh/material → 释放 image → 释放 texture）
    total_removed = 0
    for _r in range(5):
        removed = 0
        for bt in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.textures):
            for block in list(bt):
                # 跳过 fake_user 和系统块
                if block.users == 0 and not block.use_fake_user:
                    bt.remove(block)
                    removed += 1
        if removed == 0:
            break
        total_removed += removed
        if verbose:
            print(f"    Cleanup round {_r+1}: removed {removed} blocks")

    # 3. 残留检查
    leftover = {
        "objects": len([o for o in bpy.data.objects if o.type == "MESH"]),
        "meshes": len(bpy.data.meshes),
        "materials": len(bpy.data.materials),
        "images": len([i for i in bpy.data.images if i.name not in ("Render Result", "Viewer Node")]),
    }
    if verbose:
        print(f"    Purge done. Remaining: {leftover}")
    return leftover


def bake_high_to_low(
    high_fbx: str = None,
    high_obj_name: str = None,
    low_obj: str = None,
    name: str = "bake",
    cage: float = DEFAULT_CAGE,
    target_faces: int = DEFAULT_TARGET_FACES,
    tolerance: float = DEFAULT_TOLERANCE,
    res: int = DEFAULT_RES,
    samples: int = DEFAULT_SAMPLES,
    device: str = DEFAULT_DEVICE,
    uv_margin: float = DEFAULT_UV_MARGIN,
    skip_uv: bool = False,
    skip_align: bool = False,
    max_ray_dist: float = 0.0,
):
    """
    统一烘焙入口：高模 → 低模 → 烘焙 Albedo+Normal → 高模和低模都留在 Blender 场景中。

    模式 A（推荐）：不提供 low_obj → Blender 内自动减面（带二分法重试）
    模式 B（兼容）：提供 low_obj → 导入外部低模 + 对齐

    高模来源（二选一）：
    - high_obj_name: 使用场景中已有的对象（优先）
    - high_fbx: 从文件导入（high_obj_name 为 None 时使用）

    烘焙完成后：
    - 高模和低模都保留在场景中
    - 低模带烘焙材质 bake_<name>（Albedo + Normal）
    - 高模保留原始 PBR 材质和纹理
    - 清理孤立数据块（无引用的 mesh/material/image/texture）

    参数:
        high_fbx: 带纹理的高模路径（FBX 或 OBJ）
        high_obj_name: 场景中已有的高模对象名（优先于 high_fbx）
        low_obj: 低模 OBJ 路径（None=Blender 内自动减面）
        name: 烘焙贴图名称前缀
        cage: cage extrusion 距离（默认 0.1）
        target_faces: 低模目标面数（默认 3000）
        tolerance: 面数允许误差（默认 0.1 = 10%）
        res: 烘焙分辨率
        samples: 采样数
        device: CPU 或 GPU
        uv_margin: Smart UV island margin（默认 0.01）
        skip_uv: 跳过 Smart UV（低模已有 UV 时）
        skip_align: 跳过旋转对齐（仅模式 B）
        max_ray_dist: 最大射线距离，0=无限制
    """
    import bpy
    import mathutils

    if high_obj_name is None and high_fbx is None:
        raise RuntimeError("必须提供 high_obj_name 或 high_fbx 之一")

    mode = "A (Blender内减面)" if low_obj is None else "B (外部OBJ低模)"
    high_src = high_obj_name if high_obj_name else high_fbx
    print(f"\n{'='*70}")
    print(f"BAKE: {name}  [模式 {mode}]")
    print(f"  High:   {high_src} {'(场景对象)' if high_obj_name else '(文件导入)'}")
    if low_obj:
        print(f"  Low:    {low_obj}")
    else:
        print(f"  Low:    自动生成 (target_faces={target_faces}, tolerance={tolerance:.0%})")
    print(f"  Params: cage={cage}, res={res}, samples={samples}, device={device}")
    print(f"  UV:     island_margin={uv_margin}")
    print(f"{'='*70}\n")

    # === 0. 预清理：删除残留的同名烘焙数据，防止重名冲突 ===
    print("[0] Pre-cleanup: removing stale bake data...")
    for img in list(bpy.data.images):
        if img.name.startswith(f"bake_{name}"):
            bpy.data.images.remove(img)
    for mat in list(bpy.data.materials):
        if mat.name.startswith(f"bake_{name}"):
            bpy.data.materials.remove(mat)
    for obj in list(bpy.data.objects):
        if obj.name.startswith(f"{name}_High") or obj.name.startswith(f"{name}_Low"):
            bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    print("    Pre-cleanup done")

    # === 1. 获取高模 ===
    if high_obj_name:
        # 使用场景中已有的对象
        print(f"[1] Using existing scene object '{high_obj_name}' as high-poly...")
        obj_high = bpy.data.objects.get(high_obj_name)
        if obj_high is None:
            raise RuntimeError(f"场景中未找到对象 '{high_obj_name}'")
        if obj_high.type != "MESH":
            raise RuntimeError(f"对象 '{high_obj_name}' 不是 MESH 类型（{obj_high.type}）")
    else:
        # 从文件导入
        print("[1] Importing high-poly model...")
        bpy.ops.object.select_all(action="DESELECT")
        existing = set(bpy.data.objects.keys())

        ext = Path(high_fbx).suffix.lower()
        if ext == ".obj":
            bpy.ops.wm.obj_import(filepath=high_fbx)
        elif ext in (".fbx",):
            bpy.ops.import_scene.fbx(filepath=high_fbx)
        else:
            raise RuntimeError(f"不支持的格式: {ext}（仅支持 .fbx / .obj）")

        new_objs = [o for o in bpy.data.objects if o.name not in existing]
        meshes = [o for o in new_objs if o.type == "MESH"]
        if not meshes:
            raise RuntimeError(f"文件中无 MESH 对象: {high_fbx}")

        if len(meshes) > 1:
            bpy.ops.object.select_all(action="DESELECT")
            for m in meshes:
                m.select_set(True)
            bpy.context.view_layer.objects.active = meshes[0]
            bpy.ops.object.join()

        obj_high = [o for o in bpy.data.objects if o.type == "MESH" and o.select_get()][0] if len(meshes) > 1 else meshes[0]

    obj_high.name = f"{name}_High"
    bpy.ops.object.select_all(action="DESELECT")
    obj_high.select_set(True)
    bpy.context.view_layer.objects.active = obj_high
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    print(f"    High: verts={len(obj_high.data.vertices)}, faces={len(obj_high.data.polygons)}")

    # === 1b. 材质校验 ===
    ok, msg = _validate_high_material(obj_high)
    if not ok:
        raise RuntimeError(f"[FATAL] 高模材质校验失败: {msg}")
    print(f"    材质校验: {msg}")

    # === 2. 获取低模 ===
    if low_obj is None:
        print(f"[2] Creating low-poly via Decimate (target_faces={target_faces}, tolerance={tolerance:.0%})...")
        obj_low, actual_faces = _decimate_with_retry(
            obj_high, target_faces=target_faces, tolerance=tolerance, name=name)
    else:
        print("[2] Importing low-poly OBJ...")
        bpy.ops.object.select_all(action="DESELECT")
        existing = set(bpy.data.objects.keys())
        bpy.ops.wm.obj_import(
            filepath=low_obj,
            forward_axis="NEGATIVE_Z",
            up_axis="Y",
        )
        new_objs = [o for o in bpy.data.objects if o.name not in existing and o.type == "MESH"]
        if not new_objs:
            raise RuntimeError(f"OBJ 导入失败: {low_obj}")
        obj_low = new_objs[0]
        obj_low.name = f"{name}_Low"
        actual_faces = len(obj_low.data.polygons)
        print(f"    Low: verts={len(obj_low.data.vertices)}, faces={actual_faces}")

    # === 3. 对齐（仅模式 B） ===
    if low_obj is not None and not skip_align:
        print("[3] Aligning low to high...")
        _align_low_to_high(obj_high, obj_low)
    else:
        if low_obj is None:
            print("[3] Skipping alignment (Blender内减面，坐标系天然一致)")
        else:
            print("[3] Skipping alignment (skip_align=True)")

    # === 3b. BBox 校验 ===
    ok, msg = _validate_bbox_alignment(obj_high, obj_low)
    print(f"    BBox 校验: {msg}")
    if not ok:
        if low_obj is None:
            raise RuntimeError(f"[FATAL] Blender内减面但BBox不对齐（不应发生）: {msg}")
        else:
            print(f"    [WARN] BBox 未对齐，烘焙可能出错！")

    # === 3c. 法线修复（仅模式 B） ===
    if low_obj is not None:
        print("[3c] Checking/Fixing normals...")
        _ensure_consistent_normals(obj_low, obj_high)
    else:
        print("[3c] Skipping normals fix (Blender内减面，法线天然一致)")

    # === 4. Smart UV ===
    if not skip_uv:
        print(f"[4] Smart UV Project (island_margin={uv_margin})...")
        _setup_smart_uv(obj_low, margin=uv_margin)
    else:
        print("[4] Skipping UV (skip_uv=True)")

    ok, msg = _validate_uv(obj_low)
    print(f"    UV 校验: {msg}")
    if not ok:
        raise RuntimeError(f"[FATAL] UV 校验失败: {msg}")

    # === 5. 创建烘焙图 ===
    print(f"[5] Creating bake images ({res}x{res})...")
    img_albedo = _make_bake_image(
        f"bake_{name}_albedo", "sRGB", (1, 1, 1, 1), False, res
    )
    img_normal = _make_bake_image(
        f"bake_{name}_normal", "Non-Color", (0.5, 0.5, 1, 1), True, res
    )

    # === 6. 低模材质 + 节点树 ===
    print("[6] Setting up low-poly material...")
    mat = bpy.data.materials.new(f"bake_{name}")
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)

    tex_coord = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")
    nt.links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])

    n_alb = nt.nodes.new("ShaderNodeTexImage")
    n_alb.image = img_albedo
    n_alb.label = "albedo"

    n_norm = nt.nodes.new("ShaderNodeTexImage")
    n_norm.image = img_normal
    n_norm.label = "normal"

    for n in (n_alb, n_norm):
        nt.links.new(mapping.outputs["Vector"], n.inputs["Vector"])

    n_normmap = nt.nodes.new("ShaderNodeNormalMap")
    n_normmap.space = "TANGENT"
    nt.links.new(n_norm.outputs["Color"], n_normmap.inputs["Color"])

    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Metallic"].default_value = 0.0
    bsdf.inputs["Roughness"].default_value = 0.8
    out_node = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(n_alb.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(n_normmap.outputs["Normal"], bsdf.inputs["Normal"])
    nt.links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])

    obj_low.data.materials.clear()
    obj_low.data.materials.append(mat)

    # === 7. Cycles 配置 ===
    print(f"[7] Configuring Cycles (cage={cage})...")
    scn = bpy.context.scene
    scn.render.engine = "CYCLES"
    scn.cycles.device = device
    scn.cycles.samples = samples
    scn.render.bake.use_selected_to_active = True
    scn.render.bake.cage_extrusion = cage
    scn.render.bake.use_cage = False

    if max_ray_dist > 0:
        scn.render.bake.max_ray_distance = max_ray_dist
    else:
        scn.render.bake.max_ray_distance = 0.0

    scn.render.bake.normal_space = 'TANGENT'

    # === 8. 烘焙 Albedo (EMIT) ===
    print("[8] Baking Albedo via EMIT...")
    _emit_bake_albedo(obj_high, obj_low, img_albedo, nt, n_alb)

    # === 9. 烘焙 Normal ===
    print("[9] Baking Normal...")
    _bake_normal(obj_high, obj_low, img_normal, nt, n_norm)

    # === 10. 打包烘焙图（确保 .blend 保存时图像数据不丢失）===
    print("[10] Packing bake images into .blend...")
    try:
        img_albedo.pack()
        img_normal.pack()
    except Exception as e:
        print(f"    Pack warning (non-critical): {e}")

    # === 11. 收尾：保留高模和低模 + 隔离高模资源避免污染下次导入 ===
    print("[11] Keeping both models + isolating high-poly resources to prevent pollution...")

    # 关键修复：给高模引用的所有原始资源（图像/材质/纹理/mesh）加上唯一前缀，
    # 让它们脱离与磁盘文件名/原名的关联。这样下次导入同名 FBX 时，Blender 不会
    # 在内存中找到同名 image 而错误复用，会从磁盘正常加载新纹理。
    iso_prefix = f"__baked_{name}__"

    # 1) 重命名高模引用的所有 image（Blender 凭 image.name 决定 FBX 导入是否复用）
    high_images = set()
    high_materials = set()
    for slot in obj_high.material_slots:
        m = slot.material
        if m is None:
            continue
        high_materials.add(m)
        if m.use_nodes:
            for node in m.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image:
                    high_images.add(node.image)

    n_imgs = 0
    for img in high_images:
        if img.name.startswith(iso_prefix):
            continue
        # 同时清空 filepath，进一步防止"按文件路径匹配"的复用逻辑
        img.filepath = ""
        img.filepath_raw = ""
        img.name = iso_prefix + img.name
        n_imgs += 1
    print(f"    Renamed {n_imgs} high-poly textures with prefix '{iso_prefix}'")

    # 2) 重命名高模材质（避免下次导入的 'Material' 撞上现有同名材质，产生 .001）
    n_mats = 0
    for m in high_materials:
        if m.name.startswith(iso_prefix):
            continue
        m.name = iso_prefix + m.name
        n_mats += 1
    print(f"    Renamed {n_mats} high-poly materials")

    # 3) 重命名高模 mesh data（防止 mesh 数据块名冲突）
    if obj_high.data and not obj_high.data.name.startswith(iso_prefix):
        obj_high.data.name = iso_prefix + obj_high.data.name

    # 4) 多轮孤立清理（删 obj 后释放 mesh/material，删 material 后释放 image，链式清理）
    for _round in range(5):
        removed = 0
        for block_type in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.textures):
            for block in list(block_type):
                if block.users == 0 and not block.use_fake_user:
                    block_type.remove(block)
                    removed += 1
        if removed == 0:
            break
        print(f"    Cleanup round {_round + 1}: removed {removed} orphan blocks")

    # 验证最终场景状态
    obj_low_ref = bpy.data.objects.get(obj_low.name)
    obj_high_ref = bpy.data.objects.get(obj_high.name)
    leftover_imgs = [i.name for i in bpy.data.images if i.name not in ("Render Result", "Viewer Node")]

    if obj_low_ref:
        print(f"\n[DONE] {name}: 低模 '{obj_low.name}' 已留在场景中")
        print(f"    面数: {actual_faces} (目标 {target_faces})")
        print(f"    材质: bake_{name} (Albedo + Normal)")
    else:
        print(f"\n[WARN] 低模 '{obj_low.name}' 未找到！")

    if obj_high_ref:
        print(f"    高模 '{obj_high.name}' 已保留在场景中")
    else:
        print(f"    [WARN] 高模 '{obj_high.name}' 未找到！")

    print(f"    最终场景图像: {leftover_imgs}")

    return obj_low.name, actual_faces


# ============ CLI 入口 ============
def main():
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1:]
    else:
        argv = []

    import argparse
    p = argparse.ArgumentParser(description="统一烘焙脚本（结果留在 Blender 场景中）")
    p.add_argument("--high_fbx", default=None, help="带纹理的高模路径（FBX 或 OBJ）")
    p.add_argument("--high_obj_name", default=None, help="场景中已有的高模对象名（优先于 --high_fbx）")
    p.add_argument("--low_obj", default=None, help="低模 OBJ 路径（不提供则 Blender 内自动减面）")
    p.add_argument("--name", default="bake", help="贴图名称前缀")
    p.add_argument("--cage", type=float, default=DEFAULT_CAGE,
                   help=f"Cage extrusion 距离（默认 {DEFAULT_CAGE}）")
    p.add_argument("--target_faces", type=int, default=DEFAULT_TARGET_FACES,
                   help="低模目标面数（默认 3000）")
    p.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                   help=f"面数允许误差（默认 {DEFAULT_TOLERANCE}）")
    p.add_argument("--res", type=int, default=DEFAULT_RES)
    p.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    p.add_argument("--device", default=DEFAULT_DEVICE, choices=["CPU", "GPU"])
    p.add_argument("--uv_margin", type=float, default=DEFAULT_UV_MARGIN,
                   help="Smart UV island margin（默认 0.01）")
    p.add_argument("--skip_uv", action="store_true")
    p.add_argument("--skip_align", action="store_true", help="仅模式 B：跳过对齐")
    p.add_argument("--max_ray_dist", type=float, default=0.0,
                   help="最大射线距离(0=无限制)")
    args = p.parse_args(argv)

    bake_high_to_low(
        high_fbx=args.high_fbx,
        high_obj_name=args.high_obj_name,
        low_obj=args.low_obj,
        name=args.name,
        cage=args.cage,
        target_faces=args.target_faces,
        tolerance=args.tolerance,
        uv_margin=args.uv_margin,
        skip_uv=args.skip_uv,
        skip_align=args.skip_align,
        max_ray_dist=args.max_ray_dist,
    )


if __name__ == "__main__":
    main()
