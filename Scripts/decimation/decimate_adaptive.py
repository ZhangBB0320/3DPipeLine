#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 预烘焙质量门 —— 固定 V2 体素阶梯，在给定面数预算下产出可烘焙的低模。

阶梯：voxel700 → voxel500 → voxel1000 → baseline(安全修复)。
所有候选都过 evaluate_quality；第一个达到 accept 级别的立即返回。
全部失败则抛 QualityGateError，由宿主决定换预算或放弃。
"""
from __future__ import annotations

import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (_HERE, os.path.dirname(_HERE)):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import bpy

import decimate_strategies as S
import evaluate_quality as Q

_RANK = {"PASS": 2, "WARN": 1, "FAIL": 0}


class QualityGateError(RuntimeError):
    """当前预算下所有策略都未通过质量门。携带完整报告。"""

    def __init__(self, msg, report):
        super().__init__(msg)
        self.report = report


def _ladder():
    tol = 0.03
    return [
        ("voxel700", lambda h, t, n: S.strat_voxel(h, t, n, voxel_div=700, tolerance=tol)),
        ("voxel500", lambda h, t, n: S.strat_voxel(h, t, n, voxel_div=500, tolerance=tol)),
        ("voxel1000", lambda h, t, n: S.strat_voxel(h, t, n, voxel_div=1000, tolerance=tol)),
        ("baseline", lambda h, t, n: S.strat_baseline(h, t, n)),
    ]


def decimate_adaptive(high_obj_name, target_faces=3000, name="lowmodel",
                      accept="PASS", report_path=None, verbose=True):
    """固定预算下的 V2 减面；返回 (low_obj_name, actual_faces, report)。"""
    t_all = time.time()
    high = bpy.data.objects[high_obj_name]
    low_name = f"{name}_Low"

    for o in list(bpy.data.objects):
        if o.name in (low_name, f"{low_name}_best"):
            S._drop(o)

    attempts, best = [], None
    log = print if verbose else (lambda *a, **k: None)
    log(f"\n[v2-decimate] 目标 {target_faces} 面，接受级别 {accept}")

    for sname, fn in _ladder():
        t0 = time.time()
        try:
            low, info = fn(high, target_faces, name)
        except Exception as exc:
            attempts.append({"strategy": sname, "error": str(exc)})
            log(f"[v2-decimate] {sname}: 异常 {exc}")
            continue
        t_dec = time.time() - t0
        rep = Q.evaluate_quality(high.name, low.name, target_faces=target_faces, verbose=False)
        faces = rep["intrinsic"]["faces"]
        rec = {"strategy": sname, "faces": faces, "verdict": rep["verdict"], "score": rep["score"],
               "reasons": rep["reasons"], "stagnated": bool(info.get("stagnated")),
               "p95_pct": (max(rep["fidelity"]["hd_l2h_p95_pct"], rep["fidelity"]["hd_h2l_p95_pct"])
                           if rep["fidelity"] else None),
               "seconds": round(time.time() - t0, 1), "decimate_seconds": round(t_dec, 1)}
        attempts.append(rec)
        p95 = rec["p95_pct"] if rec["p95_pct"] is not None else float("nan")
        log(f"[v2-decimate] {sname:<14} → {faces:>6} 面  {rep['verdict']:<4} "
            f"score={rep['score']:<5} p95={p95:.3f}% ({rec['seconds']}s)")
        for r in rep["reasons"]:
            log("               " + r)

        key = (_RANK[rep["verdict"]], rep["score"], -faces)
        if best is None or key > best[0]:
            if best is not None:
                S._drop(bpy.data.objects.get(best[2]))
            low.name = f"{low_name}_best"
            best = (key, sname, low.name, rep, rec)
        else:
            S._drop(low)

        if _RANK[rep["verdict"]] >= _RANK[accept]:
            break

    report = {"pipeline": "v2", "target_faces": target_faces, "accept": accept,
              "attempts": attempts, "chosen": None, "seconds": round(time.time() - t_all, 1)}
    if best is None:
        report["verdict"] = "FAIL"
        _dump(report, report_path)
        raise QualityGateError("所有策略都异常，未产出任何低模", report)

    key, sname, obj_name, rep, rec = best
    report.update({"chosen": sname, "verdict": rep["verdict"], "score": rep["score"],
                   "faces": rec["faces"], "intrinsic": rep["intrinsic"], "fidelity": rep["fidelity"]})
    _dump(report, report_path)

    if _RANK[rep["verdict"]] < _RANK[accept]:
        S._drop(bpy.data.objects.get(obj_name))
        raise QualityGateError(
            f"{target_faces} 面预算下所有策略均未达到 {accept}（最好：{sname} = {rep['verdict']}）", report)

    obj = bpy.data.objects[obj_name]
    obj.name = low_name
    obj.data.name = low_name
    obj.data.materials.clear()
    log(f"[v2-decimate] ✓ 采用 {sname}：{rec['faces']} 面 / {rep['verdict']} / score {rep['score']}")
    return obj.name, rec["faces"], report


def _dump(report, path):
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"[v2-decimate][WARN] 报告写入失败: {exc}")
