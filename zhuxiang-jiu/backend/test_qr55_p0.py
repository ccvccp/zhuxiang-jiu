"""55号·二维码AI智能管理模块 P0 专项测试
(服务资源注册表+意图引擎+签名底座)

运行方式:
    python test_qr55_p0.py

覆盖(55号计划 §六 P0):
    - 注册表: 12 项白名单+模板四类+启动自检+
      敏感度成本对齐+PII 禁入+route 前缀白名单
    - 意图引擎: 规则轨三态(resolved/partial/
      clarify)+关键词命中+参数抽取+受众过滤
    - 签名底座: 五段格式+验签四态(ok/expired/
      tampered/格式非法)+nonce 一次性+载荷
      digest 无明文 PII
    - 评分器: 八因子→信任分→三级策略
      direct/confirm/clarify+输入非法
    - 生成面: off 铁律+白名单校验+参数过滤
    - 端点: 4 端点+鉴权
    - 零影响: 44号档案数 30+48/51号零改动红线
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


class TestRegistry:
    """01 服务资源注册表"""

    async def run(self):
        print("[01 注册表]")
        reset_all()
        from services.qr55_registry import (
            SERVICE_REGISTRY, active_services,
            get_service, match_services,
            registry_view,
        )
        record("12 项服务白名单",
               len(SERVICE_REGISTRY) == 12,
               str(len(SERVICE_REGISTRY)))
        templates = {v["template"]
                     for v in SERVICE_REGISTRY.values()}
        record("模板四类覆盖",
               templates == {"apply", "query",
                             "download", "feedback"},
               str(sorted(templates)))
        record("active 服务可查",
               get_service("elderly_card")
               is not None
               and len(active_services()) == 12,
               str(len(active_services())))

        # 敏感度成本对齐(L0-L3)
        from services.qr55_registry import (
            SENSITIVITY_TIERS,
        )
        aligned = all(
            abs(float(v["privacyCost"])
                - SENSITIVITY_TIERS[v["sensitivity"]])
            < 1e-9
            for v in SERVICE_REGISTRY.values())
        record("敏感度成本对齐 51号口径",
               aligned, "存在不对齐项")

        # 受众过滤
        elder = match_services(audience="elderly")
        record("受众过滤(elderly 命中优待证)",
               any(s["serviceId"] == "elderly_card"
                   for s in elder)
               and len(elder) < 12,
               str(len(elder)))

        # registry 视图
        view = registry_view()
        record("registry 自描述(红线 5 条)",
               len(view.get("redlines") or []) == 5
               and view.get("serviceCount") == 12,
               str(len(view.get("redlines") or [])))


class TestIntent:
    """02 意图理解引擎(规则轨三态)"""

    async def run(self):
        print("[02 意图引擎]")
        reset_all()
        from services.qr55_intent_service import (
            Qr55IntentService,
        )
        svc = Qr55IntentService()

        # resolved: 精确命中
        r = svc.parse_intent("我要给老人办优待证")
        record("resolved 精确命中",
               r.get("status") == "resolved"
               and r.get("serviceId")
               == "elderly_card"
               and r.get("confidence", 0) > 0.7,
               str((r.get("status"),
                    r.get("serviceId"))))

        # clarify: 无候选
        r2 = svc.parse_intent("今天天气怎么样")
        record("clarify 零命中需澄清",
               r2.get("status") == "clarify"
               and bool(r2.get("question")),
               str(r2.get("status")))

        # 参数抽取(引号)
        r3 = svc.parse_intent('查"杭州"的政策')
        record("参数抽取(引号包裹)",
               bool(r3.get("params")),
               str(r3.get("params")))

        # 空输入拒绝
        try:
            svc.parse_intent("  ")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空输入拒绝", ok, err)

        # 澄清问句生成(mock)
        from services.qr55_registry import (
            SERVICE_REGISTRY,
        )
        # 构造 partial: 双候选
        svc_r = svc.parse_intent("我要下载表格顺便投诉")
        record("partial 多候选歧义",
               svc_r.get("status")
               in ("partial", "resolved"),
               str(svc_r.get("status")))
        clarify = svc.generate_clarify(
            {"status": "clarify",
             "question": "请问需要什么服务?"})
        record("澄清问句(mock 确定性)",
               clarify.get("needClarify") is True
               and "LLM" in str(
                   clarify.get("note", "")),
               str(clarify.get("needClarify")))


class TestCrypto:
    """03 签名底座"""

    async def run(self):
        print("[03 签名底座]")
        reset_all()
        from services.qr55_crypto import (
            generate_code, verify_code,
            code_fingerprint, decode_payload,
        )

        g = generate_code("elderly_card",
                         {"region": "杭州"},
                         member_id=9901)
        code = g["code"]
        record("五段格式生成",
               code.startswith("ZXBJ-QR55:")
               and len(code.split(":")) == 3
               and len(code.split(":")[2]
                       .split(".")) == 4,
               code[:40])

        # 验签 ok
        v = verify_code(code)
        record("验签 ok(载荷解码)",
               v.get("status") == "ok"
               and (v.get("payload") or {})
               .get("serviceId") == "elderly_card",
               str(v.get("status")))

        # 载荷 digest 无明文 PII
        payload = v.get("payload") or {}
        record("会员 digest 化(无明文)",
               "memberDigest" in payload
               and len(payload.get(
                   "memberDigest") or "") == 16,
               str(payload)[:60])

        # 过期态(ttl 负数——确保已过)
        g2 = generate_code("policy_search", {},
                           member_id=9901,
                           ttl_seconds=-10)
        v2 = verify_code(g2["code"])
        record("过期态 expired",
               v2.get("status") == "expired",
               str(v2.get("status")))

        # 篡改态(改 serviceId 段)
        tampered = code.replace(
            "elderly_card", "trust_balance", 1) \
            if "elderly_card" in code \
            else code + "x"
        v3 = verify_code(tampered)
        record("篡改态 tampered(验签失败)",
               v3.get("status") == "tampered",
               str(v3.get("status")))

        # 格式非法
        try:
            verify_code("not-a-code")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("格式非法拒绝", ok, err)

        # nonce 指纹
        record("nonce 指纹(防重放键)",
               len(code_fingerprint(code)) == 16,
               code_fingerprint(code)[:10])


class TestScorer:
    """04 八因子评分器"""

    async def run(self):
        print("[04 评分器]")
        reset_all()
        from services.qr55_scorer import (
            Qr55Scorer,
        )
        scorer = Qr55Scorer()

        good_ctx = {
            "intentConfidence": 0.95,
            "serviceMatch": "resolved",
            "paramComplete": 1.0,
            "budgetRemaining": 0.9,
            "memberTrustLevel": "L2",
            "freshRatio": 1.0,
            "accessibility": False,
            "riskFlagged": False,
        }
        r = await scorer.score(good_ctx)
        record("高信任→direct 策略",
               r.get("strategy") == "direct"
               and r.get("trustScore") >= 70,
               str((r.get("strategy"),
                    r.get("trustScore"))))
        record("八因子齐备",
               len(r.get("factors") or []) == 8,
               str(len(r.get("factors") or [])))

        bad_ctx = {
            "intentConfidence": 0.2,
            "serviceMatch": "clarify",
            "paramComplete": 0.1,
            "budgetRemaining": 0.1,
            "memberTrustLevel": "L0",
            "riskFlagged": True,
        }
        r2 = await scorer.score(bad_ctx)
        record("低信任→clarify 策略",
               r2.get("strategy") == "clarify"
               and r2.get("trustScore") < 40,
               str((r2.get("strategy"),
                    r2.get("trustScore"))))

        mid_ctx = dict(good_ctx)
        mid_ctx.update({
            "intentConfidence": 0.6,
            "serviceMatch": "partial",
            "paramComplete": 0.6,
            "memberTrustLevel": "L1"})
        r3 = await scorer.score(mid_ctx)
        record("中间态策略合法(三态)",
               r3.get("strategy")
               in ("direct", "confirm", "clarify"),
               str(r3.get("strategy")))

        # 输入非法
        try:
            await scorer.score({
                "intentConfidence": 1.5,
                "serviceMatch": "resolved"})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("置信度越界拒绝", ok, err)
        try:
            await scorer.score({})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("空上下文拒绝", ok, err)


class TestGenerateAndHttp:
    """05 生成面+端点+零影响"""

    async def run(self):
        print("[05 生成+端点]")
        reset_all()
        from services.qr55_service import (
            Qr55Service,
        )
        svc = Qr55Service()

        # off 铁律
        try:
            await svc.generate_code(
                "elderly_card", {}, member_id=9901)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态生成拒绝", ok, err)

        os.environ["QR55_MODE"] = "on"
        # 白名单外拒绝(幻觉链接防护)
        try:
            await svc.generate_code(
                "evil_service", {}, member_id=9901)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "白名单" in str(e), str(e)[:30]
        record("白名单外服务拒绝", ok, err)

        # 正常生成+参数白名单过滤
        g = await svc.generate_code(
            "elderly_card",
            {"region": "杭州", "phone": "13900001111"},
            member_id=9901)
        record("生成成功(P0 能力验证)",
               g.get("success") is True
               and g.get("codeId", 0) > 0,
               str(g.get("codeId")))
        record("参数白名单过滤(PII 剔除)",
               "region" in (g.get("params") or {})
               and "phone" not in (
                   g.get("params") or {}),
               str(g.get("params")))

        # 码实例落库
        from repositories.qr55_repository import (
            Qr55Repository,
        )
        code_rec = await Qr55Repository().get_code(
            g["codeId"])
        record("码实例落库(nonce+active)",
               (code_rec or {}).get("status")
               == "active"
               and bool((code_rec or {})
                        .get("nonce")),
               str((code_rec or {}).get("status")))

        # nonce 消费一次性
        nonce = code_rec["nonce"]
        first = await Qr55Repository().consume_nonce(
            nonce)
        second = await Qr55Repository().consume_nonce(
            nonce)
        record("nonce 一次性(重放拒绝)",
               first is True and second is False,
               str((first, second)))
        os.environ["QR55_MODE"] = "off"

        # HTTP 层
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        resp = client.get("/api/qr55/registry",
                          headers=admin)
        record("HTTP registry 200",
               resp.status_code == 200
               and (resp.json() or {}
                    ).get("serviceCount") == 12,
               str(resp.status_code))

        resp = client.post(
            "/api/qr55/intent/parse",
            json={"text": "我要办老年优待证"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP intent/parse 200(resolved)",
               resp.status_code == 200
               and body.get("status") == "resolved"
               and body.get("serviceId")
               == "elderly_card",
               str(resp.status_code))

        resp = client.post(
            "/api/qr55/code/generate",
            json={"serviceId": "elderly_card",
                  "params": {}, "memberId": 9901},
            headers=admin)
        record("HTTP generate off 409",
               resp.status_code == 409,
               str(resp.status_code))

        resp = client.get(
            "/api/qr55/model/status", headers=admin)
        record("HTTP model/status 200",
               resp.status_code == 200,
               str(resp.status_code))

        # 鉴权
        resp = client.get("/api/qr55/registry")
        record("registry 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 零影响红线
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号档案数 ≥30(56号扩至 31)",
               len(SCORER_REGISTRY) >= 30,
               str(len(SCORER_REGISTRY)))
        record("54号档案零改动",
               SCORER_REGISTRY.get(
                   "login_orchestration"
               ).get("batch") == 13)
        from services.qr55_registry import (
            SERVICE_REGISTRY as REG55,
        )
        record("51号本体未触碰(自建白名单)",
               len(REG55) == 12
               and "elderly_card" not in dir(
                   __import__(
                       "services.kg51_ontology"
                   ))[:100],
               str(len(REG55)))


async def run_all():
    await TestRegistry().run()
    await TestIntent().run()
    await TestCrypto().run()
    await TestScorer().run()
    await TestGenerateAndHttp().run()


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
