"""53号·小竹智能登录引擎 P3 专项测试
(角色专属入口+价值钩子)

运行方式:
    python test_login53_p3.py

覆盖(53号计划 §九 P3):
    - 四态门户配置: new(价值启蒙+快速建档)/
      active(无感续接+意图直达页)/dormant
      (损失规避+错过时间轴)/high_risk
      (透明保护——去污名化禁红色警告)
    - 价值钩子投放: 登录前(preAuth)四态差异化
      ——new 交互演示/active 待办摘要/
      dormant 错过收益/high_risk 风险透明
    - 沉睡唤醒: 错过收益时间轴(天数+估算+
      恢复路径)
    - 预算感知通道推荐
    - off 铁律+端点+零影响(宪法断言)
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
os.environ["LOGIN53_MODE"] = "off"

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


async def seed_member(member_id: int,
                      nickname: str = "门户测试",
                      created_days_ago: int = 90):
    from datetime import datetime, timedelta
    from repositories.member_repository import (
        MemberRepository,
    )
    created = (datetime.now()
               - timedelta(days=created_days_ago)
               ).isoformat()
    await MemberRepository().save(member_id, {
        "id": member_id,
        "phone": f"139{member_id:08d}",
        "nickname": nickname, "role": "member",
        "created_at": created, "points": 100,
        "status": 1,
    })


async def seed_profile(member_id: int, **kwargs):
    """种子: 入口档案(直接控制四态判定输入)"""
    from services.login53_service import (
        Login53Service,
    )
    svc = Login53Service()
    record = {"memberId": member_id}
    record.update(kwargs)
    await svc.repo.save_profile(record)


class TestPortalConfig:
    """01 四态门户配置"""

    async def run(self):
        print("[01 四态门户配置]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()

        # off 态拒绝(编排面)
        try:
            await svc.portal_config(5480)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e) \
                       and "观测面" in str(e), \
                str(e)[:40]
        record("off 态门户配置拒绝", ok, err)

        os.environ["LOGIN53_MODE"] = "on"

        # new 态: 价值启蒙
        await seed_member(5480, created_days_ago=2)
        c = await svc.portal_config(5480)
        record("new 态配置(价值启蒙)",
               c["portalState"] == "new"
               and (c["ui"] or {}).get("focus")
               == "价值启蒙+快速建档"
               and (c["ui"] or {}).get("deEmphasize")
               == "登录表单(弱化)",
               str(c["portalState"]))
        record("new 态三步引导",
               len((c["ui"] or {})
                   .get("guideSteps") or []) == 3,
               str(len((c["ui"] or {})
                   .get("guideSteps") or [])))

        # active 态: 无感续接+意图直达
        reset_all()
        await seed_member(5481)
        await seed_profile(
            5481, lastLoginAt="2026-09-04T10:00:00",
            topIntent="shopping",
            accountAgeDays=90)
        c2 = await svc.portal_config(5481)
        record("active 态配置(无感续接)",
               c2["portalState"] == "active"
               and (c2["ui"] or {}).get("directPage")
               == "商品列表页",
               str(c2["portalState"]))
        record("active 态价值条",
               "光晕" in str(
                   (c2["ui"] or {}).get("valueBar")),
               str((c2["ui"] or {}).get("valueBar")))

        # dormant 态: 损失规避+时间轴
        reset_all()
        await seed_member(5482)
        await seed_profile(
            5482, lastLoginAt="2026-07-20T10:00:00",
            accountAgeDays=365)
        c3 = await svc.portal_config(5482)
        record("dormant 态配置(损失规避)",
               c3["portalState"] == "dormant"
               and (c3["ui"] or {}).get("focus")
               == "损失规避+一键恢复",
               str(c3["portalState"]))
        timeline = (c3["ui"] or {}) \
            .get("missedTimeline") or {}
        record("沉睡错过时间轴(天数+估算)",
               timeline.get("days", 0) > 30
               and timeline.get("estimatedMissed", 0)
               > 0
               and timeline.get("restorePath"),
               str((timeline.get("days"),
                    timeline.get(
                        "estimatedMissed"))))

        # high_risk 态: 透明保护(去污名化)
        reset_all()
        await seed_member(5483)
        await seed_profile(
            5483, riskFlagged=1,
            lastLoginAt="2026-09-04T10:00:00",
            accountAgeDays=90)
        c4 = await svc.portal_config(5483)
        record("high_risk 态配置(透明保护)",
               c4["portalState"] == "high_risk"
               and (c4["ui"] or {}).get("focus")
               == "透明保护(去污名化)",
               str(c4["portalState"]))
        record("高危去污名化(禁红色+人工兜底)",
               "禁红色警告" in str(
                   (c4["ui"] or {})
                   .get("riskCard"))
               and (c4["ui"] or {})
               .get("humanSupport") is True,
               str((c4["ui"] or {}).get("riskCard")))

        # 通用字段(通道推荐+预算)
        record("配置含通道推荐+预算探针",
               len(c["recommendedChannels"]) >= 2
               and "remaining" in (
                   c["budget"] or {}),
               str(c["recommendedChannels"]))
        os.environ["LOGIN53_MODE"] = "off"


class TestPortalHook:
    """02 登录前价值钩子投放"""

    async def run(self):
        print("[02 价值钩子]")
        reset_all()
        from services.login53_service import (
            Login53Service,
        )
        svc = Login53Service()

        try:
            await svc.generate_portal_hook(5490)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态钩子投放拒绝", ok, err)

        os.environ["LOGIN53_MODE"] = "on"

        # new 态钩子: 交互式价值演示
        await seed_member(5490, created_days_ago=1)
        h = await svc.generate_portal_hook(5490)
        record("new 态钩子(价值演示)",
               h["portalState"] == "new"
               and (h["hook"] or {}).get("type")
               == "value_demo"
               and "攒信值" in (h["hook"] or {})
               .get("content", ""),
               str((h["hook"] or {}).get("type")))
        record("登录前投放标注(preAuth)",
               h.get("preAuth") is True,
               str(h.get("preAuth")))

        # active 态钩子: 待办摘要
        reset_all()
        await seed_member(5491)
        await seed_profile(
            5491, lastLoginAt="2026-09-04T10:00:00",
            accountAgeDays=90)
        h2 = await svc.generate_portal_hook(5491)
        record("active 态钩子(待办摘要)",
               h2["portalState"] == "active"
               and (h2["hook"] or {}).get("type")
               == "todo_summary"
               and "待办" in (h2["hook"] or {})
               .get("content", ""),
               str((h2["hook"] or {}).get("type")))

        # dormant 态钩子: 错过收益
        reset_all()
        await seed_member(5492)
        await seed_profile(
            5492, lastLoginAt="2026-07-15T10:00:00",
            accountAgeDays=365)
        h3 = await svc.generate_portal_hook(5492)
        record("dormant 态钩子(损失规避)",
               h3["portalState"] == "dormant"
               and (h3["hook"] or {}).get("type")
               == "loss_avoidance"
               and "恢复" in (h3["hook"] or {})
               .get("content", ""),
               str((h3["hook"] or {}).get("type")))
        record("沉睡钩子绑定时间轴",
               ((h3["hook"] or {})
                .get("missedTimeline") or {})
               .get("days", 0) > 30,
               str(((h3["hook"] or {})
                    .get("missedTimeline") or {})
                   .get("days")))

        # high_risk 态钩子: 风险透明
        reset_all()
        await seed_member(5493)
        await seed_profile(
            5493, riskFlagged=1,
            lastLoginAt="2026-09-04T10:00:00",
            accountAgeDays=90)
        h4 = await svc.generate_portal_hook(5493)
        record("high_risk 态钩子(风险透明)",
               h4["portalState"] == "high_risk"
               and (h4["hook"] or {}).get("type")
               == "risk_transparency"
               and "这不是您的错" in (
                   h4["hook"] or {}).get(
                   "content", ""),
               str((h4["hook"] or {}).get("type")))

        # 钩子话术绑定
        record("钩子话术渲染(script)",
               (h["script"] or {}).get("key")
               == "wake_login"
               and (h["script"] or {}).get("text"),
               str((h["script"] or {}).get("key")))
        os.environ["LOGIN53_MODE"] = "off"


class TestEndpoints:
    """03 端点+鉴权+零影响"""

    async def run(self):
        print("[03 端点+鉴权]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        member = {"X-Member-Id": "5500"}

        # off 态 409
        resp = client.get("/api/login53/portal/config",
                          headers=member)
        record("HTTP portal/config off 409",
               resp.status_code == 409,
               str(resp.status_code))
        resp = client.post("/api/login53/portal/hook",
                           headers=member)
        record("HTTP portal/hook off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 观测面对照: GET /portal(off 可访问)
        resp = client.get("/api/login53/portal",
                          headers=member)
        record("GET /portal 观测面 off 可访问",
               resp.status_code == 200,
               str(resp.status_code))

        # on 态端到端
        os.environ["LOGIN53_MODE"] = "on"
        await seed_member(5500, created_days_ago=1)

        resp = client.get("/api/login53/portal/config",
                          headers=member)
        body = resp.json() or {}
        record("HTTP portal/config 200(new)",
               resp.status_code == 200
               and ((body.get("config") or {})
                    .get("portalState")) == "new",
               str(resp.status_code))

        resp = client.post("/api/login53/portal/hook",
                           headers=member)
        body = resp.json() or {}
        record("HTTP portal/hook 200(价值演示)",
               resp.status_code == 200
               and ((body.get("portalHook") or {})
                    .get("portalState")) == "new"
               and (((body.get("portalHook") or {})
                     .get("hook") or {})
                    .get("type")) == "value_demo",
               str(resp.status_code))

        # 鉴权
        resp = client.get("/api/login53/portal/config")
        record("portal/config 无 Member 403",
               resp.status_code == 403,
               str(resp.status_code))
        resp = client.post("/api/login53/portal/hook")
        record("portal/hook 无 Member 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 零影响: 宪法断言
        from routes.entry_routes import (
            router as entry_router,
        )
        entry_count = sum(
            1 for r in entry_router.routes)
        record("39号 entry 路由零改动(24)",
               entry_count == 24, str(entry_count))
        from services.xiaozhu_fc_registry import (
            TOOL_REGISTRY,
        )
        record("49号17工具零改动",
               len(TOOL_REGISTRY) == 17)
        os.environ["LOGIN53_MODE"] = "off"


async def run_all():
    await TestPortalConfig().run()
    await TestPortalHook().run()
    await TestEndpoints().run()


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
