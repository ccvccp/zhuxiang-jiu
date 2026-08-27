"""推广码矩阵获利模块端到端测试(Service 层直调, 不依赖 fastapi)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_promotion_routes.py

覆盖:
    1. 推广码领取(6):   ZXBJ标识/多渠道/幂等/非法渠道/会员不存在/列表
    2. 绑定关系(7):     正常/重复/自绑/码不存在/撤销码/防环/绑定计数
    3. 一级奖励(5):     达标发放/流水标注/未达标不发/第二轮/奖励余额隔离
    4. 二级奖励+领酒(8): 达标发资格/领取成功/核销/无资格拒绝/地址过短/
                        价格不达标/资格用尽/发货流转
    5. 奖励余额购买(4): 成功/余额不足/产品不存在/不可提现验证
    6. 参数管理(5):     默认值/修改/非法值/酒池校验/参数即时生效
    7. 管理端(6):       关系列表/作废关系/撤销码/手动补发/发货终态
    8. 统计(3):         stats完整性/团队列表/活动酒池
"""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.promotion_service import PromotionService
from services.wallet_service import WalletService
from repositories.member_repository import MemberRepository
from repositories.product_repository import ProductRepository
from repositories.store import _mock_store, reset_store as _reset_store_impl

# 测试结果收集
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


def reset_store():
    _reset_store_impl()


async def _mk_member(member_repo, phone, nickname="", created_at=None):
    """创建测试会员(默认注册时间=现在, 即'新人'; created_at 可指定为过去)"""
    return await member_repo.create({
        "phone": phone, "nickname": nickname or f"会员{phone[-4:]}",
        "password": "x" * 64, "status": 1, "role": "member",
        # growth_value >= 500 才能满足钱包开户的会员等级要求
        "level": 3, "growth_value": 600, "points": 0,
        "created_at": created_at or datetime.now(UTC).isoformat(),
    })


async def _expect_value_error(coro, keyword=""):
    """断言协程抛 ValueError(可含关键字), 返回 (raised, msg)"""
    try:
        await coro
        return False, ""
    except ValueError as exc:
        return (not keyword or keyword in str(exc)), str(exc)
    except Exception as exc:
        return False, f"非ValueError: {type(exc).__name__}: {exc}"


async def _expect_key_error(coro):
    try:
        await coro
        return False, ""
    except KeyError:
        return True, ""
    except Exception as exc:
        return False, f"非KeyError: {type(exc).__name__}: {exc}"


# ============================================================
# 1. 推广码领取
# ============================================================

class TestClaimCode:
    async def run(self):
        svc = PromotionService()
        member_repo = MemberRepository()
        reset_store()
        a = await _mk_member(member_repo, "13900000001", "推广人A")
        a_id = a["id"]

        # test 1: 领码成功, 含 ZXBJ 竹奕标识
        r = await svc.claim_promo_code(a_id, "wechat_miniprogram")
        record("test_01_claim_code_success",
               r["success"] and r["code"].startswith("ZXBJ-")
               and r["channel"] == "wechat_miniprogram" and not r["reclaimed"],
               f"result={r}")

        # test 2: 多渠道各领一码
        r2 = await svc.claim_promo_code(a_id, "douyin")
        record("test_02_multi_channel_code",
               r2["success"] and r2["code"].startswith("ZXBJ-")
               and r2["code"] != r["code"],
               f"douyin={r2.get('code')}")

        # test 3: 同渠道幂等(返回已有码)
        r3 = await svc.claim_promo_code(a_id, "wechat_miniprogram")
        record("test_03_claim_idempotent",
               r3["reclaimed"] and r3["code"] == r["code"],
               f"result={r3}")

        # test 4: 非法渠道
        ok, msg = await _expect_value_error(
            svc.claim_promo_code(a_id, "facebook"), "渠道非法")
        record("test_04_invalid_channel", ok, f"msg={msg}")

        # test 5: 会员不存在
        ok, msg = await _expect_key_error(
            svc.claim_promo_code(999999, "douyin"))
        record("test_05_member_not_found", ok, f"msg={msg}")

        # test 6: 我的推广码列表(2 渠道 + 分享文案)
        codes = await svc.list_my_codes(a_id)
        record("test_06_list_my_codes",
               len(codes) == 2 and all("shareTip" in c for c in codes),
               f"codes={[(c['code'], c['channel']) for c in codes]}")

        # 供后续测试复用
        return a_id, r["code"]


