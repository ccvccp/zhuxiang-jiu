# -*- coding: utf-8 -*-
"""契约端点 contentType 验证: GET 8511/dramas?n=3 → 条目应带
contentType=manju(漫剧)/duanju(短剧) 三态之一(2026-10-15 分轨改造收尾)"""
import json
import httpx

BASE = "http://127.0.0.1:8511/dramas"

def probe(n: int, offset: int = 0, timeout: float = 300.0) -> dict:
    r = httpx.get(BASE, params={"n": n, "offset": offset}, timeout=timeout)
    print(f"GET n={n} offset={offset} → {r.status_code} ({len(r.text)/1024:.1f}KB, {r.elapsed.total_seconds():.0f}s)")
    r.raise_for_status()
    return r.json()

def main() -> None:
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    offset = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    data = probe(n, offset)
    items = data.get("items") or data.get("dramas") or []
    print(f"契约 keys: {sorted(data.keys())}")
    print(f"条目数: {len(items)}")
    for it in items:
        ct = it.get("contentType", "<缺失>")
        print(f"  [{ct:>7}] {it.get('title')}  ep={it.get('episodeCount', it.get('episodes'))}")
    cts = {it.get("contentType") for it in items}
    print(f"\ncontentType 取值集合: {cts}")
    assert cts and "<缺失>" not in cts and all(cts), "存在缺失/空 contentType 条目"
    print("PASS: 全部条目带 contentType 分轨标记")

if __name__ == "__main__":
    main()
