#!/bin/bash
# Blender MCP Doctor — 一键诊断 + 清理僵尸 wrapper
# Usage:
#   bash blender_mcp_doctor.sh        # 仅诊断
#   bash blender_mcp_doctor.sh --fix  # 诊断 + 自动清理

set -u

echo "========== Blender MCP 健康检查 =========="

# 1. Blender 进程
BLENDER_COUNT=$(pgrep -f "Blender.app" | wc -l | tr -d ' ')
echo "[1] Blender 进程: $BLENDER_COUNT 个"
if [ "$BLENDER_COUNT" -eq 0 ]; then
  echo "    ✗ Blender 未运行 — 请先启动 Blender 并启用 MCP addon"
  exit 1
fi

# 2. 9876 端口监听
LISTEN_COUNT=$(lsof -nP -i :9876 2>/dev/null | grep -c LISTEN)
echo "[2] 9876 端口监听: $LISTEN_COUNT 个"
if [ "$LISTEN_COUNT" -eq 0 ]; then
  echo "    ✗ 没有进程监听 9876 — Blender 内的 MCP addon 未启用"
  echo "      请打开 Blender 侧栏(N)，找到 BlenderMCP 面板，点 'Connect to MCP'"
  exit 1
fi

# 3. wrapper 进程数
WRAPPER_COUNT=$(ps -ef | grep -iE "blender-mcp|blender_mcp" | grep -v grep | grep -v doctor | wc -l | tr -d ' ')
echo "[3] wrapper 进程数: $WRAPPER_COUNT (健康: 1-2; 污染: ≥4)"

# 4. ESTABLISHED 连接数
ESTAB_COUNT=$(lsof -nP -i :9876 2>/dev/null | grep -c ESTABLISHED)
echo "[4] ESTABLISHED 连接到 9876: $ESTAB_COUNT (健康: 0-2; 污染: ≥4)"

# 5. uv tool 安装状态
if /Users/zbb/.local/share/uv/tools/blender-mcp/bin/python -c "import blender_mcp" 2>/dev/null; then
  VERSION=$(/Users/zbb/.local/share/uv/tools/blender-mcp/bin/python -c "import blender_mcp; print(blender_mcp.__version__)" 2>/dev/null)
  echo "[5] blender-mcp 包: 已安装 (版本 $VERSION, 路径稳定 ✓)"
else
  echo "[5] blender-mcp 包: ✗ 未安装"
  echo "      运行: uv tool install blender-mcp"
fi

# 6. 健康总结
echo ""
if [ "$WRAPPER_COUNT" -le 2 ] && [ "$ESTAB_COUNT" -le 2 ]; then
  echo "✅ 状态: 健康"
  exit 0
else
  echo "⚠️  状态: 检测到 wrapper 污染（重载 CodeBuddy 后未清理旧 wrapper）"
  if [ "${1:-}" = "--fix" ]; then
    echo ""
    echo "执行清理..."
    pkill -9 -f "blender-mcp" 2>/dev/null
    pkill -9 -f "blender_mcp" 2>/dev/null
    sleep 2
    REMAIN=$(ps -ef | grep -iE "blender-mcp|blender_mcp" | grep -v grep | grep -v doctor | wc -l | tr -d ' ')
    echo "✅ 清理完成 (剩余 wrapper: $REMAIN — CodeBuddy 会在 ~2 秒内自动重建一个干净的)"
  else
    echo "   修复: 运行 'bash $0 --fix'"
  fi
fi
