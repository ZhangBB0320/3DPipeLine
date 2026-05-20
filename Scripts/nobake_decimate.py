"""
NoBake 减面管线 - AI 生成建筑模型 → 3000 面低模 FBX（保留原始 UV + 纹理，不烘焙）

核心思路：
  减面时保留原始 UV，减面后直接使用原始纹理，跳过烘焙步骤。
  消除烘焙射线未命中导致的黑块问题。

用法（Blender 命令行模式）：
  /Applications/Blender.app/Contents/MacOS/Blender --background --python nobake_decimate.py -- \
      --input /path/to/model.fbx \
      --output /path/to/output_dir \
      --target 3000

  批量模式（处理整个目录）：
  /Applications/Blender.app/Contents/MacOS/Blender --background --python nobake_decimate.py -- \
      --input /path/to/fbx_dir/ \
      --output /path/to/output_dir/ \
      --target 3000

  仅渲染对比图（不导出 FBX）：
  ... --render-only --input model.fbx --output out_dir

输出结构：
  <output>/<model_name>/
    <model_name>_3k.fbx        # 低模 FBX（纹理 COPY 模式引用）
    textures/                   # 纹理副本
    <model_name>_compare.png    # 高模 vs 低模对比渲染图
"""

import bpy
import bmesh
import argparse
import json
import sys
import os
from pathlib import Path
from mathutils import Vector
import time


def clear_scene():
    """清空场景"""
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=True)
    # 清理孤立数据
    for block in bpy.data.meshes:
        if block.users == 0:
            bpy.data.meshes.remove(block)
    for block in bpy.data.materials:
        if block.users == 0:
            bpy.data.materials.remove(block)
    for block in bpy.data.images:
        if block.users == 0:
            bpy.data.images.remove(block)


def import_fbx(filepath):
    """导入 FBX，返回导入的对象列表"""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=filepath)
    after = set(bpy.data.objects)
    imported = after - before
    return list(imported)


def get_mesh_objects(objects):
    """筛选出 MESH 类型对象"""
    return [obj for obj in objects if obj.type == 'MESH']


def decimate_object(obj, target_faces):
    """对对象执行 Decimate 修改器，保留 UV"""
    mesh = obj.data
    current_faces = len(mesh.polygons)

    if current_faces <= target_faces:
        print(f"  [SKIP] {obj.name}: {current_faces} faces <= target {target_faces}")
        return current_faces

    ratio = target_faces / current_faces

    # 使用修改器而非 bmesh，更好地保留 UV
    mod = obj.modifiers.new(name='Decimate_NoBake', type='DECIMATE')
    mod.decimate_type = 'COLLAPSE'
    mod.ratio = ratio

    # 应用修改器
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.modifier_apply(modifier=mod.name)

    result_faces = len(obj.data.polygons)
    print(f"  [DECIMATE] {obj.name}: {current_faces} → {result_faces} faces "
          f"(ratio={ratio:.4f}, reduction={(1-result_faces/current_faces)*100:.1f}%)")
    return result_faces


def export_fbx_with_textures(obj, output_dir, model_name):
    """导出 FBX，先解包纹理到目标目录再用 COPY 模式导出"""
    export_dir = Path(output_dir) / model_name
    export_dir.mkdir(parents=True, exist_ok=True)

    tex_dir = export_dir / "textures"
    tex_dir.mkdir(parents=True, exist_ok=True)

    fbx_path = str(export_dir / f"{model_name}_3k.fbx")

    # 先解包/复制纹理到目标目录，并更新材质中的文件路径
    unpacked = unpack_textures(obj, tex_dir)

    # 导出 FBX（path_mode='COPY' 会让 Blender 把引用的纹理复制到 FBX 旁）
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    bpy.ops.export_scene.fbx(
        filepath=fbx_path,
        use_selection=True,
        path_mode='COPY',
        embed_textures=False,
        mesh_smooth_type='FACE',
        use_mesh_modifiers=False,
        axis_forward='-Z',
        axis_up='Y',
        primary_bone_axis='Y',
        secondary_bone_axis='X',
    )

    fbx_size = os.path.getsize(fbx_path)
    print(f"  [EXPORT] {fbx_path} ({fbx_size/1024:.0f} KB)")
    print(f"  [TEXTURES] {unpacked} texture files unpacked to {tex_dir}")

    return fbx_path


