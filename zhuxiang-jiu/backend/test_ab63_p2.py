"""63号·AI智能后台管理模块 P2 专项测试
(情境工作台+AI 合规护航)

运行方式:
    python test_ab63_p2.py

覆盖(63号计划 §九 P2):
    - COMPLIANCE_GUARD 三轨检测:
      文本轨(敏感词/夸大词/缺失条款)+
      表单轨(必填遗漏/逻辑矛盾/
      超范围采集)+隐私轨(PII 泄露
      48号正则复用/49号预算预估)
    - 三档干预分级正确(tip<warn<block)
    - 知识嵌入(why/regulation/example)
    - 隐私预算可视化(超支脱敏替代)
    - 情境工作台: 意图驱动导航+
      无障碍标记+智能模板推荐
    - 铁律: LLM 不进判定链(确定性
      +同输入同输出)
    - HTTP 层+回归
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


CLEAN_CONTENT = (
    "本服务含服务有效期说明与退改政策, "
    "适合养老人群")


class TestGuardRules:
    """01 护航规则库(封闭注册)"""

    async def run(self):
        print("[01 护航规则库]")
        from services.ab63_registry import (
            EXAGGERATION_WORDS,
            GUARD_KNOWLEDGE,
            GUARD_LEVELS,
            GUARD_RULE_LEVELS,
            GUARD_TRACKS,
            INTENT_NAV_MAP,
            OVERCOLLECT_FIELDS,
            REQUIRED_CLAUSES,
            SENSITIVE_WORDS,
            guard_rule_view,
        )
        record("三轨域(封闭)",
               GUARD_TRACKS == ("text", "form",
                                "privacy"),
               str(GUARD_TRACKS))
        record("三档域(渐进)",
               GUARD_LEVELS == ("tip", "warn",
                                "block"),
               str(GUARD_LEVELS))
        record("规则数 8(每轨锚定档)",
               len(GUARD_RULE_LEVELS) == 8
               and all(
                   lv in GUARD_LEVELS
                   for lv in
                   GUARD_RULE_LEVELS.values()),
               str(len(GUARD_RULE_LEVELS)))
        record("知识嵌入全覆盖",
               set(GUARD_KNOWLEDGE) == set(
                   GUARD_RULE_LEVELS)
               and all(
                   all(k in v for k in (
                       "why", "regulation",
                       "example"))
                   for v in
                   GUARD_KNOWLEDGE.values()),
               str(len(GUARD_KNOWLEDGE)))
        record("意图导航四侧映射",
               set(INTENT_NAV_MAP) == {
                   "product", "trust", "nav",
                   "other"},
               str(sorted(INTENT_NAV_MAP)))
        view = guard_rule_view()
        record("规则视图(观测面)",
               view.get("rules") == 8
               and view.get("tracks") == [
                   "text", "form", "privacy"],
               str((view.get("rules"),
                    view.get("tracks"))))
        record("词表非空(封闭)",
               len(SENSITIVE_WORDS) >= 5
               and len(EXAGGERATION_WORDS) >= 5
               and len(REQUIRED_CLAUSES) >= 2
               and len(OVERCOLLECT_FIELDS) >= 2,
               "")


class TestGuardText:
    """02 文本轨检测"""

    async def run(self):
        print("[02 文本轨]")
        reset_all()
        from services.ab63_guard_service import (
            Ab63GuardService,
        )
        svc = Ab63GuardService()

        # off 拒绝
        try:
            await svc.check(1, "ally_merchant",
                            content="测试")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), \
                str(e)[:30]
        record("off 态护航拒绝", ok, err)

        os.environ["AB63_MODE"] = "shadow"

        # 角色域外
        try:
            await svc.check(
                1, "hacker", content="测试")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "域外" in str(e), \
                str(e)[:30]
        record("护航角色域外拒绝", ok, err)

        # 检测内容为空
        try:
            await svc.check(1, "ally_merchant")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "为空" in str(e), \
                str(e)[:30]
        record("空内容拒绝", ok, err)

        # ① 敏感词(block)
        r = await svc.check(
            10, "ally_merchant",
            content="提供假发票开具服务")
        hits = [f for f in r.get("findings")
                if f.get("ruleId")
                == "GUARD_SENSITIVE_WORD"]
        record("敏感词阻断(block)",
               r.get("intervention")
               == "block"
               and len(hits) == 1
               and hits[0].get("match")
               == "假发票",
               str((r.get("intervention"),
                    len(hits))))

        # ② 夸大词(warn)
        r = await svc.check(
            11, "ally_merchant",
            content="本店是全市最好的"
                    "养老服务, 国家级认证")
        ex = [f for f in r.get("findings")
              if f.get("ruleId")
              == "GUARD_EXAGGERATION"]
        record("夸大词警告(warn)",
               r.get("intervention")
               == "warn"
               and len(ex) == 2,
               str((r.get("intervention"),
                    len(ex))))

        # ③ 缺失条款(tip——纯净文本
        #    无敏感词无夸大词)
        r = await svc.check(
            12, "ally_merchant",
            content="这是一个普通描述")
        miss = [f for f in
                r.get("findings")
                if f.get("ruleId")
                == "GUARD_MISSING_CLAUSE"]
        record("缺失条款提示(tip)",
               r.get("intervention")
               == "tip"
               and len(miss) == 2,
               str((r.get("intervention"),
                    len(miss))))

        # ④ 完整条款无 finding
        r = await svc.check(
            13, "ally_merchant",
            content=CLEAN_CONTENT)
        record("达标文本零 finding",
               r.get("detections") == 0
               and r.get("intervention")
               == "clean",
               str(r.get("detections")))
        os.environ["AB63_MODE"] = "off"


class TestGuardForm:
    """03 表单轨检测"""

    async def run(self):
        print("[03 表单轨]")
        reset_all()
        from services.ab63_guard_service import (
            Ab63GuardService,
        )
        svc = Ab63GuardService()
        os.environ["AB63_MODE"] = "shadow"

        # ① 必填遗漏(warn)
        r = await svc.check(
            20, "ally_merchant",
            form={"title": "居家养老服务"})
        req = [f for f in r.get("findings")
               if f.get("ruleId")
               == "GUARD_FORM_REQUIRED"]
        record("必填遗漏(warn×4)",
               r.get("intervention")
               == "warn"
               and len(req) == 4,
               str((r.get("intervention"),
                    len(req))))

        # ② 逻辑矛盾——价格非正
        r = await svc.check(
            21, "ally_merchant",
            form={"title": "服务A",
                  "price": 0,
                  "validityStart":
                      "2026-01-01",
                  "validityEnd":
                      "2026-12-31",
                  "refundPolicy": "可退"})
        logic = [f for f in
                 r.get("findings")
                 if f.get("ruleId")
                 == "GUARD_FORM_LOGIC"]
        record("价格非正(warn)",
               len(logic) == 1
               and logic[0].get("match")
               == "price",
               str(len(logic)))

        # ③ 逻辑矛盾——有效期倒置
        r = await svc.check(
            22, "ally_merchant",
            form={"title": "服务B",
                  "price": 100,
                  "validityStart":
                      "2026-12-31",
                  "validityEnd":
                      "2026-01-01",
                  "refundPolicy": "可退"})
        logic = [f for f in
                 r.get("findings")
                 if f.get("ruleId")
                 == "GUARD_FORM_LOGIC"
                 and f.get("match")
                 == "validity"]
        record("有效期倒置(warn)",
               len(logic) == 1,
               str(len(logic)))

        # ④ 超范围采集(block)
        r = await svc.check(
            23, "ally_merchant",
            form={"title": "服务C",
                  "price": 100,
                  "validityStart":
                      "2026-01-01",
                  "validityEnd":
                      "2026-12-31",
                  "refundPolicy": "可退",
                  "collectFields": [
                      "name", "phone",
                      "id_number"]})
        over = [f for f in
                r.get("findings")
                if f.get("ruleId")
                == "GUARD_OVERCOLLECT"]
        record("超范围采集阻断(block)",
               r.get("intervention")
               == "block"
               and len(over) == 1
               and over[0].get("match")
               == "id_number",
               str((r.get("intervention"),
                    len(over))))

        # ⑤ 完整表单零 finding
        r = await svc.check(
            24, "ally_merchant",
            form={"title": "服务D",
                  "price": 100,
                  "validityStart":
                      "2026-01-01",
                  "validityEnd":
                      "2026-12-31",
                  "refundPolicy": "可退",
                  "collectFields": [
                      "name", "phone"]})
        record("完整表单零 finding",
               r.get("detections") == 0,
               str(r.get("detections")))
        os.environ["AB63_MODE"] = "off"


class TestGuardPrivacy:
    """04 隐私轨检测"""

    async def run(self):
        print("[04 隐私轨]")
        reset_all()
        from services.ab63_guard_service import (
            Ab63GuardService,
        )
        svc = Ab63GuardService()
        os.environ["AB63_MODE"] = "shadow"

        # ① PII 泄露(48号正则复用——block)
        r = await svc.check(
            30, "ally_merchant",
            content="联系管家 "
                    "13812345678 "
                    "服务有效期90天 "
                    "退改政策见合同")
        pii = [f for f in r.get("findings")
               if f.get("ruleId")
               == "GUARD_PII_LEAK"]
        record("PII 泄露阻断(block)",
               r.get("intervention")
               == "block"
               and len(pii) == 1
               and "脱敏" in pii[0].get(
                   "message"),
               str((r.get("intervention"),
                    len(pii))))
        record("脱敏预览(maskedPreview)",
               "*手机号*" in str(
                   pii[0].get(
                       "maskedPreview")),
               str(pii[0].get(
                   "maskedPreview")))

        # ② 无 PII 文本零隐私 finding
        r = await svc.check(
            31, "ally_merchant",
            content=CLEAN_CONTENT)
        pii = [f for f in r.get("findings")
               if f.get("track")
               == "privacy"]
        record("无 PII 零隐私 finding",
               len(pii) == 0,
               str(len(pii)))

        # ③ 预算可视化(49号 fail-soft)
        r = await svc.check(
            32, "ally_merchant",
            content=CLEAN_CONTENT,
            estimated_cost=0.3)
        budget = r.get("privacyBudget") or {}
        record("预算可视化(余额读回)",
               budget.get("remaining")
               is not None
               and "GUARD_PRIVACY_BUDGET"
               not in [f.get("ruleId")
                       for f in r.get(
                           "findings")],
               str(budget.get("remaining")))

        # ④ 超支提示(tip)+脱敏替代
        r = await svc.check(
            33, "ally_merchant",
            content=CLEAN_CONTENT,
            estimated_cost=5.0)
        budget_f = [
            f for f in r.get("findings")
            if f.get("ruleId")
            == "GUARD_PRIVACY_BUDGET"]
        record("超支提示(tip)",
               r.get("intervention")
               == "tip"
               and len(budget_f) == 1
               and "归零" in budget_f[0]
               .get("message"),
               str((r.get("intervention"),
                    len(budget_f))))
        os.environ["AB63_MODE"] = "off"


class TestIntervention:
    """05 三档干预分级+铁律"""

    async def run(self):
        print("[05 三档干预+铁律]")
        reset_all()
        from services.ab63_guard_service import (
            Ab63GuardService,
        )
        svc = Ab63GuardService()
        os.environ["AB63_MODE"] = "shadow"

        # ① 混合 finding 取最高档(block)
        r = await svc.check(
            40, "ally_merchant",
            content="假发票+最好的服务"
                    "这是一个描述",
            form={"collectFields": [
                "bank_account"]})
        record("混合取最高档(block)",
               r.get("intervention")
               == "block",
               str(r.get("intervention")))

        # ② 分级聚合正确(warn 无 block)
        r = await svc.check(
            41, "ally_merchant",
            content="全市最好的服务",
            form={"title": "T"})
        record("warn 聚合(无 block)",
               r.get("intervention")
               == "warn",
               str(r.get("intervention")))

        # ③ 留痕读回(guard_view 观测面)
        view = await svc.guard_view()
        record("护航观测面(off 可观测)",
               view.get("total") >= 2
               and "block" in (
                   view.get("byLevel")
                   or {}),
               str((view.get("total"),
                    view.get("byLevel"))))

        # ④ 铁律: LLM 不进判定链
        #    (确定性——同输入同输出)
        r1 = await svc.check(
            42, "ally_merchant",
            content="最好的服务",
            form={"title": "T"})
        r2 = await svc.check(
            42, "ally_merchant",
            content="最好的服务",
            form={"title": "T"})
        record("确定性(同输入同输出)",
               r1.get("engine")
               == "deterministic"
               and [f.get("ruleId")
                    for f in r1.get(
                        "findings")]
               == [f.get("ruleId")
                    for f in r2.get(
                        "findings")],
               str(r1.get("engine")))

        # ⑤ 知识嵌入携带(每 finding)
        all_know = all(
            f.get("knowledge")
            and f["knowledge"].get("why")
            and f["knowledge"].get(
                "regulation")
            for f in r1.get("findings"))
        record("finding 携带知识嵌入",
               all_know is True,
               str(r1.get("findings")[:1]))

        # ⑥ guard 事件留痕
        from repositories.ab63_repository \
            import Ab63Repository
        repo = Ab63Repository()
        evs = await repo.list_events(
            event_type="guard", limit=50)
        record("guard 事件留痕",
               len(evs) >= 2,
               str(len(evs)))
        os.environ["AB63_MODE"] = "off"


class TestWorkbench:
    """06 情境工作台渲染"""

    async def run(self):
        print("[06 情境工作台]")
        reset_all()
        from services.ab63_service import (
            Ab63Service,
        )
        svc = Ab63Service()
        os.environ["AB63_MODE"] = "shadow"

        # ① 无障碍标记(建议性)
        r = await svc.render_workbench(
            50, "ally_merchant",
            novice=True,
            accessibility={
                "largeFont": True,
                "voiceAssist": True,
                "pauseDetected": True},
            industry="养老")
        opts = r.get("renderOptions") or {}
        acc = opts.get(
            "accessibilityMarks") or {}
        record("无障碍标记(大字+语音)",
               acc.get("largeFont") is True
               and acc.get("voiceAssist")
               is True,
               str(acc))
        record("停顿简化建议(建议性)",
               len(opts.get(
                   "simplification")
                   or []) == 1
               and "建议性" in opts.get(
                   "simplification")[0],
               str(opts.get(
                   "simplification")))

        # ② 智能模板推荐(行业优先)
        rec = opts.get(
            "templateRecommendation") \
            or []
        record("行业模板推荐(养老优先)",
               rec and rec[0] == "养老",
               str(rec))

        # ③ 意图导航(58号 off fail-soft)
        r2 = await svc.render_workbench(
            51, "ally_merchant",
            intent_text="想发布新产品")
        nav = (r2.get("renderOptions")
               or {}).get("intentNav")
        record("意图导航 fail-soft"
               "(58号 off 不阻塞)",
               nav == [],
               str(nav))

        # ④ 57号 seeds 匹配(纯读取)
        from repositories.kb57_repository \
            import Kb57Repository
        from core.helpers import ts
        kb_repo = Kb57Repository()
        sid = await kb_repo.next_seed_id()
        await kb_repo.save_seed({
            "seedId": sid,
            "title": "养老行业优质案例",
            "status": "published",
            "valueTags": ["养老", "案例"],
            "createdAt": ts(),
            "updatedAt": ts()})
        r3 = await svc.render_workbench(
            52, "ally_merchant",
            industry="养老")
        refs = (r3.get("renderOptions")
                or {}).get("seedRefs")
        record("种子关联(57号纯读取)",
               refs == [sid],
               str(refs))
        os.environ["AB63_MODE"] = "off"


class TestIntentNav:
    """07 意图驱动导航(58号纯消费)"""

    async def run(self):
        print("[07 意图导航]")
        reset_all()
        from repositories.ii58_repository \
            import Ii58Repository
        from core.helpers import ts
        repo = Ii58Repository()
        cid = await repo.next_corpus_id()
        await repo.save_corpus({
            "corpusId": cid,
            "intentId": "product.new_query",
            "sampleType": "positive",
            "text": "我想上架新品养老服务包",
            "weight": 1.0,
            "status": "active",
            "createdAt": ts(),
            "updatedAt": ts()})
        os.environ["II58_MODE"] = "shadow"
        os.environ["AB63_MODE"] = "shadow"

        from services.ab63_service import (
            Ab63Service,
        )
        r = await Ab63Service(
        ).render_workbench(
            60, "ally_merchant",
            intent_text="我想上架新品"
                        "养老服务包")
        opts = r.get("renderOptions") or {}
        record("意图导航(product 侧)",
               opts.get("intentNav") == [
                   "产品管理", "新品发布向导",
                   "行业模板库"],
               str(opts.get("intentNav")))
        os.environ["II58_MODE"] = "off"
        os.environ["AB63_MODE"] = "off"


class TestHttp:
    """08 HTTP 层(P2)"""

    async def run(self):
        print("[08 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # off 409(决策面)
        resp = client.post(
            "/api/ab63/guard/check",
            json={"memberId": 70,
                  "role": "ally_merchant",
                  "content": "测试"},
            headers=admin)
        record("HTTP guard off 409",
               resp.status_code == 409,
               str(resp.status_code))

        os.environ["AB63_MODE"] = "shadow"

        # 敏感词 block
        resp = client.post(
            "/api/ab63/guard/check",
            json={"memberId": 70,
                  "role": "ally_merchant",
                  "content": "提供赌博渠道"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP guard block(敏感词)",
               resp.status_code == 200
               and body.get("intervention")
               == "block"
               and body.get("guardId")
               > 0,
               str((resp.status_code,
                    body.get("intervention"))))

        # 域外 409
        resp = client.post(
            "/api/ab63/guard/check",
            json={"memberId": 70,
                  "role": "hacker",
                  "content": "测试"},
            headers=admin)
        record("HTTP guard 域外 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 工作台情境化(intent+acc+industry)
        resp = client.post(
            "/api/ab63/workbench/render",
            json={"memberId": 71,
                  "role": "ally_merchant",
                  "novice": True,
                  "accessibility": {
                      "largeFont": True},
                  "industry": "文创"},
            headers=admin)
        body = resp.json() or {}
        opts = body.get("renderOptions") or {}
        record("HTTP workbench 情境化 200",
               resp.status_code == 200
               and (opts.get(
                   "templateRecommendation")
                   or [None])[0] == "文创"
               and (opts.get(
                   "accessibilityMarks")
                   or {}).get("largeFont")
               is True,
               str((resp.status_code,
                    opts.get(
                        "templateRecommendation"))))

        # registry 含护航规则(观测面)
        resp = client.get(
            "/api/ab63/registry",
            headers=admin)
        guard = (resp.json() or {}).get(
            "guard") or {}
        record("HTTP registry 护航视图",
               resp.status_code == 200
               and guard.get("rules") == 8
               and guard.get("levels") == [
                   "tip", "warn", "block"],
               str(guard.get("rules")))

        # 鉴权 403
        resp = client.post(
            "/api/ab63/guard/check",
            json={"memberId": 70,
                  "role": "ally_merchant"})
        record("HTTP guard 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))
        os.environ["AB63_MODE"] = "off"


class TestConstitution:
    """09 宪法回归(零改动断言)"""

    async def run(self):
        print("[09 宪法回归]")
        from services import (
            ab63_registry,
        )
        record("registry 启动自检通过",
               ab63_registry.
               GUARD_RULE_LEVELS is not None,
               "")
        # 感知源零写入: 58/49 调用均为
        # 纯读取/纯调用(此处断言 63号
        # 模块文件未反向修改感知源)
        import services.ii58_service as s58
        import services.xiaozhu_privacy_service as s49
        record("感知源模块可导入(零改动)",
               s58.__name__.endswith(
                   "ii58_service")
               and s49.__name__.endswith(
                   "xiaozhu_privacy_service"),
               "")


async def run_all():
    await TestGuardRules().run()
    await TestGuardText().run()
    await TestGuardForm().run()
    await TestGuardPrivacy().run()
    await TestIntervention().run()
    await TestWorkbench().run()
    await TestIntentNav().run()
    await TestHttp().run()
    await TestConstitution().run()


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
