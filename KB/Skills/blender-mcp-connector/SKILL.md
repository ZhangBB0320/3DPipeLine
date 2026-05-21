# BlenderMCP Connector Skill

## 唯一指定连接方式

**所有 agent 连接 BlenderMCP 必须使用 `Scripts/blender_connect.py`，禁止任何其他方式。**

旧方式（已废弃，禁止使用）：
- ~~`sys.path.insert` + 手动 socket 连接~~
- ~~`uvx blender-mcp`~~
- ~~直接使用 MCP wrapper 的 `execute_blender_code`~~
- ~~手写 TCP 连接代码~~

---

## CLI 用法

```bash
# 标准连接（自动修复 + 详细输出）
python3 /Users/zbb/3DPipeLine/Scripts/blender_connect.py --fix --verbose

# 只诊断不连接
python3 /Users/zbb/3DPipeLine/Scripts/blender_connect.py --diag-only

# 连接并在 Blender 内执行代码
python3 /Users/zbb/3DPipeLine/Scripts/blender_connect.py --exec "import bpy; print(len(bpy.data.objects))"
```

退出码：0=OK, 1=Blender未启动, 2=9876未监听/被占用, 3=wrapper污染, 4=addon异常, 5=其他

---

## Python API 用法（agent 代码内 import）

```python
import sys
sys.path.insert(0, '/Users/zbb/3DPipeLine/Scripts')
from blender_connect import ensure_blender, send_python, BlenderConnectError

try:
    ensure_blender()  # 保证连接可用，否则抛 BlenderConnectError（含完整诊断）
    r = send_python("import bpy; bpy.ops.mesh.primitive_cube_add()")
except BlenderConnectError as e:
    print(f"连接失败 (exit={e.exit_code}):", e)
    print("诊断快照:", e.diagnostics)
```

长任务（烘焙等）需增大超时：
```python
send_python("bpy.ops.object.bake()", recv_timeout=300)
```

---

## 脚本工作原理

1. **直接 TCP 连接 Blender 内 addon 监听的 9876 端口** —— 绕过 MCP wrapper，最稳定（不受 wrapper 污染影响）
2. **完整诊断**（每次失败都输出）：
   - Blender 进程数
   - 9876 端口监听状态 + listener 是否是 Blender（防止端口被其他进程占用）
   - wrapper 进程数（健康 ≤3 / 污染 ≥4）
   - ESTABLISHED 连接数
3. **自动恢复**：wrapper 污染时自动调用 `blender_mcp_doctor.sh --fix` 清理
4. **3 次重试 + 指数退避**：偶发 socket 失败可自愈
5. **macOS pgrep 兼容**：以 9876 端口 listener 是否为 Blender 为权威判断，不依赖 pgrep

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
| `/Users/zbb/3DPipeLine/Scripts/blender_connect.py` | 主连接脚本 |
| `/Users/zbb/3DPipeLine/Scripts/blender_mcp_doctor.sh` | wrapper 污染诊断+清理 |
| `/Users/zbb/3DPipeLine/Scripts/bake.py` | 烘焙脚本（通过 blender_connect.py 调用） |
