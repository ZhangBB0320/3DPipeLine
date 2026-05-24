# BlenderMCP Connector Skill

## 唯一指定连接方式

**所有 agent 连接 BlenderMCP 必须使用 `Scripts/blender_connect.py`，禁止任何其他方式。**

旧方式（已废弃，禁止使用）：
- ~~`sys.path.insert` + 手动 socket 连接~~
- ~~`uvx blender-mcp`~~
- ~~直接使用 MCP wrapper 的 `execute_blender_code`~~
- ~~手写 TCP 连接代码~~

---

## 五脚本流水线 — 强制要求

**导入、减面、烘焙、导出操作必须且只能使用以下脚本，禁止手写内联代码或使用其他方式。**

### 1. 导入 — `Scripts/import_fbx.py`

**用途**：安全导入 FBX（防贴图污染）或 OBJ（纯几何）到 Blender 场景。

```python
import sys; sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts')
from import_fbx import safe_import

# 单文件导入
new_objs = safe_import("/path/to/model.fbx")   # 自动防污染 + 按文件名重命名
new_objs = safe_import("/path/to/model.obj")    # OBJ 导入（无需防污染）
```

**防污染原理**：FBX 导入前自动隔离所有已有 image（加 `__guard_` 前缀 + 清空 filepath），切断 Blender 按名复用内存图像的路径。

**禁止**：
- ~~直接调用 `bpy.ops.import_scene.fbx()`~~ （无防污染保护）
- ~~手写隔离逻辑~~ （必须用 `safe_import()` 或 `isolate_existing_images()`）

### 2. 减面 — `Scripts/decimation/decimate_blender.py`

**用途**：复制高模 → COLLAPSE 减面 → 二分法精确控面数 → 流形修复（默认启用）。

```python
import sys; sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts/decimation')
from decimate_blender import decimate_blender

# 默认：减面 + 流形修复
low_name, actual_faces = decimate_blender("building1", target_faces=3000)

# 自定义名称
decimate_blender("building1", target_faces=3000, name="MyHouse")

# 禁用流形修复（旧行为）
decimate_blender("building1", target_faces=3000, repair_mesh=False)

# 自定义合并距离
decimate_blender("building1", target_faces=3000, merge_distance=0.001)
```

**流形修复 5 步**（默认启用 `repair_mesh=True`）：
1. 删除退化面（零面积/零法线）
2. 删除非流形边及其关联面
3. 删除松散几何（无边/无面的顶点和边）
4. 合并重叠顶点（by distance）
5. 重新计算法线

**禁止**：
- ~~直接调用 `bpy.ops.object.modifier_apply()` 减面~~ （必须通过 `decimate_blender()` 执行）
- ~~使用 `decimate_progressive.py`~~ （已删除，功能已合并到 `decimate_blender.py`）

### 3. 烘焙 — `Scripts/bake_scene.py`

**用途**：高模 → Shrinkwrap 包裹 → Smart UV → 烘焙 Albedo(EMIT) + Normal → Principled BSDF。

```python
import sys; sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts')
from bake_scene import bake_scene

# 标准烘焙
bake_scene(high_obj_name="building1", low_obj_name="MyHouse_Low", name="MyHouse",
           cage=0.1, res=4096, samples=64, device="GPU")
```

**核心规则**：
- Albedo 必须用 EMIT 烘焙（禁用 DIFFUSE）
- cage=0.1, res=4096, samples=64, Cycles+GPU
- Smart UV island_margin=0.01
- Shrinkwrap 包裹高低模

**禁止**：
- ~~手写内联烘焙代码~~ （必须通过 `bake_scene()` 执行）
- ~~使用 DIFFUSE 烘焙 Albedo~~ （必须用 EMIT）
- ~~使用旧版 `bake.py` / `bake_high_to_low()`~~ （已废弃）

### 4. 导出 — `Scripts/export_model.py`

**用途**：将场景中指定对象导出为 FBX（内嵌纹理）或 OBJ（纯几何）。

```python
from export_model import export_model

# 导出为 FBX（内嵌纹理）
export_model(obj_names=["Building1_Low"], output_path="/tmp/building1.fbx", fmt="fbx")

# 导出为 OBJ（纯几何）
export_model(obj_names=["Building1_Low"], output_path="/tmp/building1.obj", fmt="obj")
```

**禁止**：
- ~~直接调用 `bpy.ops.export_scene.fbx()`~~ （不保证内嵌纹理正确）

### 5. 动画迁移 — `Scripts/transfer_animation.py`

**用途**：将动画从源骨架迁移到目标骨架，兼容 Blender 4.x layered action。

```python
import sys; sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts')
from transfer_animation import transfer_animation

transfer_animation(src_armature="A", src_action_name="AnimX",
                   dst_armature="B", dst_action_name="AnimY")
```

---

## CLI 用法

```bash
# 连接 Blender
python3 /Users/zbb/3DPipeLine/Scripts/blender_connect.py --fix --verbose

# 在 Blender 中执行脚本
python3 -c "
from blender_connect import send_python
send_python('''
import sys; sys.path.insert(0, \"/Users/zbb/3DPipeLine/Scripts\")
from import_fbx import safe_import
safe_import(\"/path/to/model.fbx\")
''')
"
```

退出码：0=OK, 1=Blender未启动, 2=9876未监听/被占用, 3=wrapper污染, 4=addon异常, 5=其他

---

## Python API 用法（agent 代码内 import）

```python
import sys
sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts')
from blender_connect import ensure_blender, send_python, BlenderConnectError

try:
    ensure_blender()
    r = send_python("import bpy; bpy.ops.mesh.primitive_cube_add()")
except BlenderConnectError as e:
    print(f"连接失败 (exit={e.exit_code}):", e)
```

长任务（烘焙等）需增大超时：
```python
send_python("bpy.ops.object.bake()", recv_timeout=300)
```

---

## 长任务规则

1. bake() / smart_project() / 大 import 等耗时操作必须单独调用，不要串行
2. samples 用 64 即可（128 太慢），device 优先 GPU
3. 烘焙超时但不要重复调用 —— 等 1-2 分钟用 `--diag-only` 探活
4. 长任务必须设 `recv_timeout=300`（默认 300s，足够烘焙）

---

## 依赖文件

| 文件 | 用途 |
|---|---|
| `/Users/zbb/3DPipeLine/Scripts/blender_connect.py` | BlenderMCP 连接脚本 |
| `/Users/zbb/3DPipeLine/Scripts/blender_mcp_doctor.sh` | wrapper 污染诊断+清理 |
| `/Users/zbb/3DPipeLine/Scripts/import_fbx.py` | 安全导入（FBX防污染 + OBJ） |
| `/Users/zbb/3DPipeLine/Scripts/decimation/decimate_blender.py` | 减面 + 流形修复（COLLAPSE + 二分法 + bmesh 修复） |
| `/Users/zbb/3DPipeLine/Scripts/decimation/decimate_external.py` | 外部 Open3D 减面（备选） |
| `/Users/zbb/3DPipeLine/Scripts/bake_scene.py` | 烘焙脚本（Shrinkwrap + EMIT + Normal → Principled BSDF） |
| `/Users/zbb/3DPipeLine/Scripts/export_model.py` | 导出脚本（FBX内嵌纹理 / OBJ纯几何） |
| `/Users/zbb/3DPipeLine/Scripts/transfer_animation.py` | 动画迁移（兼容 Blender 4.x layered action） |
| `/Users/zbb/3DPipeLine/Scripts/safe_fbx_import_addon.py` | Blender GUI 导入保护 addon |
