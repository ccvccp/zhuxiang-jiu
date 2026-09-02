# -*- coding: utf-8 -*-
"""40号 P3b 冻结契约·参考源代理(自建爬虫代理的确定性参考实现)

用途:
    - 代理侧开发的契约参照(协议冻结, 见 blogger_source_adapter 模块注释)
    - 本地/实机 proxy 模式联调: 设 BLOGGER_SOURCE_MODE=proxy +
      BLOGGER_{PLATFORM}_URL=http://127.0.0.1:18900/works 即切换

契约(冻结):
    GET /works?account={博主账号}&cursor={增量游标}&limit=20
    响应: {"works": [{"workId", "title", "summary", "likes",
            "comments", "shares", "coverUrl", "durationSeconds",
            "publishedAt"}], "nextCursor": ""}
    鉴权: Authorization: Bearer {PROXY_API_KEY}(配置后启用)

数据口径: 每账号确定性生成 3 条作品(hash 种子, 同账号同结果),
支持运维与联调验证指纹去重/增量游标全链路。

用法:
    python scripts/reference_source_proxy.py            # 0.0.0.0:18900
    $env:PROXY_PORT=18901; python scripts/...           # 自定义端口
    $env:PROXY_API_KEY=secret; python scripts/...       # 启用 Bearer
"""
import hashlib
import json
import os
import urllib.parse
from datetime import datetime, timedelta, UTC
from http.server import BaseHTTPRequestHandler, HTTPServer

_TITLES = ("竹香酒开箱测评与推荐清单", "中秋宴席用酒指南",
           "送礼不踩坑的三个原则")


def proxy_works_for(account: str, limit: int = 20) -> list[dict]:
    """确定性参考作品(同账号同结果, hash 种子)"""
    works = []
    now = datetime.now(UTC)
    for i, title in enumerate(_TITLES[:max(1, min(3, limit))]):
        seed = hashlib.sha256(
            f"{account}|{i}".encode("utf-8")).hexdigest()
        base = int(seed[:6], 16)
        published = now - timedelta(hours=i + 1)
        works.append({
            "workId": f"{account}_w{i}",
            "title": title,
            "summary": f"@{account} 的参考作品 {i}",
            "likes": 800 + base % 5000,
            "comments": 40 + base % 300,
            "shares": 10 + base % 120,
            "coverUrl": (f"https://ref-proxy.local/{account}"
                         f"/{i}/cover.jpg"),
            "durationSeconds": 15 + base % 45,
            "publishedAt": published.isoformat(),
        })
    return works


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/works":
            self.send_response(404)
            self.end_headers()
            return
        key = os.environ.get("PROXY_API_KEY", "").strip()
        if key and self.headers.get("Authorization", "") \
                != f"Bearer {key}":
            self.send_response(401)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        account = (qs.get("account") or [""])[0]
        limit = int((qs.get("limit") or ["20"])[0])
        if not account:
            body = json.dumps({"works": [], "nextCursor": "",
                               "error": "missing account"})
        else:
            body = json.dumps({"works": proxy_works_for(account,
                                                        limit),
                               "nextCursor": ""})
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print("[reference-proxy] " + fmt % args)


def make_server(host: str = "127.0.0.1", port: int = 0) -> HTTPServer:
    """可编程启动(测试用; port=0 自动分配)"""
    return HTTPServer((host, port), _Handler)


if __name__ == "__main__":
    port = int(os.environ.get("PROXY_PORT", "18900"))
    server = make_server("0.0.0.0", port)
    print(f"[reference-proxy] listening on 0.0.0.0:{port} "
          f"(contract: GET /works?account=&cursor=&limit=)")
    server.serve_forever()
