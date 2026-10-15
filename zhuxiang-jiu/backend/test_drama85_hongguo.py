"""85号·红果漫剧采集 worker 专项测试(fake 页面零网络)

运行:
    python test_drama85_hongguo.py

覆盖:
    1. 解析: _ROUTER_DATA 主剧(serial_count 总集数/category_list/
       series_play_cnt/like_count 键名) + 推荐位(series_title/episode_cnt/
       like_cnt 键名, 字符串数值换算)
    2. 契约: 1 主剧+推荐小榜单/platforms=hongguo/指标入契约
    3. HONGGUO_INCLUDE_RECOMMEND=0 仅主剧
    4. 红线: 评论与收藏不采(comments=0/commentsData 恒空/favorite 不入契约)/
       robots Disallow 拒采/缺 HONGGUO_SHARE_URLS 明确报错
    5. 去重(同 series_id 跨链合并)/缺 title 条目丢弃/单链失败跳过留痕
    6. 限速(QPS 间隔)与 T+1 缓存
    7. serve handler 纯逻辑(offset/n 分页/参数容错/上游错误 502)
    8. probe 结构摘要(主剧+推荐计数+样本)
    9. 全链闭环: worker 契约 → RealDramaSource 直接消费(85号零改动)
"""
import asyncio
import json
import os
import sys
import time

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")
os.environ.setdefault("AUTH_MODE", "compat")
os.environ.setdefault("AUTH_COMPAT_TRUST_HEADERS", "1")

PASS = 0
FAIL = 0
RESULTS = []

HG_KEYS = ("HONGGUO_SHARE_URLS", "HONGGUO_QPS", "HONGGUO_CACHE_TTL",
           "HONGGUO_PLATFORMS", "HONGGUO_INCLUDE_RECOMMEND", "HONGGUO_PORT")

# fake 红果分享落地页(复刻实测 _ROUTER_DATA 结构《牢狱蒙尘》2026-10-13:
# 主剧键 serial_count/like_count/category_list; 推荐位键 episode_cnt/
# like_cnt/category_name; used_chapter_id 为主剧去重键)
HG_PAGE = """<html><head><title>红果漫剧免费看《牢狱蒙尘，都市起风云第一季》</title></head><body>
<div>牢狱蒙尘，都市起风云第一季 战神 都市 豪门 第61集</div>
<script nonce="x">window._ROUTER_DATA = {"loaderData":{
"video-animation-share_layout":null,
"video-animation-share_page":{"pageData":{
"series_data":{"title":"牢狱蒙尘，都市起风云第一季","popularity":53897668,
"category":"战神","serial_count":100,"like_count":12467,"comment_count":0,
"series_play_cnt":19919859,"category_list":["战神","都市","豪门"]},
"recommend_series":[
{"series_id":"7692043430955863102","series_title":"反向拿捏：和闺蜜穿七零带着婆婆一起离",
"series_play_cnt":"69363128","category_name":"剧情","episode_cnt":"126",
"popularity":68250890,"like_cnt":"4417959","favorite_cnt":"2507822"},
{"series_id":"7690494161551625241","series_title":"灾年逆袭：我靠穿越养全家",
"series_play_cnt":"37604702","category_name":"脑洞","episode_cnt":"100",
"like_cnt":"2646893"},
{"series_id":"7691615145977449496","series_title":"持械入宋：第八季",
"series_play_cnt":3066642,"category_name":"脑洞","episode_cnt":100,
"like_cnt":1103690},
{"series_id":"7690000000000000000"}],
"used_chapter_id":"7689429703953746969","BaseResp":{}}}}},
"reporter":{},"mode":"string","renderLevel":2}</script>
</body></html>"""

HG_PAGE_2 = """<html><body><script>
window._ROUTER_DATA = {"loaderData":{"p":{"pageData":{
"series_data":{"title":"总裁是我前女友","serial_count":95,
"category_list":["恋爱","都市"],"series_play_cnt":29780361,
"like_count":2687919},
"recommend_series":[
{"series_id":"7692043430955863102","series_title":"反向拿捏：和闺蜜穿七零带着婆婆一起离",
"series_play_cnt":"69363128","category_name":"剧情","episode_cnt":"126",
"like_cnt":"4417959"},
{"series_id":"7695555555555555555","series_title":"牢狱蒙尘，都市起风云第一季",
"series_play_cnt":"19919859","category_name":"战神","episode_cnt":"100",
"like_cnt":"1436605"}],
"used_chapter_id":"7688352674999176254"}}}}}
</script></body></html>"""

