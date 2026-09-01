"""信息管理模块 HTTP 层鉴权测试(v2 安全加固: message/list 公开越权 + IDOR 修复)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_message_auth.py

覆盖(TD-4 最后一个代码侧遗留项验收):
    1. GET /api/message/list: 无头 401 / 会员查他人 403 / 查自己 200 / admin 查任意 200
    2. GET /api/message/stats: 会员查自己 / 查他人 403 / admin 全局
    3. POST /api/message/mark-all-read: body userId 与头不一致 403 / 一致 200 / admin 任意
    4. GET /api/message/{id}: 他人消息 403 / 自己 200 / admin 任意
    5. POST /api/message/mark-read/{id}: 他人消息 403 / 自己 200
    6. POST /api/message/send: 会员 403 / admin 200(收紧防滥发)
"""
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from fastapi.testclient import TestClient

from main import app
from repositories.store import _mock_store

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


M = {"X-Member-Id": "1001"}          # 归属会员
OTHER = {"X-Member-Id": "1002"}       # 非归属会员
ADMIN = {"X-Role": "admin"}


def run(client):
    global PASS, FAIL
    for k in list(_mock_store.keys()):
        if "message" in k:
            del _mock_store[k]

    # ---- 造数: 给 1001 和 1002 各发一条站内信(admin 通道) ----
    r = client.post("/api/message/send", headers=ADMIN, json={
        "userId": 1001, "channel": "inmail",
        "title": "会员消息", "content": "1001 的消息", "category": "system"})
    check("造数: 1001 消息", r.status_code == 200
          and r.json().get("success") is True, f"{r.status_code} {r.text[:150]}")
    r = client.post("/api/message/send", headers=ADMIN, json={
        "userId": 1002, "channel": "inmail",
        "title": "他人消息", "content": "1002 的消息", "category": "system"})
    check("造数: 1002 消息", r.status_code == 200)

    # ============================================================
    # 1. GET /api/message/list(核心越权修复: 原公开端点)
    # ============================================================
    r = client.get("/api/message/list?user_id=1001")
    check("列表: 无头 401(原公开)", r.status_code == 401, f"got {r.status_code}")
    r = client.get("/api/message/list?user_id=1001", headers=OTHER)
    check("列表: 查他人 403", r.status_code == 403, f"got {r.status_code}")
    r = client.get("/api/message/list?user_id=1001", headers=M)
    body = r.json()
    check("列表: 查自己 200", r.status_code == 200 and body.get("count", 0) == 1,
          f"{r.status_code} {r.text[:150]}")
    check("列表: 只含自己消息", all(m.get("userId") == 1001
                                    for m in body.get("data", [])))
    r = client.get("/api/message/list?user_id=1002", headers=ADMIN)
    check("列表: admin 查他人 200", r.status_code == 200
          and r.json().get("count", 0) == 1)
    r = client.get("/api/message/list?user_id=1001", headers=ADMIN)
    check("列表: admin 查任意 200", r.status_code == 200)

    # 1001 的消息 ID(后续单条操作用)
    own_msg = body["data"][0]
    msg_id = own_msg["id"]
    other_msg_id = client.get("/api/message/list?user_id=1002",
                              headers=ADMIN).json()["data"][0]["id"]

    # ============================================================
    # 2. GET /api/message/stats
    # ============================================================
    r = client.get("/api/message/stats")
    check("统计: 无头 401", r.status_code == 401, f"got {r.status_code}")
    r = client.get("/api/message/stats?user_id=1002", headers=M)
    check("统计: 查他人 403", r.status_code == 403, f"got {r.status_code}")
    r = client.get("/api/message/stats", headers=M)
    check("统计: 会员默认查自己 200", r.status_code == 200
          and r.json().get("success") is True)
    r = client.get("/api/message/stats?user_id=1002", headers=ADMIN)
    check("统计: admin 查他人 200", r.status_code == 200)
    r = client.get("/api/message/stats", headers=ADMIN)
    check("统计: admin 全局 200", r.status_code == 200)

    # ============================================================
    # 3. POST /api/message/mark-all-read
    # ============================================================
    r = client.post("/api/message/mark-all-read",
                    headers=OTHER, json={"userId": 1001})
    check("批量已读: 代他人 403", r.status_code == 403, f"got {r.status_code}")
    r = client.post("/api/message/mark-all-read",
                    headers=M, json={"userId": 1001})
    check("批量已读: 自己 200", r.status_code == 200,
          f"{r.status_code} {r.text[:150]}")
    # 1002 的消息再补一条未读供后续测试
    client.post("/api/message/send", headers=ADMIN, json={
        "userId": 1002, "channel": "inmail",
        "title": "补充", "content": "1002 未读", "category": "system"})
    r = client.post("/api/message/mark-all-read",
                    headers=ADMIN, json={"userId": 1002})
    check("批量已读: admin 代任意 200", r.status_code == 200)

    # ============================================================
    # 4. GET /api/message/{id}(IDOR: 枚举消息 ID 读他人消息)
    # ============================================================
    r = client.get(f"/api/message/{other_msg_id}", headers=M)
    check("详情: 他人消息 403", r.status_code == 403, f"got {r.status_code}")
    r = client.get(f"/api/message/{msg_id}", headers=M)
    check("详情: 自己消息 200", r.status_code == 200
          and r.json()["data"]["userId"] == 1001)
    r = client.get(f"/api/message/{other_msg_id}", headers=ADMIN)
    check("详情: admin 任意 200", r.status_code == 200)
    r = client.get("/api/message/999999", headers=M)
    check("详情: 不存在 404", r.status_code == 404, f"got {r.status_code}")

    # ============================================================
    # 5. POST /api/message/mark-read/{id}
    # ============================================================
    # 造一条 1001 的新未读消息
    r = client.post("/api/message/send", headers=ADMIN, json={
        "userId": 1001, "channel": "popup",
        "title": "未读", "content": "待标记", "category": "coupon"})
    new_id = r.json()["data"]["id"] if isinstance(r.json().get("data"), dict) \
        else r.json()["data"][0]["id"] if isinstance(r.json().get("data"), list) \
        else None
    if new_id is None:  # 兜底: 从列表取最新
        items = client.get("/api/message/list?user_id=1001&status=unread",
                           headers=M).json()["data"]
        new_id = items[0]["id"]
    r = client.post(f"/api/message/mark-read/{new_id}", headers=OTHER)
    check("单条已读: 他人消息 403", r.status_code == 403, f"got {r.status_code}")
    r = client.post(f"/api/message/mark-read/{new_id}", headers=M)
    check("单条已读: 自己消息 200", r.status_code == 200,
          f"{r.status_code} {r.text[:150]}")
    r = client.post(f"/api/message/mark-read/{new_id}", headers=M)
    check("单条已读: 重复标记 409", r.status_code == 409)

    # ============================================================
    # 6. POST /api/message/send(收紧: 会员 403)
    # ============================================================
    r = client.post("/api/message/send", headers=M, json={
        "userId": 1002, "channel": "inmail",
        "title": "垃圾", "content": "会员滥发", "category": "system"})
    check("发送: 会员 403(防滥发)", r.status_code == 403, f"got {r.status_code}")
    r = client.post("/api/message/send", json={
        "userId": 1002, "channel": "inmail",
        "title": "匿名", "content": "无头发送", "category": "system"})
    check("发送: 无头 403", r.status_code == 403, f"got {r.status_code}")
    r = client.post("/api/message/send", headers=ADMIN, json={
        "userId": 1002, "channel": "inmail",
        "title": "正常", "content": "管理员发送", "category": "system"})
    check("发送: admin 200", r.status_code == 200)


def main():
    client = TestClient(app)
    run(client)
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
