"""85号·投喂自动预检层(drama85_preflight.py)专项测试(fake 网络零外联, 池/指纹库直接注入)

运行:
    python test_drama85_preflight.py

覆盖:
    1. robots_verdict: UA:* 根级 Disallow: / 一票否决 / 部分路径禁放行 /
       404 按允许 / 无 UA:* 段放行 / 拉取异常 na / 空文件放行
    2. extract_names: 《》提取/去重/保序/长度边界(1字不采, 2-20字采)
    3. check_icp: 省份备案号匹配(备/证/连字符变体)/无备案 None
    4. seo_title_verdict: >=3 段且 >=2 段含 SEO 词才算堆叠
    5. preflight 判定路径:
       robots 终局 REJECT / 已知农场域 REJECT / 同族指纹(交叉>=2) REJECT /
       启发式农场(>=3 剧名 0 命中 + 无ICP 或 SEO堆叠) REJECT /
       有 ICP 且不堆叠时不误杀(REVIEW) / 池命中 PASS /
       样本不足 REVIEW / 页面不可达 REVIEW / HTTP>=400 REVIEW
    6. load_farm_fp: 指纹库 JSON 结构(6 域 32 名 seeding 完整)
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


# ---------- fake 组件(零网络/零 DB) ----------

class FakeResp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class FakeClient:
    """按 URL 精确路由; 未命中 404(页面未配即视为不可达形态之一)"""
    def __init__(self, routes):
        self.routes = routes
        self.hits = []

    def get(self, url):
        self.hits.append(url)
        if url in self.routes:
            s, t = self.routes[url]
            return FakeResp(s, t)
        return FakeResp(404, "")


class RaisingClient:
    def get(self, url):
        raise ConnectionError("getaddrinfo failed")


def page(names, icp=None, title="测试短剧页面"):
    """构造 fake 榜单页: title + 《》剧名 li + 可选 ICP"""
    ju = "".join(f"<li>第{i+1}名《{n}》</li>" for i, n in enumerate(names))
    icp_html = f"<div class='footer'>{icp}</div>" if icp else "<div class='footer'>© test</div>"
    return (f"<html><head><title>{title}</title></head><body>"
            f"<ul>{ju}</ul>{icp_html}</body></html>")


POOL = {"牢狱蒙尘，都市起风云第一季", "帐中香", "聚宝仙盆之杂灵根才是真BOSS第十三季",
        "万妖图录传第十三季", "家有五女，穿越来的他连夜搞事业啦"}
FARM = {"domains": ["known-farm.cn", "www.farm2.com.cn"],
        "names": ["闪婚老公竟是首富", "重生之我在豪门当团宠", "我在古代开外卖",
                  "战神归来", "午夜十二点的电梯"]}


def pf(client, url="https://site-a.cn", farm=None, pool=None):
    from drama85_preflight import preflight
    return preflight(url, farm or FARM, pool or POOL, client)


def routes_for(base, robots, html):
    return {base + "/robots.txt": robots, base: (200, html)}


def main() -> int:
    from drama85_preflight import (robots_verdict, extract_names, check_icp,
                                   seo_title_verdict, load_farm_fp)

    # ---------- 01 robots_verdict ----------
    print("[01 robots_verdict]")
    c = FakeClient({})
    c.routes["https://x.cn/robots.txt"] = (200, "User-agent: *\nDisallow: /\n")
    v, m = robots_verdict(c, "https://x.cn/")
    check("UA:* 根级 Disallow: / → banned", v == "banned", m)

    c.routes["https://y.cn/robots.txt"] = (200,
                                           "User-agent: *\nDisallow: /api\nAllow: /\n")
    v, m = robots_verdict(c, "https://y.cn/")
    check("UA:* 部分路径禁(/api) → ok", v == "ok", m)

    c.routes["https://z.cn/robots.txt"] = (404, "")
    v, m = robots_verdict(c, "https://z.cn/")
    check("robots 404 → ok(按允许)", v == "ok", m)

    c.routes["https://g.cn/robots.txt"] = (200,
                                           "User-agent: Googlebot\nDisallow: /\n")
    v, m = robots_verdict(c, "https://g.cn/")
    check("仅 Googlebot 段根禁(无 UA:*) → ok", v == "ok", m)

    v, m = robots_verdict(RaisingClient(), "https://e.cn/")
    check("robots 拉取异常 → na", v == "na", m)

    c.routes["https://n.cn/robots.txt"] = (200, "# 空注释\n\n")
    v, m = robots_verdict(c, "https://n.cn/")
    check("robots 200 空内容 → ok", v == "ok", m)

    # ---------- 02 extract_names ----------
    print("[02 extract_names]")
    names = extract_names("《帐中香》第1名《帐中香》重复《万妖图录传第十三季》")
    check("提取+去重", names == ["帐中香", "万妖图录传第十三季"], str(names))
    check("长度边界: 1 字不采/2 字采/20 字采/21 字不采",
          extract_names("《我》《归来》《一二三四五六七八九十一二三四五六七八九十》"
                        "《一二三四五六七八九十一二三四五六七八九十1》")
          == ["归来", "一二三四五六七八九十一二三四五六七八九十"])

    # ---------- 03 check_icp ----------
    print("[03 check_icp]")
    check("京ICP备12345678号",
          check_icp("<div>京ICP备12345678号</div>") == "京ICP备12345678号")
    check("鄂ICP备2026035462号(duanjubaike 实测)",
          check_icp("footer 鄂ICP备2026035462号-1") is not None)
    check("粤ICP证 无数字 → None", check_icp("粤ICP证号") is None)
    check("无备案 → None", check_icp(page(["帐中香"])) is None)

    # ---------- 04 seo_title_verdict ----------
    print("[04 seo_title_verdict]")
    seo, _ = seo_title_verdict("爆款短剧推荐_2026高热短剧排行榜_免费在线追剧指南")
    check("3 段 3 词 SEO 堆叠 → True", seo is True)
    seo, _ = seo_title_verdict("短剧百科聚合热播榜 - 多平台人气热度前100名")
    check("叙事型 title → False", seo is False)
    seo, _ = seo_title_verdict("")
    check("空 title → False", seo is False)
    seo, _ = seo_title_verdict("短剧推荐_站点导航_关于我们")
    check("3 段仅 1 个 SEO 词 → False", seo is False)

    # ---------- 05 preflight: robots 终局 ----------
    print("[05 preflight·robots 终局]")
    r = pf(FakeClient(routes_for("https://site-a.cn",
                                 (200, "User-agent: *\nDisallow: /\n"), "")))
    check("robots 根禁 → REJECT(robots 终局)",
          r["verdict"] == "REJECT" and "robots" in r["tag"], str(r))

    # ---------- 06 preflight: 已知农场域 ----------
    print("[06 preflight·已知农场域]")
    r = pf(FakeClient(routes_for("https://known-farm.cn", (200, ""), page(["帐中香"]))),
           url="https://known-farm.cn/")
    check("域在指纹库 → REJECT(已知域) 且先于页面信号",
          r["verdict"] == "REJECT" and "已知域" in r["tag"], str(r))

    # ---------- 07 preflight: 同族指纹 ----------
    print("[07 preflight·同族指纹]")
    r = pf(FakeClient(routes_for(
        "https://site-a.cn", (200, "User-agent: *\nAllow: /\n"),
        page(["闪婚老公竟是首富", "重生之我在豪门当团宠", "某新剧A"]))))
    check("指纹交叉 2 → REJECT(同族指纹)",
          r["verdict"] == "REJECT" and "同族" in r["tag"], str(r))
    r = pf(FakeClient(routes_for(
        "https://site-a.cn", (200, ""),
        page(["闪婚老公竟是首富", "全新剧B", "全新剧C", "全新剧D"]))))
    check("交叉 1 不触发 L2b(落入启发式: 0 命中+无 ICP)",
          r["verdict"] == "REJECT" and "剧名编造" in r["tag"], str(r))

    # ---------- 08 preflight: 启发式农场与不误杀 ----------
    print("[08 preflight·启发式]")
    r = pf(FakeClient(routes_for(
        "https://site-a.cn", (200, ""),
        page(["全新剧B", "全新剧C", "全新剧D"]))))
    check("≥3 剧名 0 命中+无 ICP → REJECT(启发式)",
          r["verdict"] == "REJECT" and "剧名编造" in r["tag"], str(r))
    r = pf(FakeClient(routes_for(
        "https://site-a.cn", (200, ""),
        page(["全新剧B", "全新剧C", "全新剧D"],
             icp="沪ICP备2026001111号", title="某站榜单"))),
           )
    check("0 命中但有 ICP 且不堆叠 → 不误杀(REVIEW)",
          r["verdict"] == "REVIEW", str(r))
    r = pf(FakeClient(routes_for(
        "https://site-a.cn", (200, ""),
        page(["全新剧B", "全新剧C", "全新剧D"],
             icp="沪ICP备2026001111号",
             title="短剧推荐_2026热播排行榜_免费在线看"))))
    check("0 命中+有 ICP+SEO 堆叠 → REJECT(堆叠补偿)",
          r["verdict"] == "REJECT", str(r))

    # ---------- 09 preflight: PASS ----------
    print("[09 preflight·PASS]")
    r = pf(FakeClient(routes_for(
        "https://site-a.cn", (200, ""),
        page(["帐中香", "全新剧C", "全新剧D"]))))
    check("池命中 1 → PASS(候选接入)",
          r["verdict"] == "PASS" and "命中 1" in r["tag"], str(r))

    # ---------- 10 preflight: REVIEW ----------
    print("[10 preflight·REVIEW]")
    r = pf(FakeClient(routes_for("https://site-a.cn", (200, ""),
                                 page(["全新剧B", "全新剧C"]))))
    check("2 剧名 0 命中(样本不足) → REVIEW", r["verdict"] == "REVIEW", str(r))
    r = pf(RaisingClient())
    check("页面/robots 全不可达 → REVIEW(页面不可达)",
          r["verdict"] == "REVIEW" and "不可达" in r["tag"], str(r))
    r = pf(FakeClient({**routes_for("https://site-a.cn", (200, ""), ""),
                       "https://site-a.cn": (503, "")}))
    check("HTTP 503 → REVIEW", r["verdict"] == "REVIEW", str(r))

    # ---------- 11 指纹库 ----------
    print("[11 load_farm_fp]")
    fp = load_farm_fp()
    check("指纹库 seeding: ≥6 域 ≥30 名 含已知域",
          len(fp["domains"]) >= 6 and len(fp["names"]) >= 30
          and "aayow.cn" in fp["domains"] and "www.djtj7.com" in fp["domains"]
          and "闪婚老公竟是首富" in fp["names"],
          f"domains={len(fp['domains'])} names={len(fp['names'])}")

    print(f"\n85号预检层专项: PASS={PASS} FAIL={FAIL}")
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
