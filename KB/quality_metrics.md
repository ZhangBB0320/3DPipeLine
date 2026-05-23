# 减面质量评估指标体系

> 脚本: `Scripts/evaluate_quality.py`
> 函数: `evaluate_quality(high_obj_name, low_obj_name, ...)`

## 指标总览

| 指标组 | 指标 | 理想值 | 权重 | 含义 |
|--------|------|--------|------|------|
| **几何保真度** | hausdorff_max | 0 | 25% | 双向 Hausdorff 最大距离（低模与高模最远偏差） |
| | hausdorff_mean | 0 | 15% | 双向 Hausdorff 平均距离 |
| | hausdorff_rms | 0 | — | 双向 Hausdorff RMS 距离（参考） |
| **表面属性** | volume_ratio | 1.0 | 8% | 体积比 Vol(low)/Vol(high) |
| | area_ratio | ≈0.85 | 7% | 表面积比 Area(low)/Area(high) |
| | normal_consistency | 1.0 | 10% | 法线一致性（dot product 均值） |
| **特征保留** | sharp_edge_retention | 1.0 | 12% | 锐边比例保留率 |
| | boundary_edge_retention | 1.0 | 8% | 边界边比例保留率 |
| **包裹质量** | coverage_ratio | 1.0 | 10% | 低模对高模的射线覆盖率 |
| | bbox_max_deviation | 0 | 5% | 包围盒最大偏差 |
| | **overall_score** | **100** | — | **加权综合评分** |

## 指标详解

### 1. Hausdorff 距离 (几何保真度)

**核心指标，最直接衡量减面的几何偏差。**

- **计算方法**: 在两个模型表面均匀采样 N 个点，用 BVH tree 查询最近点距离
- **双向计算**: 
  - High→Low: 从高模采样，查询到低模最近点的距离
  - Low→High: 从低模采样，查询到高模最近点的距离
  - 取两个方向的最大值/均值
- **归一化**: 距离除以高模 BBox 对角线长度，得到百分比
- **评分阈值**:
  - max < 2% diag → 90+ 分
  - max < 5% diag → 75+ 分
  - max > 20% diag → 0 分
  - mean < 0.2% diag → 95+ 分
  - mean > 5% diag → 0 分

### 2. 体积比 (Volume Ratio)

- **计算方法**: bmesh `calc_volume()`，计算封闭网格的体积
- **理想值**: 1.0（体积完全一致）
- **典型范围**: 减面后体积通常变化很小（0.98-1.02）
- **异常**: >1.05 或 <0.95 说明减面导致了显著的膨胀/收缩

### 3. 表面积比 (Area Ratio)

- **计算方法**: 所有面的面积之和
- **理想值**: ≈0.85（减面后面数减少，但表面积不会等比减少）
- **典型范围**: 0.8-1.0
- **异常**: <0.7 说明大量细节几何被删除

### 4. 法线一致性 (Normal Consistency)

- **计算方法**: 在低模表面采样，查询高模最近点的法线，计算 dot product
- **值域**: [-1, 1]，1 = 完全一致，0 = 正交，-1 = 反转
- **典型范围**: 0.7-0.95（减面后法线方向基本一致）
- **评分**: 直接映射到 0-100（0.8 → 80 分）
- **flipped 比例**: dot < 0 的采样比例，反映反转法线的程度
- **用途**: 检测减面是否导致面片翻转/法线方向错误

### 5. 锐边保留率 (Sharp Edge Retention)

- **计算方法**: 
  - 统计高模中角度 > threshold 的边占比 `high_sharp_ratio`
  - 统计低模中角度 > threshold 的边占比 `low_sharp_ratio`
  - 保留率 = `low_sharp_ratio / high_sharp_ratio`，上限 1.0
- **角度阈值**: 默认 30°，可通过 `sharp_angle` 参数调整
- **典型范围**: 良好减面 > 0.5；差减面 < 0.2
- **注意**: 低模面数少，锐边比例可能反而比高模高（这是正常的）

### 6. 边界边保留率 (Boundary Edge Retention)

