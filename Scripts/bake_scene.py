#!/usr/bin/env python3
"""
场景烘焙脚本 — 直接烘焙场景中已重合的高模和低模。

不负责减面、导入、对齐，只做烘焙。适用于：
  - Blender 内 Decimate 减面后的高低模（已重合）
  - 外部减面后导入的低模（已手动对齐）
  - 任何已同时存在于场景中且位置重合的高低模对

烘焙流程从 bake.py 的 bake_high_to_low() 提取，
去掉减面/导入/对齐步骤，仅保留：
  缩裹(Shrinkwrap) → Smart UV → 创建烘焙图 → 材质节点 → Cycles 配置 → 烘焙 Albedo(EMIT) + Normal → 资源隔离

用法：
    from bake_scene import bake_scene

    # 场景中已有 building2（高模）和 building2_low（低模），位置重合
    bake_scene(high_obj_name="building2", low_obj_name="building2_low", name="Building2")

    # 跳过 Smart UV（低模已有 UV）
    bake_scene(high_obj_name="building2", low_obj_name="building2_low",
               name="Building2", skip_uv=True)

核心规则（同 bake.py）：
- Albedo 必须用 EMIT 烘焙（禁用 DIFFUSE）
- cage=0.1, res=4096, samples=64, Cycles+GPU
- Smart UV island_margin=0.01
- 烘焙前自动缩裹（Shrinkwrap）低模到高模表面
"""

import sys
import os

# 统一参数（与 bake.py 一致）
DEFAULT_CAGE = 0.05
DEFAULT_RES = 4096
DEFAULT_SAMPLES = 64
DEFAULT_DEVICE = "GPU"
DEFAULT_UV_MARGIN = 0.01


# ============================================================
# 校验函数（从 bake.py 提取）
# ============================================================
def _validate_high_material(obj_high):
    """前置校验：高模材质是否有效（非 Blender 默认紫色）。"""
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
    """校验低模 UV 是否有效。"""
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
        return False, f"低模 '{obj_low.name}' UV 仅 {ratio:.1%} 在 [0,1] 范围内"

    return True, f"UV 有效 ({ratio:.1%} in [0,1])"


def _validate_bbox_alignment(obj_high, obj_low, tol=0.5):
    """校验高低模 BBox 是否对齐。

    比较世界空间下的轴对齐包围盒(AABB)，而非按角点索引比较，
    这样即使高低模旋转状态不同（如 OBJ 导入带 90° X 旋转），
    只要世界坐标下重合，校验就能通过。
    """
    import mathutils
    h_bb = [obj_high.matrix_world @ mathutils.Vector(c) for c in obj_high.bound_box]
    l_bb = [obj_low.matrix_world @ mathutils.Vector(c) for c in obj_low.bound_box]

    # 计算世界空间 AABB
    h_min = mathutils.Vector((min(v.x for v in h_bb), min(v.y for v in h_bb), min(v.z for v in h_bb)))
    h_max = mathutils.Vector((max(v.x for v in h_bb), max(v.y for v in h_bb), max(v.z for v in h_bb)))
    l_min = mathutils.Vector((min(v.x for v in l_bb), min(v.y for v in l_bb), min(v.z for v in l_bb)))
    l_max = mathutils.Vector((max(v.x for v in l_bb), max(v.y for v in l_bb), max(v.z for v in l_bb)))

    max_diff = max(
        abs(h_min.x - l_min.x), abs(h_max.x - l_max.x),
        abs(h_min.y - l_min.y), abs(h_max.y - l_max.y),
        abs(h_min.z - l_min.z), abs(h_max.z - l_max.z),
    )
    ok = max_diff < tol
    return ok, f"BBox AABB max diff={max_diff:.4f} ({'OK' if ok else 'MISMATCH'})"


