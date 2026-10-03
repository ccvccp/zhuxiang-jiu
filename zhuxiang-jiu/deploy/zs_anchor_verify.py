"""智搜锚点自动调权·生产验证"""
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
    # 1. 反馈 → 锚点调权留痕(命中词 +0.1)
    d = post("/api/search-ai/query", {"text": "竹香酒怎么样"}).get("data", {})
    fb = post("/api/search-ai/feedback",
              {"decisionId": d.get("decisionId"),
               "verdict": "useful"}).get("data", {})
    tun = fb.get("anchorTuning") or []
    check("锚点-反馈调权留痕(+0.1)",
          any(t.get("word") == "竹香" and t.get("after") == 1.1
              for t in tun), str(tun))

    # 2. 防刷分: 二次反馈不重复调
    fb2 = post("/api/search-ai/feedback",
               {"decisionId": d.get("decisionId"),
                "verdict": "useful"}).get("data", {})
    check("锚点-防刷分(二次仅留痕)",
          fb2.get("evolved") is False
          and "anchorTuning" not in fb2, str(fb2)[:100])

    # 3. 判定回归(锚点基线未破坏日常查询)
    for text, expect in [("竹奕酒多少钱一瓶", "product"),
                         ("会员有什么权益", "equity"),
                         ("我的政策", "agent")]:
        r = post("/api/search-ai/query", {"text": text}).get("data", {})
        check(f"回归[{expect}] {text}", r.get("intent") == expect,
              f"got={r.get('intent')}")

    print(f"\n{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