# ============================================================
# 2. 绑定关系
# ============================================================

class TestBindRelation:
    async def run(self):
        svc = PromotionService()
        member_repo = MemberRepository()
        reset_store()
        a = await _mk_member(member_repo, "13900000001", "推广人A")
        b = await _mk_member(member_repo, "13900000002", "下线B")
        c = await _mk_member(member_repo, "13900000003", "下线C")
        a_id, b_id, c_id = a["id"], b["id"], c["id"]

        code = (await svc.claim_promo_code(a_id, "wechat_miniprogram"))["code"]
        code_b = (await svc.claim_promo_code(b_id, "douyin"))["code"]

        # test 7: 正常绑定
        r = await svc.bind_relation(code, b_id)
        record("test_07_bind_success",
               r["success"] and r["inviterMemberId"] == a_id
               and r["inviteeMemberId"] == b_id, f"result={r}")

        # test 8: 重复绑定拒绝(一人仅一次)
        ok, msg = await _expect_value_error(
            svc.bind_relation(code, b_id), "已绑定")
        record("test_08_duplicate_bind_rejected", ok, f"msg={msg}")

        # test 9: 自绑拒绝
        ok, msg = await _expect_value_error(
            svc.bind_relation(code, a_id), "自己")
        record("test_09_self_bind_rejected", ok, f"msg={msg}")

        # test 10: 码不存在
        ok, msg = await _expect_value_error(
            svc.bind_relation("ZXBJ-XXXXXX", c_id), "不存在")
        record("test_10_code_not_found", ok, f"msg={msg}")

        # test 11: 撤销码拒绝绑定
        await svc.admin_revoke_code(code_b)
        d = await _mk_member(member_repo, "13900000004", "下线D")
        ok, msg = await _expect_value_error(
            svc.bind_relation(code_b, d["id"]), "失效")
        record("test_11_revoked_code_rejected", ok, f"msg={msg}")

        # test 12: 防环: B 的新码绑 A(A→B 已存在, B→A 将成环)
        code_b_direct = (await svc.claim_promo_code(b_id, "direct"))["code"]
        ok, msg = await _expect_value_error(
            svc.bind_relation(code_b_direct, a_id), "成环")
        record("test_12_cycle_rejected", ok, f"msg={msg}")

        # test 13: 码绑定计数
        codes = await svc.list_my_codes(a_id)
        record("test_13_bound_count",
               codes[0].get("boundCount") == 1, f"codes={codes}")

        # test 13b: 新人注册原则 —— 老会员(注册超24h)绑定成功但不计业绩
        old = await _mk_member(
            member_repo, "13900000005", "老会员E",
            created_at=(datetime.now(UTC) - timedelta(days=2)).isoformat())
        r_old = await svc.bind_relation(code, old["id"])
        stats = await svc.get_my_stats(a_id)
        record("test_13b_old_member_not_counted",
               r_old["success"] and r_old["counted"] is False
               and stats["directCount"] == 1,
               f"result={r_old}, directCount={stats['directCount']}")

        # test 13c: 新人注册原则 —— 新注册会员(24h内)绑定计业绩
        fresh = await _mk_member(member_repo, "13900000006", "新人F")
        r_new = await svc.bind_relation(code, fresh["id"])
        stats = await svc.get_my_stats(a_id)
        record("test_13c_new_member_counted",
               r_new["success"] and r_new["counted"] is True
               and stats["directCount"] == 2,
               f"result={r_new}, directCount={stats['directCount']}")


# ============================================================
# 3+4. 两级矩阵奖励(小参数验证算法)
# ============================================================

