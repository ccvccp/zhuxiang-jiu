"""小竹护栏人工恢复(admin resume——决策留痕)"""
import json
import urllib.request

BASE = "http://localhost:8000"


def req(method, path, headers=None, body=None):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(
        BASE + path, data=data, headers=h,
        method=method)
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode())


tok = req("POST", "/api/auth/login", body={
    "phone": "13800000002",
    "password": "test123456"})
token = tok.get("accessToken") \
    or (tok.get("data") or {}).get("accessToken")
r = req("POST", "/api/xiaozhu/mode/resume",
        headers={"Authorization": f"Bearer {token}",
                 "X-Role": "admin"},
        body={"note": "asrFailRate 误伤清洗: 103 条"
                      "测试轮(bytes=0, '#'守卫/fake-audio)"
                      "已剔除, 真实失败 0 条; 备份留痕"
                      "asr_failed_test_rounds_*.json"})
print(json.dumps(r, ensure_ascii=False, indent=1))
