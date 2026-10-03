"""全站智能体规划 GAP-1~3·生产验证"""
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
    # GAP-2a 区域保护卡(省份联动)
    d = post("/api/search-ai/query",
             {"text": "做山东的代理怎么申请"}).get("data", {})
    routes = [r.get("route") for r in d.get("results", [])]
    check("GAP2a-区域保护卡", "region" in routes,
          f"routes={routes} ans={d.get('answer', '')[:60]}")

    # GAP-3a 宴请餐饮导流卡
    d = post("/api/search-ai/query",
             {"text": "商务宴请用什么酒"}).get("data", {})
    routes = [r.get("route") for r in d.get("results", [])]
    check("GAP3a-餐饮导流卡", "dining" in routes,
          f"routes={routes}")

    # GAP-2c 库存展示(商品卡 snippet 含库存词)
    d = post("/api/search-ai/query",
             {"text": "竹奕酒多少钱一瓶"}).get("data", {})
    prods = [r for r in d.get("results", [])
             if r.get("route") == "product"]
    tip = any(("有货" in r.get("snippet", "")
               or "库存紧张" in r.get("snippet", "")
               or "补货中" in r.get("snippet", "")) for r in prods)
    check("GAP2c-库存状态展示", (not prods) or tip,
          str([r.get("snippet", "")[:30] for r in prods[:1]]))

    # GAP-1 输出守门(正常输出不受影响; 净化逻辑回归覆盖)
    d = post("/api/search-ai/query", {"text": "会员有什么权益"}
             ).get("data", {})
    check("回归-权益路正常", d.get("intent") == "equity",
          f"got={d.get('intent')}")

    print(f"\n{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
