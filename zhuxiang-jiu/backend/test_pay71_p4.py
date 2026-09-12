"""71号·AI智能支付端口大模型 P4 专项测试
(对账自愈: T+0 三向核验+幂等域自动
补单+人工转诊+延迟差错基线)

运行方式:
    python test_pay71_p4.py

覆盖(71号规划 §七 P4/§4.5):
    - 注册表 P4 扩展封闭: 核验源/
      差错分类/补单状态机/自动处置
      映射(资金类永不自动——自检级
      铁律)/退避序列递增
    - T+0 三向核验: 金额三方匹配
      (0.01 容差)/matched 态/差错
      四类确定性分类(amount_diff/
      timeout_no_callback/
      duplicate_charge/partial_refund)
    - 幂等域补单: timeout→retrying
      (attempt 1)/防重键幂等(同单
      同差错二连 heal 返回同一记录)/
      重试成功→auto_healed/失败退避
      →上限耗尽→failed 转人工
    - 人工转诊铁律: amount_diff/
      duplicate_charge/partial_refund
      →manual_referral(pay60_P3)
      直接、无重试
    - 资金类不可重试(409 铁律)
    - 延迟差错基线: totalCount/heal
      Count/diffCount 统计/延迟档
      instant-fast-slow/avgCallback
    - 决策面 off 409 + 观测面不受影响
    - QC: 69号注册表零改动(叠加铁律)
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ["II58_MODE"] = "off"
os.environ["II59_MODE"] = "off"
os.environ["AB63_MODE"] = "off"
os.environ["PAY60_MODE"] = "off"
os.environ["PAY69_MODE"] = "off"
os.environ["PAY71_MODE"] = "off"
os.environ.pop("PAY71_KILL", None)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/pay71"

    print("[01 注册表 P4 扩展封闭]")

    from services import pay71_registry as reg

    record("三向核验源域封闭",
           set(reg.VERIFY_SOURCES) == {
               "order", "flow", "receipt"})
    record("核验结果域封闭",
           set(reg.VERIFY_STATES) == {
               "matched", "mismatch"})
    record("差错分类四类封闭",
           set(reg.DISCREPANCY_KINDS) == {
               "amount_diff",
               "timeout_no_callback",
               "duplicate_charge",
               "partial_refund"})
    record("补单状态机五态封闭",
           set(reg.RECON_STATES) == {
               "pending", "retrying",
               "auto_healed",
               "manual_referral",
               "failed"})
    record("自动处置仅幂等修复类"
           "(timeout)",
           reg.RECON_AUTO_KINDS == (
               "timeout_no_callback",))
    record("资金类差错永不自动"
           "(自检级铁律)",
           "amount_diff" not in
           reg.RECON_AUTO_KINDS
           and "duplicate_charge"
           not in reg.RECON_AUTO_KINDS
           and "partial_refund"
           not in reg.RECON_AUTO_KINDS)
    record("补单重试上限 3+退避递增",
           reg.RECON_RETRY_MAX == 3
           and reg.RECON_RETRY_BACKOFF_S
           == (60, 300, 900))
    record("延迟档三档递增",
           [b[1] for b in
            reg.RECON_DELAY_BANDS] == [
               "instant", "fast", "slow"])
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 对账字典+决策面 off]")

    r = client.get(f"{BASE}/recon/dict",
                   headers=ADMIN)
    body = r.json()
    record("对账字典 200",
           r.status_code == 200
           and body["sources"] == [
               "order", "flow", "receipt"]
           and body["retryMax"] == 3,
           f"s={r.status_code}")
    record("字典含资金铁律声明",
           "60号 P3 人工"
           in body["fundsIronRule"]
           and "幂等修复域"
           in body["fundsIronRule"])
    record("字典人工类=资金类三差错",
           set(body["manualKinds"]) == {
               "amount_diff",
               "duplicate_charge",
               "partial_refund"})

    r = client.post(f"{BASE}/recon/verify",
                    headers=ADMIN, json={
        "orderId": "ORD-1",
        "orderAmount": 100,
        "flowAmount": 100,
        "receiptAmount": 100})
    record("核验 off=409(决策面)",
           r.status_code == 409, f"s={r.status_code}")

    print("[03 T+0 三向核验(确定性)]")

    os.environ["PAY71_MODE"] = "shadow"
    try:
        # 一致样本
        r = client.post(f"{BASE}/recon/verify",
                        headers=ADMIN, json={
            "orderId": "ORD-100",
            "orderAmount": 299.00,
            "flowAmount": 299.00,
            "receiptAmount": 299.00,
            "portId": "wechat"})
        body = r.json()
        record("三方一致→matched",
               r.status_code == 200
               and body["state"] == "matched"
               and body["amountMatch"] is True
               and body["discrepancyKind"] == "",
               f"s={r.status_code} b={body}")
        matched_seq = body["verifySeq"]

        # 金额不符(flow 低于 order)
        r = client.post(f"{BASE}/recon/verify",
                        headers=ADMIN, json={
            "orderId": "ORD-101",
            "orderAmount": 500.00,
            "flowAmount": 460.00,
            "receiptAmount": 460.00,
            "portId": "alipay"})
        body = r.json()
        record("回执/流水低且一致→"
               "partial_refund",
               body["state"] == "mismatch"
               and body["discrepancyKind"]
               == "partial_refund",
               str(body.get(
                   "discrepancyKind")))

        # 金额不符(流水+回执高于订单)
        r = client.post(f"{BASE}/recon/verify",
                        headers=ADMIN, json={
            "orderId": "ORD-102",
            "orderAmount": 100.00,
            "flowAmount": 200.00,
            "receiptAmount": 200.00,
            "portId": "bank"})
        body = r.json()
        record("流水+回执>订单→"
               "duplicate_charge",
               body["state"] == "mismatch"
               and body["discrepancyKind"]
               == "duplicate_charge",
               str(body.get(
                   "discrepancyKind")))
        dup_seq = body["verifySeq"]

        # 一般金额不符(不规则偏差——
        # 流水高回执低, 方向不一致)
        r = client.post(f"{BASE}/recon/verify",
                        headers=ADMIN, json={
            "orderId": "ORD-103",
            "orderAmount": 300.00,
            "flowAmount": 350.00,
            "receiptAmount": 250.00,
            "portId": "unionpay"})
        body = r.json()
        record("不规则金额偏差→"
               "amount_diff",
               body["state"] == "mismatch"
               and body["discrepancyKind"]
               == "amount_diff",
               str(body.get(
                   "discrepancyKind")))
        diff_seq = body["verifySeq"]

        # 超时未回调(金额一致但时间窗超)
        from datetime import UTC, \
            datetime, timedelta
        stale = (datetime.now(UTC)
                 - timedelta(
                     hours=2)).isoformat()
        r = client.post(f"{BASE}/recon/verify",
                        headers=ADMIN, json={
            "orderId": "ORD-104",
            "orderAmount": 88.00,
            "flowAmount": 88.00,
            "receiptAmount": 88.00,
            "portId": "wechat",
            "receiptAt": stale})
        body = r.json()
        record("金额一致+时间窗超→"
               "timeout_no_callback",
               body["state"] == "mismatch"
               and body["discrepancyKind"]
               == "timeout_no_callback",
               str(body.get(
                   "discrepancyKind")))
        record("时间偏移留痕"
               "(receiptOffsetS≈7200)",
               7100 < body[
                   "receiptOffsetS"]
               < 7300,
               str(body.get(
                   "receiptOffsetS")))
        timeout_seq = body["verifySeq"]

        # 空时间戳=不校验时间窗(即时态)
        r = client.post(f"{BASE}/recon/verify",
                        headers=ADMIN, json={
            "orderId": "ORD-105",
            "orderAmount": 66.00,
            "flowAmount": 66.00,
            "receiptAmount": 66.00,
            "portId": "qr"})
        record("空时间戳→matched"
               "(不校验口径)",
               r.json()["state"]
               == "matched")

        # 入参校验
        r = client.post(f"{BASE}/recon/verify",
                        headers=ADMIN, json={
            "orderId": "  ",
            "orderAmount": 10,
            "flowAmount": 10,
            "receiptAmount": 10})
        record("空订单号 409",
               r.status_code == 409, f"s={r.status_code}")

        r = client.post(f"{BASE}/recon/verify",
                        headers=ADMIN, json={
            "orderId": "ORD-X",
            "orderAmount": 10,
            "flowAmount": 0,
            "receiptAmount": 10})
        record("零金额 422(Pydantic)",
               r.status_code == 422, f"s={r.status_code}")

        r = client.post(f"{BASE}/recon/verify",
                        headers=ADMIN, json={
            "orderId": "ORD-X",
            "orderAmount": 10,
            "flowAmount": 10,
            "receiptAmount": 10,
            "portId": "nonexist"})
        record("端口域外 409",
               r.status_code == 409, f"s={r.status_code}")
    finally:
        os.environ["PAY71_MODE"] = "off"

    print("[04 幂等域自动补单(timeout)]")

    os.environ["PAY71_MODE"] = "shadow"
    try:
        r = client.post(f"{BASE}/recon/heal",
                        headers=ADMIN, json={
            "verifySeq": timeout_seq})
        body = r.json()
        record("timeout→retrying"
               "(attempt 1)",
               r.status_code == 200
               and body["state"]
               == "retrying"
               and body["retryAttempt"]
               == 1
               and body["autoHealed"]
               is True,
               f"s={r.status_code} b={body}")
        record("防重键+退避序列齐备",
               len(body["idempotentKey"])
               == 20
               and body["backoffS"] == [
                   60, 300, 900])
        recon_seq = body["reconSeq"]

        # 幂等防重: 同核验二连 heal
        # →返回同一补单记录
        r2 = client.post(f"{BASE}/recon/heal",
                         headers=ADMIN, json={
            "verifySeq": timeout_seq})
        record("同单同差错幂等防重"
               "(二连 heal 同记录)",
               r2.json()["reconSeq"]
               == recon_seq,
               f"{r2.json().get('reconSeq')}"
               f" vs {recon_seq}")

        # 重试失败两次→第三次失败耗尽
        client.post(f"{BASE}/recon/retry",
                    headers=ADMIN, json={
        "reconSeq": recon_seq,
        "success": False})
        r = client.post(f"{BASE}/recon/"
                        "retry",
                        headers=ADMIN, json={
        "reconSeq": recon_seq,
        "success": False})
        body = r.json()
        record("两次失败→failed"
               "(上限 3 耗尽转人工)",
               body["state"] == "failed"
               and body["retryAttempt"] == 3
               and "pay60_P3_manual"
               in body["referral"],
               f"b={body}")

        # 另一 timeout 样本: 重试成功
        r = client.post(f"{BASE}/recon/"
                        "verify",
                        headers=ADMIN, json={
        "orderId": "ORD-106",
        "orderAmount": 199.00,
        "flowAmount": 199.00,
        "receiptAmount": 199.00,
        "portId": "alipay",
        "receiptAt": stale})
        ok_timeout_seq = r.json()[
            "verifySeq"]
        r = client.post(f"{BASE}/recon/heal",
                        headers=ADMIN, json={
            "verifySeq": ok_timeout_seq})
        ok_recon = r.json()["reconSeq"]
        r = client.post(f"{BASE}/recon/"
                        "retry",
                        headers=ADMIN, json={
        "reconSeq": ok_recon,
        "success": True})
        body = r.json()
        record("重试成功→auto_healed",
               body["state"]
               == "auto_healed"
               and body["retryAttempt"]
               == 1,
               f"b={body}")

        # 非 retrying 态重试 409
        r = client.post(f"{BASE}/recon/"
                        "retry",
                        headers=ADMIN, json={
        "reconSeq": ok_recon,
        "success": True})
        record("已愈合再重试 409",
               r.status_code == 409, f"s={r.status_code}")
    finally:
        os.environ["PAY71_MODE"] = "off"

    print("[05 人工转诊铁律(资金类)]")

    os.environ["PAY71_MODE"] = "shadow"
    try:
        for seq, _kind, label in (
            (diff_seq, "amount_diff",
             "金额不符"),
            (dup_seq, "duplicate_charge",
             "重复扣款")):
            r = client.post(
                f"{BASE}/recon/heal",
                headers=ADMIN, json={
                "verifySeq": seq})
            body = r.json()
            record(f"{label}→manual_"
                   f"referral(直接转人工)",
                   body["state"]
                   == "manual_referral"
                   and body["autoHealed"]
                   is False
                   and body[
                       "referral"].startswith(
                       "pay60_P3"),
                   f"b={body}")
            record(f"{label} 无重试"
                   f"(attempt 0)",
                   body["retryAttempt"] == 0)

        # partial_refund 补测(heal 后
        # 资金类不可重试 409)
        r = client.post(f"{BASE}/recon/"
                        "verify",
                        headers=ADMIN, json={
        "orderId": "ORD-107",
        "orderAmount": 400.00,
        "flowAmount": 380.00,
        "receiptAmount": 380.00,
        "portId": "wechat"})
        pr_seq = r.json()["verifySeq"]
        r = client.post(f"{BASE}/recon/heal",
                        headers=ADMIN, json={
            "verifySeq": pr_seq})
        pr_recon = r.json()["reconSeq"]
        record("partial_refund→"
               "manual_referral",
               r.json()["state"]
               == "manual_referral")
        r = client.post(f"{BASE}/recon/"
                        "retry",
                        headers=ADMIN, json={
        "reconSeq": pr_recon,
        "success": True})
        record("资金类不可重试 409"
               "(人工铁律)",
               r.status_code == 409, f"s={r.status_code}")

        # matched 无需处置
        r = client.post(f"{BASE}/recon/heal",
                        headers=ADMIN, json={
            "verifySeq": matched_seq})
        record("matched 处置 409"
               "(无差异)",
               r.status_code == 409, f"s={r.status_code}")

        r = client.post(f"{BASE}/recon/heal",
                        headers=ADMIN, json={
            "verifySeq": 999})
        record("核验不存在 404",
               r.status_code == 404, f"s={r.status_code}")
    finally:
        os.environ["PAY71_MODE"] = "off"

    print("[06 延迟差错基线(快环统计)]")

    r = client.get(f"{BASE}/recon/baseline",
                   headers=ADMIN)
    body = r.json()
    wechat = next(
        (p for p in body["ports"]
         if p["portId"] == "wechat"),
        None)
    # wechat 样本: ORD-100 matched/
    # ORD-104 timeout(2h 迟)/
    # ORD-107 partial(无时间戳)
    record("wechat 统计落库"
           "(total=3)",
           wechat and wechat[
               "totalCount"] == 3
           and wechat["healCount"] == 1
           and wechat["diffCount"] == 2,
           str(wechat))
    record("healRate≈0.333 口径",
           wechat and abs(
               wechat["healRate"]
               - 0.3333) < 1e-3,
           str(wechat))
    record("延迟档观测"
           "(avgCallback=7200/3=2400→fast)",
           wechat is not None
           and wechat["delayBand"]
           == "fast"
           and abs(wechat[
               "avgCallbackSeconds"]
               - 2400.0) < 1.0,
           str(wechat))
    alipay = next(
        (p for p in body["ports"]
         if p["portId"] == "alipay"),
        None)
    # alipay 样本: ORD-101 partial/
    # ORD-106 timeout(2h 迟)
    record("alipay 统计落库"
           "(total=2)",
           alipay and alipay[
               "totalCount"] == 2
           and alipay["diffCount"] == 2,
           str(alipay))

    print("[07 观测视图(verifies/records)]")

    r = client.get(f"{BASE}/recon/verifies",
                   headers=ADMIN)
    body = r.json()
    record("核验留痕视图(全量 8)",
           body["count"] == 8,
           f"count={body['count']}")

    r = client.get(f"{BASE}/recon/verifies",
                   headers=ADMIN,
                   params={"state":
                           "mismatch"})
    record("留痕按结果过滤"
           "(mismatch 6)",
           r.json()["count"] == 6
           and all(
               rec["state"]
               == "mismatch"
               for rec in
               r.json()["records"]),
           f"count={r.json()['count']}")

    r = client.get(f"{BASE}/recon/records",
                   headers=ADMIN)
    body = r.json()
    # 5 笔: ORD-104 timeout(failed——
    # 两连失败耗尽)/ORD-106 timeout
    # (auto_healed)/ORD-103 amount(
    # manual)/ORD-102 dup(manual)/
    # ORD-107 partial(manual)
    record("补单账本视图(5 笔)",
           body["count"] == 5,
           f"count={body['count']}")
    states = {rec["state"]
              for rec in
              body["records"]}
    record("状态域覆盖"
           "(auto_healed/failed/"
           "manual_referral)",
           states == {
               "auto_healed",
               "failed",
               "manual_referral"},
           str(states))

    r = client.get(f"{BASE}/recon/records",
                   headers=ADMIN,
                   params={"state":
                           "auto_healed"})
    record("账本按状态过滤",
           r.json()["count"] == 1
           and r.json()["records"][0][
               "state"]
           == "auto_healed")

    print("[08 QC 叠加铁律(69号零改动)]")

    from services import pay69_registry as \
        reg69
    record("69号注册表零改动(七通道)",
           set(reg69.CHANNEL_REGISTRY)
           == set(reg69.CHANNEL_IDS)
           and len(reg69.CHANNEL_IDS) == 7)
    record("69号路由权重零改动",
           reg69.ROUTE_WEIGHTS == {
               "fee": 0.15, "health": 0.35,
               "affinity": 0.35,
               "habit": 0.15})

    r = client.get("/api/pay69/channels",
                   headers=ADMIN)
    record("69号端点正常(200——无回归)",
           r.status_code == 200
           and r.json()["channelCount"] == 7,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/ports",
                   headers=ADMIN)
    record("71号 P0 端点正常(无回归)",
           r.status_code == 200
           and r.json()["portCount"] == 7,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/predict/dict",
                   headers=ADMIN)
    record("71号 P2 端点正常(无回归)",
           r.status_code == 200
           and r.json()["splitThreshold"]
           == 5000.0)

    r = client.get(f"{BASE}/allocation/"
                  "dict",
                  headers=ADMIN)
    record("71号 P3 端点正常(无回归)",
           r.status_code == 200
           and "compliance"
           in r.json()["dimensions"])

    total = PASS + FAIL
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败"
          f" (共 {total})")
    print("-" * 62)
    if FAIL:
        for line in RESULTS:
            if "✗" in line:
                print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