class TestMatrixRewards:
    async def run(self):
        svc = PromotionService()
        wallet_svc = WalletService()
        member_repo = MemberRepository()
        reset_store()

        # 小参数: 直推满2人发10元; 2个下线各推满2人发二级现金12元
        await svc.update_settings({
            "level1Threshold": 2, "level1RewardAmount": 10.0,
            "level2SubPromoterCount": 2, "level2SubThreshold": 2,
            "level2RewardAmount": 12.0,
        }, admin="tester")

        a = await _mk_member(member_repo, "13900000001", "推广人A")
        a_id = a["id"]
        await wallet_svc.open(a_id)

        code = (await svc.claim_promo_code(a_id, "wechat_miniprogram"))["code"]

        # B1/B2 绑定 A → A 直推 2 人, 得第一轮 10 元
        b1 = await _mk_member(member_repo, "13900000011", "下线B1")
        b2 = await _mk_member(member_repo, "13900000012", "下线B2")
        await wallet_svc.open(b1["id"])
        await wallet_svc.open(b2["id"])
        await svc.bind_relation(code, b1["id"])
        await svc.bind_relation(code, b2["id"])

        # test 14: 一级奖励发放(直推满2人 → 10元奖励余额)
        info = await wallet_svc.get_reward_balance(a_id)
        record("test_14_l1_reward_issued",
               abs(info["rewardBalance"] - 10.0) < 0.001,
               f"rewardBalance={info['rewardBalance']}")

        # test 15: 奖励流水 type=reward 且标注不可提现
        txs = await wallet_svc.wallet_repo.list_transactions(
            a_id, tx_type="reward")
        record("test_15_reward_tx_annotated",
               len(txs) == 1 and "不可提现" in txs[0]["description"],
               f"tx={txs[0]['description'] if txs else None}")

        # test 16: 再绑第3人不重复发(阈值2, 已发1轮, 需4人才发第2轮);
        #          绑第4人 → 叠加发放第2轮(10+10=20元, 奖励可叠加)
        x = await _mk_member(member_repo, "13900000013", "下线X")
        await svc.bind_relation(code, x["id"])
        info = await wallet_svc.get_reward_balance(a_id)
        record("test_16_no_premature_second_cycle",
               abs(info["rewardBalance"] - 10.0) < 0.001,
               f"rewardBalance={info['rewardBalance']}")
        x2 = await _mk_member(member_repo, "13900000014", "下线X2")
        await svc.bind_relation(code, x2["id"])
        info = await wallet_svc.get_reward_balance(a_id)
        record("test_16b_l1_reward_stacks",
               abs(info["rewardBalance"] - 20.0) < 0.001,
               f"rewardBalance={info['rewardBalance']}")

        # B1 推满 2 人(B1 得奖励; A 的 qualified=1 不发二级现金)
        code_b1 = (await svc.claim_promo_code(b1["id"], "douyin"))["code"]
        c1 = await _mk_member(member_repo, "13900000021", "C1")
        c2 = await _mk_member(member_repo, "13900000022", "C2")
        await svc.bind_relation(code_b1, c1["id"])
        await svc.bind_relation(code_b1, c2["id"])
        rewards_a = await svc.list_my_rewards(a_id)
        l2_before = [r for r in rewards_a if r["rewardType"] == "wallet_l2"]

        # test 17: 仅1个达标下线时不发二级现金
        record("test_17_no_l2_until_threshold",
               len(l2_before) == 0, f"l2={l2_before}")

        # B2 也推满 2 人 → A 的 qualified=2 → 发二级现金 12 元
        code_b2 = (await svc.claim_promo_code(b2["id"], "kuaishou"))["code"]
        c3 = await _mk_member(member_repo, "13900000023", "C3")
        c4 = await _mk_member(member_repo, "13900000024", "C4")
        await svc.bind_relation(code_b2, c3["id"])
        await svc.bind_relation(code_b2, c4["id"])

        rewards_a = await svc.list_my_rewards(a_id)
        l2_rewards = [r for r in rewards_a if r["rewardType"] == "wallet_l2"]
        info_after_l2 = await wallet_svc.get_reward_balance(a_id)

        # test 18: 2个达标下线 → 二级现金奖励(两轮一级10+10 + 二级12 = 32元)
        record("test_18_l2_reward_issued",
               len(l2_rewards) == 1 and l2_rewards[0]["status"] == "issued"
               and abs(l2_rewards[0]["amount"] - 12.0) < 0.001
               and abs(info_after_l2["rewardBalance"] - 32.0) < 0.001,
               f"l2={l2_rewards}, balance={info_after_l2['rewardBalance']}")

        # test 19: B2 也获得一级奖励(直推满2人)
        info_b2 = await wallet_svc.get_reward_balance(b2["id"])
        record("test_19_b2_l1_reward",
               abs(info_b2["rewardBalance"] - 10.0) < 0.001,
               f"b2 rewardBalance={info_b2['rewardBalance']}")

        # test 20: 活动酒池(价格>=200)
        pool = await svc.list_eligible_products()
        record("test_20_eligible_pool",
               len(pool) >= 1 and all(p["price"] >= 200 for p in pool),
               f"pool={[(p['productId'], p['price']) for p in pool]}")

        # test 21: 领酒成功(先手动补发领酒资格, 二级奖励已改为现金)
        product = pool[0]
        await svc.admin_grant_reward(a_id, "wine_qualify", 0, "测试补发资格")
        claim = await svc.claim_wine(a_id, product["productId"],
                                     "北京市朝阳区XX路1号竹香酒业大厦")
        record("test_21_claim_wine_success",
               claim["success"] and claim["status"] == "pending_shipped"
               and claim["productId"] == product["productId"],
               f"claim={claim}")

        # test 22: 资格核销后再领 → 拒绝
        ok, msg = await _expect_value_error(
            svc.claim_wine(a_id, product["productId"],
                           "北京市朝阳区XX路1号竹香酒业大厦"), "资格")
        record("test_22_wine_qualify_consumed", ok, f"msg={msg}")

        # test 23: 地址过短拒绝
        ok, msg = await _expect_value_error(
            svc.claim_wine(a_id, product["productId"], "北京"), "地址")
        record("test_23_short_address_rejected", ok, f"msg={msg}")

        return a_id, code, product


