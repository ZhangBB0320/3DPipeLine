"""
MeshLab 减面脚本 - 使用 pymeshlab
流程: 拓扑修复 -> 各向同性重网格化 -> 减面
输出结构: outPut/<日期>/模型名.obj (纯几何，无材质/纹理)
"""
import pymeshlab
import argparse
from pathlib import Path
from datetime import datetime


def repair_mesh(ms):
    """修复 AI 生成模型的常见拓扑问题"""
    print("\n--- 阶段1: 拓扑修复 ---")

    mesh = ms.current_mesh()
    print(f"修复前面数: {mesh.face_number()}, 顶点数: {mesh.vertex_number()}")

    # 1. 移除重复顶点
    ms.meshing_remove_duplicate_vertices()
    print("  [OK] 移除重复顶点")

    # 2. 移除重复面
    ms.meshing_remove_duplicate_faces()
    print("  [OK] 移除重复面")

    # 3. 移除未引用顶点
    ms.meshing_remove_unreferenced_vertices()
    print("  [OK] 移除未引用顶点")

    # 4. 移除零面积退化面
    ms.meshing_remove_null_faces()
    print("  [OK] 移除退化面")

    # 5. 修复非流形顶点（AI模型常见：多个不连通的面共享一个顶点）
    ms.meshing_repair_non_manifold_vertices()
    print("  [OK] 修复非流形顶点")

    # 6. 修复非流形边（一条边被3个以上面共享）
    ms.meshing_repair_non_manifold_edges()
    print("  [OK] 修复非流形边")

    # 7. 统一面朝向（AI模型常见法线不一致）
    ms.meshing_re_orient_faces_coherently()
    print("  [OK] 统一面朝向")

    mesh = ms.current_mesh()
    print(f"修复后面数: {mesh.face_number()}, 顶点数: {mesh.vertex_number()}")


def remesh_isotropic(ms, target_edge_len_pct=1.0, iterations=3):
    """各向同性重网格化 - 重建均匀网格拓扑，消除退化和自交"""
    print("\n--- 阶段2: 各向同性重网格化 ---")
    mesh = ms.current_mesh()
    print(f"重网格化前面数: {mesh.face_number()}")

    ms.meshing_isotropic_explicit_remeshing(
        targetlen=pymeshlab.PercentageValue(target_edge_len_pct),
        iterations=iterations,
        featuredeg=30,
        adaptive=False,
        collapseflag=True,
        swapflag=True,
        smoothflag=True,
        maxsurfdist=pymeshlab.PercentageValue(0.1),
    )
    print(f"重网格化后面数: {ms.current_mesh().face_number()}")


def close_holes(ms, max_hole_size=30):
    """修补孔洞"""
    print("\n--- 阶段2.5: 修补孔洞 ---")
    ms.meshing_close_holes(
        maxholesize=max_hole_size,
        selfintersection=True,
        newfaceselected=False,
    )
    print(f"补洞后面数: {ms.current_mesh().face_number()}")


def decimate(ms, target_faces=3000):
    """Quadric Edge Collapse 减面"""
    print(f"\n--- 阶段3: 减面到 {target_faces} ---")
    mesh = ms.current_mesh()
    before = mesh.face_number()
    print(f"减面前面数: {before}")

    if before <= target_faces:
        print(f"当前面数({before})已小于目标({target_faces})，跳过减面")
        return

    ms.apply_filter('meshing_decimation_quadric_edge_collapse',
                    targetfacenum=target_faces,
                    qualitythr=0.5,
                    preservetopology=False,
                    optimalplacement=True,
                    planarquadric=True,
                    selected=False)

    result = ms.current_mesh()
    after = result.face_number()
    print(f"减面后面数: {after}, 顶点数: {result.vertex_number()}")
    reduction = (1 - after / before) * 100
    print(f"减面比例: {reduction:.1f}%")


