#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
光照对比（EEVEE，含法线贴图）—— 比 render_compare 的 Workbench 更接近"在 Blender / Unity 里实际看到的样子"。

为什么需要：
  render_compare 用 Workbench（FLAT / STUDIO），**看不到法线贴图**。但低模的全部"凹凸细节"恰恰靠法线贴图
  承载；法线烘错了（撕裂 / 漏光 / 色块），Workbench 对比一概看不出（V1 的法线贴图大片饱和色块就是例证）。
  远景也看不出 UV 密度/纹理清晰度差异 → 额外加了【近景机位】。

做法：高模与低模用【同一套简化材质】（底色贴图 + 固定粗糙度 0.8 / 金属度 0；低模保留法线贴图）、
      同一盏随相机的太阳光 + 环境光，渲 6 个机位（4 远景 + 2 近景）× 2 个 pass：
        tex   ：底色 × 光照   → 颜色 + 凹凸一起评价
        shape ：纯灰 × 光照   → 只看几何 + 法线贴图（隔离颜色，专门暴露法线烘焙缺陷）
      高模渲染一次后缓存为 npz，同一模型的多个变体共用，只需渲低模（几千面，很快）。

  ★ 高模 OBJ 导入时没有接法线贴图（只接底色/金属度/粗糙度），所以高模=纯几何 + 底色，是公平基准。
