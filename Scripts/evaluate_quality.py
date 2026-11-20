#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
减面质量评估 —— 在【烘焙之前】就发现"形状错了 / 几何被撕碎了"。

背景（2026-11-20 诺布大厦事故）：
  1.87M 面一步 COLLAPSE 到 3000 面，流水线只看"面数达标"就继续烘焙，结果
    · Style1：拓扑干净（1 个连通块）但轮廓整体变形 —— 只看低模自身永远发现不了；
    · Style5：COLLAPSE 触底（卡在 13036 面），修复步骤再删 3007 面 → 94 个碎片壳。
  KB/quality_metrics.md 里写的 evaluate_quality.py 一直只存在于文档，这里补上并针对
  上述两类失败设计指标。

两层检测（便宜的先行，贵的在后）：
  ① 低模自检（intrinsic）  —— 不需要高模：连通块 / 碎片壳 / 边界边 / 非流形 / 细长三角 / 面数偏离目标
  ② 对高模保真度（fidelity）—— 双向采样距离 + 三视图轮廓 IoU + 法线一致性

判级：
  PASS  可以进入烘焙
  WARN  能用，但建议升面或换策略
  FAIL  不要烘焙（烘焙只会把错误几何"画"上漂亮的贴图，更难被发现）

用法（Blender 内，对象已在场景里）：
    from evaluate_quality import evaluate_quality
    rep = evaluate_quality("high_name", "low_name", target_faces=3000)
    print(rep["verdict"], rep["reasons"])
