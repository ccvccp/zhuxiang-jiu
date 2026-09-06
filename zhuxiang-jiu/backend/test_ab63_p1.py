"""63号·AI智能后台管理模块 P1 专项测试
(智能权限引擎——衰减+临时降权+恢复)

运行方式:
    python test_ab63_p1.py

覆盖(63号计划 §九 P1):
    - 权限衰减: 90 日闲置高危回收+
      基础 CRUD 不衰减+从未授权
      非衰减域
    - 重新激活: 人工批准(不受开关影响
      铁律)+非衰减域拒绝+无衰减拒绝
    - 临时降权: 异常触发+重复降权拒绝+
      冷却期校验+管理员通知留痕
    - 降权恢复: 冷却期满/管理员提前/
      培训通道+未满拒绝+恢复留痕
    - 连续拒绝检测: 阈值触发
    - 完整可解释链: ruleId+recoveryPath
    - HTTP 层+回归
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["QR55_LEARN_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ["II58_MODE"] = "off"
os.environ["II59_MODE"] = "off"
os.environ["AB63_MODE"] = "off"
os.environ.pop("AB63_LLM_MODE", None)
os.environ.pop("AB63_LEARN_MODE", None)

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


async def seed_old_grant(member_id: int, role: str,
                          action: str, granted: bool,
                          days_ago: int = 0) -> int:
    """种历史裁决(可指定时间偏移)"""
    from core.helpers import ts
    from repositories.ab63_repository import (
        Ab63Repository,
    )
    repo = Ab63Repository()
    grant_id = await repo.next_grant_id()
    created = (datetime.utcnow()
               - timedelta(
                   days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%S")
    await repo.save_grant({
        "grantId": grant_id,
        "memberId": member_id,
        "role": role,
        "action": action,
        "granted": granted,
        "score": 90 if granted else 20,
        "threshold": 60,
        "reason": {
            "text": "种子裁决",
            "ruleId": "PERM_4AXIS",
            "recoveryPath": "",
            "factors": {},
        },
        "context": {
            "tier": "standard",
            "complianceRate": 0.8,
            "period": "normal",
            "sensitivity": "low",
        },
        "createdAt": created,
        "updatedAt": ts(),
    })
    return grant_id


class TestDecay:
    """01 权限衰减引擎"""

    async def run(self):
        print("[01 权限衰减]")
        reset_all()
        from services.ab63_permission_service \
            import (
                Ab63PermissionService,
            )
        svc = Ab63PermissionService()

        # off 拒绝
        try:
            await svc.check_decay(1,
                                  "ally_merchant")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), \
                str(e)[:30]
        record("off 态衰减检查拒绝", ok, err)

        os.environ["AB63_MODE"] = "shadow"

        # 角色域外拒绝
        try:
            await svc.check_decay(1, "hacker")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "域外" in str(e), \
                str(e)[:30]
        record("衰减角色域外拒绝", ok, err)

        # ① 90 日闲置高危衰减
        await seed_old_grant(
            10, "ally_merchant",
            "batch_ops", True, days_ago=100)
        await seed_old_grant(
            10, "ally_merchant",
            "whitelist_quota", True,
            days_ago=30)
        r = await svc.check_decay(
            10, "ally_merchant")
        decayed = r.get("decayed") or []
        healthy = r.get("healthy") or []
        record("90 日闲置衰减(batch_ops)",
               len(decayed) == 1
               and decayed[0].get(
                   "action") == "batch_ops"
               and (decayed[0].get(
                   "idleDays") or 0) >= 90,
               str(decayed))
        record("30 日健康(whitelist)",
               len(healthy) >= 1
               and any(h.get("action")
                       == "whitelist_quota"
                       for h in healthy),
               str(healthy))

        # ② 从未授权非衰减域
        r2 = await svc.check_decay(
            11, "ally_merchant")
        record("从未授权非衰减",
               len(r2.get("decayed") or [])
               == 0,
               str(r2.get("decayed")))

        # ③ 基础 CRUD 不衰减(种子
        #    basic_crud 100 日前)
        await seed_old_grant(
            12, "ally_merchant",
            "basic_crud", True,
            days_ago=100)
        r3 = await svc.check_decay(
            12, "ally_merchant")
        record("基础 CRUD 不衰减(业务必需)",
               all(d.get("action")
                   != "basic_crud"
                   for d in
                   (r3.get("decayed")
                    or [])),
               str(r3.get("decayed")))

        # decay 事件留痕(member 10 有衰减
        # 留痕; member 11 无衰减不留痕)
        from repositories.ab63_repository \
            import Ab63Repository
        repo = Ab63Repository()
        evs = await repo.list_events(
            event_type="grant", limit=20)
        decay_evs = [e for e in evs
                     if (e.get("detail")
                         or {}).get(
                         "action")
                     == "decay_detected"]
        record("decay 事件留痕(有衰减才留)",
               len(decay_evs) == 1,
               str(len(decay_evs)))
        os.environ["AB63_MODE"] = "off"


class TestReactivate:
    """02 重新激活(人工)"""

    async def run(self):
        print("[02 重新激活]")
        reset_all()
        from services.ab63_permission_service \
            import (
                Ab63PermissionService,
            )
        svc = Ab63PermissionService()
        os.environ["AB63_MODE"] = "shadow"

        # 种衰减态(120 日前授权)
        await seed_old_grant(
            20, "ally_merchant",
            "batch_ops", True,
            days_ago=120)

        # 非衰减域拒绝
        try:
            await svc.reactivate(
                20, "ally_merchant",
                "basic_crud")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非衰减域" in str(e), \
                str(e)[:30]
        record("非衰减域激活拒绝", ok, err)

        # 无衰减态拒绝
        try:
            await svc.reactivate(
                21, "ally_merchant",
                "batch_ops")
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("无衰减态激活拒绝", ok, err)

        # 合法激活(off 态亦可用——人工铁律)
        os.environ["AB63_MODE"] = "off"
        r = await svc.reactivate(
            20, "ally_merchant",
            "batch_ops", admin="ops_admin",
            reason="业务扩张需要")
        record("人工激活(off 亦可用铁律)",
               r.get("status")
               == "recovered"
               and int(r.get("grantId")
                       or 0) > 0,
               str(r.get("status")))

        # 激活后不再衰减
        os.environ["AB63_MODE"] = "shadow"
        check = await svc.check_decay(
            20, "ally_merchant")
        record("激活后不再衰减",
               all(d.get("action")
                   != "batch_ops"
                   for d in
                   (check.get("decayed")
                    or [])),
               str(check.get("decayed")))
        os.environ["AB63_MODE"] = "off"


class TestSanction:
    """03 临时降权"""

    async def run(self):
        print("[03 临时降权]")
        reset_all()
        from services.ab63_permission_service \
            import (
                Ab63PermissionService,
            )
        svc = Ab63PermissionService()

        # off 拒绝
        try:
            await svc.sanction(30,
                               "ally_merchant")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), \
                str(e)[:30]
        record("off 态降权拒绝", ok, err)

        os.environ["AB63_MODE"] = "shadow"

        # 角色域外拒绝
        try:
            await svc.sanction(30, "hacker")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "域外" in str(e), \
                str(e)[:30]
        record("降权角色域外拒绝", ok, err)

        # 合法降权
        r = await svc.sanction(
            30, "ally_merchant",
            trigger="anomaly",
            reason="异常操作频率")
        record("降权受理(restricted+冷却)",
               r.get("status")
               == "restricted"
               and bool(r.get(
                   "cooldownUntil"))
               and "冷却期" in str(
                   r.get("recoveryPath")),
               str((r.get("status"),
                    r.get(
                        "cooldownUntil"))))

        # 重复降权拒绝(状态机)
        try:
            await svc.sanction(
                30, "ally_merchant",
                trigger="anomaly")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "降权态" in str(e), \
                str(e)[:30]
        record("重复降权拒绝(状态机)", ok, err)

        # 恢复(冷却未满——管理员通道)
        try:
            await svc.recover(30,
                              via="cooldown")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "冷却期未满" in str(e), \
                str(e)[:30]
        record("冷却未满拒绝(cooldown)",
               ok, err)

        # 管理员提前恢复
        r2 = await svc.recover(
            30, via="admin",
            admin="ops_admin")
        record("管理员提前恢复",
               r2.get("status")
               == "recovered"
               and r2.get("recoveredVia")
               == "admin",
               str((r2.get("status"),
                    r2.get(
                        "recoveredVia"))))

        # 恢复后可再降权
        r3 = await svc.sanction(
            30, "ally_merchant",
            trigger="threat")
        record("恢复后可再降权",
               r3.get("status")
               == "restricted",
               str(r3.get("status")))

        # 培训通道恢复
        r4 = await svc.recover(
            30, via="training")
        record("培训通道恢复",
               r4.get("recoveredVia")
               == "training",
               str(r4.get(
                   "recoveredVia")))

        # 无降权态恢复拒绝
        try:
            await svc.recover(31)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("无降权态恢复拒绝", ok, err)

        # 降权留痕(reason 结构)
        from repositories.ab63_repository \
            import Ab63Repository
        repo = Ab63Repository()
        sanctions = [g for g in
                     await repo.list_grants(
                         member_id=30,
                         limit=50)
                     if (g.get("context")
                         or {}).get("kind")
                     == "sanction"]
        record("降权留痕(2 次)",
               len(sanctions) == 2,
               str(len(sanctions)))
        s0 = sanctions[-1] \
            if sanctions else {}
        record("降权 reason(ruleId+恢复路径)",
               (s0.get("reason")
                or {}).get("ruleId")
               == "SANCTION_TEMP"
               and "冷却期" in str(
                   (s0.get("reason")
                    or {}).get(
                       "recoveryPath")),
               str(s0.get("reason"))[:60])

        # sanction_view 观测面
        view = await svc.sanction_view(
            member_id=30)
        record("降权全景观测面",
               view.get("total") == 2
               and view.get("active")
               == 0,
               str((view.get("total"),
                    view.get("active"))))
        os.environ["AB63_MODE"] = "off"


class TestStreak:
    """04 连续拒绝检测"""

    async def run(self):
        print("[04 连续拒绝]")
        reset_all()
        from services.ab63_permission_service \
            import (
                Ab63PermissionService,
            )
        svc = Ab63PermissionService()
        os.environ["AB63_MODE"] = "shadow"

        # ① 2 连拒(未达阈值)
        await seed_old_grant(
            40, "ally_merchant",
            "batch_ops", False)
        await seed_old_grant(
            40, "ally_merchant",
            "batch_ops", False)
        r1 = await svc.check_denied_streak(40)
        record("2 连拒(未达阈值)",
               r1.get("streak") == 2
               and r1.get(
                   "shouldSanction")
               is False,
               str(r1))

        # ② 3 连拒(达阈值)
        await seed_old_grant(
            41, "ally_merchant",
            "batch_ops", False)
        await seed_old_grant(
            41, "ally_merchant",
            "batch_ops", False)
        await seed_old_grant(
            41, "ally_merchant",
            "batch_ops", False)
        r2 = await svc.check_denied_streak(41)
        record("3 连拒(触发降权建议)",
               r2.get("streak") == 3
               and r2.get(
                   "shouldSanction")
               is True,
               str(r2))

        # ③ granted 打断 streak
        await seed_old_grant(
            42, "ally_merchant",
            "batch_ops", False)
        await seed_old_grant(
            42, "ally_merchant",
            "batch_ops", True)
        await seed_old_grant(
            42, "ally_merchant",
            "batch_ops", False)
        r3 = await svc.check_denied_streak(42)
        record("granted 打断 streak",
               r3.get("streak") == 1,
               str(r3.get("streak")))
        os.environ["AB63_MODE"] = "off"


class TestHttp:
    """06 HTTP 层(P1 grants/{id} 观测面)"""

    async def run(self):
        print("[06 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # shadow 造一条裁决
        os.environ["AB63_MODE"] = "shadow"
        resp = client.post(
            "/api/ab63/grants",
            json={"memberId": 50,
                  "role": "ally_merchant",
                  "action": "batch_ops",
                  "tier": "trusted",
                  "complianceRate": 0.9},
            headers=admin)
        body = resp.json() or {}
        gid = body.get("grantId") or (
            (body.get("record") or {})
            .get("grantId"))
        record("HTTP 裁决造数(grantId)",
               bool(gid), str(gid))

        # 单条观测面(off 亦可用)
        os.environ["AB63_MODE"] = "off"
        resp = client.get(
            f"/api/ab63/grants/{gid}",
            headers=admin)
        g = (resp.json() or {}).get(
            "grant") or {}
        reason = g.get("reason") or {}
        record("HTTP 裁决单条 200(off 可观测)",
               resp.status_code == 200
               and reason.get("ruleId")
               == "PERM_4AXIS"
               and isinstance(
                   reason.get("recoveryPath"),
                   str),
               str((resp.status_code,
                    reason.get("ruleId"))))

        # 不存在 404
        resp = client.get(
            "/api/ab63/grants/99999",
            headers=admin)
        record("HTTP 裁决单条 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 鉴权 403
        resp = client.get(
            f"/api/ab63/grants/{gid}")
        record("HTTP 裁决单条无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))


class TestReasonChain:
    """05 完整可解释链"""

    async def run(self):
        print("[05 可解释链]")
        reset_all()
        from services.ab63_registry import (
            evaluate_permission,
        )

        # 达标态
        v1 = evaluate_permission(
            "ally_merchant", "basic_crud",
            tier="trusted",
            compliance_rate=0.9)
        reason1 = v1.get("reason") or {}
        record("reason 结构四字段",
               all(k in reason1 for k in (
                   "text", "ruleId",
                   "recoveryPath",
                   "factors")),
               str(sorted(reason1.keys())))
        record("ruleId 锚点(PERM_4AXIS)",
               reason1.get("ruleId")
               == "PERM_4AXIS",
               str(reason1.get("ruleId")))
        record("达标恢复路径语义",
               "已达标" in str(
                   reason1.get(
                       "recoveryPath")),
               str(reason1.get(
                   "recoveryPath")))

        # 未达标态(恢复路径指引)
        v2 = evaluate_permission(
            "ally_merchant", "batch_ops",
            tier="watched",
            compliance_rate=0.3,
            period="peak",
            sensitivity="high")
        reason2 = v2.get("reason") or {}
        rp = str(reason2.get(
            "recoveryPath"))
        record("未达标恢复路径(三指引)",
               "信值" in rp
               and "合规率" in rp
               and "高峰" in rp,
               rp[:60])

        # 域外态
        v3 = evaluate_permission(
            "hacker", "basic_crud")
        reason3 = v3.get("reason") or {}
        record("域外 ruleId(DOMAIN_OUT)",
               reason3.get("ruleId")
               == "DOMAIN_OUT",
               str(reason3.get("ruleId")))


async def run_all():
    await TestDecay().run()
    await TestReactivate().run()
    await TestSanction().run()
    await TestStreak().run()
    await TestReasonChain().run()
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