- **计算方法**: 类似锐边，但只统计边界边
- **典型范围**: 1.0（边界边不应丢失）
- **异常**: <1.0 说明减面导致了开放边界

### 7. 覆盖率 (Coverage Ratio)

- **计算方法**: 从高模表面沿法线方向投射射线到低模，统计命中率
- **射线方向**: 正反两个方向都尝试，一个命中即算
- **典型范围**: 0.9-1.0（好的低模应覆盖高模 90% 以上表面）
- **异常**: <0.7 说明低模无法包裹高模，烘焙时会有大量缺失

### 8. BBox 偏差 (BBox Deviation)

- **计算方法**: 比较高低模世界空间 AABB 的最大坐标偏差
- **典型范围**: <1% 对角线长度
- **用途**: 快速检测对齐问题

## 综合评分权重

```
overall = hausdorff_max × 0.25
        + hausdorff_mean × 0.15
        + volume × 0.08
        + area × 0.07
        + normal × 0.10
        + sharp_edges × 0.12
        + boundary × 0.08
        + coverage × 0.10
        + bbox × 0.05
```

权重设计逻辑：
- **Hausdorff (40%)**: 几何偏差是最核心的指标
- **表面属性 (25%)**: 体积/面积/法线反映减面后的表面质量
- **特征保留 (20%)**: 锐边和边界保留是建筑模型的关键
- **包裹质量 (15%)**: 覆盖率和 BBox 影响烘焙质量

## 评分等级

| 分数 | 等级 | 含义 |
|------|------|------|
| 90-100 | A (优秀) | 减面质量极高，几乎无损失 |
| 80-89 | B (良好) | 减面质量好，可接受 |
| 70-79 | C (一般) | 减面质量一般，某些指标需优化 |
| 60-69 | D (较差) | 减面质量差，需要调参 |
| <60 | F (不合格) | 减面质量不可接受 |

## 用法示例

```python
# 标准流水线中调用
from blender_connect import ensure_blender, send_python
send_python('''
import sys
sys.path.insert(0, "/Users/zbb/3DPipeLine/Scripts")
from evaluate_quality import evaluate_quality

# 评估当前场景中的高低模
result = evaluate_quality("node_0", "Building_Low", name="Building")
print(result["overall_score"])
print(result["summary"])
''', recv_timeout=300)

# 关键指标快速读取
# result["hausdorff_max_pct_diag"]  — Hausdorff 最大偏差（% 对角线）
# result["volume_ratio"]            — 体积比
# result["normal_consistency"]      — 法线一致性
# result["coverage_ratio"]          — 覆盖率
# result["overall_score"]           — 综合评分
```

## 当前模型评估结果 (Building, 1.48M → 3K)

```
  [几何保真度]
    Hausdorff max:  0.029 (2.29% diag)     ← 良好
    Hausdorff mean: 0.002 (0.15% diag)     ← 优秀
    Hausdorff RMS:  0.003 (0.21% diag)     ← 优秀

  [表面属性]
    Volume ratio:   1.0038                 ← 几乎完美
    Area ratio:     0.9226                 ← 正常
    Normal consist: 0.8008                 ← 良好

  [特征保留]
    Sharp edges:    100.0% retained        ← 优秀
    Boundary:       100.0% retained        ← 优秀

  [包裹质量]
    Coverage:       95.9%                  ← 优秀
    BBox deviation: 0.0074                 ← 优秀

  ═══════════════════════════════════
  OVERALL SCORE: 91.2 / 100 (A)
  ═══════════════════════════════════
```

## 参数优化方向

| 评分低 | 优化方向 |
|--------|----------|
| Hausdorff 差 | 降低 step_ratio（更小步长）、增大 crease_angle |
| Volume 差 | 检查是否有封闭性破坏、调整 decimate 算法 |
| Normal 差 | 增加法线保护、使用渐进减面替代一步减面 |
| Sharp edges 差 | 增大 crease_angle、减小 step_ratio |
| Coverage 差 | 检查低模是否包裹高模、增大 cage 距离 |
