#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
烘焙后"渲染对比"检查 —— 发现几何指标看不到的问题。

几何指标（evaluate_quality）只回答"形状像不像"。2026-11-20 的事故里还有另一类缺陷：
  · Style1：几何完全正确，但烘出来的贴图窗内扭曲、顶部发黑（光线打到窗洞内壁 / 内部家具）
  · Style5：几何撕碎 + 贴图乱码
这些最终都表现在【画面】上。所以这里做最直接的事：高模和低模用同一机位、同一渲染设置
各渲 4 个方向，逐像素比对。人眼能看出来的差异，这里就能量化出来。

两个 pass：
  tex    FLAT 光照 + 贴图色   → 只比较颜色（albedo 烘焙质量、UV、窗内内容）
  shape  STUDIO 光照 + 纯灰    → 只比较几何明暗（形状 / 皱褶 / 凹凸）
都在 Workbench 下渲染（不受 GPU 烘焙环境影响，1.8M 面高模单帧约 1~2 秒）。

产物：<out>.compare.png 联系表（每个机位 高模|低模），给美术一眼确认用。
"""
from __future__ import annotations

import math
import os
import shutil
import tempfile

import numpy as np

AZIMUTHS = (-35.0, 55.0, 145.0, 235.0)
ELEVATION = 14.0
RES = (420, 560)

# 阈值（2026-11-20 用 7 个真实模型校准，见 KB/quality_metrics.md）
THRESH = {
    # 实测分布（7 个真实模型）：好模型 贴图均差 ≤0.023 / p95 ≤0.072 / 错误像素 ≤1.1%；
    #   nobu5(撕碎) 0.074 / 0.49 / 6.1%；school3(塌陷) 0.29 / 1.00 / 29.6%。阈值夹在两群之间。
    "tex_warn": 0.05, "tex_fail": 0.12,
    "tex_p95_warn": 0.15, "tex_p95_fail": 0.35,
    "tex_bad_warn": 0.03, "tex_bad_fail": 0.08,
    # 明暗差好模型本身就有 ~0.08 的底噪（高模是噪声曲面、低模是平面），只做兜底
    "shape_warn": 0.15, "shape_fail": 0.22,
}


def _setup(scene):
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x, scene.render.resolution_y = RES
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    sh = scene.display.shading
    sh.show_cavity = False
    sh.show_shadows = False
    sh.show_object_outline = False
    # Workbench 默认带抗锯齿，保持默认即可


def _camera(scene, high_obj, az, el):
    import bpy
    from mathutils import Vector
    bb = [high_obj.matrix_world @ Vector(c) for c in high_obj.bound_box]
    ctr = sum(bb, Vector()) / 8
    size = max(max(v[i] for v in bb) - min(v[i] for v in bb) for i in range(3))
    cam = bpy.data.objects.get("__cmp_cam")
    if cam is None:
        cam = bpy.data.objects.new("__cmp_cam", bpy.data.cameras.new("__cmp_cam"))
        scene.collection.objects.link(cam)
    cam.data.lens = 50
    a, e, d = math.radians(az), math.radians(el), size * 2.1
    cam.location = ctr + Vector((d * math.cos(e) * math.sin(a),
                                 -d * math.cos(e) * math.cos(a),
                                 d * math.sin(e)))
    cam.rotation_euler = (ctr - cam.location).normalized().to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam


def _render_np(scene, path):
    import bpy
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path)
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)


def _down(a, k=4):
    """块平均下采样，容忍贴图 texel 级差异。"""
    h, w = a.shape[0] // k * k, a.shape[1] // k * k
    return a[:h, :w].reshape(h // k, k, w // k, k, -1).mean(axis=(1, 3))


def render_compare(high_name, low_name, out_png=None, work_dir=None):
    import bpy
    # ★ 每次调用独占一个临时目录：批量流水线可能并行多个 Blender 进程，
    #   共用固定目录会互相覆盖渲染图 → 比对到别人的图，得出看似合理的错误分数（实测踩过）。
    own_dir = work_dir is None
    work_dir = work_dir or tempfile.mkdtemp(prefix="cmp_")
    os.makedirs(work_dir, exist_ok=True)
    scene = bpy.context.scene
    high = bpy.data.objects[high_name]
    low = bpy.data.objects[low_name]

    old = {
        "engine": scene.render.engine, "res": (scene.render.resolution_x, scene.render.resolution_y),
        "ft": scene.render.film_transparent, "cam": scene.camera,
        "light": scene.display.shading.light, "ct": scene.display.shading.color_type,
        "sc": tuple(scene.display.shading.single_color),
        "hide": {o.name: (o.hide_render, o.hide_viewport) for o in scene.objects},
    }
    # 只渲高/低模：其它对象（别的低模备份等）全藏
    for o in scene.objects:
        if o.name not in (high.name, low.name):
            o.hide_render = True
    high.hide_viewport = False
    low.hide_viewport = False
    _setup(scene)

    tex_err, shape_err, rows, tex_loc = [], [], [], []
    for vi, az in enumerate(AZIMUTHS):
        _camera(scene, high, az, ELEVATION)
        pair = {}
        for which, mode in (("tex", ("FLAT", "TEXTURE")), ("shape", ("STUDIO", "SINGLE"))):
            scene.display.shading.light, scene.display.shading.color_type = mode
            if mode[1] == "SINGLE":
                scene.display.shading.single_color = (0.72, 0.72, 0.75)
            for tag, hide_h, hide_l in (("H", False, True), ("L", True, False)):
                high.hide_render, low.hide_render = hide_h, hide_l
                pair[(which, tag)] = _render_np(scene, f"{work_dir}/v{vi}_{which}_{tag}.png")

        for which, bucket in (("tex", tex_err), ("shape", shape_err)):
            H, L = pair[(which, "H")], pair[(which, "L")]
            Hd, Ld = _down(H), _down(L)
            # 掩码 = 任一方有像素（alpha）；背景透明区不参与，但"一方有一方无"算最大误差，
            # 这样轮廓走样 / 缺块会直接体现在分数里
            mH, mL = Hd[..., 3] > 0.5, Ld[..., 3] > 0.5
            union = mH | mL
            if not union.any():
                bucket.append(0.0)
                continue
            diff = np.abs(Hd[..., :3] - Ld[..., :3]).mean(axis=-1)
            diff = np.where(mH & mL, diff, 1.0)          # 轮廓不一致处 = 满误差
            d = diff[union]
            bucket.append(float(d.mean()))
            # ★ 平均值会把"窗内扭曲 / 顶部发黑"这类【局部缺陷】稀释掉（实测 Style1 平均误差
            #   与好模型无异）。所以另记：p95（最差 5% 像素有多糟）与"明显错误像素占比"。
            if which == "tex":
                tex_loc.append((float(np.percentile(d, 95)), float((d > 0.25).mean())))
        rows.append(np.concatenate([pair[("tex", "H")][..., :3], pair[("tex", "L")][..., :3],
                                    pair[("shape", "H")][..., :3], pair[("shape", "L")][..., :3]], axis=1))

    # 还原场景状态
    scene.render.engine = old["engine"]
    scene.render.resolution_x, scene.render.resolution_y = old["res"]
    scene.render.film_transparent = old["ft"]
    scene.camera = old["cam"]
    scene.display.shading.light = old["light"]
    scene.display.shading.color_type = old["ct"]
    scene.display.shading.single_color = old["sc"]
    for o in scene.objects:
        if o.name in old["hide"]:
            o.hide_render, o.hide_viewport = old["hide"][o.name]

    if out_png:
        sheet = np.concatenate(rows, axis=0)                # (4*H, 4*W, 3)
        # 联系表缩小一半，避免 4 图 × 4 机位过大
        sheet = sheet[::2, ::2]
        img = bpy.data.images.new("__cmp_sheet", sheet.shape[1], sheet.shape[0], alpha=False)
        rgba = np.concatenate([sheet, np.ones(sheet.shape[:2] + (1,), np.float32)], axis=-1)
        img.pixels.foreach_set(rgba.ravel())
        img.filepath_raw = out_png
        img.file_format = "PNG"
        img.save()
        bpy.data.images.remove(img)

    if own_dir:
        shutil.rmtree(work_dir, ignore_errors=True)

    t = float(np.mean(tex_err)); s = float(np.mean(shape_err))
    tw, sw = float(np.max(tex_err)), float(np.max(shape_err))
    tex_p95 = float(np.mean([x[0] for x in tex_loc]))
    tex_bad = float(np.mean([x[1] for x in tex_loc]))
    T = THRESH
    reasons, level = [], 0
    for name, val, w, f in (("贴图颜色(整体)", t, T["tex_warn"], T["tex_fail"]),
                            ("贴图局部缺陷(最差5%像素)", tex_p95, T["tex_p95_warn"], T["tex_p95_fail"]),
                            ("明显错误像素占比", tex_bad, T["tex_bad_warn"], T["tex_bad_fail"]),
                            ("几何明暗", s, T["shape_warn"], T["shape_fail"])):
        if val >= f:
            reasons.append(f"[FAIL] 渲染对比·{name}差异 {val:.3f}（≥{f}）：与高模肉眼可见不一致")
            level = max(level, 2)
        elif val >= w:
            reasons.append(f"[WARN] 渲染对比·{name}差异 {val:.3f}（≥{w}）")
            level = max(level, 1)
    verdict = ("PASS", "WARN", "FAIL")[level]
    return {"verdict": verdict, "tex_err": round(t, 4), "shape_err": round(s, 4),
            "tex_p95": round(tex_p95, 4), "tex_bad_frac": round(tex_bad, 4),
            "tex_err_worst_view": round(tw, 4), "shape_err_worst_view": round(sw, 4),
            "per_view_tex": [round(x, 4) for x in tex_err],
            "per_view_shape": [round(x, 4) for x in shape_err],
            "reasons": reasons, "sheet": out_png}
