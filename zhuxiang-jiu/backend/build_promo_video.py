"""36号·视频号生产线构建入口(开发机运行)

用法:
    python build_promo_video.py <contentId>   # 从生产拉内容构建
    python build_promo_video.py --demo        # 内置演示内容构建

链路: 登录 → GET /api/promo/contents/{id} → 四页卡渲染 →
ffmpeg xfade 合成 → ffmpeg -i 校验(时长/分辨率/编码)。

运行: python build_promo_video.py 37 (backend 目录, 需本机
Pillow+ffmpeg; 产物 promo_videos/)
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://zxjiu.com"

DEMO = {
    "contentId": 0,
    "title": "中秋团圆宴白酒清单火了｜竹香酒借势笔记",
    "body": "竹香型白酒, 竹香清雅入口绵甜, 家宴小聚都合适。"
            "评论区聊聊你的聚会安排~",
    "hashtags": "#竹香型白酒 #热点",
}


def call(method, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def fetch_content(cid: int) -> dict:
    st, body = call("POST", "/api/auth/login",
                    body={"phone": "13800000002",
                          "password": "test123456"})
    tok = body.get("accessToken", "")
    st, body = call("GET", f"/api/promo/contents/{cid}", tok)
    data = body.get("data") or {}
    if not data:
        raise SystemExit(f"内容 #{cid} 拉取失败: st={st} "
                         f"{str(body)[:150]}")
    return data


def probe(mp4: Path) -> str:
    from services.promo_video_service import _ffmpeg_bin
    out = subprocess.run(
        [_ffmpeg_bin(), "-i", str(mp4)],
        capture_output=True, timeout=60)
    info = out.stderr.decode("utf-8", "replace")
    for line in info.splitlines():
        if "Duration" in line or "Stream #" in line:
            return line.strip()
    return "未知"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    if sys.argv[1] == "--demo":
        content = DEMO
    else:
        content = fetch_content(int(sys.argv[1]))
    print(f"内容 #{content.get('contentId')} "
          f"《{content.get('title', '')}》")

    from services.promo_video_service import PromoVideoService
    result = PromoVideoService().build(content)
    print(f"页面: {len(result['pages'])} 张 ->")
    for p in result["pages"]:
        print(f"  {p} ({Path(p).stat().st_size} bytes)")
    print(f"视频: {result['video']}")
    print(f"  {result['sizeBytes']} bytes, "
          f"时长 {result['durationSeconds']}s")
    print(f"  {probe(Path(result['video']))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
