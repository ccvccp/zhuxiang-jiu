"""43号P0 Docker 实机快速验证(宿主机运行)"""
import json
import sys
import urllib.request
import urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")


def call(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode()[:100]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:100]


print("健康检查(快道):", call("GET", "/api/decision/health")[0])
print("正常业务流量:", call("GET", "/api/product/list")[0])
print("正常流量带分页:", call("GET", "/api/product/list?page=1")[0])
print("SQLi攻击(observe放行):",
      call("GET", "/api/product/search?kw=%27%20OR%201%3D1%20--")[0])
print("探针路径(observe放行):", call("GET", "/.env")[0])
print("会员登录:", call("POST", "/api/member/login",
                        {"phone": "13800000001",
                         "password": "Pass1234"})[0])
print("管理端点(鉴权照常):", call("GET", "/api/invoice/admin/stats",
                                  headers={"X-Role": "admin"})[0])