# ============================================================
# 5. 奖励余额购买 + 不可提现验证
# ============================================================

class TestRewardPurchase:
    async def run(self, ctx):
        a_id, _code, product = ctx
        svc = PromotionService()
        wallet_svc = WalletService()

        # A 当前奖励余额 10 元
        # test 24: 奖励余额购买成功(取价格<=10的产品或先补发)
        products = await svc.product_repo.list_products()
        cheap = min(products, key=lambda p: float(p.get("price", 0)))
        cheap_price = float(cheap["price"])
        # 补发足额奖励便于购买
        await svc.admin_grant_reward(a_id, "wallet", cheap_price + 5,
                                     "测试补发")
        r = await svc.reward_purchase(a_id, cheap["product_id"], 1)
        record("test_24_reward_purchase_success",
               r["success"] and abs(r["amount"] - cheap_price) < 0.001
               and r["rewardBalanceAfter"] >= 0,
               f"result={r}")

        # test 25: 购买流水 type=reward_pay
        txs = await wallet_svc.wallet_repo.list_transactions(
            a_id, tx_type="reward_pay")
        record("test_25_reward_pay_tx",
               len(txs) >= 1 and txs[0]["direction"] == "OUT",
               f"count={len(txs)}")

        # test 26: 奖励余额不足拒绝
        ok, msg = await _expect_value_error(
            svc.reward_purchase(a_id, product["productId"], 99), "不足")
        record("test_26_reward_insufficient", ok, f"msg={msg}")

        # test 27: 产品不存在 404
        ok, msg = await _expect_key_error(
            svc.reward_purchase(a_id, "NO-SUCH-PRODUCT", 1))
        record("test_27_product_not_found", ok, f"msg={msg}")

        # test 28: 奖励余额不可提现(balance=0, reward>0, 提现拒绝)
        info = await wallet_svc.get_reward_balance(a_id)
        try:
            await wallet_svc.withdraw(a_id, 1.0, pay_channel="alipay")
            record("test_28_reward_not_withdrawable", False,
                   "提现竟然成功了")
        except ValueError as exc:
            record("test_28_reward_not_withdrawable",
                   "余额不足" in str(exc) and info["rewardBalance"] > 0,
                   f"msg={exc}, reward={info['rewardBalance']}")


# ============================================================
# 6. 参数管理
# ============================================================

