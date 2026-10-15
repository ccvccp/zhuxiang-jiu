# -*- coding: utf-8 -*-
"""sdc AI 榜详情页元数据闭环(2026-10-15 方案落点三):
榜单页 article→slug 映射 → 逐剧详情页(1 QPS) TVSeason JSON-LD 解析
→ sdc_ai 快照 raw_json 补 {season, episodes, genre, detail}
→ 互链主表 genre_tags merge 站方 genre(含「AI漫剧」标签)"""
import io
import json
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
import httpx
from sqlalchemy import select
from db import get_session
from models import Drama, DramaRankSnapshot

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
RANK_URL = "https://shortdramacast.com/zh-cn/rankings/ai-short-dramas"


def fetch_slug_map(c: httpx.Client) -> list[tuple[str, str]]:
    """榜单页 article 块 → (剧名, /zh-cn/dramas/{slug}) 映射"""
    t = c.get(RANK_URL).text
    pairs = []
    for blk in re.split(r"<article\b", t)[1:]:
        mn = re.search(r'data-analytics-item-name="([^"]{2,40})"', blk)
        mh = re.search(r'href="(/zh-cn/dramas/[^"]+)"', blk)
        if mn and mh:
            pairs.append((mn.group(1), mh.group(1)))
    return pairs


def fetch_detail_meta(c: httpx.Client, slug: str) -> dict | None:
    """详情页 TVSeason JSON-LD → {season, episodes, genre}"""
    d = c.get("https://shortdramacast.com" + slug).text
    for m in re.finditer(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', d, re.S):
        try:
            obj = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        for o in (obj if isinstance(obj, list) else [obj]):
            types = o.get("@type") or []
            types = types if isinstance(types, list) else [types]
            if "TVSeason" in types or "TVSeries" in types:
                # 多季剧=TVSeason(含 seasonNumber); 单季剧=TVSeries(无季字段)
                return {
                    "season": o.get("seasonNumber"),
                    "episodes": o.get("numberOfEpisodes"),
                    "genre": [str(g) for g in o.get("genre") or []],
                }
    return None


def main() -> None:
    metas: dict[str, dict] = {}
    fails: list[str] = []
    with httpx.Client(timeout=20.0, follow_redirects=True,
                      headers={"User-Agent": UA}) as c:
        pairs = fetch_slug_map(c)
        print(f"slug 映射 {len(pairs)} 对")
        for name, slug in pairs:
            time.sleep(1.0)
            try:
                meta = fetch_detail_meta(c, slug)
            except Exception as e:  # noqa: BLE001(网络失败留痕不中断)
                print(f"  [失败] {name}: {type(e).__name__} {e}")
                fails.append(name)
                continue
            if meta:
                meta["detail"] = RANK_URL.rsplit("/rankings", 1)[0] + slug
                metas[name] = meta
                print(f"  [ok] {name}: S{meta['season']} ep={meta['episodes']} "
                      f"genre={meta['genre']}")
            else:
                print(f"  [缺JSON-LD] {name}")
                fails.append(name)
    print(f"详情页元数据 {len(metas)} 部 / 失败 {len(fails)}: {fails}")

    with get_session() as s:
        # 最新一期 sdc_ai 快照(uk_snap 幂等 → 只补当期行)
        latest = s.execute(select(DramaRankSnapshot.snapshot_time)
                           .where(DramaRankSnapshot.source == "sdc_ai")
                           .order_by(DramaRankSnapshot.snapshot_time.desc())
                           .limit(1)).scalar()
        snaps = s.execute(select(DramaRankSnapshot).where(
            DramaRankSnapshot.source == "sdc_ai",
            DramaRankSnapshot.snapshot_time == latest)).scalars().all()
        n_snap = n_drama = 0
        for sn in snaps:
            meta = metas.get(sn.drama_name)
            if not meta:
                continue
            rj = dict(sn.raw_json or {})
            rj.update({k: v for k, v in meta.items() if v is not None})
            sn.raw_json = rj
            n_snap += 1
            if sn.drama_id:
                d = s.get(Drama, sn.drama_id)
                if d is not None:
                    cur = list(d.genre_tags or [])
                    for g in meta["genre"]:
                        if g and g not in cur:
                            cur.append(g)
                    d.genre_tags = cur
                    n_drama += 1
        s.commit()
        print(f"快照 raw_json 补齐 {n_snap} 行 / 主表 genre_tags merge {n_drama} 行")

    # 复核
    with get_session() as s:
        snaps = s.execute(select(DramaRankSnapshot).where(
            DramaRankSnapshot.source == "sdc_ai",
            DramaRankSnapshot.snapshot_time == latest)).scalars().all()
        ok = [sn for sn in snaps if (sn.raw_json or {}).get("detail")]
        ai_tag = s.execute(select(Drama).where(
            Drama.is_ai_drama.is_(True))).scalars().all()
        with_ai = [d for d in ai_tag if "AI漫剧" in (d.genre_tags or [])]
        print(f"复核: 快照带 detail {len(ok)}/{len(snaps)}; "
              f"is_ai_drama {len(ai_tag)} 行中 genre_tags 含「AI漫剧」{len(with_ai)} 行")
        for d in with_ai[:5]:
            print(f"  {d.drama_name}: {d.genre_tags}")


if __name__ == "__main__":
    main()
