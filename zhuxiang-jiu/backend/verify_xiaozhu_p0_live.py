"""48号P0 小竹智能语音中枢 Docker 实机验收

运行方式:
    python verify_xiaozhu_p0_live.py [基址]

前置: 容器已运行(含 P0 代码, 镜像已重建)。

覆盖(计划 §四, 真实容器):
    01 正常业务零影响
    02 会话开启 + 指令集
    03 唤醒直达 E2E(看新品→产品卡片+jump)
    04 免唤醒连续对话 E2E(指代消解)
    05 八指令逐一实测(导航/优惠/转人工/帮助/信值引导)
    06 未唤醒反语音霸权(不执行只提示)
    07 语音降级(无 key: 结构化失败+keyboard 兜底)
    08 隐私红线(PII mask 落库 + 一键清除级联)
    09 鉴权与业务回归

每轮验收前清理 zhuxiang:voice48:* 残留, ×2 轮幂等验证。
"""
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []
HEADERS = {"X-Member-Id": "42"}


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


def main():
    print("=" * 62)
    print("48号·P0 小竹智能语音中枢 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_voice48()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel?role=member")
    record("35号 Hub 面板回归", code == 200, str(code))

    print("\n[02 会话开启 + 指令集]")
    ok, (code, body) = call(
        "POST", "/api/xiaozhu/sessions",
        body={"channel": "voice"}, headers=HEADERS)
    sid = body.get("sessionId")
    record("开启会话",
           code == 200 and body.get("success") is True
           and sid >= 1 and body.get("memberId") == 42,
           str(body)[:70])
    ok, (code, body) = call("GET", "/api/xiaozhu/commands")
    cmds = body.get("commands") or []
    record("指令集自描述(10 条)",
           code == 200 and len(cmds) == 10
           and body.get("wakeWords") == ["小竹"],
           str(len(cmds)))

    print("\n[03 唤醒直达 E2E]")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，看看有什么新上线产品"},
        headers=HEADERS)
    record("看新品直达(卡片+jump)",
           code == 200 and "新品" in body.get("reply", "")
           and (body.get("card") or {}).get("type")
           == "product_list"
           and body.get("jump")
           == "/product-list.html?sort=new"
           and len((body.get("card") or {})
                   .get("items") or []) >= 1,
           str(body.get("reply"))[:50])
    cards_items = (body.get("card") or {}).get("items") or []

    print("\n[04 免唤醒连续对话 E2E]")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "这个多少钱"}, headers=HEADERS)
    record("指代消解(这个多少钱)",
           code == 200
           and body.get("wakeHint") is False
           and "元" in body.get("reply", ""),
           str(body.get("reply"))[:50])
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "查优惠"}, headers=HEADERS)
    record("免唤醒直接解析(查优惠)",
           code == 200
           and ("活动" in body.get("reply", "")
                or "优惠" in body.get("reply", "")),
           body.get("reply", "")[:40])

    print("\n[05 八指令逐一实测]")
    for text, check in (
            ("小竹，竹香酒多少钱",
             lambda b: "元" in b.get("reply", "")),
            ("小竹，查我的信值",
             lambda b: "绑定" in b.get("reply", "")),
            ("小竹，我的信值余额",
             lambda b: "绑定" in b.get("reply", "")),
            ("小竹，打开购物车",
             lambda b: b.get("jump") == "/cart.html"),
            ("小竹，转人工客服",
             lambda b: "人工" in b.get("reply", "")),
            ("小竹，你能干什么",
             lambda b: (b.get("card") or {}).get("type")
             == "help"),
    ):
        ok, (code, body) = call(
            "POST", f"/api/xiaozhu/sessions/{sid}/text",
            body={"text": text}, headers=HEADERS)
        record(f"「{text}」",
               code == 200 and check(body),
               body.get("reply", "")[:40])

    print("\n[06 反语音霸权]")
    ok, (code, body) = call(
        "POST", "/api/xiaozhu/sessions",
        body={"channel": "voice"}, headers=HEADERS)
    sid2 = body.get("sessionId")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid2}/text",
        body={"text": "看新品"}, headers=HEADERS)
    record("新会话未唤醒不执行",
           code == 200
           and body.get("wakeHint") is True
           and "小竹" in body.get("reply", ""),
           str(body.get("wakeHint")) + body.get("reply",
                                                "")[:30])

    print("\n[07 语音降级(无 key)]")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/voice",
        body={"audioBase64": base64.b64encode(
            b"fake-audio").decode()},
        headers=HEADERS)
    record("语音降级结构化兜底",
           code == 200
           and "语音" in body.get("reply", "")
           and body.get("fallbackHint") == "keyboard",
           str(body.get("reply"))[:50])

    print("\n[08 隐私红线]")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，我的手机 13812345678 帮查下订单"},
        headers=HEADERS)
    ok, (code, view) = call(
        "GET", f"/api/xiaozhu/sessions/{sid}")
    turns = view.get("turns") or []
    masked = [t for t in turns
              if "1381234" not in (t.get("rawText") or "")]
    record("PII mask 落库(手机号脱敏)",
           len(masked) == len(turns),
           str([t.get("rawText") for t in turns
                if "手机" in (t.get("rawText") or "")]))
    ok, (code, body) = call(
        "DELETE", f"/api/xiaozhu/sessions/{sid}")
    record("一键清除级联",
           code == 200
           and body.get("removedRecords", 0) >= 8,
           str(body.get("removedRecords")))
    ok, (code, _) = call(
        "GET", f"/api/xiaozhu/sessions/{sid}",
        expect=(404,))
    record("清除后 404", code == 404, str(code))

    print("\n[09 鉴权与业务回归]")
    ok, (code, _) = call(
        "POST", "/api/xiaozhu/sessions",
        body={"channel": "voice"},
        headers={"X-Member-Id": "abc"})
    record("非法 Member-Id 401", code == 401, str(code))
    ok, (code, body) = call(
        "GET", "/api/product/list?sort=new")
    record("产品模块回归",
           code == 200
           and (body.get("count") or 0) >= 1,
           str(code))
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
