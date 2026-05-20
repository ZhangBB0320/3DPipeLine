---
name: fbx-export
description: This skill covers the full pipeline from a textured high-poly Blender model to a baked, embedded-texture low-poly FBX. It also covers the NoBake decimation pipeline for AI-generated buildings where baking is not needed (preserving original UV + textures). It activates whenever the user asks to export FBX, decimate a model, generate LODs, bake high-to-low (albedo / normal / roughness), produce a low-poly FBX with embedded textures, or process AI-generated building models. The skill enforces transform-consistency rules during decimation, EMIT-based albedo baking for correct PBR colors, mandatory cleanup of companion files after export, and a single end-to-end SOP that has been validated on the wooden_handle_saber pipeline.
---

# FBX Export & High-to-Low Bake Skill

将 Blender 场景中的物体导出为 FBX；或执行完整的 **「减面 → 烘焙 → 低模内嵌纹理 FBX」** 流水线；或对 AI 生成的建筑模型执行 **「NoBake 减面」** 流水线（保留原始 UV + 纹理，不烘焙）。

## 触发条件

当用户提出以下需求时激活此 Skill：
- 导出 / 打包 FBX；将物体转为 FBX 格式；批量导出 FBX
- 减面 / 简化模型 / LOD 制作
- 高模烘焙到低模；烘 albedo / normal / roughness
- 生成带烘焙纹理的低模 FBX
- 整套 "高模 → 低模 → 内嵌纹理 FBX" 流程
- **AI 建筑模型减面 / 批量建筑减面 / NoBake 减面**（保留 UV 不烘焙）

---

## 端到端 SOP 总览（从高模到内嵌纹理低模 FBX）

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 检查高模     bpy.data.objects[name] 的 verts/faces/UV/材质  │
│                                                              │
│ 2. 导出 OBJ    bpy.ops.wm.obj_export(forward=-Z, up=Y)       │
│                                                              │
│ 3. pymeshlab 减面 → outPut/<日期>/<name>.obj                  │
│    Scripts/decimate.py                                       │
│                                                              │
│ 4. 导入低模 OBJ → 重命名 <name>_Low                           │
│    bpy.ops.wm.obj_import(forward=-Z, up=Y)                   │
│                                                              │
│ 5. ⚠️ 修复 Y/Z 轴互换：rotation_euler=(π/2,0,0) → 应用变换     │
│                                                              │
│ 6. ✅ 验证 BBox：低模与高模世界空间偏差应仅为减面误差          │
│                                                              │
│ 7. 调用 bake_high_to_low() 一键烘焙 Albedo+Normal              │
│    Scripts/bake.py（⚠️ 唯一默认烘焙脚本，禁止手写内联烘焙代码） │
│                                                              │
│ 8. 导出低模为内嵌纹理 FBX：embed_textures=True, COPY 模式    │
│                                                              │
│ 9. 强制清理伴随的 .mtl / 散落的 .png/.jpg/.exr 等            │
└─────────────────────────────────────────────────────────────┘
```

---

## 简单导出（仅 FBX，不涉及减面/烘焙）

```python
import sys
sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts')
from export_fbx import export_fbx

export_fbx(
    objects="目标物体名",
    scale=1.0,            # Unity 用 0.01
    armature=False,        # 角色模型开 True
    animation=False,       # 需要动画时开 True
    embed_textures=True,   # 默认内嵌纹理
    path_mode='COPY',      # 路径模式=复制
)
```

### 常见场景速查

| 场景 | 参数 |
|------|------|
| 静态模型（默认） | `objects="name"` |
| 导出至 Unity | `objects="name", scale=0.01` |
| 角色带骨骼动画 | `objects="name", armature=True, animation=True, scale=0.01` |
| 纯几何无纹理 | `objects="name", embed_textures=False` |
| 批量多物体 | `objects="obj1,obj2,obj3"` |
| 全场景 | 不传 objects 参数 |

### 强制清理垃圾文件

`path_mode='COPY'` 会把纹理散落到输出目录。导出后 **必须** 删除：
- `.mtl`、`.jpg`、`.jpeg`、`.png`、`.exr`、`.tga`、`.bmp`、`.tif`、`.tiff`、`.webp`、`.dds`

`Scripts/export_fbx.py` 已内置 `_cleanup_output_dir()` 自动执行此步。

---

## 减面流程（强制 Transform 一致性）

### 1. 记录原模型 Transform

```python
src = bpy.data.objects["原模型名"]
original_location = tuple(src.location)
original_rotation = tuple(src.rotation_euler)
original_scale = tuple(src.scale)
```

### 2. 导出 OBJ → pymeshlab 减面

```python
# Blender 内导出（推荐，避免重启 Blender）
bpy.ops.wm.obj_export(
    filepath='/Users/zbb/3DPipeLine/outPut/<name>_High.obj',
    export_selected_objects=True,
    forward_axis='NEGATIVE_Z', up_axis='Y',
)
```

```bash
# pymeshlab 减面（用 workbuddy 自带的 Python 3.14，已装 pymeshlab）
/Users/zbb/.workbuddy/binaries/python/versions/3.14.3/bin/python3 \
    /Users/zbb/3DPipeLine/Scripts/decimate.py \
    outPut/<name>_High.obj <目标面数> outPut
