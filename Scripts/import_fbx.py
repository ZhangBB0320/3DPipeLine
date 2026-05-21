#!/usr/bin/env python3
"""
安全导入脚本 — 支持 FBX（防贴图污染）和 OBJ（纯几何）。

为什么需要这个 helper？
=======================
Blender FBX importer 通过 `image.name` 决定是否复用内存图像。
连续导入 building1.fbx → building3.fbx 时，因为两份 fbx 内嵌纹理同名
（如 `texture_pbr_20250901.png`），Blender 直接复用 building1 的内存图，
不解压 building3 的内嵌纹理 → building3 显示 building1 的贴图（污染）。

本 helper 在每次 FBX 导入**之前**主动把所有"未隔离"的 image 加
`__guard_<ts>__` 前缀 + 清空 filepath，切断按名复用路径，从而保证
每次导入都创建全新的 image data-block。

OBJ 导入不存在贴图污染问题，但为保持 API 一致性，同样走统一入口。

与 `safe_fbx_import_addon.py` 的关系
====================================
- `safe_fbx_import_addon.py` —— monkey-patch Blender 的 FBX import operator，
  适合**用户手动在 GUI 里 File→Import→FBX**的交互场景。
- 本文件（import_fbx.py）—— 显式 API，适合**批量自动化脚本**调用，
  自包含、不依赖 addon 是否启用。

二者底层用的是同一套隔离逻辑（`SAFE_PREFIXES`）。

适用场景
========
1. **批量导入 + 烘焙**（核心场景）
   ```python
   from import_fbx import safe_import, batch_import_and_run
   from bake import bake_high_to_low

   def per_object(obj_name, idx):
       bake_high_to_low(
           high_obj_name=obj_name,
           name=f"Building{idx+1}",
           target_faces=3000, cage=0.1, res=4096, samples=64, device="GPU",
       )

   batch_import_and_run(
       file_paths=[
           "/Users/zbb/Downloads/Building/building1.fbx",
           "/Users/zbb/Downloads/Building/building3.fbx",
       ],
       per_object_callback=per_object,
   )
   ```

2. **单文件安全导入**
   ```python
   from import_fbx import safe_import
   new_objs = safe_import("/path/to/some.fbx")
   new_objs = safe_import("/path/to/some.obj")
   ```

3. **CLI**（通过 blender_connect.py 推送）
   ```bash
   python3 -c "
   from blender_connect import send_python
   send_python('''
   import sys; sys.path.insert(0, \"/Users/zbb/3DPipeLine/Scripts\")
   from import_fbx import safe_import
   safe_import(\"/path/to/some.fbx\")
   ''')
   "
   ```
"""
import os
import sys
import time


# 已视为"安全"的 image 名前缀（这些图不会与新导入的 FBX 冲突）
SAFE_PREFIXES = ("__baked_", "__residual__", "__guard_", "bake_")
PROTECTED_NAMES = {"Render Result", "Viewer Node"}


# ============================================================
# 核心：导入前隔离同名 image（仅 FBX 需要，OBJ 不触发）
# ============================================================
def isolate_existing_images(verbose: bool = True) -> int:
    """
    扫描 `bpy.data.images`，把所有不带安全前缀的 image 加上 `__guard_<ts>__`
    前缀并清空 filepath，切断 FBX 导入器的"按名复用"路径。

    Returns
    -------
    int : 被隔离的 image 数量
    """
    import bpy

    ts = time.strftime("%Y%m%d_%H%M%S")
    guard_prefix = f"__guard_{ts}__"
    n = 0
    for img in list(bpy.data.images):
        nm = img.name
        if nm in PROTECTED_NAMES:
            continue
        if nm.startswith(SAFE_PREFIXES):
            continue
        try:
            img.filepath = ""
            img.filepath_raw = ""
            img.name = guard_prefix + nm
            n += 1
            if verbose:
                print(f"    [guard] {nm} -> {img.name}")
        except Exception as e:
            print(f"    [WARN] 隔离 image '{nm}' 失败: {e}")
    return n


