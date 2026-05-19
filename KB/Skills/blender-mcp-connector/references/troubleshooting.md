# Blender MCP 连接问题排查参考

本文档记录用户在使用 CodeBuddy + Blender MCP 时遇到的真实问题与正确处理方式，供 CodeBuddy 在排查阶段参考。

## 1. "为什么连接很慢？"

**真实原因**：MCP wrapper 进程冷启动 + 第一次 socket 握手需要 1-3 秒。这是 stdio MCP 的天然行为，不是故障。

**错误做法**：
- 反复调用 `mcp_get_tool_description` 做"探测"
- 启动 PowerShell 跑 `python -m blender_mcp.server --help`
- 多次重发 `get_scene_info` 想"再试一次"

**正确做法**：直接发 1 次 `get_scene_info`，等待返回。如果返回了合法 JSON（含 `name`/`object_count`/`objects`），就是成功。

## 2. "返回结果看起来是空的"

**真实原因**：CodeBuddy 把成功的 JSON 响应误判为空。例如下面这个响应是**成功**：

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

只要 JSON 顶层有 `name` 字段，就视为连接成功。

## 3. "用户多次说 Blender 已经开了，但 Agent 还是要去检查"

这是在浪费用户耐心。规则：**用户明确告知 Blender 状态正常时，跳过所有进程级检查，直接调用 MCP 工具**。失败再说，不要预防性诊断。

## 4. mcp.json 配置示例（Windows）

路径：`c:\Users\<user>\.codebuddy\mcp.json`

```json
{
  "mcpServers": {
    "blender": {
      "command": "C:\\Python312\\python.exe",
      "args": ["-m", "blender_mcp.server"],
      "enabled": true,
      "disabled": false
    }
  }
}
```

要求：
- `C:\Python312\python.exe` 已安装且包含 `blender_mcp` 包（`pip install blender-mcp` 或本地 wheel）
- Blender 端已安装并启用 BlenderMCP 插件，且在 N 面板里点了 "Start MCP Server"

## 5. Blender 插件端检查清单（仅当 MCP 调用真的失败时让用户确认）

1. Blender 是否运行
2. 编辑 → 首选项 → 插件，搜索 "MCP"，勾选启用
3. 3D 视图按 N 打开侧边栏，找到 BlenderMCP 面板
4. 面板里点击 "Connect to Claude" 或 "Start MCP Server"，看到 "Server Running" 字样
5. 默认监听端口（通常 9876）未被防火墙/杀软拦截

只在 MCP 调用失败时才让用户走这 5 步，不要预防性询问。

## 6. 常用调用样例

### 查看场景
```
mcp_call_tool(serverName="blender", toolName="get_scene_info",
              arguments={"user_prompt": "查看场景"})
```

### 查看某个对象
```
mcp_call_tool(serverName="blender", toolName="get_object_info",
              arguments={"object_name": "wooden_handle_saber",
                         "user_prompt": "查看军刀模型"})
```

### 截图视口
```
mcp_call_tool(serverName="blender", toolName="get_viewport_screenshot",
              arguments={"max_size": 1000, "user_prompt": "截图当前视口"})
```

### 执行 Python
```
mcp_call_tool(serverName="blender", toolName="execute_blender_code",
              arguments={"code": "import bpy; print(len(bpy.data.objects))",
                         "user_prompt": "统计对象数量"})
```
