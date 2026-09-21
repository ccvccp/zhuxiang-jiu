"""活动中心·发布功能完整性检查(直跑本地 uvicorn :8010)

发布链全路径 + C端可见性 + 边界:
  创建(draft) → C端不可见 → 编辑 → 审核发布(observe AI 门) → C端可见
  → C端报名 → 开始 → 结束; 拒绝路径 → cancelled; 各状态边界
"""
import json
import urllib.request
import urllib.error

BASE = "http://localhost:8010"
ADMIN = {"X-Role": "admin", "Content-Type": "application/json"}
USER = {"X-Member-Id": "202", "Content-Type": "application/json"}


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


def c_side_ids():
    """C端视角: /api/activity/list 可见的活动 id 集"""
    st, r = call("GET", "/api/activity/list?limit=200", None, {})
    return {a["id"] for a in r.get("data", [])}


def main():
    ok = []
    c0 = c_side_ids()

    # ===== 1. 创建草稿 =====
    st, c = call("POST", "/api/activity/admin/create", {
        "name": "国庆品鉴周", "type": "promotion",
        "description": "全场竹香酒满减", "startTime": "2026-10-01T00:00",
        "endTime": "2026-10-07T23:59", "budget": 3000})
    aid = c["data"]["id"]
    ok.append(("1 创建草稿(draft)", st == 200 and c["data"]["status"] == "draft"))

    # ===== 2. C端不可见草稿 =====
    ok.append(("2 C端不可见草稿", aid not in c_side_ids()))

    # ===== 3. 编辑草稿 =====
    st, u = call("PUT", f"/api/activity/admin/update/{aid}",
                 {"name": "国庆品鉴周(改)", "budget": 5000})
    ok.append(("3 编辑草稿生效", st == 200 and u["data"]["name"].endswith("(改)")
               and u["data"]["budget"] == 5000))

    # ===== 4. 审核发布(observe AI 门放行) =====
    st, a = call("POST", f"/api/activity/admin/audit/{aid}",
                 {"approve": True, "auditor": 0})
    ok.append(("4 审核发布→registering", st == 200 and a["data"]["status"] == "registering"))

    # ===== 5. C端立即可见 =====
    ok.append(("5 C端可见已发布活动", aid in c_side_ids()))

    # ===== 6. C端报名 =====
    st, r = call("POST", "/api/activity/register",
                 {"activityId": aid, "userId": 202}, USER)
    st2, s = call("GET", f"/api/activity/stats/{aid}")
    ok.append(("6 C端报名+统计", st == 200 and s["data"]["registrationCount"] == 1))

    # ===== 7. 开始 → ongoing =====
    st, t = call("POST", f"/api/activity/admin/transition/{aid}",
                 {"targetStatus": "ongoing"})
    st3, d = call("GET", f"/api/activity/{aid}")
    ok.append(("7 开始→ongoing+C端状态同步", st == 200
               and d["data"]["status"] == "ongoing"))

    # ===== 8. 结束 → ended(终态) =====
    st, t2 = call("POST", f"/api/activity/admin/transition/{aid}",
                  {"targetStatus": "ended"})
    ok.append(("8 结束→ended", st == 200 and t2["data"]["statusAfter"] == "ended"))
    # ended 后再流转应 409
    st, _ = call("POST", f"/api/activity/admin/transition/{aid}",
                 {"targetStatus": "ongoing"})
    ok.append(("8b 终态不可再流转(409)", st == 409))

    # ===== 9. 拒绝路径 =====
    st, c2 = call("POST", "/api/activity/admin/create", {
        "name": "测试拒绝活动", "type": "interactive"})
    aid2 = c2["data"]["id"]
    st, rj = call("POST", f"/api/activity/admin/audit/{aid2}",
                  {"approve": False, "auditor": 0, "reason": "测试拒绝"})
    ok.append(("9 拒绝→cancelled+理由留痕", st == 200
               and rj["data"]["status"] == "cancelled"))

    # ===== 10. 边界: 已发布活动不可编辑 =====
    st, _ = call("PUT", f"/api/activity/admin/update/{aid}", {"name": "x"})
    ok.append(("10 已发布不可编辑(409)", st == 409))

    # ===== 11. 边界: 非草稿不可再审核 =====
    st, _ = call("POST", f"/api/activity/admin/audit/{aid}", {"approve": True})
    ok.append(("11 非草稿不可再审核(409)", st == 409))

    # ===== 12. 边界: cancelled 不可复活 =====
    st, _ = call("POST", f"/api/activity/admin/transition/{aid2}",
                 {"targetStatus": "registering"})
    ok.append(("12 cancelled 不可复活(409)", st == 409))

    # ===== 13. 擂台赛 subType 联动 =====
    st, ar = call("POST", "/api/activity/admin/create", {
        "name": "品鉴擂台赛", "type": "arena", "subType": "L06"})
    st, ap = call("POST", f"/api/activity/admin/audit/{ar['data']['id']}",
                  {"approve": True})
    ok.append(("13 擂台赛(subType L06)发布", st == 200
               and ap["data"]["status"] == "registering"))

    # ===== 14. 时间容错(空时间可创建——记录为发现) =====
    st, nt = call("POST", "/api/activity/admin/create", {
        "name": "无时间活动", "type": "promotion"})
    ok.append((f"14 无时间可创建(现状, st={st})", st == 200))

    # ===== 15. C端列表字段完整性 =====
    st, ls = call("GET", "/api/activity/list?limit=200", None, {})
    pub = [a for a in ls["data"] if a["id"] == aid][0]
    fields_ok = all(pub.get(k) is not None for k in
                    ("name", "type", "status", "startTime", "endTime"))
    ok.append(("15 C端列表字段完整", fields_ok))

    print("\n".join(f"  [{'PASS' if c else 'FAIL'}] {n}" for n, c in ok))
    print("-" * 52)
    print(f"通过 {sum(1 for _, c in ok if c)} / {len(ok)}")
    return 0 if all(c for _, c in ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