# ============================================================
# 内部：根据扩展名选择导入操作
# ============================================================
def _import_file(filepath: str, verbose: bool = True):
    """根据文件扩展名调用 Blender 的 FBX 或 OBJ 导入器。
    返回导入操作本身（由调用方前后对比对象集合）。
    """
    import bpy

    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=filepath)
    elif ext in (".obj",):
        bpy.ops.wm.obj_import(
            filepath=filepath,
            forward_axis="NEGATIVE_Z",
            up_axis="Y",
        )
    else:
        raise ValueError(f"不支持的导入格式: '{ext}'（仅支持 .fbx / .obj）")


# ============================================================
# 单文件导入 API — 统一入口
# ============================================================
def safe_import(
    file_path: str,
    verbose: bool = True,
    rename_to_stem: bool = True,
) -> list:
    """
    安全导入 FBX 或 OBJ 文件。

    - FBX：导入前隔离已有 image，防止贴图污染。
    - OBJ：直接导入（无贴图污染风险）。
    - 通用：导入后按文件名重命名 mesh 对象。

    Parameters
    ----------
    file_path : str
        FBX 或 OBJ 文件绝对路径
    verbose : bool
        是否输出详细日志
    rename_to_stem : bool
        是否把导入的 mesh 对象重命名为文件名（不含扩展名）。
        默认 True。第一个 mesh 对象 → file_stem；后续 mesh →
        file_stem.001 等（Blender 自动加后缀）。

    Returns
    -------
    list[str] : 新导入对象的名称列表（已应用重命名后的最终名称）
    """
    import bpy

    if not os.path.isabs(file_path):
        file_path = os.path.abspath(file_path)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    file_stem = os.path.splitext(os.path.basename(file_path))[0]
    ext = os.path.splitext(file_path)[1].lower()

    print("=" * 70)
    print(f"SAFE IMPORT: {file_path}")
    print("=" * 70)

    # 1) FBX 导入前：隔离所有未受控的 image（OBJ 不需要）
    if ext == ".fbx":
        print("[1] Isolating existing images to prevent reuse pollution...")
        n_iso = isolate_existing_images(verbose=verbose)
        if n_iso > 0:
            print(f"    Isolated {n_iso} pre-existing image(s)")
        else:
            print("    No pre-existing image needs isolation (clean slate)")
    else:
        print("[1] OBJ import — no image isolation needed")

    # 2) 记录导入前对象/图像集合
    pre_objs = {o.name for o in bpy.data.objects}
    pre_imgs = {i.name for i in bpy.data.images}

    # 3) 执行导入
    print(f"[2] Importing {ext.upper()}: {file_path}")
    _import_file(file_path, verbose=verbose)

    # 4) 收集新增对象 / 新增 image（保留导入顺序）
    new_objs = [o.name for o in bpy.data.objects if o.name not in pre_objs]
    new_imgs = [i.name for i in bpy.data.images if i.name not in pre_imgs]

    # 5) 重命名 mesh 对象为文件名（关键功能）
    final_obj_names = list(new_objs)
    if rename_to_stem:
        print(f"[3] Renaming mesh objects to file stem '{file_stem}'...")
        mesh_idx = 0
        for i, nm in enumerate(new_objs):
            obj = bpy.data.objects.get(nm)
            if obj is None or obj.type != "MESH":
                continue
            old = obj.name
            obj.name = file_stem
            actual = obj.name  # Blender 实际确定的名字（可能加 .001）
            final_obj_names[i] = actual
            print(f"    {old} -> {actual}")
            mesh_idx += 1
        if mesh_idx == 0:
            print("    [WARN] no mesh object found to rename")

    # 6) 报告
    print(f"[4] Imported {len(final_obj_names)} object(s):")
    for nm in final_obj_names:
        obj = bpy.data.objects.get(nm)
        if obj and obj.type == "MESH":
            print(f"    - {nm} ({len(obj.data.polygons)} faces)")
        else:
            print(f"    - {nm}")
    if new_imgs:
        print(f"    New image data-blocks: {len(new_imgs)}")
        for n in new_imgs:
            print(f"      - {n}")

    return final_obj_names


