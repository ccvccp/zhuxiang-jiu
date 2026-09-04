"""48号·小竹智能语音中枢 P3 专项测试
(进化层·交互驱动双优化)

运行方式:
    python test_xiaozhu_p3.py

覆盖(计划 §七):
    - 积分账本: 指令完成计分(+2)/余额/流水/
      fail-soft 不阻断
    - 兑换: 不足拒绝/未绑定拒绝/足额走 45号 deposit
      验真通道(verified 后扣减)/兑换单位口径
    - 失败挖掘: 兜底归档/负反馈归档/重复归档/
      聚类视图 topPhrases
    - 共创指令: 提交校验(短语长度/action 白名单/
      重复短语)/审核(上架+100/驳回/重复审核拒绝)/
      上架后短语匹配生效(track=custom)/共创不能
      新建执行器
    - 主动关怀: 默认 off 跳过/开关 on 扫描生成任务/
      频控(7 天内同类不重发)
    - 反语音霸权: 澄清轮次不计分
    - HTTP 层: 7 端点/鉴权
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


def set_proactive(v: str):
    os.environ["XIAOZHU_PROACTIVE_MODE"] = v


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()
    import services.xiaozhu_executor as ex_mod
    ex_mod._EXECUTOR_SINGLETON = None


async def _new_trust() -> int:
    from services.trust_scoring_service import (
        TrustProfileService,
    )
    import uuid
    suffix = uuid.uuid4().hex[:10]
    r = await TrustProfileService().create_role(
        "person", f"p3-{suffix}", f"110101{suffix}4321")
    return r["trustId"]


async def _session(member_id: int) -> int:
    from services.xiaozhu_service import XiaozhuService
    return (await XiaozhuService().open_session(
        member_id))["sessionId"]


async def _text(sid: int, text: str) -> dict:
    from services.xiaozhu_service import XiaozhuService
    return await XiaozhuService().handle_text(sid, text)


class TestPoints:
    async def run(self):
        print("[01 积分账本]")
        reset_all()
        from services.xiaozhu_evolution_service import (
            XiaozhuEvolutionService,
        )
        ev = XiaozhuEvolutionService()
        sid = await _session(100)

        # 初始零
        v = await ev.points_view(100)
        record("初始零积分",
               v["balance"] == 0 and v["ledger"] == [],
               str(v["balance"]))

        # 指令完成 → +2
        await _text(sid, "小竹，看新品")
        v = await ev.points_view(100)
        record("指令完成计分(+2)",
               v["balance"] == 2
               and v["ledger"][0]["kind"]
               == "command_done",
               str(v["balance"]))
        # 再来一指令 → +2
        await _text(sid, "小竹，查优惠")
        v = await ev.points_view(100)
        record("累计计分(+4)",
               v["balance"] == 4, str(v["balance"]))

        # 澄清轮次不计分(反语音霸权——无效行为零分)
        sid2 = await _session(101)
        await _text(sid2, "小竹，把信用分换成信值")
        v = await ev.points_view(101)
        record("澄清轮次不计分",
               v["balance"] == 0, str(v["balance"]))

        # 未唤醒轮次不计分
        sid3 = await _session(102)
        await _text(sid3, "看新品")
        v = await ev.points_view(102)
        record("未唤醒轮次不计分",
               v["balance"] == 0, str(v["balance"]))


class TestRedeem:
    async def run(self):
        print("[02 积分兑换(45号 deposit 通道)]")
        reset_all()
        from services.xiaozhu_evolution_service import (
            XiaozhuEvolutionService,
        )
        ev = XiaozhuEvolutionService()

        # 不足拒绝
        sid = await _session(110)
        await _text(sid, "小竹，看新品")
        try:
            await ev.redeem(110)
            record("积分不足拒绝", False, "未抛")
        except ValueError as e:
            record("积分不足拒绝", "不足" in str(e))

        # 未绑定拒绝(先灌足积分)
        await _text(sid, "小竹，查优惠")
        for _ in range(50):
            await ev.award_command_done(
                110, sid, 0)
        try:
            await ev.redeem(110)
            record("未绑定拒绝", False, "未抛")
        except ValueError as e:
            record("未绑定拒绝", "绑定" in str(e))

        # 足额+绑定 → deposit 通道
        tid = await _new_trust()
        from services.xiaozhu_service import XiaozhuService
        await XiaozhuService().bind_trust(110, tid)
        r = await ev.redeem(110)
        record("兑换走 deposit 通道",
               r.get("success") is True
               and (r.get("deposit") or {})
               .get("verified") is True,
               str(r)[:80])
        record("兑换后积分扣减",
               (await ev.points_view(110))["balance"] == 4,
               str((await ev.points_view(110))["balance"]))
        record("redeem 账目负向留痕",
               (await ev.points_view(110))["ledger"][0]
               ["kind"] == "redeem")


class TestFailureMining:
    async def run(self):
        print("[03 失败案例挖掘]")
        reset_all()
        from services.xiaozhu_evolution_service import (
            XiaozhuEvolutionService,
        )
        ev = XiaozhuEvolutionService()
        sid = await _session(120)

        # 兜底归档
        await _text(sid, "小竹，明天天气怎么样")
        v = await ev.failures_view()
        record("兜底归档(fallback)",
               v["byKind"].get("fallback", 0) >= 1,
               str(v["byKind"]))

        # 负反馈归档
        await _text(sid, "小竹，不对")
        v = await ev.failures_view()
        record("负反馈归档(negative)",
               v["byKind"].get("negative", 0) >= 1,
               str(v["byKind"]))

        # 重复归档(同文本连续 2 次)
        await _text(sid, "小竹，看看那个新款")
        await _text(sid, "小竹，看看那个新款")
        v = await ev.failures_view()
        record("重复归档(repeat)",
               v["byKind"].get("repeat", 0) >= 1,
               str(v["byKind"]))
        record("topPhrases 聚类呈现",
               len(v.get("topPhrases") or []) >= 1,
               str(v.get("topPhrases"))[:60])


class TestCustomCommands:
    async def run(self):
        print("[04 共创指令]")
        reset_all()
        from services.xiaozhu_evolution_service import (
            XiaozhuEvolutionService,
        )
        ev = XiaozhuEvolutionService()

        # 校验
        for name, phrase, action in (
                ("短语过短拒绝", "看", "product.new"),
                ("短语过长拒绝", "超" * 31, "product.new"),
                ("非白名单action拒绝", "给我来点好的",
                 "admin.wipe"),
        ):
            try:
                await ev.submit_custom(130, phrase, action)
                record(name, False, "未抛")
            except ValueError:
                record(name, True)

        # 提交 → pending
        r = await ev.submit_custom(
            130, "来点好酒", "product.new")
        cmd_id = r["cmdId"]
        record("提交进入pending",
               r["status"] == "pending")
        # 重复短语拒绝
        try:
            await ev.submit_custom(
                131, "来点好酒", "promo.query")
            record("重复短语拒绝", False, "未抛")
        except ValueError:
            record("重复短语拒绝", True)

        # 审核: 驳回
        r = await ev.review_custom(cmd_id, False,
                                   "语义模糊")
        record("驳回留痕",
               r["status"] == "rejected"
               and r["note"] == "语义模糊")
        try:
            await ev.review_custom(cmd_id, True)
            record("重复审核拒绝", False, "未抛")
        except ValueError:
            record("重复审核拒绝", True)

        # 再提交 → 上架(+100 计分)
        r = await ev.submit_custom(
            130, "上新了啥", "product.new")
        r = await ev.review_custom(r["cmdId"], True,
                                  "高频短语")
        record("上架生效",
               r["status"] == "approved")
        v = await ev.points_view(130)
        record("上架贡献者+100",
               v["balance"] >= 100,
               str(v["balance"]))

        # 上架后短语匹配生效(track=custom)
        sid = await _session(130)
        r = await _text(sid, "小竹，上新了啥")
        record("共创短语匹配(track=custom)",
               r.get("track") == "custom"
               and "新品" in r.get("reply", ""),
               f"track={r.get('track')}")
        # 未上架短语不匹配
        sid2 = await _session(131)
        r = await _text(sid2, "小竹，来点好酒")
        record("驳回短语不生效",
               r.get("track") != "custom")


class TestProactive:
    async def run(self):
        print("[05 主动关怀(默认 off)]")
        reset_all()
        from services.xiaozhu_evolution_service import (
            XiaozhuEvolutionService,
        )
        ev = XiaozhuEvolutionService()

        # 默认 off 跳过
        r = await ev.scan_proactive()
        record("默认off跳过",
               r.get("skipped") is True)

        # 开关 on: 无候选(无 watched/绑定档案)零任务
        set_proactive("on")
        try:
            r = await ev.scan_proactive()
            record("on无候选零任务",
                   r.get("success") is True
                   and r.get("generated", 0) == 0,
                   str(r)[:60])
            # 构造: 违规(修复计划依据) + watched 档案
            # (4 次守门命中推 EMA) + 绑定——注意违规事件
            # 先灌: 负向事件回流画像会稀释 EMA(0.5904→
            # 0.4723 降回 standard), 守门命中须最后灌
            tid = await _new_trust()
            from services.trust_scoring_service import (
                TrustProfileService,
            )
            await TrustProfileService().record_event(
                tid, "L2", "ethics_evidence", -5.0)
            for _ in range(4):
                await TrustProfileService().record_event(
                    tid, "L2", "ethics_evidence", 20.0,
                    consistency=0.1)
            from services.xiaozhu_service import (
                XiaozhuService,
            )
            await XiaozhuService().bind_trust(140, tid)
            r = await ev.scan_proactive()
            record("on生成关怀任务",
                   r.get("generated", 0) >= 1,
                   str(r)[:70])
            # 频控: 7 天内同类不重发
            r2 = await ev.scan_proactive()
            record("频控(7天同类不重发)",
                   r2.get("generated", 0) == 0,
                   str(r2.get("generated")))
        finally:
            set_proactive("off")


class TestHttp:
    async def run(self):
        print("[06 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.xiaozhu_routes import (
            register_xiaozhu_routes,
        )
        app = FastAPI()
        register_xiaozhu_routes(app)
        client = TestClient(app)
        h = {"X-Member-Id": "150"}
        admin = {"X-Role": "admin"}

        # 计分(经指令)
        async def _prep():
            sid = await _session(150)
            await _text(sid, "小竹，看新品")
        await _prep()
        resp = client.get("/api/xiaozhu/points", headers=h)
        body = resp.json()
        record("GET points 200",
               resp.status_code == 200
               and body.get("balance") == 2,
               str(body.get("balance")))

        # 兑换不足 409
        resp = client.post("/api/xiaozhu/points/redeem",
                           headers=h)
        record("兑换不足 409",
               resp.status_code == 409)

        # 共创提交
        resp = client.post("/api/xiaozhu/commands/custom",
                           json={"phrase": "整点新品",
                                 "action": "product.new"},
                           headers=h)
        body = resp.json()
        record("POST custom 200",
               resp.status_code == 200
               and body.get("status") == "pending",
               str(body)[:50])
        cmd_id = body.get("cmdId")

        # 队列(admin)
        resp = client.get("/api/xiaozhu/commands/custom",
                         headers=admin)
        record("GET custom 队列(admin)",
               resp.status_code == 200
               and len(resp.json()
                       .get("pending") or []) >= 1)
        resp = client.get("/api/xiaozhu/commands/custom")
        record("custom 队列缺Role 403",
               resp.status_code == 403)

        # 审核
        resp = client.post(
            f"/api/xiaozhu/commands/custom/{cmd_id}"
            "/review",
            json={"approve": True, "note": "ok"},
            headers=admin)
        record("审核上架 200",
               resp.status_code == 200
               and resp.json().get("status")
               == "approved")
        resp = client.post(
            f"/api/xiaozhu/commands/custom/{cmd_id}"
            "/review",
            json={"approve": True}, headers=admin)
        record("重复审核 409",
               resp.status_code == 409)
        resp = client.post(
            "/api/xiaozhu/commands/custom/999/review",
            json={"approve": True}, headers=admin)
        record("未知共创 404",
               resp.status_code == 404)

        # 关怀扫描(默认 off)
        resp = client.post("/api/xiaozhu/proactive/scan",
                          headers=admin)
        record("关怀扫描 off 跳过",
               resp.status_code == 200
               and resp.json().get("skipped") is True)
        resp = client.post("/api/xiaozhu/proactive/scan")
        record("关怀扫描缺Role 403",
               resp.status_code == 403)

        # 失败视图
        resp = client.get("/api/xiaozhu/failures",
                          headers=admin)
        record("GET failures 200(admin)",
               resp.status_code == 200
               and "byKind" in resp.json())
        resp = client.get("/api/xiaozhu/failures")
        record("failures 缺Role 403",
               resp.status_code == 403)

        # points 鉴权
        resp = client.get("/api/xiaozhu/points")
        record("points 缺 Member 401",
               resp.status_code == 401)


async def run_all():
    await TestPoints().run()
    await TestRedeem().run()
    await TestFailureMining().run()
    await TestCustomCommands().run()
    await TestProactive().run()
    await TestHttp().run()
    set_proactive("off")


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