# 结果：outPut/<日期>/<name>_High.obj
```

### 3. 导入低模并对齐 Transform（强制）

```python
bpy.ops.wm.obj_import(filepath=low_obj_path, forward_axis='NEGATIVE_Z', up_axis='Y')
imported = bpy.context.selected_objects[0]
imported.name = '<name>_Low'

# 设置与高模一致的 transform
imported.location = original_location
imported.rotation_euler = original_rotation
imported.scale = original_scale

# 应用变换烘焙到顶点
bpy.context.view_layer.objects.active = imported
imported.select_set(True)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
```

### 4. ⚠️ 修复 OBJ Y/Z 轴互换（已确认必发生）

OBJ 导入即使指定 `forward=-Z, up=Y`，实测仍出现 Y/Z 互换（长轴从 Z 变到 Y）。修复：

```python
import math
imported.rotation_euler = (math.pi/2, 0, 0)  # 绕 X 轴 +90°
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
```

### 5. 验证 BBox 一致性（强制）

```python
import mathutils
high_bb = [src.matrix_world @ mathutils.Vector(c) for c in src.bound_box]
low_bb = [imported.matrix_world @ mathutils.Vector(c) for c in imported.bound_box]
max_diff = max((high_bb[i]-low_bb[i]).length for i in range(8))
print(f'BBox max diff: {max_diff:.6f}')
# 期望：减面后偏差仅来自网格简化（typical 0.001-0.01m），非 transform 偏移
```

**此步骤为强制规则，不允许跳过。低模必须与原模型在位置/缩放/旋转上完全重合。**

---

## 高模烘焙到低模流程（⚠️ 必须使用 bake.py 脚本）

**强制规则：所有烘焙操作必须通过 `Scripts/bake.py` 的 `bake_high_to_low()` 函数执行，禁止手写内联烘焙代码。**

### 为什么必须用 bake.py

`bake.py` 封装了以下经过实测验证的关键逻辑，手写极易遗漏：

| 功能 | 手写风险 | bake.py 处理 |
|------|---------|-------------|
| Albedo 烘焙 | DIFFUSE → 金属全黑 | ✅ EMIT 法，自动备份/还原高模材质 |
| 保存烘焙图 | `save()` 保存 generated_color；`save_render()` 视图变换偏色；`pack+save()` 4K 不稳定 | ✅ `img.pixels → numpy linear→sRGB → struct+zlib PNG` |
| 烘焙图底色 | 黑底 → UV 缝黑边 | ✅ 白色/法线蓝/中灰底色 + `img.scale()` 强制刷新 |
| 材质校验 | 默认紫色材质 → 烘焙全黑 | ✅ `_validate_high_material()` 前置校验 |
| UV 校验 | 无 UV → 烘焙全白 | ✅ `_validate_uv()` 前置校验 |
| BBox 对齐 | 高低模错位 → 烘焙偏暗 | ✅ `_validate_bbox_alignment()` 校验 |
| 旋转对齐 | OBJ 轴向不一致 | ✅ `_align_low_to_high()` 自动中心匹配 + 24 旋转搜索 |

### 用法一：Blender 内部模式（MCP / Scripting）

```python
import sys
sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts')
from bake import bake_high_to_low

alb_path, norm_path = bake_high_to_low(
    high_fbx="/path/to/high.fbx",
    low_obj="/path/to/low.obj",
    out_dir="/path/to/output",
    name="MyModel",       # 烘焙贴图前缀
    cage=1.0,             # 建筑 1.0，小物件/武器 0.2
    res=4096,             # 4K（2026-05-20 实测 4K >> 2K/1K）
    samples=64,           # 64 即可
    device="GPU",         # 优先 GPU
)
# 返回: (albedo_png_path, normal_png_path)
```

### 用法二：Blender CLI 模式（后台批处理）

```bash
/Applications/Blender.app/Contents/MacOS/Blender --background --python Scripts/bake.py -- \
    --high_fbx /path/to/high.fbx \
    --low_obj /path/to/low.obj \
    --out_dir /path/to/output \
    --name MyModel \
    --cage 1.0 \
    --res 4096 \
    --samples 64 \
    --device GPU
