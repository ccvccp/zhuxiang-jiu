"""76号·组剧 CLI(DP0)——开发机一站式:

  python drama76_build.py --drama-id drama_zhuxiang_01 \
      --title "竹香酒逆袭" --free 2 \
      --episodes 68,69,70 \
      [--titles "中秋独白,非遗独白,国庆独白"]

步骤: ①本地 sv73_videos 找 mix 成片 ②scp 到生产
dist/videos/drama/ ③POST /api/v1/dramas 注册(生产 token 管道)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dh_batch import http, prod_token  # noqa: E402

import subprocess  # noqa: E402

PROD = "root@47.236.61.117"
DIST_DIR = "/var/www/zxjiu/dist/videos/drama"


def _sid(cid: str) -> str:
    """contentId → scriptId(经生产 content 库查)"""
    import json
    pipe = (
        "import asyncio\n"
        "async def m():\n"
        "    from repositories.promo_repository import PromoRepository\n"
        f"    c = await PromoRepository().get_content({int(cid)})\n"
        "    sv = (c or {}).get('sv73') or {}\n"
        "    print(sv.get('storyboardId') or '')\n"
        "asyncio.run(m())\n"
    )
    r = subprocess.run(["ssh", PROD,
                        "docker exec -i zhuxiang-backend-1 python3 -"],
                       input=pipe, capture_output=True, text=True,
                       timeout=60, encoding="utf-8", errors="ignore")
    sid = (r.stdout or "").strip()
    # 84号剧集集成片 storyboardId 为 d84_ 前缀(多角色分镜轮切,
    # 本地成片 d84_{sid}_ep{n}.mp4 同目录可寻)——与 sv73_ 并存
    if not (sid.startswith("sv73_") or sid.startswith("d84_")):
        print(f"[fail] cid {cid} 查询失败: {r.stdout} {r.stderr[-200:]}")
        sys.exit(1)
    return sid


def main() -> int:
    ap = argparse.ArgumentParser(description="76号 组剧 CLI")
    ap.add_argument("--drama-id", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--free", type=int, default=2)
    ap.add_argument("--episodes", required=True,
                    help="contentId 逗号序列, 按集序")
    ap.add_argument("--titles", default="",
                    help="集标题逗号序列(可选)")
    ap.add_argument("--persona", default="zhuxiaomei")
    args = ap.parse_args()

    cids = [c.strip() for c in args.episodes.split(",") if c.strip()]
    titles = [t.strip() for t in args.titles.split(",")] if args.titles else []
    if len(cids) <= args.free:
        print("[fail] 集数必须大于 free_episodes(至少一集卡点)")
        return 1

    eps = []
    for i, cid in enumerate(cids, 1):
        sid = _sid(cid)
        # 84号集成片文件名=d84_..._ep{n}.mp4(无 _mix 后缀)
        fname = (f"{sid}.mp4" if sid.startswith("d84_")
                 else f"{sid}_mix.mp4")
        local = Path("sv73_videos") / fname
        if not local.is_file():
            print(f"[fail] 本地成片缺失: {local}")
            return 1
        url = f"/videos/drama/{fname}"
        eps.append({"episode_id": cid, "title": titles[i - 1] if
                    i - 1 < len(titles) else f"第{i}集",
                    "video_url": url,
                    "duration_seconds": 22,
                    "cliff_note": ""})
        print(f"  ep{i}: cid={cid} {fname} "
              f"({local.stat().st_size // 1024}KB)")

    # ① 上传成片到 dist
    files = " ".join(f'"sv73_videos/{e["video_url"].split("/")[-1]}"'
                     for e in eps)
    r = subprocess.run(
        f'ssh {PROD} "mkdir -p {DIST_DIR}" && '
        f'scp {files} {PROD}:{DIST_DIR}/',
        shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        print("[fail] 上传失败:", r.stderr[-300:])
        return 1
    print("[ok] 成片已上传 dist/videos/drama/")

    # ② 注册剧集
    token = prod_token()
    s, b = http("POST", "/api/v1/dramas", {
        "drama_id": args.drama_id, "title": args.title,
        "persona": args.persona, "free_episodes": args.free,
        "status": "online", "episodes": eps}, token=token)
    if s != 200:
        print("[fail] 注册失败:", s, str(b)[:200])
        return 1
    d = b.get("data") or {}
    print(f"[ok] 剧集上线: {d.get('drama_id')} 共{len(eps)}集 "
          f"免费{args.free}集")
    print(f"[play] https://zxjiu.com/drama.html?d={args.drama_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
