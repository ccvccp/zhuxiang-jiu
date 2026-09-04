"""48号P1 认知层·角色感知大脑 Docker 实机验收

运行方式:
    python verify_xiaozhu_p1_live.py [基址]

前置: 容器已运行(含 P1 代码, 镜像已重建)。

覆盖(计划 §五, 真实容器):
    01 正常业务零影响
    02 绑定 E2E(HTTP 三端点: 绑定/视图/解除)
    03 上下文调试视图(绑定态+LLM 轨 off)
    04 绑定后信值指令升级(查信值/查余额直读 45号)
    05 能换吗换算 E2E(看新品→指代→换算卡片)
    06 修复引导 E2E(无违规正向反馈)
    07 会话内快捷绑定流(「小竹，绑定信值档案 N」)
    08 LLM 轨默认 off(规则轨兜底断言)
    09 鉴权与业务回归

每轮验收前清理 zhuxiang:voice48:* 残留,
×2 轮幂等验证。
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []
HEADERS = {"X-Member-Id": "88"}


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def clear_voice48() -> None:
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:voice48:*"],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def call(method, path, body=None, headers=None,
         expect=(200,)):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                  method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            code, text = resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed)


def new_trust() -> int:
    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": "person",
        "name": f"p1live-{uuid.uuid4().hex[:6]}",
        "idNumber": f"110101{uuid.uuid4().hex[:10]}"})
    return body.get("trustId")


def main():
    print("=" * 62)
    print("48号·P1 认知层·角色感知大脑 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_voice48()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))

    print("\n[02 绑定 E2E]")
    tid = new_trust()
    ok, (code, body) = call("POST", "/api/xiaozhu/bindings",
                            body={"trustId": tid,
                                  "note": "实机绑定"},
                            headers=HEADERS)
    record("POST 绑定",
           code == 200 and body.get("trustId") == tid
           and body.get("boundAt"), str(body)[:60])
    ok, (code, body) = call("GET", "/api/xiaozhu/bindings",
                            headers=HEADERS)
    record("GET 绑定视图",
           code == 200 and body.get("trustId") == tid)
    # 改绑
    tid2 = new_trust()
    ok, (code, body) = call("POST", "/api/xiaozhu/bindings",
                            body={"trustId": tid2},
                            headers=HEADERS)
    ok, (code, body) = call("GET", "/api/xiaozhu/bindings",
                            headers=HEADERS)
    record("重复绑定=改绑", body.get("trustId") == tid2)

    print("\n[03 上下文调试视图]")
    ok, (code, body) = call("GET", "/api/xiaozhu/context",
                            headers=HEADERS)
    record("上下文(绑定态+LLM off)",
           code == 200 and body.get("bound") is True
           and body.get("trustId") == tid2
           and body.get("trustBalance") is not None
           and body.get("llmMode") is False,
           str(body)[:80])

    print("\n[04 绑定后信值指令升级]")
    ok, (code, body) = call("POST",
                            "/api/xiaozhu/sessions",
                            body={"channel": "voice"},
                            headers=HEADERS)
    sid = body.get("sessionId")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，查我的信值"}, headers=HEADERS)
    record("查信值直读(45号档案)",
           code == 200 and "信值分" in body.get("reply", "")
           and (body.get("card") or {}).get("type")
           == "trust_score",
           body.get("reply", "")[:50])
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，我的信值余额"},
        headers=HEADERS)
    record("查余额直读(45号 balance)",
           code == 200 and "余额" in body.get("reply", "")
           and (body.get("card") or {}).get("type")
           == "trust_balance",
           body.get("reply", "")[:50])

    print("\n[05 能换吗换算 E2E]")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，看新品"}, headers=HEADERS)
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "这个能用信值换吗"}, headers=HEADERS)
    card = body.get("card") or {}
    record("指代换算(商品 vs 余额)",
           code == 200 and "TV" in body.get("reply", "")
           and card.get("type") == "trust_exchange"
           and card.get("price") is not None
           and card.get("balance") is not None,
           body.get("reply", "")[:60])
    record("换算数学一致",
           card.get("enough")
           == (card.get("balance", 0)
               >= card.get("price", 0)), str(card)[:80])

    print("\n[06 修复引导 E2E]")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，我上次违章怎么修复"},
        headers=HEADERS)
    record("无违规正向反馈",
           code == 200
           and "没有待修复" in body.get("reply", ""),
           body.get("reply", "")[:50])

    print("\n[07 会话内快捷绑定流]")
    tid3 = new_trust()
    ok, (code, body) = call("POST", "/api/xiaozhu/sessions",
                            body={"channel": "voice"},
                            headers=HEADERS)
    sid2 = body.get("sessionId")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid2}/text",
        body={"text": f"小竹，绑定信值档案 {tid3}"},
        headers=HEADERS)
    record("快捷绑定指令",
           code == 200 and "已绑定" in body.get("reply", "")
           and (body.get("card") or {}).get("trustId")
           == tid3,
           body.get("reply", "")[:50])
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid2}/text",
        body={"text": "小竹，绑定信值档案 99999"},
        headers=HEADERS)
    record("错误档案号引导",
           "绑定失败" in body.get("reply", ""),
           body.get("reply", "")[:40])

    print("\n[08 LLM 轨默认 off]")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid2}/text",
        body={"text": "小竹，帮我看看天气"},
        headers=HEADERS)
    record("规则轨兜底(track=rule)",
           code == 200
           and body.get("track") == "rule"
           and "还不会" in body.get("reply", ""),
           str(body.get("track")))

    print("\n[09 鉴权与业务回归]")
    ok, (code, _) = call("POST", "/api/xiaozhu/bindings",
                        body={"trustId": 1})
    record("缺 Member-Id 401", code == 401, str(code))
    ok, (code, _) = call("GET", "/api/xiaozhu/context")
    record("context 缺 Member-Id 401", code == 401,
           str(code))
    ok, (code, _) = call("POST", "/api/xiaozhu/bindings",
                        body={"trustId": 99999},
                        headers=HEADERS, expect=(404,))
    record("档案不存在 404", code == 404, str(code))
    # 解除绑定
    ok, (code, body) = call("DELETE",
                            "/api/xiaozhu/bindings",
                            headers=HEADERS)
    record("DELETE 解除绑定",
           code == 200 and body.get("bound") is False)
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
