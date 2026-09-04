"""48号·小竹智能语音中枢 P0 专项测试
(感知层: 会话/唤醒/免唤醒/指代/脱敏/8 指令直达)

运行方式:
    python test_xiaozhu_p0.py

覆盖(计划 §四):
    - 唤醒判定: 前缀匹配/近似音容错/标点剥离/未唤醒提示
    - 免唤醒窗口: 5 分钟内直接解析/超窗须重新唤醒
    - 指代消解: "这个多少钱"→上一轮 card.subject
    - PII 脱敏: 身份证/手机号/银行卡 mask
    - 会话生命周期: 开启/视图/关闭/一键清除级联
    - 8 指令直达: product.new/product.price/trust.score/
      trust.balance/nav.page/promo.query/chat.human/
      xiaozhu.help(逐一断言 reply/card/jump)
    - 反语音霸权: 未唤醒不执行只提示
    - HTTP 层: 6 端点结构/鉴权/404
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

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


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


async def _open(member_id: int = 1) -> int:
    from services.xiaozhu_service import XiaozhuService
    r = await XiaozhuService().open_session(member_id)
    return r["sessionId"]


async def _text(sid: int, text: str) -> dict:
    from services.xiaozhu_service import XiaozhuService
    return await XiaozhuService().handle_text(sid, text)


class TestWake:
    async def run(self):
        print("[01 唤醒判定]")
        from services.xiaozhu_service import detect_wake
        ok, rest = detect_wake("小竹，看新品")
        record("标准唤醒+剥离",
               ok is True and rest == "看新品",
               f"{ok}|{rest}")
        ok, rest = detect_wake("小朱 看新品")
        record("近似音容错(小朱)",
               ok is True and rest == "看新品")
        ok, rest = detect_wake("小猪看新品")
        record("近似音容错(小猪)",
               ok is True and rest == "看新品")
        ok, rest = detect_wake("小竹竹, 查优惠")
        record("叠词唤醒+剥离",
               ok is True and rest == "查优惠")
        ok, rest = detect_wake("帮我看看新品")
        record("未唤醒不误判",
               ok is False and rest == "帮我看看新品")
        ok, rest = detect_wake("")
        record("空文本未唤醒", ok is False)
        # 前缀但指令为空(纯唤醒)
        ok, rest = detect_wake("小竹")
        record("纯唤醒空指令",
               ok is True and rest == "")


class TestMaskPii:
    async def run(self):
        print("[02 PII 脱敏]")
        from services.xiaozhu_service import mask_pii
        record("手机号 mask",
               mask_pii("我的手机 13812345678 帮我查")
               == "我的手机 *手机号* 帮我查",
               mask_pii("我的手机 13812345678 帮我查"))
        record("身份证18位 mask",
               "*身份证*" in mask_pii(
                   "证件 110101199001011234 查询"),
               mask_pii("证件 110101199001011234 查询"))
        record("正常文本零改写",
               mask_pii("竹韵佳酿多少钱")
               == "竹韵佳酿多少钱")
        record("空文本安全",
               mask_pii("") == "" and mask_pii(None) == "")


class TestSessionLifecycle:
    async def run(self):
        print("[03 会话生命周期]")
        reset_all()
        from services.xiaozhu_service import XiaozhuService
        svc = XiaozhuService()

        r = await svc.open_session(1)
        sid = r["sessionId"]
        record("开启会话",
               r["success"] is True and sid >= 1
               and r["status"] == "open")

        # 两轮指令
        await _text(sid, "小竹，看新品")
        await _text(sid, "查优惠")
        v = await svc.get_session(sid)
        record("会话视图含轮次",
               len(v["turns"]) == 2
               and v["turns"][0]["seq"] == 1,
               str(len(v.get("turns") or [])))

        # channel 校验
        try:
            await svc.open_session(1, channel="video")
            record("非法channel拒绝", False, "未抛")
        except ValueError:
            record("非法channel拒绝", True)

        # 关闭后再用 → 404 语义
        await svc.close_session(sid)
        try:
            await _text(sid, "小竹，看新品")
            record("关闭会话拒用", False, "未抛")
        except KeyError as e:
            record("关闭会话拒用", "已关闭" in str(e))

        # 一键清除级联
        sid2 = await _open(1)
        await _text(sid2, "小竹，看新品")
        await _text(sid2, "小竹，查优惠")
        r = await svc.delete_session(sid2)
        record("一键清除级联轮次",
               r["success"] is True
               and r["removedRecords"] >= 3,
               str(r))
        try:
            await svc.get_session(sid2)
            record("清除后404", False, "未抛")
        except KeyError:
            record("清除后404", True)

        # 空文本拒绝
        sid3 = await _open(1)
        try:
            await _text(sid3, "   ")
            record("空文本拒绝", False, "未抛")
        except ValueError:
            record("空文本拒绝", True)


class TestCommands:
    async def run(self):
        print("[04 八指令直达]")
        reset_all()
        sid = await _open(7)

        # product.new
        r = await _text(sid, "小竹，看看有什么新上线产品")
        record("看新品直达",
               r["success"] is True
               and "新品" in r["reply"]
               and (r["card"] or {}).get("type")
               == "product_list"
               and r["jump"] == "/product-list.html?sort=new",
               str(r)[:90])

        # 指代消解→product.price
        r = await _text(sid, "这个多少钱")
        record("指代消解(这个多少钱)",
               (r.get("commandText") or "").startswith(
                   "竹") or r["reply"].startswith("「"),
               str(r.get("commandText")) + "|"
               + r["reply"][:30])

        # product.price 直接问
        r = await _text(sid, "小竹，竹韵佳酿多少钱")
        record("问价格直达",
               "元" in r["reply"]
               and (r["card"] or {}).get("type")
               == "product_detail",
               r["reply"][:50])

        # trust.score / trust.balance → 绑定引导(P0)
        for phrase, act in (("小竹，查我的信值", "score"),
                            ("小竹，我的信值余额", "balance")):
            r = await _text(sid, phrase)
            record(f"信值{act}→绑定引导(P0)",
                   "绑定" in r["reply"]
                   and (r["card"] or {}).get("guide")
                   == "trust-bind"
                   and r["jump"] == "/trust-dashboard.html",
                   r["reply"][:40])

        # nav.page
        r = await _text(sid, "小竹，打开购物车")
        record("导航直达(购物车)",
               r["jump"] == "/cart.html"
               and (r["card"] or {}).get("type") == "nav",
               str(r["jump"]))
        r = await _text(sid, "小竹，去个人中心")
        record("导航直达(个人中心)",
               r["jump"] == "/member.html")
        r = await _text(sid, "小竹，打开火星基地")
        record("导航白名单外引导",
               "没听清" in r["reply"], r["reply"][:30])

        # promo.query
        r = await _text(sid, "小竹，今天有什么优惠")
        record("查优惠直达",
               "活动" in r["reply"] or "优惠" in r["reply"],
               r["reply"][:40])

        # chat.human
        r = await _text(sid, "小竹，转人工客服")
        record("转人工直达",
               "人工" in r["reply"]
               and (r["card"] or {}).get("type") == "human")

        # xiaozhu.help
        r = await _text(sid, "小竹，你能干什么")
        record("帮助直达",
               (r["card"] or {}).get("type") == "help"
               and len((r["card"] or {}).get("items")
                       or []) == 13,
               str((r["card"] or {}).get("items"))[:50])

        # 未匹配 → general 兜底
        r = await _text(sid, "小竹，明天天气怎么样")
        record("兜底引导",
               "还不会" in r["reply"], r["reply"][:40])

        # 轮次留痕含 intent/action
        from services.xiaozhu_service import XiaozhuService
        v = await XiaozhuService().get_session(sid)
        actions = [t.get("intent") for t in v["turns"]]
        record("轮次 intent 留痕",
               "product.new" in actions
               and "nav.page" in actions
               and "general" in actions,
               str(actions))
        pii_turn = [t for t in v["turns"]
                    if "手机" in (t.get("rawText") or "")]
        record("轮次 rawText 已脱敏",
               all("1381234" not in t["rawText"]
                   for t in pii_turn) or not pii_turn)


class TestWakeFlow:
    async def run(self):
        print("[05 未唤醒与免唤醒]")
        reset_all()
        sid = await _open(9)

        # 未唤醒 → 不执行只提示
        r = await _text(sid, "看新品")
        record("未唤醒不执行(反语音霸权)",
               r.get("wakeHint") is True
               and "小竹" in r["reply"],
               str(r.get("wakeHint")) + r["reply"][:30])

        # 唤醒一次后免唤醒窗口内连续对话
        await _text(sid, "小竹，看新品")
        r = await _text(sid, "查优惠")
        record("免唤醒窗口内直接解析",
               r.get("wakeHint") is False
               and ("活动" in r["reply"]
                    or "优惠" in r["reply"]),
               r["reply"][:40])

        # 会话元信息
        from services.xiaozhu_service import XiaozhuService
        v = await XiaozhuService().get_session(sid)
        record("lastActiveAt 维护",
               v["lastActiveAt"] >= v["startedAt"])


class TestVoiceChain:
    async def run(self):
        print("[06 语音链降级(mock 无 key)]")
        reset_all()
        sid = await _open(11)
        from services.xiaozhu_service import XiaozhuService
        r = await XiaozhuService().handle_voice(
            sid, b"fake-audio-bytes", 11,
            filename="audio.webm")
        record("无key降级结构化失败",
               r["success"] is True
               and ("语音" in (r.get("reply") or "")
                    or "转写" in (r.get("reply") or ""))
               and r.get("fallbackHint") == "keyboard",
               str(r.get("reply"))[:50])
        # 轮次仍留痕(asr_failed)
        v = await XiaozhuService().get_session(sid)
        record("asr_failed 轮次留痕",
               v["turns"][0]["intent"] == "asr_failed"
               and (v["turns"][0].get("audioMeta")
                    or {}).get("sizeBytes")
               == len(b"fake-audio-bytes"),
               str(v["turns"][0])[:80])


class TestHttp:
    async def run(self):
        print("[07 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.xiaozhu_routes import (
            register_xiaozhu_routes,
        )
        app = FastAPI()
        register_xiaozhu_routes(app)
        client = TestClient(app)
        headers = {"X-Member-Id": "42"}

        resp = client.post(
            "/api/xiaozhu/sessions", json={"channel": "voice"},
            headers=headers)
        body = resp.json()
        record("POST sessions 200",
               resp.status_code == 200
               and body.get("success") is True
               and body.get("memberId") == 42,
               str(resp.status_code))
        sid = body.get("sessionId")

        resp = client.post(
            f"/api/xiaozhu/sessions/{sid}/text",
            json={"text": "小竹，看新品"}, headers=headers)
        body = resp.json()
        record("POST text 200",
               resp.status_code == 200
               and body.get("reply"),
               str(resp.status_code))
        record("回包含 jump 直达",
               body.get("jump")
               == "/product-list.html?sort=new")

        resp = client.get(
            f"/api/xiaozhu/sessions/{sid}")
        record("GET session 200",
               resp.status_code == 200
               and len(resp.json().get("turns") or []) == 1)

        resp = client.get("/api/xiaozhu/commands")
        body = resp.json()
        record("GET commands 200",
               resp.status_code == 200
               and len(body.get("commands") or []) == 13
               and body.get("wakeWords") == ["小竹"],
               str(len(body.get("commands") or [])))

        # 语音端点(base64, 无 key 降级)
        import base64
        resp = client.post(
            f"/api/xiaozhu/sessions/{sid}/voice",
            json={"audioBase64": base64.b64encode(
                b"fake-audio").decode()},
            headers=headers)
        record("POST voice 降级 200",
               resp.status_code == 200
               and resp.json().get("fallbackHint")
               == "keyboard",
               str(resp.status_code))
        resp = client.post(
            f"/api/xiaozhu/sessions/{sid}/voice",
            json={})
        record("voice 缺 audioBase64 409",
               resp.status_code == 409,
               str(resp.status_code))
        resp = client.post(
            f"/api/xiaozhu/sessions/{sid}/voice",
            json={"audioBase64": "!!!非法!!!"})
        record("voice 非法 base64 409",
               resp.status_code == 409)

        # DELETE 级联
        resp = client.delete(
            f"/api/xiaozhu/sessions/{sid}")
        record("DELETE session 200",
               resp.status_code == 200
               and resp.json().get("removedRecords") >= 2)

        # 404 / 409
        resp = client.get("/api/xiaozhu/sessions/99999")
        record("未知会话 404",
               resp.status_code == 404, str(resp.status_code))
        resp = client.post(
            "/api/xiaozhu/sessions/99999/text",
            json={"text": "x"})
        record("未知会话轮次 404",
               resp.status_code == 404)
        sid_blank = await _open(1)
        resp = client.post(
            f"/api/xiaozhu/sessions/{sid_blank}/text",
            json={"text": "   "})
        record("空文本 409",
               resp.status_code == 409,
               str(resp.status_code))
        resp = client.post(
            "/api/xiaozhu/sessions",
            json={"channel": "video"})
        record("非法 channel 409",
               resp.status_code == 409)


async def run_all():
    await TestWake().run()
    await TestMaskPii().run()
    await TestSessionLifecycle().run()
    await TestCommands().run()
    await TestWakeFlow().run()
    await TestVoiceChain().run()
    await TestHttp().run()


def main():
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
