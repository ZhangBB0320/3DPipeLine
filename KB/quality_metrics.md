# V2 / V2Lite 低模烘焙质量体系

> 单入口：`Scripts/v2_pipeline.py`
> 默认数据目录：`/Users/zbb/Desktop/美术风格确定`，可用 `--root` 覆盖。
> 产物：`<建筑名><StyleN>LowModel_V2.fbx`（质量主版本）与可选的 `<建筑名><StyleN>LowModel_V2Lite.fbx`（自适应轻量版）。

## 1. 代码结构

| 文件 | 作用 |
|---|---|
| `Scripts/v2_pipeline.py` | 宿主批量入口：遍历 zip、解压、调度 Blender、V2 生成、Lite 预算搜索、落盘 |
| `Scripts/v2_runner.py` | Blender headless 单模型 runner：导入 → V2 减面质量门 → 烘焙 → 导出 FBX |
| `Scripts/v2_verify.py` | Blender headless 验收：重读高模 OBJ 与导出 FBX，出几何/渲染/光照/UV 报告 |
| `Scripts/v2_bake.py` | V2 烘焙封装：平直着色、小边距 Smart UV、无 Shrinkwrap、cage 0.02 |
| `Scripts/bake_scene.py` | Cycles 烘焙底层：材质节点、EMIT Albedo / Normal、贴图隔离 |
| `Scripts/import_fbx.py` | `safe_import()`：OBJ/FBX 导入与贴图污染隔离 |
| `Scripts/export_model.py` | `export_model()`：FBX 内嵌纹理 + Unity Y-up |
| `Scripts/decimation/decimate_adaptive.py` | 固定 V2 体素阶梯 + 预烘焙质量门 |
| `Scripts/decimation/decimate_strategies.py` | V2 体素重构、QEM COLLAPSE、安全修复 |
| `Scripts/evaluate_quality.py` | 几何保真度与低模自检 |
| `Scripts/render_compare.py` | Workbench 贴图/明暗渲染对比 |
| `Scripts/render_lit.py` | EEVEE 光照对比（含法线贴图与近景机位） |

## 2. 固定 V2 配方

| 项 | 值 | 原因 |
|---|---|---|
| 体素重构 | `voxel_div=700`（退路 500 / 1000 / baseline） | 更细体素保留窗框、栏杆、翅片，几何与贴图误差更低 |
| 减面容差 | ±3% | 面数贴近预算，不会飘到 3000 面以上太多 |
| Shrinkwrap | 关闭 | 吸附到噪声高模会让墙面起伏、贴图变差 |
| cage | 0.02 | 0.01–0.02 为稳定平台区；0.05 会让烘焙射线打到无关表面 |
| Smart UV 边距 | 0.003 | 小岛很多时，0.01 边距会吃掉大半图集空间 |
| 着色 | 平直着色 | 与 FBX 导出 `FACE` 法线一致，保证切线空间法线贴图在 Unity 中不偏 |
| 导出 | FBX 内嵌纹理 + Unity Y-up | 直接交付 Unity |

## 3. 两档产物

### `_V2`（质量主版本）

- 每个有 zip 的模型都会生成。
- 默认预算 3000 面；若质量门失败，可用 `--max_faces` / `--step` 做升面兜底。
- 验收报告：`.quality.json`、`.compare.png`、`.lit.png`。

### `_V2Lite`（自适应轻量版）

- 不依赖任何旧版文件。先生成/读取 `_V2` 的 `lit` 指标作为基线。
- 从低到高尝试 `--lite_budgets`（默认 `1000,1500,2000,2500`）。
- 通过条件：
  1. 验收判级为 PASS；
  2. 面数至少比 V2 低 5%；
  3. 6 项光照指标均不超过 V2 × `--lite_rel`（默认 1.25）+ 微小绝对余量。
- 第一个达标预算落盘为 `_V2Lite`；没有达标预算则不生成 Lite，也不会留下半成品。