# ============================================================
# 烘焙辅助函数（从 bake.py 提取）
# ============================================================
def _apply_shrinkwrap(obj_high, obj_low):
    """将低模缩裹(Shrinkwrap)到高模表面，确保烘焙时低模紧贴高模。

    使用 NEAREST_SURFACEPOINT 方法，目标为高模对象，模式为 ON_SURFACE。
    缩裹后低模的顶点会投影到高模最近表面点，改善烘焙精度。
    """
    import bpy

    # 确保高低模可见
    obj_high.hide_set(False)
    obj_high.hide_viewport = False
    obj_low.hide_set(False)
    obj_low.hide_viewport = False

    # 选择低模
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj_low.select_set(True)
    bpy.context.view_layer.objects.active = obj_low

    if obj_low.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    # 添加缩裹修改器
    shrinkwrap = obj_low.modifiers.new(name="ShrinkwrapBake", type='SHRINKWRAP')
    shrinkwrap.target = obj_high
    shrinkwrap.wrap_method = 'NEAREST_SURFACEPOINT'
    shrinkwrap.wrap_mode = 'ON_SURFACE'

    # 应用修改器
    bpy.ops.object.modifier_apply(modifier=shrinkwrap.name)
    print(f"    Shrinkwrap applied: '{obj_low.name}' → '{obj_high.name}' (NEAREST_SURFACEPOINT)")


def _setup_smart_uv(obj, margin=DEFAULT_UV_MARGIN):
    """给低模做 Smart UV Project。

    注意：smart_project 在 headless/MCP 模式下不会自动创建 UV 层，
    需要先手动添加空 UV 层再执行 Smart UV。
    """
    import bpy
    # 选择低模（不用 select_all，避免 MCP 环境下无选中对象问题）
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # 确保对象可见且在 OBJECT 模式
    obj.hide_set(False)
    obj.hide_viewport = False
    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    # 先手动创建 UV 层（如果不存在），smart_project 在 headless 下不会自动创建
    if not obj.data.uv_layers:
        obj.data.uv_layers.new(name="UVMap")

    # 选择低模（不用 select_all，避免 MCP 环境下无选中对象问题）
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15191, island_margin=margin)
    bpy.ops.object.mode_set(mode="OBJECT")
    print(f"    Smart UV Project done (island_margin={margin})")


def _make_bake_image(name, colorspace, base_color, is_data, res):
    """创建烘焙图像（强制非黑底色）。"""
    import bpy
    import tempfile
    img = bpy.data.images.new(name, res, res, alpha=False, is_data=is_data)
    img.colorspace_settings.name = colorspace
    img.generated_color = base_color
    img.file_format = "PNG"
    tmp_dir = tempfile.mkdtemp(prefix="bake_img_")
    img.filepath_raw = os.path.join(tmp_dir, f"{name}.png")
    img.scale(img.size[0], img.size[1])
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

    # 选择高低模
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj_high.select_set(True)
    obj_low.select_set(True)
    bpy.context.view_layer.objects.active = obj_low
    for n in nt.nodes:
        n.select = False
    n_alb = n_albedo
    n_alb.select = True
    nt.nodes.active = n_alb

    # 从场景设置获取烘焙参数
    cage = bpy.context.scene.render.bake.cage_extrusion
    use_cage = bpy.context.scene.render.bake.use_cage

    bpy.context.scene.cycles.bake_type = "EMIT"
    print("    BAKE START: Albedo (EMIT)")
    bpy.ops.object.bake(
        type="EMIT",
        use_selected_to_active=True,
        cage_extrusion=cage,
        use_cage=use_cage,
        margin=16,
    )
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

    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj_high.select_set(True)
    obj_low.select_set(True)
    bpy.context.view_layer.objects.active = obj_low
    for n in nt.nodes:
        n.select = False
    n_norm.select = True
    nt.nodes.active = n_norm

    cage = bpy.context.scene.render.bake.cage_extrusion
    use_cage = bpy.context.scene.render.bake.use_cage

    bpy.context.scene.cycles.bake_type = "NORMAL"
    print("    BAKE START: Normal")
    bpy.ops.object.bake(
        type="NORMAL",
        use_selected_to_active=True,
        cage_extrusion=cage,
        use_cage=use_cage,
        margin=16,
    )
    print("    BAKE DONE: Normal")


