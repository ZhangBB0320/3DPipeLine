#!/usr/bin/env python3
"""
模型导出脚本 — 支持按参数选择 FBX（内嵌纹理）或 OBJ（纯几何）。

用法：
    from export_model import export_model

    # 导出为 FBX（内嵌纹理）
    export_model(
        obj_names=["building1_Low"],
        output_path="/path/to/output.fbx",
        fmt="fbx",
    )

    # 导出为 FBX（内嵌纹理），自定义文件名
    export_model(
        obj_names=["building1_Low"],
        output_path="/path/to/output.fbx",
        output_name="my_building",
        fmt="fbx",
    )
    # 导出为 OBJ（纯几何，无纹理）
    export_model(
        obj_names=["building1_Low"],
        output_path="/path/to/output.obj",
        fmt="obj",
    )

    # 自动判断：有纹理材质 → FBX，无纹理 → OBJ
    export_model(
        obj_names=["building1_Low"],
        output_path="/path/to/output",  # 无扩展名时自动添加
        fmt="auto",
    )

    # 通过 blender_connect.py 推送
    from blender_connect import send_python
    send_python('''
    import sys; sys.path.insert(0, "/Users/zbb/3DPipeLine/Scripts")
    from export_model import export_model
    export_model(["building1_Low"], "/tmp/building1.fbx", fmt="fbx")
    ''')

导出格式说明：
    FBX:
      - 内嵌纹理（path_mode='COPY' + embed_textures=True）
      - 仅导出选中对象
      - 应用修改器
      - target="unity": Y-up（自动烘焙 Z-up→Y-up 轴转换，Unity 导入无需旋转）
      - target="blender": Z-up（不做轴转换，Blender 原生坐标）
    OBJ:
      - 纯几何，不导出材质/纹理
      - 仅导出选中对象
      - 应用修改器
      - target="unity": Y-up（Unity 兼容）
      - target="blender": Z-up（Blender 原生坐标）
"""

import os
import sys


def _has_textured_materials(obj) -> bool:
    """检查对象是否拥有带纹理的材质。"""
    for slot in obj.material_slots:
        m = slot.material
        if m is None:
            continue
        if not m.use_nodes:
            continue
        for node in m.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image is not None:
                return True
    return False


def _any_has_textures(objects) -> bool:
    """检查对象列表中是否有任何一个拥有纹理材质。"""
    return any(_has_textured_materials(o) for o in objects)


def _resolve_format(fmt: str, objects) -> str:
    """解析导出格式。
    
    Parameters
    ----------
    fmt : str
        "fbx", "obj", 或 "auto"（根据纹理自动判断）
    objects : list
        Blender 对象列表
    
    Returns
    -------
    str : "fbx" 或 "obj"
    """
    fmt = fmt.lower().strip()
    if fmt in ("fbx", "obj"):
        return fmt
    if fmt == "auto":
        if _any_has_textures(objects):
            print("    [auto] 检测到纹理材质 → FBX（内嵌纹理）")
            return "fbx"
        else:
            print("    [auto] 无纹理材质 → OBJ（纯几何）")
            return "obj"
    raise ValueError(f"不支持的导出格式: '{fmt}'（支持: fbx, obj, auto）")


def _resolve_output_path(output_path: str, fmt: str, output_name: str = "") -> str:
    """确保输出路径有正确的扩展名。

    Parameters
    ----------
    output_path : str
        输出目录或完整文件路径。
    fmt : str
        导出格式 "fbx" 或 "obj"。
    output_name : str
        自定义文件名（不含扩展名）。提供后覆盖 output_path 中的文件名部分。
        例：output_path="/tmp/out.fbx", output_name="my_model" → "/tmp/my_model.fbx"
    """
    if not output_path:
        raise ValueError("output_path 不能为空")

    ext = ".fbx" if fmt == "fbx" else ".obj"

    # 如果指定了 output_name，替换路径中的文件名
    if output_name:
        directory = os.path.dirname(os.path.abspath(output_path))
        # 清理用户传入的扩展名（避免重复）
        stem = os.path.splitext(output_name)[0] if os.path.splitext(output_name)[1].lower() in (".fbx", ".obj") else output_name
        return os.path.join(directory, stem + ext)

    current_ext = os.path.splitext(output_path)[1].lower()

    if current_ext == ext:
        return output_path
    elif current_ext in (".fbx", ".obj"):
        # 扩展名与格式不匹配，替换
        return os.path.splitext(output_path)[0] + ext
    else:
        # 无扩展名或非标准扩展名，追加
        return output_path + ext


