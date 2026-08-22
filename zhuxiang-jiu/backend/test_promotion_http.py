"""推广码矩阵获利模块 HTTP 层验证(TestClient 全栈直连, 覆盖 18 个接口)

在宿主机运行(需已安装 fastapi + httpx):
    cd D:\\网站架构设计\\zhuxiang-jiu\\backend
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_promotion_http.py

场景主线(全部走 HTTP, 请求经过 JWT 中间件 + 全部路由层):
    管理员调小参数 → A/B1/B2 开钱包 → A 领码 → B1/B2 绑定(触发一级奖 ¥10)
    → B1/B2 各推 2 人(触发 A 领酒资格) → A 领酒 → 奖励余额购物
    → 奖励余额不可提现 → 发货流转 → 撤销码/作废关系/负向用例
    → JWT Bearer 免旧头访问(中间件身份注入演示)
"""

import asyncio
import os
import sys

# 必须在导入 main 之前设置(内存模式 + 认证兼容模式)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.setdefault("AUTH_MODE", "compat")

from fastapi.testclient import TestClient

from main import app
from repositories.member_repository import MemberRepository
from repositories.store import reset_store

client = TestClient(app)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} -- {detail}")


def hdr(member_id):
    return {"X-Member-Id": str(member_id)}


ADMIN = {"X-Role": "admin"}


def _mk_members() -> dict:
    """构造测试会员(成长值 600, 满足钱包开户等级要求), 返回 {角色: 会员ID}"""
    async def _create_all():
        repo = MemberRepository()
        mapping = {}
        for key, phone in (("A", "13900000001"), ("B1", "13900000011"),
                           ("B2", "13900000012"), ("C1", "13900000021"),
                           ("C2", "13900000022"), ("C3", "13900000023"),
                           ("C4", "13900000024"), ("D", "13900000031")):
            m = await repo.create({
                "phone": phone, "nickname": f"测试{key}",
                "password": "x" * 64, "status": 1, "role": "member",
                "level": 3, "growth_value": 600, "points": 0,
            })
            mapping[key] = m["id"]
        return mapping
    return asyncio.run(_create_all())


