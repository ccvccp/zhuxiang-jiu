"""trace 模块 CRC 校验升级专项测试(P2 正确性修复)

运行:
    python test_trace_crc.py

背景:
    旧口径 format(abs(hash(raw)) % 0xFFFF, "04X") 受 PYTHONHASHSEED
    随机化影响跨进程不确定 → 离线校验必失效; 升级 CRC16-CCITT
    (binascii.crc_hqx, 纯标准库跨进程确定) + v1/v2 双格式兼容。

覆盖:
    1. CRC16 纯函数: 确定性/与 binascii 直算一致/4 位 hex 格式
    2. 跨进程确定性实证: 两个不同 PYTHONHASHSEED 子进程算同 raw
       结果一致(旧口径两进程必不同——修复核心证明)
    3. 生成侧: 新码 crcVersion=2 + 离线校验通过 + 同 raw 重算一致
    4. 篡改检测: 改码面任一段(产品/批次/序号/校验位) → 离线校验 False
    5. scan_trace: 正常码 crcValid=True/crcVersion=2; 篡改码 404
    6. 存量 v1 兼容: 直插旧口径码(无 crcVersion) → 查询正常,
       crcValid=None(不适用), 不被预检拦截
    7. 严格模式: TRACE_CRC_STRICT=on 伪造码 scan/activate 拦截
    8. activate 预检同口径(观测不阻断)
"""
import asyncio
import os
import subprocess
import sys

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


# ============================================================
# 1/2. 纯函数 + 跨进程确定性
# ============================================================