"""
from __future__ import annotations

import math
import time

import numpy as np

# ───────────────────────── 阈值（以 2026-11-20 真实数据校准，见 KB/quality_metrics.md）─────────────────────────
THRESH = {
    # 低模自检
    "face_overshoot_fail": 1.30,     # 实际面数 > 目标×1.30 = 触底，COLLAPSE 没减下来
    "face_overshoot_warn": 1.12,
    "tiny_comp_faces": 4,            # ≤4 面的壳视为"碎片"
    "tiny_comp_warn": 3,             # 碎片壳【只告警】：school5 有 18 个小碎片，渲染对比一切正常(0.017)
    "main_comp_fail_pct": 90.0,      # 主体壳占比 <90% = 真正被撕碎（nobu5 旧结果主体仅 97.7% 但边界 22.7%）
    "boundary_ratio_fail": 0.20,     # 边界边 / 总边
    "boundary_ratio_warn": 0.06,
    # 保真度（距离均归一化到高模包围盒对角线，单位 %）
    "hd_p95_fail": 2.0,
    "hd_p95_warn": 1.0,
    "hd_mean_warn": 0.35,
    "sil_iou_fail": 0.90,
    "sil_iou_warn": 0.95,
    "flip_fail": 0.10,
    "flip_warn": 0.04,
}


# ═════════════════════════════ 网格 → numpy ═════════════════════════════

def _tri_arrays(obj):
    """返回世界坐标下的 (verts[N,3], tris[M,3])。"""
    import bpy
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(depsgraph)
    me = ev.to_mesh()
    try:
        me.calc_loop_triangles()
        nv = len(me.vertices)
        nt = len(me.loop_triangles)
        co = np.empty(nv * 3, dtype=np.float32)
        me.vertices.foreach_get("co", co)
        co = co.reshape(nv, 3)
        tri = np.empty(nt * 3, dtype=np.int32)
        me.loop_triangles.foreach_get("vertices", tri)
        tri = tri.reshape(nt, 3)
        mw = np.array(obj.matrix_world, dtype=np.float32)
        co = co @ mw[:3, :3].T + mw[:3, 3]
        return co, tri
    finally:
        ev.to_mesh_clear()


def _sample_surface(co, tri, n, rng):
    """按面积加权在表面均匀采样 n 个点，返回 (points[n,3], face_idx[n])。"""
    a, b, c = co[tri[:, 0]], co[tri[:, 1]], co[tri[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    tot = area.sum()
    if tot <= 0:
        return np.zeros((0, 3), np.float32), np.zeros(0, np.int64)
    cdf = np.cumsum(area, dtype=np.float64)
    cdf /= cdf[-1]
    fi = np.searchsorted(cdf, rng.random(n))
    fi = np.clip(fi, 0, len(tri) - 1)
    r1 = np.sqrt(rng.random(n))[:, None]
    r2 = rng.random(n)[:, None]
    pts = (1 - r1) * a[fi] + r1 * (1 - r2) * b[fi] + r1 * r2 * c[fi]
    return pts.astype(np.float32), fi


# ═════════════════════════════ ① 低模自检 ═════════════════════════════

def intrinsic_check(low_obj, target_faces=None):
    """不依赖高模的体检。能发现：触底 / 碎片壳 / 撕裂（边界边暴增）/ 非流形。"""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(low_obj.data)
    bm.faces.ensure_lookup_table()
    nfaces = len(bm.faces)

    # 连通块（按面邻接）
    seen = set()
    comps = []
    for f in bm.faces:
        if f.index in seen:
            continue
        seen.add(f.index)
        stack, n = [f], 0
        while stack:
            cur = stack.pop()
            n += 1
            for e in cur.edges:
                for g in e.link_faces:
                    if g.index not in seen:
                        seen.add(g.index)
                        stack.append(g)
        comps.append(n)
    comps.sort(reverse=True)

    nedges = len(bm.edges)
    boundary = sum(1 for e in bm.edges if e.is_boundary)
    nonman = sum(1 for e in bm.edges if (not e.is_manifold) and (not e.is_boundary))

    sliver = 0
    for f in bm.faces:
        a = f.calc_area()
        if a > 1e-12:
            m = max(e.calc_length() for e in f.edges)
            if (m * m) / a > 12.0:
                sliver += 1
    bm.free()

    r = {
        "faces": nfaces,
        "components": len(comps),
        "main_component_pct": round(100.0 * comps[0] / max(nfaces, 1), 1) if comps else 0.0,
        "tiny_components": sum(1 for c in comps if c <= THRESH["tiny_comp_faces"]),
        "boundary_edges": boundary,
        "boundary_ratio": round(boundary / max(nedges, 1), 4),
        "nonmanifold_edges": nonman,
        "sliver_pct": round(100.0 * sliver / max(nfaces, 1), 1),
    }
    if target_faces:
        r["face_overshoot"] = round(nfaces / target_faces, 3)
    return r


# ═════════════════════════════ ② 对高模保真度 ═════════════════════════════

def _dilate1(m):
    """3×3 最大值膨胀（纯 numpy，避免引 scipy）。"""
    p = np.pad(m, 1)
    out = np.zeros_like(m)
    for dx in (0, 1, 2):
        for dy in (0, 1, 2):
            out |= p[dx:dx + m.shape[0], dy:dy + m.shape[1]]
    return out


def _silhouette_iou(pa, pb, lo, hi, grid=128):
    """三个轴向投影的轮廓【容差一致率】，返回 (min, [xy,yz,xz])。

    ★ 为什么不用严格 IoU：建筑墙面是竖直的，俯视时墙的投影只是一圈细线，
      两个模型差 2 毫米就让这一圈格子对不上 —— 实测"完全正确"的结果严格 IoU 也只有 0.94。
      所以允许 1 格容差：A 中有多少落在 B 的 1 格邻域里、B 中有多少落在 A 的邻域里，
      两边合起来占总量的比例。真正的形状走样（鼓包、塌陷、缺块）仍会大幅拉低它。
    """
    span = np.maximum(hi - lo, 1e-9)
    out = []
    for (i, j) in ((0, 1), (1, 2), (0, 2)):
        def occ(p):
            u = np.clip(((p[:, i] - lo[i]) / span[i] * grid).astype(np.int32), 0, grid - 1)
            v = np.clip(((p[:, j] - lo[j]) / span[j] * grid).astype(np.int32), 0, grid - 1)
            m = np.zeros((grid, grid), bool)
            m[u, v] = True
            return m
        A, B = occ(pa), occ(pb)
        tot = A.sum() + B.sum()
        agree = (A & _dilate1(B)).sum() + (B & _dilate1(A)).sum()
        out.append(float(agree / tot) if tot else 1.0)
    return min(out), out


_HIGH_CACHE = {}


def clear_cache():
    _HIGH_CACHE.clear()


def _high_data(high_obj):
    """高模不变而低模会被反复评估（自适应升面 / 对比实验）：三角数组与 BVH 只建一次。"""
    import bpy
    from mathutils.bvhtree import BVHTree
    key = (high_obj.name, len(high_obj.data.polygons))
    hit = _HIGH_CACHE.get(key)
    if hit is None:
        dg = bpy.context.evaluated_depsgraph_get()
        hco, htri = _tri_arrays(high_obj)
        bvh = BVHTree.FromObject(high_obj, dg)
        hit = (hco, htri, bvh)
        _HIGH_CACHE.clear()          # 同一时刻只留一个高模，防止 GB 级内存堆积
        _HIGH_CACHE[key] = hit
    return hit


def fidelity_check(high_obj, low_obj, n_samples=20000, seed=7):
    """双向采样距离（对角线 %）+ 轮廓 IoU + 法线一致性。"""
    import bpy
    from mathutils.bvhtree import BVHTree

    rng = np.random.default_rng(seed)
    dg = bpy.context.evaluated_depsgraph_get()

    hco, htri, bvh_h = _high_data(high_obj)
    lco, ltri = _tri_arrays(low_obj)
    lo, hi = hco.min(0), hco.max(0)
    diag = float(np.linalg.norm(hi - lo))

    hp, _ = _sample_surface(hco, htri, n_samples, rng)
    lp, lfi = _sample_surface(lco, ltri, n_samples, rng)

    bvh_l = BVHTree.FromObject(low_obj, dg)

    # ★ 坐标空间：采样点是【世界坐标】，而 BVHTree.FromObject 建的是物体【局部坐标】的树。
    #   OBJ 导入常带旋转 / 缩放，两边不换算就会错位 —— 第一版评估就因此给所有策略
    #   报出同一个"偏差 63%"（一个看起来合理却完全错误的数）。
    #   所以：查询前把点转入该物体局部空间，命中点 / 法线再转回世界空间。
    from mathutils import Vector
    mw_h, mw_l = high_obj.matrix_world, low_obj.matrix_world
    mi_h, mi_l = mw_h.inverted(), mw_l.inverted()
    rot_h = mw_h.to_3x3()

    # 低 → 高：低模表面离高模多远（"低模有没有飘出去 / 缩进去"）
    d_l2h = np.empty(len(lp), np.float32)
    flips = 0
    la, lb, lc = lco[ltri[lfi, 0]], lco[ltri[lfi, 1]], lco[ltri[lfi, 2]]
    ln = np.cross(lb - la, lc - la)
    ln /= np.maximum(np.linalg.norm(ln, axis=1, keepdims=True), 1e-12)
    for k in range(len(lp)):
        p_w = Vector(lp[k])
        loc, nrm, _idx, _d = bvh_h.find_nearest(mi_h @ p_w)
        if loc is None:
            d_l2h[k] = diag
            continue
        d_l2h[k] = (mw_h @ loc - p_w).length
        if nrm is not None:
            n_w = (rot_h @ nrm).normalized()
            if (ln[k] @ np.array(n_w, np.float32)) < 0:
                flips += 1

    # 高 → 低：高模表面被低模"覆盖"得多好（"低模有没有漏掉东西"）
    d_h2l = np.empty(len(hp), np.float32)
    for k in range(len(hp)):
        p_w = Vector(hp[k])
        loc, _n, _i, _d = bvh_l.find_nearest(mi_l @ p_w)
        d_h2l[k] = diag if loc is None else (mw_l @ loc - p_w).length

    pct = lambda a, q: float(np.percentile(a, q) / diag * 100.0)
    # 轮廓 IoU 用更稠密的采样
    hp2, _ = _sample_surface(hco, htri, 300000, rng)
    lp2, _ = _sample_surface(lco, ltri, 300000, rng)
    sil_min, sil_all = _silhouette_iou(hp2, lp2, lo, hi)

    return {
        "diag": round(diag, 4),
        "hd_l2h_mean_pct": round(float(d_l2h.mean() / diag * 100), 3),
        "hd_l2h_p95_pct": round(pct(d_l2h, 95), 3),
        "hd_h2l_mean_pct": round(float(d_h2l.mean() / diag * 100), 3),
        "hd_h2l_p95_pct": round(pct(d_h2l, 95), 3),
        "hd_max_pct": round(max(float(d_l2h.max()), float(d_h2l.max())) / diag * 100, 3),
        "silhouette_iou_min": round(sil_min, 4),
        "silhouette_iou": [round(x, 4) for x in sil_all],
        "normal_flip_ratio": round(flips / max(len(lp), 1), 4),
    }


# ═════════════════════════════ 判级 ═════════════════════════════

def verdict_of(intr, fid=None):
    """返回 (verdict, score, reasons)。reasons 里每条都是人话，直接给美术看。"""
    T = THRESH
    reasons, fail, warn = [], 0, 0
    pen = 0.0

    ov = intr.get("face_overshoot")
    if ov is not None:
        if ov > T["face_overshoot_fail"]:
            reasons.append(f"[FAIL] 减面触底：实际 {intr['faces']} 面 = 目标 {ov:.2f} 倍（COLLAPSE 减不动，后续修复会大面积删面）")
            fail += 1; pen += 30
        elif ov > T["face_overshoot_warn"]:
            reasons.append(f"[WARN] 面数偏高：目标的 {ov:.2f} 倍")
            warn += 1; pen += 8
    tc = intr["tiny_components"]
    if intr["main_component_pct"] < T["main_comp_fail_pct"]:
        reasons.append(f"[FAIL] 几何被撕碎：{intr['components']} 个连通块，主体仅占 {intr['main_component_pct']}%")
        fail += 1; pen += 30
    elif tc >= T["tiny_comp_warn"]:
        reasons.append(f"[WARN] 出现 {tc} 个 ≤{T['tiny_comp_faces']} 面的碎片壳（共 {intr['components']} 个连通块）")
        warn += 1; pen += 8
    br = intr["boundary_ratio"]
    if br > T["boundary_ratio_fail"]:
        reasons.append(f"[FAIL] 边界边占 {br*100:.1f}%：表面大量撕裂 / 漏洞")
        fail += 1; pen += 25
    elif br > T["boundary_ratio_warn"]:
        reasons.append(f"[WARN] 边界边占 {br*100:.1f}%：存在较多开口")
        warn += 1; pen += 6

    if fid:
        p95 = max(fid["hd_l2h_p95_pct"], fid["hd_h2l_p95_pct"])
        if p95 > T["hd_p95_fail"]:
            reasons.append(f"[FAIL] 与高模偏差过大：p95 = {p95:.2f}% 对角线（>{T['hd_p95_fail']}%）")
            fail += 1; pen += 30
        elif p95 > T["hd_p95_warn"]:
            reasons.append(f"[WARN] 与高模偏差偏大：p95 = {p95:.2f}%")
            warn += 1; pen += 10
        si = fid["silhouette_iou_min"]
        if si < T["sil_iou_fail"]:
            reasons.append(f"[FAIL] 轮廓走样：三视图最差 IoU = {si:.3f}（<{T['sil_iou_fail']}），整体形状已变")
            fail += 1; pen += 30
        elif si < T["sil_iou_warn"]:
            reasons.append(f"[WARN] 轮廓略有变形：最差 IoU = {si:.3f}")
            warn += 1; pen += 10
        fl = fid["normal_flip_ratio"]
        if fl > T["flip_fail"]:
            reasons.append(f"[FAIL] {fl*100:.1f}% 的低模表面法线与高模相反（面翻转 / 内外颠倒）")
            fail += 1; pen += 20
        elif fl > T["flip_warn"]:
            reasons.append(f"[WARN] {fl*100:.1f}% 法线与高模相反")
            warn += 1; pen += 6
        if fid["hd_l2h_mean_pct"] > T["hd_mean_warn"]:
            reasons.append(f"[WARN] 平均偏差 {fid['hd_l2h_mean_pct']:.2f}% 偏大")
            warn += 1; pen += 4

    v = "FAIL" if fail else ("WARN" if warn else "PASS")
    return v, max(0.0, round(100.0 - pen, 1)), reasons


def evaluate_quality(high_obj_name, low_obj_name, target_faces=None,
                     n_samples=20000, with_fidelity=True, verbose=True):
    """主入口：对象名 → 报告 dict。with_fidelity=False 时只做便宜的低模自检。"""
    import bpy
    t0 = time.time()
    low = bpy.data.objects[low_obj_name]
    intr = intrinsic_check(low, target_faces)
    fid = None
    if with_fidelity:
        high = bpy.data.objects[high_obj_name]
        fid = fidelity_check(high, low, n_samples=n_samples)
    v, score, reasons = verdict_of(intr, fid)
    rep = {"verdict": v, "score": score, "reasons": reasons,
           "intrinsic": intr, "fidelity": fid,
           "seconds": round(time.time() - t0, 1)}
    if verbose:
        print(f"[quality] {low_obj_name}: {v} score={score} ({rep['seconds']}s)")
        for r in reasons:
            print("    " + r)
    return rep


def flat(rep):
    """展平成一行，便于做实验对比表。"""
    d = {"verdict": rep["verdict"], "score": rep["score"]}
    d.update(rep["intrinsic"])
    if rep["fidelity"]:
        d.update(rep["fidelity"])
    return d
