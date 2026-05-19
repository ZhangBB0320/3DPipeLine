"""
Blender FBX/OBJ 导出脚本 - 三种调用方式
支持: 比例缩放 / 骨骼动画 / 内嵌纹理 / 选中物体 / 全场景

使用方式:
  1) 通过 BlenderMCP execute_code 调用（需要 Blender 运行 + MCP 连接）
  2) 命令行: blender --background input.blend --python export_fbx.py -- <参数>
     （无需打开 Blender GUI，无需 MCP，直接从 .blend 文件导出）
  3) Blender 内置脚本编辑器执行

参数说明:
  --objects       物体名列表，逗号分隔（默认: 空则导出全场景）
  --output        输出路径（默认: <项目根>/outPut/<物体名>.fbx）
  --format        导出格式: fbx/obj（默认: fbx）
  --scale         全局缩放比例（默认: 1.0）
  --armature      是否导出骨骼（默认: False）
  --animation     是否导出动画（默认: False）
  --embed-textures 是否内嵌纹理（默认: True）
  --path-mode     路径模式: COPY/AUTO/ABSOLUTE/RELATIVE（默认: COPY）
  --apply-transforms 是否应用变换（默认: True）
  --forward       前轴: Y/Z/-Y/-Z（默认: -Z）
  --up            上轴: Y/Z/-Y/-Z（默认: Y）
  --only-selected 是否仅导出选中物体（默认: False，当指定--objects时自动True）
"""

import bpy
import argparse
import sys
import json
from pathlib import Path


# ===== 默认输出目录：项目根/outPut/ =====
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outPut"


def get_scene_info():
    """获取当前场景信息（物体列表、材质等）"""
    return {
        'name': bpy.context.scene.name,
        'objects': [{'name': o.name, 'type': o.type} for o in bpy.data.objects],
        'meshes': [{'name': o.name, 'type': o.type} for o in bpy.data.objects if o.type == 'MESH'],
        'armatures': [{'name': o.name} for o in bpy.data.objects if o.type == 'ARMATURE'],
        'materials': [m.name for m in bpy.data.materials],
    }


def export_fbx(
    objects=None,
    output_path=None,
    scale=1.0,
    armature=False,
    animation=False,
    embed_textures=True,
    path_mode='COPY',
    apply_transforms=True,
    forward='-Z',
    up='Y',
    only_selected=False,
):
    """
    导出 FBX

    Args:
        objects: 物体名列表，None 则导出全场景
        output_path: 输出路径，None 则自动生成到 outPut/<物体名>.fbx
        scale: 全局缩放比例
        armature: 是否包含骨骼
        animation: 是否包含动画
        embed_textures: 是否内嵌纹理
        path_mode: 路径模式 COPY/AUTO/ABSOLUTE/RELATIVE
        apply_transforms: 是否应用变换（旋转/缩放烘焙到顶点）
        forward: 前轴方向
        up: 上轴方向
        only_selected: 仅导出当前选中物体
    """
    # 确定要导出的物体
    if objects:
        obj_names = [n.strip() for n in objects.split(',') if n.strip()] if isinstance(objects, str) else objects
        bpy.ops.object.select_all(action='DESELECT')
        found = []
        missing = []
        for name in obj_names:
            obj = bpy.data.objects.get(name)
            if obj:
                obj.select_set(True)
                found.append(obj)
            else:
                missing.append(name)
        if missing:
            print(f"[警告] 未找到物体: {missing}")
        if not found:
            print("[错误] 没有找到任何指定物体，导出中止")
            return None
        bpy.context.view_layer.objects.active = found[0]
        only_selected = True
        # 自动生成输出文件名
        if not output_path:
            DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            if len(found) == 1:
                output_path = str(DEFAULT_OUTPUT_DIR / f"{found[0].name}.fbx")
            else:
                output_path = str(DEFAULT_OUTPUT_DIR / "merged_export.fbx")

    elif only_selected:
        selected = bpy.context.selected_objects
        if not selected:
            print("[错误] 没有选中任何物体")
            return None
        if not output_path:
            DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            if len(selected) == 1:
                output_path = str(DEFAULT_OUTPUT_DIR / f"{selected[0].name}.fbx")
            else:
                output_path = str(DEFAULT_OUTPUT_DIR / "merged_export.fbx")
    else:
        if not output_path:
            DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            scene_name = bpy.context.scene.name
            output_path = str(DEFAULT_OUTPUT_DIR / f"{scene_name}.fbx")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    print(f"\n===== FBX 导出 =====")
    print(f"  输出路径: {output_path}")
    print(f"  缩放比例: {scale}")
    print(f"  骨骼: {'是' if armature else '否'}")
    print(f"  动画: {'是' if animation else '否'}")
    print(f"  内嵌纹理: {'是' if embed_textures else '否'}")
    print(f"  路径模式: {path_mode}")
    print(f"  应用变换: {'是' if apply_transforms else '否'}")
    print(f"  前轴/上轴: {forward}/{up}")

    # 构建 FBX 导出参数（兼容 Blender 3.x/4.x/5.x）
    fbx_kwargs = dict(
        filepath=output_path,
        use_selection=only_selected,
        global_scale=scale,
        embed_textures=embed_textures,
        path_mode=path_mode,
        apply_scale_options='FBX_SCALE_ALL' if apply_transforms else 'FBX_SCALE_NONE',
        axis_forward=forward,
        axis_up=up,
        object_types={'MESH', 'ARMATURE', 'EMPTY', 'LIGHT', 'CAMERA'} if armature else {'MESH', 'EMPTY', 'LIGHT', 'CAMERA'},
    )
    # Blender 3.x/4.x 支持 use_armature/use_animation，Blender 5.x 已移除
    try:
        bpy.ops.export_scene.fbx(**fbx_kwargs)
    except TypeError as e:
        if 'use_armature' in str(e) or 'use_animation' in str(e):
            fbx_kwargs.pop('use_armature', None)
            fbx_kwargs.pop('use_animation', None)
            bpy.ops.export_scene.fbx(**fbx_kwargs)
        else:
            raise

    file_size = Path(output_path).stat().st_size
    print(f"\n  导出完成! 文件大小: {file_size / 1024 / 1024:.2f} MB")

    # ===== 清理垃圾文件：只保留目标文件 =====
    _cleanup_output_dir(output_path)

    print(f"========================\n")
    return output_path


