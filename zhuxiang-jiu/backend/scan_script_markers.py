"""36号·存量内容脚本标记扫描(2026-09-27 治理动作)

扫描全部 promo_contents 正文的分镜时间标记/脚本结构标签,
输出 contentId/platform/status/receipt.mode 与命中行,
供存量修正决策(#30 已知命中)。

运行: python3 scan_script_markers.py (生产服务器本机)
"""
import json
import re
import urllib.request

BASE = "http://127.0.0.1:8000"
MARKER_RE = re.compile(r"【\d+(?:-\d+)?s[^】]*】")


def call(method, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                  headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "{}")


tok = call("POST", "/api/auth/login",
           body={"phone": "13800000002",
                 "password": "test123456"})["accessToken"]
rows = call("GET", "/api/promo/contents?limit=200", tok)["data"] or []
print(f"内容总数: {len(rows)}")
hits = 0
for c in rows:
    body = str(c.get("body") or "")
    marks = MARKER_RE.findall(body)
    legacy = "【热点借势】" in body
    if marks or legacy:
        hits += 1
        receipt = c.get("receipt") or {}
        print(f"#{c.get('contentId')} [{c.get('platform')}] "
              f"{c.get('status')}/{receipt.get('mode')} "
              f"markers={marks} legacy_hot_label={legacy}")
        print(f"   title: {c.get('title', '')}")
print(f"命中: {hits} 条")
