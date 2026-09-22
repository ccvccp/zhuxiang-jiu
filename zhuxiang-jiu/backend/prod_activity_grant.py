"""生产 E2E: 活动后台授权链(权限节点种子同步 + 授权/签署/发布/吊销)

在容器内以 stdin 方式执行:
    docker exec -i zhuxiang-backend-1 python - < prod_activity_grant.py
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


def login(phone, pwd="test123456"):
    st, b = call("POST", "/api/auth/login", {"phone": phone, "password": pwd},
                 {"Content-Type": "application/json"})
    assert st == 200 and b.get("accessToken"), f"login fail {phone}: {b}"
    return {"Authorization": "Bearer " + b["accessToken"],
            "Content-Type": "application/json"}, b.get("memberId")


def main():
    ok = []

    def rec(name, cond):
        ok.append((name, bool(cond)))

    ADM, adm_id = login("13800000002")

    # ===== 1. 权限节点种子同步(activity 域 4 节点) =====
    st, nodes = call("GET", "/api/perm/nodes", None, ADM)
    stages = (nodes.get("stages") or {})
    act_nodes = stages.get("活动后台") or []
    codes = sorted(n.get("code") for n in act_nodes)
    rec("1a activity域4节点种子同步", st == 200 and codes == [
        "activity.approve", "activity.manage", "activity.operate",
        "activity.view"])
    rec("1b 域归属网站权限中心", st == 200 and all(
        n.get("center") == "site" for n in act_nodes))

    # ===== 2. 被授权人(注册/登录幂等) =====
    mgr_phone = "13900009001"
    call("POST", "/api/auth/register",
         {"phone": mgr_phone, "password": "test123456",
          "nickname": "活动运营官", "ageConfirmed": True},
         {"Content-Type": "application/json"})
    MGR, mgr_id = login(mgr_phone)
    rec("2 运营官登录", mgr_id is not None)

    # ===== 3. 授权链 =====
    st, _ = call("POST", "/api/activity/admin/create",
                 {"name": "授权链验证活动", "type": "promotion"}, MGR)
    rec("3a 未授权403", st == 403)

    st, g = call("POST", "/api/perm/grants",
                 {"memberId": mgr_id, "nodeCode": "activity.operate",
                  "durationDays": 30}, ADM)
    gid = g.get("grantId")
    if not gid:
        st, gl = call("GET", "/api/perm/admin/grants", None, ADM)
        for gg in gl.get("grants", []):
            if (gg.get("memberId") == mgr_id
                    and gg.get("nodeCode") == "activity.operate"
                    and gg.get("status") == "active"):
                gid = gg.get("grantId")
                break
    rec("3b 授予activity.operate", gid is not None)

    st, _ = call("POST", "/api/activity/admin/create",
                 {"name": "授权链验证活动", "type": "promotion"}, MGR)
    rec("3c 未签责任书403", st == 403)

    st, _ = call("POST", f"/api/perm/grants/{gid}/duty-sign", None, MGR)
    rec("3d 签署责任书", st == 200)

    st, c = call("POST", "/api/activity/admin/create",
                 {"name": "运营官专区·授权验证", "type": "interactive",
                  "description": "活动后台授权链生产验证(稍后取消)。",
                  "startTime": "2026-10-01T00:00", "endTime": "2026-10-07T23:59"},
                 MGR)
    aid = (c.get("data") or {}).get("id")
    rec("3e 授权后可创建", st == 200 and aid is not None)

    st, a = call("POST", f"/api/activity/admin/audit/{aid}",
                 {"approve": True, "auditor": mgr_id}, MGR)
    rec("3f 授权后可发布", st == 200
        and a.get("data", {}).get("status") == "registering")

    st, ml = call("GET", "/api/activity/admin/list?limit=100", None, MGR)
    rec("3g 授权后可看管理列表", st == 200 and ml.get("count", 0) >= 2)

    # ===== 4. 吊销 + 活动取消(清理) =====
    st, _ = call("DELETE", f"/api/perm/grants/{gid}", None, ADM)
    rec("4a 吊销授权", st == 200)

    st, _ = call("POST", "/api/activity/admin/create",
                 {"name": "授权链验证活动2", "type": "promotion"}, MGR)
    rec("4b 吊销后403", st == 403)

    # 测试活动下线(注册中→取消, 不留 C端列表)
    st, _ = call("POST", f"/api/activity/admin/transition/{aid}",
                 {"targetStatus": "cancelled", "operator": adm_id}, ADM)
    rec("4c 验证活动已取消清理", st == 200)

    print("=" * 60)
    print("活动后台授权链·生产 E2E")
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


if __name__ == "__main__":
    main()
