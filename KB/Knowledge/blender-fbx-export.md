# Blender FBX 导出知识库

## 导出脚本

位置：`Scripts/export_fbx.py`

### 调用方式

**方式1：通过 BlenderMCP execute_code（推荐）**

```python
# 在 execute_code 中执行以下代码
import sys
sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts')
from export_fbx import export_fbx

# 基础导出
export_fbx(objects="wooden_axe")

# 带骨骼动画
export_fbx(objects="character", armature=True, animation=True)

# 缩放到 Unity 比例 (Blender 1m = Unity 1cm → scale=0.01)
export_fbx(objects="prop", scale=0.01)

# 不内嵌纹理（纹理单独存放）
export_fbx(objects="wooden_axe", embed_textures=False, path_mode='RELATIVE')
```

**方式2：通过 BlenderMCP 直接传代码片段**

```python
import bpy
bpy.ops.object.select_all(action='DESELECT')
obj = bpy.data.objects.get("物体名")
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
bpy.ops.export_scene.fbx(
    filepath="/Users/zbb/3DPipeLine/outPut/物体名.fbx",
    use_selection=True,
    global_scale=1.0,
    path_mode='COPY',
    embed_textures=True,
)
```

**方式3：命令行**

```bash
blender --background --python Scripts/export_fbx.py -- --objects "wooden_axe" --scale 0.01
```

### 参数参考

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| objects | str/list | None | 物体名，逗号分隔；空则导出全场景 |
| output_path | str | None | 输出路径；空则自动到 outPut/ |
| scale | float | 1.0 | 全局缩放比例 |
| armature | bool | False | 是否导出骨骼 |
| animation | bool | False | 是否导出动画 |
| embed_textures | bool | True | 是否内嵌纹理 |
| path_mode | str | COPY | 路径模式: COPY/AUTO/ABSOLUTE/RELATIVE |
| apply_transforms | bool | True | 是否将旋转缩放烘焙到顶点 |
| forward | str | -Z | 前轴方向 |
| up | str | Y | 上轴方向 |
| only_selected | bool | False | 仅导出当前选中物体 |

### 常见场景配置

#### Unity 导入（最常见）
```python
export_fbx(objects="model", scale=0.01, forward='-Z', up='Y')
```

#### Unreal Engine 导入
```python
export_fbx(objects="model", scale=1.0, forward='Y', up='Z')
```

#### 带骨骼动画角色
```python
export_fbx(objects="character", armature=True, animation=True, scale=0.01)
```

#### 纯几何无纹理
```python
export_fbx(objects="prop", embed_textures=False, path_mode='AUTO')
```

### Blender FBX 路径模式说明

| 模式 | 行为 |
|------|------|
| COPY | 复制所有引用文件到输出目录旁 |
| AUTO | 根据相对路径是否可用来决定 |
| ABSOLUTE | 使用绝对路径引用 |
| RELATIVE | 使用相对路径引用 |

### 内嵌纹理 (embed_textures)

- `True`：纹理图片打包进 FBX 文件，文件体积增大但无需额外管理纹理文件
- `False`：纹理作为独立文件存放，需要配合 path_mode 使用
- 仅在 path_mode='COPY' 时内嵌纹理生效

### 缩放比例参考

| 目标引擎 | scale 值 | 说明 |
|----------|----------|------|
| Blender 内部 | 1.0 | 默认米制 |
| Unity | 0.01 | Blender 1m → Unity 100cm = 1单位 |
| Unreal | 1.0 | UE 默认厘米，与 Blender 不同需检查 |
| Godot | 1.0 | Godot 4.x 默认米制 |

### 输出目录结构

```
3DPipeLine/
  outPut/           ← 默认输出目录
    model_name.fbx  ← 单物体导出
    merged_export.fbx ← 多物体合并导出
    Scene.fbx       ← 全场景导出
```
