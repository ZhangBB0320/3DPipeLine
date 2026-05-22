#!/usr/bin/env python3
"""
外部减面脚本 — pyfqmr (Fast Quadric Mesh Reduction) 实现。

使用 C++ 加速的二次误差度量边折叠，比 Open3D 更快，
支持法线保持，适合大模型快速减面。

用法：
    # CLI
    python3 Scripts/decimation/decimate_pyfqmr.py input.obj -o output.obj -t 3000

    # Python API
    from decimate_pyfqmr import decimate_pyfqmr
    decimate_pyfqmr("input.obj", "output.obj", target_faces=3000)

输入：高模 OBJ 文件路径 + 目标面数
输出：低模 OBJ 文件
"""

import os
import sys
import argparse
import numpy as np


def decimate_pyfqmr(
    input_path: str,
    output_path: str,
    target_faces: int,
    verbose: bool = True,
) -> dict:
    """
    使用 pyfqmr 对 OBJ 模型进行外部减面。

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
    from pyfqmr import Simplify

    if verbose:
        print("=" * 70)
        print("EXTERNAL DECIMATE: pyfqmr (Fast Quadric Mesh Reduction)")
        print(f"  Input:  {input_path}")
        print(f"  Output: {output_path}")
        print(f"  Target: {target_faces} faces")
        print("=" * 70)

    # 加载 OBJ
    if verbose:
        print("    Loading OBJ...")
    mesh = o3d.io.read_triangle_mesh(input_path)
    if not mesh.has_vertices():
        raise RuntimeError(f"Failed to load OBJ: {input_path}")

    verts = np.asarray(mesh.vertices, dtype=np.float32)
    faces = np.asarray(mesh.triangles, dtype=np.int32)
    n_input_verts = len(verts)
    n_input_faces = len(faces)
    if verbose:
        print(f"    Loaded: {n_input_verts} verts, {n_input_faces} faces")

    if n_input_faces <= target_faces:
        print(f"    [SKIP] 面数 {n_input_faces} 已 ≤ 目标 {target_faces}，直接复制")
        o3d.io.write_triangle_mesh(output_path, mesh)
        return {
            "input_path": input_path,
            "output_path": output_path,
            "input_verts": n_input_verts,
            "input_faces": n_input_faces,
            "output_verts": n_input_verts,
            "output_faces": n_input_faces,
            "reduction_ratio": 1.0,
        }

    # pyfqmr 减面
    if verbose:
        print(f"    Simplifying to {target_faces} faces with pyfqmr...")

    mesh_simplifier = Simplify()
    mesh_simplifier.setMesh(verts, faces)
    mesh_simplifier.simplify_mesh(
        target_count=target_faces,
        aggressiveness=7,       # 保形激进程度 (默认7, 越高越激进)
        preserve_border=False,  # 不保留边界边（否则减面下限偏高）
        verbose=False,
    )

    out_verts, out_faces, _ = mesh_simplifier.getMesh()

    n_output_verts = len(out_verts)
    n_output_faces = len(out_faces)
    actual_ratio = n_output_faces / n_input_faces

    if verbose:
        print(f"    Result: {n_output_verts} verts, {n_output_faces} faces "
              f"(ratio={actual_ratio:.4f})")

    # 保存
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    out_mesh = o3d.geometry.TriangleMesh()
    out_mesh.vertices = o3d.utility.Vector3dVector(out_verts)
    out_mesh.triangles = o3d.utility.Vector3iVector(out_faces)
    o3d.io.write_triangle_mesh(output_path, out_mesh)
    if verbose:
        print(f"    Saved: {output_path}")

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
        print(f"\n[DONE] pyfqmr decimation complete:")
        print(f"    {n_input_faces} → {n_output_faces} faces ({1-actual_ratio:.1%} reduction)")
        print(f"    {n_input_verts} → {n_output_verts} verts")

    return result


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="外部减面脚本 — pyfqmr (Fast Quadric Mesh Reduction)"
    )
    parser.add_argument("input", help="输入高模 OBJ 文件路径")
    parser.add_argument("-o", "--output", required=True, help="输出低模 OBJ 文件路径")
    parser.add_argument(
        "-t", "--target-faces", type=int, required=True,
        help="目标面数",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式")

    args = parser.parse_args()
    decimate_pyfqmr(
        input_path=args.input,
        output_path=args.output,
        target_faces=args.target_faces,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