## 4. 验收指标

`.quality.json` 的 `verify` 字段包含：

- `geometry`：Hausdorff 双向采样距离、轮廓一致率、法线翻转率、连通块/边界边/碎片壳等；
- `render_compare`：Workbench 4 机位贴图与明暗对比；
- `lit`：EEVEE 4 远景 + 2 近景光照对比，含法线贴图效果，是 V2Lite 判据的数据源；
- `uv`：UV 越界、重叠、覆盖率、4096 贴图下的纹素密度。

EEVEE 首帧渲染前有一次丢弃的预热渲染，用来避免刚导入的 FBX 内嵌贴图尚未上传 GPU 导致的假 FAIL。

## 5. 常用命令

```bash
# 桌面目录全量生成 V2 + 合格 Lite，2 个模型并行
python3 Scripts/v2_pipeline.py --root /Users/zbb/Desktop/美术风格确定 --mode both --jobs 2

# 只生成 V2
python3 Scripts/v2_pipeline.py --mode v2

# 单模型强制重做
python3 Scripts/v2_pipeline.py --mode both --only 学校大楼/Style1 --force

# 只列任务，不跑 Blender
python3 Scripts/v2_pipeline.py --dry-run
```

## 6. 已知边界

- 玻璃幕墙类模型的自阴影来自高模真实浮雕，法线贴图无法完全复现；V2 相对早期流程的近景提升较小属方法上限。
- 当前交付只烘焙 Albedo + Normal；玻璃/金属反射需要额外 ORM 或金属-光滑度贴图。
- `hd_max` 是稀疏离群点指标，不作为 Lite 达标判据；判据使用 p95 与光照渲染指标。
- 所有旧版、legacy、实验性脚本与过时规范文档均已从代码库移除；不再提供 V1 兼容入口。

## 7. 三轮 Debug 验证（`/Users/zbb/Desktop/美术风格确定`）

### 第 1 轮：静态与目录验证
- 全量 `py_compile` 通过；`v2_pipeline.py --dry-run` 识别 10 个 Style 任务，其中 7 个有 zip、3 个无 zip。
- 旧引用扫描最初发现 4 处与已删旧文件同名的函数命名残留，已统一重命名为 `bake_v2`；复扫 `NO_STALE_REFS`。
- 确认 `render_lit.py` 的 EEVEE 预热渲染仍在，避免首帧贴图未上传 GPU 的假 FAIL。

### 第 2 轮：单模型端到端
- `学校大楼/Style1`：`_V2` 3023 面 PASS；`_V2Lite` 2489 面 PASS。
- 两份 `.compare.png` / `.lit.png` 哈希不同，确认不是重复联系表。
- 复核脚本最初未清空 `bpy.data.images`，导致第二个 FBX 的贴图数被累计；已修正复核方式。修正后两份 FBX 均为 1 mesh / 1 material / 2 张内嵌贴图。

### 第 3 轮：桌面目录全量重跑

| 结果 | 数量 |
|---|---:|
| 有 zip 的模型 | 7 |
| 无 zip 跳过 | 3 |
| `_V2` 生成且 PASS | 7 |
| `_V2Lite` 生成且 PASS | 6 |
| 不合格而不生成 Lite | 1（学校 Style2） |
| 失败 | 0 |

- `_V2` 平均 2988 面，平均 `lit_tex_err=0.0107`、近景贴图 p95 `0.0560`。
- `_V2Lite` 平均 2323 面，平均 `lit_tex_err=0.0124`、近景贴图 p95 `0.0651`。
- 13 个最终 FBX 全部用 Blender 重导入通过：每个都是 1 mesh / 1 material / 2 张 packed 贴图。
- 全部日志扫描无真实错误（仅 Rokoko addon 的无害 LZ4 警告）。
- V2Lite 现在以 V2 为基线，不再依赖任何旧版 `_New` / V1 产物；因此预算相对上一轮实验略有变化，这是去旧化后的正常结果。
