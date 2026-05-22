#!/usr/bin/env python3
"""
外部减面脚本 — Open3D Vertex Clustering（顶点聚类体素化）。

使用 Open3D 的 simplify_vertex_clustering 实现体素化聚类减面，
与二次误差边折叠（Quadric）是完全不同的减面思路：
  - Quadric: 逐步折叠误差最小的边
  - Vertex Clustering: 将空间划分为体素网格，每个体素内的顶点合并为一个

适合作为对比基准，验证不同减面策略的效果差异。

用法：
    # CLI
    python3 Scripts/decimation/decimate_cluster.py input.obj -o output.obj -t 3000

    # Python API
    from decimate_cluster import decimate_cluster
    decimate_cluster("input.obj", "output.obj", target_faces=3000)

输入：高模 OBJ 文件路径 + 目标面数
输出：低模 OBJ 文件
"""

import os
import sys
import math
import argparse
import numpy as np


def decimate_cluster(
    input_path: str,
    output_path: str,
    target_faces: int,
    verbose: bool = True,
) -> dict:
    """
    使用 Open3D Vertex Clustering 对 OBJ 模型进行外部减面。

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
        print("EXTERNAL DECIMATE: Open3D Vertex Clustering")
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

    verts = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.triangles)
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

    # 二分法搜索 voxel_size
    # Vertex Clustering 的 voxel_size 越大 → 面数越少
    # 面数 ∝ (1/voxel_size)^2 粗略估计
    if verbose:
        print(f"    Binary search for voxel_size (target={target_faces})...")

    # 计算模型尺寸
    mins = verts.min(axis=0)
    maxs = verts.max(axis=0)
    diag = np.linalg.norm(maxs - mins)

    lo_voxel = diag / 1000.0  # 小 voxel → 面多
    hi_voxel = diag / 10.0    # 大 voxel → 面少
    voxel_size = diag / math.sqrt(target_faces * 2)  # 初始估计

    max_attempts = 20
    best_result = None
    best_diff = float('inf')
    stagnation_count = 0
    last_face_count = None

    for attempt in range(1, max_attempts + 1):
        simplified = mesh.simplify_vertex_clustering(
            voxel_size=voxel_size,
            contraction=o3d.geometry.SimplificationContraction.Average,
        )
        actual_faces = len(simplified.triangles)
        diff = abs(actual_faces - target_faces) / max(target_faces, 1)

        if verbose:
            print(f"    Attempt {attempt}: voxel_size={voxel_size:.6f} → "
                  f"{len(simplified.vertices)}v {actual_faces}f (diff={diff:.1%})")

        # 记录最接近目标的结果
        if diff < best_diff:
            best_diff = diff
            best_result = (np.asarray(simplified.vertices), np.asarray(simplified.triangles))

        if diff <= 0.1:  # 10% 误差内
            if verbose:
                print(f"    ✓ 面数达标: {actual_faces} (误差 {diff:.1%})")
            break

        # 检测停滞
        if actual_faces == last_face_count:
            stagnation_count += 1
            if stagnation_count >= 3:
                if verbose:
                    print(f"    ⚠ 检测到停滞：连续 {stagnation_count} 次面数相同 ({actual_faces})")
                break
        else:
            stagnation_count = 0
        last_face_count = actual_faces

        # 调整二分边界
        if actual_faces > target_faces:
            lo_voxel = voxel_size
        else:
            hi_voxel = voxel_size
        voxel_size = (lo_voxel + hi_voxel) / 2.0

    # 使用最佳结果
    out_verts, out_faces = best_result
    n_output_verts = len(out_verts)
    n_output_faces = len(out_faces)
    actual_ratio = n_output_faces / n_input_faces

    # 保存
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    out_mesh = o3d.geometry.TriangleMesh()
    out_mesh.vertices = o3d.utility.Vector3dVector(out_verts)
    out_mesh.triangles = o3d.utility.Vector3iVector(out_faces.astype(np.int32))
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
        print(f"\n[DONE] Vertex Clustering decimation complete:")
        print(f"    {n_input_faces} → {n_output_faces} faces ({1-actual_ratio:.1%} reduction)")
        print(f"    {n_input_verts} → {n_output_verts} verts")

    return result


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="外部减面脚本 — Open3D Vertex Clustering"
    )
    parser.add_argument("input", help="输入高模 OBJ 文件路径")
    parser.add_argument("-o", "--output", required=True, help="输出低模 OBJ 文件路径")
    parser.add_argument(
        "-t", "--target-faces", type=int, required=True,
        help="目标面数",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式")

    args = parser.parse_args()
    decimate_cluster(
        input_path=args.input,
        output_path=args.output,
        target_faces=args.target_faces,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