HG_PAGE_BAD = "<html><body>下载红果App</body></html>"

URL_1 = "https://kylin.hainanyuyue.com/s/HG1/"
URL_2 = "https://kylin.hainanyuyue.com/s/HG2/"
URL_BAD = "https://kylin.hainanyuyue.com/s/BAD/"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


class EnvGuard:
    def __enter__(self):
        self._backup = {k: os.environ.get(k) for k in HG_KEYS}
        for k in HG_KEYS:
            os.environ.pop(k, None)
        return self

    def __exit__(self, *exc):
        for k, v in self._backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def fake_site(pages=None, fail_urls=()):
    pages = pages or {URL_1: HG_PAGE, URL_2: HG_PAGE_2, URL_BAD: HG_PAGE_BAD}
    calls = {"page": 0, "robots": set(), "ua": []}

    def http_get(url, headers=None):
        calls["page"] += 1
        calls["ua"].append((headers or {}).get("User-Agent", ""))
        if url in fail_urls:
            return 500, ""
        return 200, pages.get(url, HG_PAGE_BAD)

    def robots_get(url):
        calls["robots"].add(url.rsplit("/", 1)[0])
        return "User-agent: *\nAllow: /"

    return http_get, robots_get, calls


def main() -> int:
    from drama85_hongguo_worker import HongguoCollector, \
        build_contract_response, _extract_router_data

    print("[01 解析: 主剧+推荐位(键名差异/字符串数值)]")
    http_get, robots_get, _ = fake_site()
    with EnvGuard():
        os.environ["HONGGUO_QPS"] = "100"
        c = HongguoCollector(http_get, robots_get)
        main_raw, recs = c.parse_page(HG_PAGE)
        check("主剧(serial_count=100 总集数/category_list tags)",
              main_raw["title"] == "牢狱蒙尘，都市起风云第一季"
              and main_raw["episodes"] == 100
              and main_raw["tags"] == ["战神", "都市", "豪门"],
              str(main_raw))
        check("主剧指标(plays 19919859/likes 12467/itemId=used_chapter_id)",
              main_raw["plays"] == 19919859
              and main_raw["likes"] == 12467
              and main_raw["itemId"] == "7689429703953746969",
              str(main_raw))
        check("推荐位 3 条有效(无 series_title 条目由 collect 过滤)",
              len(recs) == 4, str(len(recs)))
        r0 = recs[0]
        check("推荐位字符串数值换算('126'→126/'69363128'→69363128)",
              r0["episodes"] == "126" and r0["plays"] == "69363128"
              and str(r0["likes"]) == "4417959", str(r0))
        check("推荐位 tags 取 category_name", r0["tags"] == ["剧情"],
              str(r0["tags"]))
        # 非红果页 / 损坏 JSON → 明确报错
        try:
            HongguoCollector(http_get, robots_get).parse_page(HG_PAGE_BAD)
            check("非红果页 → ValueError", False, "未抛错")
        except ValueError as e:
            check("非红果页 → ValueError(_ROUTER_DATA 缺失)",
                  "_ROUTER_DATA" in str(e), str(e))
        check("_extract_router_data 字符串内大括号不干扰配对",
              isinstance(_extract_router_data(
                  'x=1;window._ROUTER_DATA = {"a":"}{b","c":{"d":1}};y=2'),
                  dict), "")

    print("[02 契约: 1 主剧 + 推荐小榜单 + 指标入契约]")
    with EnvGuard():
        os.environ["HONGGUO_SHARE_URLS"] = URL_1
        os.environ["HONGGUO_QPS"] = "100"
        contract = HongguoCollector(http_get, robots_get).collect(n=50)
        check("契约条目 = 1 主剧 + 3 有效推荐", len(contract["dramas"]) == 4,
              str(len(contract["dramas"])))
        d0, d1 = contract["dramas"][0], contract["dramas"][1]
        check("主剧契约(episodes=100/metrics.plays=19919859)",
              d0["title"] == "牢狱蒙尘，都市起风云第一季"
              and d0["episodes"] == 100
              and d0["metrics"]["plays"] == 19919859, str(d0)[:100])
        check("推荐位契约(字符串数值换算 126/69363128/likes)",
              d1["episodes"] == 126
              and d1["metrics"]["plays"] == 69363128
              and d1["metrics"]["likes"] == 4417959, str(d1)[:100])
        check("platforms=hongguo", d0["platforms"] == ["hongguo"],
              str(d0["platforms"]))

    print("[03 INCLUDE_RECOMMEND=0 仅主剧]")
    with EnvGuard():
        os.environ["HONGGUO_SHARE_URLS"] = URL_1
        os.environ["HONGGUO_QPS"] = "100"
        os.environ["HONGGUO_INCLUDE_RECOMMEND"] = "0"
        contract = HongguoCollector(http_get, robots_get).collect(n=50)
        check("关推荐位 → 仅主剧 1 条",
              len(contract["dramas"]) == 1
              and contract["dramas"][0]["title"] == "牢狱蒙尘，都市起风云第一季",
              str(len(contract["dramas"])))

    print("[04 合规红线: 评论/收藏不采, robots, 缺配置]")
    with EnvGuard():
        os.environ["HONGGUO_SHARE_URLS"] = URL_1
        os.environ["HONGGUO_QPS"] = "100"
        contract = HongguoCollector(http_get, robots_get).collect(n=50)
        body = json.dumps(contract, ensure_ascii=False)
        check("评论不采(comments=0 + commentsData 恒空)",
              all(d["metrics"]["comments"] == 0
                  and d["commentsData"] == [] for d in contract["dramas"]),
              "")
        check("收藏数不采(favorite 不入契约)", "favorite" not in body, "")
        # robots Disallow → 拒采
        bad_robots = lambda u: "User-agent: *\nDisallow: /s/"     # noqa: E731
        try:
            HongguoCollector(http_get, bad_robots).collect(n=50)
            check("robots Disallow → 拒采", False, "未抛错")
        except ValueError as e:
            check("robots Disallow → 拒采(不可覆盖)", "robots" in str(e),
                  str(e))
        # 缺 SHARE_URLS → 明确报错
        os.environ.pop("HONGGUO_SHARE_URLS")
        try:
            HongguoCollector(http_get, robots_get).collect(n=50)
            check("缺 HONGGUO_SHARE_URLS → ValueError", False, "未抛错")
        except ValueError as e:
            check("缺 HONGGUO_SHARE_URLS → ValueError(提示配置)",
                  "HONGGUO_SHARE_URLS" in str(e), str(e))

    print("[05 去重/缺 title 丢弃/单链失败跳过]")
    with EnvGuard():
        os.environ["HONGGUO_SHARE_URLS"] = f"{URL_1} {URL_2} {URL_BAD}"
        os.environ["HONGGUO_QPS"] = "100"
        c = HongguoCollector(http_get, robots_get)
        contract = c.collect(n=50)
        titles = [d["title"] for d in contract["dramas"]]
        check("三链契约 = 2 主剧 + 3 新推荐(跨链 series_id 去重, BAD 跳过)",
              len(contract["dramas"]) == 5
              and titles.count("反向拿捏：和闺蜜穿七零带着婆婆一起离") == 1,
              str(titles))
        check("缺 series_title 推荐条目丢弃",
              all(t for t in titles), str(titles))
        check("同名双入防线(推荐位 series_id≠主剧 used_chapter_id 只入一次)",
              titles.count("牢狱蒙尘，都市起风云第一季") == 1, str(titles))
        # 单链网络失败 → 跳过留痕不中断
        http_fail, robots2, _ = fake_site(fail_urls=(URL_BAD,))
        os.environ["HONGGUO_SHARE_URLS"] = f"{URL_1} {URL_BAD}"
        contract = HongguoCollector(http_fail, robots2).collect(n=50)
        check("单链 500 → 跳过留痕(好链正常出 4 条)",
              len(contract["dramas"]) == 4, str(len(contract["dramas"])))

    print("[06 限速与 T+1 缓存]")
    with EnvGuard():
        os.environ["HONGGUO_SHARE_URLS"] = URL_1
        os.environ["HONGGUO_QPS"] = "100"
        http6, robots6, calls = fake_site()
        c = HongguoCollector(http6, robots6)
        t0 = time.monotonic()
        c.collect(n=50)
        c.collect(n=50)
        elapsed = time.monotonic() - t0
        check("TTL 内二次 collect 复用缓存(页面仅 1 次)", calls["page"] == 1,
              str(calls["page"]))
        check("移动 UA(分享页移动渲染)", all("Mobile" in u for u in calls["ua"]),
              str(calls["ua"][:1]))
        os.environ["HONGGUO_QPS"] = "5"
        c2 = HongguoCollector(http6, robots6)
        t0 = time.monotonic()
        c2.fetch_page(URL_2)
        c2.fetch_page(URL_1)
        check("限速间隔 ≥ 1/qps(0.2s)", time.monotonic() - t0 >= 0.19, "")

    print("[07 serve handler 纯逻辑]")
    with EnvGuard():
        os.environ["HONGGUO_SHARE_URLS"] = URL_1
        os.environ["HONGGUO_QPS"] = "100"
        c = HongguoCollector(http_get, robots_get)
        c.collect(n=50)
        status, body = build_contract_response(c, "n=2&offset=1")
        check("分页 n=2 offset=1", status == 200 and len(body["dramas"]) == 2
              and body["hasMore"] is True, str(status))
        status, body = build_contract_response(c, "n=abc")
        check("非法参数 → 400", status == 400, str(status))
        os.environ.pop("HONGGUO_SHARE_URLS")
        status, body = build_contract_response(
            HongguoCollector(http_get, robots_get), "")
        check("缺 HONGGUO_SHARE_URLS → 502+error 文案",
              status == 502 and "HONGGUO_SHARE_URLS" in body.get("error", ""),
              str(body))

    print("[08 probe 结构摘要]")
    with EnvGuard():
        os.environ["HONGGUO_SHARE_URLS"] = f"{URL_1} {URL_BAD}"
        os.environ["HONGGUO_QPS"] = "100"
        summary = HongguoCollector(http_get, robots_get).probe()
        p0 = summary["preview"][0]
        check("probe kind=hongguo + 主剧/推荐计数/样本",
              summary.get("kind") == "hongguo"
              and p0.get("main", {}).get("episodes") == 100
              and p0.get("recommendCount") == 4
              and len(p0.get("sample", [])) == 3,
              str(p0)[:100])
        check("probe hint 1/2 成功", "1/2" in summary.get("hint", ""),
              summary.get("hint"))

    print("[09 全链闭环: worker 契约 → 85号 RealDramaSource 零改动消费]")
    from services.drama85_source_gateway import RealDramaSource
    contract = None
    with EnvGuard():
        os.environ["HONGGUO_SHARE_URLS"] = URL_1
        os.environ["HONGGUO_QPS"] = "100"
        contract = HongguoCollector(http_get, robots_get).collect(n=50)

    async def worker_endpoint(url, headers=None):
        return 200, contract

    saved = {k: os.environ.get(k) for k in
             ("DRAMA85_SOURCE_URL", "DRAMA85_SOURCE_API_KEY",
              "DRAMA85_SOURCE_QPS")}
    os.environ["DRAMA85_SOURCE_URL"] = "http://127.0.0.1:8511/dramas"
    os.environ["DRAMA85_SOURCE_API_KEY"] = "local-worker"
    os.environ["DRAMA85_SOURCE_QPS"] = "100"
    try:
        rows = asyncio.run(RealDramaSource(worker_endpoint).fetch(50, 0))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("85号消费 worker 契约: 条目 4", len(rows) == 4, str(len(rows)))
    r0 = rows[0]
    check("契约字段全通过 normalize(title/plays/likes)",
          r0["title"] == "牢狱蒙尘，都市起风云第一季"
          and r0["metrics"]["plays"] == 19919859
          and r0["metrics"]["likes"] == 12467, str(r0)[:100])
    check("platforms=hongguo 入池 + commentsData 空",
          r0["platforms"] == ["hongguo"] and r0["commentsData"] == [],
          str(r0["platforms"]))

    print(f"\n85号红果 worker 专项: PASS={PASS} FAIL={FAIL}")
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
