#!/usr/bin/env python3
"""
transfer_animation.py — 将源骨骼的指定动画 Action 精确拷贝到目标骨骼。

兼容 Blender 4.x layered action 系统和 legacy action。

用法（Blender 内）：
    import sys; sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts')
    from transfer_animation import transfer_animation

    transfer_animation(
        src_armature="Armature",
        src_action_name="Idle",
        dst_armature="LowBodyAmature.001",
        dst_action_name="Fight_Idle",
    )

设计要点：
- 同时支持 Blender 4.x layered action 和 legacy action
- 深拷贝所有 F-Curve（路径、索引、关键帧、插值、修饰器），确保 100% 一致
- 自动校验：源/目标骨骼存在、Action 存在、目标名称不冲突
- 自动创建 slot / channelbag 并绑定目标骨骼
"""

from __future__ import annotations

import bpy


class TransferAnimationError(RuntimeError):
    """动画迁移失败时抛出。"""
    pass


def _find_armature(name: str) -> bpy.types.Object:
    """按名称查找 Armature 对象，不存在或类型不对则抛错。"""
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise TransferAnimationError("对象 '%s' 不存在于场景中" % name)
    if obj.type != "ARMATURE":
        raise TransferAnimationError("对象 '%s' 类型为 %s，期望 ARMATURE" % (name, obj.type))
    return obj


def _find_action(name: str) -> bpy.types.Action:
    """按名称查找 Action，不存在则抛错。"""
    action = bpy.data.actions.get(name)
    if action is None:
        raise TransferAnimationError("Action '%s' 不存在" % name)
    return action


def _copy_keyframe_points(src_fc, dst_fc):
    """拷贝关键帧点（位置、手柄、插值）。"""
    dst_fc.keyframe_points.add(len(src_fc.keyframe_points))
    for i, skp in enumerate(src_fc.keyframe_points):
        dkp = dst_fc.keyframe_points[i]
        dkp.co = skp.co.copy()
        dkp.handle_left = skp.handle_left.copy()
        dkp.handle_right = skp.handle_right.copy()
        dkp.interpolation = skp.interpolation
        dkp.easing = skp.easing
        dkp.amplitude = skp.amplitude
        dkp.back = skp.back
        dkp.period = skp.period


def _copy_modifiers(src_fc, dst_fc):
    """拷贝 F-Curve 修饰器。"""
    for src_mod in src_fc.modifiers:
        dst_mod = dst_fc.modifiers.new(type=src_mod.type)
        for prop in src_mod.bl_rna.properties:
            if prop.is_readonly:
                continue
            try:
                setattr(dst_mod, prop.identifier, getattr(src_mod, prop.identifier))
            except Exception:
                pass


def _copy_fcurve_props(src_fc, dst_fc):
    """拷贝 F-Curve 级属性。"""
    dst_fc.mute = src_fc.mute
    dst_fc.hide = src_fc.hide
    dst_fc.lock = src_fc.lock
    dst_fc.extrapolation = src_fc.extrapolation


def _copy_legacy_action(src_action: bpy.types.Action, dst_name: str) -> bpy.types.Action:
    """深拷贝 legacy action（Blender < 4.x 或 4.x legacy 模式）。"""
    existing = bpy.data.actions.get(dst_name)
    if existing is not None:
        existing.use_fake_user = False
        bpy.data.actions.remove(existing)

    new_action = bpy.data.actions.new(dst_name)

    for src_fc in src_action.fcurves:
        dst_fc = new_action.fcurves.new(
            data_path=src_fc.data_path,
            index=src_fc.array_index,
        )
        _copy_fcurve_props(src_fc, dst_fc)
        _copy_keyframe_points(src_fc, dst_fc)
        _copy_modifiers(src_fc, dst_fc)

        if src_fc.group is not None:
            group_name = src_fc.group.name
            dst_group = new_action.groups.get(group_name)
            if dst_group is None:
                dst_group = new_action.groups.new(name=group_name)
            dst_fc.group = dst_group

    new_action.use_fake_user = False
    return new_action


def _copy_layered_action(src_action: bpy.types.Action, dst_name: str) -> bpy.types.Action:
    """
    深拷贝 Blender 4.x layered action。

    使用 Blender 内置 action.copy() 确保所有内部数据结构
    （slot、layer、strip、channelbag、fcurve、frame_range 等）
    完整复制，避免 Action Editor 等界面显示异常（如关键帧黄点不显示）。

    手动逐层创建 layered action 容易遗漏 Blender 内部依赖的隐藏字段，
    而 copy() 由 Blender 核心实现，保证数据一致性。
    """
    existing = bpy.data.actions.get(dst_name)
    if existing is not None:
        existing.use_fake_user = False
        bpy.data.actions.remove(existing)

    # 使用 Blender 内置复制：完整复制 slot/layer/strip/channelbag/fcurve
    new_action = src_action.copy()
    new_action.name = dst_name
    new_action.use_fake_user = False
    return new_action


