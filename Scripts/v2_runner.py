#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 headless Blender runner —— 单个 OBJ 高模 → 质量门减面 → V2 烘焙 → Unity FBX。

由 v2_pipeline.py 以 `--background --python v2_runner.py -- ...` 调用。
只负责一个模型；解压、遍历、命名、批量调度、Lite 决策由宿主负责。

流水线固定为 V2：
    safe_import → decimate_adaptive(V2 阶梯) → bake_v2 → export_model
质量门 FAIL 约定退出码 3，由宿主决定升面/换预算；其它异常退出码 1。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (_HERE, os.path.join(_HERE, "decimation")):
    if _d not in sys.path:
        sys.path.insert(0, _d)


def _argv() -> list:
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]


def clean_scene():
    import bpy
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.images,
                 bpy.data.cameras, bpy.data.lights, bpy.data.curves):
        for block in list(coll):
            if block.users == 0 and not block.use_fake_user:
                coll.remove(block)
    print("[v2-runner] scene cleaned", flush=True)


def configure_cycles_device(device: str) -> str:
    import bpy
    if device != "GPU":
        return "CPU"
    try:
        addon = bpy.context.preferences.addons.get("cycles")
        prefs = addon.preferences if addon else None
        if prefs is None:
            print("[v2-runner][WARN] cycles preferences unavailable -> CPU", flush=True)
            return "CPU"
        prefs.compute_device_type = "METAL"
        prefs.get_devices()
        gpus = [d for d in prefs.devices if d.type == "METAL"]
        print(f"[v2-runner] Cycles METAL devices: {[d.name for d in gpus]}", flush=True)
        if not gpus:
            print("[v2-runner][WARN] no METAL device -> CPU", flush=True)
            return "CPU"
        return "GPU"
    except Exception as exc:
        print(f"[v2-runner][WARN] GPU setup failed ({exc}) -> CPU", flush=True)
        return "CPU"


def main():
    p = argparse.ArgumentParser(description="V2 单模型 headless 烘焙")
    p.add_argument("--obj", required=True, help="解压后的高模 OBJ")
    p.add_argument("--output", required=True, help="输出低模 FBX")
    p.add_argument("--internal_name", default="lowmodel")
    p.add_argument("--target_faces", type=int, default=3000)
    p.add_argument("--cage", type=float, default=0.02)
    p.add_argument("--res", type=int, default=4096)
    p.add_argument("--samples", type=int, default=64)
    p.add_argument("--device", default="GPU", choices=["CPU", "GPU"])
    p.add_argument("--accept", default="PASS", choices=["PASS", "WARN"])
    p.add_argument("--report", default=None, help="几何门报告 JSON")
    a = p.parse_args(_argv())

    t_all = time.time()
    print("=" * 70, flush=True)
    print(f"[v2-runner] OBJ    : {a.obj}", flush=True)
    print(f"[v2-runner] OUTPUT : {a.output}", flush=True)
    print(f"[v2-runner] PARAMS : faces={a.target_faces} cage={a.cage} "
          f"res={a.res} samples={a.samples} device={a.device}", flush=True)
    print("=" * 70, flush=True)

    if not os.path.exists(a.obj):
        raise FileNotFoundError(f"OBJ 不存在: {a.obj}")
    os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)

    clean_scene()
    device = configure_cycles_device(a.device)

    import bpy
    from import_fbx import safe_import
    from decimate_adaptive import decimate_adaptive, QualityGateError
    from v2_bake import bake_v2
    from export_model import export_model

    t = time.time()
    new_objs = safe_import(a.obj, verbose=True, rename_to_stem=True)
    high_name = next((nm for nm in new_objs
                      if bpy.data.objects.get(nm) is not None and bpy.data.objects[nm].type == "MESH"), None)
    if high_name is None:
        raise RuntimeError("OBJ 中未找到 mesh 对象")
    print(f"[v2-runner] [1/4] imported '{high_name}' — "
          f"{len(bpy.data.objects[high_name].data.polygons):,} faces ({time.time() - t:.1f}s)", flush=True)

    t = time.time()
    try:
        low_name, actual_faces, gate = decimate_adaptive(
            high_obj_name=high_name, target_faces=a.target_faces,
            name=a.internal_name, accept=a.accept, report_path=a.report,
        )
    except QualityGateError as exc:
        print(f"[v2-runner] ✗ 预烘焙质量门未通过：{exc}", flush=True)
        sys.stdout.flush()
        sys.exit(3)
    print(f"[v2-runner] [2/4] decimated -> '{low_name}' — {actual_faces:,} faces "
          f"({time.time() - t:.1f}s)", flush=True)

    t = time.time()
    bake_v2(
        high_obj_name=high_name, low_obj_name=low_name, name=a.internal_name,
        res=a.res, samples=a.samples, device=device, cage=a.cage,
    )
    print(f"[v2-runner] [3/4] baked ({time.time() - t:.1f}s)", flush=True)

    final = {"pipeline": "v2", "geometry_gate": gate,
             "verdict": (gate or {}).get("verdict", "UNKNOWN"),
             "target_faces": a.target_faces, "faces": actual_faces}
    if a.report:
        with open(a.report, "w", encoding="utf-8") as fh:
            json.dump(final, fh, ensure_ascii=False, indent=2)
    print(f"[v2-runner] GATE_VERDICT={final['verdict']}", flush=True)

    t = time.time()
    result = export_model(obj_names=[low_name], output_path=a.output, fmt="fbx", target="unity")
    print(f"[v2-runner] [4/4] exported {result['path']} ({time.time() - t:.1f}s)", flush=True)

    if not os.path.exists(a.output) or os.path.getsize(a.output) < 1024:
        raise RuntimeError(f"输出 FBX 未生成或过小: {a.output}")
    print(f"[v2-runner] DONE size={os.path.getsize(a.output)/1024/1024:.1f}MB "
          f"total={time.time() - t_all:.1f}s", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(1)
