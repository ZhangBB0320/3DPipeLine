"""
Blender FBX 导出脚本 - 通过 BlenderMCP 或 Blender 内部执行
支持: 比例缩放 / 骨骼动画 / 内嵌纹理 / 选中物体 / 全场景

使用方式:
  1) 通过 BlenderMCP execute_code 调用（推荐）
  2) Blender 内置脚本编辑器执行
  3) 命令行: blender --background --python export_fbx.py -- <参数>

参数说明:
  --objects       物体名列表，逗号分隔（默认: 空则导出全场景）
  --output        输出路径（默认: <项目根>/outPut/<物体名>.fbx）
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
        # 使用当前选中物体
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
        # 导出全场景
        if not output_path:
            DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            scene_name = bpy.context.scene.name
            output_path = str(DEFAULT_OUTPUT_DIR / f"{scene_name}.fbx")

    # 确保输出目录存在
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # 轴向映射
    axis_forward = forward
    axis_up = up

    print(f"\n===== FBX 导出 =====")
    print(f"  输出路径: {output_path}")
    print(f"  缩放比例: {scale}")
    print(f"  骨骼: {'是' if armature else '否'}")
    print(f"  动画: {'是' if animation else '否'}")
    print(f"  内嵌纹理: {'是' if embed_textures else '否'}")
    print(f"  路径模式: {path_mode}")
    print(f"  应用变换: {'是' if apply_transforms else '否'}")
    print(f"  前轴/上轴: {axis_forward}/{axis_up}")
    if objects:
        print(f"  导出物体: {obj_names if isinstance(objects, str) else objects}")

    # 执行导出
    bpy.ops.export_scene.fbx(
        filepath=output_path,
        use_selection=only_selected,
        global_scale=scale,
        use_armature=armature,
        use_animation=animation,
        embed_textures=embed_textures,
        path_mode=path_mode,
        apply_scale_options='FBX_SCALE_ALL' if apply_transforms else 'FBX_SCALE_NONE',
        axis_forward=axis_forward,
        axis_up=axis_up,
        object_types={'MESH', 'ARMATURE', 'EMPTY', 'LIGHT', 'CAMERA'} if armature else {'MESH', 'EMPTY', 'LIGHT', 'CAMERA'},
    )

    file_size = Path(output_path).stat().st_size
    print(f"\n  导出完成! 文件大小: {file_size / 1024 / 1024:.2f} MB")
    print(f"========================\n")

    return output_path


# ===== BlenderMCP 调用入口 =====
# 当通过 BlenderMCP execute_code 调用时，传入 JSON 字符串作为参数
# 示例:
#   export_fbx(objects="wooden_axe", scale=0.01, embed_textures=True)
#   export_fbx(objects="character", armature=True, animation=True)
#   export_fbx(only_selected=True)


# ===== 命令行入口 (blender --background --python) =====
if __name__ == '__main__':
    # Blender 的 -- 参数解析
    argv = sys.argv
    if '--' in argv:
        argv = argv[argv.index('--') + 1:]
    else:
        argv = []

    parser = argparse.ArgumentParser(description='Blender FBX 导出工具')
    parser.add_argument('--objects', default=None, help='物体名列表，逗号分隔')
    parser.add_argument('--output', default=None, help='输出路径')
    parser.add_argument('--scale', type=float, default=1.0, help='全局缩放比例')
    parser.add_argument('--armature', action='store_true', help='导出骨骼')
    parser.add_argument('--animation', action='store_true', help='导出动画')
    parser.add_argument('--no-embed-textures', dest='embed_textures', action='store_false', help='不内嵌纹理')
    parser.add_argument('--path-mode', default='COPY', choices=['COPY', 'AUTO', 'ABSOLUTE', 'RELATIVE'])
    parser.add_argument('--no-apply-transforms', dest='apply_transforms', action='store_false', help='不应用变换')
    parser.add_argument('--forward', default='-Z', help='前轴')
    parser.add_argument('--up', default='Y', help='上轴')
    parser.add_argument('--only-selected', action='store_true', help='仅导出选中物体')

    args = parser.parse_args(argv)

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
