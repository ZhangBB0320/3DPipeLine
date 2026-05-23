#!/usr/bin/env python3
"""
外部减面脚本 — 独立于 Blender 的 mesh simplification。

使用 Open3D 的 simplify_quadric_decimation 实现二次误差度量减面，
不依赖 Blender，可独立运行。

用法：
    # CLI
    python3 Scripts/decimate_external.py input.obj -o output.obj -t 3000

    # Python API
    from decimate_external import decimate_obj
    decimate_obj("input.obj", "output.obj", target_faces=3000)

输入：高模 OBJ 文件路径 + 目标面数
输出：低模 OBJ 文件
"""

import os
import sys
import argparse
import numpy as np


def _load_obj_open3d(filepath: str):
    """使用 Open3D 加载 OBJ，返回 (vertices_np, faces_np)。"""
    import open3d as o3d

    mesh = o3d.io.read_triangle_mesh(filepath)
    if not mesh.has_vertices():
        raise RuntimeError(f"Failed to load OBJ: {filepath}")
    verts = np.asarray(mesh.vertices, dtype=np.float32)
    faces = np.asarray(mesh.triangles, dtype=np.int32)
    return verts, faces


def _save_obj(filepath: str, vertices: np.ndarray, faces: np.ndarray):
    """保存为 OBJ 文件（纯 numpy 写入，避免 Open3D write_triangle_mesh 在 macOS 上段错误）。"""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    with open(filepath, "w") as f:
        f.write("# Decimated mesh (Open3D Quadric)\n")
        for v in vertices:
            f.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
        for face in faces:
            # OBJ 索引从 1 开始
            f.write(f"f {face[0]+1} {face[1]+1} {face[2]+1}\n")
    print(f"    Saved: {filepath} ({len(vertices)} verts, {len(faces)} faces)")


def decimate_obj(
    input_path: str,
    output_path: str,
    target_faces: int,
    verbose: bool = True,
) -> dict:
    """
    使用 Open3D 对 OBJ 模型进行外部减面。

    Parameters
    ----------
    input_path : str
        输入高模 OBJ 文件路径
    output_path : str
        输出低模 OBJ 文件路径
    target_faces : int
        目标面数
    verbose : bool
        是否输出详细日志

    Returns
    -------
    dict : 减面结果摘要
    """
    import open3d as o3d

    if verbose:
        print("=" * 70)
        print("EXTERNAL DECIMATE: Open3D quadric decimation")
        print(f"  Input:  {input_path}")
        print(f"  Output: {output_path}")
        print(f"  Target: {target_faces} faces")
        print("=" * 70)

    # 加载
    if verbose:
        print("    Loading OBJ...")
    mesh = o3d.io.read_triangle_mesh(input_path)
    if not mesh.has_vertices():
        raise RuntimeError(f"Failed to load OBJ: {input_path}")

    n_input_verts = len(mesh.vertices)
    n_input_faces = len(mesh.triangles)
    if verbose:
        print(f"    Loaded: {n_input_verts} verts, {n_input_faces} faces")

    if n_input_faces <= target_faces:
        print(f"    [SKIP] 面数 {n_input_faces} 已 ≤ 目标 {target_faces}，直接复制")
        _save_obj(output_path, np.asarray(mesh.vertices), np.asarray(mesh.triangles))
        return {
            "input_path": input_path,
            "output_path": output_path,
            "input_verts": n_input_verts,
            "input_faces": n_input_faces,
            "output_verts": n_input_verts,
            "output_faces": n_input_faces,
            "reduction_ratio": 1.0,
        }

    # 减面 — Open3D 的 simplify_quadric_decimation 可精确控制目标三角形数
    if verbose:
        print(f"    Simplifying to {target_faces} faces...")

    simplified = mesh.simplify_quadric_decimation(target_number_of_triangles=target_faces)

    n_output_verts = len(simplified.vertices)
    n_output_faces = len(simplified.triangles)
    actual_ratio = n_output_faces / n_input_faces

    if verbose:
        print(f"    Result: {n_output_verts} verts, {n_output_faces} faces (ratio={actual_ratio:.4f})")

    # 保存
    out_verts = np.asarray(simplified.vertices, dtype=np.float32)
    out_faces = np.asarray(simplified.triangles, dtype=np.int32)

    # Open3D 读 FBX 时会做 Y↔Z 轴交换（FBX Y-up → Open3D Z-up），
    # 读 OBJ 时不会。为保持输出一致（Y-up，与 Blender OBJ 导出相同），
    # 当输入是 FBX 时需要转回 Y-up：交换 Y↔Z 并翻转 Z 符号。
    input_ext = os.path.splitext(input_path)[1].lower()
    if input_ext == ".fbx":
        out_verts = out_verts[:, [0, 2, 1]].copy()
        out_verts[:, 2] *= -1
        if verbose:
            print("    Axis fix: FBX input detected, converted Z-up → Y-up for OBJ output")

    _save_obj(output_path, out_verts, out_faces)

    result = {
        "input_path": input_path,
        "output_path": output_path,
        "input_verts": n_input_verts,
        "input_faces": n_input_faces,
        "output_verts": n_output_verts,
        "output_faces": n_output_faces,
        "reduction_ratio": actual_ratio,
    }

    if verbose:
        print(f"\n[DONE] External decimation complete:")
        print(f"    {n_input_faces} → {n_output_faces} faces ({1-actual_ratio:.1%} reduction)")
        print(f"    {n_input_verts} → {n_output_verts} verts")

    return result


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="外部减面脚本 — Open3D quadric decimation"
    )
    parser.add_argument("input", help="输入高模 OBJ 文件路径")
    parser.add_argument("-o", "--output", required=True, help="输出低模 OBJ 文件路径")
    parser.add_argument(
        "-t", "--target-faces", type=int, required=True,
        help="目标面数",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式")

    args = parser.parse_args()
    decimate_obj(
        input_path=args.input,
        output_path=args.output,
        target_faces=args.target_faces,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
