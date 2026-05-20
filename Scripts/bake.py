"""
统一烘焙脚本 — 高模(带纹理) → 低模(OBJ) → 烘焙 Albedo+Normal → 保存贴图

用法：
    # Blender CLI 模式（完整自动化）:
    /Applications/Blender.app/Contents/MacOS/Blender --background --python Scripts/bake.py -- \
        --high_fbx /path/to/high.fbx --low_obj /path/to/low.obj --out_dir /path/to/output \
        [--name MyModel] [--cage 1.0] [--res 4096] [--samples 64]

    # Blender 内部模式（通过 MCP execute_blender_code 调用）:
    from bake import bake_high_to_low
    bake_high_to_low(high_fbx="/path/to/high.fbx", low_obj="/path/to/low.obj",
                     out_dir="/path/to/output", name="MyModel")

核心规则（已写入 KB）：
- Albedo 必须用 EMIT 烘焙（禁用 DIFFUSE）
- 烘焙图底色：白色 / 法线蓝，禁止黑色
- 建筑类参数：cage=1.0, res=4096, samples=64, Cycles+GPU
- OBJ 导入后必须校验材质非默认紫色
"""

import sys
import os
import math
import shutil
import tempfile
from pathlib import Path

# ============ 统一参数（一处定义） ============
DEFAULT_CAGE = 1.0        # 建筑 cage；小物件/武器用 0.2
DEFAULT_RES = 4096        # 4K
DEFAULT_SAMPLES = 64
DEFAULT_DEVICE = "GPU"    # 优先 GPU


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
            # 非节点材质，检查 diffuse_color
            dc = tuple(m.diffuse_color)
            if _is_default_purple(dc):
                return False, f"高模 '{obj_high.name}' 材质 '{m.name}' 是默认紫色 {dc}"
            continue

        # 节点材质：找 BSDF 的 Base Color
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

    # 统计有效 UV（在 [0,1] 范围内的比例）
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

    # 先清零两者 transform
    for obj in (obj_high, obj_low):
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        obj.location = (0, 0, 0)
        obj.rotation_euler = (0, 0, 0)
        obj.scale = (1, 1, 1)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    # 计算 BBox 中心
    def bbox_center(obj):
        bb = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
        return sum((bb[i] for i in range(8)), mathutils.Vector()) / 8.0

    high_c = bbox_center(obj_high)
    low_c = bbox_center(obj_low)
    offset = high_c - low_c
    print(f"    High center: {tuple(high_c)}")
    print(f"    Low center:  {tuple(low_c)}")
    print(f"    Translation offset: {tuple(offset)}")

    # 应用平移偏移到低模
    obj_low.location = offset
    bpy.ops.object.select_all(action="DESELECT")
    obj_low.select_set(True)
    bpy.context.view_layer.objects.active = obj_low
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    # 验证：用顶点采样检查对齐质量
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

    if avg_dist > 0.5:
        print(f"    [WARN] avg_dist={avg_dist:.4f} 仍然很大，尝试 24 种旋转搜索...")
        # BBox 匹配失败，回退到旋转搜索
        angles = [0, math.pi/2, math.pi, 3*math.pi/2]
        candidates = [(rx, ry, rz) for rx in angles for ry in angles for rz in angles]

        # 恢复低模到原点
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
            # 对每种旋转，先做中心匹配再做距离计算
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

            # 重置
            obj_low.location = (0, 0, 0)

        print(f"    Best rotation: ({best_rot[0]:.4f}, {best_rot[1]:.4f}, {best_rot[2]:.4f}) "
              f"avg_dist={best_score:.6f}")
        obj_low.rotation_euler = best_rot
        bpy.context.view_layer.update()
        # 最终对齐中心
        low_c3 = bbox_center(obj_low)
        obj_low.location = high_c - low_c3
        bpy.ops.object.select_all(action="DESELECT")
        obj_low.select_set(True)
        bpy.context.view_layer.objects.active = obj_low
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def _setup_smart_uv(obj):
    """给低模做 Smart UV Project。"""
    import bpy
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15191, island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")


def _make_bake_image(name, colorspace, base_color, is_data, res):
    """创建烘焙图像（强制非黑底色）。"""
    import bpy
    img = bpy.data.images.new(name, res, res, alpha=False, is_data=is_data)
    img.colorspace_settings.name = colorspace
    img.generated_color = base_color
    img.scale(img.size[0], img.size[1])  # 强制刷新
    img.file_format = "PNG"
    return img


