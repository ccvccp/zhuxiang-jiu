# -*- coding: utf-8 -*-
"""djtj7 双域终判: .com(已接入8条)池内快照剧名 vs .com.cn 剧名重合 + .com 重试"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
import httpx
from sqlalchemy import select
from db import get_session
from models import DramaRankSnapshot

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 已接入 djtj7.com 的 8 条快照剧名
with get_session() as s:
    snaps = s.execute(select(DramaRankSnapshot).where(
        DramaRankSnapshot.source == "djtj7")).scalars().all()
com_names = {sn.drama_name for sn in snaps}
print(f"djtj7.com 已入库快照 {len(snaps)} 条剧名: {sorted(com_names)}")

cn_names = set()
com_text = ""
with httpx.Client(timeout=15.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    r = c.get("https://www.djtj7.com.cn/")
    cn_names = {m.group(1).strip() for m in re.finditer(r"《([^》]{2,20})》", r.text)}
    print(f"djtj7.com.cn 剧名 {len(cn_names)}: {sorted(cn_names)}")
    try:
        r2 = c.get("https://www.djtj7.com/")
        com_text = r2.text
        ttl = re.search(r"<title>(.*?)</title>", com_text, re.S)
        n_rankno = len(re.findall(r'class="rank-no"', com_text))
        live = {m.group(1).strip() for m in re.finditer(r"《([^》]{2,20})》", com_text)}
        print(f"djtj7.com 重试 OK: title={ttl.group(1).strip()[:44]!r} "
              f"rank-no={n_rankno} 《》剧名={sorted(live)}")
        print(f"  .com 页面剧名 vs 库内快照重合: {sorted(live & com_names)}")
    except Exception as e:  # noqa: BLE001
        print(f"djtj7.com 重试仍失败: {type(e).__name__} {str(e)[:80]}")

inter = com_names & cn_names
print(f"\n双域剧名重合(.com 库内 vs .com.cn 页面): {len(inter)} {sorted(inter)}")
