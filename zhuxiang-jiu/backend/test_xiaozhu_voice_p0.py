"""48号·小竹语音精灵 P0 语音链路页面契约测试
(生产 strict 鉴权面: xiaozhu-voice.html Bearer → 会话全链)

运行方式:
    python test_xiaozhu_voice_p0.py

覆盖(xiaozhu-voice.html 页面契约——与既有 test_xiaozhu_p0
的 compat 裸头面互补, 本文件走生产同款 strict + Bearer):
    - strict 面鉴权: 裸 X-Member-Id → 401 / Bearer 放行(注入身份)
    - POST /sessions channel=voice(页面开启会话)
    - POST /sessions/{id}/text 唤醒轮(小竹，看新品) → reply/card/jump
    - 免唤醒窗口: 追问无需再唤醒(页面唤醒状态 pill 数据源)
    - POST /sessions/{id}/voice 无 key 降级 → fallbackHint=keyboard
    - GET /commands(指令集+唤醒词+窗口——chips 数据源)
    - GET /sessions/{id} 轮次留痕 + PII 脱敏(页面隐私红线)
    - POST /confirm 契约: 非法 code 409 / 未知 token 404
    - DELETE 清除级联(页面「清除记录」)
    - GET /context 角色上下文(页面等级/绑定 pills 数据源)
"""

import base64
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "strict"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

PASS = 0
FAIL = 0


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  \u2713 {name}")
    else:
        FAIL += 1
        print(f"  \u2717 {name} \u2014 {detail}")


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


def main():
    reset_all()
    from fastapi.testclient import TestClient

    from main import app
    client = TestClient(app)

    # ===== strict 面鉴权 =====
    print("[01 strict 鉴权面]")
    r = client.post("/api/xiaozhu/sessions",
                    json={"channel": "voice"},
                    headers={"X-Member-Id": "1"})
    record("裸 X-Member-Id → 401(strict)",
           r.status_code == 401, str(r.status_code))

    # 页面同款 JWT 登录(member1)
    r = client.post("/api/auth/login",
                    json={"phone": "13800000001",
                          "password": "test123456"})
    token = (r.json() or {}).get("accessToken")
    record("页面登录 /api/auth/login",
           r.status_code == 200 and bool(token),
           r.text[:120])
    if not token:
        print("-" * 64)
        print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
        return 1
    h = {"Authorization": f"Bearer {token}"}

    # ===== 会话链(页面契约) =====
    print("[02 开启会话]")
    r = client.post("/api/xiaozhu/sessions",
                    json={"channel": "voice"}, headers=h)
    body = r.json()
    record("POST sessions 200(Bearer 注入身份)",
           r.status_code == 200 and body.get("success") is True
           and body.get("memberId") == 1
           and body.get("channel") == "voice",
           f"{r.status_code}|{body.get('memberId')}")
    record("assist 档留痕 xiaoMode",
           body.get("xiaoMode") == "assist",
           str(body.get("xiaoMode")))
    sid = body.get("sessionId")

    print("[03 唤醒轮 + 免唤醒窗口]")
    r = client.post(f"/api/xiaozhu/sessions/{sid}/text",
                    json={"text": "小竹，看新品"}, headers=h)
    body = r.json()
    record("唤醒轮 reply+card+jump",
           r.status_code == 200 and body.get("reply")
           and (body.get("card") or {}).get("type")
           == "product_list"
           and body.get("jump") == "/#/pages/products/index?sort=new",
           f"{r.status_code}|{str(body)[:100]}")
    record("轮次 turn.wake 留痕",
           (body.get("turn") or {}).get("wake") is True)

    r = client.post(f"/api/xiaozhu/sessions/{sid}/text",
                    json={"text": "有什么优惠"}, headers=h)
    body = r.json()
    record("免唤醒追问直接执行",
           r.status_code == 200 and body.get("reply")
           and not body.get("wakeHint"),
           f"{r.status_code}|{str(body.get('wakeHint'))}")

    print("[04 云端语音降级(LLM off)]")
    r = client.post(
        f"/api/xiaozhu/sessions/{sid}/voice",
        json={"audioBase64": base64.b64encode(
            b"fake-audio").decode(),
            "filename": "audio.wav", "durationSec": 1.2},
        headers=h)
    body = r.json()
    record("voice 无 key 降级 fallbackHint=keyboard",
           r.status_code == 200
           and body.get("fallbackHint") == "keyboard",
           f"{r.status_code}|{str(body.get('fallbackHint'))}")

    print("[05 指令集 + 角色上下文]")
    r = client.get("/api/xiaozhu/commands", headers=h)
    body = r.json()
    record("GET commands(26 指令+唤醒词+窗口)",
           r.status_code == 200
           and len(body.get("commands") or []) == 26
           and body.get("wakeWords") == ["小竹"]
           and body.get("wakeFreeWindowSeconds") == 300,
           f"{r.status_code}|{len(body.get('commands') or [])}")
    r = client.get("/api/xiaozhu/context", headers=h)
    body = r.json()
    record("GET context(等级/绑定 pills)",
           r.status_code == 200 and body.get("success") is True
           and body.get("memberId") == 1
           and isinstance(body.get("bound"), bool)
           and body.get("level") in (1, 2, 3, 4, 5),
           f"{r.status_code}|{str(body)[:100]}")

    print("[06 PII 脱敏留痕]")
    r = client.post(f"/api/xiaozhu/sessions/{sid}/text",
                    json={"text": "小竹，13812345678 查优惠"},
                    headers=h)
    record("含手机号指令 200", r.status_code == 200,
           str(r.status_code))
    r = client.get(f"/api/xiaozhu/sessions/{sid}", headers=h)
    turns = (r.json() or {}).get("turns") or []
    masked = [t for t in turns
              if "*手机号*" in (t.get("rawText") or "")]
    record("GET session 轮次留痕+PII 脱敏",
           r.status_code == 200 and len(turns) >= 4
           and len(masked) >= 1,
           f"{r.status_code}|turns={len(turns)}|"
           f"masked={len(masked)}")

    print("[07 高敏确认契约]")
    r = client.post("/api/xiaozhu/confirm/nonexistent-token",
                    json={"code": "12a4"}, headers=h)
    record("非法 code(非 4 位数字) → 409",
           r.status_code == 409, str(r.status_code))
    r = client.post("/api/xiaozhu/confirm/nonexistent-token",
                    json={"code": "1234"}, headers=h)
    record("未知 token → 404", r.status_code == 404,
           str(r.status_code))

    print("[08 清除级联(隐私红线)]")
    r = client.delete(f"/api/xiaozhu/sessions/{sid}", headers=h)
    body = r.json()
    record("DELETE session 级联",
           r.status_code == 200
           and body.get("removedRecords") >= 5,
           f"{r.status_code}|{body.get('removedRecords')}")
    r = client.get(f"/api/xiaozhu/sessions/{sid}", headers=h)
    record("清除后 404", r.status_code == 404,
           str(r.status_code))

    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
