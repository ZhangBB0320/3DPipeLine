---
name: fbx-export
description: This skill covers the full pipeline from a textured high-poly Blender model to a baked, embedded-texture low-poly FBX. It activates whenever the user asks to export FBX, decimate a model, generate LODs, bake high-to-low (albedo / normal / roughness), or produce a low-poly FBX with embedded textures. The skill enforces transform-consistency rules during decimation, EMIT-based albedo baking for correct PBR colors, mandatory cleanup of companion files after export, and a single end-to-end SOP that has been validated on the wooden_handle_saber pipeline.
---

# FBX Export & High-to-Low Bake Skill

将 Blender 场景中的物体导出为 FBX；或执行完整的 **「减面 → 烘焙 → 低模内嵌纹理 FBX」** 流水线。

## 触发条件

当用户提出以下需求时激活此 Skill：
- 导出 / 打包 FBX；将物体转为 FBX 格式；批量导出 FBX
- 减面 / 简化模型 / LOD 制作
- 高模烘焙到低模；烘 albedo / normal / roughness
- 生成带烘焙纹理的低模 FBX
- 整套 "高模 → 低模 → 内嵌纹理 FBX" 流程

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
│ 4. 导入低模 OBJ → 重命名 saber_Low                            │
│    bpy.ops.wm.obj_import(forward=-Z, up=Y)                   │
│                                                              │
│ 5. ⚠️ 修复 Y/Z 轴互换：rotation_euler=(π/2,0,0) → 应用变换     │
│                                                              │
│ 6. ✅ 验证 BBox：低模与高模世界空间偏差应仅为减面误差          │
│                                                              │
│ 7. Smart UV Project（保证无重叠）                             │
│                                                              │
│ 8. 创建 3 张烘焙图（白色/法线蓝/中灰底色，禁黑色）             │
│                                                              │
│ 9. 建立低模 BSDF 节点树：tex_coord→mapping→3 图→BSDF→输出      │
│                                                              │
│ 10. Cycles 引擎 + use_selected_to_active=True + cage=1.5    │
│                                                              │
│ 11. ⚠️ 烘焙 albedo 用 EMIT 法（临时接 Emission，不用 DIFFUSE） │
│ 12. 烘焙 normal（NORMAL 类型，默认参数）                       │
│ 13. 烘焙 roughness（ROUGHNESS 类型，默认参数）                 │
│                                                              │
│ 14. 导出低模为内嵌纹理 FBX：embed_textures=True, COPY 模式    │
│                                                              │
│ 15. 强制清理伴随的 .mtl / 散落的 .png/.jpg/.exr 等            │
└─────────────────────────────────────────────────────────────┘
```

整套流程在 `wooden_handle_saber` 案例上已端到端验证（4031 面 → 978 面，BBox 偏差 0.005m，4.35 MB 内嵌纹理 FBX）。

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

## 高模烘焙到低模流程

减面 + 对齐完成后，进行高→低烘焙。

### 1. Smart UV Project（保证 UV 不重叠）

```python
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(angle_limit=1.15191, island_margin=0.02)
bpy.ops.object.mode_set(mode='OBJECT')
```

### 2. 创建 3 张烘焙图（强制非黑底色）

UV 岛之间的 padding 区域显示底色，**黑底会导致整体偏暗 + UV 缝出现黑边**。

```python
def make_bake_image(name, colorspace, gen_color, is_data=False):
    img = bpy.data.images.new(name, 2048, 2048, alpha=False, is_data=is_data)
    img.colorspace_settings.name = colorspace
    img.generated_color = gen_color
    img.scale(img.size[0], img.size[1])  # 强制把底色刷到像素
    img.filepath_raw = f'/Users/zbb/3DPipeLine/outPut/baked/{name}.png'
    img.file_format = 'PNG'
    return img