def process(input_path, output_base_dir, target_faces=3000):
    print(f"输入模型: {input_path}")
    print(f"目标面数: {target_faces}")

    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(input_path)

    mesh = ms.current_mesh()
    original_faces = mesh.face_number()
    original_verts = mesh.vertex_number()
    print(f"原始面数: {original_faces}, 原始顶点数: {original_verts}")

    # 阶段1: 拓扑修复
    repair_mesh(ms)

    # 阶段2: 各向同性重网格化（根据目标面数估算边长）
    current_faces = ms.current_mesh().face_number()
    if current_faces > 0:
        remesh_target = target_faces * 3
        ratio = remesh_target / current_faces
        edge_pct = (1.0 / ratio ** 0.5) * 100.0
        edge_pct = max(0.5, min(edge_pct, 50.0))
    else:
        edge_pct = 1.0

    remesh_isotropic(ms, target_edge_len_pct=edge_pct, iterations=3)

    # 阶段2.5: 补洞
    close_holes(ms, max_hole_size=30)

    # 阶段3: 减面
    decimate(ms, target_faces)

    # 保存输出
    out_dir = Path(output_base_dir) / datetime.now().strftime("%Y%m%d")
    out_dir.mkdir(parents=True, exist_ok=True)

    model_name = Path(input_path).stem
    # pymeshlab 不支持中文路径输出，先临时名再重命名
    tmp_obj = str(out_dir / "tmp_output.obj")
    ms.save_current_mesh(tmp_obj)

    final_obj = out_dir / f"{model_name}.obj"
    if final_obj.exists():
        final_obj.unlink()

    # 清理 OBJ 中的材质引用行，输出纯几何文件
    tmp_obj_path = Path(tmp_obj)
    clean_lines = [l for l in tmp_obj_path.read_text(encoding='utf-8', errors='ignore').splitlines(keepends=True)
                   if not l.startswith('mtllib ') and not l.startswith('usemtl ')]
    final_obj.write_text(''.join(clean_lines), encoding='utf-8')
    tmp_obj_path.unlink()

    # 清理 pymeshlab 自动生成的附带文件（MTL/PNG 等），只保留 OBJ
    for pattern in ['tmp_output.*', 'dummy.*']:
        for f in out_dir.glob(pattern):
            if f.suffix != '.obj':
                f.unlink()

    print(f"\n已保存OBJ到: {final_obj}")
    file_size = final_obj.stat().st_size
    print(f"文件大小: {file_size / 1024 / 1024:.1f} MB")

    # 打印总结
    final_mesh = ms.current_mesh()
    print(f"\n===== 总结 =====")
    print(f"原始: {original_faces} 面 / {original_verts} 顶点")
    print(f"最终: {final_mesh.face_number()} 面 / {final_mesh.vertex_number()} 顶点")
    print(f"总减面: {(1 - final_mesh.face_number() / original_faces) * 100:.1f}%")
    print(f"输出格式: OBJ (纯几何，无材质/纹理)")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='MeshLab 减面脚本: 拓扑修复 -> 重网格化 -> 减面',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='示例:\n'
               '  python decimate.py ./model.obj 3000\n'
               '  python decimate.py /path/to/model.obj 5000 /path/to/output\n'
        )
    parser.add_argument('input', help='输入模型路径 (OBJ/FBX/STL/PLY 等)')
    parser.add_argument('target_faces', nargs='?', type=int, default=3000,
                        help='目标面数 (默认: 3000)')
    parser.add_argument('output', nargs='?', default=None,
                        help='输出目录 (默认: <项目根>/outPut/)')
    args = parser.parse_args()

    input_file = str(Path(args.input).resolve())
    if not Path(input_file).exists():
        parser.error(f'输入文件不存在: {input_file}')

    if args.output:
        output_base_dir = str(Path(args.output).resolve())
    else:
        # 项目根目录 = Scripts 的上一级
        project_root = Path(__file__).resolve().parent.parent
        output_base_dir = str(project_root / 'outPut')

    process(input_file, output_base_dir, args.target_faces)