def _apply_transforms(objects):
    """对导出对象应用变换（rotation/scale），确保导出坐标正确。

    注意：不 apply location，以保留对象的原点位置（如 set_origin_to_bottom_center 设置的底部原点）。
    如果 apply location，原点会被强制移到 (0,0,0)，导致底部原点变成几何中心。
    """
    import bpy

    for obj in objects:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)


def _select_objects(objects):
    """选中所有待导出对象。"""
    import bpy

    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def _bake_axis_conversion(objects, verbose=True):
    """将 Blender Z-up → Y-up 轴转换烘焙进顶点，使导出的 FBX 在 Unity 中无需旋转。

    原理：
      1. 对每个对象施加 -90° X 旋转（Z-up → Y-up）并 apply，将轴转换烘焙进顶点
      2. 设置 +90° X 旋转，抵消 FBX 导出器自动施加的 -90° X 轴转换
      结果：FBX 中顶点为 Y-up，对象旋转为 identity，Unity 导入后方向正确
    """
    import bpy
    import math

    if verbose:
        print("    Baking Z-up → Y-up axis conversion for Unity...")

    for obj in objects:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        # Step 1: 旋转 -90° X (Z-up → Y-up) 并烘焙进顶点
        obj.rotation_euler = (math.radians(-90), 0, 0)
        bpy.ops.object.transform_apply(rotation=True)
        # Step 2: 设 +90° X 抵消导出器的轴转换（导出器会施加 -90° X）
        obj.rotation_euler = (math.radians(90), 0, 0)

    if verbose:
        print("    Axis conversion baked into vertices.")


def _reset_axis_conversion(objects):
    """导出后清理：将对象旋转重置为 (0,0,0)。"""
    import bpy

    for obj in objects:
        obj.rotation_euler = (0, 0, 0)


def _export_fbx(objects, output_path, apply_transforms=True, target="unity", verbose=True):
    """导出为 FBX（内嵌纹理），根据 target 决定是否烘焙轴转换。

    Parameters
    ----------
    target : str
        "unity" — 烘焙 Z-up→Y-up 轴转换，导出 Y-up FBX（Unity 兼容）
        "blender" — 不做轴转换，导出 Z-up FBX（Blender 原生坐标）
    """
    import bpy

    if apply_transforms:
        print("    Applying transforms...")
        _apply_transforms(objects)

    # 仅 Unity 目标需要烘焙 Z-up → Y-up 轴转换
    if target == "unity":
        _bake_axis_conversion(objects, verbose=verbose)
    else:
        if verbose:
            print("    Target=Blender: skipping axis conversion (Z-up native)")

    _select_objects(objects)

    # 导出前先保存所有未保存的图像（确保内嵌纹理数据完整）
    if verbose:
        print("    Saving unsaved images before export...")
    for img in bpy.data.images:
        if img.is_dirty:
            try:
                img.save()
                if verbose:
                    print(f"      Saved dirty image: {img.name}")
            except Exception as e:
                if verbose:
                    print(f"      [WARN] Could not save image '{img.name}': {e}")

    print(f"    Exporting FBX (embedded textures) → {output_path}")

    # 确保输出目录存在
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    # 根据目标平台设置轴方向
    if target == "unity":
        axis_forward, axis_up = "-Z", "Y"    # Y-up（Unity 标准）
    else:
        axis_forward, axis_up = "-Y", "Z"    # Z-up（Blender 原生）

    kwargs = dict(
        filepath=output_path,
        use_selection=True,
        # 内嵌纹理关键参数
        path_mode="COPY",        # 复制纹理文件到 FBX 旁边或内嵌
        embed_textures=True,     # 内嵌纹理到 FBX
        # 网格选项
        use_mesh_modifiers=True,
        mesh_smooth_type="FACE", # 保持面平滑标记
        # 材质/动画
        use_custom_props=True,
        add_leaf_bones=False,
        # 轴方向
        axis_forward=axis_forward,
        axis_up=axis_up,
    )

    bpy.ops.export_scene.fbx(**kwargs)

    # 导出后清理对象旋转（仅 Unity 目标做了轴转换，需要恢复）
    if target == "unity":
        _reset_axis_conversion(objects)

    n_faces = sum(len(o.data.polygons) for o in objects)
    n_verts = sum(len(o.data.vertices) for o in objects)
    print(f"    FBX exported: {len(objects)} object(s), {n_verts} verts, {n_faces} faces")
    print(f"    Path: {output_path}")


