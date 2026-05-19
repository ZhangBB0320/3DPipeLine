# Blender FBX/OBJ 导出知识库

## 导出脚本

位置：`Scripts/export_fbx.py`

本机 Blender 路径：`/Applications/Blender.app/Contents/MacOS/blender`

### 三种调用方式对比

| 方式 | 需要Blender运行 | 需要MCP | 适用场景 |
|------|:-:|:-:|------|
| BlenderMCP execute_code | ✅ | ✅ | 交互式操作，Blender 已打开 |
| 命令行 blender --background | ❌ | ❌ | 批量/自动化，无需打开 Blender |
| Blender 内置脚本编辑器 | ✅ | ❌ | 手动调试 |

### 方式1：BlenderMCP execute_code（交互式，需 Blender 运行）

```python
import sys
sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts')
from export_fbx import export_fbx, export_obj, get_scene_info

# 查看场景物体
get_scene_info()

# FBX 导出
export_fbx(objects="wooden_axe", embed_textures=True, path_mode='COPY')

# OBJ 导出（纯几何）
export_obj(objects="wooden_axe")

# 带骨骼动画
export_fbx(objects="character", armature=True, animation=True, scale=0.01)
```

### 方式2：命令行 --background（无需打开 Blender，无需 MCP）

核心原理：`blender --background` 以无界面模式启动 Blender，加载 .blend 文件后可以完整使用 bpy API 读取场景信息和导出。

**查看 .blend 文件中的场景信息：**

```bash
/Applications/Blender.app/Contents/MacOS/blender --background model.blend \
  --python Scripts/export_fbx.py -- --info
```

输出示例：
```
SCENE_JSON:{"name":"Scene","objects":[{"name":"wooden_axe","type":"MESH"}],"materials":["wooden_axe"]}
```

**导出 FBX：**

```bash
/Applications/Blender.app/Contents/MacOS/blender --background model.blend \
  --python Scripts/export_fbx.py -- --objects "wooden_axe" --scale 0.01
```

**导出 OBJ：**

```bash
/Applications/Blender.app/Contents/MacOS/blender --background model.blend \
  --python Scripts/export_fbx.py -- --objects "wooden_axe" --format obj
```

**批量处理（shell 脚本）：**

```bash
BLENDER=/Applications/Blender.app/Contents/MacOS/blender
SCRIPT=Scripts/export_fbx.py

for blend in models/*.blend; do
    $BLENDER --background "$blend" --python $SCRIPT -- --format fbx --scale 0.01
done
```

### 方式3：BlenderMCP 直接传代码片段

```python
import bpy
bpy.ops.object.select_all(action='DESELECT')
obj = bpy.data.objects.get("物体名")
obj.select_set(True)
bpy.context.view_layer.objects.active = obj

# FBX
bpy.ops.export_scene.fbx(
    filepath="/Users/zbb/3DPipeLine/outPut/物体名.fbx",
    use_selection=True, global_scale=1.0,
    path_mode='COPY', embed_textures=True,
)

# OBJ (Blender 4.x+)
bpy.ops.wm.obj_export(
    filepath="/Users/zbb/3DPipeLine/outPut/物体名.obj",
    export_selected_objects=True,
)
```

### 参数参考

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| objects | str/list | None | 物体名，逗号分隔；空则导出全场景 |
| output_path | str | None | 输出路径；空则自动到 outPut/ |
| format | str | fbx | 导出格式: fbx / obj |
| scale | float | 1.0 | 全局缩放比例 |
| armature | bool | False | 是否导出骨骼（仅 FBX） |
| animation | bool | False | 是否导出动画（仅 FBX） |
| embed_textures | bool | True | 是否内嵌纹理（仅 FBX） |
| path_mode | str | COPY | 路径模式: COPY/AUTO/ABSOLUTE/RELATIVE（仅 FBX） |
| apply_transforms | bool | True | 是否将旋转缩放烘焙到顶点 |
| forward | str | -Z | 前轴方向 |
| up | str | Y | 上轴方向 |
| only_selected | bool | False | 仅导出当前选中物体 |
| info | flag | - | 命令行专用：仅输出场景信息不导出 |

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

#### 纯几何 OBJ（用于减面等后处理管线）
```python
export_obj(objects="prop")
# 或命令行:
# blender --background prop.blend --python export_fbx.py -- --format obj --objects "prop"
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

### Blender 版本 API 差异

| 功能 | Blender 3.x | Blender 4.x+ |
|------|-------------|---------------|
| OBJ 导出 | `bpy.ops.export_scene.obj()` | `bpy.ops.wm.obj_export()` |
| FBX 导出 | `bpy.ops.export_scene.fbx()` | `bpy.ops.export_scene.fbx()`（兼容） |

脚本内部已做版本兼容处理。

### 输出目录结构

```
3DPipeLine/
  outPut/           ← 默认输出目录
    model_name.fbx  ← 单物体 FBX 导出
    model_name.obj  ← 单物体 OBJ 导出
    merged_export.fbx ← 多物体合并导出
    Scene.fbx       ← 全场景导出
```