# ============================================================
# 向后兼容别名
# ============================================================
def safe_import_fbx(fbx_path: str, verbose: bool = True, rename_to_stem: bool = True) -> list:
    """safe_import 的 FBX 兼容别名。"""
    return safe_import(fbx_path, verbose=verbose, rename_to_stem=rename_to_stem)


def safe_import_fbx_with_rename(
    fbx_path: str,
    rename_to: str,
    verbose: bool = True,
) -> str:
    """
    安全导入 FBX 并把第一个 mesh 对象重命名为 `rename_to`（覆盖默认的文件名命名）。

    适用于需要自定义命名的场景。普通用法直接用 `safe_import(path)` 即可，
    它已经会自动用文件名命名。

    Returns
    -------
    str : 重命名后的 mesh 对象名（即 `rename_to`，若已被占用则会带 .001 后缀）
    """
    import bpy

    new_objs = safe_import(fbx_path, verbose=verbose, rename_to_stem=False)

    target = None
    for nm in new_objs:
        obj = bpy.data.objects.get(nm)
        if obj and obj.type == "MESH":
            target = obj
            break

    if target is None:
        raise RuntimeError(f"文件 '{fbx_path}' 导入后没有 mesh 对象")

    old_name = target.name
    target.name = rename_to
    actual = target.name  # Blender 可能加 .001
    print(f"    Renamed: {old_name} -> {actual}")
    return actual


# ============================================================
# 批量 API：导入 + 用户回调（典型用途：导入并烘焙）
# ============================================================
def batch_import_and_run(
    file_paths=None,
    fbx_paths=None,
    per_object_callback=None,
    rename_pattern: str = None,
    continue_on_error: bool = True,
):
    """
    批量"安全导入 + 用户回调"流水线，专为「批量导入并烘焙」设计。

    每次循环：
      1) 调用 `isolate_existing_images()` 隔离同名 image（FBX 时）
      2) 根据扩展名选择 FBX/OBJ 导入器
      3) 默认按文件名重命名 mesh 对象（如 building1.fbx → 'building1'）
         若提供 `rename_pattern` 则覆盖默认命名
      4) 调用 `per_object_callback(obj_name, idx)` —— 用户在此处做烘焙等操作
      5) 进入下一次循环

    Parameters
    ----------
    file_paths : list[str]
        FBX/OBJ 文件绝对路径列表（推荐，替代 fbx_paths）
    fbx_paths : list[str]
        旧参数名，为向后兼容保留，与 file_paths 合并使用
    per_object_callback : Callable[[str, int], Any]
        每导入一个文件后调用的回调，签名为 (obj_name, index) -> Any。
    rename_pattern : str, optional
        覆盖默认的"文件名"命名。支持 `{idx}` / `{idx_1based}` / `{stem}` 占位符。
        例：`"Building_{idx_1based}"` 或 `"{stem}_high"`。
        若为 None，使用文件名（默认行为）。
    continue_on_error : bool
        某次失败是否继续后续。默认 True。

    Returns
    -------
    list[dict] : 每个文件的处理结果摘要
    """
    import bpy

    # 兼容旧参数名
    paths = file_paths or fbx_paths or []
    if not paths:
        raise ValueError("必须提供 file_paths 或 fbx_paths")

    results = []
    for idx, file_path in enumerate(paths):
        if not os.path.isabs(file_path):
            file_path = os.path.abspath(file_path)
        stem = os.path.splitext(os.path.basename(file_path))[0]

        print()
        print("#" * 70)
        print(f"# BATCH [{idx + 1}/{len(paths)}]: {os.path.basename(file_path)}")
        print("#" * 70)

        try:
            # 安全导入（含隔离 + Blender 导入 + 默认按文件名命名）
            do_default_rename = (rename_pattern is None)
            new_objs = safe_import(file_path, verbose=True, rename_to_stem=do_default_rename)

            # 取第一个 mesh 对象
            target = None
            for nm in new_objs:
                obj = bpy.data.objects.get(nm)
                if obj and obj.type == "MESH":
                    target = obj
                    break
            if target is None:
                raise RuntimeError(f"文件 '{file_path}' 没有 mesh 对象")

            # 用户自定义 rename_pattern 时，覆盖默认命名
            if rename_pattern:
                desired = rename_pattern.format(
                    idx=idx, idx_1based=idx + 1, stem=stem
                )
                old = target.name
                target.name = desired
                print(f"    Custom renamed: {old} -> {target.name}")

            obj_name = target.name

            # 用户回调
            print(f"\n[CALLBACK] {obj_name}")
            cb_result = per_object_callback(obj_name, idx)

            results.append({
                "file": file_path,
                "obj_name": obj_name,
                "callback_result": cb_result,
                "ok": True,
            })

        except Exception as e:
            import traceback
            err_msg = traceback.format_exc()
            print(f"\n[ERROR] {file_path}: {e}\n{err_msg}")
            results.append({
                "file": file_path,
                "ok": False,
                "error": str(e),
            })
            if not continue_on_error:
                print("[BATCH] continue_on_error=False, abort batch")
                break

    # 摘要
    n_ok = sum(1 for r in results if r.get("ok"))
    print()
    print("=" * 70)
    print(f"BATCH SUMMARY: {n_ok}/{len(paths)} succeeded")
    print("=" * 70)
    for r in results:
        flag = "OK " if r.get("ok") else "ERR"
        info = r.get("obj_name") or r.get("error", "")
        print(f"  [{flag}] {os.path.basename(r['file'])} -> {info}")

    return results