def run_pure_tests():
    print("[01 CRC 纯函数与跨进程确定性]")
    import binascii
    from services.trace_service import crc16_ccitt, verify_life_crc, legacy_crc4

    raw = "ZX42-2026L07-B20260901-000001"
    c1 = crc16_ccitt(raw)
    c2 = crc16_ccitt(raw)
    check("同进程两次计算一致", c1 == c2 and len(c1) == 4, str((c1, c2)))
    check("与 binascii 直算一致",
          c1 == format(binascii.crc_hqx(raw.encode(), 0xFFFF), "04X"), c1)
    check("4 位大写 hex", len(c1) == 4
          and all(ch in "0123456789ABCDEF" for ch in c1), c1)

    # 跨进程确定性: 两个不同 PYTHONHASHSEED 的子进程
    snippet = (
        "import binascii,hashlib;"
        "raw='ZX42-2026L07-B20260901-000001';"
        "print(format(binascii.crc_hqx(raw.encode(),0xFFFF),'04X'),"
        "format(abs(hash(raw))%0xFFFF,'04X'))"
    )
    outs = []
    for seed in ("1", "424242"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        r = subprocess.run([sys.executable, "-c", snippet],
                           capture_output=True, text=True, env=env,
                           cwd=os.path.dirname(os.path.abspath(__file__)))
        outs.append(r.stdout.strip())
    crc_a, legacy_a = outs[0].split()
    crc_b, legacy_b = outs[1].split()
    check("跨进程 CRC16 一致(修复核心)",
          crc_a == crc_b == c1, str((crc_a, crc_b, c1)))
    check("跨进程旧口径不一致(问题实证)", legacy_a != legacy_b,
          str((legacy_a, legacy_b)))

    # 离线校验函数
    good = f"BLC-ZX42-2026L07-B20260901-000001-{c1}"
    check("verify: 正确码通过", verify_life_crc(good) is True, good)
    check("verify: 校验位篡改拒绝",
          verify_life_crc(good[:-1] + ("0" if c1[-1] != "0" else "1"))
          is False)
    check("verify: 序号篡改拒绝",
          verify_life_crc(good.replace("000001", "000002")) is False)
    check("verify: 非生命码前缀拒绝", verify_life_crc("TBC-X-1") is False)
    check("legacy 口径保留(迁移工具用)", len(legacy_crc4(raw)) == 4)


# ============================================================
# 3-8. 服务层(HTTP 语义)
# ============================================================

async def run_service_tests():
    print("[02 生成/扫码/兼容/严格模式]")
    from services.trace_service import (
        TraceService, crc16_ccitt, legacy_crc4, verify_life_crc,
        BRAND_PREFIX,
    )
    from repositories.trace_repository import TraceRepository

    svc = TraceService()
    repo = TraceRepository()

    # 3. 生成侧 v2
    result = await svc.generate_life_codes(
        "ZX42-2026L07", "B20261012", 3, product_name="竹奕 42°")
    lifes = result["lifeCodes"]
    check("生成 3 码且 crcVersion=2",
          len(lifes) == 3 and all(l.get("crcVersion") == 2 for l in lifes),
          str([l.get("crcVersion") for l in lifes]))
    code0 = lifes[0]["lifeCode"]
    check("新码离线校验通过",
          all(verify_life_crc(x["lifeCode"]) for x in lifes), code0)
    check("crcCode 与码面尾段一致",
          all(x["lifeCode"].endswith("-" + x["crcCode"]) for x in lifes))
    # 同 raw 重算一致(跨批次重启等价场景)
    raw0 = "ZX42-2026L07-B20261012-000001"
    check("生成 CRC 与纯函数重算一致",
          lifes[0]["crcCode"] == crc16_ccitt(raw0),
          str((lifes[0]["crcCode"], crc16_ccitt(raw0))))

    # 4. scan_trace 正常码
    r = await svc.scan_trace(code0)
    check("scan 正常码 crcValid=True/crcVersion=2",
          r.get("crcValid") is True and r.get("crcVersion") == 2,
          str((r.get("crcValid"), r.get("crcVersion"))))

    # 5. 篡改码(改一位) → 查库 404; 预检观测不阻断流程
    tampered = code0[:-1] + ("0" if code0[-1] != "0" else "1")
    try:
        await svc.scan_trace(tampered)
        check("篡改码 scan → 404(KeyError)", False, "未抛出")
    except KeyError:
        check("篡改码 scan → 404(KeyError)", True)

    # 6. 存量 v1 码兼容: 直插旧口径码(无 crcVersion 字段)
    raw_legacy = "ZX52-2026L08-B20250101-000001"
    legacy_code = f"{BRAND_PREFIX}-ZX52-2026L08-B20250101-000001-" \
                  f"{legacy_crc4(raw_legacy)}"
    await repo.create_life_code({
        "lifeCode": legacy_code, "productId": "ZX52-2026L08",
        "productName": "竹奕 52°", "productAbv": 52,
        "productVolume": "500ml", "batchNo": "B20250101",
        "status": "active", "firstActivationDate": "2025-06-01",
        "userId": 99001, "holderName": "存量用户", "crcCode":
            legacy_crc4(raw_legacy), "boxCodeId": None,
        "createdAt": "2025-01-01T00:00:00",
    })
    r = await svc.scan_trace(legacy_code)
    check("存量 v1 码查询正常(不被预检拦截)",
          r.get("code") == legacy_code and r.get("crcVersion") == 1,
          str(r.get("crcVersion")))
    check("存量 v1 码 crcValid=None(离线校验不适用)",
          r.get("crcValid") is None, str(r.get("crcValid")))

    # 7. 严格模式: 伪造码拦截(scan + activate 同口径)
    fake = f"{BRAND_PREFIX}-ZX42-2026L07-B20261012-000001-DEAD"
    os.environ["TRACE_CRC_STRICT"] = "on"
    try:
        await svc.scan_trace(fake)
        check("严格模式 scan 伪造码 → 404", False, "未拦截")
    except KeyError:
        check("严格模式 scan 伪造码 → 404", True)
    try:
        await svc.activate_life_code(fake, 99001)
        check("严格模式 activate 伪造码 → 404", False, "未拦截")
    except KeyError:
        check("严格模式 activate 伪造码 → 404", True)
    # 严格模式下 v1 存量码仍会被 CRC 拦截(其码面 CRC16 必不匹配) →
    # 严格模式须待存量消耗后启用(文档已注明), 本用例验证该语义
    try:
        await svc.scan_trace(legacy_code)
        check("严格模式 v1 码被拦(语义确认: 启用前须清存量)",
              False, "未拦截")
    except KeyError:
        check("严格模式 v1 码被拦(语义确认: 启用前须清存量)", True)
    os.environ["TRACE_CRC_STRICT"] = "off"

    # 8. 默认观测模式下 v1 码 activate 预检不阻断(状态已 active 会 409,
    #    证明预检未先拦截)
    try:
        await svc.activate_life_code(legacy_code, 99001)
        check("观测模式 v1 码 activate 走业务校验(409 已激活)",
              False, "未抛出")
    except ValueError as e:
        check("观测模式 v1 码 activate 走业务校验(409 已激活)",
              "已激活" in str(e), str(e))
    except KeyError:
        check("观测模式 v1 码 activate 走业务校验(409 已激活)",
              False, "被 CRC 预检拦截")


async def main():
    run_pure_tests()
    await run_service_tests()

    print("\n" + "=" * 60)
    print("\n".join(RESULTS))
    print("=" * 60)
    print(f"PASS: {PASS}  FAIL: {FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
