"""活动中心 C端(列表/详情页)·本地全流程验证(需先起服: uvicorn main:app --port 8010)

覆盖 C端前端(activity.html / activity-detail.html)依赖的全部接口链:
    1. 游客公开面(无任何凭证): list / detail / 奖品池公示 / 擂台榜
    2. 会员 JWT 链: login → register → my-registrations → cancel → re-register
    3. 本人校验(本次新增): header 与 body.userId 不一致 → 403
    4. 抽奖闭环: 抽奖 → 我的奖品 → 签收确认
    5. 擂台赛: 提交分数 → 排行榜
种子: 预置 registering 促销 + ongoing 抽奖 + ongoing 擂台赛(供浏览器联调)
"""
import json
import urllib.request

BASE = "http://localhost:8010"
ADMIN = {"X-Role": "admin", "Content-Type": "application/json"}
GUEST = {}


def call(method, path, body=None, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers=headers or ADMIN)
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

    # ===== 0. 会员登录(种子 13800000001 / test123456) =====
    st, login = call("POST", "/api/auth/login",
                     {"phone": "13800000001", "password": "test123456"},
                     {"Content-Type": "application/json"})
    if st != 200 or not login.get("accessToken"):
        # 尝试常见种子密码兜底
        for pwd in ("Passw0rd!", "123456", "password"):
            st, login = call("POST", "/api/auth/login",
                             {"phone": "13800000001", "password": pwd},
                             {"Content-Type": "application/json"})
            if st == 200 and login.get("accessToken"):
                break
    token = login.get("accessToken", "")
    member_id = login.get("memberId")
    USER = {"Authorization": f"Bearer {token}",
            "Content-Type": "application/json"}
    rec("0 会员JWT登录", st == 200 and token and member_id is not None)

    # ===== 1. 种子活动 =====
    # 1a. registering 促销(报名面)
    st, c1 = call("POST", "/api/activity/admin/create", {
        "name": "竹香开酿节·满300减30", "type": "promotion",
        "description": "开酿节大促——全场满300减30, 上不封顶。",
        "startTime": "2026-09-20T00:00", "endTime": "2026-10-07T23:59",
        "budget": 8000, "rules": {"满减": "满300减30"}})
    promo_id = c1["data"]["id"]
    call("POST", f"/api/activity/admin/audit/{promo_id}",
         {"approve": True, "auditor": 0})
    rec("1a 种子促销(报名中)", st == 200)

    # 1b. ongoing 抽奖(奖品池+抽奖面)
    st, c2 = call("POST", "/api/activity/admin/create", {
        "name": "中秋大转盘·赢竹香小酒", "type": "lottery",
        "description": "每日3次免费抽奖——竹香小酒/积分/优惠券。",
        "startTime": "2026-09-20T00:00", "endTime": "2026-10-07T23:59",
        "budget": 2000})
    lot_id = c2["data"]["id"]
    call("POST", f"/api/activity/admin/audit/{lot_id}", {"approve": True})
    call("POST", f"/api/activity/admin/transition/{lot_id}",
         {"targetStatus": "ongoing"})
    st, p = call("POST", f"/api/activity/admin/prizes/{lot_id}", {"prizes": [
        {"prizeName": "竹香小酒(100ml)", "prizeType": "product",
         "prizeValue": 88, "probability": 30, "dailyLimit": 0, "totalLimit": 3},
        {"prizeName": "信值积分50", "prizeType": "points",
         "prizeValue": 50, "probability": 30, "dailyLimit": 0, "totalLimit": 100},
        {"prizeName": "满减券¥10", "prizeType": "coupon",
         "prizeValue": 10, "probability": 40, "dailyLimit": 0, "totalLimit": 100}]})
    rec("1b 种子抽奖+奖品池", st == 200 and p["data"]["prizeCount"] == 3)

    # 1c. ongoing 擂台赛(排行榜面)
    st, c3 = call("POST", "/api/activity/admin/create", {
        "name": "品鉴擂台赛·L06", "type": "arena", "subType": "L06",
        "description": "竹香酒品鉴擂台——上传品鉴笔记, 由评审打分排名。",
        "startTime": "2026-09-20T00:00", "endTime": "2026-10-07T23:59",
        "budget": 3000})
    arena_id = c3["data"]["id"]
    call("POST", f"/api/activity/admin/audit/{arena_id}", {"approve": True})
    call("POST", f"/api/activity/admin/transition/{arena_id}",
         {"targetStatus": "ongoing"})
    rec("1c 种子擂台赛", st == 200)

    # ===== 2. 游客公开面(无凭证) =====
    st, lst = call("GET", "/api/activity/list?limit=100", None, GUEST)
    rec("2a 游客列表", st == 200 and lst["count"] >= 3
        and all(a["status"] != "draft" for a in lst["data"]))

    st, det = call("GET", f"/api/activity/{promo_id}", None, GUEST)
    rec("2b 游客详情", st == 200 and det["data"]["name"].startswith("竹香开酿节"))

    st, pp = call("GET", f"/api/activity/lottery/{lot_id}/prizes", None, GUEST)
    rec("2c 游客奖品池公示", st == 200 and pp["count"] == 3
        and abs(sum(x["probability"] for x in pp["data"]) - 100) < 0.01)

    st, _ = call("GET", f"/api/activity/leaderboard/{arena_id}", None, GUEST)
    rec("2d 游客擂台榜", st == 200)

    # 会员私有面游客 401(compat 下无头也 401——路由层要求)
    st, _ = call("GET", "/api/activity/my-registrations", None, GUEST)
    rec("2e 游客我的报名401", st == 401)
    st, _ = call("GET", "/api/activity/prizes/mine", None, GUEST)
    rec("2f 游客我的奖品401", st == 401)

    # ===== 3. 报名链(JWT) =====
    st, rg = call("POST", "/api/activity/register",
                  {"activityId": promo_id, "userId": member_id}, USER)
    rec("3a 会员报名", st == 200 and rg["data"]["status"] == "registered")

    # 本人校验(本次新增): body.userId 与 JWT 会员不一致 → 403
    st, _ = call("POST", "/api/activity/register",
                 {"activityId": promo_id, "userId": member_id + 999}, USER)
    rec("3b 冒名报名403", st == 403)

    st, my = call("GET", "/api/activity/my-registrations", None, USER)
    rec("3c 我的报名可见", st == 200 and any(
        r["activityId"] == promo_id for r in my["data"]))

    st, cx = call("POST", "/api/activity/cancel",
                  {"activityId": promo_id, "userId": member_id}, USER)
    rec("3d 取消报名", st == 200)

    # 本人校验: 取消冒名 → 403
    st, _ = call("POST", "/api/activity/cancel",
                 {"activityId": promo_id, "userId": member_id + 999}, USER)
    rec("3e 冒名取消403", st == 403)

    st, rg2 = call("POST", "/api/activity/register",
                   {"activityId": promo_id, "userId": member_id}, USER)
    rec("3f 重新报名", st == 200 and rg2["data"]["status"] == "registered")

    # ===== 4. 抽奖闭环(JWT) =====
    won_record = None
    for i in range(3):
        st, d = call("POST", "/api/activity/lottery/draw",
                     {"activityId": lot_id, "userId": member_id}, USER)
        if st != 200:
            rec(f"4a 抽奖第{i+1}次", False)
            break
        if d["data"].get("won"):
            won_record = d["data"].get("recordNo", "")
    rec("4a 抽奖(≤3次/日)", st == 200)

    # 本人校验: 冒名抽奖 → 403(路由先于业务校验)
    st, _ = call("POST", "/api/activity/lottery/draw",
                 {"activityId": lot_id, "userId": member_id + 999}, USER)
    rec("4b 冒名抽奖403", st == 403)

    st, mine = call("GET", "/api/activity/prizes/mine", None, USER)
    has_prize = mine.get("data", {}).get("total", 0) > 0
    rec("4c 我的奖品", st == 200 and (has_prize or won_record is None))

    # 第4次抽奖 → 409(次数用尽)——仅当前3次全成功时
    if st == 200:
        st4, d4 = call("POST", "/api/activity/lottery/draw",
                       {"activityId": lot_id, "userId": member_id}, USER)
        rec("4d 次数用尽409", st4 == 409)

    # ===== 5. 擂台赛分数+榜单 =====
    st, sc = call("POST", "/api/activity/arena/score",
                  {"activityId": arena_id, "userId": member_id,
                   "score": 88.5, "realName": "竹香品鉴官"}, USER)
    rec("5a 提交擂台分数", st == 200 and sc["data"]["rank"] == 1)

    st, lb = call("GET", f"/api/activity/leaderboard/{arena_id}", None, GUEST)
    rec("5b 擂台榜展示", st == 200 and lb["count"] == 1
        and lb["data"][0]["score"] == 88.5)

    # ===== 结果 =====
    print("=" * 60)
    print("活动中心 C端全流程验证")
    print("=" * 60)
    failed = 0
    for name, passed in ok:
        mark = "PASS" if passed else "FAIL"
        if not passed:
            failed += 1
        print(f"  [{mark}] {name}")
    print("-" * 60)
    print(f"通过 {len(ok) - failed} / {len(ok)}")
    if failed:
        raise SystemExit(1)
    print(f"种子活动: promo={promo_id} lottery={lot_id} "
          f"arena={arena_id} member={member_id}")


if __name__ == "__main__":
    main()
