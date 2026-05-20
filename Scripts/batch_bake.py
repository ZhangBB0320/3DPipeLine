"""
批处理脚本：高模 → 减面到一半 → 烘焙 → 内嵌纹理低模 FBX

用法（在 Blender CLI 中调用）：
    /Applications/Blender.app/Contents/MacOS/Blender \
        <input.blend> --background --python Scripts/batch_bake.py -- \
        --object <object_name> --out <output.fbx> [--cage 1.5]

或调用调度器：
    python3 Scripts/batch_bake.py --schedule

环境约束（已记录到 SKILL）：
- Albedo 必须用 EMIT 烘焙
- 烘焙图底色：白色 / 法线蓝 / 中灰，禁止黑色
- OBJ 导入必发 Y/Z 轴互换 → rotation_euler=(pi/2,0,0) 修复
- 减面用外部 pymeshlab（workbuddy python 3.14）
"""

import sys
import os
import math
import subprocess
import argparse
import shutil
import tempfile
from pathlib import Path


# 减面用的外部 Python（workbuddy 已装 pymeshlab）
EXTERNAL_PY = "/Users/zbb/.workbuddy/binaries/python/versions/3.14.3/bin/python3"
DECIMATE_SCRIPT = "/Users/zbb/3DPipeLine/Scripts/decimate.py"
EXPORT_FBX_SCRIPT = "/Users/zbb/3DPipeLine/Scripts/export_fbx.py"


