"""85号·悟空探针脚本(独立运行, --url 直传免 env 配置)

用途:
    对候选公开页 URL 批量探测——robots 检查 → GET(诚实 UA) →
    形态判定(JSON/HTML) → 结构摘要 + WUKONG_FIELD_MAP 映射草稿,
    为 drama85_wukong_worker.py 的正式接入提供实测依据。

用法:
    python probe_wukong.py --url https://www.wkbrowser.com \
                           [--url https://m.wkbrowser.com ...]
    python probe_wukong.py --demo        # 本地 fake 页演示(零网络自检)

输出(每 URL):
    - robots 判定(Disallow → 拒采红线)
    - HTTP 状态/字节数/形态
    - JSON: 顶层键/数组路径(listPaths)/映射草稿(fieldMapDraft)
    - HTML: 标签频次(tagFreq)/XPATH 骨架(xpathDraft)
    - 结论: 可直接粘贴的 WUKONG_LIST_URL / WUKONG_FIELD_MAP 配置行
      (或"该 URL 无剧集数据结构"的排除结论)

合规: 只 GET 公开页/UA 诚实标识/robots Disallow 拒采(同 worker 红线)。
"""
import argparse
import json
import os
import re
import sys

# standalone 惯例 env(不启动服务, 仅满足 import 链)
os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")

from drama85_wukong_worker import WukongCollector   # noqa: E402

DEMO_PAGE = json.dumps({
    "code": 0, "data": {"list": [
        {"dramaName": "演示剧A", "tag": "逆袭", "episodeCount": 80,
         "playCnt": "1.2万", "finishRate": 0.62},
    ]},
}, ensure_ascii=False)


def probe_url(url: str) -> dict:
    """单 URL 探测(自动判形态: /is/、/short_play/ 悟空分享链、
    hainanyuyue.com 红果分享链、v.douyin.com/iesdouyin.com 抖音
    西瓜短链(302 落地 xg/share 形态)、baidu.com 百度短链(302 落地
    mbdlite lite 分享页) → share 预览; 其余 → WUKONG_LIST_URL
    列表页结构分析; 复用 worker 全部逻辑)"""
    os.environ.setdefault("WUKONG_QPS", "1")
    if (re.search(r"/(?:is|short_play)/", url)
            or "hainanyuyue.com" in url
            or "douyin.com" in url
            or "baidu.com" in url):
        os.environ.pop("WUKONG_LIST_URL", None)
        os.environ["WUKONG_SHARE_URLS"] = url      # 分享链 → share 模式
    else:
        os.environ.pop("WUKONG_SHARE_URLS", None)
        os.environ["WUKONG_LIST_URL"] = url
    collector = WukongCollector()
    try:
        return collector.probe(gen_map=True)
    except ValueError as e:
        return {"url": url, "error": str(e)}


