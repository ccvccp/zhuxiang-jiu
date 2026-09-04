"""49号·小竹可信函数调用深化 P4 专项测试
(红队测试与收官)

运行方式:
    python test_xiaozhu_p49_4.py

覆盖(49号计划 §六 P4):
    - 红队四类攻击向量 14 用例(跑真网关):
      A 工具描述越狱(伪造禁令覆盖×3)
      B 成本篡改(零成本/负成本/预算绕过×3)
      C 伪造 token(随机/格式/重放/跨用户/动作劫持×5)
      D 越权诱导(未注册工具/注入指令串/只读零摩擦×3)
    - token 拒绝分布(executor 五类计数)
    - 看板 FC 分区(调用量/失败降级/预算消耗/拒绝分布/
      fail-soft)
    - 上线检查清单七项逐项核验(计划 §十二)
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

# 红队整跑报告(跨类共享——只跑一次)
RT_REPORT = {}


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
    import services.xiaozhu_executor as ex_mod
    ex_mod._EXECUTOR_SINGLETON = None


class TestRedteamRun:
    """红队整跑(服务级——一次 run 断言 14 用例+报告)"""

    async def run(self):
        print("[01 红队整跑(真网关)]")
        reset_all()
        from services.xiaozhu_fc_redteam import (
            XiaozhuFcRedteamService,
        )
        RT_REPORT["report"] = await \
            XiaozhuFcRedteamService().run()
        r = RT_REPORT["report"]
        cases = {c["caseId"]: c
                for c in r.get("cases") or []}
        record("用例总数(14)", r.get("total") == 14,
               str(r.get("total")))
        record("全部阻断(breached=0)",
               r.get("breached") == 0,
               str([(c["caseId"], c["evidence"])
                    for c in r.get("cases") or []
                    if not c.get("blocked")]))
        # A 工具描述越狱
        for cid in ("RT-01", "RT-02", "RT-03"):
            record(f"{cid} 伪造禁令覆盖被拒",
                   cases.get(cid, {}).get("blocked") is True,
                   str(cases.get(cid, {}).get("evidence")))
        # B 成本篡改
        for cid in ("RT-04", "RT-05", "RT-06"):
            record(f"{cid} 成本篡改无效",
                   cases.get(cid, {}).get("blocked") is True,
                   str(cases.get(cid, {}).get("evidence")))
        # C 伪造 token
        for cid in ("RT-07", "RT-08", "RT-09", "RT-10",
                    "RT-11"):
            record(f"{cid} 伪造 token 被拒",
                   cases.get(cid, {}).get("blocked") is True,
                   str(cases.get(cid, {}).get("evidence")))
        # D 越权诱导
        for cid in ("RT-12", "RT-13", "RT-14"):
            record(f"{cid} 越权诱导被拒",
                   cases.get(cid, {}).get("blocked") is True,
                   str(cases.get(cid, {}).get("evidence")))
        # 向量分布
        v = r.get("vectors") or {}
        record("攻击向量分布(3/3/5/3)",
               v.get("jailbreak") == 3
               and v.get("costTamper") == 3
               and v.get("forgedToken") == 5
               and v.get("privEscalation") == 3, str(v))
        # 拒绝分布(进程级——红队至少触发五类拒绝)
        rej = r.get("tokenRejects") or {}
        record("token 拒绝分布计数",
               (rej.get("notFound") or 0) >= 2
               and (rej.get("used") or 0) >= 1
               and (rej.get("crossUser") or 0) >= 1
               and (rej.get("actionMismatch") or 0) >= 1,
               str(rej))
        # 红队拒绝落审计(kind=fallback 证据可溯)
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        rows = await Xiaozhu48Repository().list_records(
            Xiaozhu48Repository.TABLE_FC_AUDIT,
            limit=500)
        fallbacks = [x for x in rows
                    if x.get("kind") == "fallback"]
        record("红队拒绝留审计流水",
               len(fallbacks) >= 6, str(len(fallbacks)))


class TestConsentRejects:
    """token 拒绝分布(executor 五类计数——含过期路径)"""

    async def run(self):
        print("[02 token 拒绝分布(executor)]")
        from services.xiaozhu_executor import get_executor
        ex = get_executor()
        # notFound
        before = ex.consent_stats()["notFound"]
        try:
            ex.validate_consent_token("ct-nope", 1,
                                      "trust.convert")
        except KeyError:
            pass
        record("notFound 计数(伪造)",
               ex.consent_stats()["notFound"] == before + 1)
        # expired(手动过期)
        ct = ex._issue_consent_token(2, "trust.convert")
        ex._consent_tokens[ct]["expiresAt"] = 0
        try:
            ex.validate_consent_token(ct, 2,
                                      "trust.convert")
            record("expired 计数", False, "未抛")
        except KeyError:
            record("expired 计数(过期)",
                   ex.consent_stats()["expired"] >= 1)
        # used(重放)
        ct2 = ex._issue_consent_token(3, "trust.convert")
        ex.validate_consent_token(ct2, 3, "trust.convert")
        try:
            ex.validate_consent_token(ct2, 3,
                                      "trust.convert")
            record("used 计数", False, "未抛")
        except KeyError:
            record("used 计数(一次性)",
                   ex.consent_stats()["used"] >= 1)
        # crossUser
        ct3 = ex._issue_consent_token(4, "trust.convert")
        try:
            ex.validate_consent_token(ct3, 5,
                                      "trust.convert")
            record("crossUser 计数", False, "未抛")
        except ValueError:
            record("crossUser 计数(声纹绑定)",
                   ex.consent_stats()["crossUser"] >= 1)
        # actionMismatch
        ct4 = ex._issue_consent_token(6, "trust.convert")
        try:
            ex.validate_consent_token(ct4, 6,
                                      "repair.execute")
            record("actionMismatch 计数", False, "未抛")
        except ValueError:
            record("actionMismatch 计数(动作劫持)",
                   ex.consent_stats()["actionMismatch"] >= 1)
        # total 口径
        s = ex.consent_stats()
        record("total=五类之和",
               s["total"] == sum(
                   s[k] for k in ("notFound", "expired",
                                  "used", "crossUser",
                                  "actionMismatch")))


class TestDashboardFc:
    """看板 FC 分区(七区块聚合)"""

    async def run(self):
        print("[03 看板 FC 分区]")
        from services.xiaozhu_dashboard_service import (
            XiaozhuDashboardService,
        )
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        svc = XiaozhuDashboardService()
        board = await svc.build()
        zones = board.get("zones") or {}
        record("看板七区块(fail-soft 无错)",
               len(zones) == 7
               and not (board.get("zoneErrors") or []),
               str(board.get("zoneErrors")))
        fc = zones.get("fc") or {}
        # 调用量对账(审计流水总数)
        rows = await Xiaozhu48Repository().list_records(
            Xiaozhu48Repository.TABLE_FC_AUDIT,
            limit=5000)
        record("FC 调用量=审计流水数",
               fc.get("calls") == len(rows),
               f"{fc.get('calls')} vs {len(rows)}")
        # 失败降级
        fallbacks = [x for x in rows
                     if x.get("kind") == "fallback"]
        expect_rate = round(
            len(fallbacks) / len(rows) * 100, 1) \
            if rows else None
        record("失败降级计数与率",
               fc.get("byKind", {}).get("fallback")
               == len(fallbacks)
               and fc.get("fallbackRate") == expect_rate,
               f"{fc.get('byKind')} / "
               f"{fc.get('fallbackRate')}")
        # 预算消耗(成本口径)
        cost = round(sum(float(x.get("privacyCost") or 0)
                        for x in rows), 2)
        record("预算消耗=流水成本合计",
               fc.get("privacyCostTotal") == cost,
               f"{fc.get('privacyCostTotal')} vs {cost}")
        record("预算账户聚合字段",
               isinstance(fc.get("budget"), dict)
               and "accounts" in fc.get("budget", {})
               and "usedTodayTotal"
               in fc.get("budget", {}))
        # token 拒绝分布透出
        rej = fc.get("consentRejects") or {}
        record("拒绝分布透出(五类)",
               all(k in rej for k in (
                   "notFound", "expired", "used",
                   "crossUser", "actionMismatch")),
               str(rej)[:60])
        # 干预端点提示(红队复跑)
        record("干预提示含红队端点",
               "fc/redteam" in str(
                   board.get("intervention")))
        # fail-soft: FC 数据源故障不阻断看板
        real = Xiaozhu48Repository()

        class _WrapRepo:
            def __getattr__(self, name):
                return getattr(real, name)

            async def list_records(self, table,
                                   limit=100):
                if table == real.TABLE_FC_AUDIT:
                    raise RuntimeError("FC 数据源故障")
                return await real.list_records(
                    table, limit=limit)

        svc2 = XiaozhuDashboardService(repo=_WrapRepo())
        board2 = await svc2.build()
        record("FC 分区 fail-soft(不阻断看板)",
               "fc" in (board2.get("zoneErrors") or [])
               and (board2.get("zones") or {})
               .get("fc", {}).get("error"),
               str(board2.get("zoneErrors")))
        record("fail-soft 其余区块正常",
               len((board2.get("zones") or {})) == 7
               and "usage" in (board2.get("zones")
                               or {}))


class TestChecklist:
    """上线检查清单七项(计划 §十二)——逐项核验"""

    async def run(self):
        print("[04 上线检查清单七项]")
        from services.xiaozhu_fc_registry import (
            TOOL_REGISTRY, build_tool_prompt,
            TIER_WRITE, TIER_SENSITIVE,
        )
        from services.xiaozhu_explainability_service \
            import EXPLAINABLE_ACTIONS
        # 1 Token 时效 ≤60s
        from services.xiaozhu_executor import (
            CONSENT_TOKEN_TTL, CONFIRM_TOKEN_TTL,
        )
        record("① Token 时效 ≤60s",
               CONSENT_TOKEN_TTL <= 60
               and CONFIRM_TOKEN_TTL <= 60,
               f"{CONSENT_TOKEN_TTL}/"
               f"{CONFIRM_TOKEN_TTL}")
        # 2 privacy_cost 描述与注册表一致
        prompt = build_tool_prompt()
        consistent = all(
            f"privacy_cost={t['privacyCost']}" in prompt
            or t["privacyCost"] in (0.0,)
            for t in TOOL_REGISTRY.values())
        record("② cost 描述与注册表一致",
               consistent and all(
                   t["privacyCost"] >= 0
                   for t in TOOL_REGISTRY.values()))
        # 3 explainability_ref 必填(写/高敏全覆盖)
        explainable = {a for a, t in
                       TOOL_REGISTRY.items()
                       if t["tier"] in (TIER_WRITE,
                                        TIER_SENSITIVE)}
        record("③ 写/高敏 ref 必填全覆盖",
               explainable
               <= EXPLAINABLE_ACTIONS,
               f"{explainable - EXPLAINABLE_ACTIONS}")
        # 4 兜底话术全覆盖
        record("④ 兜底话术全覆盖",
               all((t.get("safeMessage") or "").strip()
                   for t in TOOL_REGISTRY.values()))
        # 5 Prompt 注入防护(红队 breached=0)
        r = RT_REPORT.get("report") or {}
        record("⑤ 红队注入防护(14/14 阻断)",
               r.get("total") == 14
               and r.get("breached") == 0,
               f"{r.get('blocked')}/{r.get('total')}")
        # 6 审计日志完整(六字段——红队流水抽验)
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        rows = await Xiaozhu48Repository().list_records(
            Xiaozhu48Repository.TABLE_FC_AUDIT,
            limit=500)
        sample = [x for x in rows
                  if x.get("kind") == "fallback"][:5] or rows[:5]
        record("⑥ 审计六字段齐备",
               bool(sample) and all(
                   all(k in x for k in (
                       "memberId", "toolName",
                       "consentTokenHash", "privacyCost",
                       "ts", "kind")) for x in sample))
        # 7 声纹绑定(确定性摘要+跨用户拒绝)
        from services.xiaozhu_executor import (
            XiaozhuExecutor,
        )
        d1 = XiaozhuExecutor.speaker_digest(77)
        d2 = XiaozhuExecutor.speaker_digest(77)
        d3 = XiaozhuExecutor.speaker_digest(78)
        record("⑦ 声纹代理绑定(确定性+区分)",
               d1 == d2 and d1 != d3 and len(d1) == 32)


async def run_all():
    await TestRedteamRun().run()
    await TestConsentRejects().run()
    await TestDashboardFc().run()
    await TestChecklist().run()


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
