#!/usr/bin/env python3
"""
hunyuan3d_gen.py — 腾讯混元生3D 图生 3D 脚本

通过 OpenAI 兼容 API 上传一张本地图片，生成带 PBR 纹理的 3D 模型，
轮询任务状态直到完成，下载 FBX（含贴图）到本地目录。

API 文档：
- Submit : https://api.ai3d.cloud.tencent.com/v1/ai3d/submit
- Query  : https://api.ai3d.cloud.tencent.com/v1/ai3d/query
- 计费   : Normal(20) + EnablePBR(+10) + FaceCount(+10) + ResultFormat(+5) = 45 积分/次

用法（基础）：
    python3 hunyuan3d_gen.py <image_path> <output_dir>

用法（完整参数）：
    python3 hunyuan3d_gen.py /path/to/image.png /path/to/output_dir \\
        --api-key sk-xxx \\
        --face-count 1500000 \\
        --pbr \\
        --format FBX \\
        --model 3.0 \\
        --poll-interval 5 \\
        --max-wait 1200
"""

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


# ============================================================
# 默认配置
# ============================================================
DEFAULT_API_KEY = "sk-suIUuOpsq5d1rnslaypv0nZ51FmLs5j0mzJqglUUoJukmIPs"
BASE_URL = "https://api.ai3d.cloud.tencent.com"
SUBMIT_URL = f"{BASE_URL}/v1/ai3d/submit"
QUERY_URL = f"{BASE_URL}/v1/ai3d/query"


# ============================================================
# HTTP 工具（仅用标准库，无第三方依赖）
# ============================================================

