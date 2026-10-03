"""智搜 P3·生产验证(混合意图拆分)"""
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
    # 1. 混合意图双路合并回答
    d = post("/api/search-ai/query",
             {"text": "竹奕酒多少钱，顺便看看我的订单发货了吗"}
             ).get("data", {})
    check("拆分-主意图product", d.get("intent") == "product",
          f"got={d.get('intent')}")
    check("拆分-副问摘要合并",
          "订单服务" in d.get("answer", "")
          and "另外" in d.get("answer", ""),
          d.get("answer", "")[:110])
    check("拆分-副卡进结果",
          any(r.get("subIntent") == "order"
              for r in d.get("results", [])),
          f"n={len(d.get('results', []))}")

    # 2. 护栏: 合规整句拦截(混合句含违规词不拆不放行)
    d = post("/api/search-ai/query",
             {"text": "竹奕酒多少钱，这酒能治病吗"}).get("data", {})
    check("护栏-合规整句拦截", d.get("intent") == "blocked",
          f"got={d.get('intent')}")

    # 3. 单意图回归不受影响
    for text, expect in [("竹奕酒多少钱一瓶", "product"),
                         ("会员有什么权益", "equity")]:
        d = post("/api/search-ai/query", {"text": text}).get("data", {})
        check(f"回归[{expect}] {text}", d.get("intent") == expect,
              f"got={d.get('intent')}")

    print(f"\n{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
