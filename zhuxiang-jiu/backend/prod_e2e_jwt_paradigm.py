"""阶段D: 生产 E2E 裸头范式验证(strict 切换后)+ JWT 化新范式全链实证(hub 试点)

背景:
    AUTH_MODE=strict(2026-09-18 阶段C)后, 旧 E2E 裸头范式
    (X-Role: admin / X-Member-Id)全部 401 —— 本脚本做两件事:
      ① 裸头对照实证: 代表性端点裸头请求 → 预期全 401(旧范式失效留痕)
      ② JWT 化新范式: POST /api/auth/login 取 accessToken →
         Bearer 头全链验证(hub 14 端点代表面 + 用户侧面)

零副作用原则:
    - retrigger(全 skipped) / capabilities 查询 / 只读端点为主
    - asr 假音频 → 结构化降级(无副作用); intent 文本 → 埋点+1(无害)
    - approve/reject 用 not-exist id → 404(不碰生产 3 条真实挑战者)
    - toggle 不碰(避免能力状态变更)
"""
import base64

import httpx

BASE = "http://127.0.0.1:8000"
PASS = 0
FAIL = 0


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  \u2713 {name}")
    else:
        FAIL += 1
        print(f"  \u2717 {name} \u2014 {detail}")


def get_jwt(c: httpx.Client, phone: str) -> str:
    """E2E JWT 辅助: 登录取 accessToken(阶段B范式核心)"""
    r = c.post("/api/auth/login",
               json={"phone": phone, "password": "test123456"})
    assert r.status_code == 200, f"登录失败({phone}): {r.text}"
    return r.json()["accessToken"]


with httpx.Client(base_url=BASE, timeout=30) as c:
    print("=" * 64)
    print("阶段D-①: 旧裸头范式对照(strict 下预期全 401)")
    print("=" * 64)
    LEGACY = [
        ("GET", "/api/hub/ops/overview", {"X-Role": "admin"},
         "管理面裸头 X-Role"),
        ("GET", "/api/ai-learning/overview", {"X-Role": "admin"},
         "管理面裸头 X-Role(跨模块)"),
        ("GET", "/api/points/account/1", {"X-Member-Id": "1"},
         "用户面裸头 X-Member-Id"),
        ("GET", "/api/member/profile", {"X-Member-Id": "1"},
         "用户面裸头 X-Member-Id(跨模块)"),
    ]
    for method, path, hdrs, label in LEGACY:
        r = c.request(method, path, headers=hdrs)
        blocked = (r.status_code == 401
                   and "未登录" in str(r.json().get("detail", "")))
        record(label, blocked, f"status={r.status_code} body={r.text[:60]}")

    print()
    print("=" * 64)
    print("阶段D-②: JWT 化新范式全链(hub 试点 + 用户侧面)")
    print("=" * 64)
    # 登录: 管理面用 member3(role=admin 站点管理员), 用户面用 member1
    admin_token = get_jwt(c, "13800000002")
    member_token = get_jwt(c, "13800000001")
    record("管理面登录取 JWT(member3)", True)
    record("用户面登录取 JWT(member1)", True)
    A = {"Authorization": f"Bearer {admin_token}"}
    M = {"Authorization": f"Bearer {member_token}"}

    # --- hub 输入/入口面(公开) ---
    r = c.post("/api/hub/input/intent", json={"text": "这瓶酒多少钱"})
    record("intent 意图分类(公开)", r.status_code == 200
           and r.json().get("intent") is not None, f"{r.status_code}")
    r = c.post("/api/hub/asr",
               json={"audio_b64": base64.b64encode(b"fake").decode(),
                     "fmt": "wav"})
    record("asr 结构化降级(公开)", r.status_code == 200
           and r.json().get("success") is False, f"{r.status_code}")
    r = c.get("/api/hub/panel", params={"role": "member"})
    record("panel 角色面板(公开)", r.status_code == 200
           and len(r.json().get("chips", [])) > 0, f"{r.status_code}")
    r = c.get("/api/hub/health")
    record("health 入口健康(公开)", r.status_code == 200
           and r.json().get("status") in ("healthy", "degraded"),
           f"{r.status_code}")

    # --- hub 管理面(Bearer, JWT 注入 x-role=admin) ---
    r = c.get("/api/hub/capabilities", headers=A)
    record("capabilities 注册表(admin Bearer)", r.status_code == 200
           and r.json().get("total", 0) > 0, f"{r.status_code}")
    r = c.get("/api/hub/ops/overview", headers=A)
    record("ops/overview 治理总览(admin Bearer)", r.status_code == 200
           and "capabilityMatrix" in r.json(), f"{r.status_code}")
    r = c.get("/api/hub/ops/usage", headers=A)
    record("ops/usage 用量视图(admin Bearer)", r.status_code == 200,
           f"{r.status_code}")
    r = c.get("/api/hub/ops/learning/approvals", headers=A)
    record("ops/approvals 待审清单(admin Bearer)", r.status_code == 200,
           f"{r.status_code}")
    r = c.post("/api/hub/ops/learning/retrigger", json={}, headers=A)
    b = r.json()
    record("retrigger 学习重跑(admin Bearer, 全 skipped 零副作用)",
           r.status_code == 200 and b.get("governanceOnly") == 8
           and all(x["status"] != "unknown" for x in b["results"]),
           f"{r.status_code} total={b.get('total')}")
    # approve/reject 用 not-exist id → 404(不碰真实挑战者)
    r = c.post("/api/hub/ops/learning/approve/not-exist", headers=A)
    record("approve 未知 404(admin Bearer)", r.status_code == 404,
           f"{r.status_code}")
    # 会员 JWT 访问管理面 → 403(注入 x-role=member, 权限边界保持)
    r = c.get("/api/hub/ops/overview", headers=M)
    record("管理面 member Bearer → 403(权限边界)", r.status_code == 403,
           f"{r.status_code}")

    # --- 用户侧面(Bearer + 原 X-Member-Id 调用点) ---
    r = c.get("/api/points/account/1", headers=M)
    record("points/account(用户 Bearer)", r.status_code == 200,
           f"{r.status_code}")
    r = c.get("/api/points/signin/1")
    record("points/signin 无头(需鉴权面, 401 预期)", r.status_code == 401,
           f"{r.status_code}")

print("-" * 64)
print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
raise SystemExit(1 if FAIL else 0)
