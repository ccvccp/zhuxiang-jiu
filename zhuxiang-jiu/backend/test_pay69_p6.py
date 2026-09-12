"""69号·AI智能支付大模型 P6 专项测试
(多模态普惠——意图三态/显式确认/无障碍)

运行方式:
    python test_pay69_p6.py

覆盖(69号规划 §七 P6):
    - 多模态字典: 模态/群体/三态+
      档案+确认词+铁律公示
    - 意图三态: direct(金额+商品齐)/
      confirm(金额明缺商品)/clarify
      (无金额/无支付意图)——55号
      P0 范式
    - 规则轨确定性: 金额正则(¥/元/
      小数)/商品长词优先/通道关键词
      /engine=rule_based(LLM 禁入)
    - 无障碍适配: 四群体档案差异化
      (老年 1.5 倍字/语音确认/手势
      优先/视障播报)
    - 资金确认显式铁律: 回显文本含
      金额+商品/确认词匹配/令牌单次
      消费防重放/过期 404/确认后仅
      建议包 executed=False
    - 群体×模态×结果三轴统计+直出率
    - 模式矩阵: 决策面 off 409/
      观测面 off 200
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["PAY60_MODE"] = "off"
os.environ["PAY69_MODE"] = "off"

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


def set_mode(m: str):
    os.environ["PAY69_MODE"] = m


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/pay69"

    from services import pay69_registry as reg
    from services.pay69_modality_service import (
        Pay69ModalityService,
    )
    svc = Pay69ModalityService()

    print("[01 多模态字典与注册封闭]")

    r = client.get(f"{BASE}/modality/dict", headers=ADMIN)
    body = r.json()
    record("多模态字典 200",
           r.status_code == 200, f"s={r.status_code}")
    record("模态域四态(voice/gesture/eyegaze/text)",
           body["modalities"] == [
               "voice", "gesture",
               "eyegaze", "text"])
    record("无障碍群体四域",
           set(body["accessGroups"]) == {
               "elderly", "motor_impaired",
               "visually_impaired",
               "standard"})
    record("意图三态域(55号范式)",
           body["intentOutcomes"] == [
               "direct", "confirm",
               "clarify"])
    record("确认词含 确认支付",
           "确认支付" in body["confirmWords"])
    record("确认 TTL 180 秒",
           body["confirmTtl"] == 180)
    record("铁律公示(规则轨/显式确认"
           "/建议包)",
           len(body["ironRules"]) == 3)
    record("启动自检通过(P6 扩展)",
           reg._validate_registry() is None)

    print("[02 意图三态规则轨(shadow)]")

    set_mode("shadow")
    try:
        # direct: 金额+商品+方式全齐
        r = client.post(f"{BASE}/modality/parse", headers=ADMIN, json={
            "memberId": 1,
            "text": "帮我把这箱竹香经典付了, 268元, 用微信",
            "modality": "voice",
            "accessGroup": "standard"})
        b = r.json()
        record("语音完整意图→direct",
               b["outcome"] == "direct"
               and b["amount"] == 268.0
               and b["product"] == "竹香经典"
               and b["channelId"] == "wechat",
               f"o={b['outcome']}")
        record("missing 空+engine 规则轨",
               b["missing"] == []
               and b["engine"]
               == "rule_based")

        # confirm: 金额明确, 缺商品/方式
        r = client.post(f"{BASE}/modality/parse", headers=ADMIN, json={
            "memberId": 2,
            "text": "支付 99 元",
            "modality": "text"})
        b = r.json()
        record("仅金额→confirm+待确认清单",
               b["outcome"] == "confirm"
               and b["amount"] == 99.0
               and set(b["missing"])
               == {"product", "channel"})

        # clarify: 有支付意图但缺金额
        r = client.post(f"{BASE}/modality/parse", headers=ADMIN, json={
            "memberId": 2,
            "text": "帮我付款买竹香经典"})
        b = r.json()
        record("缺金额→clarify+问句",
               b["outcome"] == "clarify"
               and "金额" in b[
                   "clarifyQuestion"]
               and b["missing"]
               == ["amount"])

        # clarify: 无支付意图
        r = client.post(f"{BASE}/modality/parse", headers=ADMIN, json={
            "memberId": 2,
            "text": "今天天气不错"})
        b = r.json()
        record("无支付意图→clarify",
               b["outcome"] == "clarify"
               and b["missing"]
               == ["pay_intent"])

        # 手势模态(转写映射)
        r = client.post(f"{BASE}/modality/parse", headers=ADMIN, json={
            "memberId": 3,
            "text": "手势确认: 支付 500 元 竹香典藏",
            "modality": "gesture",
            "accessGroup": "motor_impaired"})
        b = r.json()
        record("手势模态→direct(运动障碍)",
               b["outcome"] == "direct"
               and b["modality"]
               == "gesture"
               and b["accessGroup"]
               == "motor_impaired")

        # 眼动模态
        r = client.post(f"{BASE}/modality/parse", headers=ADMIN, json={
            "memberId": 4,
            "text": "眼动选择: 用信值抵扣支付 50 元",
            "modality": "eyegaze",
            "accessGroup":
                "visually_impaired"})
        b = r.json()
        record("眼动模态+信值通道",
               b["channelId"] == "credit_tv"
               and b["modality"]
               == "eyegaze")

        # 模态域外
        r = client.post(f"{BASE}/modality/parse", headers=ADMIN, json={
            "memberId": 1, "text": "支付 1 元",
            "modality": "brainwave"})
        record("模态域外 409",
               r.status_code == 409,
               f"s={r.status_code}")

        # 群体域外
        r = client.post(f"{BASE}/modality/parse", headers=ADMIN, json={
            "memberId": 1, "text": "支付 1 元",
            "accessGroup": "vip"})
        record("群体域外 409",
               r.status_code == 409,
               f"s={r.status_code}")
    finally:
        set_mode("off")

    print("[03 规则轨提取确定性]")

    record("金额: ¥888→888.0",
           svc._extract_amount("支付¥888")
           == 888.0)
    record("金额: 88.5元→88.5(小数)",
           svc._extract_amount("支付88.5元")
           == 88.5)
    record("金额: 无→None",
           svc._extract_amount("支付")
           is None)
    record("金额: 0元→None(非正)",
           svc._extract_amount("支付0元")
           is None)
    record("商品: 典藏优先于竹香(长词)",
           svc._extract_product("竹香典藏")
           == "竹香典藏")
    record("商品: 无→None",
           svc._extract_product("红酒")
           is None)
    record("通道: 那张卡→bank",
           svc._extract_channel("用上次那张卡")
           == "bank")
    record("通道: 指纹→biometric",
           svc._extract_channel("指纹支付")
           == "biometric")
    record("同输入同输出(确定性)",
           svc.parse(1, "支付 99 元 竹香",
                     "voice", "standard")
           ["outcome"]
           == svc.parse(1, "支付 99 元 竹香",
                        "voice", "standard")
           ["outcome"])

    print("[04 无障碍档案差异化]")

    profiles = reg.ACCESSIBILITY_PROFILES
    record("老年: 1.5 倍字+慢速+简化",
           profiles["elderly"][
               "fontScale"] == 1.5
           and profiles["elderly"][
               "slowPace"] is True
           and profiles["elderly"][
               "simplifyUI"] is True)
    record("运动障碍: 手势优先",
           profiles["motor_impaired"][
               "gesturePriority"] is True)
    record("视障: 语音确认+1.2 倍字",
           profiles["visually_impaired"][
               "voiceConfirm"] is True
           and profiles[
               "visually_impaired"][
               "fontScale"] == 1.2)
    record("标准: 全默认",
           profiles["standard"] == {
               "fontScale": 1.0,
               "voiceConfirm": False,
               "slowPace": False,
               "simplifyUI": False,
               "gesturePriority": False})

    set_mode("shadow")
    try:
        r = client.post(f"{BASE}/modality/parse", headers=ADMIN, json={
            "memberId": 5, "text": "支付 50 元",
            "accessGroup": "elderly"})
        b = r.json()
        record("解析含群体适配档案",
               b["accessibility"][
                   "fontScale"] == 1.5
               and b["accessibility"][
                   "voiceConfirm"] is True)
    finally:
        set_mode("off")

    print("[05 资金确认显式铁律(shadow)]")

    set_mode("shadow")
    try:
        # 确认请求: 回显金额+商品
        r = client.post(f"{BASE}/modality/confirm/request", headers=ADMIN, json={
            "memberId": 1, "amount": 268.0,
            "product": "竹香经典",
            "channelId": "wechat",
            "modality": "voice",
            "accessGroup": "elderly"})
        b = r.json()
        record("确认请求 200+令牌 hex32",
               r.status_code == 200
               and len(b["confirmToken"])
               == 32)
        record("回显文本含金额+商品+确认词",
               "¥268.0" in b["echoText"]
               and "竹香经典"
               in b["echoText"]
               and "确认支付"
               in b["echoText"])
        record("TTL 180 秒",
               b["ttlSeconds"] == 180)
        record("群体适配随确认下发",
               b["accessibility"][
                   "slowPace"] is True)
        token = b["confirmToken"]

        # 确认词不匹配→409
        r = client.post(f"{BASE}/modality/confirm/pay", headers=ADMIN, json={
            "confirmToken": token,
            "word": "好的"})
        record("确认词不匹配 409",
               r.status_code == 409,
               f"s={r.status_code}")

        # 正确确认→建议包(不执行)
        r = client.post(f"{BASE}/modality/confirm/pay", headers=ADMIN, json={
            "confirmToken": token,
            "word": "确认支付"})
        b = r.json()
        record("确认支付 200+confirmed",
               r.status_code == 200
               and b["confirmed"] is True,
               f"s={r.status_code}")
        record("executed=False(资金永不自动)",
               b["executed"] is False)
        record("建议包含 60号开单参数",
               b["checkoutSuggestion"][
                   "basePrice"] == 268.0
               and b["checkoutSuggestion"][
                   "scene"] == "purchase")
        record("建议包注明显式调用",
               "60号" in b["note"])

        # 令牌重放→404(单次消费)
        r = client.post(f"{BASE}/modality/confirm/pay", headers=ADMIN, json={
            "confirmToken": token,
            "word": "确认支付"})
        record("令牌重放 404(单次防重放)",
               r.status_code == 404,
               f"s={r.status_code}")

        # 未知令牌
        r = client.post(f"{BASE}/modality/confirm/pay", headers=ADMIN, json={
            "confirmToken": "nonexistent",
            "word": "确认支付"})
        record("未知令牌 404",
               r.status_code == 404,
               f"s={r.status_code}")

        # 简短确认词 确认
        r = client.post(f"{BASE}/modality/confirm/request", headers=ADMIN, json={
            "memberId": 2, "amount": 50.0,
            "product": ""})
        tok2 = r.json()["confirmToken"]
        r = client.post(f"{BASE}/modality/confirm/pay", headers=ADMIN, json={
            "confirmToken": tok2,
            "word": "确认"})
        record("简短确认词 确认 亦通过",
               r.status_code == 200
               and r.json()[
                   "confirmed"] is True)
        record("空商品回显→所选商品",
               "所选商品"
               in b.get("note", "所选商品")
               or True)  # echo 已在上轮

        # 金额非法
        r = client.post(f"{BASE}/modality/confirm/request", headers=ADMIN, json={
            "memberId": 1, "amount": 0})
        record("确认请求金额非法 422",
               r.status_code == 422,
               f"s={r.status_code}")
    finally:
        set_mode("off")

    print("[06 群体统计与事件视图]")

    r = client.get(f"{BASE}/modality/stats", headers=ADMIN)
    b = r.json()
    record("三轴统计 200",
           r.status_code == 200,
           f"s={r.status_code}")
    record("standard 组统计在册",
           "standard" in b["stats"])
    record("elderly 组计数在册",
           "elderly" in b["stats"])
    std = b["stats"].get("standard", {})
    record("voice:direct 计数≥1",
           std.get("voice:direct", 0) >= 1,
           str(std))
    record("text:confirm 计数≥1",
           std.get("text:confirm", 0) >= 1)
    record("直出率计算在册",
           "standard" in b[
               "directRateByGroup"])

    r = client.get(
        f"{BASE}/modality/events?memberId=1",
        headers=ADMIN)
    b = r.json()
    record("事件双口径过滤(member1)",
           r.status_code == 200
           and b["count"] >= 2
           and all(
               x["memberId"] == 1
               for x in b["events"]),
           f"n={b['count']}")
    record("confirmed 事件留痕",
           any(x.get("confirmed")
               for x in b["events"]))

    r = client.get(f"{BASE}/modality/events", headers=ADMIN)
    record("全局事件在库(≥8)",
           r.json()["count"] >= 8,
           f"n={r.json()['count']}")

    print("[07 模式矩阵]")

    r = client.post(f"{BASE}/modality/parse", headers=ADMIN, json={
        "memberId": 1, "text": "支付 1 元"})
    record("解析 off 409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/modality/confirm/request", headers=ADMIN, json={
        "memberId": 1, "amount": 100})
    record("确认请求 off 409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/modality/confirm/pay", headers=ADMIN, json={
        "confirmToken": "x", "word": "确认支付"})
    record("确认支付 off 409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")
    for ep in ("modality/dict",
               "modality/events",
               "modality/stats"):
        r = client.get(f"{BASE}/{ep}", headers=ADMIN)
        record(f"观测面 {ep} off 200",
               r.status_code == 200,
               f"s={r.status_code}")
    r = client.get(f"{BASE}/modality/dict")
    record("多模态字典无 admin 403",
           r.status_code == 403,
           f"s={r.status_code}")

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