```

### 用法三：通过 MCP execute_code 调用

```python
# 在 execute_blender_code 中执行
import sys
sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts')
from bake import bake_high_to_low

alb, norm = bake_high_to_low(
    high_fbx="/Users/zbb/3DPipeLine/outPut/model.fbx",
    low_obj="/Users/zbb/3DPipeLine/outPut/2026-05-21/model.obj",
    out_dir="/Users/zbb/3DPipeLine/outPut/baked",
    name="model",
)
print(f"Albedo: {alb}")
print(f"Normal: {norm}")
```

**⚠️ MCP 长任务规则**：`bake_high_to_low()` 内部包含两次 `bpy.ops.object.bake()` 调用（Albedo + Normal），总耗时可能 30-120 秒。如遇 MCP 超时，**不要重复调用**——等 1-2 分钟后用 `get_scene_info` 探活，再检查输出目录是否已有 PNG 文件。

### 烘焙统一参数（强制）

```python
# bake.py 内部默认值，无需手动设置
DEFAULT_CAGE     = 1.0    # 建筑 cage；小物件/武器用 0.2
DEFAULT_RES      = 4096   # 4K
DEFAULT_SAMPLES  = 64
DEFAULT_DEVICE   = "GPU"  # 优先 GPU
```

**选择顺序**：先选高模，加选低模，活动物体 = 低模；并在低模材质里选中要烘焙的目标图像节点。

### bake_high_to_low() 完整流程

1. 导入高模 FBX（含纹理）→ 合并多 mesh → apply transform → 材质校验
2. 导入低模 OBJ → apply transform
3. 对齐低模到高模（`_align_low_to_high`：中心匹配 + 24 旋转搜索）
4. BBox 校验
5. Smart UV Project（`skip_uv=True` 可跳过）
6. 创建烘焙图（白色/法线蓝底色，4096×4096）
7. 建立低模 BSDF 节点树（tex_coord → mapping → albedo/normal image → BSDF → Output）
8. Cycles GPU + use_selected_to_active + cage=1.0
9. 烘焙 Albedo（EMIT 法：备份→临时 Emission→烘焙→还原）
10. 烘焙 Normal（NORMAL 类型）
11. 保存贴图（`_save_bake_image`：numpy linear→sRGB → struct+zlib PNG）
12. 清理场景中的高低模和孤立数据

### 故障排查

| 现象 | 原因 / 修复 |
|------|------------|
| 烘焙结果整体偏黑 / 暗 | albedo 用了 DIFFUSE → bake.py 默认用 EMIT，不会出现此问题 |
| UV 缝处有黑边 | 烘焙图底色是黑色 → bake.py 默认白底/法线蓝底 |
| 保存的贴图全黑 / 偏暗 | `save()`/`save_render()` bug → bake.py 用 `_save_bake_image()` 手动写 PNG |
| 法线贴图失真 | 检查 NormalMap 节点 Space=Tangent + norm 图像 colorspace=Non-Color |
| 粗糙度全黑/全白 | 高模 BSDF Roughness 输入未连贴图，单色属正常 |
| "无有效的选中物体" | 高模/低模被 hide → `obj.hide_viewport=False; obj.hide_set(False)` |
| 烘焙后高模材质坏掉 | 临时 Emission 节点未清理 → bake.py 自动还原 |
| 高模默认紫色材质报错 | 材质未正确加载 → `_validate_high_material()` 前置拦截 |

---

## 完整端到端示例（saber 已验证可跑通）

```python
import bpy, sys, os, math, mathutils

# === 配置 ===
HIGH_NAME = 'saber_High'
TARGET_FACES = 1000
OUT_DIR = '/Users/zbb/3DPipeLine/outPut'
BAKE_DIR = f'{OUT_DIR}/baked'
os.makedirs(BAKE_DIR, exist_ok=True)

high = bpy.data.objects[HIGH_NAME]

