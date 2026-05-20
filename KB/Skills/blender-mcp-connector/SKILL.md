---
name: blender-mcp-connector
description: This skill should be used whenever the user asks CodeBuddy to connect to Blender, verify Blender MCP availability, query the current Blender scene, or run Blender Python operations through MCP. Trigger phrases include "connect Blender", "连接 Blender", "BlenderMCP", "查看 Blender 场景", "在 Blender 中执行", "截图 Blender 视口", or any request that requires interacting with a running Blender instance via MCP. The skill enforces a one-shot, low-overhead connection procedure that avoids redundant diagnostics and uses the already-configured MCP server entry.
---

# Blender MCP Connector

Establish a fast, reliable Blender MCP connection from CodeBuddy and run common Blender operations without redundant diagnostics.

## When To Use

Use this skill whenever the user requests:
- A connection to Blender via MCP ("connect Blender", "连接 BlenderMCP")
- Reading the current Blender scene (objects, materials)
- Inspecting a specific Blender object
- Capturing a viewport screenshot
- Executing arbitrary Python code inside Blender
- Generating or importing 3D assets via Polyhaven, Sketchfab, Hyper3D Rodin, or Hunyuan3D

Do NOT use this skill for:
- Pure file-based 3D pipeline tasks that don't touch a running Blender instance
- Unity/UGUI/Layout work (use Unity MCP instead)

## Core Principle: Connect In One Shot

The Blender MCP server is **already configured** in `c:\Users\<user>\.codebuddy\mcp.json` under the key `"blender"`. The user's Blender is typically running with the MCP addon enabled. Do not attempt to:
- Run `python -m blender_mcp.server --help` or other process-level diagnostics
- Inspect `mcp.json` before every call
- Call `mcp_get_tool_description` repeatedly for tools whose schema is already known (see Reference Tool Schemas below)

The standard connection procedure is **a single `mcp_call_tool` invocation of `get_scene_info`**. If it returns a JSON object with `name`, `object_count`, and `objects`, the connection is healthy — report success immediately.

## Standard Connection Procedure

To connect and verify Blender MCP, perform exactly one tool call:

```
mcp_call_tool(
  serverName="blender",
  toolName="get_scene_info",
  arguments={"user_prompt": "<the user's original request>"}
)
```

A successful response looks like:

```json
{
  "name": "Scene",
  "object_count": 1,
  "objects": [
    {"name": "wooden_handle_saber", "type": "MESH", "location": [0.0, 0.0, 0.0]}
  ],
  "materials_count": 1
}
```

On success, summarize the scene (object count, key object names, materials) and stop. Do NOT run additional verification steps.

On failure (timeout, connection refused, empty response), report the failure once and ask the user to check:
1. Blender is running
2. The Blender MCP addon panel (sidebar `N`) shows "Server Running"
3. No firewall/AV is blocking the loopback socket

Do not loop retries or run shell diagnostics unless the user explicitly asks.

## CodeBuddy MCP Configuration Reference

The `blender` MCP server entry expected in `mcp.json`:

```json
"blender": {
  "command": "C:\\Python312\\python.exe",
  "args": ["-m", "blender_mcp.server"],
  "enabled": true,
  "disabled": false
}
```

Notes:
- The Python interpreter path must have `blender_mcp` package installed.
- The server is a stdio MCP wrapper; it connects via socket to the Blender addon listening inside Blender.
- Cold-start of the wrapper process takes ~1-3 seconds on the first call per session; subsequent calls are sub-second.
- If the user reports "connection is slow", the bottleneck is almost always cold-start, NOT a real failure. Do not treat the first call's latency as an error.

## Reference Tool Schemas (Skip mcp_get_tool_description for these)

The following tools have stable, known schemas. Call them directly via `mcp_call_tool` without prefetching descriptions.

### Inspection
- `get_scene_info` — `{"user_prompt": str}` → scene summary
- `get_object_info` — `{"object_name": str, "user_prompt": str}` → object detail
- `get_viewport_screenshot` — `{"max_size": int = 1000, "user_prompt": str}` → PNG image

### Execution
- `execute_blender_code` — `{"code": str, "user_prompt": str}` → exec result
  - Break long scripts into smaller chunks for reliability.

### Asset Integrations (only when user asks)
- `get_polyhaven_status`, `get_polyhaven_categories`, `search_polyhaven_assets`, `download_polyhaven_asset`, `set_texture`
- `get_sketchfab_status`, `search_sketchfab_models`, `get_sketchfab_model_preview`, `download_sketchfab_model`
- `get_hyper3d_status`, `generate_hyper3d_model_via_text`, `generate_hyper3d_model_via_images`, `poll_rodin_job_status`, `import_generated_asset`
- `get_hunyuan3d_status`, `generate_hunyuan3d_model`, `poll_hunyuan_job_status`, `import_generated_asset_hunyuan`

For these integration tools, status checks are only needed once per session if the user wants to use that specific provider. Do not preflight-check all of them.

## Common Pitfalls (Learned From Real Sessions)

