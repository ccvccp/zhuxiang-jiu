"""73号(sv)·数字人 GPU 轨算力机一键脚本(dh_oral 模板分段链路配套)

用法(算力机: AutoDL RTX 5090D 等, SV73_SADTALKER_DIR 必配):
    python build_dh_dev.py <scriptId>
        [--api https://zxjiu.com]
        [--host root@47.236.61.117]
        [--container zhuxiang-backend-1]
        [--token <accessToken>](或 SV73_TOKEN env; 缺省走 SSH 容器管道)

环境(SV73_DH_MODE=real 时):
    SV73_TTS_MODE=on          78号竹语 TTS(口播音轨源)
    SV73_DH_MODE=real         数字人推理开(本脚本核心)
    SV73_SADTALKER_DIR=...    SadTalker 仓库根(2026-09-30 实证部署:
                              numpy 2.5.3 + torch 2.7.1+cu128 +
                              ravel/ffmpeg 补丁, 见手册 §二)
    SV73_DH_IMAGE=...         口播基准图(P1 占位: 金鹿 IP 图)
    SV73_DH_CMD=...           推理命令模板(四占位符, 可覆盖)

流程(对齐 build_sv73_dev.py 分段范式):
    ①(已完成)生产 pipeline/run: 热点→匹配→剧本(dh_oral)→登记
    ②本脚本: 拉生产 storyboard → TTS 音频 → SadTalker 推理
    ③本脚本: POST /api/sv73/render/attach 挂载产物元数据
    ④(后续)36号人工三审 → approve → publish → rpa_pending
    ⑤(后续)四平台 bot RPA 发布(算力机/开发机本地取 mp4)

方案修正(2026-09-30 GPU 机实证): 原评估 LivePortrait main 无
audio-driven——按 docx 方案 B 列名的 SadTalker 落地(5090D 出片
实证)。产物落 result_dir 根级 {timestamp}.mp4, 模板尾部 find 按
修改时间取最新对齐 {out}, 走 bash -c。
"""

import argparse
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
        description="73号 数字人 GPU 轨算力机一键脚本"
                    "(拉 storyboard→TTS→SadTalker→attach)")
    ap.add_argument("scriptId",
                    help="剧本号(dh_oral 模板, 如 sv73_xxx)")
    ap.add_argument("--api", default="https://zxjiu.com",
                    help="生产 API 基址")
    ap.add_argument("--host", default="root@47.236.61.117",
                    help="SSH 管道主机(token 获取用)")
    ap.add_argument("--container",
                    default="zhuxiang-backend-1",
                    help="后端容器名")
    ap.add_argument("--token", default="",
                    help="accessToken(缺省走 SSH 容器管道)")
    args = ap.parse_args()
    sid = args.scriptId.strip()

    print(f"== 73号 数字人 GPU 轨一键脚本 scriptId={sid} ==")

    # ②a 拉 storyboard
    print("[1/4] 拉取生产 storyboard ...")
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
    if tpl.get("name") != "dh_oral":
        print(f"  失败: 非 dh_oral 模板({tpl.get('name')})"
              "——零 GPU 模板请用 build_sv73_dev.py")
        return 1
    print(f"  模板={tpl.get('name')} 总时长="
          f"{storyboard.get('totalDuration')}s"
          f" track={storyboard.get('track')}")

    # ②b TTS 音轨(78号竹语)+PNG 主题卡(封面/角标素材)
    print("[2/4] TTS 音轨 + PNG 主题卡 ...")
    import os
    if os.environ.get("SV73_TTS_MODE", "off") != "on":
        os.environ["SV73_TTS_MODE"] = "on"   # 口播轨音源强制开
    from services.sv73_render_service import Sv73RenderService
    render = Sv73RenderService()
    audio = render._tts_audio(storyboard)
    if audio is None:
        print("  失败: TTS 音轨缺失(78号竹语 TTS 合成失败, "
              "检查 LLM_PROVIDER 配置)")
        return 1
    pages = render.render_pages(storyboard)
    print(f"  wav={audio} pages={len(pages)}")

    # ②c SadTalker GPU 推理(基准图+音频→口播 mp4)
    print("[3/4] SadTalker GPU 推理 ...")
    from services.sv73_digital_human_service import (
        Sv73DigitalHumanService, dh_mode)
    print(f"  SV73_DH_MODE={dh_mode()}")
    try:
        built = Sv73DigitalHumanService().build(storyboard, audio)
    except ValueError as e:
        print(f"  失败: {e}")
        return 1
    except RuntimeError as e:
        print(f"  推理失败(按 stderr 尾校准 SV73_DH_CMD): {e}")
        return 1
    video = Path(built["video"])
    if built["engine"] != "mock" and not video.is_file():
        print("  失败: 推理产物缺失")
        return 1
    print(f"  mp4={built['video']} engine={built['engine']}"
          f" 时长={built['durationSeconds']}s")

    # ③ attach 元数据回填
    print("[4/4] 挂载产物元数据(attach) ...")
    s, b = _call(
        "POST", f"{args.api}/api/sv73/render/attach",
        body={
            "scriptId": sid,
            "video": built["video"],
            "pages": [str(p) for p in pages],
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
    print("  ⑤ rpa_pending → 四平台 bot RPA 发布"
          "(本地取 mp4); 影子对照: 44号档案 track=模板名 对比"
          "完播率(dh_oral vs vertical)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
