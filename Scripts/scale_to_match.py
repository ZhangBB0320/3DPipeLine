#!/usr/bin/env python3
"""
scale_to_match.py — 将一个模型等比缩放到与另一个模型一样大。

"一样大"定义：基于世界空间 bounding box 的最大维度（通常是角色高度 Z）
做等比缩放，使 src 的最大维度 == ref 的最大维度。

缩放后自动 apply scale，确保模型尺寸干净。

用法（在 Blender 内）：
    import sys; sys.path.insert(0, "/Users/zbb/3DPipeLine/Scripts")
    from scale_to_match import scale_to_match

    # 将 HighPoly 缩放到和 BodyLow 一样大
    scale_to_match("HighPoly", "BodyLow")

通过 blender_connect.py：
    from blender_connect import send_python
    send_python('''
    import sys; sys.path.insert(0, "/Users/zbb/3DPipeLine/Scripts")
    from scale_to_match import scale_to_match
    scale_to_match("HighPoly", "BodyLow")
    ''')
"""

import bpy
import mathutils


def _world_bbox(obj) -> tuple:
    """计算对象的世界空间 AABB，返回 (w_min, w_max)。"""
    w_min = mathutils.Vector((float('inf'),) * 3)
    w_max = mathutils.Vector((float('-inf'),) * 3)
    for v in obj.bound_box:
        wv = obj.matrix_world @ mathutils.Vector(v)
        for i in range(3):
            w_min[i] = min(w_min[i], wv[i])
            w_max[i] = max(w_max[i], wv[i])
    return w_min, w_max


def _bbox_dimensions(obj) -> mathutils.Vector:
    """返回世界空间 bounding box 的尺寸 (dx, dy, dz)。"""
    w_min, w_max = _world_bbox(obj)
    return w_max - w_min


def scale_to_match(src_name: str, ref_name: str, axis: str = "max", verbose: bool = True):
    """将 src 模型等比缩放到与 ref 模型一样大。

    Parameters
    ----------
    src_name : str
        要缩放的源对象名称。
    ref_name : str
        参考对象名称（目标尺寸）。
    axis : str
        匹配哪个轴的尺寸：
        - "max" — 匹配最大维度（默认，适合角色以身高为准）
        - "x" / "y" / "z" — 匹配指定轴
        - "volume" — 匹配体积（立方根比）
    verbose : bool
        是否输出详细日志。

    Returns
    -------
    float : 实际施加的缩放因子

    Raises
    ------
    ValueError
        对象不存在或不是 MESH 类型。
    """
    src = bpy.data.objects.get(src_name)
    ref = bpy.data.objects.get(ref_name)

    if src is None:
        raise ValueError(f"源对象 '{src_name}' 不存在")
    if ref is None:
        raise ValueError(f"参考对象 '{ref_name}' 不存在")
    if src.type != "MESH":
        raise ValueError(f"源对象 '{src_name}' 类型为 {src.type}，仅支持 MESH")
    if ref.type != "MESH":
        raise ValueError(f"参考对象 '{ref_name}' 类型为 {ref.type}，仅支持 MESH")

    # 计算世界空间 bbox 尺寸
    src_dim = _bbox_dimensions(src)
    ref_dim = _bbox_dimensions(ref)

    if verbose:
        print(f"[scale_to_match] '{src_name}' → '{ref_name}'")
        print(f"  源尺寸: {src_dim.x:.4f} x {src_dim.y:.4f} x {src_dim.z:.4f}")
        print(f"  参考尺寸: {ref_dim.x:.4f} x {ref_dim.y:.4f} x {ref_dim.z:.4f}")

    # 计算缩放因子
    if axis == "max":
        src_val = max(src_dim.x, src_dim.y, src_dim.z)
        ref_val = max(ref_dim.x, ref_dim.y, ref_dim.z)
        if verbose:
            matched_axis = "XYZ"[[src_dim.x, src_dim.y, src_dim.z].index(src_val)]
            print(f"  匹配最大维度: 源 {matched_axis}={src_val:.4f}, 参考 {matched_axis}={ref_val:.4f}")
    elif axis in ("x", "y", "z"):
        idx = {"x": 0, "y": 1, "z": 2}[axis]
        src_val = src_dim[idx]
        ref_val = ref_dim[idx]
        if verbose:
            print(f"  匹配 {axis.upper()} 轴: 源={src_val:.4f}, 参考={ref_val:.4f}")
    elif axis == "volume":
        src_vol = src_dim.x * src_dim.y * src_dim.z
        ref_vol = ref_dim.x * ref_dim.y * ref_dim.z
        src_val = src_vol
        ref_val = ref_vol
        if verbose:
            print(f"  匹配体积: 源={src_vol:.4f}, 参考={ref_vol:.4f}")
    else:
        raise ValueError(f"不支持的 axis 参数: '{axis}'（支持: max, x, y, z, volume）")

    if src_val < 1e-6:
        raise ValueError(f"源对象 '{src_name}' 的匹配尺寸接近零 ({src_val})，无法缩放")

    scale_factor = ref_val / src_val

    if verbose:
        print(f"  缩放因子: {scale_factor:.6f}")

    # 计算源对象的世界空间 bbox 中心
    src_w_min, src_w_max = _world_bbox(src)
    src_center = (src_w_min + src_w_max) / 2

    # 以源对象自身原点为中心等比缩放（直接乘 scale）
    src.scale = (
        src.scale[0] * scale_factor,
        src.scale[1] * scale_factor,
        src.scale[2] * scale_factor,
    )

    # Apply scale
    bpy.ops.object.select_all(action='DESELECT')
    src.select_set(True)
    bpy.context.view_layer.objects.active = src
    bpy.ops.object.transform_apply(scale=True)

    # 验证
    new_dim = _bbox_dimensions(src)
    if verbose:
        print(f"  缩放后尺寸: {new_dim.x:.4f} x {new_dim.y:.4f} x {new_dim.z:.4f}")
        print(f"  完成 ✓")

    return scale_factor


# CLI 入口
if __name__ == "__main__":
    import sys
    argv = sys.argv
    if "--" in argv:
        args = argv[argv.index("--") + 1:]
    else:
        args = argv[1:]

    if len(args) < 2:
        print("用法: scale_to_match.py <源对象> <参考对象> [axis=max|x|y|z|volume]")
    else:
        axis = args[2] if len(args) > 2 else "max"
        scale_to_match(args[0], args[1], axis=axis)