def _export_obj(objects, output_path, apply_transforms=True, target="unity", verbose=True):
    """导出为 OBJ（纯几何，无材质/纹理）。"""
    import bpy

    if apply_transforms:
        print("    Applying transforms...")
        _apply_transforms(objects)

    _select_objects(objects)

    print(f"    Exporting OBJ (geometry only) → {output_path}")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    if target == "unity":
        forward_axis, up_axis = "NEGATIVE_Z", "Y"
    else:
        forward_axis, up_axis = "NEGATIVE_Y", "Z"

    kwargs = dict(
        filepath=output_path,
        # 不导出材质
        export_materials=False,
        # 不导出法线（避免兼容性问题，让导入端重新计算）
        export_normals=False,
        # 导出 UV（保留展开信息，对后续烘焙有用）
        export_uv=True,
        # 坐标
        forward_axis=forward_axis,
        up_axis=up_axis,
    )

    bpy.ops.wm.obj_export(**kwargs)

    n_faces = sum(len(o.data.polygons) for o in objects)
    n_verts = sum(len(o.data.vertices) for o in objects)
    print(f"    OBJ exported: {len(objects)} object(s), {n_verts} verts, {n_faces} faces")
    print(f"    Path: {output_path}")


def export_model(
    obj_names,
    output_path,
    fmt="auto",
    output_name="",
    target="unity",
    apply_transforms=True,
    verbose=True,
):
    """
    导出模型为 FBX（内嵌纹理）或 OBJ（纯几何）。

    Parameters
    ----------
    obj_names : str or list[str]
        要导出的对象名称。可以是单个名称或名称列表。
        仅支持 MESH 类型对象，非 MESH 对象会被跳过。
    output_path : str
        输出文件路径。如果扩展名与格式不匹配会自动修正；
        如果无扩展名会自动追加（.fbx / .obj）。
    fmt : str
        导出格式：
        - "fbx" — FBX，内嵌纹理（适合带贴图的烘焙低模）
        - "obj" — OBJ，纯几何无纹理（适合只需模型的场景）
        - "auto" — 自动判断：有纹理材质 → FBX，无纹理 → OBJ
    output_name : str
        自定义导出文件名（不含扩展名）。提供后覆盖 output_path 中的文件名部分，
        目录仍取自 output_path。例：output_name="my_house" → my_house.fbx
    target : str
        目标平台：
        - "unity" — 烘焙 Z-up→Y-up 轴转换，导出 Y-up 文件（Unity 兼容）
        - "blender" — 不做轴转换，导出 Z-up 文件（Blender 原生坐标）
    apply_transforms : bool
        导出前是否应用变换（location/rotation/scale → 0）。
        默认 True，确保导出坐标干净。
    verbose : bool
        是否输出详细日志。

    Returns
    -------
    dict : {
        "format": str,          # 实际使用的格式 "fbx" 或 "obj"
        "path": str,            # 最终输出路径
        "objects": list[str],   # 导出的对象名列表
        "n_verts": int,         # 总顶点数
        "n_faces": int,         # 总面数
    }
    """
    import bpy

    # 标准化 obj_names
    if isinstance(obj_names, str):
        obj_names = [obj_names]

    # 收集有效的 MESH 对象
    objects = []
    for name in obj_names:
        obj = bpy.data.objects.get(name)
        if obj is None:
            print(f"    [WARN] 对象 '{name}' 不存在，跳过")
            continue
        if obj.type != "MESH":
            print(f"    [WARN] 对象 '{name}' 类型为 {obj.type}（非 MESH），跳过")
            continue
        objects.append(obj)

    if not objects:
        raise RuntimeError(f"没有可导出的 MESH 对象: {obj_names}")

    # 解析格式
    resolved_fmt = _resolve_format(fmt, objects)

    # 解析输出路径
    resolved_path = _resolve_output_path(output_path, resolved_fmt, output_name=output_name)

    print("=" * 70)
    print(f"EXPORT: {resolved_fmt.upper()}")
    print(f"  Objects: {[o.name for o in objects]}")
    print(f"  Output:  {resolved_path}")
    print(f"  Target:  {target} ({'Y-up, axis conversion' if target == 'unity' else 'Z-up, native coords'})")
    print(f"  Textures: {'yes (embedded)' if resolved_fmt == 'fbx' else 'no'}")
    print("=" * 70)

    # 执行导出
    if resolved_fmt == "fbx":
        _export_fbx(objects, resolved_path, apply_transforms=apply_transforms, target=target, verbose=verbose)
    else:
        _export_obj(objects, resolved_path, apply_transforms=apply_transforms, target=target, verbose=verbose)

    n_verts = sum(len(o.data.vertices) for o in objects)
    n_faces = sum(len(o.data.polygons) for o in objects)

    result = {
        "format": resolved_fmt,
        "path": resolved_path,
        "objects": [o.name for o in objects],
        "n_verts": n_verts,
        "n_faces": n_faces,
    }

    print(f"\n[DONE] Export complete:")
    print(f"    Format: {result['format'].upper()}")
    print(f"    Path:   {result['path']}")
    print(f"    Objects: {result['objects']}")
    print(f"    Verts:  {result['n_verts']}, Faces: {result['n_faces']}")

    return result