def unpack_textures(obj, tex_dir):
    """解包/复制对象材质引用的纹理文件到目标目录，并更新图片路径"""
    import shutil
    count = 0
    for mat in obj.data.materials:
        if mat is None:
            continue
        if mat.use_nodes is False:
            continue
        for node in mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                img = node.image
                src = bpy.path.abspath(img.filepath)

                # 确定输出文件名
                if img.filepath:
                    basename = os.path.basename(img.filepath)
                else:
                    basename = img.name
                    if not basename.lower().endswith(('.png', '.jpg', '.jpeg')):
                        basename += '.png'

                dst = str(tex_dir / basename)

                if img.packed_file:
                    # packed 纹理：先设路径再解包
                    img.filepath = dst
                    try:
                        img.unpack(method='WRITE_LOCAL')
                        count += 1
                        print(f"    Unpacked: {basename}")
                    except Exception as e:
                        print(f"    [WARN] Unpack failed for {basename}: {e}")
                        # 回退：save 操作
                        try:
                            img.save()
                            count += 1
                            print(f"    Saved (fallback): {basename}")
                        except Exception as e2:
                            print(f"    [ERROR] Save also failed: {e2}")
                elif os.path.exists(src):
                    # 外部纹理：复制到目标目录
                    if src != dst:
                        shutil.copy2(src, dst)
                    img.filepath = dst
                    img.reload()
                    count += 1
                    print(f"    Copied: {basename}")
                else:
                    # 文件不存在且未 packed → 尝试保存当前像素数据
                    try:
                        img.filepath = dst
                        img.save()
                        count += 1
                        print(f"    Saved (from memory): {basename}")
                    except Exception as e:
                        print(f"    [WARN] Cannot save {basename}: {e}")

    return count


def setup_render_camera(obj, scene=None):
    """为对象设置渲染相机"""
    if scene is None:
        scene = bpy.context.scene

    # 计算包围盒
    bbox = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    min_co = Vector((min(b.x for b in bbox), min(b.y for b in bbox), min(b.z for b in bbox)))
    max_co = Vector((max(b.x for b in bbox), max(b.y for b in bbox), max(b.z for b in bbox)))
    center = (min_co + max_co) / 2
    size = (max_co - min_co).length

    # 创建相机
    cam_data = bpy.data.cameras.new(name="CompareCam")
    cam_obj = bpy.data.objects.new(name="CompareCam", object_data=cam_data)
    scene.collection.objects.link(cam_obj)

    # 定位相机（正前方偏上 30°）
    cam_distance = size * 1.8
    cam_obj.location = center + Vector((0, -cam_distance, size * 0.3))
    cam_obj.rotation_euler = (1.2, 0, 0)  # 略俯视

    scene.camera = cam_obj

    # 设置渲染参数
    scene.render.engine = 'BLENDER_EEVEE_NEXT' if hasattr(bpy.types, 'EEVEE_NEXT') else 'BLENDER_EEVEE'
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGB'

    # 环境光
    scene.world = bpy.data.worlds.get('World') or bpy.data.worlds.new('World')
    if scene.world.use_nodes:
        bg = scene.world.node_tree.nodes.get('Background')
        if bg:
            bg.inputs[0].default_value = (0.5, 0.5, 0.5, 1)
            bg.inputs[1].default_value = 2.0

    return cam_obj


def render_object(obj, output_path, label=""):
    """渲染单个对象"""
    scene = bpy.context.scene

    # 临时隐藏其他对象
    hidden = []
    for o in scene.objects:
        if o != obj and o.type == 'MESH':
            o.hide_render = True
            o.hide_viewport = True
            hidden.append(o)

    setup_render_camera(obj, scene)
    scene.render.filepath = output_path

    bpy.ops.render.render(write_still=True)
    print(f"  [RENDER] {output_path} ({label})")

    # 恢复可见性
    for o in hidden:
        if o.name in bpy.data.objects:  # 对象可能已被删除
            o.hide_render = False
            o.hide_viewport = False

    # 清理相机
    cam = scene.camera
    if cam:
        try:
            scene.collection.objects.unlink(cam)
        except Exception:
            pass
        cam_data = cam.data
        try:
            bpy.data.objects.remove(cam, do_unlink=True)
        except Exception:
            pass
        try:
            bpy.data.cameras.remove(cam_data, do_unlink=True)
        except Exception:
            pass


