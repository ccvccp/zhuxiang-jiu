"""活动中心 C端·生产 E2E + 上线种子活动(服务器上运行, 直连容器 127.0.0.1:8000)

链路: 管理员 JWT → 创建双节抽奖+开酿节促销 → 审核发布/奖品池 →
      会员 JWT → 报名/我的报名(修复验证) → 冒名 403 → 游客公开面
"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def call(method, path, body=None, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def main():
    ok = []

    def rec(name, cond):
        ok.append((name, bool(cond)))

    # ===== 管理员登录(种子 member3) =====
    st, adm = call("POST", "/api/auth/login",
                   {"phone": "13800000002", "password": "test123456"},
                   {"Content-Type": "application/json"})
    ADMIN = {"Authorization": "Bearer " + adm.get("accessToken", ""),
             "Content-Type": "application/json"}
    rec("0 管理员JWT登录", st == 200 and adm.get("accessToken"))

    # ===== 上线种子活动 =====
    # 1) 双节大转盘(抽奖, 进行中)
    st, c1 = call("POST", "/api/activity/admin/create", {
        "name": "双节大转盘·赢竹香好礼", "type": "lottery",
        "description": "中秋遇国庆, 每日3次免费抽奖——竹香小酒/信值积分/满减券, 100% 中奖率。",
        "startTime": "2026-09-20T00:00", "endTime": "2026-10-08T23:59",
        "budget": 5000}, ADMIN)
    lid = c1["data"]["id"]
    call("POST", f"/api/activity/admin/audit/{lid}",
         {"approve": True, "auditor": 0}, ADMIN)
    call("POST", f"/api/activity/admin/transition/{lid}",
         {"targetStatus": "ongoing"}, ADMIN)
    st, p = call("POST", f"/api/activity/admin/prizes/{lid}", {"prizes": [
        {"prizeName": "竹香小酒(100ml)", "prizeType": "product",
         "prizeValue": 88, "probability": 20, "dailyLimit": 0, "totalLimit": 10},
        {"prizeName": "信值积分50", "prizeType": "points",
         "prizeValue": 50, "probability": 40, "dailyLimit": 0, "totalLimit": 200},
        {"prizeName": "满减券¥10", "prizeType": "coupon",
         "prizeValue": 10, "probability": 40, "dailyLimit": 0, "totalLimit": 200}]}, ADMIN)
    rec("1a 双节抽奖(进行中+奖品池)", st == 200 and p["data"]["prizeCount"] == 3)

    # 2) 开酿节促销(报名中)
    st, c2 = call("POST", "/api/activity/admin/create", {
        "name": "竹香开酿节·满300减30", "type": "promotion",
        "description": "开酿节大促——全场满300减30, 上不封顶; 报名会员额外享双倍信值积分。",
        "startTime": "2026-09-25T00:00", "endTime": "2026-10-08T23:59",
        "budget": 8000, "rules": {"满减": "满300减30"}}, ADMIN)
    pid = c2["data"]["id"]
    st, a2 = call("POST", f"/api/activity/admin/audit/{pid}",
                  {"approve": True, "auditor": 0}, ADMIN)
    rec("1b 开酿节促销(报名中)", st == 200
        and a2["data"]["status"] == "registering")

    # ===== 会员链(种子 member1) =====
    st, m = call("POST", "/api/auth/login",
                 {"phone": "13800000001", "password": "test123456"},
                 {"Content-Type": "application/json"})
    USER = {"Authorization": "Bearer " + m.get("accessToken", ""),
            "Content-Type": "application/json"}
    mid = m.get("memberId")
    rec("2 会员JWT登录", st == 200 and mid is not None)

    # 游客公开面(无凭证)
    st, lst = call("GET", "/api/activity/list?limit=50")
    rec("3a 游客列表>=2", st == 200 and lst["count"] >= 2)
    st, det = call("GET", f"/api/activity/{pid}")
    rec("3b 游客详情", st == 200 and det["data"]["name"].startswith("竹香开酿节"))
    st, pp = call("GET", f"/api/activity/lottery/{lid}/prizes")
    rec("3c 游客奖品池公示", st == 200 and pp["count"] == 3)

    # 报名 + 我的报名(修复验证: int 转换后 Redis 键可查)
    st, rg = call("POST", "/api/activity/register",
                  {"activityId": pid, "userId": mid}, USER)
    rec("4a 会员报名", st == 200 and rg["data"]["status"] == "registered")

    st, my = call("GET", "/api/activity/my-registrations", None, USER)
    rec("4b 我的报名可见(int修复)", st == 200 and any(
        r["activityId"] == pid for r in my["data"]))

    # 冒名 403(本次加固)
    st, _ = call("POST", "/api/activity/register",
                 {"activityId": pid, "userId": mid + 999}, USER)
    rec("4c 冒名报名403", st == 403)

    # 抽奖
    st, d = call("POST", "/api/activity/lottery/draw",
                 {"activityId": lid, "userId": mid}, USER)
    rec("5 生产抽奖", st == 200 and "drawsRemainingToday" in d.get("data", {}))

    st, mine = call("GET", "/api/activity/prizes/mine", None, USER)
    rec("6 我的奖品", st == 200 and mine["data"]["total"] >= 1)

    print("=" * 60)
    print("活动中心 C端·生产 E2E")
    print("=" * 60)
    failed = 0
    for name, passed in ok:
        mark = "PASS" if passed else "FAIL"
        if not passed:
            failed += 1
        print(f"  [{mark}] {name}")
    print("-" * 60)
    print(f"通过 {len(ok) - failed} / {len(ok)}")
    print(f"种子活动: lottery={lid} promotion={pid} member={mid}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