def export_all_meshes(
    output_dir,
    fmt="auto",
    target="unity",
    apply_transforms=True,
    name_pattern="{name}",
    verbose=True,
):
    """
    批量导出场景中所有 MESH 对象，每个对象单独一个文件。

    Parameters
    ----------
    output_dir : str
        输出目录，不存在则自动创建。
    fmt : str
        导出格式（同 export_model 的 fmt 参数）。
    target : str
        目标平台："unity" 或 "blender"。
    apply_transforms : bool
        导出前是否应用变换。
    name_pattern : str
        文件名模板，支持 {name} 占位符（对象名）。
        例："{name}_baked" → building1_Low_baked.fbx
    verbose : bool

    Returns
    -------
    list[dict] : 每个对象的导出结果摘要
    """
    import bpy

    os.makedirs(output_dir, exist_ok=True)

    results = []
    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]

    if not mesh_objs:
        print("[WARN] 场景中没有 MESH 对象")
        return results

    print(f"\nBATCH EXPORT: {len(mesh_objs)} mesh object(s) → {output_dir}")

    for obj in mesh_objs:
        filename = name_pattern.format(name=obj.name)
        out_path = os.path.join(output_dir, filename)

        try:
            result = export_model(
                obj_names=[obj.name],
                output_path=out_path,
                fmt=fmt,
                target=target,
                apply_transforms=apply_transforms,
                verbose=verbose,
            )
            results.append(result)
        except Exception as e:
            print(f"    [ERROR] Export failed for '{obj.name}': {e}")
            results.append({"object": obj.name, "error": str(e)})

    # 摘要
    n_ok = sum(1 for r in results if "error" not in r)
    print(f"\nBATCH EXPORT SUMMARY: {n_ok}/{len(mesh_objs)} succeeded")
    for r in results:
        if "error" in r:
            print(f"  [ERR] {r['object']}: {r['error']}")
        else:
            print(f"  [OK ] {r['objects'][0]} → {r['path']} ({r['format'].upper()})")

    return results


# ============================================================
# 带骨骼角色导出
# ============================================================


def _compute_mesh_world_center(meshes):
    """计算所有 mesh 的世界空间包围盒中心。"""
    from mathutils import Vector

    all_min = Vector((float("inf"),) * 3)
    all_max = Vector((float("-inf"),) * 3)

    for m in meshes:
        for corner in m.bound_box:
            world_corner = m.matrix_world @ Vector(corner)
            for i in range(3):
                all_min[i] = min(all_min[i], world_corner[i])
                all_max[i] = max(all_max[i], world_corner[i])

    return (all_min + all_max) / 2


def _save_object_transforms(objects):
    """保存对象的 location/rotation/scale 状态，用于后续恢复。

    Returns
    -------
    dict : {object_name: (location, rotation_euler, scale)}
    """
    saved = {}
    for obj in objects:
        saved[obj.name] = (
            obj.location.copy(),
            obj.rotation_euler.copy(),
            obj.scale.copy(),
        )
    return saved