def _save_bake_image(img, filepath, is_srgb=True):
    """直接从 img.pixels 读取烘焙数据，手动转 sRGB，用 struct+zlib 写 PNG。
    
    为什么不用 Blender 原生 save()/save_render()/pack+save：
    - bake 目标图同时作为低模材质输入 → Blender 报"循环依赖"警告
    - save()         → 保存 generated_color 而非烘焙数据
    - save_render()  → 应用视图变换导致偏色
    - pack+save      → 大图(4K)时 tmp.pixels=67M 浮点列表赋值不稳定
    - 唯一可靠方案：直接读 pixels → numpy 转 sRGB → struct+zlib 写 PNG
    """
    import numpy as np
    import struct
    import zlib

    width, height = img.size[0], img.size[1]
    pixels = np.array(img.pixels, dtype=np.float64).reshape(height, width, 4)

    if is_srgb:
        # 线性 → sRGB (BT.709)
        rgb = pixels[:, :, :3].copy()
        mask = rgb <= 0.0031308
        srgb = np.where(mask, rgb * 12.92,
                        1.055 * np.power(np.maximum(rgb, 1e-10), 1.0 / 2.4) - 0.055)
        rgb_out = np.clip(srgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    else:
        # Non-Color：线性值直接量化
        rgb_out = np.clip(pixels[:, :, :3] * 255.0 + 0.5, 0, 255).astype(np.uint8)

    # 写 PNG (RGBA → RGB, filter=0)
    def make_chunk(chunk_type, data):
        chunk = chunk_type + data
        crc = struct.pack('>I', zlib.crc32(chunk) & 0xffffffff)
        return struct.pack('>I', len(data)) + chunk + crc

    sig = b'\x89PNG\r\n\x1a\n'
    ihdr_data = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    ihdr = make_chunk(b'IHDR', ihdr_data)

    # 构建原始扫描行（每行前加 filter=0 字节）
    raw_rows = []
    for y in range(height):
        raw_rows.append(b'\x00' + rgb_out[y].tobytes())
    raw_data = b''.join(raw_rows)

    idat = make_chunk(b'IDAT', zlib.compress(raw_data, 6))
    iend = make_chunk(b'IEND', b'')

    with open(filepath, 'wb') as f:
        f.write(sig + ihdr + idat + iend)

    size = os.path.getsize(filepath)
    print(f"    {filepath} ({size / 1024:.0f} KB)")


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

        # 备份 BSDF Base Color 来源
        bc_input = m_bsdf.inputs["Base Color"]
        bc_src = bc_input.links[0].from_socket if bc_input.is_linked else None
        bc_default = tuple(bc_input.default_value)

        # 备份 Surface 连接
        surf_input = m_out.inputs["Surface"]
        orig_surf_src = surf_input.links[0].from_socket if surf_input.is_linked else None

        # 创建临时 Emission 节点
        emit = m_nt.nodes.new("ShaderNodeEmission")
        emit.label = "__bake_tmp__"
        if bc_src:
            m_nt.links.new(bc_src, emit.inputs["Color"])
        else:
            emit.inputs["Color"].default_value = bc_default

        # 替换 Surface
        for link in list(surf_input.links):
            m_nt.links.remove(link)
        m_nt.links.new(emit.outputs["Emission"], surf_input)

        backups.append((m_nt, surf_input, orig_surf_src, emit))

    # 选择 + 烘焙
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

    # 还原所有高模材质
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


def bake_high_to_low(
    high_fbx: str,
    low_obj: str,
    out_dir: str,
    name: str = "bake",
    cage: float = DEFAULT_CAGE,
    res: int = DEFAULT_RES,
    samples: int = DEFAULT_SAMPLES,
    device: str = DEFAULT_DEVICE,
    skip_uv: bool = False,
    skip_align: bool = False,
):
    """
    统一烘焙入口：高模 FBX + 低模 OBJ → 烘焙 Albedo+Normal 贴图。

    参数:
        high_fbx: 带纹理的高模 FBX 路径
        low_obj: 低模 OBJ 路径
        out_dir: 输出目录
        name: 烘焙贴图名称前缀
        cage: cage extrusion 距离
        res: 烘焙分辨率
        samples: 采样数
        device: CPU 或 GPU
        skip_uv: 跳过 Smart UV（低模已有 UV 时）
        skip_align: 跳过旋转对齐（已确认对齐时）
    """
    import bpy
    import mathutils

    print(f"\n{'='*70}")
    print(f"BAKE: {name}")
    print(f"  High:   {high_fbx}")
    print(f"  Low:    {low_obj}")
    print(f"  Out:    {out_dir}")
    print(f"  Params: cage={cage}, res={res}, samples={samples}, device={device}")
    print(f"{'='*70}\n")

    # === 0. 准备输出目录 ===
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # === 1. 导入高模 FBX（带纹理） ===
    print("[1] Importing high-poly FBX...")
    bpy.ops.object.select_all(action="DESELECT")
    # 记住现有对象，以便找到新导入的
    existing = set(bpy.data.objects.keys())
    bpy.ops.import_scene.fbx(filepath=high_fbx)
    new_objs = [o for o in bpy.data.objects if o.name not in existing]
    meshes = [o for o in new_objs if o.type == "MESH"]
    if not meshes:
        raise RuntimeError(f"FBX 中无 MESH 对象: {high_fbx}")

    # 合并多个 mesh
    if len(meshes) > 1:
        bpy.ops.object.select_all(action="DESELECT")
        for m in meshes:
            m.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        bpy.ops.object.join()

    obj_high = [o for o in bpy.data.objects if o.type == "MESH" and o.select_get()][0] if len(meshes) > 1 else meshes[0]
    obj_high.name = f"{name}_High"

    # 应用变换
    bpy.ops.object.select_all(action="DESELECT")
    obj_high.select_set(True)
    bpy.context.view_layer.objects.active = obj_high
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    print(f"    High: verts={len(obj_high.data.vertices)}, faces={len(obj_high.data.polygons)}")

    # === 1b. 前置校验：高模材质 ===
    ok, msg = _validate_high_material(obj_high)
    if not ok:
        raise RuntimeError(f"[FATAL] 高模材质校验失败: {msg}")
    print(f"    材质校验: {msg}")

    # === 2. 导入低模 OBJ ===
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
    print(f"    Low: verts={len(obj_low.data.vertices)}, faces={len(obj_low.data.polygons)}")

    # === 3. 对齐低模到高模 ===
    if not skip_align:
        print("[3] Aligning low to high...")
        _align_low_to_high(obj_high, obj_low)
    else:
        print("[3] Skipping alignment (skip_align=True)")

    # === 3b. 校验 BBox 对齐 ===
    ok, msg = _validate_bbox_alignment(obj_high, obj_low)
    print(f"    BBox 校验: {msg}")
    if not ok:
        print(f"    [WARN] BBox 未对齐，烘焙可能出错！")

    # === 4. Smart UV ===
    if not skip_uv:
        print("[4] Smart UV Project...")
        _setup_smart_uv(obj_low)
    else:
        print("[4] Skipping UV (skip_uv=True)")

    # === 4b. 校验 UV ===
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
    img_albedo.filepath_raw = str(out_path / f"bake_{name}_albedo.png")
    img_normal.filepath_raw = str(out_path / f"bake_{name}_normal.png")

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
    print("[7] Configuring Cycles...")
    scn = bpy.context.scene
    scn.render.engine = "CYCLES"
    scn.cycles.device = device
    scn.cycles.samples = samples
    scn.render.bake.use_selected_to_active = True
    scn.render.bake.cage_extrusion = cage
    scn.render.bake.use_cage = False

    # === 8. 烘焙 Albedo (EMIT) ===
    print("[8] Baking Albedo via EMIT...")
    _emit_bake_albedo(obj_high, obj_low, img_albedo, nt, n_alb)

    # === 9. 烘焙 Normal ===
    print("[9] Baking Normal...")
    _bake_normal(obj_high, obj_low, img_normal, nt, n_norm)

    # === 10. 保存贴图 ===
    # 关键修复：Blender 的 save()/save_render() 在循环依赖场景下
    # 无法正确保存烘焙像素数据（save 保存 generated_color，save_render 保存
    # 带视图变换的错误值）。必须手动读取 img.pixels 并用 numpy/PIL 保存。
    print("[10] Saving bake images...")
    _save_bake_image(img_albedo, str(out_path / f"bake_{name}_albedo.png"), is_srgb=True)
    _save_bake_image(img_normal, str(out_path / f"bake_{name}_normal.png"), is_srgb=False)

    # === 11. 清理场景中的高低模 ===
    print("[11] Cleaning up...")
    bpy.ops.object.select_all(action="DESELECT")
    for obj_name in (obj_high.name, obj_low.name):
        obj = bpy.data.objects.get(obj_name)
        if obj:
            obj.select_set(True)
    bpy.ops.object.delete(use_global=False)

    # 清理孤立数据
    for block_type in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for block in block_type:
            if block.users == 0:
                block_type.remove(block)

    print(f"\n[DONE] {name}: 2 images saved to {out_dir}\n")
    return str(out_path / f"bake_{name}_albedo.png"), str(out_path / f"bake_{name}_normal.png")


# ============ CLI 入口 ============
def main():
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1:]
    else:
        argv = []

    import argparse
    p = argparse.ArgumentParser(description="统一烘焙脚本")
    p.add_argument("--high_fbx", required=True, help="带纹理的高模 FBX 路径")
    p.add_argument("--low_obj", required=True, help="低模 OBJ 路径")
    p.add_argument("--out_dir", required=True, help="输出目录")
    p.add_argument("--name", default="bake", help="贴图名称前缀")
    p.add_argument("--cage", type=float, default=DEFAULT_CAGE)
    p.add_argument("--res", type=int, default=DEFAULT_RES)
    p.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    p.add_argument("--device", default=DEFAULT_DEVICE, choices=["CPU", "GPU"])
    p.add_argument("--skip_uv", action="store_true")
    p.add_argument("--skip_align", action="store_true")
    args = p.parse_args(argv)

    bake_high_to_low(
        high_fbx=args.high_fbx,
        low_obj=args.low_obj,
        out_dir=args.out_dir,
        name=args.name,
        cage=args.cage,
        res=args.res,
        samples=args.samples,
        device=args.device,
        skip_uv=args.skip_uv,
        skip_align=args.skip_align,
    )


if __name__ == "__main__":
    main()
