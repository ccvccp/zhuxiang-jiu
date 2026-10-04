"""73号(sv)·一键排产脚本(B1, 2026-10-04)——周产 5 条口径

用法: python sv73_daily.py                    # 默认 5 条, tier 分级路由
      python sv73_daily.py --count 3          # 自定义条数
      python sv73_daily.py --template dh_mix  # 显式模板(不经分级)
      python sv73_daily.py --dry              # 只看热点不投产

链路(人工三审闸门不动——排产止步 content 登记):
  1. 生产雷达热点(GET /api/promo/radar/hotspots, 评分降序)
  2. 取前 N 条(标题去重), 平台轮换(默认 抖音/视频号)
  3. POST /api/sv73/pipeline/run ×N(template=tier: 分值→B/A/S
     档; 同热点撞幂等(scriptId 同)→ 标题加平台变体重试一次)
  4. 打印三审/GPU 跑批/发布 下一步清单

配置: SV73_API(默认 https://zxjiu.com); 生产 token 经 SSH 容器
管道自动获取(dh_batch.prod_token 同款)。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dh_batch import http, prod_token  # noqa: E402

PLATFORM_LABELS = {
    "douyin": "抖音", "wechat_channels": "视频号",
    "xiaohongshu": "小红书", "weibo": "微博",
}


def fetch_hotspots(token: str, limit: int = 20) -> list[dict]:
    s, b = http("GET", "/api/promo/radar/hotspots?minScore=60",
                token=token)
    rows = b.get("data") or []
    if s != 200 or not rows:
        print(f"[热点] 拉取失败/为空: {s} {str(b)[:150]}")
        return []
    # 标题去重(雷达多平台同源热点), 评分降序取前 limit
    seen, picked = set(), []
    for h in rows:
        title = str(h.get("title") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        picked.append(h)
        if len(picked) >= limit:
            break
    return picked


def run_one(token: str, hs: dict, platform: str,
            template: str) -> dict | None:
    body = {
        "hotspot": {
            "hotspotId": hs.get("hotspotId") or 0,
            "title": hs.get("title") or "",
            "platform": hs.get("platform") or "",
            "score": hs.get("score") or 0,
            "fingerprint": hs.get("fingerprint") or "",
        },
        "platform": platform,
        "template": template,
    }
    s, b = http("POST", "/api/sv73/pipeline/run", body, token=token)
    if s != 200:
        print(f"    run 失败: {s} {str(b)[:120]}")
        return None
    return b.get("data") or {}


def main() -> int:
    ap = argparse.ArgumentParser(description="73号 一键排产")
    ap.add_argument("--count", type=int, default=5,
                    help="排产条数(默认 5——周产量口径)")
    ap.add_argument("--template", default="tier",
                    help="模板(tier=分级路由; 或显式 vertical/"
                         "dh_oral/dh_mix)")
    ap.add_argument("--platforms", default="douyin,wechat_channels",
                    help="平台轮换池(逗号分隔)")
    ap.add_argument("--dry", action="store_true",
                    help="只列热点不投产")
    args = ap.parse_args()

    print("[token] 生产凭证管道 ...")
    token = prod_token()

    hotspots = fetch_hotspots(token)
    if not hotspots:
        return 1
    print(f"[热点] 取 {len(hotspots)} 条(评分降序, 标题去重):")
    for h in hotspots[:args.count]:
        print(f"  {h.get('score'):>3} [{h.get('platform')}] "
              f"{str(h.get('title'))[:36]}")
    if args.dry:
        return 0

    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    rows, dh_sids = [], []
    for i, hs in enumerate(hotspots[:args.count]):
        plat = platforms[i % len(platforms)]
        label = PLATFORM_LABELS.get(plat, plat)
        print(f"  [{i + 1}/{args.count}] {label} "
              f"{str(hs.get('title'))[:30]} ...")
        r = run_one(token, hs, plat, args.template)
        if r is None:
            continue
        # 幂等撞车(同热点同品类同人设同模板→同 scriptId)→
        # 标题加平台变体重试一次(10-04 实验同款手法)
        if r.get("reused"):
            hs2 = {**hs, "title": f"{hs.get('title')}·{label}"}
            print("    幂等复用→标题变体重试 ...")
            r = run_one(token, hs2, plat, args.template) or r
        sb = r.get("storyboard") or {}
        tpl = (sb.get("template") or {}).get("name", "?")
        content = r.get("content") or {}
        rows.append({
            "tier": r.get("tier", ""),
            "tpl": tpl, "plat": label,
            "cid": content.get("contentId"),
            "sid": (sb.get("scriptId") or "")[:16],
            "reused": r.get("reused"),
            "sc": content.get("shortCode") or "-",
        })
        if tpl in ("dh_oral", "dh_mix"):
            dh_sids.append(sb.get("scriptId"))

    print(f"\n== 排产完成 {len(rows)} 条 ==")
    print(f"{'档':<3}{'模板':<9}{'平台':<5}{'cid':<5}"
          f"{'scriptId':<18}{'短码':<10}复用")
    for r in rows:
        print(f"{r['tier'] or '-':<3}{r['tpl']:<9}{r['plat']:<5}"
              f"{str(r['cid']):<5}{r['sid'] + '...':<18}"
              f"{r['sc']:<10}{'是' if r['reused'] else '否'}")

    print("\n下一步:")
    if dh_sids:
        print(f"  ① GPU 轨: 更新 dh_batch.py ADH 连接 → AutoDL 控制台"
              f"开机 →\n     python -u dh_batch.py --sids "
              f"{','.join(dh_sids)}")
    cids = [str(r["cid"]) for r in rows if r["cid"]]
    if cids:
        print(f"  ② 三审: POST /api/promo/contents/{cids[0]}/review"
              f" {{\"action\": \"approve\"}} ×{len(cids)}")
        print("  ③ 发布: publish_content ×N → 黄金时段(18:00) "
              "publish/process → channels-bot 拉单")
    return 0


if __name__ == "__main__":
    sys.exit(main())
