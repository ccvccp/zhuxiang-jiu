"""顺手赚钱模块端到端测试(Service 层直调, 不依赖 fastapi)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_pocket_e2e.py

覆盖:
    1. 张贴打卡(5):  成功发首奖/非法场景/地址过短/照片缺失/超在贴点位上限
    2. 每日打卡(5):  成功发奖/当日重复拒绝/非本人点位/已撤销拒绝/隔天连续
    3. 存续奖励(6):  未满30天拒绝/海报满月¥20/车贴满月¥30/重复领取拒绝/
                     已撤销拒绝/满月可领标记
    4. 撤销作废(3):  撤销成功/撤销后打卡拒绝/管理端作废
    5. 统计记录(3):  stats完整性/点位列表/打卡记录
    6. 参数管理(4):  默认值/修改/非法值/规则接口
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.pocket_service import PocketService
from repositories.pocket_repository import PocketRepository
from repositories.member_repository import MemberRepository
from repositories.store import reset_store as _reset_store_impl

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


async def _mk_member(member_repo, phone, nickname=""):
    """创建测试会员(level3/growth600, 满足钱包开通条件)"""
    return await member_repo.create({
        "phone": phone, "nickname": nickname or f"会员{phone[-4:]}",
        "password": "x" * 64, "status": 1, "role": "member",
        "level": 3, "growth_value": 600, "points": 0,
        "created_at": datetime.now(UTC).isoformat(),
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


def _days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


async def main():
    svc = PocketService()
    repo = PocketRepository()
    member_repo = MemberRepository()
    reset_store()

    m = await _mk_member(member_repo, "13900000011", "张贴人")
    m_id = m["id"]
    # 开通钱包(奖励入账前置条件)
    from services.wallet_service import WalletService
    from repositories.wallet_repository import WalletRepository
    wallet = WalletService(wallet_repo=WalletRepository(),
                           member_repo=member_repo)
    await wallet.open(m_id)

    # ========================================================
    # 1. 张贴打卡
    # ========================================================
    print("\n========== 1. 张贴打卡 ==========")

    # test 1: 张贴打卡成功(酒店海报), 首打卡发 ¥2
    r = await svc.report_site(m_id, "hotel", "XX市XX区迎宾路1号 如家酒店大堂",
                              "https://cdn.example.com/p1.jpg")
    record("test_01_report_site_success",
           r["success"] and r["site"]["scene"] == "hotel"
           and r["site"]["posterType"] == "poster"
           and r["site"]["checkinCount"] == 1
           and r["checkin"]["rewardAmount"] == 2.0
           and r["checkin"]["aiScore"] >= 60,
           f"result={r}")

    # test 2: 非法场景拒绝
    ok, msg = await _expect_value_error(
        svc.report_site(m_id, "subway", "某地铁站", "p.jpg"), "场景非法")
    record("test_02_invalid_scene_rejected", ok, msg)

    # test 3: 地址过短拒绝
    ok, msg = await _expect_value_error(
        svc.report_site(m_id, "hotel", "路口", "p.jpg"), "地址过短")
    record("test_03_short_address_rejected", ok, msg)

    # test 4: 照片缺失拒绝
    ok, msg = await _expect_value_error(
        svc.report_site(m_id, "hotel", "XX超市入口", ""), "照片")
    record("test_04_missing_photo_rejected", ok, msg)

    # test 5: 在贴点位上限(默认5个), 第6个拒绝
    for i in range(4):
        await svc.report_site(m_id, "supermarket", f"XX市第{i + 2}号超市收银台",
                              f"https://cdn.example.com/s{i}.jpg")
    ok, msg = await _expect_value_error(
        svc.report_site(m_id, "community", "XX小区1单元公告栏", "p6.jpg"),
        "上限")
    record("test_05_max_sites_reached", ok, msg)

    # 撤销一个点位, 为后续测试腾出额度
    sites_all = await repo.list_sites_by_member(m_id, status="active")
    await svc.remove_site(m_id, sites_all[-1]["siteId"])

    # 后续场景需要更多点位, 临时放宽上限(结束前还原)
    await svc.admin_update_settings({"maxActiveSites": 20},
                                    updated_by="tester")

    # ========================================================
    # 2. 每日打卡
    # ========================================================
    print("\n========== 2. 每日打卡 ==========")

    # 准备一个干净的点位
    r2 = await svc.report_site(m_id, "taxi_rear", "鲁AT·12345 出租车后窗",
                               "https://cdn.example.com/taxi1.jpg")
    site_id = r2["site"]["siteId"]

    # test 6: 当日重复打卡拒绝
    ok, msg = await _expect_value_error(
        svc.checkin_site(m_id, site_id, "again.jpg"), "今日已打卡")
    record("test_06_duplicate_daily_checkin_rejected", ok, msg)

    # test 7: 隔天打卡成功(连续天数+1, 发 ¥2)
    await repo.update_site(site_id, {
        "lastCheckinAt": _days_ago(1)})
    r3 = await svc.checkin_site(m_id, site_id, "taxi-day2.jpg")
    record("test_07_next_day_checkin_success",
           r3["success"] and r3["checkin"]["rewardAmount"] == 2.0,
           f"result={r3}")
    site = await repo.get_site(site_id)
    record("test_07b_consecutive_days_increment",
           site["consecutiveDays"] == 2 and site["checkinCount"] == 2,
           f"site={site}")

    # test 8: 非本人点位打卡拒绝
    other = await _mk_member(member_repo, "13900000012", "路人乙")
    ok, msg = await _expect_value_error(
        svc.checkin_site(other["id"], site_id, "x.jpg"), "只能打卡自己")
    record("test_08_not_owner_rejected", ok, msg)

    # test 9: 照片缺失打卡拒绝
    ok, msg = await _expect_value_error(
        svc.checkin_site(m_id, site_id, ""), "照片")
    record("test_09_checkin_photo_required", ok, msg)

    # test 10: 点位不存在
    ok, _ = await _expect_key_error(svc.checkin_site(m_id, 99999, "x.jpg"))
    record("test_10_site_not_found", ok)

    # ========================================================
    # 3. 存续奖励(满月)
    # ========================================================
    print("\n========== 3. 存续奖励 ==========")

    # test 11: 未满 30 天拒绝
    ok, msg = await _expect_value_error(
        svc.claim_month_reward(m_id, site_id), "才可领取")
    record("test_11_month_reward_too_early", ok, msg)

    # test 12: 海报满 30 天领 ¥20
    poster = await svc.report_site(m_id, "restaurant", "XX市好吃来饭店收银台",
                                   "rest.jpg")
    poster_id = poster["site"]["siteId"]
    await repo.update_site(poster_id, {"postedAt": _days_ago(31)})
    r4 = await svc.claim_month_reward(m_id, poster_id)
    record("test_12_poster_month_reward_20",
           r4["success"] and r4["amount"] == 20.0,
           f"result={r4}")

    # test 13: 车贴满 30 天领 ¥30
    await repo.update_site(site_id, {"postedAt": _days_ago(30)})
    r5 = await svc.claim_month_reward(m_id, site_id)
    record("test_13_sticker_month_reward_30",
           r5["success"] and r5["amount"] == 30.0,
           f"result={r5}")

    # test 14: 重复领取拒绝
    ok, msg = await _expect_value_error(
        svc.claim_month_reward(m_id, poster_id), "已领取")
    record("test_14_duplicate_month_reward_rejected", ok, msg)

    # test 15: 撤销后领取拒绝
    tmp = await svc.report_site(m_id, "community", "XX小区2单元宣传栏",
                                "c2.jpg")
    tmp_id = tmp["site"]["siteId"]
    await repo.update_site(tmp_id, {"postedAt": _days_ago(35)})
    r6 = await svc.remove_site(m_id, tmp_id)
    record("test_16_remove_site",
           r6["success"] and r6["activeDays"] >= 35, f"result={r6}")
    ok, msg = await _expect_value_error(
        svc.claim_month_reward(m_id, tmp_id), "撤销")
    record("test_15_removed_site_month_reward_rejected", ok, msg)

    # ========================================================
    # 4. 撤销与作废
    # ========================================================
    print("\n========== 4. 撤销与作废 ==========")

    # test 17: 撤销后打卡拒绝
    ok, msg = await _expect_value_error(
        svc.checkin_site(m_id, tmp_id, "x.jpg"), "撤销")
    record("test_17_checkin_after_remove_rejected", ok, msg)

    # test 18: 管理端作废点位
    bad = await svc.report_site(m_id, "hotel", "XX酒店后巷隐蔽处", "bad.jpg")
    r7 = await svc.admin_invalidate_site(bad["site"]["siteId"], "位置不显眼")
    record("test_18_admin_invalidate_site",
           r7["success"] and r7["status"] == "invalid", f"result={r7}")

    # ========================================================
    # 5. 统计与记录
    # ========================================================
    print("\n========== 5. 统计与记录 ==========")

    # test 19: stats 完整性
    stats = await svc.my_stats(m_id)
    record("test_19_stats_complete",
           stats["activeSiteCount"] >= 1 and stats["totalCheckinCount"] >= 2
           and stats["totalCheckinReward"] >= 4.0
           and stats["checkinReward"] == 2.0
           and stats["monthRewardPoster"] == 20.0
           and stats["monthRewardSticker"] == 30.0,
           f"stats={stats}")

    # test 20: 我的点位列表(附满月标记)
    sites = await svc.my_sites(m_id)
    record("test_20_my_sites_with_progress",
           len(sites) >= 5 and all("activeDays" in s for s in sites),
           f"count={len(sites)}")

    # test 21: 打卡记录列表
    checkins = await svc.my_checkins(m_id)
    record("test_21_my_checkins",
           len(checkins) >= 2 and all("aiScore" in c for c in checkins),
           f"count={len(checkins)}")

    # ========================================================
    # 6. 参数管理与规则
    # ========================================================
    print("\n========== 6. 参数管理与规则 ==========")

    # test 22: 默认参数(先还原临时放宽的上限)
    await svc.admin_update_settings({"maxActiveSites": 5},
                                    updated_by="tester")
    settings = await svc.admin_get_settings()
    record("test_22_default_settings",
           settings["checkinReward"] == 2.0
           and settings["monthRewardPoster"] == 20.0
           and settings["monthRewardSticker"] == 30.0
           and settings["maxActiveSites"] == 5
           and settings["durationDays"] == 30,
           f"settings={settings}")

    # test 23: 修改参数并即时生效
    await svc.admin_update_settings({"checkinReward": 3.0,
                                     "monthRewardSticker": 35.0},
                                    updated_by="tester")
    settings2 = await svc.admin_get_settings()
    record("test_23_update_settings",
           settings2["checkinReward"] == 3.0
           and settings2["monthRewardSticker"] == 35.0,
           f"settings={settings2}")

    # test 24: 非法参数值拒绝
    ok, msg = await _expect_value_error(
        svc.admin_update_settings({"checkinReward": -1}), "区间")
    record("test_24_invalid_settings_rejected", ok, msg)

    # test 25: 规则接口(公开)
    rules = await svc.get_rules()
    record("test_25_rules_public",
           rules["checkinReward"] == 3.0 and "ZXBJ" in rules["scanRewardTip"]
           and rules["scenes"]["taxi_rear"] == "sticker",
           f"rules={rules}")

    # 还原参数
    await svc.admin_update_settings({"checkinReward": 2.0,
                                     "monthRewardSticker": 30.0},
                                    updated_by="tester")

    # ========================================================
    # 汇总
    # ========================================================
    print("\n".join(RESULTS))
    print("\n" + "=" * 60)
    print(f"  顺手赚钱模块端到端测试: 通过 {PASS} / 失败 {FAIL} / 总计 {PASS + FAIL}")
    print("=" * 60)
    return FAIL


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    raise SystemExit(exit_code)