def bake_one_blend(blend_path: str, object_name: str, out_fbx: str, cage: float = 1.5):
    """在 Blender Python 内执行单个文件的完整流程。"""
    import bpy
    import mathutils

    print(f"\n{'='*70}")
    print(f"Processing: {blend_path}")
    print(f"  Object:    {object_name}")
    print(f"  Output:    {out_fbx}")
    print(f"{'='*70}\n")

    # === 0. 准备目录 ===
    out_dir = Path(out_fbx).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    bake_dir = out_dir / "baked"
    bake_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="bake_"))

    # === 1. 找到高模 ===
    high = bpy.data.objects.get(object_name)
    if high is None:
        # 自动选 mesh 物体
        meshes = [o for o in bpy.data.objects if o.type == "MESH"]
        if len(meshes) == 0:
            raise RuntimeError(f"No MESH in {blend_path}")
        high = meshes[0]
        print(f"[auto] Using mesh: {high.name}")
    high.name = f"{object_name}_High"
    obj_high = high

    orig_faces = len(obj_high.data.polygons)
    target_faces = max(100, orig_faces // 2)
    print(f"[1] High: verts={len(obj_high.data.vertices)} faces={orig_faces} → target {target_faces}")

    # 记录 transform（虽然通常都是 0/1，但严格按规则）
    orig_loc = tuple(obj_high.location)
    orig_rot = tuple(obj_high.rotation_euler)
    orig_scl = tuple(obj_high.scale)

    # === 2. 导出 OBJ ===
    high_obj = str(tmp_dir / f"{object_name}_High.obj")
    bpy.ops.object.select_all(action="DESELECT")
    obj_high.select_set(True)
    bpy.context.view_layer.objects.active = obj_high
    bpy.ops.wm.obj_export(
        filepath=high_obj,
        export_selected_objects=True,
        forward_axis="NEGATIVE_Z",
        up_axis="Y",
    )
    print(f"[2] OBJ exported: {high_obj}")

    # === 3. 减面（外部进程） ===
    decimate_out = tmp_dir / "decimated"
    decimate_out.mkdir(exist_ok=True)
    print(f"[3] Decimating to {target_faces}...")
    result = subprocess.run(
        [EXTERNAL_PY, DECIMATE_SCRIPT, high_obj, str(target_faces), str(decimate_out)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise RuntimeError("Decimate failed")
    # decimate.py 输出在 <out>/<日期>/<name>.obj
    date_dirs = sorted([d for d in decimate_out.iterdir() if d.is_dir()])
    low_obj_path = str(date_dirs[-1] / f"{object_name}_High.obj")
    print(f"    Decimated: {low_obj_path}")

    # === 4. 导入低模 OBJ ===
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.wm.obj_import(
        filepath=low_obj_path,
        forward_axis="NEGATIVE_Z",
        up_axis="Y",
    )
    obj_low = bpy.context.selected_objects[0]
    obj_low.name = f"{object_name}_Low"

    # === 5. 对齐 Transform：用顶点采样 Hausdorff 距离找最佳旋转 ===
    # 低模 OBJ 几何已是高模的世界坐标，先把低模 transform 清零
    obj_low.location = (0, 0, 0)
    obj_low.rotation_euler = (0, 0, 0)
    obj_low.scale = (1, 1, 1)
    bpy.context.view_layer.objects.active = obj_low
    obj_low.select_set(True)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    # 采样：高模顶点（最多 500 个）
    import random
    random.seed(42)
    high_verts = [obj_high.matrix_world @ v.co for v in obj_high.data.vertices]
    if len(high_verts) > 500:
        high_sample = random.sample(high_verts, 500)
    else:
        high_sample = high_verts

    # 候选旋转：24 种正交方向（绕 X/Y/Z 各 0/90/180/270 度）
    PI = math.pi
    angles = [0, PI/2, PI, 3*PI/2]
    candidates = [(rx, ry, rz) for rx in angles for ry in angles for rz in angles]

    def avg_distance(rot):
        """对每个高模采样点，计算到低模最近顶点的距离，取平均"""
        obj_low.rotation_euler = rot
        bpy.context.view_layer.update()
        low_verts_world = [obj_low.matrix_world @ v.co for v in obj_low.data.vertices]
        # 用 KDTree 加速最近邻
        from mathutils import kdtree
        size = len(low_verts_world)
        kd = kdtree.KDTree(size)
        for i, v in enumerate(low_verts_world):
            kd.insert(v, i)
        kd.balance()
        total = 0.0
        for hv in high_sample:
            _, _, dist = kd.find(hv)
            total += dist
        return total / len(high_sample)

    best_rot = (0, 0, 0)
    best_score = float("inf")
    for rot in candidates:
        score = avg_distance(rot)
        if score < best_score:
            best_score = score
            best_rot = rot

    print(f"[5] Best rotation: rx={best_rot[0]:.4f} ry={best_rot[1]:.4f} rz={best_rot[2]:.4f}, avg vert dist={best_score:.6f}")
    obj_low.rotation_euler = best_rot
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    # === 6. 最终 BBox 验证 ===
    high_bb = [obj_high.matrix_world @ mathutils.Vector(c) for c in obj_high.bound_box]
    low_bb  = [obj_low.matrix_world  @ mathutils.Vector(c) for c in obj_low.bound_box]
    max_diff = max((high_bb[i] - low_bb[i]).length for i in range(8))
    print(f"[6] Final BBox max diff: {max_diff:.6f}, vertex-avg dist: {best_score:.6f}")

    # === 7. Smart UV ===
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15191, island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    print("[7] Smart UV done")

    # === 8. 创建 3 张烘焙图（强制非黑底色） ===
    RES = 2048

    def make_img(name, cs, color, is_data=False):
        img = bpy.data.images.new(name, RES, RES, alpha=False, is_data=is_data)
        img.colorspace_settings.name = cs
        img.generated_color = color
        img.scale(img.size[0], img.size[1])
        img.filepath_raw = str(bake_dir / f"{name}.png")
        img.file_format = "PNG"
        return img

    img_alb = make_img(f"bake_{object_name}_albedo", "sRGB", (1, 1, 1, 1), False)
    img_norm = make_img(f"bake_{object_name}_norm", "Non-Color", (0.5, 0.5, 1, 1), True)
    img_rough = make_img(f"bake_{object_name}_roughness", "Non-Color", (0.5, 0.5, 0.5, 1), True)
    print("[8] Bake images created (white/normal-blue/mid-gray)")

    # === 9. 低模 BSDF 节点树 ===
    mat = bpy.data.materials.new(f"bake_{object_name}")
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)

    tex_coord = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")
    nt.links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])

    n_alb = nt.nodes.new("ShaderNodeTexImage"); n_alb.image = img_alb; n_alb.label = "albedo"
    n_norm = nt.nodes.new("ShaderNodeTexImage"); n_norm.image = img_norm; n_norm.label = "norm"
    n_rough = nt.nodes.new("ShaderNodeTexImage"); n_rough.image = img_rough; n_rough.label = "roughness"
    for n in (n_alb, n_norm, n_rough):
        nt.links.new(mapping.outputs["Vector"], n.inputs["Vector"])

    n_normmap = nt.nodes.new("ShaderNodeNormalMap"); n_normmap.space = "TANGENT"
    nt.links.new(n_norm.outputs["Color"], n_normmap.inputs["Color"])

    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(n_alb.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(n_rough.outputs["Color"], bsdf.inputs["Roughness"])
    nt.links.new(n_normmap.outputs["Normal"], bsdf.inputs["Normal"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    obj_low.data.materials.clear()
    obj_low.data.materials.append(mat)
    print("[9] Low BSDF wired")

    # === 10. Cycles 配置 ===
    scn = bpy.context.scene
    scn.render.engine = "CYCLES"
    scn.cycles.device = "CPU"
    scn.cycles.samples = 64
    scn.render.bake.use_selected_to_active = True
    scn.render.bake.cage_extrusion = cage
    scn.render.bake.use_cage = False

    # === 11. Albedo via EMIT ===
    print("[11] Baking albedo via EMIT...")
    high_mat = obj_high.material_slots[0].material if obj_high.material_slots and obj_high.material_slots[0].material else None
    if high_mat is None:
        raise RuntimeError(f"High {obj_high.name} has no material")

    # 处理多材质：对每个槽分别走 EMIT 备份
    backups = []  # [(nt, surf_input, orig_surf_src, emit_node)]
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

    # 选择高模+低模，活动=低模，目标节点 = albedo
    bpy.ops.object.select_all(action="DESELECT")
    obj_high.select_set(True)
    obj_low.select_set(True)
    bpy.context.view_layer.objects.active = obj_low
    for n in nt.nodes: n.select = False
    n_alb.select = True
    nt.nodes.active = n_alb
    scn.cycles.bake_type = "EMIT"
    bpy.ops.object.bake(type="EMIT")

    # 还原所有材质
    for m_nt, surf_input, orig_surf_src, emit in backups:
        for link in list(surf_input.links):
            m_nt.links.remove(link)
        if orig_surf_src:
            m_nt.links.new(orig_surf_src, surf_input)
        m_nt.nodes.remove(emit)
    print("    Albedo done, high materials restored")

    # === 12. Normal ===
    print("[12] Baking normal...")
    bpy.ops.object.select_all(action="DESELECT")
    obj_high.select_set(True); obj_low.select_set(True)
    bpy.context.view_layer.objects.active = obj_low
    for n in nt.nodes: n.select = False
    n_norm.select = True; nt.nodes.active = n_norm
    scn.cycles.bake_type = "NORMAL"
    bpy.ops.object.bake(type="NORMAL")

    # === 13. Roughness ===
    print("[13] Baking roughness...")
    bpy.ops.object.select_all(action="DESELECT")
    obj_high.select_set(True); obj_low.select_set(True)
    bpy.context.view_layer.objects.active = obj_low
    for n in nt.nodes: n.select = False
    n_rough.select = True; nt.nodes.active = n_rough
    scn.cycles.bake_type = "ROUGHNESS"
    bpy.ops.object.bake(type="ROUGHNESS")

    # 保存所有图
    for img in (img_alb, img_norm, img_rough):
        img.save_render(img.filepath_raw)
        print(f"    Saved: {img.filepath_raw} ({os.path.getsize(img.filepath_raw)} B)")

    # === 14. 导出 FBX ===
    print(f"[14] Exporting FBX → {out_fbx}")
    sys.path.insert(0, "/Users/zbb/3DPipeLine/Scripts")
    import importlib
    import export_fbx
    importlib.reload(export_fbx)
    # 改 export_fbx 的输出位置：直接 monkey-patch
    bpy.ops.object.select_all(action="DESELECT")
    obj_low.select_set(True)
    bpy.context.view_layer.objects.active = obj_low
    bpy.ops.export_scene.fbx(
        filepath=out_fbx,
        use_selection=True,
        global_scale=1.0,
        apply_unit_scale=True,
        bake_space_transform=False,
        object_types={"MESH"},
        use_mesh_modifiers=True,
        mesh_smooth_type="OFF",
        path_mode="COPY",
        embed_textures=True,
        axis_forward="-Z",
        axis_up="Y",
    )
    print(f"    FBX size: {os.path.getsize(out_fbx)} B")

    # === 15. 清理 FBX 同目录的散落文件 ===
    out_path = Path(out_fbx)
    target_stem = out_path.stem
    junk_exts = {".mtl", ".jpg", ".jpeg", ".png", ".exr", ".tga", ".bmp", ".tif", ".tiff", ".webp", ".dds"}
    removed = []
    for f in out_path.parent.iterdir():
        if not f.is_file() or f.resolve() == out_path.resolve():
            continue
        if f.stem == target_stem or f.suffix.lower() in junk_exts:
            f.unlink()
            removed.append(f.name)
    if removed:
        print(f"    Cleaned {len(removed)} junk files: {', '.join(removed)}")

    # 清理 tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"[done] {out_fbx}\n")


def main():
    """从命令行参数解析任务 (Blender Python 模式)。"""
    # Blender 把 -- 之后的参数交给我们
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1:]
    else:
        argv = []
    p = argparse.ArgumentParser()
    p.add_argument("--object", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--cage", type=float, default=1.5)
    args = p.parse_args(argv)
    bake_one_blend(blend_path=None, object_name=args.object, out_fbx=args.out, cage=args.cage)


if __name__ == "__main__":
    main()
