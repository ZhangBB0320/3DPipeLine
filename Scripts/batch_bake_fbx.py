"""
批处理脚本：FBX高模 → 减面到目标面数 → 烘焙(albedo/normal/roughness) → 内嵌纹理低模FBX

用法（在 Blender CLI 中调用）：
    /Applications/Blender.app/Contents/MacOS/Blender \
        --background --python Scripts/batch_bake_fbx.py -- \
        --fbx <input.fbx> --out <output.fbx> [--cage 0.2] [--target 3000]

规则：
- Albedo 必须用 EMIT 烘焙（禁用 DIFFUSE）
- 烘焙图底色：白色 / 法线蓝 / 中灰，禁止黑色
- OBJ 导入必发 Y/Z 轴互换 → 旋转对齐修复
- 减面用外部 pymeshlab
"""

import sys
import os
import math
import subprocess
import argparse
import shutil
import tempfile
from pathlib import Path


EXTERNAL_PY = "/Users/zbb/.workbuddy/binaries/python/versions/3.14.3/bin/python3"
DECIMATE_SCRIPT = "/Users/zbb/3DPipeLine/Scripts/decimate.py"


def bake_one_fbx(fbx_path: str, out_fbx: str, cage: float = 0.2, target_faces: int = 3000):
    """在 Blender Python 内执行单个 FBX 的完整流程。"""
    import bpy
    import mathutils

    print(f"\n{'='*70}")
    print(f"Processing: {fbx_path}")
    print(f"  Output:    {out_fbx}")
    print(f"  Target:    {target_faces} faces, cage={cage}")
    print(f"{'='*70}\n")

    # === 0. 准备 ===
    out_dir = Path(out_fbx).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="bake_fbx_"))

    # === 1. 清空场景，导入 FBX ===
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block in bpy.data.meshes:
        if block.users == 0: bpy.data.meshes.remove(block)
    for block in bpy.data.materials:
        if block.users == 0: bpy.data.materials.remove(block)
    for block in bpy.data.images:
        if block.users == 0: bpy.data.images.remove(block)
    for block in bpy.data.curves:
        if block.users == 0: bpy.data.curves.remove(block)
    for block in bpy.data.cameras:
        if block.users == 0: bpy.data.cameras.remove(block)
    for block in bpy.data.lights:
        if block.users == 0: bpy.data.lights.remove(block)

    bpy.ops.import_scene.fbx(filepath=fbx_path)
    print(f"[1] Imported FBX")

    # 找到 mesh 并合并
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        raise RuntimeError(f"No MESH in {fbx_path}")

    if len(meshes) > 1:
        bpy.ops.object.select_all(action="DESELECT")
        for m in meshes:
            m.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        bpy.ops.object.join()

    # 重新获取（join 后对象可能变化）
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    obj_high = meshes[0]
    obj_high.name = "HighPoly"
    orig_faces = len(obj_high.data.polygons)
    print(f"    High: verts={len(obj_high.data.vertices)} faces={orig_faces}")

    # 应用变换
    bpy.ops.object.select_all(action="DESELECT")
    obj_high.select_set(True)
    bpy.context.view_layer.objects.active = obj_high
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    # === 2. 导出 OBJ（给 pymeshlab 减面用） ===
    high_obj = str(tmp_dir / "HighPoly.obj")
    bpy.ops.wm.obj_export(
        filepath=high_obj,
        export_selected_objects=True,
        forward_axis="NEGATIVE_Z",
        up_axis="Y",
    )
    print(f"[2] OBJ exported")

    # === 3. 减面（外部 pymeshlab 进程） ===
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
    date_dirs = sorted([d for d in decimate_out.iterdir() if d.is_dir()])
    low_obj_path = str(date_dirs[-1] / "HighPoly.obj")
    print(f"    Decimated OK")

    # === 4. 导入低模 OBJ ===
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.wm.obj_import(
        filepath=low_obj_path,
        forward_axis="NEGATIVE_Z",
        up_axis="Y",
    )
    obj_low = bpy.context.selected_objects[0]
    obj_low.name = "LowPoly"

    # === 5. 对齐 Transform（OBJ 可能 Y/Z 互换） ===
    obj_low.location = (0, 0, 0)
    obj_low.rotation_euler = (0, 0, 0)
    obj_low.scale = (1, 1, 1)
    bpy.context.view_layer.objects.active = obj_low
    obj_low.select_set(True)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    import random
    random.seed(42)
    high_verts = [obj_high.matrix_world @ v.co for v in obj_high.data.vertices]
    high_sample = random.sample(high_verts, min(500, len(high_verts)))

    PI = math.pi
    angles = [0, PI/2, PI, 3*PI/2]
    candidates = [(rx, ry, rz) for rx in angles for ry in angles for rz in angles]

    def avg_distance(rot):
        obj_low.rotation_euler = rot
        bpy.context.view_layer.update()
        low_verts_world = [obj_low.matrix_world @ v.co for v in obj_low.data.vertices]
        from mathutils import kdtree
        size = len(low_verts_world)
        kd = kdtree.KDTree(size)
        for i, v in enumerate(low_verts_world):
            kd.insert(v, i)
        kd.balance()
        return sum(kd.find(hv)[2] for hv in high_sample) / len(high_sample)

    best_rot, best_score = (0, 0, 0), float("inf")
    for rot in candidates:
        score = avg_distance(rot)
        if score < best_score:
            best_score = score
            best_rot = rot

    print(f"[5] Best rotation avg dist={best_score:.6f}")
    obj_low.rotation_euler = best_rot
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    # BBox 验证
    high_bb = [obj_high.matrix_world @ mathutils.Vector(c) for c in obj_high.bound_box]
    low_bb  = [obj_low.matrix_world  @ mathutils.Vector(c) for c in obj_low.bound_box]
    max_diff = max((high_bb[i] - low_bb[i]).length for i in range(8))
    print(f"[6] BBox max diff: {max_diff:.6f}")

    # === 7. Smart UV ===
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15191, island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    print("[7] Smart UV done")

    # === 8. 创建 3 张烘焙图（强制非黑底色） ===
    RES = 2048
    bake_dir = tmp_dir / "baked"
    bake_dir.mkdir(exist_ok=True)

    def make_img(name, cs, color, is_data=False):
        img = bpy.data.images.new(name, RES, RES, alpha=False, is_data=is_data)
        img.colorspace_settings.name = cs
        img.generated_color = color
        img.scale(img.size[0], img.size[1])
        img.filepath_raw = str(bake_dir / f"{name}.png")
        img.file_format = "PNG"
        return img

    img_alb = make_img("bake_albedo", "sRGB", (1, 1, 1, 1), False)
    img_norm = make_img("bake_normal", "Non-Color", (0.5, 0.5, 1, 1), True)
    img_rough = make_img("bake_roughness", "Non-Color", (0.5, 0.5, 0.5, 1), True)
    print("[8] Bake images created (white/normal-blue/mid-gray)")

    # === 9. 低模 BSDF 节点树 ===
    mat = bpy.data.materials.new("bake_material")
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
    out_node = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(n_alb.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(n_rough.outputs["Color"], bsdf.inputs["Roughness"])
    nt.links.new(n_normmap.outputs["Normal"], bsdf.inputs["Normal"])
    nt.links.new(bsdf.outputs["BSDF"], out_node.inputs["Surface"])

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
    for n in nt.nodes: n.select = False
    n_alb.select = True
    nt.nodes.active = n_alb
    scn.cycles.bake_type = "EMIT"
    bpy.ops.object.bake(type="EMIT")

    for m_nt, surf_input, orig_surf_src, emit in backups:
        for link in list(surf_input.links):
            m_nt.links.remove(link)
        if orig_surf_src:
            m_nt.links.new(orig_surf_src, surf_input)
        m_nt.nodes.remove(emit)
    print("    Albedo done, materials restored")

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
    print("    All bake images saved")

    # === 14. 导出 FBX（内嵌纹理） ===
    print(f"[14] Exporting FBX → {out_fbx}")
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
    fbx_size = os.path.getsize(out_fbx)
    print(f"    FBX exported: {fbx_size / 1024 / 1024:.1f} MB")

    # === 15. 清理垃圾文件 ===
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

    # 清理临时目录
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"[done] {out_fbx}  ({fbx_size / 1024:.0f} KB)\n")


def main():
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1:]
    else:
        argv = []
    p = argparse.ArgumentParser()
    p.add_argument("--fbx", required=True, help="输入 FBX 路径")
    p.add_argument("--out", required=True, help="输出 FBX 路径")
    p.add_argument("--cage", type=float, default=0.2, help="烘焙 cage 距离")
    p.add_argument("--target", type=int, default=3000, help="目标面数")
    args = p.parse_args(argv)
    bake_one_fbx(fbx_path=args.fbx, out_fbx=args.out, cage=args.cage, target_faces=args.target)


if __name__ == "__main__":
    main()