def report(summary: dict) -> None:
    """人读结论输出"""
    url = summary.get("url", "?")
    print(f"\n{'=' * 62}\n探针目标: {url}\n{'=' * 62}")
    if summary.get("error"):
        print(f"  [不可用] {summary['error']}")
        return
    if summary.get("kind") == "share":
        print(f"  形态: share(分享链→单剧元数据)")
        for p in summary.get("sharePreview") or []:
            if p.get("error"):
                print(f"  - {p.get('url')} → [失败] {p['error']}")
            else:
                extra = []
                if p.get("durationSec"):
                    extra.append(f"单集时长={p['durationSec']}s")
                em = p.get("episodeMetrics") or {}
                if em:
                    extra.append(
                        f"集级播放={em.get('plays')} 赞={em.get('digg')}")
                if p.get("source"):
                    extra.append(f"出品={p['source']}")
                print(f"  - title={p.get('title')} episodes="
                      f"{p.get('episodes')} tags={p.get('tags')} "
                      f"itemId={p.get('itemId')}"
                      + (f" | {' '.join(extra)}" if extra else ""))
                if p.get("title"):
                    print("    ✔ 解析成功——追加到 WUKONG_SHARE_URLS 即入列"
                          "(逗号分隔)")
        print(f"  提示: {summary.get('hint', '')}")
        return
    print(f"  形态: {summary.get('kind')}  顶层键: "
          f"{summary.get('topKeys') or '-'}")
    if summary.get("kind") == "json":
        print(f"  数组路径: {summary.get('listPaths') or '(无)'}")
        draft = summary.get("fieldMapDraft")
        if draft:
            print(f"  映射草稿: {json.dumps(draft, ensure_ascii=False)}")
            tf = summary.get("tryFetch") or {}
            print(f"  试采验证: ok={tf.get('ok')} rows={tf.get('rows')} "
                  f"hasMore={tf.get('hasMore')}")
            if tf.get("ok"):
                print("  ---- 可直接粘贴到 backend/.env ----")
                print(f"  WUKONG_LIST_URL={url}")
                print(f"  WUKONG_FIELD_MAP="
                      f"{json.dumps(draft, ensure_ascii=False)}")
        else:
            print("  [结论] 页面为 JSON 但未识别剧集数组/字段"
                  "——无短剧数据结构")
    else:
        print(f"  标签频次: {json.dumps(summary.get('tagFreq', {}),
                                      ensure_ascii=False)}")
        draft = summary.get("xpathDraft")
        if draft:
            print(f"  XPATH 骨架: {json.dumps(draft, ensure_ascii=False)}")
            print("  [结论] HTML 页——若为落地页(仅下载引导)则无剧集数据,"
                  "需按骨架人工核对")
    print(f"  提示: {summary.get('hint', '')}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="85号·悟空候选页探针(--url 直传, 免 env)")
    parser.add_argument("--url", action="append", default=[],
                        help="候选公开页 URL(可多次)")
    parser.add_argument("--demo", action="store_true",
                        help="本地 fake 页演示(零网络)")
    args = parser.parse_args(argv)

    if args.demo:
        print("[demo 模式] 本地 fake 页面, 验证探针输出格式(零网络)")
        saved = {k: os.environ.get(k) for k in
                 ("WUKONG_LIST_URL", "WUKONG_QPS", "WUKONG_CACHE_TTL")}

        def fake_http(u, headers=None):
            return 200, DEMO_PAGE

        def fake_robots(u):
            return "User-agent: *\nAllow: /"

        os.environ["WUKONG_QPS"] = "100"
        os.environ["WUKONG_CACHE_TTL"] = "3600"
        os.environ["WUKONG_LIST_URL"] = "https://demo.test/list"
        c = WukongCollector(fake_http, fake_robots)
        report(c.probe(gen_map=True))
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return 0

    if not args.url:
        parser.error("请提供 --url(至少一个候选)或 --demo")
    summaries = []
    for u in args.url:
        summary = probe_url(u)
        summaries.append(summary)
        report(summary)
    usable = [s for s in summaries if s.get("fieldMapDraft")
              and (s.get("tryFetch") or {}).get("ok")]
    # share 形态: 解析成功即视为可接入(追加 WUKONG_SHARE_URLS)
    share_ok = [s for s in summaries if s.get("kind") == "share"
                and any(p.get("title")
                        for p in s.get("sharePreview") or [])]
    print(f"\n{'=' * 62}\n汇总: 探测 {len(summaries)} 个 URL, "
          f"列表页可直接接入 {len(usable)} 个, "
          f"分享链解析成功 {len(share_ok)} 个")
    for s in usable:
        print(f"  ✔ [列表] {s['url']}")
    for s in share_ok:
        print(f"  ✔ [分享] {s['url']} → 追加到 WUKONG_SHARE_URLS")
    if not usable and not share_ok:
        print("  ✘ 无可接入 URL(各 URL 结论见上)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