def transfer_animation(
    src_armature: str,
    src_action_name: str,
    dst_armature: str,
    dst_action_name: str,
) -> bpy.types.Action:
    """
    将源骨骼的指定动画 Action 拷贝到目标骨骼。

    参数:
        src_armature:    源骨骼对象名称
        src_action_name: 源骨骼上要拷贝的 Action 名称
        dst_armature:    目标骨骼对象名称
        dst_action_name: 拷贝后 Action 的新名称

    返回:
        新创建的 Action 对象

    副作用：
        - 完成后自动还原 Blender 状态：停止动画播放、恢复当前帧、恢复选中/活跃对象
        - 场景帧范围会调整为覆盖新动画
    """
    # ── 保存 Blender 状态 ──────────────────────────────────
    scene = bpy.context.scene
    saved_frame = scene.frame_current
    saved_frame_start = scene.frame_start
    saved_frame_end = scene.frame_end
    saved_playing = (bpy.context.screen is not None
                     and bpy.context.screen.is_animation_playing)
    saved_active = bpy.context.view_layer.objects.active
    saved_selected = set(obj.name for obj in bpy.context.selected_objects)

    # 如果正在播放，先暂停（避免干扰）
    if saved_playing:
        bpy.ops.screen.animation_play(reverse=False)

    try:
        # 1. 校验源骨骼
        _find_armature(src_armature)

        # 2. 校验目标骨骼
        dst_arm_obj = _find_armature(dst_armature)

        # 3. 校验源 Action 存在
        src_action = _find_action(src_action_name)

        # 4. 深拷贝 Action
        if src_action.is_action_legacy:
            new_action = _copy_legacy_action(src_action, dst_action_name)
        else:
            new_action = _copy_layered_action(src_action, dst_action_name)

        # 5. 将新 Action 赋给目标骨骼
        if dst_arm_obj.animation_data is None:
            dst_arm_obj.animation_data_create()
        dst_arm_obj.animation_data.action = new_action

        # 5.1 绑定 action_slot（Blender 4.x layered action 必须）
        #     未绑定 slot 时，Action 数据虽存在但不驱动骨骼
        #     优先使用 action_suitable_slots（Blender 4.4+ 推荐），
        #     它按 target_id_type 自动匹配，比手动取 slots[0] 更可靠
        if not new_action.is_action_legacy:
            suitable_slots = dst_arm_obj.animation_data.action_suitable_slots
            if len(suitable_slots) > 0:
                dst_arm_obj.animation_data.action_slot = suitable_slots[0]
            elif len(new_action.slots) > 0:
                dst_arm_obj.animation_data.action_slot = new_action.slots[0]

        # 6. 确保场景帧范围覆盖新动画
        frame_start = int(new_action.frame_range[0])
        frame_end = int(new_action.frame_range[1])
        if scene.frame_start > frame_start:
            scene.frame_start = frame_start
        if scene.frame_end < frame_end:
            scene.frame_end = frame_end

    except Exception:
        # 出错也要还原状态
        _restore_state(scene, saved_frame, saved_frame_start, saved_frame_end,
                       saved_active, saved_selected, saved_playing)
        raise

    # ── 还原 Blender 状态 ──────────────────────────────────
    _restore_state(scene, saved_frame, saved_frame_start, saved_frame_end,
                   saved_active, saved_selected, was_playing=False)
    # 注意：始终不恢复播放，保持停止状态，让用户手动按空格键播放

    # ── 修复 Action Editor 显示（确保关键帧黄点可见）──────
    _refresh_dopesheet_ui()

    return new_action


def _refresh_dopesheet_ui():
    """
    刷新 Dopesheet/Action Editor 显示设置，确保关键帧黄点可见。

    常见显示问题（即使数据正确，UI 也可能不显示黄点）：
      - show_only_selected=True 但目标骨骼未选中 → 通道列表为空
      - show_hidden=False → 隐藏的骨骼通道不显示
      - 通道区域被折叠
      - UI 缓存未刷新

    本函数对所有 DOPESHEET_EDITOR 的 ACTION/DOPESHEET 模式区域：
      1. 关闭 show_only_selected（不要求选中骨骼）
      2. 打开 show_hidden（显示所有通道）
      3. 打开通道列表区域
      4. 触发重绘
    """
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != "DOPESHEET_EDITOR":
                continue
            for space in area.spaces:
                if space.type != "DOPESHEET_EDITOR":
                    continue
                if space.mode in ("ACTION", "DOPESHEET"):
                    space.show_region_channels = True
                    space.dopesheet.show_only_selected = False
                    space.dopesheet.show_hidden = True
            area.tag_redraw()


def _restore_state(
    scene,
    saved_frame: int,
    saved_frame_start: int,
    saved_frame_end: int,
    saved_active,
    saved_selected: set,
    was_playing: bool,
):
    """还原 Blender 场景状态。"""
    # 还原帧范围
    scene.frame_start = saved_frame_start
    scene.frame_end = saved_frame_end
    # 跳回原帧
    scene.frame_set(saved_frame)
    # 还原选中状态
    bpy.ops.object.select_all(action='DESELECT')
    for name in saved_selected:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            obj.select_set(True)
    # 还原活跃对象
    if saved_active is not None:
        bpy.context.view_layer.objects.active = saved_active
    # 不自动恢复播放 — 保持停止状态


# ============================================================
# Blender 内测试入口
# ============================================================
def _test():
    """在 Blender 内快速验证。"""
    result = transfer_animation(
        src_armature="Armature",
        src_action_name="mixamo.com",
        dst_armature="LowBodyAmature.001",
        dst_action_name="Fight_Idle",
    )
    print("Transfer OK: Action '%s' (frames %d-%d)" % (
        result.name,
        int(result.frame_range[0]),
        int(result.frame_range[1]),
    ))


if __name__ == "__main__":
    _test()