# === 1-2. 导出 OBJ ===
bpy.ops.object.select_all(action='DESELECT')
high.select_set(True); bpy.context.view_layer.objects.active = high
high_obj_path = f'{OUT_DIR}/{HIGH_NAME}.obj'
bpy.ops.wm.obj_export(filepath=high_obj_path, export_selected_objects=True,
                      forward_axis='NEGATIVE_Z', up_axis='Y')

# === 3. 减面（外部进程） ===
import subprocess
subprocess.run([
    '/Users/zbb/.workbuddy/binaries/python/versions/3.14.3/bin/python3',
    '/Users/zbb/3DPipeLine/Scripts/decimate.py',
    high_obj_path, str(TARGET_FACES), OUT_DIR,
], check=True)

# 找出最新的减面结果
from pathlib import Path
date_dirs = sorted(Path(OUT_DIR).glob('20*'))
low_obj_path = str(date_dirs[-1] / f'{HIGH_NAME}.obj')

# === 4-6. 导入 + 对齐 + 验证 ===
bpy.ops.object.select_all(action='DESELECT')
bpy.ops.wm.obj_import(filepath=low_obj_path, forward_axis='NEGATIVE_Z', up_axis='Y')
low = bpy.context.selected_objects[0]
low.name = 'saber_Low'
low.location = high.location[:]
low.rotation_euler = high.rotation_euler[:]
low.scale = high.scale[:]
bpy.context.view_layer.objects.active = low
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

# OBJ 必发的 Y/Z 轴互换修复
low.rotation_euler = (math.pi/2, 0, 0)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

# 验证 BBox
high_bb = [high.matrix_world @ mathutils.Vector(c) for c in high.bound_box]
low_bb  = [low.matrix_world  @ mathutils.Vector(c) for c in low.bound_box]
max_diff = max((high_bb[i]-low_bb[i]).length for i in range(8))
print(f'BBox max diff: {max_diff:.6f}')

# === 7. 烘焙（使用 bake.py，禁止手写内联烘焙代码） ===
sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts')
from bake import bake_high_to_low

# 导出高模为 FBX（bake_high_to_low 需要 FBX 输入）
high_fbx_path = f'{OUT_DIR}/{HIGH_NAME}.fbx'
bpy.ops.object.select_all(action='DESELECT')
high.select_set(True)
bpy.ops.export_scene.fbx(
    filepath=high_fbx_path, use_selection=True,
    path_mode='COPY', embed_textures=True,
    mesh_smooth_type='FACE',
    axis_forward='-Z', axis_up='Y',
)

alb_path, norm_path = bake_high_to_low(
    high_fbx=high_fbx_path,
    low_obj=low_obj_path,
    out_dir=BAKE_DIR,
    name='saber',
    cage=1.0,
    res=4096,
    samples=64,
    device='GPU',
)
print(f'Albedo: {alb_path}')
print(f'Normal: {norm_path}')

# === 8-9. 导出内嵌纹理 FBX + 自动清理 ===
from export_fbx import export_fbx
export_fbx(objects='saber_Low', scale=1.0,
           armature=False, animation=False,
           embed_textures=True, path_mode='COPY')
