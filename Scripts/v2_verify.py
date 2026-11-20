#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 交付物验收 —— 独立 Blender 进程重读【高模 OBJ】与【导出的低模 FBX】。

验收对象必须是真正交付给 Unity 的 FBX：几何、内嵌贴图、UV、导出轴是否正确，
只有重新读回来才知道。输出：
    --report   JSON（几何保真度 + Workbench 渲染对比 + EEVEE 光照对比 + UV 体检）
    --png      Workbench 联系表
    --lit_png  EEVEE 光照对比联系表（含近景，能看到法线贴图）
    --lit_ref  高模光照参考缓存；同一模型多个预算共用，避免重复渲染
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (_HERE, os.path.join(_HERE, "decimation")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import bpy
import numpy as np
from mathutils import Matrix

from import_fbx import safe_import
import evaluate_quality as Q
import render_compare as RC

_ORDER = {"PASS": 0, "WARN": 1, "FAIL": 2}


def _argv():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]


def _align(high, low):
    hd, ld = np.array(high.dimensions), np.array(low.dimensions)
    if np.allclose(hd, ld, rtol=0.05, atol=1e-4):
        return "as-is"
    best = None
    hco, htri, _ = Q._high_data(high)
    lo, hi = hco.min(0), hco.max(0)
    rng = np.random.default_rng(1)
    hp, _ = Q._sample_surface(hco, htri, 150000, rng)
    for rx in (0, 90, 180, 270):
        for ry in (0, 90, 180, 270):
            low.matrix_world = Matrix.Rotation(math.radians(rx), 4, "X") @ Matrix.Rotation(math.radians(ry), 4, "Y")
            bpy.context.view_layer.update()
            lco, ltri = Q._tri_arrays(low)
            shift = (hco.min(0) + hco.max(0)) / 2 - (lco.min(0) + lco.max(0)) / 2
            lp, _ = Q._sample_surface(lco + shift, ltri, 150000, rng)
            iou, _ = Q._silhouette_iou(hp, lp, lo, hi)
            if best is None or iou > best[0]:
                best = (iou, rx, ry, shift)
    from mathutils import Vector
    low.matrix_world = (Matrix.Translation(Vector(best[3])) @
                        Matrix.Rotation(math.radians(best[1]), 4, "X") @
                        Matrix.Rotation(math.radians(best[2]), 4, "Y"))
    bpy.context.view_layer.update()
    return f"auto-aligned rx={best[1]} ry={best[2]} (agree={best[0]:.3f})"


def uv_report(low, grid=1024):
    """UV 越界 / 重叠 / 覆盖率 / 纹素密度(@4096)。"""
    me = low.data
    if not me.uv_layers:
        return {"has_uv": False}
    me.calc_loop_triangles()
    nt, nv = len(me.loop_triangles), len(me.vertices)
    co = np.empty(nv * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    tri = np.empty(nt * 3, np.int64); me.loop_triangles.foreach_get("vertices", tri); tri = tri.reshape(-1, 3)
    lt = np.empty(nt * 3, np.int64); me.loop_triangles.foreach_get("loops", lt); lt = lt.reshape(-1, 3)
    uv = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", uv); uv = uv.reshape(-1, 2)
    a3 = 0.5 * np.linalg.norm(np.cross(co[tri[:, 1]] - co[tri[:, 0]], co[tri[:, 2]] - co[tri[:, 0]]), axis=1)
    p0, p1, p2 = uv[lt[:, 0]], uv[lt[:, 1]], uv[lt[:, 2]]
    au = 0.5 * np.abs((p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1]) -
                      (p1[:, 1] - p0[:, 1]) * (p2[:, 0] - p0[:, 0]))
    oor = float((((uv < -1e-4) | (uv > 1 + 1e-4)).any(axis=1)).mean() * 100)
    cnt = np.zeros((grid, grid), np.int16)
    for k in range(nt):
        tri_uv = np.array([p0[k], p1[k], p2[k]]) * grid
        x0, y0 = np.floor(tri_uv.min(0)).astype(int); x1, y1 = np.ceil(tri_uv.max(0)).astype(int)
        x0, y0 = max(x0, 0), max(y0, 0); x1, y1 = min(x1, grid - 1), min(y1, grid - 1)
        if x1 < x0 or y1 < y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        (ax, ay), (bx, by), (cx, cy) = tri_uv
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) < 1e-12:
            continue
        l1 = ((by - cy) * (xs - cx) + (cx - bx) * (ys - cy)) / d
        l2 = ((cy - ay) * (xs - cx) + (ax - cx) * (ys - cy)) / d
        ins = (l1 >= 0) & (l2 >= 0) & (l1 + l2 <= 1)
        cnt[y0:y1 + 1, x0:x1 + 1] += ins
    covered = int((cnt > 0).sum())
    overlap = float((cnt > 1).sum() / max(covered, 1) * 100)
    dens = float((np.sqrt(np.maximum(au, 1e-30) / np.maximum(a3, 1e-30)) * a3).sum() / a3.sum())
    return {"has_uv": True, "uv_out_of_range_pct": round(oor, 3), "uv_overlap_pct": round(overlap, 3),
            "uv_cover": round(float(au.sum()), 4), "texel_per_unit_4096": round(dens * 4096, 1)}


