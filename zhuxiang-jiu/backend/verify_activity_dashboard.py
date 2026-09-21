"""活动中心管理后台·本地全流程验证(需先起服: uvicorn main:app --port 8010)

流程: 创建→编辑→审核发布→报名→统计→开始→结束 + 抽奖链(奖品池→抽奖→发货)
注意: 默认 8000 端口可能被 Docker 旧容器占用, 故本脚本用 8010。
"""
import json
import urllib.request

BASE = "http://localhost:8010"
ADMIN = {"X-Role": "admin", "Content-Type": "application/json"}
USER = {"X-Member-Id": "101", "Content-Type": "application/json"}


def call(method, path, body=None, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers=headers or ADMIN)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def main():
    ok = []

    # ===== 促销链 =====
    st, c = call("POST", "/api/activity/admin/create", {
        "name": "竹香开酿节·满减促销", "type": "promotion",
        "description": "满300减30", "startTime": "2026-10-01T00:00",
        "endTime": "2026-10-07T23:59", "budget": 5000,
        "rules": {"满减": "满300减30"}})
    aid = c["data"]["id"]
    ok.append(("1 创建草稿", st == 200 and c["data"]["status"] == "draft"))

    st, u = call("PUT", f"/api/activity/admin/update/{aid}", {
        "name": "竹香开酿节·满减促销(改)", "budget": 8000})
    ok.append(("2 编辑草稿", st == 200 and u["data"]["name"].endswith("(改)")
               and u["data"]["budget"] == 8000 and u["data"]["rules"]["满减"] == "满300减30"))

    st, a = call("POST", f"/api/activity/admin/audit/{aid}",
                 {"approve": True, "auditor": 0})
    ok.append(("3 审核发布→报名中", st == 200 and a["data"]["status"] == "registering"))

    st, r = call("POST", "/api/activity/register",
                 {"activityId": aid, "userId": 101}, USER)
    ok.append(("4 用户报名", st == 200 and r["data"]["status"] == "registered"))

    st, s = call("GET", f"/api/activity/stats/{aid}")
    st2, rl = call("GET", f"/api/activity/registrations/{aid}")
    ok.append(("5 统计+报名列表", s["data"]["registrationCount"] == 1
               and rl["count"] == 1 and rl["data"][0]["userId"] == 101))

    st, t1 = call("POST", f"/api/activity/admin/transition/{aid}",
                  {"targetStatus": "ongoing"})
    st, t2 = call("POST", f"/api/activity/admin/transition/{aid}",
                  {"targetStatus": "ended"})
    ok.append(("6 开始→结束", t1["data"]["statusAfter"] == "ongoing"
               and t2["data"]["statusAfter"] == "ended"))

    # 编辑已结束活动应 409
    st, _ = call("PUT", f"/api/activity/admin/update/{aid}", {"name": "x"})
    ok.append(("7 非草稿编辑拒绝409", st == 409))

    # ===== 抽奖链 =====
    st, c2 = call("POST", "/api/activity/admin/create", {
        "name": "中秋大转盘", "type": "lottery", "budget": 2000})
    lid = c2["data"]["id"]
    call("POST", f"/api/activity/admin/audit/{lid}", {"approve": True})
    call("POST", f"/api/activity/admin/transition/{lid}", {"targetStatus": "ongoing"})

    st, p = call("POST", f"/api/activity/admin/prizes/{lid}", {"prizes": [
        {"prizeName": "竹香小酒", "prizeType": "product", "prizeValue": 88,
         "probability": 100, "dailyLimit": 0, "totalLimit": 3},
        {"prizeName": "谢谢参与", "prizeType": "coupon", "prizeValue": 0,
         "probability": 0, "dailyLimit": 0, "totalLimit": 100}]})
    ok.append(("8 配置奖品池", st == 200 and p["data"]["totalProbability"] == 100))

    st, d = call("POST", "/api/activity/lottery/draw",
                 {"activityId": lid, "userId": 101}, USER)
    record_no = d["data"].get("recordNo", "")
    ok.append(("9 抽奖必中实物", st == 200 and d["data"]["won"]
               and d["data"]["prizeType"] == "product"
               and d["data"]["status"] == "pending"))

    st, pr = call("GET", "/api/activity/admin/prize-records?status=pending")
    ok.append(("10 发奖记录列表(pending)", st == 200
               and any(x["recordNo"] == record_no for x in pr["data"])))

    st, dv = call("POST", f"/api/activity/admin/prize/{record_no}/deliver",
                  {"waybillNo": "SF2026092101"})
    ok.append(("11 发货登记", st == 200 and dv["data"]["status"] == "shipped"
               and dv["data"]["waybillNo"] == "SF2026092101"))

    st, cf = call("POST", f"/api/activity/prize/{record_no}/confirm", None, USER)
    ok.append(("12 签收确认", st == 200 and cf["data"]["status"] == "signed"))

    # 管理列表可见全部
    st, al = call("GET", "/api/activity/admin/list?limit=100")
    ok.append(("13 管理列表含两类活动", st == 200
               and {aid, lid}.issubset({x["id"] for x in al["data"]})))

    print("\n".join(f"  [{'PASS' if c else 'FAIL'}] {n}" for n, c in ok))
    print("-" * 50)
    print(f"通过 {sum(1 for _, c in ok if c)} / {len(ok)}")
    return 0 if all(c for _, c in ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
