"""54号·小竹AI智能登录引擎大模型 P1 专项测试
(决策回流管道)

运行方式:
    python test_login54_p1.py

覆盖(54号计划 §六 P1):
    - 七类信号源: 逐信号 reward/语义断言
      (S1 驻留正当/S2 弱驻留/S3 失败切换/S4 申诉
      成立误拦修正/S5 拦截正确/S6 红队对抗样本/
      S7 riskFlagged 高危兜底)
    - 幂等铁律: 二次 collect 零新增(labeled 跳过)
    - T+1 延迟态: pending_dwell(<5min 不判)/
      pending_appeal(申诉中)不双写; 重扫转正
    - 44号池双写: factors 八因子+reward+expected
      (学习闭环数据源)
    - 中间态 skip(credential_fail 等无终态标签)
    - 上下文重构: 通道成功率/失败计数/账龄
    - 回流统计: 分布/延迟态/池双写数
    - 调度器: LOGIN54_LEARN_MODE 默认 off+
      手动触发一轮 T+1 补标
    - HTTP 层: collect/stats 端点+鉴权
    - 零影响: 53号路由 20 端点零改动
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
os.environ["LOGIN54_MODE"] = "off"
os.environ["LOGIN54_LEARN_MODE"] = "off"

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


# ------------------------------------------------------------
# 测试辅助(seed 53号/43号 数据——纯仓储直写)
# ------------------------------------------------------------

async def seed_event(member_id: int, method: str,
                    success: bool, decision: str,
                    detail: str = "",
                    explain_ref: str = "",
                    created_at: str = None) -> int:
    """直写 53号 登录事件(回流引擎数据源)"""
    from core.helpers import ts
    from repositories.login53_repository import (
        Login53Repository,
    )
    repo = Login53Repository()
    event_id = await repo.next_event_id()
    await repo.save_event({
        "eventId": event_id,
        "memberId": member_id,
        "method": method,
        "riskScore": 20.0,
        "decision": decision,
        "durationMs": 100.0,
        "privacyCost": 0.01,
        "explainRef": explain_ref,
        "success": success,
        "detail": detail,
        "createdAt": created_at or ts(),
    })
    return event_id


async def seed_appeal(member_id: int, status: str,
                      created_at: str = None) -> int:
    """直写 43号 申诉(误拦信号源)"""
    from core.helpers import ts
    from repositories.security_repository import (
        Security43Repository,
    )
    repo = Security43Repository()
    appeal_id = await repo.next_id("appeal")
    await repo.save_appeal({
        "appealId": appeal_id,
        "eventId": 1,
        "memberId": member_id,
        "ip": "1.2.3.4",
        "reason": "误拦申诉测试",
        "status": status,
        "reviewer": "admin" if status != "pending" else "",
        "reviewNote": "",
        "createdAt": created_at or ts(),
        "decidedAt": ts() if status != "pending" else None,
    })
    return appeal_id


async def seed_retention(member_id: int,
                         day_key: str) -> None:
    """直写 53号 驻留台账(驻留正当信号源)"""
    from core.helpers import ts
    from repositories.login53_repository import (
        Login53Repository,
    )
    repo = Login53Repository()
    await repo.save_retention({
        "memberId": member_id,
        "dayKey": day_key,
        "rewardPoints": 1,
        "streakDays": 1,
        "greeting": "test",
        "claimedAt": ts(),
        "milestoneUnlocked": 0,
        "eventNote": "",
    })


async def collect(**kwargs):
    from services.login54_feedback_service import (
        Login54FeedbackService,
    )
    return await Login54FeedbackService(
    ).collect_feedback(**kwargs)


async def labels():
    """读全部终态标注(labeled)"""
    from repositories.login54_repository import (
        Login54Repository,
    )
    return await Login54Repository().list_feedback(
        status="labeled", limit=100)


class TestSevenSignals:
    """01 七类信号源"""

    async def run(self):
        print("[01 七类信号源]")
        reset_all()
        from core.helpers import ts
        from datetime import datetime, timedelta

        now = datetime.now().astimezone()
        old_ts = (now - timedelta(hours=1)
                  ).isoformat()   # 1h 前(驻留窗口外)
        today_key = ts()[:10]

        # S1 驻留正当: 成功+同日驻留领取
        e1 = await seed_event(6101, "passkey", True,
                              "silent",
                              created_at=old_ts)
        await seed_retention(6101, today_key)

        # S2 弱驻留: 成功+1h 前无驻留(窗口外)
        e2 = await seed_event(6102, "voice", True,
                              "one_tap",
                              created_at=old_ts)

        # S3 失败切换: 3 次异通道失败+切换成功
        for _ in range(3):
            await seed_event(6103, "qr", False,
                             "credential_fail",
                             created_at=old_ts)
        e3 = await seed_event(6103, "passkey", True,
                              "silent",
                              created_at=old_ts)

        # S4 申诉成立误拦: 拦截+43号 approved 申诉
        e4 = await seed_event(6104, "voice", False,
                              "enhanced",
                              created_at=old_ts)
        await seed_appeal(6104, "approved")

        # S5 拦截正确: 拦截+无申诉
        e5 = await seed_event(6105, "qr", False,
                              "security_challenge",
                              created_at=old_ts)

        # S6 红队对抗: detail 标记重放
        e6 = await seed_event(6106, "passkey", True,
                              "silent",
                              detail="token_replay 跨会员盗用",
                              created_at=old_ts)

        # S7 高危兜底: riskFlagged 会员成功登录
        from repositories.login53_repository import (
            Login53Repository,
        )
        await Login53Repository().save_profile({
            "memberId": 6107, "riskFlagged": 1})
        e7 = await seed_event(6107, "passkey", True,
                              "silent",
                              created_at=old_ts)

        # 触发回流
        r = await collect()
        record("collect 成功",
               r.get("success") is True
               and r.get("labeled") == 7,
               f"labeled={r.get('labeled')} "
               f"errors={r.get('errors')}")

        lab = {int(f["eventId"]): f
               for f in await labels()}

        # S1 驻留正当 +1.0
        f1 = lab.get(e1) or {}
        record("S1 驻留正当(+1.0)",
               f1.get("source") == "retention_dwell"
               and f1.get("reward") == 1.0,
               str((f1.get("source"),
                    f1.get("reward"))))
        record("S1 expected=observed(allow 正确)",
               f1.get("expectedTier")
               == f1.get("observedTier") == "silent",
               str((f1.get("expectedTier"),
                    f1.get("observedTier"))))

        # S2 弱驻留 +0.3
        f2 = lab.get(e2) or {}
        record("S2 弱驻留(+0.3)",
               f2.get("source") == "weak_dwell"
               and abs(f2.get("reward", 0) - 0.3)
               < 1e-9,
               str((f2.get("source"),
                    f2.get("reward"))))

        # S3 失败切换 -1.0
        f3 = lab.get(e3) or {}
        record("S3 失败切换(-1.0)",
               f3.get("source") == "fail_switch"
               and f3.get("reward") == -1.0,
               str((f3.get("source"),
                    f3.get("reward"))))
        record("S3 expected=step_up(挑战正确)",
               f3.get("expectedTier") == "step_up",
               str(f3.get("expectedTier")))

        # S4 申诉成立 -1.0(QC: 误拦修正)
        f4 = lab.get(e4) or {}
        record("S4 申诉成立误拦(-1.0)",
               f4.get("source") == "appeal_upheld"
               and f4.get("reward") == -1.0,
               str((f4.get("source"),
                    f4.get("reward"))))
        record("S4 expected=silent(应放行)",
               f4.get("expectedTier") == "silent"
               and f4.get("observedTier")
               == "enhanced",
               str((f4.get("expectedTier"),
                    f4.get("observedTier"))))

        # S5 拦截正确 +1.0
        f5 = lab.get(e5) or {}
        record("S5 拦截正确(+1.0)",
               f5.get("source") == "block_correct"
               and f5.get("reward") == 1.0,
               str((f5.get("source"),
                    f5.get("reward"))))

        # S6 红队对抗 -1.0(QC: 对抗样本回流)
        f6 = lab.get(e6) or {}
        record("S6 红队对抗样本(-1.0)",
               f6.get("source") == "replay_theft"
               and f6.get("reward") == -1.0,
               str((f6.get("source"),
                    f6.get("reward"))))
        record("S6 expected=enhanced",
               f6.get("expectedTier") == "enhanced",
               str(f6.get("expectedTier")))

        # S7 高危兜底 -0.5
        f7 = lab.get(e7) or {}
        record("S7 riskFlagged 高危(-0.5)",
               f7.get("source") == "risk_flagged"
               and f7.get("reward") == -0.5,
               str((f7.get("source"),
                    f7.get("reward"))))


class TestIdempotentAndDefer:
    """02 幂等+延迟态+skip"""

    async def run(self):
        print("[02 幂等+延迟态]")
        reset_all()
        from core.helpers import ts
        from datetime import datetime, timedelta

        now = datetime.now().astimezone()

        # 幂等基线: 1 条成功+驻留事件
        e1 = await seed_event(6201, "passkey", True,
                              "silent",
                              created_at=(
                                  now - timedelta(hours=1)
                              ).isoformat())
        await seed_retention(6201, ts()[:10])

        r1 = await collect()
        record("首轮标注 1 条",
               r1.get("labeled") == 1,
               str(r1.get("labeled")))

        # 幂等: 二次 collect 零新增(labeled 跳过)
        r2 = await collect()
        record("幂等重扫零新增",
               r2.get("labeled") == 0
               and r2.get("skipped") == 1,
               f"labeled={r2.get('labeled')} "
               f"skipped={r2.get('skipped')}")
        record("标注总量恒定",
               len(await labels()) == 1,
               str(len(await labels())))

        # T+1 pending_dwell: 新事件(5min 内)不判
        e2 = await seed_event(6202, "voice", True,
                              "silent")   # 刚刚发生
        r3 = await collect()
        record("pending_dwell 延迟(<5min 不判)",
               r3.get("deferred") == 1
               and r3.get("labeled") == 0,
               f"deferred={r3.get('deferred')}")
        from repositories.login54_repository import (
            Login54Repository,
        )
        pend = await Login54Repository(
        ).get_feedback_by_event(e2)
        record("pending_dwell 状态落库",
               (pend or {}).get("status")
               == "pending_dwell"
               and int((pend or {})
                       .get("poolFeedbackId") or 0) == 0,
               str((pend or {}).get("status")))

        # pending_appeal: 拦截+申诉中
        e3 = await seed_event(6203, "qr", False,
                              "enhanced")
        await seed_appeal(6203, "pending")
        r4 = await collect()
        record("pending_appeal 延迟(申诉中不判)",
               r4.get("deferred") == 2,
               f"deferred={r4.get('deferred')}")
        pend3 = await Login54Repository(
        ).get_feedback_by_event(e3)
        record("pending_appeal 状态落库",
               (pend3 or {}).get("status")
               == "pending_appeal",
               str((pend3 or {}).get("status")))

        # T+1 重扫转正: pending_dwell 到期后
        # 重扫(伪造旧时间戳——直接更新源事件)
        from repositories.login53_repository import (
            Login53Repository,
        )
        repo53 = Login53Repository()
        ev = await repo53.list_events(
            member_id=6202, limit=1)
        ev[0]["createdAt"] = (
            now - timedelta(hours=1)).isoformat()
        await repo53.save_event(ev[0])
        r5 = await collect()
        fb2 = await Login54Repository(
        ).get_feedback_by_event(e2)
        record("重扫转正 weak_dwell(+0.3)",
               r5.get("labeled") == 1
               and (fb2 or {}).get("source")
               == "weak_dwell",
               f"labeled={r5.get('labeled')}")

        # 转正复用 feedbackId(索引不重复)
        all_fb = await Login54Repository(
        ).list_feedback(limit=100)
        fb_ids = [f["feedbackId"] for f in all_fb]
        record("转正复用 feedbackId(无重复)",
               len(fb_ids) == len(set(fb_ids)),
               str(fb_ids))

        # 中间态 skip: credential_fail 单独事件
        await seed_event(6204, "qr", False,
                         "credential_fail",
                         created_at=(
                             now - timedelta(hours=1)
                         ).isoformat())
        r6 = await collect()
        record("中间态事件 skip(无终态标签)",
               r6.get("skipped") >= 1
               and r6.get("labeled") == 0,
               f"skipped={r6.get('skipped')}")


class TestPoolDoubleWrite:
    """03 44号池双写+上下文重构"""

    async def run(self):
        print("[03 44号池双写]")
        reset_all()
        from core.helpers import ts
        from datetime import datetime, timedelta

        old_ts = (datetime.now().astimezone()
                  - timedelta(hours=1)).isoformat()

        # 会员先有历史(上下文重构数据源):
        # 2 次同通道凭证失败(中间态→skip, 但计入
        # 通道成功率/失败计数重构)
        for _ in range(2):
            await seed_event(6301, "passkey", False,
                             "credential_fail",
                             created_at=old_ts)
        # 本次成功+驻留(唯一终态标注)
        e1 = await seed_event(6301, "passkey", True,
                              "silent",
                              created_at=old_ts)
        await seed_retention(6301, ts()[:10])

        r = await collect()
        record("标注+双写各 1 条",
               r.get("labeled") == 1
               and r.get("poolSubmitted") == 1,
               f"labeled={r.get('labeled')} "
               f"pool={r.get('poolSubmitted')}")

        # login54_feedback 侧回填 poolFeedbackId
        from repositories.login54_repository import (
            Login54Repository,
        )
        fb = await Login54Repository(
        ).get_feedback_by_event(e1)
        record("poolFeedbackId 回填",
               int((fb or {}).get("poolFeedbackId")
                   or 0) > 0,
               str((fb or {}).get("poolFeedbackId")))

        # 44号池: login_orchestration 反馈存在
        from repositories.ai_learning_repository \
            import AiLearningRepository
        pool = await AiLearningRepository(
        ).list_feedback("login_orchestration",
                        limit=10)
        record("44号池双写落池",
               len(pool) == 1,
               str(len(pool)))
        pf = pool[0] if pool else {}
        record("池反馈八因子快照",
               len(pf.get("factors") or []) == 8,
               str(len(pf.get("factors") or [])))
        record("池反馈 reward=+1.0(连续奖励)",
               abs(float(pf.get("reward") or 0)
                   - 1.0) < 1e-9,
               str(pf.get("reward")))
        record("池反馈 source=login54_pipeline",
               pf.get("source") == "login54_pipeline",
               str(pf.get("source")))
        record("池反馈 note 溯源 eventId",
               f"eventId={e1}" in str(
                   pf.get("note") or ""),
               str(pf.get("note")))

        # 上下文重构: 同通道 2 失败 0 成功——
        # Laplace 平滑 (0+1)/(2+2)=0.25; 失败计数 2
        ctx = (fb or {}).get("context") or {}
        record("上下文通道成功率重构",
               abs(float(ctx.get("channelSuccess") or 0)
                   - 0.25) < 1e-6,
               str(ctx.get("channelSuccess")))
        record("上下文失败计数重构",
               int(ctx.get("channelFailCount") or 0)
               == 2,
               str(ctx.get("channelFailCount")))

        # 统计视图
        from services.login54_feedback_service \
            import Login54FeedbackService
        stats = await Login54FeedbackService(
        ).feedback_stats()
        record("统计: 标注分布+池双写数",
               stats.get("total") == 1
               and (stats.get("bySource") or {})
               .get("retention_dwell") == 1
               and stats.get("poolSubmitted") == 1,
               str(stats.get("bySource")))
        record("统计: 正负样本分拆",
               (stats.get("rewardSplit") or {})
               .get("positive") == 1
               and (stats.get("rewardSplit") or {})
               .get("negative") == 0,
               str(stats.get("rewardSplit")))


class TestSchedulerAndHttp:
    """04 调度器+HTTP 层+零影响"""

    async def run(self):
        print("[04 调度器+HTTP]")
        reset_all()
        from core.helpers import ts
        from datetime import datetime, timedelta

        old_ts = (datetime.now().astimezone()
                  - timedelta(hours=1)).isoformat()
        await seed_event(6401, "passkey", True,
                         "silent",
                         created_at=old_ts)
        await seed_retention(6401, ts()[:10])

        # 调度器: 默认 off 不启动
        from services.login54_scheduler import (
            start_scheduler, stop_scheduler,
            scheduler_running, run_scheduled_collect,
        )
        record("调度器默认 off 不启动",
               start_scheduler() is False
               and scheduler_running() is False,
               str(scheduler_running()))

        # 手动触发一轮 T+1 补标(调度体)
        stats = await run_scheduled_collect()
        record("手动 T+1 补标一轮",
               int(stats.get("runs") or 0) == 1
               and ((stats.get("lastCollect") or {})
                    .get("labeled")) == 1,
               str(stats.get("lastCollect")))

        # HTTP 层
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        resp = client.post(
            "/api/login54/feedback/collect",
            headers=admin)
        body = resp.json() or {}
        record("HTTP collect 200(幂等——已标注跳过)",
               resp.status_code == 200
               and body.get("labeled") == 0
               and body.get("skipped") == 1,
               f"{resp.status_code} "
               f"labeled={body.get('labeled')}")

        resp = client.post(
            "/api/login54/feedback/collect",
            json={"memberId": 6401},
            headers=admin)
        record("HTTP collect 定向 memberId 200",
               resp.status_code == 200
               and (resp.json() or {}
                    ).get("scanned") == 1,
               str(resp.status_code))

        resp = client.get(
            "/api/login54/feedback/stats",
            headers=admin)
        body = resp.json() or {}
        record("HTTP stats 200(七类信号表)",
               resp.status_code == 200
               and len(body.get("signalRewards")
                       or {}) == 7,
               str(resp.status_code))

        # 鉴权
        resp = client.post(
            "/api/login54/feedback/collect")
        record("collect 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))
        resp = client.get(
            "/api/login54/feedback/stats")
        record("stats 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 零影响: 53号 路由 20 端点零改动
        from routes.login53_routes import (
            router as login53_router,
        )
        count53 = sum(
            1 for r in login53_router.routes)
        record("53号路由零改动(20 端点)",
               count53 == 20, str(count53))

        # 44号 学习循环接口零改动
        from services.ai_learning_service import (
            run_learning_cycle,
        )
        record("44号学习循环接口零改动",
               callable(run_learning_cycle))
        stop_scheduler()


async def run_all():
    await TestSevenSignals().run()
    await TestIdempotentAndDefer().run()
    await TestPoolDoubleWrite().run()
    await TestSchedulerAndHttp().run()


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
