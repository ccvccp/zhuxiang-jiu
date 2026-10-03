"""智搜意图判定·P2修复生产验证(LLM兜底审计完整 + 防刷分)"""
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
    # 1. 规则链场景回归(4 意图 + 合规)
    cases = [
        ("竹香酒怎么下单", "help"), ("退货怎么办理", "order"),
        ("会员有什么权益", "equity"), ("竹奕酒多少钱一瓶", "product"),
        ("小孩能喝酒吗", "blocked"),
    ]
    for text, expect in cases:
        d = post("/api/search-ai/query", {"text": text}).get("data", {})
        check(f"规则链[{expect}] {text}", d.get("intent") == expect,
              f"got={d.get('intent')}")

    # 2. LLM 兜底真实链路(低置信口语)
    d = post("/api/search-ai/query",
             {"text": "帮我挑个送领导的口粮酒"}).get("data", {})
    check("LLM兜底-低置信口语可用",
          d.get("intent") in ("product", "chat", "brand", "help",
                              "equity", "order", "agent", "attract"),
          f"intent={d.get('intent')}")

    # 3. 防刷分: 同决策二次反馈不重复进化
    d = post("/api/search-ai/query", {"text": "竹香酒怎么样"}).get("data", {})
    did = d.get("decisionId")
    f1 = post("/api/search-ai/feedback",
              {"decisionId": did, "verdict": "useful"}).get("data", {})
    f2 = post("/api/search-ai/feedback",
              {"decisionId": did, "verdict": "useful"}).get("data", {})
    check("防刷分-首次进化", f1.get("evolved") is True, str(f1)[:100])
    check("防刷分-二次仅留痕",
          f2.get("evolved") is False and "防刷分" in str(
              f2.get("note", "")), str(f2)[:100])

    print(f"\n{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
