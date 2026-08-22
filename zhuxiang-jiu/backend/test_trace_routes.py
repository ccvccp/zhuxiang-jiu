"""双码追溯管理模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 TraceService 方法, 模拟 12 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_trace_routes.py

覆盖 12 个接口对应的业务方法:
    1. 箱码(3):     generate_box_codes / bind_box_code / get_box_code
    2. 生命码(3):    generate_life_codes / bind_life_to_box / get_life_code
    3. 扫码(2):     scan_trace / get_trace_chain
    4. 防窜(1):     detect_anti_channel
    5. 转让(1):     transfer_life_code
    6. 记录(1):     list_scan_logs
    7. 统计(1):     get_stats

另含 activate_life_code(激活, 转让前置依赖)
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.trace_service import TraceService, ACTIVATION_REWARD_POINTS
from repositories.trace_repository import (
    TraceRepository,
    CODE_TYPE_BOX, CODE_TYPE_LIFE,
    BOX_CODE_TOP, BOX_CODE_BOTTOM,
    LIFE_STATUS_PENDING, LIFE_STATUS_ACTIVE, LIFE_STATUS_TRANSFERRED,
    LIFE_STATUS_RECYCLED, LIFE_STATUS_FROZEN,
    BOX_STATUS_PENDING, BOX_STATUS_BOUND,
    SCAN_TYPE_ACTIVATE, SCAN_TYPE_VERIFY, SCAN_TYPE_TRANSFER, SCAN_TYPE_QUERY,
)
from repositories.store import _mock_store, reset_store as _reset_store_impl

# 测试结果收集
PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_store():
    """重置内存存储, 保证测试隔离"""
    _reset_store_impl()


# ============================================================
# 测试数据
# ============================================================

USER_ID_1 = 1001
USER_ID_2 = 1002
USER_ID_3 = 1003
PRODUCT_ID_1 = "ZX42-2026L07"
PRODUCT_ID_2 = "ZX52-2026L02"
BATCH_NO_1 = "B20260822001"
BATCH_NO_2 = "B20260822002"
AGENT_ID_1 = 2001
AGENT_REGION_1 = "山东省"
AGENT_PROVINCE_1 = "山东省"
AGENT_CITY_1 = "泰安市"


# ============================================================
# 测试用例
# ============================================================

class TestBoxGenerate:
    """箱码生成测试"""

    async def run(self, svc):
        # test 1: 正常生成单箱码(TBC+BBC双码)
        result = await svc.generate_box_codes(
            PRODUCT_ID_1, BATCH_NO_1, 1, AGENT_ID_1, AGENT_REGION_1
        )
        record("test_01_generate_single_box",
               result["count"] == 1 and len(result["boxes"]) == 1,
               f"expected count=1, got {result['count']}")

        # test 2: TBC箱顶码格式校验
        box = result["boxes"][0]
        expected_top = f"{BOX_CODE_TOP}-{PRODUCT_ID_1}-{BATCH_NO_1}-000001"
        record("test_02_box_top_code_format",
               box["boxCode"] == expected_top,
               f"expected {expected_top}, got {box['boxCode']}")

        # test 3: BBC箱底码格式校验
        expected_bottom = f"{BOX_CODE_BOTTOM}-{PRODUCT_ID_1}-{BATCH_NO_1}-000001"
        record("test_03_box_bottom_code_format",
               box["boxBottomCode"] == expected_bottom,
               f"expected {expected_bottom}, got {box['boxBottomCode']}")

        # test 4: 初始状态为pending
        record("test_04_initial_status_pending",
               box["status"] == BOX_STATUS_PENDING,
               f"expected {BOX_STATUS_PENDING}, got {box['status']}")

        # test 5: 批量生成10箱码
        result = await svc.generate_box_codes(
            PRODUCT_ID_1, BATCH_NO_2, 10
        )
        record("test_05_batch_generate",
               result["count"] == 10 and len(result["boxes"]) == 10,
               f"expected count=10, got {result['count']}")

        # test 6: 序号递增校验
        seqs = [b["boxCode"].split("-")[-1] for b in result["boxes"]]
        expected_seqs = [f"{i:06d}" for i in range(1, 11)]
        record("test_06_sequence_increment",
               seqs == expected_seqs,
               f"expected {expected_seqs}, got {seqs}")

        # test 7: 数量无效(0)
        try:
            await svc.generate_box_codes(PRODUCT_ID_1, BATCH_NO_1, 0)
            record("test_07_zero_count", False, "应抛出ValueError")
        except ValueError:
            record("test_07_zero_count", True)

        # test 8: 数量超限(1001)
        try:
            await svc.generate_box_codes(PRODUCT_ID_1, BATCH_NO_1, 1001)
            record("test_08_over_limit", False, "应抛出ValueError")
        except ValueError:
            record("test_08_over_limit", True)


class TestBoxBind:
    """箱码绑定测试"""

    async def run(self, svc):
        # 准备: 生成箱码 + 生命码
        box_result = await svc.generate_box_codes(PRODUCT_ID_1, BATCH_NO_1, 1)
        box_id = box_result["boxes"][0]["id"]
        life_result = await svc.generate_life_codes(PRODUCT_ID_1, BATCH_NO_1, 3)
        life_ids = [l["id"] for l in life_result["lifeCodes"]]

        # test 9: 正常绑定箱码+生命码
        result = await svc.bind_box_code(box_id, life_ids)
        record("test_09_bind_box_success",
               result["status"] == BOX_STATUS_BOUND,
               f"expected {BOX_STATUS_BOUND}, got {result['status']}")

        # test 10: 绑定后lifeCodeIds更新
        record("test_10_life_code_ids_updated",
               result["lifeCodeIds"] == life_ids,
               f"expected {life_ids}, got {result['lifeCodeIds']}")

        # test 11: 绑定不存在的箱码
        try:
            await svc.bind_box_code(99999, life_ids)
            record("test_11_bind_nonexistent_box", False, "应抛出KeyError")
        except KeyError:
            record("test_11_bind_nonexistent_box", True)

        # test 12: 重复绑定(状态非法)
        try:
            await svc.bind_box_code(box_id, life_ids)
            record("test_12_duplicate_bind", False, "应抛出ValueError")
        except ValueError:
            record("test_12_duplicate_bind", True)

        # test 13: 绑定不存在的生命码
        box_result2 = await svc.generate_box_codes(PRODUCT_ID_1, BATCH_NO_2, 1)
        box_id2 = box_result2["boxes"][0]["id"]
        try:
            await svc.bind_box_code(box_id2, [99999])
            record("test_13_bind_nonexistent_life", False, "应抛出KeyError")
        except KeyError:
            record("test_13_bind_nonexistent_life", True)

        # test 14: 查询箱码
        box = await svc.get_box_code(box_id)
        record("test_14_get_box_code",
               box["id"] == box_id and box["status"] == BOX_STATUS_BOUND,
               f"expected id={box_id}/bound, got {box.get('id')}/{box.get('status')}")

        # test 15: 查询不存在的箱码
        try:
            await svc.get_box_code(99999)
            record("test_15_get_nonexistent_box", False, "应抛出KeyError")
        except KeyError:
            record("test_15_get_nonexistent_box", True)


class TestLifeGenerate:
    """生命码生成测试"""

    async def run(self, svc):
        # test 16: 正常生成生命码
        result = await svc.generate_life_codes(
            PRODUCT_ID_1, BATCH_NO_1, 1,
            product_name="竹香酒42度", product_abv=42, product_volume="500ml"
        )
        record("test_16_generate_life_success",
               result["count"] == 1 and len(result["lifeCodes"]) == 1,
               f"expected count=1, got {result['count']}")

        # test 17: BLC编码格式校验(BLC-{产品}-{批次}-{序号}-{CRC})
        life = result["lifeCodes"][0]
        code = life["lifeCode"]
        record("test_17_blc_code_format",
               code.startswith("BLC-") and PRODUCT_ID_1 in code and BATCH_NO_1 in code,
               f"unexpected format: {code}")

        # test 18: CRC校验码生成
        record("test_18_crc_code",
               len(life["crcCode"]) == 4 and life["lifeCode"].endswith("-" + life["crcCode"]),
               f"unexpected crc: {life['crcCode']}")

        # test 19: 初始状态为pending
        record("test_19_initial_status_pending",
               life["status"] == LIFE_STATUS_PENDING,
               f"expected {LIFE_STATUS_PENDING}, got {life['status']}")

        # test 20: 批量生成5个生命码
        result = await svc.generate_life_codes(PRODUCT_ID_2, BATCH_NO_2, 5)
        record("test_20_batch_generate_life",
               result["count"] == 5,
               f"expected count=5, got {result['count']}")

        # test 21: 唯一性校验(不同生命码)
        codes = [l["lifeCode"] for l in result["lifeCodes"]]
        record("test_21_life_code_unique",
               len(set(codes)) == len(codes),
               f"duplicate codes found: {codes}")

        # test 22: 数量无效
        try:
            await svc.generate_life_codes(PRODUCT_ID_1, BATCH_NO_1, 0)
            record("test_22_zero_count", False, "应抛出ValueError")
        except ValueError:
            record("test_22_zero_count", True)


class TestLifeBind:
    """生命码绑定测试"""

    async def run(self, svc):
        # 准备: 生成箱码 + 生命码
        box_result = await svc.generate_box_codes(PRODUCT_ID_1, BATCH_NO_1, 1)
        box_id = box_result["boxes"][0]["id"]
        life_result = await svc.generate_life_codes(PRODUCT_ID_1, BATCH_NO_1, 1)
        life_id = life_result["lifeCodes"][0]["id"]

        # test 23: 绑定生命码到箱码
        result = await svc.bind_life_to_box(life_id, box_id)
        record("test_23_bind_life_success",
               result["boxCodeId"] == box_id,
               f"expected boxCodeId={box_id}, got {result['boxCodeId']}")

        # test 24: 绑定不存在的生命码
        try:
            await svc.bind_life_to_box(99999, box_id)
            record("test_24_bind_nonexistent_life", False, "应抛出KeyError")
        except KeyError:
            record("test_24_bind_nonexistent_life", True)

        # test 25: 绑定不存在的箱码
        life_result2 = await svc.generate_life_codes(PRODUCT_ID_1, BATCH_NO_1, 1)
        life_id2 = life_result2["lifeCodes"][0]["id"]
        try:
            await svc.bind_life_to_box(life_id2, 99999)
            record("test_25_bind_nonexistent_box", False, "应抛出KeyError")
        except KeyError:
            record("test_25_bind_nonexistent_box", True)

        # test 26: 查询生命码
        life = await svc.get_life_code(life_id)
        record("test_26_get_life_code",
               life["id"] == life_id and life["boxCodeId"] == box_id,
               f"expected id={life_id}/box={box_id}")

        # test 27: 查询不存在的生命码
        try:
            await svc.get_life_code(99999)
            record("test_27_get_nonexistent_life", False, "应抛出KeyError")
        except KeyError:
            record("test_27_get_nonexistent_life", True)


class TestActivate:
    """扫码激活测试"""

    async def run(self, svc):
        # 准备: 生成生命码
        life_result = await svc.generate_life_codes(PRODUCT_ID_1, BATCH_NO_1, 1)
        life_code = life_result["lifeCodes"][0]["lifeCode"]
        life_id = life_result["lifeCodes"][0]["id"]

        # test 28: 首次激活成功
        result = await svc.activate_life_code(
            life_code, USER_ID_1,
            user_phone="13800000001", user_name="测试用户1",
            province=AGENT_PROVINCE_1, city=AGENT_CITY_1,
            purchase_channel="online", purchase_price=536.0
        )
        record("test_28_activate_success",
               result["status"] == LIFE_STATUS_ACTIVE,
               f"expected {LIFE_STATUS_ACTIVE}, got {result['status']}")

        # test 29: 激活日期记录
        record("test_29_activation_date_recorded",
               result["firstActivationDate"] is not None,
               f"expected date, got {result['firstActivationDate']}")

        # test 30: 激活奖励积分
        record("test_30_activation_reward",
               result["rewardPoints"] == ACTIVATION_REWARD_POINTS,
               f"expected {ACTIVATION_REWARD_POINTS}, got {result['rewardPoints']}")

        # test 31: 重复激活(冲突)
        try:
            await svc.activate_life_code(life_code, USER_ID_1)
            record("test_31_duplicate_activate", False, "应抛出ValueError")
        except ValueError:
            record("test_31_duplicate_activate", True)

        # test 32: 激活不存在的生命码
        try:
            await svc.activate_life_code("BLC-INVALID-0000-0000", USER_ID_1)
            record("test_32_activate_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_32_activate_nonexistent", True)

        # test 33: 已回收生命码不可激活
        life_result2 = await svc.generate_life_codes(PRODUCT_ID_1, BATCH_NO_1, 1)
        life2 = life_result2["lifeCodes"][0]
        # 手动标记为已回收(通过repo)
        await svc.repo.update_life_code(life2["id"], {"status": LIFE_STATUS_RECYCLED})
        try:
            await svc.activate_life_code(life2["lifeCode"], USER_ID_1)
            record("test_33_activate_recycled", False, "应抛出ValueError")
        except ValueError:
            record("test_33_activate_recycled", True)

        # test 34: 已冻结生命码不可激活
        life_result3 = await svc.generate_life_codes(PRODUCT_ID_1, BATCH_NO_1, 1)
        life3 = life_result3["lifeCodes"][0]
        await svc.repo.update_life_code(life3["id"], {"status": LIFE_STATUS_FROZEN})
        try:
            await svc.activate_life_code(life3["lifeCode"], USER_ID_1)
            record("test_34_activate_frozen", False, "应抛出ValueError")
        except ValueError:
            record("test_34_activate_frozen", True)


class TestScanTrace:
    """扫码追溯测试"""

    async def run(self, svc):
        # 准备: 生成箱码+生命码+激活
        box_result = await svc.generate_box_codes(PRODUCT_ID_1, BATCH_NO_1, 1)
        box_code = box_result["boxes"][0]["boxCode"]
        life_result = await svc.generate_life_codes(PRODUCT_ID_1, BATCH_NO_1, 1)
        life_code = life_result["lifeCodes"][0]["lifeCode"]
        await svc.activate_life_code(life_code, USER_ID_1, province="山东省", city="泰安市")

        # test 35: 扫码生命码追溯
        result = await svc.scan_trace(life_code, user_id=USER_ID_1)
        record("test_35_scan_life_code",
               result["codeType"] == CODE_TYPE_LIFE and result["code"] == life_code,
               f"expected life/{life_code}, got {result.get('codeType')}/{result.get('code')}")

        # test 36: 扫码箱码追溯
        result = await svc.scan_trace(box_code, user_id=USER_ID_1)
        record("test_36_scan_box_code",
               result["codeType"] == CODE_TYPE_BOX and result["code"] == box_code,
               f"expected box/{box_code}, got {result.get('codeType')}/{result.get('code')}")

        # test 37: 扫码不存在的码
        try:
            await svc.scan_trace("INVALID-CODE-0000")
            record("test_37_scan_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_37_scan_nonexistent", True)

        # test 38: 扫码记录写入(scanId存在)
        result = await svc.scan_trace(life_code, user_id=USER_ID_1, scan_type=SCAN_TYPE_QUERY)
        record("test_38_scan_log_written",
               result.get("scanId") is not None,
               f"expected scanId, got {result.get('scanId')}")


class TestTraceChain:
    """追溯链测试"""

    async def run(self, svc):
        # 准备: 生成+激活(产生扫码记录)
        life_result = await svc.generate_life_codes(PRODUCT_ID_1, BATCH_NO_1, 1)
        life_code = life_result["lifeCodes"][0]["lifeCode"]
        await svc.activate_life_code(life_code, USER_ID_1)

        # test 39: 查询生命码追溯链
        result = await svc.get_trace_chain(life_code)
        record("test_39_life_trace_chain",
               result["codeType"] == CODE_TYPE_LIFE and "traceChain" in result,
               f"unexpected: {result}")

        # test 40: 追溯链含扫码记录
        record("test_40_trace_chain_has_scans",
               len(result["traceChain"]) >= 1,
               f"expected >=1 scan, got {len(result['traceChain'])}")

        # test 41: 查询箱码追溯链
        box_result = await svc.generate_box_codes(PRODUCT_ID_1, BATCH_NO_1, 1)
        box_code = box_result["boxes"][0]["boxCode"]
        # 先扫码箱码产生记录
        await svc.scan_trace(box_code, user_id=USER_ID_1)
        result = await svc.get_trace_chain(box_code)
        record("test_41_box_trace_chain",
               result["codeType"] == CODE_TYPE_BOX,
               f"expected box, got {result.get('codeType')}")

        # test 42: 查询不存在的码
        try:
            await svc.get_trace_chain("NONEXISTENT-CODE")
            record("test_42_chain_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_42_chain_nonexistent", True)


class TestAntiChannel:
    """防窜货检测测试"""

    async def run(self, svc):
        # 准备: 生成箱码(带代理区域)+生命码+绑定+激活
        box_result = await svc.generate_box_codes(
            PRODUCT_ID_1, BATCH_NO_1, 1, AGENT_ID_1, AGENT_REGION_1
        )
        box = box_result["boxes"][0]
        box_id = box["id"]
        # 设置箱码代理省市
        await svc.repo.update_box_code(box_id, {
            "agentProvince": AGENT_PROVINCE_1,
            "agentCity": AGENT_CITY_1,
        })
        life_result = await svc.generate_life_codes(PRODUCT_ID_1, BATCH_NO_1, 1)
        life_code = life_result["lifeCodes"][0]["lifeCode"]
        life_id = life_result["lifeCodes"][0]["id"]
        await svc.bind_life_to_box(life_id, box_id)
        await svc.activate_life_code(life_code, USER_ID_1)

        # test 43: 同区激活(无窜货)
        result = await svc.detect_anti_channel(
            life_code, 117.089, 36.200,
            province=AGENT_PROVINCE_1, city=AGENT_CITY_1
        )
        record("test_43_same_region_no_cross",
               result["isCrossChannel"] is False and result["riskLevel"] == "low",
               f"expected False/low, got {result['isCrossChannel']}/{result['riskLevel']}")

        # test 44: 跨省窜货预警
        result = await svc.detect_anti_channel(
            life_code, 116.407, 39.904,
            province="北京市", city="北京市"
        )
        record("test_44_cross_province",
               result["isCrossChannel"] is True and result["riskLevel"] == "high",
               f"expected True/high, got {result['isCrossChannel']}/{result['riskLevel']}")

        # test 45: 生命码不存在
        try:
            await svc.detect_anti_channel("BLC-INVALID-0000", 116.0, 39.0)
            record("test_45_anti_channel_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_45_anti_channel_nonexistent", True)

        # test 46: 未绑定箱码的生命码(agentRegion为空)
        life_result2 = await svc.generate_life_codes(PRODUCT_ID_1, BATCH_NO_1, 1)
        life_code2 = life_result2["lifeCodes"][0]["lifeCode"]
        await svc.activate_life_code(life_code2, USER_ID_1)
        result = await svc.detect_anti_channel(
            life_code2, 116.0, 39.0, province="北京市", city="北京市"
        )
        record("test_46_no_box_bound",
               result["isCrossChannel"] is False and result["agentRegion"] is None,
               f"expected False/None, got {result['isCrossChannel']}/{result['agentRegion']}")


class TestTransfer:
    """生命码转让测试"""

    async def run(self, svc):
        # 准备: 生成+激活
        life_result = await svc.generate_life_codes(PRODUCT_ID_1, BATCH_NO_1, 1)
        life_code = life_result["lifeCodes"][0]["lifeCode"]
        activation_result = await svc.activate_life_code(life_code, USER_ID_1)
        original_activation_date = activation_result["firstActivationDate"]

        # test 47: 正常转让
        result = await svc.transfer_life_code(
            life_code, USER_ID_1, USER_ID_2,
            to_name="受让人2", transfer_type="gift",
            province="山东省", city="泰安市"
        )
        record("test_47_transfer_success",
               result["status"] == LIFE_STATUS_TRANSFERRED and result["toUserId"] == USER_ID_2,
               f"expected {LIFE_STATUS_TRANSFERRED}/{USER_ID_2}, got {result['status']}/{result['toUserId']}")

        # test 48: 激活日期延续不变(不重置)
        record("test_48_activation_date_preserved",
               result["firstActivationDate"] == original_activation_date,
               f"expected {original_activation_date}, got {result['firstActivationDate']}")

        # test 49: 二次转让(已转让状态可再次转让)
        result = await svc.transfer_life_code(
            life_code, USER_ID_2, USER_ID_3,
            to_name="受让人3", transfer_type="trade"
        )
        record("test_49_second_transfer",
               result["status"] == LIFE_STATUS_TRANSFERRED and result["toUserId"] == USER_ID_3,
               f"expected {LIFE_STATUS_TRANSFERRED}/{USER_ID_3}, got {result['status']}/{result['toUserId']}")

        # test 50: 持有人不匹配
        try:
            await svc.transfer_life_code(life_code, USER_ID_1, USER_ID_2)
            record("test_50_holder_mismatch", False, "应抛出ValueError")
        except ValueError:
            record("test_50_holder_mismatch", True)

        # test 51: 转让未激活的生命码(状态非法)
        life_result2 = await svc.generate_life_codes(PRODUCT_ID_1, BATCH_NO_1, 1)
        life_code2 = life_result2["lifeCodes"][0]["lifeCode"]
        try:
            await svc.transfer_life_code(life_code2, USER_ID_1, USER_ID_2)
            record("test_51_transfer_inactive", False, "应抛出ValueError")
        except ValueError:
            record("test_51_transfer_inactive", True)

        # test 52: 转让不存在的生命码
        try:
            await svc.transfer_life_code("BLC-INVALID-0000", USER_ID_1, USER_ID_2)
            record("test_52_transfer_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_52_transfer_nonexistent", True)


class TestScanLogs:
    """扫码记录查询测试"""

    async def run(self, svc):
        # 准备: 多次扫码产生记录
        life_result = await svc.generate_life_codes(PRODUCT_ID_1, BATCH_NO_1, 2)
        life1 = life_result["lifeCodes"][0]
        life2 = life_result["lifeCodes"][1]
        await svc.activate_life_code(life1["lifeCode"], USER_ID_1)
        await svc.scan_trace(life1["lifeCode"], user_id=USER_ID_1, scan_type=SCAN_TYPE_QUERY)
        await svc.scan_trace(life2["lifeCode"], user_id=USER_ID_2, scan_type=SCAN_TYPE_QUERY)

        # test 53: 查询全部扫码记录
        logs = await svc.list_scan_logs(limit=100)
        record("test_53_list_all_logs",
               len(logs) >= 3,
               f"expected >=3 logs (activate+2 scans), got {len(logs)}")

        # test 54: 按码筛选
        logs = await svc.list_scan_logs(code=life1["lifeCode"])
        record("test_54_filter_by_code",
               all(l["code"] == life1["lifeCode"] for l in logs) and len(logs) >= 2,
               f"expected all matching code, got {len(logs)} logs")

        # test 55: 按用户筛选
        logs = await svc.list_scan_logs(user_id=USER_ID_1)
        record("test_55_filter_by_user",
               all(l["userId"] == USER_ID_1 for l in logs) and len(logs) >= 2,
               f"expected all matching user, got {len(logs)} logs")

        # test 56: 按扫码类型筛选
        logs = await svc.list_scan_logs(scan_type=SCAN_TYPE_ACTIVATE)
        record("test_56_filter_by_scan_type",
               all(l["scanType"] == SCAN_TYPE_ACTIVATE for l in logs) and len(logs) >= 1,
               f"expected activate logs, got {len(logs)}")

        # test 57: limit参数生效
        logs = await svc.list_scan_logs(limit=1)
        record("test_57_limit_param",
               len(logs) == 1,
               f"expected 1 log, got {len(logs)}")


class TestStats:
    """统计测试"""

    async def run(self, svc):
        # 准备数据: 生成箱码+生命码+激活
        await svc.generate_box_codes(PRODUCT_ID_1, BATCH_NO_1, 3)
        life_result = await svc.generate_life_codes(PRODUCT_ID_1, BATCH_NO_1, 5)
        # 激活2个
        await svc.activate_life_code(life_result["lifeCodes"][0]["lifeCode"], USER_ID_1)
        await svc.activate_life_code(life_result["lifeCodes"][1]["lifeCode"], USER_ID_2)

        # test 58: 统计字段完整性
        stats = await svc.get_stats()
        record("test_58_stats_fields",
               all(k in stats for k in ["totalBoxes", "totalLifeCodes", "boxStatusCount",
                                          "lifeStatusCount", "activeCount", "activationRate"]),
               f"missing fields: {stats}")

        # test 59: 统计数量正确
        record("test_59_stats_count",
               stats["totalBoxes"] == 3 and stats["totalLifeCodes"] == 5,
               f"expected 3/5, got {stats['totalBoxes']}/{stats['totalLifeCodes']}")

        # test 60: 激活数正确
        record("test_60_active_count",
               stats["activeCount"] == 2,
               f"expected 2, got {stats['activeCount']}")

        # test 61: 激活率计算
        expected_rate = round(2 / 5, 4)
        record("test_61_activation_rate",
               stats["activationRate"] == expected_rate,
               f"expected {expected_rate}, got {stats['activationRate']}")

        # test 62: 按批次统计
        stats = await svc.get_stats(batch_no=BATCH_NO_1)
        record("test_62_stats_by_batch",
               stats["batchNo"] == BATCH_NO_1 and stats["totalLifeCodes"] == 5,
               f"unexpected: {stats}")

        # test 63: 空批次统计
        stats = await svc.get_stats(batch_no="NONEXISTENT")
        record("test_63_empty_batch_stats",
               stats["totalBoxes"] == 0 and stats["totalLifeCodes"] == 0,
               f"expected 0/0, got {stats['totalBoxes']}/{stats['totalLifeCodes']}")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("双码追溯管理模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestBoxGenerate,
        TestBoxBind,
        TestLifeGenerate,
        TestLifeBind,
        TestActivate,
        TestScanTrace,
        TestTraceChain,
        TestAntiChannel,
        TestTransfer,
        TestScanLogs,
        TestStats,
    ]

    for cls in test_classes:
        reset_store()
        svc = TraceService()
        print(f"[{cls.__name__}]")
        instance = cls()
        await instance.run(svc)
        print()

    # 输出全部结果
    print("=" * 60)
    print("测试结果汇总:")
    print("-" * 60)
    for r in RESULTS:
        print(r)
    print("-" * 60)
    print(f"通过: {PASS}  失败: {FAIL}  总计: {PASS + FAIL}")
    print("=" * 60)

    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
