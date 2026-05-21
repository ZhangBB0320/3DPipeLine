#!/usr/bin/env python3
"""
blender_connect.py — Universal BlenderMCP connection helper for any agent.

用法 1（CLI 健康检查）：
    python3 Scripts/blender_connect.py
    python3 Scripts/blender_connect.py --fix          # 检测到污染时自动清理
    python3 Scripts/blender_connect.py --verbose      # 输出详细诊断

用法 2（Python API，推荐 agent 使用）：
    from blender_connect import ensure_blender, send_python
    ensure_blender()                                  # 保证连接可用，否则抛 BlenderConnectError
    result = send_python("import bpy; print(len(bpy.data.objects))")

退出码：
    0  连接 OK
    1  Blender 未启动
    2  9876 端口未监听（addon 未启动 / 端口被其他进程占用）
    3  wrapper 污染（已尝试或建议 --fix）
    4  TCP 握手成功但执行 Python 失败（addon 异常）
    5  其他不可恢复错误

设计要点：
- 直接走 TCP localhost:9876（BlenderMCP addon 在 Blender 内监听），绕过 MCP wrapper，最可靠。
- 任何失败都输出**完整诊断**：进程列表 / 端口状态 / wrapper 数 / 上次错误原因。
- 自动重试 3 次，每次失败后打印根因；--fix 模式可清理 wrapper 污染并重建连接。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
from typing import Any

# ===== 配置 =====
BLENDER_HOST = "127.0.0.1"
BLENDER_PORT = 9876
CONNECT_TIMEOUT = 5.0          # TCP 三次握手超时
RECV_TIMEOUT = 300.0           # 接收响应超时（烘焙等长任务可能需要 ≥120s）
MAX_RETRIES = 3
RETRY_BACKOFF = 1.5            # 重试间隔（秒）
DOCTOR_SH = "/Users/zbb/3DPipeLine/Scripts/blender_mcp_doctor.sh"


class BlenderConnectError(RuntimeError):
    """连接 BlenderMCP 失败时抛出，msg 中包含完整诊断信息。"""
    def __init__(self, msg: str, exit_code: int = 5, diagnostics: dict | None = None):
        super().__init__(msg)
        self.exit_code = exit_code
        self.diagnostics = diagnostics or {}


# ============================================================
# 诊断工具
# ============================================================
def _run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout: {' '.join(cmd)}"


def diag_blender_process() -> dict:
    """检查 Blender 进程是否在运行（兼容 macOS / Linux 的 pgrep 差异）。"""
    pids: list[dict] = []
    # macOS 用 -fl，Linux 用 -fa；两个都试
    for flag in ("-fl", "-fa"):
        rc, out, _ = _run(["pgrep", flag, "Blender"])
        if rc == 0 and out.strip():
            for line in out.strip().splitlines():
                m = re.match(r"^(\d+)\s+(.*)", line)
                if m and "Blender" in m.group(2) and "blender_mcp" not in m.group(2):
                    pid = int(m.group(1))
                    if not any(p["pid"] == pid for p in pids):
                        pids.append({"pid": pid, "cmd": m.group(2)})
            if pids:
                break
    return {"running": len(pids) > 0, "count": len(pids), "processes": pids}


def diag_port_9876() -> dict:
    """检查 9876 端口监听状态，并判定 listener 是不是 Blender。"""
    rc, out, _ = _run(["lsof", "-nP", "-iTCP:9876", "-sTCP:LISTEN"])
    listening = rc == 0 and "LISTEN" in out
    listener_pid = None
    listener_cmd = None
    if listening:
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                try:
                    listener_pid = int(parts[1])
                    listener_cmd = parts[0]
                    break
                except ValueError:
                    pass
    is_blender = bool(listener_cmd and "lender" in listener_cmd.lower())  # 'Blender' 或 'blender'
    return {
        "listening": listening,
        "listener_pid": listener_pid,
        "listener_cmd": listener_cmd,
        "is_blender": is_blender,
        "raw": out.strip(),
    }


def diag_wrapper_count() -> dict:
    """检查 blender_mcp wrapper 进程数。健康 1-2；污染 ≥4。"""
    rc, out, _ = _run(["pgrep", "-fa", "blender_mcp.server"])
    count = len(out.strip().splitlines()) if rc == 0 else 0
    return {
        "count": count,
        "healthy": count <= 3,
        "polluted": count >= 4,
    }


def diag_established_connections() -> dict:
    """到 9876 的 ESTABLISHED 连接数（污染指标）。"""
    rc, out, _ = _run(["lsof", "-nP", "-iTCP:9876", "-sTCP:ESTABLISHED"])
    count = max(0, len(out.strip().splitlines()) - 1) if rc == 0 else 0
    return {"count": count, "polluted": count >= 4}


def collect_diagnostics() -> dict:
    """完整诊断快照。"""
    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "blender": diag_blender_process(),
        "port_9876": diag_port_9876(),
        "wrapper": diag_wrapper_count(),
        "connections": diag_established_connections(),
    }


def format_diagnostics(d: dict) -> str:
    lines = ["===== BlenderMCP 诊断快照 =====", f"时间: {d['timestamp']}"]
    b = d["blender"]
    lines.append(f"[1] Blender 进程: {b['count']} 个" + ("" if b["running"] else "  ❌ 未启动"))
    p = d["port_9876"]
    if p["listening"]:
        owner = f"PID={p['listener_pid']}, CMD={p['listener_cmd']}" + (
            " ✓Blender" if p["is_blender"] else " ❌非Blender"
        )
        lines.append(f"[2] 9876 端口监听: ✓ 是 ({owner})")
    else:
        lines.append("[2] 9876 端口监听: ❌ 否（addon 未启动？）")
    w = d["wrapper"]
    flag = "❌ 污染" if w["polluted"] else "✓ 健康"
    lines.append(f"[3] wrapper 进程数: {w['count']} ({flag})")
    c = d["connections"]
    flag = "❌ 污染" if c["polluted"] else "✓ 健康"
    lines.append(f"[4] ESTABLISHED → 9876: {c['count']} ({flag})")
    return "\n".join(lines)


# ============================================================
# TCP 直连（绕过 wrapper，最稳）
# ============================================================
def _send_recv(payload: dict, recv_timeout: float = RECV_TIMEOUT) -> dict:
    """
    与 BlenderMCP addon 在 9876 端口直接通信。
    协议（参考 blender-mcp addon）：发送 JSON 字节，addon 回复 JSON 字节，以连接关闭为结束。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT)
    try:
        sock.connect((BLENDER_HOST, BLENDER_PORT))
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        sock.close()
        raise BlenderConnectError(f"TCP 连接 {BLENDER_HOST}:{BLENDER_PORT} 失败: {e}", exit_code=2)

    sock.settimeout(recv_timeout)
    try:
        sock.sendall(json.dumps(payload).encode("utf-8"))
        buf = bytearray()
        while True:
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                raise BlenderConnectError(
                    f"接收 BlenderMCP 响应超时 (>{recv_timeout}s)。如果是长任务（烘焙），请增大 recv_timeout。",
                    exit_code=4,
                )
            if not chunk:
                break
            buf.extend(chunk)
            # addon 通常一次性返回完整 JSON；尝试解析，成功即返回
            try:
                return json.loads(buf.decode("utf-8"))
            except json.JSONDecodeError:
                continue
        if not buf:
            raise BlenderConnectError("BlenderMCP addon 关闭连接但未返回任何数据。", exit_code=4)
        try:
            return json.loads(buf.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise BlenderConnectError(f"BlenderMCP 响应不是合法 JSON: {e}\n原始内容: {buf[:500]!r}", exit_code=4)
    finally:
        try:
            sock.close()
        except Exception:
            pass


# ============================================================
# 公开 API
# ============================================================
def ping(verbose: bool = False) -> dict:
    """
    向 Blender 发送一条最小 Python 代码，确认 addon 可正常执行。
    成功返回 dict（addon 响应）。失败抛 BlenderConnectError。
    """
    payload = {
        "type": "execute_code",
        "params": {"code": "import bpy; result = {'scene': bpy.context.scene.name, 'objects': len(bpy.data.objects)}"},
    }
    resp = _send_recv(payload, recv_timeout=10.0)
    if verbose:
        print(f"[ping] addon response: {resp}")
    return resp


def send_python(code: str, recv_timeout: float = RECV_TIMEOUT) -> dict:
    """
    在 Blender 内执行任意 Python 代码。返回 addon 响应 dict。
    用法:
        from blender_connect import ensure_blender, send_python
        ensure_blender()
        send_python("import bpy; bpy.ops.mesh.primitive_cube_add()")
    """
    payload = {"type": "execute_code", "params": {"code": code}}
    return _send_recv(payload, recv_timeout=recv_timeout)


def ensure_blender(auto_fix: bool = True, verbose: bool = False) -> dict:
    """
    保证 BlenderMCP 可用。流程：
      1. 跑一次完整诊断
      2. 若 Blender 未启动 / 9876 未监听 → 抛错（无法自愈）
      3. 若 wrapper 污染且 auto_fix=True → 调用 doctor --fix
      4. 三次重试 ping，失败抛 BlenderConnectError
    成功返回 ping 响应 dict。
    """
    diag = collect_diagnostics()
    if verbose:
        print(format_diagnostics(diag))

    port = diag["port_9876"]
    blender = diag["blender"]

    # 权威判断：9876 端口由 Blender 监听 == Blender 已就绪
    blender_ready = port["listening"] and (port["is_blender"] or blender["running"])

    if not blender_ready:
        if not port["listening"]:
            raise BlenderConnectError(
                "9876 端口未监听。原因可能是：\n"
                "  - Blender 未启动\n"
                "  - BlenderMCP addon 未启用（Edit → Preferences → Add-ons → 搜索 BlenderMCP）\n"
                "  - addon 已启用但 Server 未启动（N 面板 → BlenderMCP → Connect to Claude）\n"
                "  - 9876 端口被其他进程占用\n\n"
                + format_diagnostics(diag),
                exit_code=2,
                diagnostics=diag,
            )
        if not port["is_blender"]:
            raise BlenderConnectError(
                f"9876 端口被非 Blender 进程占用 (PID={port['listener_pid']}, CMD={port['listener_cmd']})。\n"
                "请先终止该进程或更换端口。\n\n"
                + format_diagnostics(diag),
                exit_code=2,
                diagnostics=diag,
            )
        # 兜底：端口 OK 但 pgrep 没扫到（macOS 偶发）—— 直接尝试连接，由 ping 给出最终结论
        if verbose:
            print("ℹ️ pgrep 未扫到 Blender 进程，但 9876 由 Blender 监听，继续尝试连接…")

    if diag["wrapper"]["polluted"] or diag["connections"]["polluted"]:
        if auto_fix and os.path.exists(DOCTOR_SH):
            if verbose:
                print("⚠️ 检测到 wrapper 污染，调用 doctor --fix 清理…")
            _run(["bash", DOCTOR_SH, "--fix"], timeout=30.0)
            time.sleep(1.5)
        else:
            if verbose:
                print(f"⚠️ wrapper 污染但 auto_fix=False 或 {DOCTOR_SH} 不存在，跳过清理")

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = ping(verbose=verbose)
            if verbose:
                print(f"✓ 连接成功 (尝试 {attempt}/{MAX_RETRIES})")
            return resp
        except BlenderConnectError as e:
            last_err = e
            if verbose:
                print(f"✗ 尝试 {attempt}/{MAX_RETRIES} 失败: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)

    final_diag = collect_diagnostics()
    raise BlenderConnectError(
        f"连续 {MAX_RETRIES} 次连接失败。最后一次错误：{last_err}\n\n"
        + format_diagnostics(final_diag),
        exit_code=getattr(last_err, "exit_code", 5),
        diagnostics=final_diag,
    )


# ============================================================
# CLI
# ============================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="BlenderMCP universal connection helper")
    ap.add_argument("--fix", action="store_true", help="检测到 wrapper 污染时自动清理")
    ap.add_argument("--verbose", "-v", action="store_true", help="输出详细诊断")
    ap.add_argument("--diag-only", action="store_true", help="只跑诊断，不尝试连接")
    ap.add_argument("--exec", metavar="CODE", help="在 Blender 内执行一段 Python 代码并打印结果")
    args = ap.parse_args()

    if args.diag_only:
        d = collect_diagnostics()
        print(format_diagnostics(d))
        return 0

    try:
        resp = ensure_blender(auto_fix=args.fix, verbose=args.verbose)
        print("✅ BlenderMCP 连接 OK")
        print(f"   响应: {json.dumps(resp, ensure_ascii=False)[:200]}")
        if args.exec:
            r = send_python(args.exec)
            print(f"\n--- exec 结果 ---\n{json.dumps(r, ensure_ascii=False, indent=2)}")
        return 0
    except BlenderConnectError as e:
        print(f"❌ BlenderMCP 连接失败 (exit={e.exit_code})")
        print(str(e))
        return e.exit_code
    except Exception as e:
        print(f"❌ 未预期错误: {type(e).__name__}: {e}")
        return 5


if __name__ == "__main__":
    sys.exit(main())