# ============================================================
# 便利批量 API：导入并烘焙（典型业务场景一行调用）
# ============================================================
def batch_import_and_bake(
    file_paths=None,
    fbx_paths=None,
    name_pattern: str = "Building{idx_1based}",
    target_faces: int = 3000,
    cage: float = 0.1,
    res: int = 4096,
    samples: int = 64,
    device: str = "GPU",
    continue_on_error: bool = True,
):
    """
    一行调用：批量"安全导入 → 烘焙低模"。

    Parameters
    ----------
    file_paths : list[str]
        待处理的 FBX/OBJ 文件路径列表（推荐）
    fbx_paths : list[str]
        旧参数名，为向后兼容保留
    name_pattern : str
        烘焙批次名模板，支持 `{idx}` / `{idx_1based}` / `{stem}`。
        默认 "Building{idx_1based}" → "Building1", "Building2", ...
    target_faces, cage, res, samples, device : 烘焙参数（透传给 bake_high_to_low）
    continue_on_error : bool

    Returns
    -------
    list[dict]
    """
    # 延迟导入，避免循环依赖
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if "bake" in sys.modules:
        del sys.modules["bake"]
    import bake

    paths = file_paths or fbx_paths or []
    if not paths:
        raise ValueError("必须提供 file_paths 或 fbx_paths")

    def _per_obj(obj_name, idx):
        stem = "obj"
        bake_name = name_pattern.format(
            idx=idx,
            idx_1based=idx + 1,
            stem=stem,
        )
        low_name, faces = bake.bake_high_to_low(
            high_obj_name=obj_name,
            name=bake_name,
            target_faces=target_faces,
            cage=cage,
            res=res,
            samples=samples,
            device=device,
        )
        return {"low": low_name, "faces": faces, "bake_name": bake_name}

    return batch_import_and_run(
        file_paths=paths,
        per_object_callback=_per_obj,
        rename_pattern=None,
        continue_on_error=continue_on_error,
    )


# ============================================================
# CLI 入口
# ============================================================
def main():
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1:]
    else:
        argv = sys.argv[1:]

    if not argv:
        print("Usage: import_fbx.py <file_path> [<file_path2> ...]")
        print("  Supports: .fbx (with anti-pollution) and .obj")
        sys.exit(1)

    if len(argv) == 1:
        safe_import(argv[0])
    else:
        for p in argv:
            safe_import(p)


if __name__ == "__main__":
    main()