img_alb   = make_bake_image('bake_<name>_albedo',    'sRGB',      (1.0, 1.0, 1.0, 1.0), is_data=False)
img_norm  = make_bake_image('bake_<name>_norm',      'Non-Color', (0.5, 0.5, 1.0, 1.0), is_data=True)
img_rough = make_bake_image('bake_<name>_roughness', 'Non-Color', (0.5, 0.5, 0.5, 1.0), is_data=True)
```

| 贴图 | 色彩空间 | is_data | 底色 |
|------|---------|---------|------|
| albedo | sRGB | False | (1,1,1,1) 白 |
| norm | Non-Color | True | (0.5,0.5,1,1) 法线蓝 |
| roughness | Non-Color | True | (0.5,0.5,0.5,1) 中灰 |

### 3. 建立低模 BSDF 节点树

```python
mat = bpy.data.materials.new('bake_<name>')
mat.use_nodes = True
nt = mat.node_tree
for n in list(nt.nodes): nt.nodes.remove(n)

# 节点链：texcoord → mapping → 3 个 image → (norm 经 NormalMap) → BSDF → Output
tex_coord = nt.nodes.new('ShaderNodeTexCoord')
mapping   = nt.nodes.new('ShaderNodeMapping')
nt.links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])

n_alb   = nt.nodes.new('ShaderNodeTexImage'); n_alb.image = img_alb;   n_alb.label = 'albedo'
n_norm  = nt.nodes.new('ShaderNodeTexImage'); n_norm.image = img_norm; n_norm.label = 'norm'
n_rough = nt.nodes.new('ShaderNodeTexImage'); n_rough.image = img_rough; n_rough.label = 'roughness'
for n in (n_alb, n_norm, n_rough):
    nt.links.new(mapping.outputs['Vector'], n.inputs['Vector'])

n_normmap = nt.nodes.new('ShaderNodeNormalMap'); n_normmap.space = 'TANGENT'
nt.links.new(n_norm.outputs['Color'], n_normmap.inputs['Color'])

bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
out  = nt.nodes.new('ShaderNodeOutputMaterial')
nt.links.new(n_alb.outputs['Color'], bsdf.inputs['Base Color'])
nt.links.new(n_rough.outputs['Color'], bsdf.inputs['Roughness'])
nt.links.new(n_normmap.outputs['Normal'], bsdf.inputs['Normal'])
nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])

