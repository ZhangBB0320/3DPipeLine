#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 减面策略：体素重构 → QEM COLLAPSE → 安全修复。

只保留 V2 需要的两个策略：
    strat_voxel   体素重构（默认 div=700）+ 分级 COLLAPSE，主策略
    strat_baseline 原网格 COLLAPSE + 安全修复，兜底策略
所有策略都不碰高模；低模以 f"{name}_Low" 命名并留在场景里。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import bmesh
import bpy
import numpy as np


def _select_only(obj):
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.hide_set(False)
    obj.hide_viewport = False
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def _clone(obj, name):
    c = obj.copy()
    c.data = obj.data.copy()
    c.name = name
    bpy.context.collection.objects.link(c)
    return c


def _drop(obj):
    if obj is None:
        return
    me = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if me is not None and me.users == 0:
        bpy.data.meshes.remove(me)


def _bbox_diag(obj):
    co = np.empty(len(obj.data.vertices) * 3, np.float32)
    obj.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    return float(np.linalg.norm(co.max(0) - co.min(0)))


def _collapse_once(obj, ratio):
    _select_only(obj)
    m = obj.modifiers.new("Dec", "DECIMATE")
    m.decimate_type = "COLLAPSE"
    m.ratio = max(min(ratio, 1.0), 1e-6)
    bpy.ops.object.modifier_apply(modifier=m.name)
    return len(obj.data.polygons)


def _collapse_to_target(src_obj, target, name, tolerance=0.03, max_attempts=14):
    """二分调 COLLAPSE ratio，把面数逼近 target（V2 默认 ±3%）。"""
    n_src = len(src_obj.data.polygons)
    target = max(min(target, n_src), 4)
    lo, hi = 4.0 / n_src, 1.0
    ratio = target / n_src
    last, same, best = None, 0, None
    for _ in range(max_attempts):
        if best is not None:
            _drop(best)
            best = None
        cand = _clone(src_obj, f"{name}_Low_tmp")
        faces = _collapse_once(cand, ratio)
        if abs(faces - target) / target <= tolerance:
            cand.name = f"{name}_Low"
            return cand, faces, False
        if faces == last:
            same += 1
            if same >= 2:
                cand.name = f"{name}_Low"
                return cand, faces, True
        else:
            same = 0
        last = faces
        if faces > target:
            hi = ratio
        else:
            lo = ratio
        best = cand
        ratio = (lo + hi) / 2.0
    best.name = f"{name}_Low"
    return best, len(best.data.polygons), True


def repair_safe(obj, merge_distance=1e-4, min_shell_faces=5):
    """不凿洞的安全修复：合并重叠点 / 溶解退化面 / 删微小游离壳 / 重算法线。"""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=merge_distance)
    bmesh.ops.dissolve_degenerate(bm, dist=1e-7, edges=bm.edges)

    bm.faces.ensure_lookup_table()
    seen, shells = set(), []
    for f in bm.faces:
        if f.index in seen:
            continue
        seen.add(f.index)
        stack, comp = [f], []
        while stack:
            c = stack.pop()
            comp.append(c)
            for e in c.edges:
                for g in e.link_faces:
                    if g.index not in seen:
                        seen.add(g.index)
                        stack.append(g)
        shells.append(comp)
    kill = [f for comp in shells if len(comp) < min_shell_faces for f in comp]
    if kill:
        bmesh.ops.delete(bm, geom=kill, context="FACES")
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return len(obj.data.polygons)


def make_voxel_proxy(high, name, voxel_div=700):
    """Remesh(VOXEL) 重建干净连通表面；体素尺寸 = 包围盒对角线 / voxel_div。"""
    proxy = _clone(high, f"{name}_vox")
    diag = _bbox_diag(proxy)
    _select_only(proxy)
    m = proxy.modifiers.new("Vox", "REMESH")
    m.mode = "VOXEL"
    m.voxel_size = diag / float(voxel_div)
    m.adaptivity = 0.0
    m.use_smooth_shade = False
    bpy.ops.object.modifier_apply(modifier=m.name)
    return proxy, {"voxel_size": round(diag / voxel_div, 5),
                   "proxy_faces": len(proxy.data.polygons)}


def strat_baseline(high, target, name, tolerance=0.03):
    """兜底：原高模 COLLAPSE + 安全修复。"""
    low, faces, stag = _collapse_to_target(high, target, name, tolerance=tolerance)
    f2 = repair_safe(low)
    return low, {"strategy": "baseline", "collapse_faces": faces,
                 "stagnated": stag, "final_faces": f2}


def strat_voxel(high, target, name, voxel_div=700, tolerance=0.03):
    """主策略：体素重构 → 预减面 → 精确 COLLAPSE → 安全修复。"""
    proxy, pinfo = make_voxel_proxy(high, name, voxel_div=voxel_div)
    n = len(proxy.data.polygons)
    if n > target * 40:
        _collapse_once(proxy, (target * 12) / n)
    low, faces, stag = _collapse_to_target(proxy, target, name, tolerance=tolerance)
    _drop(proxy)
    f2 = repair_safe(low)
    info = {"strategy": f"voxel{voxel_div}", "collapse_faces": faces,
            "stagnated": stag, "final_faces": f2}
    info.update(pinfo)
    return low, info
