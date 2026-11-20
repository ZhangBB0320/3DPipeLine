#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 烘焙封装：平直着色 → 小边距 Smart UV → Cycles 烘焙（无 Shrinkwrap、cage=0.02）。

底层仍复用 bake_scene.py 的材质节点、EMIT Albedo / Normal 烘焙、贴图隔离与
Cycles 配置；本模块只固化 V2 参数，避免旧版 Shrinkwrap / cage / UV 边距回流。
"""
from __future__ import annotations

import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (_HERE, os.path.join(_HERE, "decimation")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

DEFAULT_CAGE_V2 = 0.02
DEFAULT_UV_MARGIN_V2 = 0.003
DEFAULT_UV_ANGLE_V2 = 66.0


def set_flat_shading(obj):
    me = obj.data
    me.polygons.foreach_set("use_smooth", [False] * len(me.polygons))
    me.update()


def smart_uv(obj, angle_deg=DEFAULT_UV_ANGLE_V2, margin=DEFAULT_UV_MARGIN_V2):
    import bpy
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.hide_set(False)
    obj.hide_viewport = False
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    me = obj.data
    while me.uv_layers:
        me.uv_layers.remove(me.uv_layers[0])
    me.uv_layers.new(name="UVMap")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(angle_deg), island_margin=margin)
    bpy.ops.object.mode_set(mode="OBJECT")


def bake_v2(high_obj_name, low_obj_name, name="bake", res=4096, samples=64, device="GPU",
                  cage=DEFAULT_CAGE_V2, uv_margin=DEFAULT_UV_MARGIN_V2,
                  uv_angle=DEFAULT_UV_ANGLE_V2, max_ray_dist=0.0):
    import bpy
    from bake_scene import bake_scene
    low = bpy.data.objects[low_obj_name]
    set_flat_shading(low)
    smart_uv(low, uv_angle, uv_margin)
    return bake_scene(
        high_obj_name=high_obj_name, low_obj_name=low_obj_name, name=name,
        cage=cage, res=res, samples=samples, device=device,
        uv_margin=uv_margin, skip_uv=True, skip_shrinkwrap=True,
        max_ray_dist=max_ray_dist,
    )
