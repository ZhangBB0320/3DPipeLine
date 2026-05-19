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

## Critical: Stale Wrapper Process Pollution (Confirmed Root Cause)

**Symptom**: `get_scene_info` / `get_viewport_screenshot` / `execute_blender_code` return empty strings or completely empty results, even though Blender is clearly running and the addon panel shows "Server Running".

**Root cause**: CodeBuddy spawns one `uvx blender-mcp` wrapper process **per Plugin Helper / per session**. When the user reloads CodeBuddy, opens new windows, or switches workspaces, **old wrappers are NOT cleaned up**. They keep an ESTABLISHED TCP connection to Blender's port 9876 forever.

When CodeBuddy issues a new MCP request, it goes to the *current* wrapper, which forwards to Blender. Blender responds, but the response routing inside Blender's per-connection thread sometimes ends up on a stale connection's socket buffer. The current wrapper times out / returns empty.

**Diagnostic command** (run in shell):

```bash
# Count wrapper processes (healthy: 1-2; unhealthy: 4+)
ps -ef | grep -E "blender-mcp|blender_mcp" | grep -v grep | wc -l

# Count ESTABLISHED connections to Blender (healthy: 1-2; unhealthy: 4+)
lsof -i :9876 2>/dev/null | grep ESTAB | wc -l
```

If either count is ≥ 4, you have wrapper pollution.

**Fix** (kills all wrappers; CodeBuddy auto-respawns clean ones in ~2s):

```bash
pkill -9 -f "blender-mcp"
# Wait 2 seconds for CodeBuddy to respawn fresh wrappers
```

After cleanup, the next `mcp_call_tool` invocation will work correctly.

**Prevention**:
- Avoid opening multiple CodeBuddy windows on the same workspace.
- After any CodeBuddy window reload (`Developer: Reload Window`), run the diagnostic and fix if needed before resuming Blender work.
- If MCP starts misbehaving mid-session, run the fix command **first** before reporting "connection failed" to the user.

## Recommended Response Pattern

When the user says "connect Blender" or similar:

1. Make exactly one `mcp_call_tool` call to `get_scene_info`.
2. On success, in one short message: confirm connection, list scene name, object count, and notable object names; offer next-step suggestions (screenshot, run code, inspect object).
3. On failure, in one short message: report the specific error and the three checks listed above.

Do not produce step-by-step "I am now checking..." narration. Keep replies tight.
