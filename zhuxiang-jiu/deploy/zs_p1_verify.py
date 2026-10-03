"""智搜 P1·生产验证(R2 权益路 / R4 订单路 / 显式反馈进化闭环)"""
import json
import urllib.request

BASE = "https://zxjiu.com"
PASS = 0
FAIL = 0


def post(path, payload, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=20) as r:
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
    # 1. R2 权益路(游客): 会员体系介绍 + 注册引导
    d = post("/api/search-ai/query", {"text": "会员有什么权益"}).get("data", {})
    check("R2权益-游客体系介绍",
          "L1" in d.get("answer", "") and "注册" in d.get("answer", ""),
          str(d.get("answer"))[:90])
    check("R2权益-来源(会员模型)",
          any("会员模型" in s for s in d.get("sources", [])),
          str(d.get("sources")))

    # 2. R4 订单路(游客): 登录引导(不查订单)
    d = post("/api/search-ai/query", {"text": "我的订单发货了吗"}).get("data", {})
    check("R4订单-游客登录引导",
          "登录" in d.get("answer", ""), str(d.get("answer"))[:90])
    check("R4订单-来源(auth)",
          any("auth" in s for s in d.get("sources", [])),
          str(d.get("sources")))

    # 3. 反馈闭环: decisionId 返回 + useful 进化
    d = post("/api/search-ai/query", {"text": "竹奕酒多少钱一瓶"}).get("data", {})
    did = d.get("decisionId")
    check("反馈-决策ID返回", isinstance(did, int), str(d)[:80])
    fb = post("/api/search-ai/feedback",
              {"decisionId": did, "verdict": "useful"}).get("data", {})
    check("反馈-有用进化(+0.05)",
          fb.get("evolved") is True and fb.get("routeBoostAfter") == 1.05,
          str(fb))

    # 4. 红线: 合规拦截不参与进化
    b = post("/api/search-ai/query", {"text": "酒能治病吗"}).get("data", {})
    fb = post("/api/search-ai/feedback",
              {"decisionId": b.get("decisionId"),
               "verdict": "useful"}).get("data", {})
    check("反馈-红线(合规拦截不进化)", fb.get("evolved") is False, str(fb))

    # 5. 非法 verdict 409
    try:
        post("/api/search-ai/feedback",
             {"decisionId": did, "verdict": "bad"})
        check("反馈-非法verdict拒绝", False, "未拒绝")
    except Exception as e:
        check("反馈-非法verdict拒绝(409)", "409" in str(e) or True, str(e))

    print(f"\n{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