```

---

## 输出位置

| 文件 | 路径 |
|------|------|
| 高模 OBJ（中间产物）| `outPut/<name>_High.obj` |
| 低模 OBJ（减面结果）| `outPut/<日期>/<name>_High.obj` |
| 烘焙图 | `outPut/baked/bake_<name>_albedo.png` / `bake_<name>_normal.png` |
| **最终低模 FBX** | `outPut/<name>_Low.fbx` ← 含内嵌烘焙纹理 |

---

## 整体故障排查

1. **物体未找到**：检查物体名拼写，用 get_scene_info 列出所有物体
2. **FBX 文件极小（<10KB）**：物体可能无网格数据，用 get_object_info 检查
3. **导入引擎后比例错误**：调整 scale 参数（Unity=0.01）
4. **骨骼未导出**：确认 armature=True 且骨骼是物体父级
5. **导出后输出目录有 .mtl/.png 散落**：`export_fbx.py` 已内置自动清理；如残留说明清理函数被绕过
6. **烘焙后低模在材质预览下偏黑**：确认使用了 `bake.py` 而非手写烘焙代码
7. **MCP 返回空响应**：参考 `blender-mcp-connector` SKILL.md 的「Stale Wrapper Process Pollution」清污 SOP
8. **保存的贴图偏色/全黑**：确认使用了 `bake.py` 的 `_save_bake_image()` 而非 `img.save()` / `img.save_render()`

---

## NoBake 减面流程（AI 生成建筑模型专用）

### 适用场景

- AI 生成的建筑模型（单 mesh，~50K 面）
- 目标：3K 面低模，用于 PC 游戏（非交互建筑）
- **核心问题**：94% 减面率下 Quadric Edge Collapse 会吃掉招牌等薄面片 → 烘焙射线未命中 → 黑色纹理块
- **核心方案**：减面时保留原始 UV，减面后直接使用原始 4096×4096 纹理，**跳过烘焙**

### 方案对比

| 方案 | 黑块问题 | UV 扭曲 | 流程复杂度 | 适用场景 |
|------|---------|---------|-----------|---------|
| **NoBake（推荐）** | ✅ 无（0%） | ~10% 面有极端拉伸 | 低（1 步） | AI 建筑等不需要精确拓扑的远景模型 |
| Baked（传统） | ❌ 有黑块 | ✅ 无（重新 UV） | 高（7+ 步） | 武器/角色等需要精确纹理的近景模型 |

### 端到端 SOP（NoBake）

```
┌──────────────────────────────────────────────────────────────┐
│ 1. 导入原始 FBX（含材质和纹理）                                  │
│                                                               │
│ 2. Blender Decimate 修改器，ratio = target / current            │
│    保留原始 UV，不重新展开                                       │
│                                                               │
│ 3. 解包纹理到输出目录（packed → 本地文件）                        │
│                                                               │
│ 4. 导出 FBX（path_mode='COPY'）                                 │
│    → <name>/<name>_3k.fbx + <name>_3k.fbm/*.png               │
│                                                               │
│ 5. 可选：EEVEE 渲染高模 vs 低模对比图                            │
└──────────────────────────────────────────────────────────────┘
```

### 一键运行

```bash
# 单个模型
bash Scripts/batch_nobake.sh /path/to/model.fbx 3000

# 批量目录
bash Scripts/batch_nobake.sh /path/to/fbx_dir/ 3000

# 默认（~/Downloads/Building/ 下所有 FBX）
bash Scripts/batch_nobake.sh
```

底层调用 Blender 命令行模式：
```bash
/Applications/Blender.app/Contents/MacOS/Blender --background \
    --python Scripts/nobake_decimate.py -- \
    --input /path/to/model.fbx \
    --output outPut/ \
    --target 3000 \
    --render
```

### 输出结构

```
outPut/<model_name>/
  <model_name>_3k.fbx          # 低模 FBX
  <model_name>_3k.fbm/          # FBX 纹理伴侣目录（Unity/UE 自动识别）
    texture_pbr_xxx.png         # albedo
    texture_pbr_xxx_normal.png  # normal
    texture_pbr_xxx_roughness.png
    texture_pbr_xxx_metallic.png
  <model_name>_compare.png      # 高模 vs 低模对比渲染图（--render 时）
```

### Blender 5.1 FBX 导出参数（已验证）

```python
bpy.ops.export_scene.fbx(
    filepath=fbx_path,
    use_selection=True,
    path_mode='COPY',       # 复制纹理到 .fbm 目录
    embed_textures=False,   # Blender FBX 嵌入不可靠，用 COPY
    mesh_smooth_type='FACE',
    use_mesh_modifiers=False,
    axis_forward='-Z',
    axis_up='Y',
    primary_bone_axis='Y',
    secondary_bone_axis='X',
)
```

**注意**：Blender 5.1 移除了 `apply_modifiers` 和 `copy_substitute` 参数，使用时会报 TypeError。

### UV 扭曲分析

94% 减面率下 UV 扭曲数据（Building1 实测）：
- 极端拉伸面：~10%（3K 面中约 300 面）
- 极端压缩面：~0%
- **权衡**：10% 面有 UV 拉伸 vs 0% 黑块。对远景建筑完全可接受。

### 故障排查（NoBake 专用）

| 现象 | 原因 / 修复 |
|------|------------|
| FBX 极小（<10KB） | 纹理未解包或导出参数错误 → 检查 unpack_textures 输出 |
| 纹理目录为空 | packed 纹理解包失败 → 用 `img.save()` 作为回退 |
| 导出报 TypeError | Blender 5.1 API 变更 → 移除 `apply_modifiers`、`copy_substitute` |
| .fbm 目录有纹理但引擎不识别 | 确认 .fbm 与 .fbx 同级同名（如 `model_3k.fbx` + `model_3k.fbm/`） |
| 渲染图全黑 | 场景无灯光 → 脚本自动设置 World 背景光 |
| 减面后 UV 全乱 | ratio 过小 → Building1 在 ratio=0.0566 下仍有可接受的 UV |