# ============================================================
# 主入口
# ============================================================
def bake_scene(
    high_obj_name: str,
    low_obj_name: str,
    name: str = "bake",
    cage: float = DEFAULT_CAGE,
    res: int = DEFAULT_RES,
    samples: int = DEFAULT_SAMPLES,
    device: str = DEFAULT_DEVICE,
    uv_margin: float = DEFAULT_UV_MARGIN,
    skip_uv: bool = False,
    skip_shrinkwrap: bool = False,
    max_ray_dist: float = 0.0,
):
    """
    直接烘焙场景中已重合的高模和低模。

    不负责减面、导入、对齐。高低模必须已同时存在于场景中且位置重合。

    Parameters
    ----------
    high_obj_name : str
        场景中的高模对象名称
    low_obj_name : str
        场景中的低模对象名称
    name : str
        烘焙贴图名称前缀
    cage : float
        cage extrusion 距离（默认 0.1）
    res : int
        烘焙分辨率（默认 4096）
    samples : int
        采样数（默认 64）
    device : str
        "CPU" 或 "GPU"
    uv_margin : float
        Smart UV island margin（默认 0.01）
    skip_uv : bool
        跳过 Smart UV（低模已有 UV 时设为 True）
    skip_shrinkwrap : bool
        跳过缩裹（低模已紧贴高模时设为 True）
    max_ray_dist : float
        最大射线距离，0=无限制

    Returns
    -------
    tuple : (low_obj_name, n_faces)
    """
    import bpy

    print(f"\n{'='*70}")
    print(f"BAKE SCENE: {name}")
    print(f"  High:   {high_obj_name} (场景对象)")
    print(f"  Low:    {low_obj_name} (场景对象)")
    print(f"  Params: cage={cage}, res={res}, samples={samples}, device={device}")
    print(f"  UV:     island_margin={uv_margin} {'(skip)' if skip_uv else ''}")
    print(f"  Wrap:   shrinkwrap={'OFF' if skip_shrinkwrap else 'ON (NEAREST_SURFACEPOINT)'}")
    print(f"{'='*70}\n")

    # === 0. 预清理：删除残留的同名烘焙数据 ===
    print("[0] Pre-cleanup: removing stale bake data...")
    for img in list(bpy.data.images):
        if img.name.startswith(f"bake_{name}"):
            bpy.data.images.remove(img)
    for mat in list(bpy.data.materials):
        if mat.name.startswith(f"bake_{name}"):
            bpy.data.materials.remove(mat)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    print("    Pre-cleanup done")

    # === 1. 获取高低模对象 ===
    print(f"[1] Locating scene objects...")
    obj_high = bpy.data.objects.get(high_obj_name)
    if obj_high is None:
        raise RuntimeError(f"场景中未找到高模对象 '{high_obj_name}'")
    if obj_high.type != "MESH":
        raise RuntimeError(f"高模 '{high_obj_name}' 不是 MESH 类型（{obj_high.type}）")

    obj_low = bpy.data.objects.get(low_obj_name)
    if obj_low is None:
        raise RuntimeError(f"场景中未找到低模对象 '{low_obj_name}'")
    if obj_low.type != "MESH":
        raise RuntimeError(f"低模 '{low_obj_name}' 不是 MESH 类型（{obj_low.type}）")

    actual_faces = len(obj_low.data.polygons)
    print(f"    High: {obj_high.name} — {len(obj_high.data.vertices)} verts, {len(obj_high.data.polygons)} faces")
    print(f"    Low:  {obj_low.name} — {len(obj_low.data.vertices)} verts, {actual_faces} faces")

    # === 2. 材质校验 ===
    print("[2] Validating high-poly material...")
    ok, msg = _validate_high_material(obj_high)
    if not ok:
        raise RuntimeError(f"[FATAL] 高模材质校验失败: {msg}")
    print(f"    材质校验: {msg}")

    # === 3. BBox 校验 ===
    print("[3] Validating BBox alignment...")
    ok, msg = _validate_bbox_alignment(obj_high, obj_low)
    print(f"    BBox 校验: {msg}")
    if not ok:
        print(f"    [WARN] BBox 未对齐，烘焙可能出错！")

    # === 4. 缩裹（Shrinkwrap）低模到高模表面 ===
    if not skip_shrinkwrap:
        print("[4] Shrinkwrap: projecting low-poly onto high-poly surface...")
        _apply_shrinkwrap(obj_high, obj_low)
        # 缩裹后更新面数记录
        actual_faces = len(obj_low.data.polygons)
    else:
        print("[4] Skipping Shrinkwrap (skip_shrinkwrap=True)")

    # === 5. Smart UV ===
    if not skip_uv:
        print(f"[5] Smart UV Project (island_margin={uv_margin})...")
        _setup_smart_uv(obj_low, margin=uv_margin)
    else:
        print("[5] Skipping UV (skip_uv=True)")

    ok, msg = _validate_uv(obj_low)
    print(f"    UV 校验: {msg}")
    if not ok:
        raise RuntimeError(f"[FATAL] UV 校验失败: {msg}")

    # === 6. 创建烘焙图 ===
    print(f"[6] Creating bake images ({res}x{res})...")
    img_albedo = _make_bake_image(
        f"bake_{name}_albedo", "sRGB", (1, 1, 1, 1), False, res
    )
    img_normal = _make_bake_image(
        f"bake_{name}_normal", "Non-Color", (0.5, 0.5, 1, 1), True, res
    )

    # === 7. 低模材质 + 节点树 ===
    print("[7] Setting up low-poly material...")
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

    # === 8. Cycles 配置 ===
    print(f"[8] Configuring Cycles (cage={cage})...")
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

    # === 9. 烘焙 Albedo (EMIT) ===
    print("[9] Baking Albedo via EMIT...")
    _emit_bake_albedo(obj_high, obj_low, img_albedo, nt, n_alb)

    # === 10. 烘焙 Normal ===
    print("[10] Baking Normal...")
    _bake_normal(obj_high, obj_low, img_normal, nt, n_norm)

    # === 11. 打包烘焙图 ===
    print("[11] Packing bake images into .blend...")
    try:
        img_albedo.pack()
        img_normal.pack()
    except Exception as e:
        print(f"    Pack warning (non-critical): {e}")

    # === 12. 隔离高模资源防止污染 ===
    print("[12] Isolating high-poly resources to prevent pollution...")
    iso_prefix = f"__baked_{name}__"

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
        img.filepath = ""
        img.filepath_raw = ""
        img.name = iso_prefix + img.name
        n_imgs += 1
    print(f"    Renamed {n_imgs} high-poly textures with prefix '{iso_prefix}'")

    n_mats = 0
    for m in high_materials:
        if m.name.startswith(iso_prefix):
            continue
        m.name = iso_prefix + m.name
        n_mats += 1
    print(f"    Renamed {n_mats} high-poly materials")

    if obj_high.data and not obj_high.data.name.startswith(iso_prefix):
        obj_high.data.name = iso_prefix + obj_high.data.name

    # 孤立清理
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

    # 结果
    leftover_imgs = [i.name for i in bpy.data.images if i.name not in ("Render Result", "Viewer Node")]
    print(f"\n[DONE] {name}: 烘焙完成")
    print(f"    低模: '{obj_low.name}' ({actual_faces} faces)")
    print(f"    材质: bake_{name} (Albedo + Normal)")
    print(f"    高模: '{obj_high.name}' 已保留")
    print(f"    最终场景图像: {leftover_imgs}")

    return obj_low.name, actual_faces