def _http_post_json(url: str, payload: dict, headers: dict, timeout: int = 60) -> dict:
    """POST JSON，返回解析后的 JSON 响应。"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


def _http_download(url: str, dst_path: str, timeout: int = 300, chunk: int = 1 << 20):
    """下载远程 URL 到本地文件。"""
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        with open(dst_path, "wb") as f:
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                f.write(buf)


# ============================================================
# 业务逻辑
# ============================================================

def image_to_base64_data_url(image_path: str) -> str:
    """读取本地图片 → data:image/<type>;base64,<...> 形式。

    注意：腾讯混元要求 base64 前缀 + 英文逗号 + base64 字符串。
    """
    p = Path(image_path)
    if not p.exists():
        raise FileNotFoundError(f"图片不存在: {image_path}")

    mime, _ = mimetypes.guess_type(image_path)
    if mime is None or not mime.startswith("image/"):
        # fallback by suffix
        suffix = p.suffix.lower().lstrip(".")
        if suffix in ("jpg", "jpeg"):
            mime = "image/jpeg"
        elif suffix in ("png", "webp"):
            mime = f"image/{suffix}"
        else:
            raise ValueError(f"不支持的图片格式: {p.suffix}（仅支持 jpg/jpeg/png/webp）")

    raw = p.read_bytes()
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError(f"图片过大: {len(raw)} 字节，超过 8MB 限制")

    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def submit_job(
    api_key: str,
    image_b64: str,
    face_count: int,
    enable_pbr: bool,
    result_format: str,
    model_version: str,
    verbose: bool = True,
) -> str:
    """提交图生 3D 任务，返回 JobId。"""
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "ImageBase64": image_b64,
        "Model": model_version,
        "GenerateType": "Normal",  # 带纹理的几何模型
        "FaceCount": face_count,
        "EnablePBR": enable_pbr,
        "ResultFormat": result_format,
    }

    if verbose:
        print(f"[submit] POST {SUBMIT_URL}")
        log_payload = dict(payload)
        log_payload["ImageBase64"] = f"<{len(image_b64)} chars>"
        print(f"[submit] payload: {json.dumps(log_payload, ensure_ascii=False)}")

    resp = _http_post_json(SUBMIT_URL, payload, headers)
    if verbose:
        print(f"[submit] response: {json.dumps(resp, ensure_ascii=False)}")

    response_body = resp.get("Response", {})
    error = response_body.get("Error")
    if error:
        raise RuntimeError(
            f"提交失败: Code={error.get('Code')}, Message={error.get('Message')}"
        )

    job_id = response_body.get("JobId")
    if not job_id:
        raise RuntimeError(f"未返回 JobId，完整响应: {resp}")

    return job_id


def query_job(api_key: str, job_id: str, verbose: bool = False) -> dict:
    """查询任务状态。"""
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }
    payload = {"JobId": job_id}
    resp = _http_post_json(QUERY_URL, payload, headers)
    if verbose:
        print(f"[query] {json.dumps(resp, ensure_ascii=False)}")
    return resp.get("Response", {})


def poll_until_done(
    api_key: str,
    job_id: str,
    poll_interval: int = 5,
    max_wait: int = 1200,
    verbose: bool = True,
) -> dict:
    """轮询直到任务结束（DONE/FAIL）或超时。"""
    start = time.time()
    last_status = None
    while True:
        elapsed = int(time.time() - start)
        if elapsed > max_wait:
            raise TimeoutError(
                f"轮询超时 ({elapsed}s > {max_wait}s)，最后状态: {last_status}"
            )

        try:
            resp = query_job(api_key, job_id, verbose=False)
        except Exception as e:
            print(f"[poll] {elapsed}s 查询异常: {e}（继续重试）")
            time.sleep(poll_interval)
            continue

        status = resp.get("Status", "UNKNOWN")
        last_status = status

        if verbose:
            print(f"[poll] {elapsed:>4}s  Status={status}")

        if status == "DONE":
            return resp
        if status == "FAIL":
            err_code = resp.get("ErrorCode", "")
            err_msg = resp.get("ErrorMessage", "")
            raise RuntimeError(f"任务失败: Code={err_code}, Message={err_msg}")

        # WAIT / RUN / UNKNOWN — 继续等
        time.sleep(poll_interval)


def pick_result_file(result_files: list, prefer_format: str) -> dict:
    """从 ResultFile3Ds 数组中选择匹配格式的文件项。"""
    if not result_files:
        raise RuntimeError("ResultFile3Ds 为空，无文件可下载")

    prefer_upper = prefer_format.upper()
    # 精确匹配
    for item in result_files:
        if str(item.get("Type", "")).upper() == prefer_upper:
            return item
    # 退而求其次：返回第一个
    print(
        f"[warn] 未找到 {prefer_upper} 格式，可用: "
        f"{[item.get('Type') for item in result_files]}，使用第一个"
    )
    return result_files[0]


def download_result(file_item: dict, output_dir: str, basename: str, verbose: bool = True) -> str:
    """下载结果文件到本地，返回保存路径。"""
    url = file_item.get("Url")
    file_type = str(file_item.get("Type", "")).lower() or "bin"
    if not url:
        raise RuntimeError(f"文件项缺少 Url: {file_item}")

    # 从 URL 推断扩展名（处理 zip 包格式）
    parsed = urllib.parse.urlparse(url)
    url_ext = Path(parsed.path).suffix.lower().lstrip(".")
    ext = url_ext if url_ext else file_type

    os.makedirs(output_dir, exist_ok=True)
    dst = os.path.join(output_dir, f"{basename}.{ext}")

    if verbose:
        print(f"[download] {file_type} → {dst}")
        print(f"[download] url = {url[:120]}...")

    _http_download(url, dst)

    size = os.path.getsize(dst)
    if verbose:
        print(f"[download] saved {size:,} bytes ({size / 1024 / 1024:.2f} MB)")

    return dst


def download_preview(file_item: dict, output_dir: str, basename: str, verbose: bool = True):
    """下载预览图（如果有）。"""
    url = file_item.get("PreviewImageUrl")
    if not url:
        return None
    dst = os.path.join(output_dir, f"{basename}_preview.png")
    try:
        _http_download(url, dst, timeout=60)
        if verbose:
            print(f"[download] preview → {dst}")
        return dst
    except Exception as e:
        if verbose:
            print(f"[download] preview 下载失败: {e}（忽略）")
        return None


# ============================================================
# 主流程
# ============================================================

def run_pipeline(args):
    image_path = os.path.abspath(args.image)
    output_dir = os.path.abspath(args.output_dir)
    basename = args.name or Path(image_path).stem

    print("=" * 70)
    print(f"HUNYUAN 3D GEN")
    print(f"  Image:        {image_path}")
    print(f"  Output dir:   {output_dir}")
    print(f"  Basename:     {basename}")
    print(f"  FaceCount:    {args.face_count}")
    print(f"  EnablePBR:    {args.pbr}")
    print(f"  ResultFormat: {args.format}")
    print(f"  Model:        {args.model}")
    print(f"  Cost estimate: {20 + (10 if args.pbr else 0) + 10 + 5} 积分")
    print("=" * 70)

    # 1. 图片转 base64
    print("[1] Encoding image to base64...")
    img_b64 = image_to_base64_data_url(image_path)
    print(f"    base64 length: {len(img_b64):,} chars")

    # 2. 提交任务
    print("[2] Submitting job...")
    job_id = submit_job(
        api_key=args.api_key,
        image_b64=img_b64,
        face_count=args.face_count,
        enable_pbr=args.pbr,
        result_format=args.format,
        model_version=args.model,
        verbose=True,
    )
    print(f"[2] JobId = {job_id}")

    # 3. 轮询
    print(f"[3] Polling until DONE/FAIL (interval={args.poll_interval}s, max={args.max_wait}s)...")
    resp = poll_until_done(
        api_key=args.api_key,
        job_id=job_id,
        poll_interval=args.poll_interval,
        max_wait=args.max_wait,
        verbose=True,
    )

    result_files = resp.get("ResultFile3Ds", [])
    print(f"[3] Got {len(result_files)} result file(s):")
    for item in result_files:
        print(f"    - Type={item.get('Type')}  Url={str(item.get('Url'))[:80]}...")

    # 4. 选格式并下载
    print(f"[4] Downloading {args.format} ...")
    target = pick_result_file(result_files, args.format)
    saved_path = download_result(target, output_dir, basename)

    # 5. 下载预览图
    preview_path = download_preview(target, output_dir, basename)

    # 完成
    print("=" * 70)
    print(f"[DONE]")
    print(f"  Model:   {saved_path}")
    if preview_path:
        print(f"  Preview: {preview_path}")
    print(f"  JobId:   {job_id}")
    print("=" * 70)

    return saved_path


# ============================================================
# CLI
# ============================================================

def build_parser():
    p = argparse.ArgumentParser(
        description="腾讯混元生3D 图生3D 脚本（支持 PBR + 自定义面数 + FBX 下载）"
    )
    p.add_argument("image", help="输入图片路径（jpg/png/jpeg/webp，≤8MB）")
    p.add_argument("output_dir", help="输出目录（不存在会自动创建）")

    p.add_argument("--api-key", default=DEFAULT_API_KEY,
                   help="API Key（默认使用脚本内置 key；可用环境变量 HUNYUAN3D_API_KEY 覆盖）")
    p.add_argument("--name", default="",
                   help="输出文件基础名（默认取输入图片文件名 stem）")
    p.add_argument("--face-count", type=int, default=1500000,
                   help="生成模型面数，取值 [3000, 1500000]，默认 1500000（150 万）")
    p.add_argument("--pbr", action=argparse.BooleanOptionalAction, default=True,
                   help="是否开启 PBR 材质（金属度/粗糙度/法线），默认开启")
    p.add_argument("--format", default="FBX",
                   choices=["FBX", "OBJ", "GLB", "USDZ", "STL", "PLY"],
                   help="结果格式，默认 FBX")
    p.add_argument("--model", default="3.0", choices=["3.0", "3.1"],
                   help="混元生3D 模型版本，默认 3.0")
    p.add_argument("--poll-interval", type=int, default=5,
                   help="轮询间隔（秒），默认 5")
    p.add_argument("--max-wait", type=int, default=1200,
                   help="轮询最大等待（秒），默认 1200（20 分钟）")
    return p


def main():
    args = build_parser().parse_args()

    # 环境变量覆盖
    env_key = os.environ.get("HUNYUAN3D_API_KEY")
    if env_key:
        args.api_key = env_key

    # 参数校验
    if not (3000 <= args.face_count <= 1500000):
        sys.exit(f"[error] --face-count 必须在 [3000, 1500000] 范围内，当前: {args.face_count}")

    try:
        run_pipeline(args)
    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
