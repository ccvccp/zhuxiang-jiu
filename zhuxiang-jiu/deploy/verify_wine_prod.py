"""酒的问话·生产验收脚本(服务器侧执行)

验证四问话全链 + 合规提示 + 指令面/音色面零回归。
"""
import json
import urllib.request

BASE = "http://localhost:8000"
PASS = 0
FAIL = 0


def req(method, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-Member-Id"] = "1"
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(
        BASE + path, data=data, headers=headers,
        method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            print(f"  [HTTP {e.code}] {path} "
                  f"{e.read().decode()[:200]}")
        except Exception:  # noqa: BLE001
            pass
        raise


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def main():
    # 0. 登录(生产 strict——全部走鉴权)
    tok = req("POST", "/api/auth/login", body={
        "phone": "13800000001",
        "password": "test123456"})
    token = tok.get("accessToken") \
        or (tok.get("data") or {}).get("accessToken")

    # 1. 指令面: 25 项含四问
    j = req("GET", "/api/xiaozhu/commands",
            token=token)
    actions = [c.get("action")
               for c in (j.get("commands") or [])]
    check("commands 25 项含酒问话四问",
          len(actions) == 25
          and all(a in actions for a in (
              "wine.verify", "wine.craft",
              "wine.recommend", "wine.reviews")),
          f"n={len(actions)}")

    # 2. 音色面零回归(6 档)
    j = req("GET", "/api/xiaozhu/voices",
            token=token)
    profiles = (j.get("voices")
                if isinstance(j, dict) else None)
    check("音色 6 档零回归",
          profiles is not None
          and len(profiles) == 6,
          f"n={len(profiles) if profiles else 0}")

    # 3. 开会话
    s = req("POST", "/api/xiaozhu/sessions",
            token=token, body={"channel": "text"})
    sid = (s.get("sessionId")
           or (s.get("session") or {}).get("sessionId"))

    def say(text):
        return req("POST",
                   f"/api/xiaozhu/sessions/{sid}/text",
                   token=token, body={"text": text})

    # 4. 四问话全链
    r = say("小竹，这瓶酒是真的吗")
    reply = str(r.get("reply") or "")
    check("P-A 真伪问→双报告典藏",
          "ZZ26SW1489303A" in reply
          and "ZZ26SW1489404B" in reply
          and (r.get("turn") or {}).get("intent")
          == "wine.verify",
          reply[:60])
    r = say("小竹，竹香酒是怎么酿出来的")
    reply = str(r.get("reply") or "")
    check("P-A 工艺故事→75号知识",
          len(reply) > 10
          and (r.get("turn") or {}).get("intent")
          == "wine.craft",
          reply[:60])
    r = say("小竹，商务宴请推荐一款")
    reply = str(r.get("reply") or "")
    check("P-B 场景推荐+故事+合规",
          "商务宴请场景" in reply
          and "「" in reply
          and "未成年人禁止饮酒" in reply
          and (r.get("turn") or {}).get("intent")
          == "wine.recommend",
          reply[:80])
    r = say("小竹，大家觉得这款酒怎么样")
    reply = str(r.get("reply") or "")
    check("P-D 评论精华",
          "口碑" in reply
          and (r.get("turn") or {}).get("intent")
          == "wine.reviews",
          reply[:60])

    # 5. 让位回归: 短属性问仍归属性轨
    r = say("小竹，这款酒什么工艺")
    check("让位: 短属性问归属性轨",
          (r.get("turn") or {}).get("intent")
          != "wine.craft",
          str((r.get("turn") or {}).get("intent")))

    # 6. 清理
    req("DELETE", f"/api/xiaozhu/sessions/{sid}",
        token=token)
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