def render_comparison(high_obj, low_obj, output_path):
    """渲染高模 vs 低模对比图"""
    scene = bpy.context.scene

    # 简化：渲染两张单独的图，后续用 PIL 拼接
    # 如果没有 PIL，就分别渲染
    base = Path(output_path)
    high_render = str(base.parent / f"{base.stem}_high.png")
    low_render = str(base.parent / f"{base.stem}_low.png")

    render_object(high_obj, high_render, "High-poly")
    render_object(low_obj, low_render, "Low-poly (3k)")

    # 尝试用 PIL 拼接
    try:
        from PIL import Image, ImageDraw, ImageFont
        img_high = Image.open(high_render)
        img_low = Image.open(low_render)

        w, h = img_high.size
        combined = Image.new('RGB', (w * 2, h))
        combined.paste(img_high, (0, 0))
        combined.paste(img_low, (w, 0))

        # 添加文字标签
        try:
            draw = ImageDraw.Draw(combined)
            font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 40)
            draw.text((20, 20), 'High-poly', fill='white', font=font)
            draw.text((w + 20, 20), f'Low-poly (NoBake)', fill='white', font=font)
        except Exception:
            pass

        combined.save(str(base))

        # 删除中间文件
        os.remove(high_render)
        os.remove(low_render)
        print(f"  [COMPARE] {base} (side-by-side {w*2}x{h})")
    except ImportError:
        print(f"  [INFO] PIL not available, separate renders saved")
        print(f"    High: {high_render}")
        print(f"    Low: {low_render}")


def analyze_uv_distortion(obj):
    """分析 UV 扭曲度"""
    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    if not uv_layer:
        print("  [WARN] No UV layer found")
        return

    extreme_stretch = 0
    extreme_compress = 0
    total = len(mesh.polygons)

    for poly in mesh.polygons:
        # 计算 3D 面积
        geo_area = 0
        verts = [mesh.vertices[vi] for vi in poly.vertices]
        if len(verts) >= 3:
            e1 = verts[1].co - verts[0].co
            e2 = verts[2].co - verts[0].co
            geo_area = e1.cross(e2).length / 2

        # 计算 UV 面积
        uv_area = 0
        uv_loops = [uv_layer.data[li] for li in poly.loop_indices]
        if len(uv_loops) >= 3:
            u0, v0 = uv_loops[0].uv
            u1, v1 = uv_loops[1].uv
            u2, v2 = uv_loops[2].uv
            uv_area = abs((u1-u0)*(v2-v0) - (u2-u0)*(v1-v0)) / 2

        if geo_area > 0 and uv_area > 0:
            ratio = uv_area / geo_area
            if ratio > 0.1:  # UV 远大于几何 → 拉伸
                extreme_stretch += 1
            elif ratio < 0.0001:  # UV 远小于几何 → 压缩
                extreme_compress += 1

    print(f"  [UV] Total faces: {total}")
    print(f"  [UV] Extreme stretch: {extreme_stretch} ({extreme_stretch/max(total,1)*100:.1f}%)")
    print(f"  [UV] Extreme compression: {extreme_compress} ({extreme_compress/max(total,1)*100:.1f}%)")


