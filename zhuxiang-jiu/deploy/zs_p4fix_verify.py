"""智搜意图判定·四轮修复生产验证(否定语义/NFKC)"""
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
    # 1. 否定语义: "不要送礼的" 场景槽不应为送礼
    d = post("/api/search-ai/query",
             {"text": "不要送礼的，自己喝的酒推荐"}).get("data", {})
    check("否定-场景不误提",
          d.get("slots", {}).get("scene") != "送礼",
          f"slots={d.get('slots')}")
    # 2. 正向对照: 送礼场景仍提取
    d = post("/api/search-ai/query",
             {"text": "推荐送礼的竹香酒"}).get("data", {})
    check("正向-场景仍提取",
          d.get("slots", {}).get("scene") == "送礼",
          f"slots={d.get('slots')}")
    # 3. NFKC: 全角Ｌ３等级槽位
    d = post("/api/search-ai/query",
             {"text": "我是Ｌ３会员有什么权益"}).get("data", {})
    check("NFKC-全角Ｌ３槽位",
          d.get("slots", {}).get("level") == 3, f"slots={d.get('slots')}")
    # 4. NFKC: 全角订单号
    d = post("/api/search-ai/query",
             {"text": "订单ＲＴ17580000000001发货了吗"}).get("data", {})
    check("NFKC-全角ＲＴ订单号",
          d.get("slots", {}).get("orderNo") == "RT17580000000001",
          f"slots={d.get('slots')}")
    # 5. 回归: 规则链三场景
    for text, expect in [("我的政策", "agent"), ("竹香酒怎么下单", "help"),
                         ("小孩能喝酒吗", "blocked")]:
        d = post("/api/search-ai/query", {"text": text}).get("data", {})
        check(f"回归[{expect}] {text}", d.get("intent") == expect,
              f"got={d.get('intent')}")

    print(f"\n{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
