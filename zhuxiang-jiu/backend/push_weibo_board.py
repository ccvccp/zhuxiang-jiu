"""36号·微博热搜板推送(国内 IP 开发机运行)

背景(2026-09-27 实证): 生产服务器为新加坡 IP, 微博 CLI 业务
接口 IP_GEO_DENIED 仅限中国大陆——微博热搜真实源由本机(国内
IP)拉取后推送生产 API 入缓存(redis TTL 48h), 雷达每轮扫描
免费消费。本脚本是微博热搜真实源的唯一拉取入口。

成本: 450C/次(建议每日 1 次, 随内容周期触发)。

运行(开发机, 先置环境):
    $env:Path="d:\\网站架构设计\\nodejs\\node-v20.18.2-win-x64;$env:Path"
    $env:USERPROFILE="d:\\网站架构设计\\.weibo-home"
    python push_weibo_board.py
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

BASE = "https://zxjiu.com"
NODE = r"d:\网站架构设计\nodejs\node-v20.18.2-win-x64\node.exe"
# 本机 npm 全局包(Windows 口径: {prefix}/node_modules)
ENTRY = (r"d:\网站架构设计\nodejs\node-v20.18.2-win-x64"
         r"\node_modules\@weibo-ai\weibo-cli\dist\index.js")
COUNT = 20


def call(method, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def main():
    print("=== 本机 CLI 拉微博热搜主榜(450C) ===")
    out = subprocess.run(
        [NODE, ENTRY, "search", "hot_word/biz",
         "--count", str(COUNT), "--output", "json"],
        capture_output=True, timeout=90)
    if out.returncode != 0:
        print("CLI 失败:",
              out.stderr.decode("utf-8", "replace")[:300])
        return 1
    board = json.loads(out.stdout.decode("utf-8", "replace"))
    rows = board.get("data") if isinstance(board, dict) else board
    if not isinstance(rows, list) or not rows:
        print("空榜:", json.dumps(board, ensure_ascii=False)[:200])
        return 1
    print(f"拉取 {len(rows)} 条, 首条: {rows[0].get('word')}")

    print("=== 推送生产入缓存 ===")
    st, body = call("POST", "/api/auth/login",
                    body={"phone": "13800000002",
                          "password": "test123456"})
    tok = body.get("accessToken", "")
    if not tok:
        print("登录失败:", st, str(body)[:150])
        return 1
    st, body = call("POST", "/api/promo/radar/weibo-board", tok,
                    {"data": rows})
    data = body.get("data") or {}
    print(f"推送: st={st} stored={data.get('stored')} "
          f"ttl={data.get('ttlSeconds')}")
    ok = (st == 200 and data.get("stored") == len(rows))
    print("OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