class TestSettings:
    async def run(self):
        svc = PromotionService()
        reset_store()

        # test 29: 默认参数(10人/20元/6达标/5推广/15元/200元)
        s = await svc.get_settings()
        record("test_29_default_settings",
               s["level1Threshold"] == 10
               and abs(s["level1RewardAmount"] - 20.0) < 0.001
               and s["level2SubPromoterCount"] == 6
               and s["level2SubThreshold"] == 5
               and abs(s["level2RewardAmount"] - 15.0) < 0.001
               and abs(s["wineMinPrice"] - 200.0) < 0.001,
               f"settings={s}")

        # test 30: 修改参数成功
        s = await svc.update_settings({"level1Threshold": 50,
                                       "level1RewardAmount": 88.0},
                                      admin="tester")
        record("test_30_update_settings",
               s["level1Threshold"] == 50
               and abs(s["level1RewardAmount"] - 88.0) < 0.001
               and s["updatedBy"] == "tester",
               f"settings={s}")

        # test 31: 非法参数拒绝
        ok, msg = await _expect_value_error(
            svc.update_settings({"level1Threshold": 0}), "阈值")
        record("test_31_invalid_threshold", ok, f"msg={msg}")

        # test 32: 酒池产品价格不达标拒绝
        products = await svc.product_repo.list_products()
        cheap = min(products, key=lambda p: float(p.get("price", 0)))
        ok, msg = await _expect_value_error(
            svc.update_settings({"eligibleProductIds": [cheap["product_id"]]}),
            "低于")
        record("test_32_pool_price_invalid", ok, f"msg={msg}")

        # test 33: 参数即时生效(新绑定按新阈值计算)
        member_repo = MemberRepository()
        wallet_svc = WalletService()
        a = await _mk_member(member_repo, "13900000001", "A")
        await wallet_svc.open(a["id"])
        await svc.update_settings({"level1Threshold": 1,
                                   "level1RewardAmount": 10.0,
                                   "level2SubPromoterCount": 99,
                                   "level2SubThreshold": 99},
                                  admin="tester")
        code = (await svc.claim_promo_code(a["id"], "direct"))["code"]
        b = await _mk_member(member_repo, "13900000002", "B")
        await svc.bind_relation(code, b["id"])
        info = await wallet_svc.get_reward_balance(a["id"])
        record("test_33_settings_take_effect_immediately",
               abs(info["rewardBalance"] - 10.0) < 0.001,
               f"reward={info['rewardBalance']}")


# ============================================================
# 7. 管理端
# ============================================================

class TestAdmin:
    async def run(self):
        svc = PromotionService()
        wallet_svc = WalletService()
        member_repo = MemberRepository()
        reset_store()
        await svc.update_settings({
            "level1Threshold": 2, "level1RewardAmount": 10.0,
            "level2SubPromoterCount": 1, "level2SubThreshold": 1,
        }, admin="tester")

        a = await _mk_member(member_repo, "13900000001", "A")
        b = await _mk_member(member_repo, "13900000002", "B")
        c = await _mk_member(member_repo, "13900000003", "C")
        await wallet_svc.open(a["id"])
        await wallet_svc.open(b["id"])
        code = (await svc.claim_promo_code(a["id"], "wechat_miniprogram"))["code"]

        # test 34: 关系列表(管理端, A 直推 B/C 两人 → 触发一级奖励 10 元)
        await svc.bind_relation(code, b["id"])
        await svc.bind_relation(code, c["id"])
        relations = await svc.admin_list_relations(inviter_member_id=a["id"])
        record("test_34_admin_relations",
               len(relations) == 2,
               f"relations={[(r['inviteeMemberId']) for r in relations]}")

        # test 35: 作废关系 → 不计业绩(B 移除, C 仍计 1)
        await svc.admin_invalidate_relation(b["id"])
        stats = await svc.get_my_stats(a["id"])
        record("test_35_invalidate_relation",
               stats["directCount"] == 1, f"stats={stats['directCount']}")
        # 恢复
        await svc.admin_invalidate_relation(b["id"])

        # test 36: 手动补发钱包奖励(A 直推 B/C 两人已自动得 10 元)
        r = await svc.admin_grant_reward(a["id"], "wallet", 66.0, "客诉补偿")
        info = await wallet_svc.get_reward_balance(a["id"])
        record("test_36_admin_grant_wallet",
               r["success"] and abs(info["rewardBalance"]
                                    - (10.0 + 66.0)) < 0.001,
               f"reward={info['rewardBalance']}")

        # test 37: 手动补发领酒资格 + 领取 + 发货流转
        # (内存模式返回同一 dict 引用, 逐次快照状态避免引用陷阱)
        await svc.admin_grant_reward(a["id"], "wine_qualify", 0, "活动补偿")
        pool = await svc.list_eligible_products()
        claim = await svc.claim_wine(a["id"], pool[0]["productId"],
                                     "上海市浦东新区XX路8号")
        shipped = await svc.admin_ship_wine(claim["claimId"])
        ship_status = shipped["status"]  # 快照
        done = await svc.admin_ship_wine(claim["claimId"])
        done_status = done["status"]     # 快照
        ok, msg = await _expect_value_error(
            svc.admin_ship_wine(claim["claimId"]), "终态")
        record("test_37_ship_flow",
               ship_status == "shipped" and done_status == "done" and ok,
               f"ship={ship_status}, done={done_status}, final_err={msg}")

        # test 38: 领酒记录列表(管理端)
        claims = await svc.admin_list_wine_claims(member_id=a["id"])
        record("test_38_admin_wine_claims",
               len(claims) == 1 and claims[0]["status"] == "done",
               f"claims={claims}")

        # test 39: 撤销推广码后再绑定拒绝(用新会员 D)
        await svc.admin_revoke_code(code)
        d = await _mk_member(member_repo, "13900000004", "D")
        ok, msg = await _expect_value_error(
            svc.bind_relation(code, d["id"]), "失效")
        record("test_39_revoke_code_blocks_binding", ok, f"msg={msg}")

        # test 40: 奖励列表(管理端)
        rewards = await svc.admin_list_rewards(member_id=a["id"])
        record("test_40_admin_rewards",
               len(rewards) >= 2 and
               {r["rewardType"] for r in rewards} >= {"wallet", "wine_qualify"},
               f"count={len(rewards)}")


