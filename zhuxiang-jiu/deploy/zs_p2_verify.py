"""智搜 P2·生产验证(隐式回流 / LLM 意图兜底真实触发)"""
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
    # 1. 隐式转化回流: 动作卡点击 → routeBoost +0.03
    d = post("/api/search-ai/query", {"text": "竹奕酒多少钱一瓶"}).get("data", {})
    did = d.get("decisionId")
    fc = post("/api/search-ai/action-click",
              {"decisionId": did,
               "actionLabel": "逛商城选酒"}).get("data", {})
    check("隐式回流-动作点击进化(+0.03)",
          fc.get("evolved") is True
          and fc.get("routeBoostAfter") == 1.03
          and fc.get("source") == "action"
          and fc.get("actionLabel") == "逛商城选酒", str(fc))

    # 2. 红线: 合规拦截决策的动作点击不进化
    b = post("/api/search-ai/query", {"text": "酒能治病吗"}).get("data", {})
    fc = post("/api/search-ai/action-click",
              {"decisionId": b.get("decisionId"),
               "actionLabel": "x"}).get("data", {})
    check("隐式回流-红线(合规拦截不进化)",
          fc.get("evolved") is False, str(fc))

    # 3. LLM 意图兜底真实触发(口语低置信句; glm-4-flash 真调用)
    d = post("/api/search-ai/query",
             {"text": "帮我挑个送领导的口粮酒"}).get("data", {})
    ok_intent = d.get("intent") in ("product", "chat", "brand",
                                    "help", "equity")
    check("LLM兜底-低置信口语意图合法", ok_intent,
          f"intent={d.get('intent')} conf={d.get('confidence')}")

    # 4. 显式反馈对 LLM 兜底决策 → intentWeight 留痕(P2 进化)
    fb = post("/api/search-ai/feedback",
              {"decisionId": d.get("decisionId"),
               "verdict": "useful"}).get("data", {})
    check("反馈-P2字段(source/intentWeight留痕)",
          fb.get("source") == "explicit"
          and "intentWeight" in str(fb), str(fb)[:120])

    print(f"\n{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