"""
from __future__ import annotations

import math
import os
import tempfile

import numpy as np

import render_compare as RC

ROUGH, METAL = 0.8, 0.0
GREY = (0.72, 0.72, 0.72, 1.0)

# (名称, 方位角, 仰角, 距离系数, 分辨率)
VIEWS = [
    ("far0", -35.0, 14.0, 2.1, (420, 560)),
    ("far1", 55.0, 14.0, 2.1, (420, 560)),
    ("far2", 145.0, 14.0, 2.1, (420, 560)),
    ("far3", 235.0, 14.0, 2.1, (420, 560)),
    ("near0", -25.0, 8.0, 0.72, (560, 560)),
    ("near1", 150.0, 8.0, 0.72, (560, 560)),
]


# ───────────────────────── 场景 / 光照 / 相机 ─────────────────────────

def _prep(scene):
    import bpy
    scene.render.engine = "BLENDER_EEVEE"
    try:
        scene.eevee.taa_render_samples = 16
    except Exception:
        pass
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    w = scene.world or bpy.data.worlds.new("__lit_world")
    scene.world = w
    w.use_nodes = True
    bg = w.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.55, 0.58, 0.66, 1.0)
        bg.inputs[1].default_value = 0.8
    sun = bpy.data.objects.get("__lit_sun")
    if sun is None:
        sun = bpy.data.objects.new("__lit_sun", bpy.data.lights.new("__lit_sun", "SUN"))
        scene.collection.objects.link(sun)
    sun.data.energy = 3.2
    sun.data.angle = math.radians(8)
    return sun


def _camera(scene, anchor, az, el, dist_f):
    import bpy
    from mathutils import Vector
    bb = [anchor.matrix_world @ Vector(c) for c in anchor.bound_box]
    ctr = sum(bb, Vector()) / 8
    size = max(max(v[i] for v in bb) - min(v[i] for v in bb) for i in range(3))
    cam = bpy.data.objects.get("__lit_cam")
    if cam is None:
        cam = bpy.data.objects.new("__lit_cam", bpy.data.cameras.new("__lit_cam"))
        scene.collection.objects.link(cam)
    cam.data.lens = 50
    cam.data.clip_start = 0.001
    cam.data.clip_end = 100
    a, e, d = math.radians(az), math.radians(el), size * dist_f
    cam.location = ctr + Vector((d * math.cos(e) * math.sin(a), -d * math.cos(e) * math.cos(a), d * math.sin(e)))
    cam.rotation_euler = (ctr - cam.location).normalized().to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam
    return cam


def _place_sun(sun, cam):
    """太阳光随相机：相机坐标系里固定朝向（左上前方），所有机位光照一致。"""
    bpy_mat = cam.matrix_world
    sun.parent = cam
    sun.matrix_parent_inverse = bpy_mat.inverted()
    sun.rotation_euler = (math.radians(48), 0.0, math.radians(-28))


# ───────────────────────── 材质（可逆地替换） ─────────────────────────

def _principled(mat):
    return next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)


def _base_image(obj):
    for s in obj.material_slots:
        m = s.material
        if m is None or not m.use_nodes:
            continue
        b = _principled(m)
        if b and b.inputs["Base Color"].is_linked:
            n = b.inputs["Base Color"].links[0].from_node
            if n.type == "TEX_IMAGE" and n.image:
                return n.image
    return None


class _HighMats:
    def __init__(self, high):
        import bpy
        self.high = high
        self.orig = [s.material for s in high.material_slots]
        img = _base_image(high)
        self.mats = {}
        for kind in ("tex", "shape"):
            m = bpy.data.materials.new(f"__lit_high_{kind}")
            m.use_nodes = True
            nt = m.node_tree
            b = _principled(m)
            b.inputs["Roughness"].default_value = ROUGH
            b.inputs["Metallic"].default_value = METAL
            if kind == "tex" and img is not None:
                t = nt.nodes.new("ShaderNodeTexImage")
                t.image = img
                nt.links.new(t.outputs["Color"], b.inputs["Base Color"])
            else:
                b.inputs["Base Color"].default_value = GREY
            self.mats[kind] = m

    def use(self, kind):
        for s in self.high.material_slots:
            s.material = self.mats[kind]

    def restore(self):
        import bpy
        for s, m in zip(self.high.material_slots, self.orig):
            s.material = m
        for m in self.mats.values():
            bpy.data.materials.remove(m)


class _LowMats:
    """低模：统一粗糙度/金属度；shape pass 把底色换成纯灰（保留法线贴图）。"""

    def __init__(self, low):
        self.low = low
        self.saved = []
        for s in low.material_slots:
            m = s.material
            if m is None or not m.use_nodes:
                continue
            b = _principled(m)
            if b is None:
                continue
            link = b.inputs["Base Color"].links[0] if b.inputs["Base Color"].is_linked else None
            self.saved.append((m, b, b.inputs["Roughness"].default_value, b.inputs["Metallic"].default_value,
                               tuple(b.inputs["Base Color"].default_value),
                               link.from_socket if link else None))
            b.inputs["Roughness"].default_value = ROUGH
            b.inputs["Metallic"].default_value = METAL

    def use(self, kind):
        for m, b, _r, _mt, _bc, src in self.saved:
            inp = b.inputs["Base Color"]
            for l in list(inp.links):
                m.node_tree.links.remove(l)
            if kind == "tex" and src is not None:
                m.node_tree.links.new(src, inp)
            else:
                inp.default_value = GREY

    def restore(self):
        for m, b, r, mt, bc, src in self.saved:
            inp = b.inputs["Base Color"]
            for l in list(inp.links):
                m.node_tree.links.remove(l)
            if src is not None:
                m.node_tree.links.new(src, inp)
            inp.default_value = bc
            b.inputs["Roughness"].default_value = r
            b.inputs["Metallic"].default_value = mt


# ───────────────────────── 渲染 ─────────────────────────

def _render_views(scene, anchor, show_obj, hide_objs, mats, work_dir, sun):
    show_obj.hide_render = False
    show_obj.hide_viewport = False
    for o in hide_objs:
        o.hide_render = True
    # ★ 预热：EEVEE 首帧渲染时，刚导入的 FBX 内嵌贴图（packed image）尚未上传 GPU，
    #   法线贴图读成空 → 低模整体平坦着色、贴图偏暗（实测踩过：第一个机位的低模明暗是一片均匀灰，
    #   把远景指标抬高了 5 倍，而其余机位与近景完全正常）。先丢弃渲染两个 pass 各一次。
    name0, az0, el0, df0, _res0 = VIEWS[0]
    scene.render.resolution_x, scene.render.resolution_y = 64, 64
    cam0 = _camera(scene, anchor, az0, el0, df0)
    _place_sun(sun, cam0)
    for kind in ("tex", "shape"):
        mats.use(kind)
        RC._render_np(scene, f"{work_dir}/warmup_{kind}.png")
    out = {}
    for name, az, el, dist_f, (rx, ry) in VIEWS:
        scene.render.resolution_x, scene.render.resolution_y = rx, ry
        cam = _camera(scene, anchor, az, el, dist_f)
        _place_sun(sun, cam)
        for kind in ("tex", "shape"):
            mats.use(kind)
            out[(kind, name)] = RC._render_np(scene, f"{work_dir}/lit_{kind}_{name}.png")
    return out


def _others(scene, keep):
    return [o for o in scene.objects if o.type == "MESH" and o not in keep]


def build_reference(high, cache_path, work_dir=None):
    """渲染高模（简化材质）并缓存。必须在【烘焙之前】调用（bake_scene 第 12 步会隔离高模贴图）。"""
    import bpy
    import shutil
    own = work_dir is None
    work_dir = work_dir or tempfile.mkdtemp(prefix="lit_")
    scene = bpy.context.scene
    old_hide = {o.name: (o.hide_render, o.hide_viewport) for o in scene.objects}
    old_engine = scene.render.engine
    old_res = (scene.render.resolution_x, scene.render.resolution_y)
    sun = _prep(scene)
    hm = _HighMats(high)
    try:
        imgs = _render_views(scene, high, high, _others(scene, [high]), hm, work_dir, sun)
    finally:
        hm.restore()
        scene.render.engine = old_engine
        scene.render.resolution_x, scene.render.resolution_y = old_res
        for o in scene.objects:
            if o.name in old_hide:
                o.hide_render, o.hide_viewport = old_hide[o.name]
    arrs = {f"{k}__{v}": a.astype(np.float16) for (k, v), a in imgs.items()}
    os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
    np.savez_compressed(cache_path, **arrs)
    if own:
        shutil.rmtree(work_dir, ignore_errors=True)
    return cache_path


def _down(a, k=2):
    return RC._down(a, k)


THRESH = {"tex_warn": 0.07, "tex_fail": 0.14, "shape_warn": 0.10, "shape_fail": 0.18}


def _stats(ref, imgs, kind, names):
    errs, p95s, bads = [], [], []
    for nm in names:
        H = ref[f"{kind}__{nm}"].astype(np.float32)
        L = imgs[(kind, nm)]
        Hd, Ld = _down(H), _down(L)
        mH, mL = Hd[..., 3] > 0.5, Ld[..., 3] > 0.5
        union = mH | mL
        if not union.any():
            continue
        diff = np.abs(Hd[..., :3] - Ld[..., :3]).mean(axis=-1)
        diff = np.where(mH & mL, diff, 1.0)
        d = diff[union]
        errs.append(float(d.mean()))
        p95s.append(float(np.percentile(d, 95)))
        bads.append(float((d > 0.25).mean()))
    return (float(np.mean(errs)), float(np.mean(p95s)), float(np.mean(bads)))


def compare(high, low, cache_path, out_png=None, work_dir=None):
    """低模对缓存的高模参考做光照对比。远景(far)与近景(near)分开统计。"""
    import bpy
    import shutil
    own = work_dir is None
    work_dir = work_dir or tempfile.mkdtemp(prefix="lit_")
    scene = bpy.context.scene
    ref = np.load(cache_path)
    old_hide = {o.name: (o.hide_render, o.hide_viewport) for o in scene.objects}
    old_engine = scene.render.engine
    old_res = (scene.render.resolution_x, scene.render.resolution_y)
    sun = _prep(scene)
    lm = _LowMats(low)
    try:
        imgs = _render_views(scene, high, low, _others(scene, [low]), lm, work_dir, sun)
    finally:
        lm.restore()
        scene.render.engine = old_engine
        scene.render.resolution_x, scene.render.resolution_y = old_res
        for o in scene.objects:
            if o.name in old_hide:
                o.hide_render, o.hide_viewport = old_hide[o.name]

    far = [v[0] for v in VIEWS if v[0].startswith("far")]
    near = [v[0] for v in VIEWS if v[0].startswith("near")]
    res = {}
    for grp, names in (("", far), ("near_", near)):
        for kind in ("tex", "shape"):
            e, p, b = _stats(ref, imgs, kind, names)
            res[f"lit_{grp}{kind}_err"] = round(e, 4)
            res[f"lit_{grp}{kind}_p95"] = round(p, 4)
            res[f"lit_{grp}{kind}_bad"] = round(b, 4)

    if out_png:
        rows = []
        for nm in far + near:
            hh, ll = ref[f"tex__{nm}"].astype(np.float32)[..., :3], imgs[("tex", nm)][..., :3]
            hs, ls = ref[f"shape__{nm}"].astype(np.float32)[..., :3], imgs[("shape", nm)][..., :3]
            rows.append(np.concatenate([hh, ll, hs, ls], axis=1))
        h_max = max(r.shape[0] for r in rows)
        w_max = max(r.shape[1] for r in rows)
        rows = [np.pad(r, ((0, h_max - r.shape[0]), (0, w_max - r.shape[1]), (0, 0))) for r in rows]
        sheet = np.concatenate(rows, axis=0)[::2, ::2]
        sheet = np.where(np.isfinite(sheet), sheet, 0)
        im = bpy.data.images.new("__lit_sheet", sheet.shape[1], sheet.shape[0], alpha=False)
        rgba = np.concatenate([sheet, np.ones(sheet.shape[:2] + (1,), np.float32)], axis=-1)
        im.pixels.foreach_set(rgba.ravel())
        im.filepath_raw = out_png
        im.file_format = "PNG"
        im.save()
        bpy.data.images.remove(im)
    if own:
        shutil.rmtree(work_dir, ignore_errors=True)

    lvl, reasons = 0, []
    for name, val, w, f in (("光照·贴图(远)", res["lit_tex_err"], THRESH["tex_warn"], THRESH["tex_fail"]),
                            ("光照·几何+法线(远)", res["lit_shape_err"], THRESH["shape_warn"], THRESH["shape_fail"])):
        if val >= f:
            reasons.append(f"[FAIL] {name}差异 {val:.3f}（≥{f}）")
            lvl = max(lvl, 2)
        elif val >= w:
            reasons.append(f"[WARN] {name}差异 {val:.3f}（≥{w}）")
            lvl = max(lvl, 1)
    res["lit_verdict"] = ("PASS", "WARN", "FAIL")[lvl]
    res["lit_reasons"] = reasons
    return res
