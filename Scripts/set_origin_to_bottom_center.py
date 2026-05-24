#!/usr/bin/env python3
"""
set_origin_to_bottom_center.py — 将模型原点移到几何体的脚底中心。

"脚底中心"定义（世界空间）：
  - X: 世界 bbox X 方向中点（几何中心）
  - Y: 世界 bbox Y 方向中点（几何中心）
  - Z: 世界 bbox Z 最小值（脚底）

始终以世界 Z 轴为"上"方向，与物体局部坐标朝向无关。
通过 3D cursor 方法将原点移过去。

用法（在 Blender 内）：
    import sys; sys.path.insert(0, "/Users/zbb/3DPipeLine/Scripts")
    from set_origin_to_bottom_center import set_origin_to_bottom_center

    set_origin_to_bottom_center("MyObject")

通过 blender_connect.py：
    from blender_connect import send_python
    send_python('''
    import sys; sys.path.insert(0, "/Users/zbb/3DPipeLine/Scripts")
    from set_origin_to_bottom_center import set_origin_to_bottom_center
    set_origin_to_bottom_center("Zombie")
    ''')
"""

import bpy
import mathutils


def set_origin_to_bottom_center(obj_name: str, verbose: bool = True):
    """将指定对象的原点移到其几何 bounding box 的脚底中心。

    Parameters
    ----------
    obj_name : str
        目标对象名称。
    verbose : bool
        是否输出详细日志。

    Raises
    ------
    ValueError
        对象不存在或不是 MESH 类型。
    """
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        raise ValueError(f"对象 '{obj_name}' 不存在")
    if obj.type != "MESH":
        raise ValueError(f"对象 '{obj_name}' 类型为 {obj.type}，仅支持 MESH")

    # 保存当前 3D cursor 位置
    saved_cursor = bpy.context.scene.cursor.location.copy()

    # 计算世界空间 bounding box
    w_min = mathutils.Vector((float('inf'),) * 3)
    w_max = mathutils.Vector((float('-inf'),) * 3)
    for v in obj.bound_box:
        wv = obj.matrix_world @ mathutils.Vector(v)
        for i in range(3):
            w_min[i] = min(w_min[i], wv[i])
            w_max[i] = max(w_max[i], wv[i])

    # 脚底中心（世界空间）：X中点, Y中点, Z最小值
    bottom_center_world = mathutils.Vector((
        (w_min.x + w_max.x) / 2,  # X 几何中心
        (w_min.y + w_max.y) / 2,  # Y 几何中心
        w_min.z,                    # Z 脚底（最低点）
    ))

    if verbose:
        old_origin_world = obj.matrix_world.translation.copy()
        print(f"[set_origin_to_bottom_center] '{obj_name}'")
        print(f"  旧原点(世界): {old_origin_world}")
        print(f"  脚底中心(世界): {bottom_center_world}")
        print(f"  偏移: {bottom_center_world - old_origin_world}")

    # 使用 3D cursor 方法移动原点
    bpy.context.scene.cursor.location = bottom_center_world

    # 选中对象
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # 将原点移到 3D cursor 位置
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR')

    # 恢复 3D cursor
    bpy.context.scene.cursor.location = saved_cursor

    if verbose:
        new_origin_world = obj.matrix_world.translation.copy()
        print(f"  新原点(世界): {new_origin_world}")
        print(f"  完成 ✓")


# CLI 入口（通过 blender_connect 不会走到这里，仅供 Blender 脚本直接运行）
if __name__ == "__main__":
    import sys
    argv = sys.argv
    if "--" in argv:
        args = argv[argv.index("--") + 1:]
    else:
        args = argv[1:]

    if not args:
        print("用法: set_origin_to_bottom_center.py <对象名>")
    else:
        set_origin_to_bottom_center(args[0])
