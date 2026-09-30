"""73号(sv)·开发机渲染一键脚本(分段链路 ②③ 步配套)

用法:
    python build_sv73_dev.py <scriptId>
        [--api https://zxjiu.com]
        [--host root@47.236.61.117]
        [--container zhuxiang-backend-1]
        [--token <accessToken>](或 SV73_TOKEN env; 缺省走 SSH 容器管道)

流程(生产灰度验收报告 §六 分段范式):
    ①(已完成)生产 pipeline/run: 热点→匹配→剧本→content 登记
    ②本脚本: 拉生产 storyboard → 本地 ffmpeg 全链渲染
    ③本脚本: POST /api/sv73/render/attach 挂载产物元数据
    ④(后续)36号人工三审 → approve → publish → rpa_pending
    ⑤(后续)channels/douyin-bot RPA 发布(本地取 mp4)

设计依据: 生产 2 核小机 ffmpeg 300s 超时实证(2026-09-30)——
渲染对齐 36号产线定位留在开发机; mp4 为本地路径, RPA 发布
本在开发机执行, 文件无需上生产。

示例:
    python build_sv73_dev.py sv73_0a1b2c3d4e5f
    SV73_TOKEN=xxx python build_sv73_dev.py sv73_xxx --api http://localhost:8000
"""

import argparse
import asyncio
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _get_token(args) -> str:
    """token 获取: --token/SV73_TOKEN → SSH 容器管道(register/login)"""
    if args.token:
        return args.token
    import os
    env = os.environ.get("SV73_TOKEN", "").strip()
    if env:
        return env
    pipe = (
        "import asyncio\n"
        "async def m():\n"
        "    from services.auth_service import AuthService\n"
        "    try:\n"
        "        r = await AuthService().register(\n"
        "            phone='13800000731',\n"
        "            password='test123456',\n"
        "            role='admin')\n"
        "    except ValueError:\n"
        "        r = await AuthService().login(\n"
        "            '13800000731', 'test123456')\n"
        "    print(r.get('accessToken', ''))\n"
        "asyncio.run(m())\n"
    )
    # 跨 SSH 多行脚本会被远端 shell 按换行拆断, 且 list 参数
    # 拼接丢失引用——base64 单行 + 外层 shell 引号双保险
    import base64
    b64 = base64.b64encode(pipe.encode("utf-8")).decode()
    cmd = ["ssh", "-o", "ConnectTimeout=15", args.host,
           "docker", "exec", args.container,
           "python", "-c",
           "\"import base64;exec(base64.b64decode('%s'))\""
           % b64]
    out = subprocess.run(cmd, capture_output=True, text=True,
                         timeout=90)
    lines = [l for l in (out.stdout or "").splitlines()
             if l.strip()]
    if not lines:
        raise RuntimeError(
            "token 获取失败(SSH 容器管道): "
            + (out.stderr or "")[:200])
    return lines[-1].strip()


def _call(method, url, body=None, token=""):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": "Bearer " + token}
               if token else {}),
        })
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"detail": raw[:150].decode(
                "utf-8", "replace")}


def main():
    ap = argparse.ArgumentParser(
        description="73号 开发机渲染一键脚本"
                    "(拉 storyboard→本地渲染→attach)")
    ap.add_argument("scriptId",
                    help="剧本号(pipeline 产出, 如 sv73_xxx)")
    ap.add_argument("--api", default="https://zxjiu.com",
                    help="生产 API 基址(默认 https://zxjiu.com)")
    ap.add_argument("--host", default="root@47.236.61.117",
                    help="SSH 管道主机(token 获取用)")
    ap.add_argument("--container",
                    default="zhuxiang-backend-1",
                    help="后端容器名")
    ap.add_argument("--token", default="",
                    help="accessToken(缺省走 SSH 容器管道)")
    args = ap.parse_args()
    sid = args.scriptId.strip()

    print(f"== 73号 开发机渲染脚本 scriptId={sid} ==")

    # ②a 拉 storyboard(观测面)
    print("[1/3] 拉取生产 storyboard ...")
    token = _get_token(args)
    s, b = _call("GET", f"{args.api}/api/sv73/script/{sid}",
                 token=token)
    if s != 200:
        print(f"  失败: HTTP {s} {b.get('detail', '')}")
        return 1
    storyboard = b.get("data") or {}
    if storyboard.get("scriptId") != sid:
        print("  失败: scriptId 不匹配")
        return 1
    tpl = storyboard.get("template") or {}
    print(f"  模板={tpl.get('name')} 镜数="
          f"{len(storyboard.get('scenes') or [])}"
          f" 总时长={storyboard.get('totalDuration')}s"
          f" track={storyboard.get('track')}")

    # ②b 本地渲染(ffmpeg 全链)
    print("[2/3] 本地渲染(ffmpeg 全链) ...")
    from services.sv73_render_service import Sv73RenderService
    built = Sv73RenderService().build(storyboard)
    video = Path(built["video"])
    if not video.exists() or video.stat().st_size <= 0:
        print("  失败: 渲染产物异常")
        return 1
    print(f"  mp4={video} ({video.stat().st_size} bytes)"
          f" 时长={built['durationSeconds']}s"
          f" 音轨={'有' if built['audioTrack'] else '无(BGM/TTS off)'}")

    # ③ attach 元数据回填(决策面)
    print("[3/3] 挂载产物元数据(attach) ...")
    s, b = _call(
        "POST", f"{args.api}/api/sv73/render/attach",
        body={
            "scriptId": sid,
            "video": str(video.resolve()),
            "pages": built["pages"],
            "audioTrack": built["audioTrack"],
            "sizeBytes": built["sizeBytes"],
        }, token=token)
    if s != 200:
        print(f"  失败: HTTP {s} {b.get('detail', '')}")
        return 1
    d = b.get("data") or {}
    print(f"  contentId={d.get('contentId')}"
          f" renderSource={(d.get('sv73') or {}).get('renderSource')}")

    print("-" * 50)
    print("完成。后续链路:")
    print(f"  ④ 36号人工三审: POST /api/promo/contents/"
          f"{d.get('contentId')}/review → approve → publish")
    print("  ⑤ rpa_pending → channels/douyin-bot RPA 发布"
          "(本地取 mp4)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
