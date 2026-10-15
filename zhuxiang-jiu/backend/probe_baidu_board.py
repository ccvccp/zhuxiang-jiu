"""百度 board 端点结构探测(本地跑, 需外网)"""
import json
import urllib.request

req = urllib.request.Request(
    "https://top.baidu.com/api/board?platform=pc&tab=realtime",
    headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=8) as r:
    d = json.loads(r.read().decode("utf-8"))
cards = d.get("data", {}).get("cards", [])
print("cards:", len(cards))
rows = cards[0].get("content", []) if cards else []
inner = rows[0].get("content", []) if rows else []
print("inner items:", len(inner))
for x in inner[:6]:
    print({k: x.get(k) for k in ("word", "hotScore",
                                 "isTop", "index")})
print("字段全集:", sorted(inner[0].keys())
      if inner else "无")