def export_obj(
    objects=None,
    output_path=None,
    scale=1.0,
    apply_transforms=True,
    forward='-Z',
    up='Y',
    only_selected=False,
):
    """
    导出 OBJ（纯几何，无骨骼/动画）

    Args:
        objects: 物体名列表，None 则导出全场景
        output_path: 输出路径，None 则自动生成到 outPut/<物体名>.obj
        scale: 全局缩放比例
        apply_transforms: 是否应用变换
        forward: 前轴方向
        up: 上轴方向
        only_selected: 仅导出当前选中物体
    """
    if objects:
        obj_names = [n.strip() for n in objects.split(',') if n.strip()] if isinstance(objects, str) else objects
        bpy.ops.object.select_all(action='DESELECT')
        found = []
        missing = []
        for name in obj_names:
            obj = bpy.data.objects.get(name)
            if obj:
                obj.select_set(True)
                found.append(obj)
            else:
                missing.append(name)
        if missing:
            print(f"[警告] 未找到物体: {missing}")
        if not found:
            print("[错误] 没有找到任何指定物体，导出中止")
            return None
        bpy.context.view_layer.objects.active = found[0]
        only_selected = True
        if not output_path:
            DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            if len(found) == 1:
                output_path = str(DEFAULT_OUTPUT_DIR / f"{found[0].name}.obj")
            else:
                output_path = str(DEFAULT_OUTPUT_DIR / "merged_export.obj")
    elif only_selected:
        selected = bpy.context.selected_objects
        if not selected:
            print("[错误] 没有选中任何物体")
            return None
        if not output_path:
            DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            if len(selected) == 1:
                output_path = str(DEFAULT_OUTPUT_DIR / f"{selected[0].name}.obj")
            else:
                output_path = str(DEFAULT_OUTPUT_DIR / "merged_export.obj")
    else:
        if not output_path:
            DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            scene_name = bpy.context.scene.name
            output_path = str(DEFAULT_OUTPUT_DIR / f"{scene_name}.obj")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    print(f"\n===== OBJ 导出 =====")
    print(f"  输出路径: {output_path}")
    print(f"  缩放比例: {scale}")
    print(f"  应用变换: {'是' if apply_transforms else '否'}")
    print(f"  前轴/上轴: {forward}/{up}")

    # 轴向格式转换：'-Z' → 'NEGATIVE_Z' (Blender 4.x+)
    def _axis_to_v4(axis):
        mapping = {'-X': 'NEGATIVE_X', '-Y': 'NEGATIVE_Y', '-Z': 'NEGATIVE_Z',
                   'X': 'X', 'Y': 'Y', 'Z': 'Z'}
        return mapping.get(axis, axis)

    # Blender 4.x+ 使用 wm.obj_export
    try:
        bpy.ops.wm.obj_export(
            filepath=output_path,
            export_selected_objects=only_selected,
            global_scale=scale,
            apply_modifiers=True,
            forward_axis=_axis_to_v4(forward),
            up_axis=_axis_to_v4(up),
        )
    except AttributeError:
        # Blender 3.x 使用 export_scene.obj
        bpy.ops.export_scene.obj(
            filepath=output_path,
            use_selection=only_selected,
            global_scale=scale,
            path_mode='COPY',
            axis_forward=forward,
            axis_up=up,
        )

    file_size = Path(output_path).stat().st_size
    print(f"\n  导出完成! 文件大小: {file_size / 1024 / 1024:.2f} MB")

    # ===== 清理垃圾文件：只保留目标文件 =====
    _cleanup_output_dir(output_path)

    print(f"========================\n")
    return output_path