def _restore_object_transforms(saved, verbose=True):
    """恢复对象的 location/rotation/scale 到保存的状态。

    Parameters
    ----------
    saved : dict
        _save_object_transforms 的返回值。
    """
    import bpy

    for obj_name, (loc, rot, scale) in saved.items():
        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            if verbose:
                print(f"    [WARN] restore: 对象 '{obj_name}' 不存在，跳过")
            continue
        obj.location = loc
        obj.rotation_euler = rot
        obj.scale = scale

    if verbose:
        print("    Restored object transforms (programmatic, no undo).")


def _center_rigged_to_origin(arm, meshes, verbose=True):
    """将带骨骼角色居中到世界原点。

    策略：仅调整 armature 的 location，不做 transform_apply。

    原因：Mixamo 导入的 armature 携带 rotation=(90°, 0, 0) 和
    scale=(0.0053) 用于轴转换和单位换算。如果 transform_apply(rotation=True,
    scale=True)，这些变换会被烘焙进骨骼 rest pose，导致 FBX 导出时
    双重轴转换（Blender 导出器再叠加一次），骨骼位置全部错乱。

    正确做法：
    1. 计算 mesh 包围盒世界空间中心，确定 offset
    2. arm.location += offset（子级 mesh 自动跟随）
    3. 非子级 mesh 独立移动
    4. 不做任何 transform_apply — armature 保留原始 rotation/scale，
       骨骼数据不变
    5. 导出后由调用方恢复 arm.location 即可（无需恢复骨骼/顶点数据）

    子级 mesh 的 location 是相对于 armature 的 local space，
    移动 armature 后子级 mesh 的世界位置自动更新，无需额外处理。
    """
    import bpy

    center = _compute_mesh_world_center(meshes)

    if verbose:
        print(f"    Mesh bounding center: ({center.x:.4f}, {center.y:.4f}, {center.z:.4f})")
        print(f"    Centering to origin: offset = -({center.x:.4f}, {center.y:.4f}, {center.z:.4f})")

    offset = -center

    # 区分子级 mesh 和非子级 mesh
    child_meshes = [m for m in meshes if m.parent == arm]
    non_child_meshes = [m for m in meshes if m.parent != arm]

    if verbose and child_meshes:
        print(f"    Child meshes (follow armature): {[m.name for m in child_meshes]}")
    if verbose and non_child_meshes:
        print(f"    Non-child meshes (move independently): {[m.name for m in non_child_meshes]}")

    # 移动 armature（子级 mesh 自动跟随）+ 非子级 mesh 独立移动
    arm.location += offset
    for m in non_child_meshes:
        m.location += offset

    # 刷新 depsgraph 确保包围盒更新
    bpy.context.view_layer.update()

    if verbose:
        new_center = _compute_mesh_world_center(meshes)
        print(f"    After centering: mesh center = ({new_center.x:.6f}, {new_center.y:.6f}, {new_center.z:.6f})")


