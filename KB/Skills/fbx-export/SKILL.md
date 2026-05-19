# FBX Export Skill

将 Blender 场景中的物体导出为 FBX 文件，支持比例缩放、骨骼动画、内嵌纹理等。

## 触发条件

当用户提出以下需求时激活此 Skill：
- 导出/打包 FBX
- 从 Blender 输出模型
- 将物体转为 FBX 格式
- 批量导出 FBX
- 减面/简化模型后导出（OBJ/FBX）
- LOD 制作

## 前置条件

- Blender 已运行且 BlenderMCP 连接正常
- 目标物体在 Blender 场景中存在

## 执行流程

### 1. 获取场景信息

```python
# 通过 BlenderMCP get_scene_info 确认物体存在
```

### 2. 执行导出

通过 BlenderMCP `execute_code` 调用导出脚本：

```python
import sys
sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts')
from export_fbx import export_fbx

# 根据用户需求设置参数
export_fbx(
    objects="目标物体名",
    scale=1.0,            # Unity 用 0.01
    armature=False,        # 角色模型开 True
    animation=False,       # 需要动画时开 True
    embed_textures=True,   # 默认内嵌纹理
    path_mode='COPY',      # 路径模式=复制
)
```

### 3. 强制清理垃圾文件

导出完成后，**必须**删除输出目录中除目标 FBX 外的所有伴随文件：
- `.mtl`、`.jpg`、`.png`、`.exr`、`.tga`、`.bmp`、`.tif`、`.tiff`、`.webp`、`.dds`

只保留目标 FBX（或 OBJ）文件。此步骤为强制规则，不得跳过。

### 4. 验证输出

确认 `outPut/` 目录下**仅**有目标 FBX 文件，无其他垃圾文件。

## 常见场景速查

| 场景 | 参数 |
|------|------|
| 静态模型（默认） | `objects="name"` |
| 导出至 Unity | `objects="name", scale=0.01` |
| 角色带骨骼动画 | `objects="name", armature=True, animation=True, scale=0.01` |
| 纯几何无纹理 | `objects="name", embed_textures=False` |
| 批量多物体 | `objects="obj1,obj2,obj3"` |
| 全场景 | 不传 objects 参数 |

## 减面流程

当用户要求减面并导出 OBJ 时，必须执行以下流程，确保低模与原模型 Transform 完全一致。

### 1. 记录原模型 Transform

```python
# 通过 BlenderMCP execute_code 记录原模型的位置/旋转/缩放
src = bpy.data.objects["原模型名"]
original_location = tuple(src.location)
original_rotation = tuple(src.rotation_euler)
original_scale = tuple(src.scale)
```

### 2. 导出 OBJ → 减面 → 导入回 Blender

```bash
# 导出 OBJ
python3 Scripts/export_fbx.py --format obj <blend_file> --object <物体名>

# pymeshlab 减面
python3 Scripts/decimate.py outPut/<物体名>.obj --faces <目标面数> -o outPut/<物体名>_LOD.obj

# 通过 BlenderMCP import_asset 导入低模 OBJ
```

### 3. 对齐 Transform（强制规则）

低模导入后，**必须**将 location/rotation/scale 设置为与原模型完全一致：

```python
# OBJ 导入可能导致 Y/Z 轴互换，需要修正
lod = bpy.data.objects["低模名"]
lod.location = original_location
lod.rotation_euler = original_rotation
lod.scale = original_scale

# 应用变换，烘焙到顶点数据
bpy.context.view_layer.objects.active = lod
lod.select_set(True)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
```

### 4. 验证 Transform 一致性（强制步骤）

```python
# 比较世界空间包围盒，确认低模与原模型完全重合
src_bbox = src.matrix_world @ src.bound_box[i]  # 原模型8个顶点
lod_bbox = lod.matrix_world @ lod.bound_box[i]  # 低模8个顶点
# 所有对应顶点偏差应 < 0.001
```

**此步骤为强制规则，不允许跳过。低模必须与原模型在位置/缩放/旋转上完全重合。**

## 输出位置

默认输出到 `/Users/zbb/3DPipeLine/outPut/<物体名>.fbx`

## 故障排查

1. **物体未找到**：检查物体名拼写，用 get_scene_info 列出所有物体
2. **FBX 文件为空或极小**：物体可能无网格数据，用 get_object_info 检查
3. **纹理丢失**：确认 Blender 中材质有纹理贴图，且 embed_textures=True + path_mode='COPY'
4. **导入引擎后比例错误**：调整 scale 参数（Unity=0.01）
5. **骨骼未导出**：确认 armature=True 且骨骼是物体父级