low.data.materials.clear()
low.data.materials.append(mat)
```

### 4. ⚠️ Albedo 必须用 EMIT 烘焙法（强制规则）

**绝对禁止用 DIFFUSE 烘焙 albedo / base color，无论金属/非金属一律用 EMIT。**

为什么 DIFFUSE+只勾颜色不可靠：

| 材质类型 | DIFFUSE+只勾颜色结果 | EMIT 结果 |
|---------|------------------|----------|
| 纯金属（Metallic=1）| **全黑**（金属漫反射通道恒等于 0）| ✅ 正确 |
| 带 SSS 皮肤 | 偏色（混入次表面散射）| ✅ 正确 |
| 带 Sheen 布料/丝绸 | 颜色被光泽层衰减 | ✅ 正确 |
| 带 Coat 清漆 | 颜色被清漆层衰减 | ✅ 正确 |
| 普通非金属（纯漫反射）| 与 EMIT 等价 | ✅ 正确 |

EMIT 法是 Substance Painter / Marmoset Toolbag / Bake Wrangler 等业界烘焙工具的底层做法：直接采样 base color 节点输出，不经过 PBR 物理计算，无论材质多复杂都得到原始基础色。

```python
def bake_albedo_via_emit(high_obj, low_obj, target_image_node):
    """通过临时 Emission 节点烘 base color，自动还原"""
    mat_h = high_obj.material_slots[0].material
    nt = mat_h.node_tree
    bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    output = next(n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL')

    # 备份
    bc_input = bsdf.inputs['Base Color']
    bc_src = bc_input.links[0].from_socket if bc_input.is_linked else None
    bc_default = tuple(bc_input.default_value)
    surf_input = output.inputs['Surface']
    orig_surf_src = surf_input.links[0].from_socket if surf_input.is_linked else None

    # 临时 Emission
    emit = nt.nodes.new('ShaderNodeEmission')
    emit.label = '__bake_tmp__'
    if bc_src:
        nt.links.new(bc_src, emit.inputs['Color'])
    else:
        emit.inputs['Color'].default_value = bc_default
    for link in list(surf_input.links): nt.links.remove(link)
    nt.links.new(emit.outputs['Emission'], surf_input)

    # 选中物体 + 选中目标图像节点
    bpy.ops.object.select_all(action='DESELECT')
    high_obj.select_set(True); low_obj.select_set(True)
    bpy.context.view_layer.objects.active = low_obj
    low_nt = low_obj.data.materials[0].node_tree
    for n in low_nt.nodes: n.select = False
    target_image_node.select = True
    low_nt.nodes.active = target_image_node

    # 烘焙
    scn = bpy.context.scene
    scn.cycles.bake_type = 'EMIT'
    scn.render.bake.use_selected_to_active = True
    scn.render.bake.cage_extrusion = 1.5
    bpy.ops.object.bake(type='EMIT')

    # 还原
    for link in list(surf_input.links): nt.links.remove(link)
    if orig_surf_src: nt.links.new(orig_surf_src, surf_input)
    nt.nodes.remove(emit)
```

### 5. Normal / Roughness 用标准烘焙

```python
def bake_standard(high_obj, low_obj, target_image_node, bake_type):
    """bake_type ∈ {'NORMAL', 'ROUGHNESS'}"""
    bpy.ops.object.select_all(action='DESELECT')
    high_obj.select_set(True); low_obj.select_set(True)
    bpy.context.view_layer.objects.active = low_obj
    low_nt = low_obj.data.materials[0].node_tree
    for n in low_nt.nodes: n.select = False
    target_image_node.select = True
    low_nt.nodes.active = target_image_node

    scn = bpy.context.scene
    scn.cycles.bake_type = bake_type
    scn.render.bake.use_selected_to_active = True
    scn.render.bake.cage_extrusion = 1.5
    bpy.ops.object.bake(type=bake_type)
```

### 6. 三种烘焙的统一参数（强制）

```python
scn.render.engine = 'CYCLES'
scn.cycles.device = 'CPU'        # 或 'GPU' 加速
scn.cycles.samples = 128

bake = scn.render.bake
bake.use_selected_to_active = True   # 必须勾「所选 → 活动」
bake.cage_extrusion = 1.5            # 挤出 1.5m（人物模型；小物件可调小到 0.1-0.3）
bake.use_cage = False                # 不用单独的笼体物体
```

**选择顺序**：先选高模，加选低模，活动物体 = 低模；并在低模材质里选中要烘焙的目标图像节点。

### 7. 故障排查

| 现象 | 原因 / 修复 |
|------|------------|
| 烘焙结果整体偏黑 / 暗 | albedo 用了 DIFFUSE → 强制改 EMIT |
| UV 缝处有黑边 | 烘焙图底色是黑色 → 重建图像，按上表设置非黑底色 |
| 法线贴图烘出来失真 | 检查低模 NormalMap 节点 Space=Tangent + norm 图像 colorspace=Non-Color |
| 粗糙度全黑/全白 | 高模 BSDF Roughness 输入未连贴图（用了默认值），单色属正常 |
| "无有效的选中物体" | 高模/低模被 hide → `obj.hide_viewport=False; obj.hide_set(False)` |
| 烘焙后高模材质坏掉 | 临时 Emission 节点未清理 → 检查 `[n for n in nt.nodes if n.type=='EMISSION']` 并移除 `__bake_tmp__` |

---

## 完整端到端示例（saber 已验证可跑通）

```python
import bpy, sys, os, math, mathutils

# === 配置 ===
HIGH_NAME = 'saber_High'              # 高模名称
LOW_NAME  = 'saber_Low'               # 低模名称
MAT_NAME  = 'bake_saber'              # 低模材质名
BAKE_PREFIX = 'bake_saber'            # 烘焙图前缀
TARGET_FACES = 1000                   # 目标面数
RESOLUTION = 2048                     # 烘焙图分辨率
CAGE = 1.5                            # 挤出（小物件可调小）
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

# === 3. 减面（外部进程，使用 workbuddy 的 python） ===
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
low.name = LOW_NAME
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

# === 7. Smart UV ===
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(angle_limit=1.15191, island_margin=0.02)
bpy.ops.object.mode_set(mode='OBJECT')

# === 8. 创建烘焙图 ===
def make_img(name, cs, color, is_data=False):
    img = bpy.data.images.new(name, RESOLUTION, RESOLUTION, alpha=False, is_data=is_data)
    img.colorspace_settings.name = cs
    img.generated_color = color
    img.scale(img.size[0], img.size[1])
    img.filepath_raw = f'{BAKE_DIR}/{name}.png'
    img.file_format = 'PNG'
    return img
img_alb   = make_img(f'{BAKE_PREFIX}_albedo',    'sRGB',      (1,1,1,1), False)
img_norm  = make_img(f'{BAKE_PREFIX}_norm',      'Non-Color', (0.5,0.5,1,1), True)
img_rough = make_img(f'{BAKE_PREFIX}_roughness', 'Non-Color', (0.5,0.5,0.5,1), True)

# === 9. 建立低模 BSDF 节点树（省略，参见上文） ===
# ... 省略，按 "建立低模 BSDF 节点树" 节代码 ...

# === 10. Cycles 配置 ===
scn = bpy.context.scene
scn.render.engine = 'CYCLES'
scn.cycles.device = 'CPU'
scn.cycles.samples = 128
scn.render.bake.use_selected_to_active = True
scn.render.bake.cage_extrusion = CAGE
scn.render.bake.use_cage = False

# === 11-13. 烘焙 ===
nt_low = low.data.materials[0].node_tree
n_alb   = next(n for n in nt_low.nodes if n.label=='albedo')
n_norm  = next(n for n in nt_low.nodes if n.label=='norm')
n_rough = next(n for n in nt_low.nodes if n.label=='roughness')

bake_albedo_via_emit(high, low, n_alb)
bake_standard(high, low, n_norm,  'NORMAL')
bake_standard(high, low, n_rough, 'ROUGHNESS')

# 保存
for img in (img_alb, img_norm, img_rough):
    img.save_render(img.filepath_raw)

# === 14-15. 导出内嵌纹理 FBX + 自动清理 ===
sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts')
import importlib, export_fbx; importlib.reload(export_fbx)
export_fbx.export_fbx(objects=LOW_NAME, scale=1.0,
                      armature=False, animation=False,
                      embed_textures=True, path_mode='COPY')
# → outPut/saber_Low.fbx (内嵌 3 张 PNG)
```

---

## 输出位置

| 文件 | 路径 |
|------|------|
| 高模 OBJ（中间产物）| `outPut/<name>_High.obj` |
| 低模 OBJ（减面结果）| `outPut/<日期>/<name>_High.obj` |
| 烘焙图 | `outPut/baked/bake_<name>_albedo.png` 等 |
| **最终低模 FBX** | `outPut/<name>_Low.fbx` ← 含内嵌烘焙纹理 |

---

## 整体故障排查

1. **物体未找到**：检查物体名拼写，用 get_scene_info 列出所有物体
2. **FBX 文件极小（<10KB）**：物体可能无网格数据，用 get_object_info 检查
3. **导入引擎后比例错误**：调整 scale 参数（Unity=0.01）
4. **骨骼未导出**：确认 armature=True 且骨骼是物体父级
5. **导出后输出目录有 .mtl/.png 散落**：`export_fbx.py` 已内置自动清理；如残留说明清理函数被绕过
6. **烘焙后低模在材质预览下偏黑**：参考 "高模烘焙到低模流程" → 故障排查表
7. **MCP 返回空响应**：参考 `KB/Skills/blender-mcp-connector/SKILL.md` 的「Stale Wrapper Process Pollution」清污 SOP