def export_rigged_model(
    armature_name,
    output_path,
    mesh_names=None,
    output_name="",
    target="unity",
    move_to_origin=True,
    verbose=True,
):
    """
    导出带骨骼的角色模型为 FBX（内嵌纹理）。

    自动查找骨架的子级 mesh，导出骨架+mesh 为 FBX。

    Parameters
    ----------
    armature_name : str
        骨架（ARMATURE）对象名称。
    output_path : str
        输出文件路径。
    mesh_names : list[str] or None
        要导出的 mesh 名称列表。None 则自动取骨架的所有子级 MESH。
    output_name : str
        自定义导出文件名（不含扩展名）。
    target : str
        目标平台："unity"（Y-up）或 "blender"（Z-up）。
    move_to_origin : bool
        是否将角色居中到原点后导出。True 时仅调整 armature.location 将角色
        居中到世界原点（不做 transform_apply，不破坏骨骼 rest pose），
        导出后程序化恢复对象 location。False 时直接导出当前位置。
    verbose : bool
        是否输出详细日志。

    Returns
    -------
    dict : {
        "format": "fbx",
        "path": str,
        "armature": str,
        "meshes": list[str],
        "n_verts": int,
        "n_faces": int,
    }
    """
    import bpy

    # 查找骨架
    arm = bpy.data.objects.get(armature_name)
    if arm is None:
        raise RuntimeError(f"骨架对象 '{armature_name}' 不存在")
    if arm.type != "ARMATURE":
        raise RuntimeError(f"对象 '{armature_name}' 类型为 {arm.type}（非 ARMATURE）")

    # 收集 mesh
    if mesh_names is not None:
        meshes = []
        for mn in mesh_names:
            m = bpy.data.objects.get(mn)
            if m is None:
                if verbose:
                    print(f"    [WARN] mesh '{mn}' 不存在，跳过")
                continue
            if m.type != "MESH":
                if verbose:
                    print(f"    [WARN] 对象 '{mn}' 类型为 {m.type}（非 MESH），跳过")
                continue
            meshes.append(m)
    else:
        # 自动取骨架的所有子级 MESH + 通过 Armature modifier 绑定的 mesh
        meshes = [c for c in arm.children if c.type == "MESH"]
        for obj in bpy.data.objects:
            if obj.type == "MESH" and obj not in meshes:
                for mod in obj.modifiers:
                    if mod.type == "ARMATURE" and mod.object == arm:
                        meshes.append(obj)
                        break

    if not meshes:
        raise RuntimeError(f"骨架 '{armature_name}' 没有可导出的 MESH 子级")

    # 解析输出路径
    resolved_path = _resolve_output_path(output_path, "fbx", output_name=output_name)

    print("=" * 70)
    print(f"EXPORT RIGGED: FBX (embedded textures)")
    print(f"  Armature:      {arm.name}")
    print(f"  Meshes:        {[m.name for m in meshes]}")
    print(f"  Output:        {resolved_path}")
    print(f"  Target:        {target} ({'Y-up' if target == 'unity' else 'Z-up'})")
    print(f"  Move to origin: {move_to_origin}")
    print("=" * 70)

    # 居中前：保存对象变换（新方案不修改骨骼/顶点数据，只需恢复 location）
    saved_transforms = None

    if move_to_origin:
        if verbose:
            print("    Saving object transforms before centering...")
        saved_transforms = _save_object_transforms([arm] + meshes)

        _center_rigged_to_origin(arm, meshes, verbose=verbose)

    # 选中骨架 + 所有 mesh
    bpy.ops.object.select_all(action="DESELECT")
    arm.select_set(True)
    for m in meshes:
        m.select_set(True)
    bpy.context.view_layer.objects.active = arm

    # 保存脏图像
    if verbose:
        print("    Saving unsaved images before export...")
    for img in bpy.data.images:
        if img.is_dirty:
            try:
                img.save()
                if verbose:
                    print(f"      Saved dirty image: {img.name}")
            except Exception as e:
                if verbose:
                    print(f"      [WARN] Could not save image '{img.name}': {e}")

    # 导出 FBX
    os.makedirs(os.path.dirname(os.path.abspath(resolved_path)), exist_ok=True)

    if target == "unity":
        axis_forward, axis_up = "-Z", "Y"
    else:
        axis_forward, axis_up = "-Y", "Z"

    print(f"    Exporting rigged FBX (embedded textures) → {resolved_path}")

    bpy.ops.export_scene.fbx(
        filepath=resolved_path,
        use_selection=True,
        path_mode="COPY",
        embed_textures=True,
        use_mesh_modifiers=True,
        mesh_smooth_type="FACE",
        use_custom_props=True,
        add_leaf_bones=False,
        axis_forward=axis_forward,
        axis_up=axis_up,
    )

    # 恢复：只需恢复对象变换（骨骼和顶点数据未被修改）
    if move_to_origin:
        if verbose:
            print("    Restoring Blender scene (programmatic, no undo)...")
        _restore_object_transforms(saved_transforms, verbose=verbose)

    n_verts = sum(len(m.data.vertices) for m in meshes)
    n_faces = sum(len(m.data.polygons) for m in meshes)

    result = {
        "format": "fbx",
        "path": resolved_path,
        "armature": arm.name,
        "meshes": [m.name for m in meshes],
        "n_verts": n_verts,
        "n_faces": n_faces,
    }

    print(f"\n[DONE] Rigged export complete:")
    print(f"    Format:   {result['format'].upper()}")
    print(f"    Path:     {result['path']}")
    print(f"    Armature: {result['armature']}")
    print(f"    Meshes:   {result['meshes']}")
    print(f"    Verts:    {result['n_verts']}, Faces: {result['n_faces']}")

    return result


