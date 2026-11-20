#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2 / V2Lite 单入口批量管线。

目录约定：
    <root>/<建筑名>/StyleN/模型/*.zip
输出：
    <建筑名><StyleN>LowModel_V2.fbx       质量主版本（7 个模型都有）
    <建筑名><StyleN>LowModel_V2Lite.fbx  自适应轻量版（仅当低预算质量达标时生成）

V2Lite 判据不依赖任何旧版文件：先生成/读取 _V2 的验收指标，再从低到高尝试
Lite 预算，要求验收 PASS、面数明显更低，且 6 项光照指标不超过 V2 的
--lite_rel 倍 + 微小绝对余量。

用法：
    python3 Scripts/v2_pipeline.py --root /Users/zbb/Desktop/美术风格确定 --mode both
    python3 Scripts/v2_pipeline.py --root ... --mode v2 --only 学校大楼/Style1 --force
    python3 Scripts/v2_pipeline.py --root ... --mode both --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("STYLE_ROOT", "/Users/zbb/Desktop/美术风格确定"))
BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"
HERE = Path(__file__).resolve().parent
RUNNER = HERE / "v2_runner.py"
VERIFY = HERE / "v2_verify.py"
WORK = Path("/tmp/3dpipeline_v2_work")
LOG_DIR = WORK / "logs"
REF_DIR = WORK / "refs"

STYLE_RE = re.compile(r"^style\d+$", re.IGNORECASE)
MIN_FBX_BYTES = 1024
V2_SUFFIX = "_V2"
LITE_SUFFIX = "_V2Lite"
BUNDLE_EXTS = (".fbx", ".quality.json", ".compare.png", ".lit.png")
LITE_KEYS = (
    ("lit_tex_err", 0.0004),
    ("lit_tex_p95", 0.0015),
    ("lit_shape_p95", 0.0015),
    ("lit_near_tex_err", 0.0004),
    ("lit_near_tex_p95", 0.0030),
    ("lit_near_shape_p95", 0.0030),
)
_RANK = {"PASS": 0, "WARN": 1, "FAIL": 2}


def iter_jobs(root: Path):
    """产出 (tag, 建筑名, StyleN, zip|None, 模型目录, 备注)。"""
    for bdir in sorted(p for p in root.iterdir() if p.is_dir()):
        for sdir in sorted(p for p in bdir.iterdir()
                           if p.is_dir() and STYLE_RE.match(p.name)):
            mdir = sdir / "模型"
            zips = sorted(mdir.glob("*.zip")) if mdir.is_dir() else []
            tag = f"{bdir.name}/{sdir.name}"
            if not zips:
                yield tag, bdir.name, sdir.name, None, mdir, "无 zip，跳过"
                continue
            if len(zips) > 1:
                chosen = max(zips, key=lambda z: z.stat().st_size)
                print(f"[WARN] {tag} 有 {len(zips)} 个 zip，取最大的: {chosen.name}", flush=True)
            else:
                chosen = zips[0]
            yield tag, bdir.name, sdir.name, chosen, mdir, "OK"


def extract(zip_path: Path, dest: Path) -> Path:
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            parts = Path(member.filename).parts
            if any(part == "__MACOSX" or part.startswith("._") for part in parts):
                continue
            if member.filename.startswith("/") or ".." in parts:
                continue
            zf.extract(member, dest)
    objs = [p for p in dest.rglob("*.obj") if not p.name.startswith("._")]
    if not objs:
        raise RuntimeError(f"zip 中未找到 .obj: {zip_path.name}")
    return max(objs, key=lambda p: p.stat().st_size)


def _blender(script, args, log, timeout):
    cmd = [BLENDER, "--background", "--python", str(script), "--"] + [str(x) for x in args]
    with log.open("a", encoding="utf-8", errors="replace") as lf:
        lf.write("\n$ " + " ".join(cmd) + "\n\n")
        lf.flush()
        try:
            return subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            lf.write(f"\n[TIMEOUT] exceeded {timeout}s\n")
            return -9


def _out_path(mdir: Path, bname: str, sname: str, suffix: str) -> Path:
    return mdir / f"{bname}{sname}LowModel{suffix}.fbx"


def _remove_bundle(out_path: Path):
    for ext in BUNDLE_EXTS:
        p = out_path.with_suffix(ext) if ext != ".fbx" else out_path
        if p.exists():
            p.unlink()


def _load_report(out_path: Path):
    q = out_path.with_suffix(".quality.json")
    if not q.exists() or q.stat().st_size < 16:
        return None
    try:
        return json.loads(q.read_text(encoding="utf-8"))
    except Exception:
        return None


def _quality_key(ver):
    """越小越好：总判级 → 光照贴图误差 → 近景贴图 p95 → 几何分(取反) → 面数。"""
    lit = ver.get("lit", {})
    c, g = ver["render_compare"], ver["geometry"]
    return (_RANK[ver["verdict"]],
            lit.get("lit_tex_err", c["tex_err"]),
            lit.get("lit_near_tex_p95", c["tex_p95"]),
            -g["score"], g["intrinsic"]["faces"])


def _runner_args(obj, fbx, budget, gate_json, a):
    return ["--obj", obj, "--output", fbx, "--target_faces", budget,
            "--accept", a.accept, "--report", gate_json,
            "--cage", a.cage, "--res", a.res, "--samples", a.samples,
            "--device", a.device]


def _verify_args(obj, fbx, budget, report, png, lit_png, ref_path):
    return ["--obj", obj, "--fbx", fbx, "--report", report, "--png", png,
            "--lit_png", lit_png, "--lit_ref", ref_path, "--target_faces", budget]


def _run_candidate(obj: Path, adir: Path, profile: str, budget: int,
                   a, ref_path: Path, log: Path) -> dict:
    stem = f"{profile}_b{budget}"
    fbx = adir / f"{stem}.fbx"
    gate_json = adir / f"{stem}.gate.json"
    rc = _blender(RUNNER, _runner_args(obj, fbx, budget, gate_json, a), log, a.timeout)
    if rc == 3:
        return {"status": "gate_fail", "budget": budget, "rc": rc}
    if rc != 0 or not fbx.exists() or fbx.stat().st_size < MIN_FBX_BYTES:
        return {"status": "failed", "budget": budget, "rc": rc,
                "reason": "runner 失败或 FBX 过小"}
    vjson = adir / f"{stem}.verify.json"
    vpng = adir / f"{stem}.compare.png"
    vlit = adir / f"{stem}.lit.png"
    vrc = _blender(VERIFY, _verify_args(obj, fbx, budget, vjson, vpng, vlit, ref_path), log, a.timeout)
    if vrc != 0 or not vjson.exists():
        return {"status": "failed", "budget": budget, "rc": vrc, "reason": "验收脚本失败"}
    ver = json.loads(vjson.read_text(encoding="utf-8"))
    return {"status": "ok", "budget": budget, "fbx": fbx, "png": vpng,
            "lit": vlit, "ver": ver}


def _save_profile(out_path: Path, chosen: dict, profile: str, history: list) -> dict:
    _remove_bundle(out_path)
    shutil.copyfile(chosen["fbx"], out_path)
    rep = {"pipeline": "v2", "profile": profile, "budget": chosen["budget"],
           "verdict": chosen["ver"]["verdict"], "history": history,
           "verify": chosen["ver"]}
    out_path.with_suffix(".quality.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    if chosen["png"].exists():
        shutil.copyfile(chosen["png"], out_path.with_suffix(".compare.png"))
    if chosen["lit"].exists():
        shutil.copyfile(chosen["lit"], out_path.with_suffix(".lit.png"))
    status = "ok" if chosen["ver"]["verdict"] == "PASS" else "needs_review"
    return {"status": status, "path": str(out_path), "verdict": chosen["ver"]["verdict"],
            "budget": chosen["budget"],
            "faces": chosen["ver"]["geometry"]["intrinsic"]["faces"],
            "size_mb": round(out_path.stat().st_size / 1024 / 1024, 2),
            "report": rep}


def _build_v2(obj: Path, out_path: Path, adir: Path, a, ref_path: Path, log: Path) -> dict:
    t0 = time.time()
    if out_path.exists() and out_path.stat().st_size >= MIN_FBX_BYTES and not a.force:
        rep = _load_report(out_path)
        return {"status": "skipped", "reason": "V2 已存在", "path": str(out_path),
                "report": rep, "seconds": round(time.time() - t0, 1)}
    shutil.rmtree(adir, ignore_errors=True)
    adir.mkdir(parents=True, exist_ok=True)
    budgets = list(range(a.target_faces, a.max_faces + 1, a.step))
    if not budgets:
        return {"status": "failed", "reason": "V2 预算阶梯为空"}
    history, best, stalled = [], None, 0
    for b in budgets:
        print(f"  [V2][{b}] build", flush=True)
        cand = _run_candidate(obj, adir, "v2", b, a, ref_path, log)
        if cand["status"] == "gate_fail":
            history.append({"budget": b, "stage": "gate", "verdict": "FAIL"})
            print(f"  [V2][{b}] 质量门未通过 -> 升面", flush=True)
            continue
        if cand["status"] != "ok":
            return {"status": "failed", "reason": cand.get("reason", "candidate failed"),
                    "budget": b, "log": str(log)}
        ver = cand["ver"]
        key = _quality_key(ver)
        history.append({"budget": b, "stage": "verify", "verdict": ver["verdict"],
                        "faces": ver["geometry"]["intrinsic"]["faces"],
                        "lit_tex": ver.get("lit", {}).get("lit_tex_err"),
                        "lit_near_tex_p95": ver.get("lit", {}).get("lit_near_tex_p95")})
        print(f"  [V2][{b}] {ver['verdict']} faces={history[-1]['faces']}", flush=True)
        if best is None or key < best["key"]:
            best = {"key": key, **cand}
            stalled = 0
        else:
            stalled += 1
        if ver["verdict"] == "PASS":
            break
        if stalled >= 2:
            print(f"  [V2] 连续 {stalled} 档无改善 -> 停止", flush=True)
            break
    if best is None:
        return {"status": "failed", "reason": "所有 V2 预算都未通过质量门",
                "history": history, "log": str(log)}
    res = _save_profile(out_path, best, "v2", history)
    res["seconds"] = round(time.time() - t0, 1)
    return res


def _lite_accepts(baseline_lit, ver, base_faces, rel) -> tuple[bool, str]:
    if ver["verdict"] != "PASS":
        return False, "验收非 PASS"
    lit = ver.get("lit", {})
    if "lit_tex_err" not in lit:
        return False, "缺少 lit 指标"
    faces = ver["geometry"]["intrinsic"]["faces"]
    if faces >= base_faces * 0.95:
        return False, "面数未明显降低"
    for k, slack in LITE_KEYS:
        if k not in lit or k not in baseline_lit:
            return False, f"缺少 {k}"
        if lit[k] > baseline_lit[k] * rel + slack:
            return False, f"{k}={lit[k]:.4f} > {baseline_lit[k]*rel+slack:.4f}"
    return True, "达标"


def _build_lite(obj: Path, out_path: Path, adir: Path, a,
                ref_path: Path, log: Path, v2_report: dict) -> dict:
    t0 = time.time()
    if out_path.exists() and out_path.stat().st_size >= MIN_FBX_BYTES and not a.force:
        return {"status": "skipped", "reason": "V2Lite 已存在", "path": str(out_path)}
    _remove_bundle(out_path)                      # 先清旧 Lite，避免不达标时残留
    if not v2_report or "verify" not in v2_report:
        return {"status": "skipped", "reason": "V2 报告缺失，无法判定 Lite"}
    v2_verify = v2_report["verify"]
    baseline_lit = v2_verify.get("lit", {})
    if "lit_tex_err" not in baseline_lit:
        return {"status": "skipped", "reason": "V2 报告缺少 lit 指标"}
    base_faces = v2_verify["geometry"]["intrinsic"]["faces"]
    budgets = sorted({int(x) for x in a.lite_budgets.split(",") if x.strip()})
    budgets = [b for b in budgets if 4 <= b < a.target_faces]
    if not budgets:
        return {"status": "skipped", "reason": "没有低于主预算的 Lite 候选"}
    shutil.rmtree(adir, ignore_errors=True)
    adir.mkdir(parents=True, exist_ok=True)
    history = []
    for b in budgets:
        print(f"  [Lite][{b}] build", flush=True)
        cand = _run_candidate(obj, adir, "lite", b, a, ref_path, log)
        if cand["status"] == "gate_fail":
            history.append({"budget": b, "status": "gate_fail"})
            print(f"  [Lite][{b}] 质量门未通过", flush=True)
            continue
        if cand["status"] != "ok":
            history.append({"budget": b, "status": "failed", "reason": cand.get("reason")})
            continue
        ok, why = _lite_accepts(baseline_lit, cand["ver"], base_faces, a.lite_rel)
        history.append({"budget": b, "status": "accepted" if ok else "rejected",
                        "reason": why, "faces": cand["ver"]["geometry"]["intrinsic"]["faces"],
                        "verdict": cand["ver"]["verdict"]})
        print(f"  [Lite][{b}] {'✓' if ok else '✗'} {why}", flush=True)
        if ok:
            res = _save_profile(out_path, cand, "lite", history)
            res["seconds"] = round(time.time() - t0, 1)
            return res
    _remove_bundle(out_path)
    return {"status": "skipped", "reason": "没有合格的 Lite 预算", "history": history,
            "seconds": round(time.time() - t0, 1)}


def _process_job(job, a):
    tag, bname, sname, zip_path, mdir, note = job
    if zip_path is None:
        return {"tag": tag, "status": "skipped", "reason": note}
    slug = tag.replace("/", "_")
    src_dir = WORK / slug
    v2_attempts = WORK / f"{slug}_v2_attempts"
    lite_attempts = WORK / f"{slug}_lite_attempts"
    ref_path = REF_DIR / f"{slug}.npz"
    log = LOG_DIR / f"{slug}.log"
    v2_out = _out_path(mdir, bname, sname, V2_SUFFIX)
    lite_out = _out_path(mdir, bname, sname, LITE_SUFFIX)
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        if a.force and ref_path.exists():
            ref_path.unlink()
        obj = extract(zip_path, src_dir)
        v2_res = _build_v2(obj, v2_out, v2_attempts, a, ref_path, log)
        lite_res = None
        if a.mode in ("lite", "both"):
            v2_report = v2_res.get("report") or _load_report(v2_out)
            lite_res = _build_lite(obj, lite_out, lite_attempts, a, ref_path, log, v2_report)
        return {"tag": tag, "status": v2_res["status"], "v2": v2_res, "lite": lite_res}
    except Exception as exc:
        with log.open("a", encoding="utf-8", errors="replace") as lf:
            lf.write("\n[HOST EXCEPTION]\n")
            traceback.print_exc(file=lf)
        return {"tag": tag, "status": "failed", "reason": str(exc), "log": str(log)}
    finally:
        shutil.rmtree(src_dir, ignore_errors=True)
        shutil.rmtree(v2_attempts, ignore_errors=True)
        shutil.rmtree(lite_attempts, ignore_errors=True)


def main():
    p = argparse.ArgumentParser(description="V2 / V2Lite 批量烘焙管线")
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--mode", default="both", choices=["v2", "lite", "both"],
                   help="v2=只生成主版本；lite=确保 V2 后生成 Lite；both=两者都做（默认）")
    p.add_argument("--only", default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--jobs", type=int, default=2, help="并行模型数")
    p.add_argument("--target_faces", type=int, default=3000)
    p.add_argument("--max_faces", type=int, default=3000)
    p.add_argument("--step", type=int, default=1000)
    p.add_argument("--cage", type=float, default=0.02)
    p.add_argument("--res", type=int, default=4096)
    p.add_argument("--samples", type=int, default=64)
    p.add_argument("--device", default="GPU", choices=["CPU", "GPU"])
    p.add_argument("--accept", default="PASS", choices=["PASS", "WARN"])
    p.add_argument("--timeout", type=int, default=5400)
    p.add_argument("--lite_budgets", default="1000,1500,2000,2500")
    p.add_argument("--lite_rel", type=float, default=1.25,
                   help="Lite 各项光照指标允许达到 V2 的倍数")
    a = p.parse_args()

    root = a.root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"根目录不存在: {root}")
    for f in (BLENDER, RUNNER, VERIFY):
        if not Path(f).exists():
            raise FileNotFoundError(f"缺少依赖: {f}")
    WORK.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    REF_DIR.mkdir(parents=True, exist_ok=True)

    jobs = list(iter_jobs(root))
    selected = [j for j in jobs if not a.only or a.only in j[0]]
    if a.limit:
        selected = selected[:a.limit]
    print(f"根目录: {root}")
    print(f"模式: {a.mode} | 任务: {len(selected)} / {len(jobs)} | "
          f"主预算: {a.target_faces}-{a.max_faces} | Lite: {a.lite_budgets}", flush=True)

    if a.dry_run:
        for tag, bname, sname, z, mdir, note in selected:
            if z is None:
                print(f"[{tag}] {note}", flush=True)
                continue
            print(f"[{tag}] {note}")
            print(f"        V2   -> {_out_path(mdir, bname, sname, V2_SUFFIX)}")
            if a.mode in ("lite", "both"):
                print(f"        Lite -> {_out_path(mdir, bname, sname, LITE_SUFFIX)}（需光照验收）")
        return 0

    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        results = list(ex.map(lambda j: _process_job(j, a), selected))

    print("\n" + "=" * 78)
    for r in results:
        v2 = r.get("v2", {})
        lite = r.get("lite")
        print(f"[{r['tag']}] {r['status']} | "
              f"V2={v2.get('status')} verdict={v2.get('verdict','-')} "
              f"faces={v2.get('faces','-')} budget={v2.get('budget','-')}")
        if lite:
            print(f"        Lite={lite.get('status')} verdict={lite.get('verdict','-')} "
                  f"faces={lite.get('faces','-')} budget={lite.get('budget','-')} "
                  f"reason={lite.get('reason', '-')}")
    failed = [r for r in results if r["status"] == "failed"]
    print(f"\n汇总: failed={len(failed)} / {len(results)}")
    if failed:
        for r in failed:
            print(f"  - {r['tag']}: {r.get('reason')}  log={r.get('log','-')}")
    print(f"日志目录: {LOG_DIR}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