def dump_textures(low, out_dir, prefix):
    os.makedirs(out_dir, exist_ok=True)
    saved = []
    for s in low.material_slots:
        m = s.material
        if m is None or not m.use_nodes:
            continue
        for n in m.node_tree.nodes:
            if n.type == "TEX_IMAGE" and n.image:
                kind = "normal" if "normal" in n.image.name.lower() else "albedo"
                path = os.path.join(out_dir, f"{prefix}_{kind}.png")
                n.image.save_render(path)
                saved.append(path)
    return saved


def verify(obj_path, fbx_path, target_faces=None, png=None, lit_png=None,
           lit_ref=None, dump_dir=None, dump_prefix="tex"):
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    new = safe_import(obj_path, verbose=False, rename_to_stem=True)
    high = next(bpy.data.objects[n] for n in new if bpy.data.objects[n].type == "MESH")
    before = set(bpy.data.objects.keys())
    bpy.ops.import_scene.fbx(filepath=fbx_path)
    low = next(bpy.data.objects[n] for n in bpy.data.objects.keys()
               if n not in before and bpy.data.objects[n].type == "MESH")

    align = _align(high, low)
    geo = Q.evaluate_quality(high.name, low.name, target_faces=target_faces, verbose=False)
    cmp_ = RC.render_compare(high.name, low.name, out_png=png)

    owns_ref = lit_ref is None
    if owns_ref:
        ref_dir = tempfile.mkdtemp(prefix="v2_litref_")
        lit_ref = os.path.join(ref_dir, "ref.npz")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(lit_ref)), exist_ok=True)
    lit = {}
    try:
        import render_lit as L
        if not os.path.exists(lit_ref):
            L.build_reference(high, lit_ref)
        lit = L.compare(high, low, lit_ref, out_png=lit_png)
    except Exception as exc:
        lit = {"lit_error": str(exc)}
    finally:
        if owns_ref:
            shutil.rmtree(os.path.dirname(lit_ref), ignore_errors=True)

    uvr = uv_report(low)
    if dump_dir:
        dump_textures(low, dump_dir, dump_prefix)

    lit_verdict = lit.get("lit_verdict", "PASS")
    verdict = max((geo["verdict"], cmp_["verdict"], lit_verdict), key=lambda v: _ORDER[v])
    reasons = geo["reasons"] + cmp_["reasons"] + lit.get("lit_reasons", [])
    if "lit_error" in lit:
        reasons.append(f"[WARN] 光照对比失败：{lit['lit_error']}")
    return {"verdict": verdict, "alignment": align, "reasons": reasons,
            "geometry": {"verdict": geo["verdict"], "score": geo["score"],
                         "intrinsic": geo["intrinsic"], "fidelity": geo["fidelity"]},
            "render_compare": cmp_, "lit": lit, "uv": uvr, "fbx": fbx_path}


def main():
    p = argparse.ArgumentParser(description="V2 交付 FBX 验收")
    p.add_argument("--obj", required=True)
    p.add_argument("--fbx", required=True)
    p.add_argument("--report", default=None)
    p.add_argument("--png", default=None)
    p.add_argument("--lit_png", default=None)
    p.add_argument("--lit_ref", default=None, help="高模光照参考 npz 缓存路径")
    p.add_argument("--dump_tex", default=None)
    p.add_argument("--dump_prefix", default="tex")
    p.add_argument("--target_faces", type=int, default=None)
    a = p.parse_args(_argv())
    rep = verify(obj_path=a.obj, fbx_path=a.fbx, target_faces=a.target_faces,
                 png=a.png, lit_png=a.lit_png, lit_ref=a.lit_ref,
                 dump_dir=a.dump_tex, dump_prefix=a.dump_prefix)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
    for r in rep["reasons"]:
        print("  " + r)
    c, g = rep["render_compare"], rep["geometry"]
    print(f"VERIFY geo={g['verdict']}({g['score']}) faces={g['intrinsic']['faces']} "
          f"p95={max(g['fidelity']['hd_l2h_p95_pct'], g['fidelity']['hd_h2l_p95_pct'])}% "
          f"| render={c['verdict']} tex={c['tex_err']} p95={c['tex_p95']} bad={c['tex_bad_frac']}")
    lt = rep.get("lit", {})
    if "lit_tex_err" in lt:
        print(f"VERIFY_LIT tex={lt['lit_tex_err']}/{lt['lit_tex_p95']} "
              f"shape={lt['lit_shape_err']}/{lt['lit_shape_p95']} "
              f"near_tex={lt['lit_near_tex_err']}/{lt['lit_near_tex_p95']} "
              f"near_shape={lt['lit_near_shape_err']}/{lt['lit_near_shape_p95']}")
    u = rep.get("uv", {})
    if u.get("has_uv"):
        print(f"VERIFY_UV out_of_range={u['uv_out_of_range_pct']}% overlap={u['uv_overlap_pct']}% "
              f"cover={u['uv_cover']} texel/unit={u['texel_per_unit_4096']}")
    print(f"VERIFY_VERDICT={rep['verdict']}")


if __name__ == "__main__":
    main()