def batch_export_rigged(
    output_dir,
    name_mesh_map=None,
    target="unity",
    move_to_origin=True,
    verbose=True,
):
    """
    批量导出场景中所有带骨骼的角色模型。

    自动发现场景中的 ARMATURE 对象及其子级 MESH，对每个角色：
    1. 将角色居中到原点（move_to_origin=True 时）
    2. 导出为 FBX（内嵌纹理 + 骨架）
    3. 恢复 Blender 场景原始状态

    Parameters
    ----------
    output_dir : str
        输出根目录。每个角色导出到 {output_dir}/{armature_name}/{armature_name}_Low.fbx。
    name_mesh_map : dict or None
        手动指定骨架名→mesh名列表的映射。None 则自动发现。
        用于覆盖自动发现的 mesh 名称（如 Zombie2_Low_Mesh 等）。
        注意：键中包含 "." 的骨架名（如 "FemaleHighSchooler1.001"）会被跳过，
        除非在此映射中显式指定。
    target : str
        目标平台："unity" 或 "blender"。
    move_to_origin : bool
        是否将角色居中到原点后导出。
    verbose : bool

    Returns
    -------
    list[dict] : 每个角色的导出结果摘要
    """
    import bpy

    os.makedirs(output_dir, exist_ok=True)

    # 发现所有骨架
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
    if not armatures:
        print("[WARN] 场景中没有 ARMATURE 对象")
        return []

    results = []
    print(f"\nBATCH RIGGED EXPORT: {len(armatures)} armature(s) → {output_dir}")

    for arm in armatures:
        arm_name = arm.name

        # 跳过 Blender 自动生成的重复对象（如 "Name.001"）
        if "." in arm_name and (name_mesh_map is None or arm_name not in name_mesh_map):
            if verbose:
                print(f"    [SKIP] {arm_name}: likely a Blender duplicate (contains '.')")
            continue

        # 确定 mesh 列表
        if name_mesh_map and arm_name in name_mesh_map:
            mesh_names = name_mesh_map[arm_name]
        else:
            mesh_names = None  # 自动发现

        out_subdir = os.path.join(output_dir, arm_name)
        out_path = os.path.join(out_subdir, f"{arm_name}_Low.fbx")

        try:
            result = export_rigged_model(
                armature_name=arm_name,
                output_path=out_path,
                mesh_names=mesh_names,
                target=target,
                move_to_origin=move_to_origin,
                verbose=verbose,
            )
            results.append(result)
        except Exception as e:
            print(f"    [ERROR] Export failed for '{arm_name}': {e}")
            results.append({"armature": arm_name, "error": str(e)})

    # 摘要
    n_ok = sum(1 for r in results if "error" not in r)
    print(f"\nBATCH RIGGED EXPORT SUMMARY: {n_ok}/{len(results)} succeeded")
    for r in results:
        if "error" in r:
            print(f"  [ERR] {r['armature']}: {r['error']}")
        else:
            print(f"  [OK ] {r['armature']} → {r['path']}")

    return results


# ============================================================
# CLI 入口
# ============================================================
def main():
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1:]
    else:
        argv = sys.argv[1:]

    import argparse
    p = argparse.ArgumentParser(description="模型导出脚本（FBX 内嵌纹理 / OBJ 纯几何）")
    p.add_argument("objects", nargs="+", help="要导出的对象名称")
    p.add_argument("-o", "--output", required=True, help="输出文件路径")
    p.add_argument("-n", "--name", default="", help="自定义导出文件名（不含扩展名，覆盖路径中的文件名）")
    p.add_argument("-f", "--format", default="auto",
                   choices=["fbx", "obj", "auto"],
                   help="导出格式（默认 auto：有纹理→FBX，无纹理→OBJ）")
    p.add_argument("-t", "--target", default="unity",
                   choices=["unity", "blender"],
                   help="目标平台：unity=Y-up+轴转换，blender=Z-up+原生坐标（默认 unity）")
    p.add_argument("--no-apply-transforms", action="store_true",
                   help="导出前不应用变换")
    args = p.parse_args(argv)

    export_model(
        obj_names=args.objects,
        output_path=args.output,
        fmt=args.format,
        output_name=args.name,
        target=args.target,
        apply_transforms=not args.no_apply_transforms,
    )


if __name__ == "__main__":
    main()