def main():
    print("=" * 64)
    print("推广码矩阵获利模块 HTTP 层验证(TestClient 全栈)")
    print("=" * 64)

    reset_store()
    M = _mk_members()

    # --------------------------------------------------------
    # 1. 管理端参数配置
    # --------------------------------------------------------
    r = client.get("/api/promotion/admin/settings", headers=ADMIN)
    s = r.json().get("settings", {})
    record("01_admin_read_default_settings",
           r.status_code == 200 and s.get("level1Threshold") == 100
           and abs(s.get("level1RewardAmount", 0) - 50) < 0.001
           and abs(s.get("wineMinPrice", 0) - 200) < 0.001,
           f"status={r.status_code}, settings={s}")

    r = client.get("/api/promotion/admin/settings")
    record("02_admin_settings_requires_admin_role",
           r.status_code == 403, f"status={r.status_code}")

    r = client.put("/api/promotion/admin/settings", headers=ADMIN,
                   json={"level1Threshold": 2, "level1RewardAmount": 10.0,
                         "level2SubPromoterCount": 2, "level2SubThreshold": 2})
    s = r.json().get("settings", {})
    record("03_admin_update_settings",
           r.status_code == 200 and s.get("level1Threshold") == 2
           and abs(s.get("level1RewardAmount", 0) - 10) < 0.001,
           f"status={r.status_code}, body={r.json()}")

    r = client.put("/api/promotion/admin/settings", headers=ADMIN,
                   json={"level1Threshold": 0})
    record("04_admin_invalid_threshold_rejected_422",
           r.status_code == 422,
           f"status={r.status_code}(Pydantic ge=1 拦截), body={r.json()}")

    # 服务层校验(绕过 Pydantic 的路径): 低价产品入酒池 → 409
    r = client.put("/api/promotion/admin/settings", headers=ADMIN,
                   json={"eligibleProductIds": ["ZX42-2026B01"]})  # ¥88 < ¥200
    record("04b_admin_pool_price_invalid_409",
           r.status_code == 409 and "低于" in r.json().get("error", ""),
           f"status={r.status_code}, detail={r.json().get('detail')}")

    # --------------------------------------------------------
    # 2. 开通钱包(HTTP)
    # --------------------------------------------------------
    for key in ("A", "B1", "B2"):
        r = client.post("/api/wallet/open", headers=hdr(M[key]))
        record(f"05_wallet_open_{key}",
               r.status_code == 200, f"status={r.status_code}, body={r.json()}")

    # --------------------------------------------------------
    # 3. 领取专属推广码
    # --------------------------------------------------------
    r = client.post("/api/promotion/code/claim", headers=hdr(M["A"]),
                    json={"channel": "wechat_miniprogram"})
    body = r.json()
    code_a = body.get("code", "")
    record("06_claim_code_with_zxbj_brand",
           r.status_code == 200 and code_a.startswith("ZXBJ-")
           and "竹奕" in body.get("shareTip", ""),
           f"status={r.status_code}, body={body}")

    r = client.post("/api/promotion/code/claim", headers=hdr(M["A"]),
                    json={"channel": "wechat_miniprogram"})
    record("07_claim_code_idempotent",
           r.status_code == 200 and r.json().get("reclaimed") is True
           and r.json().get("code") == code_a,
           f"status={r.status_code}, body={r.json()}")

    r = client.post("/api/promotion/code/claim", headers=hdr(M["A"]),
                    json={"channel": "facebook"})
    record("08_claim_invalid_channel_409",
           r.status_code == 409 and "渠道非法" in r.json().get("error", ""),
           f"status={r.status_code}")

    r = client.post("/api/promotion/code/claim", json={"channel": "douyin"})
    record("09_claim_without_login_401",
           r.status_code == 401, f"status={r.status_code}")

    r = client.get("/api/promotion/my/codes", headers=hdr(M["A"]))
    record("10_list_my_codes",
           r.status_code == 200 and len(r.json().get("codes", [])) == 1,
           f"status={r.status_code}")

    # --------------------------------------------------------
    # 4. 绑定推广码 + 一级奖励
    # --------------------------------------------------------
    r = client.post("/api/promotion/bind",
                    json={"code": code_a, "inviteeMemberId": M["B1"]})
    record("11_bind_b1_success",
           r.status_code == 200 and r.json().get("inviterMemberId") == M["A"],
           f"status={r.status_code}, body={r.json()}")

    r = client.post("/api/promotion/bind",
                    json={"code": code_a, "inviteeMemberId": M["B1"]})
    record("12_duplicate_bind_409",
           r.status_code == 409 and "已绑定" in r.json().get("error", ""),
           f"status={r.status_code}")

    r = client.post("/api/promotion/bind",
                    json={"code": code_a, "inviteeMemberId": M["A"]})
    record("13_self_bind_409",
           r.status_code == 409 and "自己" in r.json().get("error", ""),
           f"status={r.status_code}")

    r = client.post("/api/promotion/bind",
                    json={"code": "ZXBJ-XXXXXX", "inviteeMemberId": M["C1"]})
    record("14_unknown_code_409",
           r.status_code == 409 and "不存在" in r.json().get("error", ""),
           f"status={r.status_code}")

    r = client.post("/api/promotion/bind",
                    json={"code": code_a, "inviteeMemberId": M["B2"]})
    record("15_bind_b2_triggers_l1_reward",
           r.status_code == 200, f"status={r.status_code}")

    r = client.get("/api/promotion/my/stats", headers=hdr(M["A"]))
    st = r.json()
    record("16_stats_l1_reward_balance",
           r.status_code == 200 and st.get("directCount") == 2
           and abs(st.get("rewardBalance", 0) - 10.0) < 0.001
           and "不可提现" in st.get("rewardBalanceNote", ""),
           f"status={r.status_code}, stats={st}")

    # --------------------------------------------------------
    # 5. 二级裂变: B1/B2 各推 2 人 → A 获领酒资格
    # --------------------------------------------------------
    code_b1 = client.post("/api/promotion/code/claim", headers=hdr(M["B1"]),
                          json={"channel": "douyin"}).json()["code"]
    code_b2 = client.post("/api/promotion/code/claim", headers=hdr(M["B2"]),
                          json={"channel": "kuaishou"}).json()["code"]
    for code, c in ((code_b1, "C1"), (code_b1, "C2"),
                    (code_b2, "C3"), (code_b2, "C4")):
        rr = client.post("/api/promotion/bind",
                         json={"code": code, "inviteeMemberId": M[c]})
        assert rr.status_code == 200, f"绑定 {c} 失败: {rr.json()}"

    r = client.get("/api/promotion/my/rewards", headers=hdr(M["A"]))
    rewards = r.json().get("rewards", [])
    types = {x.get("rewardType") for x in rewards}
    record("17_a_rewards_wallet_and_wine_qualify",
           r.status_code == 200 and types == {"wallet", "wine_qualify"},
           f"status={r.status_code}, types={types}")

    r = client.get("/api/promotion/my/team", headers=hdr(M["A"]))
    team = r.json().get("team", [])
    record("18_team_list_with_subcount",
           r.status_code == 200 and len(team) == 2
           and all(t.get("subCount") == 2 for t in team),
           f"status={r.status_code}, team={team}")

    r = client.get("/api/promotion/my/stats", headers=hdr(M["A"]))
    st = r.json()
    record("19_stats_wine_qualify_available",
           st.get("qualifiedSubCount") == 2 and st.get("wineQualifyAvailable") == 1,
           f"stats={st}")

    # --------------------------------------------------------
    # 6. 领取奖励酒
    # --------------------------------------------------------
    r = client.get("/api/promotion/products/eligible")
    pool = r.json().get("products", [])
    record("20_eligible_pool_min_price",
           r.status_code == 200 and len(pool) >= 1
           and all(p.get("price", 0) >= 200 for p in pool),
           f"status={r.status_code}, pool={[(p['productId'], p['price']) for p in pool]}")

    product = pool[0]
    r = client.post("/api/promotion/wine/claim", headers=hdr(M["A"]),
                    json={"productId": product["productId"],
                          "address": "北京市朝阳区竹香路1号竹香酒业大厦"})
    claim = r.json()
    record("21_claim_wine_success",
           r.status_code == 200 and claim.get("status") == "pending_shipped"
           and claim.get("productId") == product["productId"],
           f"status={r.status_code}, claim={claim}")

    r = client.post("/api/promotion/wine/claim", headers=hdr(M["A"]),
                    json={"productId": product["productId"],
                          "address": "北京市朝阳区竹香路1号竹香酒业大厦"})
    record("22_wine_qualify_consumed_409",
           r.status_code == 409 and "资格" in r.json().get("error", ""),
           f"status={r.status_code}")

    r = client.post("/api/promotion/wine/claim", headers=hdr(M["A"]),
                    json={"productId": product["productId"], "address": "北京"})
    record("23_short_address_rejected_422",
           r.status_code == 422 and "address" in str(r.json()),
           f"status={r.status_code}(Pydantic min_length=5 拦截)")

    # --------------------------------------------------------
    # 7. 奖励余额购买本站产品(唯一出口, 不可提现)
    # --------------------------------------------------------
    r = client.post("/api/promotion/admin/rewards/grant", headers=ADMIN,
                    json={"memberId": M["A"], "rewardType": "wallet",
                          "amount": 100, "detail": "HTTP验证补发"})
    record("24_admin_grant_wallet_reward",
           r.status_code == 200, f"status={r.status_code}, body={r.json()}")

    r = client.post("/api/promotion/reward/purchase", headers=hdr(M["A"]),
                    json={"productId": "ZX42-2026B01", "quantity": 1})  # ¥88 便携款
    pur = r.json()
    record("25_reward_purchase_success",
           r.status_code == 200 and abs(pur.get("amount", 0) - 88) < 0.001
           and abs(pur.get("rewardBalanceAfter", -1) - 22) < 0.001,
           f"status={r.status_code}, body={pur}")

    r = client.post("/api/promotion/reward/purchase", headers=hdr(M["A"]),
                    json={"productId": product["productId"], "quantity": 1})
    record("26_reward_insufficient_409",
           r.status_code == 409 and "不足" in r.json().get("error", ""),
           f"status={r.status_code}")

    r = client.post("/api/promotion/reward/purchase", headers=hdr(M["A"]),
                    json={"productId": "NO-SUCH-PRODUCT", "quantity": 1})
    record("27_product_not_found_404",
           r.status_code == 404, f"status={r.status_code}")

    r = client.post("/api/wallet/withdraw", headers=hdr(M["A"]),
                    json={"amount": 1, "payChannel": "alipay"})
    record("28_reward_balance_not_withdrawable",
           r.status_code == 409 and "余额不足" in r.json().get("error", ""),
           f"status={r.status_code}, error={r.json().get('error')} "
           f"(rewardBalance=22 不可提现)")

    # --------------------------------------------------------
    # 8. 管理端: 发货流转 / 撤销码 / 作废关系
    # --------------------------------------------------------
    r = client.get("/api/promotion/admin/wine-claims", headers=ADMIN)
    claims = r.json().get("claims", [])
    claim_id = claims[0]["claimId"] if claims else 0
    record("29_admin_wine_claims_list",
           r.status_code == 200 and len(claims) == 1
           and claims[0].get("status") == "pending_shipped",
           f"status={r.status_code}, claims={claims}")

    r1 = client.put(f"/api/promotion/admin/wine-claims/{claim_id}/ship",
                    headers=ADMIN)
    r2 = client.put(f"/api/promotion/admin/wine-claims/{claim_id}/ship",
                    headers=ADMIN)
    r3 = client.put(f"/api/promotion/admin/wine-claims/{claim_id}/ship",
                    headers=ADMIN)
    record("30_ship_flow_pending_to_done",
           r1.json().get("status") == "shipped"
           and r2.json().get("status") == "done"
           and r3.status_code == 409,
           f"ship1={r1.json()}, ship2={r2.json()}, ship3={r3.status_code}")

    r = client.post(f"/api/promotion/admin/codes/{code_a}/revoke",
                    headers=ADMIN)
    record("31_admin_revoke_code",
           r.status_code == 200 and r.json().get("status") == "revoked",
           f"status={r.status_code}, body={r.json()}")

    r = client.post("/api/promotion/bind",
                    json={"code": code_a, "inviteeMemberId": M["D"]})
    record("32_revoked_code_blocks_binding_409",
           r.status_code == 409 and "失效" in r.json().get("error", ""),
           f"status={r.status_code}")

    r = client.post(f"/api/promotion/admin/relations/{M['B1']}/invalidate",
                    headers=ADMIN)
    st = client.get("/api/promotion/my/stats", headers=hdr(M["A"])).json()
    direct_invalid = st.get("directCount")
    client.post(f"/api/promotion/admin/relations/{M['B1']}/invalidate",
                headers=ADMIN)
    st = client.get("/api/promotion/my/stats", headers=hdr(M["A"])).json()
    record("33_invalidate_relation_affects_stats",
           r.status_code == 200 and direct_invalid == 1
           and st.get("directCount") == 2,
           f"作废后={direct_invalid}, 恢复后={st.get('directCount')}")

    r = client.post("/api/promotion/admin/rewards/grant", headers=ADMIN,
                    json={"memberId": 999999, "rewardType": "wallet",
                          "amount": 1})
    record("34_grant_nonexistent_member_404",
           r.status_code == 404, f"status={r.status_code}")

    # --------------------------------------------------------
    # 9. JWT Bearer 免旧头访问(中间件身份注入演示)
    # --------------------------------------------------------
    r = client.post("/api/auth/login",
                    json={"phone": "13800000001", "password": "test123456"})
    token = r.json().get("accessToken", "")
    record("35_jwt_login_for_seed_member",
           r.status_code == 200 and bool(token),
           f"status={r.status_code}")

    r = client.get("/api/promotion/my/stats",
                   headers={"Authorization": f"Bearer {token}"})
    record("36_jwt_bearer_injects_identity",
           r.status_code == 200 and r.json().get("memberId") == 1,
           f"status={r.status_code}, body={r.json()} "
           f"(无 X-Member-Id 头, 身份来自 JWT)")

    # --------------------------------------------------------
    # 汇总
    # --------------------------------------------------------
    print()
    for line in RESULTS:
        print(line)
    print()
    print("=" * 64)
    print(f"总计: {PASS + FAIL}  通过: {PASS}  失败: {FAIL}")
    print("=" * 64)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
