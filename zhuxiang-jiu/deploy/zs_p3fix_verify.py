"""智搜意图判定·三轮修复生产验证(自我指代/槽位/LLM经济性)"""
import json
import urllib.request

BASE = "https://zxjiu.com"
PASS = 0
FAIL = 0


def post(path, payload):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def check(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  OK {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} -- {detail}")


def main():
    cases = [
        ("我的政策", "agent", "规划验收场景·自我指代"),
        ("我的等级", "equity", "自我指代"),
        ("订单RT17580000000001发货了吗", "order", "精确单查询意图"),
        ("运单SF1234567890到哪了", "order", "运单号槽位"),
        ("？？？。。。", "chat", "纯符号兜底"),
        ("竹香酒怎么下单", "help", "规则链回归"),
        ("小孩能喝酒吗", "blocked", "合规回归"),
    ]
    for text, expect, note in cases:
        d = post("/api/search-ai/query", {"text": text}).get("data", {})
        check(f"[{expect}] {text} ({note})",
              d.get("intent") == expect,
              f"got={d.get('intent')}")
        if text.startswith("订单RT"):
            check("精确单游客口径(登录引导)",
                  "登录" in d.get("answer", ""),
                  d.get("answer", "")[:50])

    print(f"\n{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