def process_single(input_path, output_dir, target_faces=3000, render_compare=False):
    """处理单个 FBX 文件"""
    model_name = Path(input_path).stem
    print(f"\n{'='*60}")
    print(f"Processing: {model_name}")
    print(f"Input: {input_path}")
    print(f"Target faces: {target_faces}")
    print(f"{'='*60}")

    t0 = time.time()

    # 1. 清空场景并导入
    clear_scene()
    imported = import_fbx(input_path)
    mesh_objs = get_mesh_objects(imported)

    if not mesh_objs:
        print(f"  [ERROR] No mesh objects found in {input_path}")
        return None

    print(f"  [IMPORT] {len(mesh_objs)} mesh object(s)")
    for obj in mesh_objs:
        print(f"    {obj.name}: {len(obj.data.polygons)} faces, "
              f"{len(obj.data.vertices)} verts, "
              f"{len(obj.data.materials)} material(s)")

    # 2. 记录原始面数
    original_faces = sum(len(obj.data.polygons) for obj in mesh_objs)

    # 3. 如果需要渲染对比，先保存高模状态
    if render_compare:
        # 复制高模用于对比渲染
        high_clones = []
        for obj in mesh_objs:
            obj.select_set(True)
        bpy.ops.object.duplicate()
        for obj in bpy.context.selected_objects:
            if obj.type == 'MESH':
                obj.name = obj.name + "_HighClone"
                high_clones.append(obj)
                # 隐藏高模克隆
                obj.hide_viewport = True
                obj.hide_render = True

    # 4. 减面
    total_low_faces = 0
    for obj in mesh_objs:
        f = decimate_object(obj, target_faces)
        total_low_faces += f

    print(f"  [TOTAL] {original_faces} → {total_low_faces} faces")

    # 5. UV 扭曲分析
    for obj in mesh_objs:
        analyze_uv_distortion(obj)

    # 6. 导出 FBX + 纹理
    # 合并所有 mesh 对象为一个（方便导出）
    if len(mesh_objs) > 1:
        bpy.ops.object.select_all(action='DESELECT')
        for obj in mesh_objs:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = mesh_objs[0]
        bpy.ops.object.join()
        merged = bpy.context.active_object
        merged.name = model_name + "_3k"
        export_obj = merged
    else:
        mesh_objs[0].name = model_name + "_3k"
        export_obj = mesh_objs[0]

    fbx_path = export_fbx_with_textures(export_obj, output_dir, model_name)

    # 7. 渲染对比图
    if render_compare and high_clones:
        for c in high_clones:
            c.hide_viewport = False
            c.hide_render = False
        compare_path = str(Path(output_dir) / model_name / f"{model_name}_compare.png")
        render_comparison(high_clones[0], export_obj, compare_path)

    elapsed = time.time() - t0
    print(f"\n  [DONE] {model_name}: {original_faces} → {total_low_faces} faces in {elapsed:.1f}s")
    print(f"  [OUTPUT] {fbx_path}")

    return fbx_path


def process_batch(input_dir, output_dir, target_faces=3000, render_compare=False):
    """批量处理目录下的所有 FBX"""
    fbx_files = sorted(Path(input_dir).glob("*.fbx"))
    if not fbx_files:
        print(f"[ERROR] No FBX files found in {input_dir}")
        return []

    print(f"Found {len(fbx_files)} FBX files in {input_dir}")
    results = []

    for i, fbx in enumerate(fbx_files):
        print(f"\n[{i+1}/{len(fbx_files)}] ", end="")
        result = process_single(str(fbx), output_dir, target_faces, render_compare)
        results.append((str(fbx), result))

    print(f"\n{'='*60}")
    print(f"Batch complete: {len(results)} files processed")
    for src, dst in results:
        status = "OK" if dst else "FAILED"
        print(f"  [{status}] {Path(src).name} → {dst}")
    print(f"{'='*60}")

    return results


def parse_args():
    """解析命令行参数（Blender -- 后的部分）"""
    # Blender 会吞掉 -- 之前的参数，我们只处理之后的
    argv = sys.argv
    if '--' in argv:
        argv = argv[argv.index('--') + 1:]
    else:
        argv = []

    parser = argparse.ArgumentParser(description='NoBake 减面管线')
    parser.add_argument('--input', required=True, help='输入 FBX 文件或目录')
    parser.add_argument('--output', default=None, help='输出目录 (默认: outPut/)')
    parser.add_argument('--target', type=int, default=3000, help='目标面数 (默认: 3000)')
    parser.add_argument('--render', action='store_true', help='渲染高模 vs 低模对比图')
    parser.add_argument('--render-only', action='store_true', help='仅渲染对比图（不导出 FBX）')

    return parser.parse_args(argv)


def main():
    args = parse_args()

    project_root = Path(__file__).resolve().parent.parent
    output_dir = args.output or str(project_root / "outPut")
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"[ERROR] Input not found: {input_path}")
        sys.exit(1)

    if input_path.is_dir():
        process_batch(str(input_path), output_dir, args.target, args.render)
    else:
        process_single(str(input_path), output_dir, args.target, args.render)


if __name__ == '__main__':
    main()
