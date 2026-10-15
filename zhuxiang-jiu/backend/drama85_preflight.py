# -*- coding: utf-8 -*-
"""85号·投喂自动预检层(2026-10-15, 依据 docs/百度AI摘要与推荐链接质量割裂分析.md 第四节):
输入任意 URL 列表 → 五层自动检测 → PASS/REJECT/REVIEW 判定 + 证据链。

五层(按一票否决优先级):
  L1 robots 红线: User-agent:* 段根级 Disallow: / → REJECT(robots 终局)
  L2 农场指纹库: 域名命中/剧名指纹交叉>=2 → REJECT(内容农场·同族)
  L3 ICP 备案: 页面无备案号(负向信号)
  L4 剧名池命中率: 《》剧名 vs 主表 drama_name(正信号; >=3 剧名且 0 命中为强负)
  L5 SEO 堆叠 title: 「XX_XX排行榜_免费XX」式关键词堆叠(负向信号)

判定规则(实证标定自 2026-10-15 百度AI投喂批):
  REJECT(robots)     L1 触发
  REJECT(农场·域)     L2 域名命中
  REJECT(农场·指纹)   L2 剧名交叉>=2
  REJECT(农场)        剧名>=3 且 池命中=0 且 (无ICP 或 SEO堆叠)
  PASS(候选接入)      池命中>=1 且 无上述 REJECT 触发
  REVIEW(人工)       其余(样本不足/叙事页/边缘信号冲突)

用法: python drama85_preflight.py <url> [<url> ...]
  --show-names  输出命中的剧名明细"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")
import httpx
from sqlalchemy import select

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
FP_PATH = Path(__file__).parent / "drama85_farm_fp.json"
SEO_WORDS = ("排行榜", "推荐", "免费", "热播", "在线看", "指南", "排名", "前十名")


def load_farm_fp() -> dict:
    return json.loads(FP_PATH.read_text(encoding="utf-8"))


def load_pool() -> set[str]:
    from db import get_session
    from models import Drama
    with get_session() as s:
        return set(s.execute(select(Drama.drama_name)).scalars())


def robots_verdict(client: httpx.Client, url: str) -> tuple[str, str]:
    """→ ('ok'|'banned'|'na', 摘要)  根级 Disallow: / 一票否决"""
    try:
        r = client.get(url.rstrip("/") + "/robots.txt")
    except Exception as e:  # noqa: BLE001
        return "na", f"robots 拉取失败({type(e).__name__})"
    if r.status_code != 200:
        return "ok", f"robots {r.status_code}(404 按允许)"
    ua_star, in_star = [], False
    for line in r.text.splitlines():
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        low = t.lower()
        if low.startswith("user-agent:"):
            in_star = "*" in t.split(":", 1)[1]
            continue
        if in_star and low.startswith("disallow:"):
            ua_star.append(t.split(":", 1)[1].strip())
    if "/" in [p.rstrip() for p in ua_star if p]:
        return "banned", "robots UA:* 根级 Disallow: /(一票否决)"
    return "ok", f"robots 200 放行(UA:* 禁 {len(ua_star)} 条非根路径)"


def extract_names(text: str) -> list[str]:
    names: list[str] = []
    for m in re.finditer(r"《([^》]{2,20})》", text):
        n = m.group(1).strip()
        if n not in names:
            names.append(n)
    return names


def check_icp(text: str) -> str | None:
    m = re.search(r"[京粤沪浙苏鲁湘鄂闽豫皖晋桂琼冀黔滇陕甘青宁新津渝]ICP"
                  r"[备]?\s*号?\s*[：:－\-]?\s*\d+号?(-\d+号?)?", text)
    return m.group(0) if m else None


def seo_title_verdict(title: str) -> tuple[bool, str]:
    if not title:
        return False, "无 title"
    segs = [s for s in re.split(r"[_|｜\-—]", title) if s.strip()]
    hits = sum(1 for s in segs if any(w in s for w in SEO_WORDS))
    stacked = len(segs) >= 3 and hits >= 2
    return stacked, f"title {len(segs)} 段/SEO 词 {hits} 个{'(堆叠)' if stacked else ''}"


def preflight(url: str, farm: dict, pool: set[str],
              client: httpx.Client, show_names: bool = False) -> dict:
    out = {"url": url, "verdict": "REVIEW", "tag": "", "reasons": []}
    dom = re.sub(r"^https?://", "", url).split("/")[0].lower()

    # L1 robots
    rv, rmsg = robots_verdict(client, url)
    out["reasons"].append(f"L1 robots: {rmsg}")
    if rv == "banned":
        out.update(verdict="REJECT", tag="robots 终局")
        return out

    # L2a 已知农场域
    if dom in farm["domains"]:
        out.update(verdict="REJECT", tag="内容农场·已知域")
        out["reasons"].append(f"L2 农场域命中: {dom} 在指纹库")
        return out

    # 页面
    try:
        r = client.get(url)
        page = r.text
        out["reasons"].append(f"页面 GET {r.status_code} ({len(page)/1024:.0f}KB)")
    except Exception as e:  # noqa: BLE001
        out.update(verdict="REVIEW", tag="页面不可达")
        out["reasons"].append(f"页面 GET 失败: {type(e).__name__} {str(e)[:80]}")
        return out
    if r.status_code >= 400:
        out.update(verdict="REVIEW", tag=f"HTTP {r.status_code}")
        return out

    names = extract_names(page)
    hits = [n for n in names if n in pool]
    cross = [n for n in names if n in set(farm["names"])]
    icp = check_icp(page)
    ttl = re.search(r"<title>(.*?)</title>", page, re.S)
    title = ttl.group(1).strip() if ttl else ""
    seo, seo_msg = seo_title_verdict(title)

    # L2b 同族指纹交叉
    out["reasons"].append(
        f"L2 农场指纹交叉: {len(cross)} 个"
        + (f"({','.join(cross[:3])}…)" if cross else ""))
    if len(cross) >= 2:
        out.update(verdict="REJECT", tag="内容农场·同族指纹")
        return out

    # L3/L4/L5 证据
    out["reasons"].append(f"L3 ICP 备案: {icp or '无'}")
    out["reasons"].append(
        f"L4 剧名: {len(names)} 个, 池命中 {len(hits)}/{len(pool)} "
        f"({(len(hits)/len(names)*100 if names else 0):.0f}%)"
        + (f" 命中例: {','.join(hits[:3])}" if hits else ""))
    if show_names and names:
        out["reasons"].append(f"    剧名: {names[:12]}")
    out["reasons"].append(f"L5 SEO title: {seo_msg}")

    # 判定: 剧名>=3 且 0 命中 且 (无ICP 或 SEO 堆叠) → 农场
    if len(names) >= 3 and not hits and (icp is None or seo):
        out.update(verdict="REJECT", tag="内容农场(剧名编造+合规信号缺失)")
        return out
    if hits:
        out.update(verdict="PASS", tag=f"候选接入(池命中 {len(hits)})")
        return out
    out.update(verdict="REVIEW", tag="样本不足/形态待判(人工复核)")
    return out


def main(argv: list[str]) -> int:
    import io
    # CLI 直跑时包装(GBK 控制台中文安全); 被 import 时不产生副作用
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    show_names = "--show-names" in argv
    urls = [a for a in argv if not a.startswith("--")]
    if not urls:
        print(__doc__)
        return 2
    farm = load_farm_fp()
    pool = load_pool()
    print(f"预检 {len(urls)} 链 | 指纹库 {len(farm['domains'])} 域 "
          f"{len(farm['names'])} 名 | 池基准 {len(pool)} 部")
    stats = {"PASS": 0, "REJECT": 0, "REVIEW": 0}
    with httpx.Client(timeout=15.0, follow_redirects=True,
                      headers={"User-Agent": UA}) as c:
        for url in urls:
            r = preflight(url, farm, pool, c, show_names)
            stats[r["verdict"]] += 1
            print("=" * 66)
            print(f"[{r['verdict']}] {r['url']}  —— {r['tag']}")
            for x in r["reasons"]:
                print(f"  {x}")
    print("=" * 66)
    print(f"汇总: PASS {stats['PASS']} / REJECT {stats['REJECT']} / "
          f"REVIEW {stats['REVIEW']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
