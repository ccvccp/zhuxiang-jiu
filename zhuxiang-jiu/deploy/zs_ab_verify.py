"""A/B 话术框架·生产验证"""
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
    # 1. 分流生效: 招商意图两次 query → lead 话术 A/B 交替
    a1 = post("/api/search-ai/query",
              {"text": "我想做代理怎么申请"}).get("data", {})
    a2 = post("/api/search-ai/query",
              {"text": "招商政策是什么"}).get("data", {})
    # A/B 文案差异化关键词(A=供应链 / B=营销赋能)
    ans1, ans2 = a1.get("answer", ""), a2.get("answer", "")
    pair = (("供应链" in ans1) and ("营销赋能" in ans2)) or \
           (("营销赋能" in ans1) and ("供应链" in ans2))
    check("AB-生产分流生效(A/B话术交替)", pair,
          f"{ans1[:36]} | {ans2[:36]}")

    # 2. served 计数(容器内 redis 验证)
    # (admin 面无 token, 用服务器侧验证——此处仅验证决策面)

    # 3. 回归: 非实验意图与合规不受影响
    d = post("/api/search-ai/query", {"text": "麒麟是什么"}).get("data", {})
    check("回归-品牌意图正常", d.get("intent") == "brand",
          f"got={d.get('intent')}")
    d = post("/api/search-ai/query", {"text": "小孩能喝酒吗"}).get("data", {})
    check("回归-合规拦截正常", d.get("intent") == "blocked",
          f"got={d.get('intent')}")

    print(f"\n{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
