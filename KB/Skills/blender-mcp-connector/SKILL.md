# BlenderMCP Connector Skill

## 唯一指定连接方式

**所有 agent 连接 BlenderMCP 必须使用 `Scripts/blender_connect.py`，禁止任何其他方式。**

旧方式（已废弃，禁止使用）：
- ~~`sys.path.insert` + 手动 socket 连接~~
- ~~`uvx blender-mcp`~~
- ~~直接使用 MCP wrapper 的 `execute_blender_code`~~
- ~~手写 TCP 连接代码~~

---

## 三脚本流水线 — 强制要求

**导入、烘焙、导出操作必须且只能使用以下三个脚本，禁止手写内联代码或使用其他方式。**

### 1. 导入 — `Scripts/import_fbx.py`

**用途**：安全导入 FBX（防贴图污染）或 OBJ（纯几何）到 Blender 场景。

```python
import sys; sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts')
from import_fbx import safe_import, batch_import_and_run, batch_import_and_bake

# 单文件导入
new_objs = safe_import("/path/to/model.fbx")   # 自动防污染 + 按文件名重命名
new_objs = safe_import("/path/to/model.obj")    # OBJ 导入（无需防污染）

# 批量导入并烘焙
batch_import_and_bake(
    file_paths=["/path/a.fbx", "/path/b.fbx"],
    name_pattern="Building{idx_1based}",
    target_faces=3000, cage=0.1, res=4096, samples=64, device="GPU",
)
```

**防污染原理**：FBX 导入前自动隔离所有已有 image（加 `__guard_` 前缀 + 清空 filepath），切断 Blender 按名复用内存图像的路径。

**禁止**：
- ~~直接调用 `bpy.ops.import_scene.fbx()`~~ （无防污染保护）
- ~~手写隔离逻辑~~ （必须用 `safe_import()` 或 `isolate_existing_images()`）

### 2. 烘焙 — `Scripts/bake.py`

**用途**：高模 → Blender 内减面 → 烘焙 Albedo(EMIT) + Normal → 高低模都留场景。

```python
from bake import bake_high_to_low, purge_bake_residue

# 模式 A（推荐）：Blender 内 Decimate 减面 + 烘焙
bake_high_to_low(high_obj_name="building1", name="Building1",
                 target_faces=3000, cage=0.1, res=4096, samples=64, device="GPU")

# 模式 B：外部 OBJ 低模 + 对齐 + 烘焙
bake_high_to_low(high_obj_name="building1", low_obj="/path/low.obj", name="Building1")

# 清场
purge_bake_residue()
```

**核心规则**：
- Albedo 必须用 EMIT 烘焙（禁用 DIFFUSE）
- cage=0.1, res=4096, samples=64, Cycles+GPU
- Smart UV island_margin=0.01
- 烘焙后高模资源自动加 `__baked_` 前缀隔离

**禁止**：
- ~~手写内联烘焙代码~~ （必须通过 `bake_high_to_low()` 执行）
- ~~使用 DIFFUSE 烘焙 Albedo~~ （必须用 EMIT）
- ~~直接修改 bake.py~~ （先复制为 bake_v2.py 测试，确认后再合并）

### 3. 导出 — `Scripts/export_model.py`

**用途**：将场景中指定对象导出为 FBX（内嵌纹理）或 OBJ（纯几何）。

```python
from export_model import export_model, export_all_meshes

# 导出为 FBX（内嵌纹理）
export_model(obj_names=["Building1_Low"], output_path="/tmp/building1.fbx", fmt="fbx")

# 导出为 OBJ（纯几何）
export_model(obj_names=["Building1_Low"], output_path="/tmp/building1.obj", fmt="obj")

# 自动判断格式
export_model(obj_names=["Building1_Low"], output_path="/tmp/building1", fmt="auto")

# 批量导出场景中所有 mesh
export_all_meshes(output_dir="/tmp/output", fmt="auto")
```

**禁止**：
- ~~直接调用 `bpy.ops.export_scene.fbx()`~~ （不保证内嵌纹理正确）
- ~~直接调用 `bpy.ops.wm.obj_export()`~~ （不保证参数一致）

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
| `/Users/zbb/3DPipeLine/Scripts/bake.py` | 烘焙脚本（EMIT法 + 资源隔离） |
| `/Users/zbb/3DPipeLine/Scripts/export_model.py` | 导出脚本（FBX内嵌纹理 / OBJ纯几何） |
| `/Users/zbb/3DPipeLine/Scripts/safe_fbx_import_addon.py` | Blender GUI 导入保护 addon |