1. **Treating a successful response as empty.** The `get_scene_info` JSON response is the success signal. Do not chase further "verification."
2. **Running shell commands to "diagnose" the MCP wrapper.** The user has stated multiple times that Blender + MCP are running; trust that and just call the MCP tool.
3. **Calling `mcp_get_tool_description` before every `mcp_call_tool`.** For Blender tools listed above, schemas are stable; skip the description fetch.
4. **Switching to alternative tools after one slow call.** First-call latency (cold start) is normal. Wait for the result.
5. **Asking the user to re-confirm Blender state when they already confirmed.** Respect the user's stated context.

## Critical: Stable mcp.json Configuration (2026-05-19 重大升级)

**旧配置（不稳定，已废弃）：**
```json
"blender": {
  "command": "uvx",
  "args": ["blender-mcp"],
  "disabled": false
}
```
问题：
1. `uvx` 每次启动都重新解析 ephemeral 环境，启动慢（~0.85s）。
2. 进程链层层嵌套：`uv tool uvx blender-mcp` → 内部又 spawn `uv tool uvx blender-mcp` → 最后才到 `python blender-mcp`。多个 PID。
3. 缓存路径（`~/.cache/uv/archive-v0/<hash>/`）会被 uv GC，导致 wrapper 突然找不到 Python。
4. 不同 CodeBuddy Plugin Helper 进程各自启动 wrapper，互相不知道，旧的不退出。

**新配置（稳定，已生效）：**
```json
"blender": {
  "command": "/Users/zbb/.local/share/uv/tools/blender-mcp/bin/python",
  "args": ["-m", "blender_mcp.server"],
  "env": {
    "PYTHONUNBUFFERED": "1",
    "PYTHONDONTWRITEBYTECODE": "1"
  },
  "timeout": 120,
  "disabled": false
}
```

**前置安装**（一次即可）：
```bash
uv tool install blender-mcp
# 创建稳定路径 /Users/zbb/.local/share/uv/tools/blender-mcp/，独立 Python 3.12 venv
```

**优势：**
- 启动 < 0.5s（直接 `python -m`，零中间层）。
- 进程扁平：一个 Plugin Helper → 一个 python 进程。
- 路径稳定，uv cache GC 不会失效。
- `timeout: 120` 给长烘焙留出缓冲。
- `PYTHONUNBUFFERED=1` 防 stdio 缓冲卡死。

## Critical: Stale Wrapper Process Pollution (Persistent Issue)

即使用了新配置，CodeBuddy 多 Plugin Helper 仍会各自 spawn wrapper（架构限制）。重载窗口/切 workspace 后旧 wrapper 不退出。

**Symptom**: `get_scene_info` / `get_viewport_screenshot` / `execute_blender_code` 返回 `Request timed out` 或空结果。

**Diagnostic（一键诊断）**：
```bash
bash /Users/zbb/3DPipeLine/Scripts/blender_mcp_doctor.sh
```
脚本输出：
- Blender 进程数（应 = 1）
- 9876 端口监听数（应 = 1）
- wrapper 进程数（健康 1-2；污染 ≥4）
- ESTABLISHED 连接数（健康 0-2；污染 ≥4）
- blender-mcp 包安装状态

**Fix（一键清理）**：
```bash
bash /Users/zbb/3DPipeLine/Scripts/blender_mcp_doctor.sh --fix
# 等价于：pkill -9 -f "blender-mcp" 后 CodeBuddy 自动重建
```

## Critical: Long-Running Bake Operations Cause Timeout

**根因**：`bpy.ops.object.bake()` 阻塞 Blender 主线程几十秒到几分钟。期间：
- `blender-mcp` server 内部 socket 超时设为 180s，会一直等。
- CodeBuddy → wrapper 的 stdio 默认超时约 30-60s，**先于** wrapper 报 timeout。
- 但 Blender 那边可能还在烘！`bpy.ops` 完成时，wrapper 的 socket 已经被 CodeBuddy 关闭。

**规则**：
1. 任何 `bake()`、`smart_project()`、大模型 import/decimate 等耗时操作，**单独成一次 mcp 调用**。
2. 一次只烘一张图（albedo / normal / roughness 分三次调用），不要在一个 `execute_blender_code` 里串行烘 3 张。
3. samples 控制：bake 用 64 即可（128 太慢），device 优先 GPU。
4. 调用前先打印 "BAKE START"，调用后打印 "BAKE DONE"，方便从 stderr 判断卡在哪。

如果烘焙超时但 Blender 仍在运行，**不要重复调用** —— 等 1-2 分钟后用 `get_scene_info` 探活，再查烘焙图是否已经写盘 (`outPut/baked/*.png`)。

**Prevention**:
- Avoid opening multiple CodeBuddy windows on the same workspace.
- After any CodeBuddy window reload (`Developer: Reload Window`), run the doctor and fix if needed before resuming Blender work.
- If MCP starts misbehaving mid-session, run the fix command **first** before reporting "connection failed" to the user.
- 长任务拆分多次小调用，每次 ≤ 30s。

## Recommended Response Pattern

When the user says "connect Blender" or similar:

1. Make exactly one `mcp_call_tool` call to `get_scene_info`.
2. On success, in one short message: confirm connection, list scene name, object count, and notable object names; offer next-step suggestions (screenshot, run code, inspect object).
3. On failure, in one short message: report the specific error and the three checks listed above.

Do not produce step-by-step "I am now checking..." narration. Keep replies tight.
