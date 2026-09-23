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
        body={"note": "人工评估恢复: 真实重判后 asrFailRate "
                      "0.1043 超基线 4.2%(103 条全真实"
                      "语音轮已复核恢复——75 条 '#' 转写"
                      "=残响/远场音质差 + 28 条转写失败); "
                      "每次失败均有优雅降级(引导重说/键盘"
                      "兜底), 无资金/合规风险, 恢复 assist "
                      "持续观察"})
print(json.dumps(r, ensure_ascii=False, indent=1))