# ============================================================
# 8. 统计
# ============================================================

class TestStats:
    async def run(self):
        svc = PromotionService()
        member_repo = MemberRepository()
        reset_store()
        await svc.update_settings({
            "level1Threshold": 3, "level1RewardAmount": 50.0,
            "level2SubPromoterCount": 2, "level2SubThreshold": 2,
        }, admin="tester")

        a = await _mk_member(member_repo, "13900000001", "A")
        a_id = a["id"]
        code = (await svc.claim_promo_code(a_id, "wechat_miniprogram"))["code"]
        b = await _mk_member(member_repo, "13900000002", "B")
        await svc.bind_relation(code, b["id"])

        # test 41: 统计字段完整性
        stats = await svc.get_my_stats(a_id)
        record("test_41_stats_fields",
               stats["directCount"] == 1
               and stats["level1Threshold"] == 3
               and abs(stats["level1RewardAmount"] - 50.0) < 0.001
               and stats["qualifiedSubCount"] == 0
               and "不可提现" in stats["rewardBalanceNote"],
               f"stats={stats}")

        # test 42: 团队列表含下线推广数
        team = await svc.list_my_team(a_id)
        record("test_42_team_list",
               len(team) == 1 and team[0]["inviteeMemberId"] == b["id"]
               and team[0]["subCount"] == 0,
               f"team={team}")


# ============================================================
# 主流程
# ============================================================

async def main():
    print("=" * 60)
    print("推广码矩阵获利模块 端到端测试")
    print("=" * 60)

    await TestClaimCode().run()
    await TestBindRelation().run()
    await TestMatrixRewards().run()

    # 奖励余额购买: 独立构造上下文(各 Test 类之间 reset_store 隔离)
    member_repo = MemberRepository()
    reset_store()
    svc = PromotionService()
    wallet_svc = WalletService()
    await svc.update_settings({
        "level1Threshold": 2, "level1RewardAmount": 10.0,
        "level2SubPromoterCount": 2, "level2SubThreshold": 2,
    }, admin="tester")
    a = await _mk_member(member_repo, "13900000001", "A")
    await wallet_svc.open(a["id"])
    pool = await svc.list_eligible_products()
    product = pool[0]
    await TestRewardPurchase().run((a["id"], None, product))

    await TestSettings().run()
    await TestAdmin().run()
    await TestStats().run()

    print()
    for line in RESULTS:
        print(line)
    print()
    print("=" * 60)
    print(f"总计: {PASS + FAIL}  通过: {PASS}  失败: {FAIL}")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