# ============================================================
# CLI 入口
# ============================================================
def main():
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1:]
    else:
        argv = []

    import argparse
    p = argparse.ArgumentParser(description="场景烘焙脚本（高低模已在场景中且重合）")
    p.add_argument("--high", required=True, help="高模对象名称")
    p.add_argument("--low", required=True, help="低模对象名称")
    p.add_argument("--name", default="bake", help="贴图名称前缀")
    p.add_argument("--cage", type=float, default=DEFAULT_CAGE)
    p.add_argument("--res", type=int, default=DEFAULT_RES)
    p.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    p.add_argument("--device", default=DEFAULT_DEVICE, choices=["CPU", "GPU"])
    p.add_argument("--uv_margin", type=float, default=DEFAULT_UV_MARGIN)
    p.add_argument("--skip_uv", action="store_true", help="跳过 Smart UV")
    p.add_argument("--skip_shrinkwrap", action="store_true", help="跳过缩裹")
    p.add_argument("--max_ray_dist", type=float, default=0.0)

    args = p.parse_args(argv)
    bake_scene(
        high_obj_name=args.high,
        low_obj_name=args.low,
        name=args.name,
        cage=args.cage,
        res=args.res,
        samples=args.samples,
        device=args.device,
        uv_margin=args.uv_margin,
        skip_uv=args.skip_uv,
        skip_shrinkwrap=args.skip_shrinkwrap,
        max_ray_dist=args.max_ray_dist,
    )


if __name__ == "__main__":
    main()