def _cleanup_output_dir(target_path):
    """
    清理输出目录中的垃圾文件，只保留目标文件。
    当 path_mode='COPY' 时，Blender 会复制纹理到输出目录，
    产生 .mtl/.jpg/.png/.exr 等无关文件，必须删除。
    """
    target = Path(target_path).resolve()
    output_dir = target.parent
    target_name = target.stem  # 不含扩展名

    removed = []
    for f in output_dir.iterdir():
        if not f.is_file():
            continue
        # 保留目标文件本身
        if f.resolve() == target:
            continue
        # 删除与目标文件同名的伴随文件（如 .mtl）
        if f.stem == target_name:
            f.unlink()
            removed.append(f.name)
            continue
        # 删除常见纹理格式文件
        if f.suffix.lower() in ('.mtl', '.jpg', '.jpeg', '.png', '.exr', '.tga', '.bmp', '.tif', '.tiff', '.webp', '.dds'):
            f.unlink()
            removed.append(f.name)

    if removed:
        print(f"  [清理] 删除 {len(removed)} 个垃圾文件: {', '.join(removed)}")
    else:
        print(f"  [清理] 无需清理")


def export(
    objects=None,
    output_path=None,
    format='fbx',
    **kwargs,
):
    """
    通用导出入口，根据 format 选择 FBX 或 OBJ

    Args:
        objects: 物体名列表
        output_path: 输出路径
        format: 'fbx' 或 'obj'
        **kwargs: 传递给对应导出函数的参数
    """
    if format.lower() == 'obj':
        # OBJ 不支持的参数过滤掉
        obj_kwargs = {k: v for k, v in kwargs.items()
                      if k in ('scale', 'apply_transforms', 'forward', 'up', 'only_selected')}
        return export_obj(objects=objects, output_path=output_path, **obj_kwargs)
    else:
        return export_fbx(objects=objects, output_path=output_path, **kwargs)


# ===== BlenderMCP 调用入口 =====
# 示例:
#   export_fbx(objects="wooden_axe", scale=0.01, embed_textures=True)
#   export_obj(objects="wooden_axe")
#   export(objects="wooden_axe", format='obj')
#   get_scene_info()  → 查看场景物体列表


# ===== 命令行入口 (blender --background file.blend --python export_fbx.py -- <参数>) =====
if __name__ == '__main__':
    argv = sys.argv
    if '--' in argv:
        argv = argv[argv.index('--') + 1:]
    else:
        argv = []

    parser = argparse.ArgumentParser(description='Blender FBX/OBJ 导出工具')
    parser.add_argument('--objects', default=None, help='物体名列表，逗号分隔')
    parser.add_argument('--output', default=None, help='输出路径')
    parser.add_argument('--format', default='fbx', choices=['fbx', 'obj'], help='导出格式')
    parser.add_argument('--scale', type=float, default=1.0, help='全局缩放比例')
    parser.add_argument('--armature', action='store_true', help='导出骨骼')
    parser.add_argument('--animation', action='store_true', help='导出动画')
    parser.add_argument('--no-embed-textures', dest='embed_textures', action='store_false', help='不内嵌纹理')
    parser.add_argument('--path-mode', default='COPY', choices=['COPY', 'AUTO', 'ABSOLUTE', 'RELATIVE'])
    parser.add_argument('--no-apply-transforms', dest='apply_transforms', action='store_false', help='不应用变换')
    parser.add_argument('--forward', default='-Z', help='前轴')
    parser.add_argument('--up', default='Y', help='上轴')
    parser.add_argument('--only-selected', action='store_true', help='仅导出选中物体')
    parser.add_argument('--info', action='store_true', help='仅输出场景信息，不导出')

    args = parser.parse_args(argv)

    # 仅查看场景信息
    if args.info:
        info = get_scene_info()
        print("SCENE_JSON:" + json.dumps(info, ensure_ascii=False))
        sys.exit(0)

    if args.format == 'obj':
        export_obj(
            objects=args.objects,
            output_path=args.output,
            scale=args.scale,
            apply_transforms=args.apply_transforms,
            forward=args.forward,
            up=args.up,
            only_selected=args.only_selected,
        )
    else:
        export_fbx(
            objects=args.objects,
            output_path=args.output,
            scale=args.scale,
            armature=args.armature,
            animation=args.animation,
            embed_textures=args.embed_textures,
            path_mode=args.path_mode,
            apply_transforms=args.apply_transforms,
            forward=args.forward,
            up=args.up,
            only_selected=args.only_selected,
        )
