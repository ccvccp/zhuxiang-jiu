"""43号·AI智能安全管理模块 P0 专项测试

运行方式:
    python test_security_p0.py

覆盖(设计文档 §6.1 P0 范围):
    - 特征扫描: SQLi/XSS/路径遍历/命令注入/扫描器UA/探针路径
    - ThreatGateScorer(第26档案): 六因子直测/四档处置/权重和=1
    - IP 信誉: 冷启动80/扣分三档/状态三态/钉住/冷却恢复
    - 封禁表: TTL 自动解封/手动解封/Redis与内存双模式语义
    - 决策管线: observe只留痕不处置/enforce真处置/可疑事件入流水/
      正常请求不入流水(防爆炸)/blacklisted直封
    - fail-open: 内部异常放行(网关不能锁死网站)
    - 学习注册: SCORER_REGISTRY 第26档案/阈值表/默认权重
    - 中间件挂载: main.py 注册次序(CORS→网关→JWT)
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
# P0 默认灰度口径(observe), enforce 子场景内动态切换
os.environ["SECURITY_ENFORCE_LEVEL"] = "observe"
os.environ["SECURITY_GATEWAY_MODE"] = "on"
# 冷却恢复测试用短参数
os.environ["SECURITY_REPUTATION_COOLDOWN"] = "0"
os.environ["SECURITY_RECOVER_EVERY"] = "3"

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


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


class Run:
    def __init__(self):
        self.svc = None

    async def run(self):
        from services.security_service import (
            Security43Service, scan_payload, scan_path, scan_identity,
            ACTION_ALLOW, ACTION_THROTTLE, ACTION_CHALLENGE, ACTION_BLOCK,
        )
        from repositories.security_repository import (
            REPUTATION_NORMAL, REPUTATION_SUSPICIOUS,
            REPUTATION_BLACKLISTED,
        )
        self.svc = Security43Service()

        # ============================================================
        # 01 特征扫描
        # ============================================================
        print("[01 特征扫描]")
        record("SQLi特征检出", scan_payload(
            "/api/product?kw=' OR 1=1 --") < 100,
            str(scan_payload("/api/product?kw=' OR 1=1 --")))
        record("SQLi联合查询检出", scan_payload(
            "name=x UNION SELECT * FROM members") < 100)
        record("XSS特征检出", scan_payload(
            "<script>alert(1)</script>") < 100)
        record("路径遍历检出", scan_payload(
            "../../etc/passwd") < 100)
        record("命令注入检出", scan_payload("; rm -rf /tmp") < 100)
        record("扫描器UA检出", scan_payload(
            "sqlmap/1.7") < 100)
        record("干净请求满分", scan_payload(
            "/api/product/list?page=1") == 100.0)
        record("多特征累积扣分", scan_payload(
            "<script> UNION SELECT /etc/passwd") < 60,
            str(scan_payload("<script> UNION SELECT /etc/passwd")))
        record("探针路径零分", scan_path("/.env") == 0.0)
        record("wp-admin探针", scan_path("/wp-admin/setup.php") == 0.0)
        record("正常路径满分", scan_path("/api/product/list") == 100.0)
        record("深路径降分", scan_path(
            "/a/b/c/d/e/f/g/h/i/j") == 40.0)
        record("未认证打管理端降分", scan_identity(
            "/api/admin/stats", 0) < 100)
        record("未认证普通端满分", scan_identity(
            "/api/product/list", 0) == 100.0)
        record("已认证满分", scan_identity(
            "/api/admin/stats", 1) == 100.0)

        # ============================================================
        # 02 ThreatGateScorer 六因子直测
        # ============================================================
        print("[02 威胁评分器]")
        from services.ai_scoring_service import (
            SCORERS, ThreatGateScorer,
        )
        scorer = SCORERS["security_threat_gate"]
        record("评分器已注册", isinstance(
            scorer, ThreatGateScorer))
        record("六因子权重和=1", abs(sum(
            ThreatGateScorer.WEIGHTS.values()) - 1.0) < 1e-9,
            str(ThreatGateScorer.WEIGHTS))

        clean = await scorer.score({
            "ip": "1.1.1.1", "memberId": 1, "reputation": 90,
            "requestCount": 10, "rateLimit": 120,
            "payloadSignature": 100, "pathAnomaly": 100,
            "identityRisk": 100, "hour": 14,
        })
        record("干净请求 allow", clean["action"] == ACTION_ALLOW,
               f"{clean['score']}/{clean['action']}")
        record("干净请求高分", clean["score"] >= 70, str(clean["score"]))
        record("因子数=6", len(clean["factors"]) == 6)

        attack = await scorer.score({
            "ip": "2.2.2.2", "memberId": 0, "reputation": 20,
            "requestCount": 120, "rateLimit": 120,
            "payloadSignature": 20, "pathAnomaly": 0,
            "identityRisk": 20, "hour": 3,
        })
        record("全攻击因子 block", attack["action"] == ACTION_BLOCK,
               f"{attack['score']}/{attack['action']}")

        mid = await scorer.score({
            "ip": "3.3.3.3", "memberId": 1, "reputation": 50,
            "requestCount": 110, "rateLimit": 120,
            "payloadSignature": 50, "pathAnomaly": 100,
            "identityRisk": 100, "hour": 14,
        })
        record("单特征命中至少挑战(硬规则)",
               mid["action"] == ACTION_CHALLENGE,
               f"{mid['score']}/{mid['action']}")

        # 硬规则: 多类特征叠加直通 block(即使其余因子满分)
        hard = await scorer.score({
            "ip": "4.4.4.4", "memberId": 1, "reputation": 100,
            "requestCount": 1, "rateLimit": 120,
            "payloadSignature": 0, "pathAnomaly": 100,
            "identityRisk": 100, "hour": 14,
        })
        record("多类特征直通block(硬规则)",
               hard["action"] == ACTION_BLOCK,
               f"{hard['score']}/{hard['action']}")

        # 四档口径边界直测
        s_allow = await scorer.score({
            "ip": "x", "reputation": 100, "requestCount": 1,
            "payloadSignature": 100, "pathAnomaly": 100,
            "identityRisk": 100, "hour": 10})
        record("满分请求 allow", s_allow["action"] == ACTION_ALLOW)
        s_night = await scorer.score({
            "ip": "x", "reputation": 100, "requestCount": 1,
            "payloadSignature": 100, "pathAnomaly": 100,
            "identityRisk": 100, "hour": 2})
        record("凌晨降分仍 allow(单因子小权重)",
               s_night["score"] < s_allow["score"])

        # ============================================================
        # 03 IP 信誉库
        # ============================================================
        print("[03 IP信誉库]")
        rep = await self.svc.ensure_reputation("9.9.9.9")
        record("冷启动中性分80", rep["score"] == 80.0, str(rep["score"]))
        record("冷启动normal态", rep["status"] == REPUTATION_NORMAL)

        rep = await self.svc.apply_penalty("9.9.9.9", ACTION_CHALLENGE)
        record("challenge扣30分", rep["score"] == 50.0, str(rep["score"]))
        record("扣后suspicious态",
               rep["status"] == REPUTATION_SUSPICIOUS)

        rep = await self.svc.apply_penalty("9.9.9.9", ACTION_BLOCK)
        record("block扣60分", rep["score"] == 0.0, str(rep["score"]))
        record("扣后blacklisted态",
               rep["status"] == REPUTATION_BLACKLISTED)
        record("攻击计数累积", rep["attackCount"] == 2)

        # 钉住: 不参与冷却恢复
        await self.svc.pin_reputation("9.9.9.9", True)
        for _ in range(10):
            await self.svc.recover_reputation("9.9.9.9")
        rep = await self.svc.ensure_reputation("9.9.9.9")
        record("钉住不恢复", rep["score"] == 0.0 and rep["pinned"])
        await self.svc.pin_reputation("9.9.9.9", False)

        # 冷却恢复(测试参数: cooldown=0, every=3)
        for _ in range(3):
            r = await self.svc.ensure_reputation("9.9.9.9")
            r["requestCount"] = (r.get("requestCount") or 0) + 1
            from repositories.security_repository import \
                Security43Repository
            await Security43Repository().save_reputation(r)
            await self.svc.recover_reputation("9.9.9.9")
        rep = await self.svc.ensure_reputation("9.9.9.9")
        record("冷却恢复+1", rep["score"] == 1.0, str(rep["score"]))

        # ============================================================
        # 04 封禁表 TTL
        # ============================================================
        print("[04 封禁表]")
        os.environ["SECURITY_BAN_TTL"] = "1"
        await self.svc.block_ip("8.8.8.8", reason="测试封禁")
        record("封禁生效", await self.svc.is_blocked("8.8.8.8") is True)
        record("未封IP不受影响",
               await self.svc.is_blocked("7.7.7.7") is False)
        record("手动解封", await self.svc.unblock_ip("8.8.8.8") is True)
        record("重复解封幂等False",
               await self.svc.unblock_ip("8.8.8.8") is False)
        await self.svc.block_ip("8.8.8.4", reason="TTL测试")
        await asyncio.sleep(1.2)
        record("TTL自动解封", await self.svc.is_blocked("8.8.8.4") is False)
        os.environ["SECURITY_BAN_TTL"] = "900"

        # ============================================================
        # 05 决策管线 observe(默认灰度)
        # ============================================================
        print("[05 决策管线-observe]")
        # 正常请求: 放行且不入流水
        events_before = len(await self.svc.list_events(limit=1000))
        r = await self.svc.process_request(
            "6.6.6.1", method="GET", path="/api/product/list",
            query="page=1", ua="Mozilla/5.0", member_id=1, hour=14)
        record("正常请求放行", r["action"] == ACTION_ALLOW)
        record("正常请求不入流水", len(
            await self.svc.list_events(limit=1000)) == events_before)

        # SQLi 攻击请求: observe 只留痕不处置
        r = await self.svc.process_request(
            "6.6.6.2", method="GET",
            path="/api/product/search",
            query="kw=' OR 1=1 --",
            ua="Mozilla/5.0", hour=14)
        record("攻击请求observe仍放行",
               r["action"] == ACTION_ALLOW, str(r["action"]))
        record("攻击事件已留痕", r["event"] is not None)
        record("事件含威胁分", (r["event"] or {}).get("score")
               is not None)
        record("事件enforced=False", (r["event"] or {}).get("enforced")
               is False)
        record("事件verdict=pending", (r["event"] or {}).get("verdict")
               == "pending")

        # 探针路径: 事件入流水
        r = await self.svc.process_request(
            "6.6.6.3", method="GET", path="/.env",
            ua="Mozilla/5.0", hour=14)
        record("探针请求留痕", r["event"] is not None,
               str(r.get("event"))[:80])

        # 频次超限: 观察模式仍放行但留痕
        os.environ["SECURITY_RATE_LIMIT"] = "5"
        r = None
        for i in range(6):
            r = await self.svc.process_request(
                "6.6.6.4", method="GET", path="/api/product/list",
                ua="Mozilla/5.0", hour=14)
        record("高频请求observe放行",
               r["action"] == ACTION_ALLOW)
        os.environ["SECURITY_RATE_LIMIT"] = "120"

        # ============================================================
        # 06 决策管线 enforce
        # ============================================================
        print("[06 决策管线-enforce]")
        os.environ["SECURITY_ENFORCE_LEVEL"] = "enforce"
        try:
            # block 档: 全攻击因子
            r = await self.svc.process_request(
                "6.6.6.5", method="GET",
                path="/wp-admin/setup.php",
                query="id=1 UNION SELECT * FROM users",
                ua="sqlmap/1.7", hour=3)
            record("enforce真处置", r["enforced"] is True)
            record("攻击请求block", r["action"] == ACTION_BLOCK,
                   str(r["action"]))
            record("block后自动封禁",
                   await self.svc.is_blocked("6.6.6.5") is True)
            rep = await self.svc.ensure_reputation("6.6.6.5")
            record("block扣信誉60", rep["score"] <= 20.0,
                   str(rep["score"]))

            # 已封禁 IP 直封(不再评分)
            r = await self.svc.process_request(
                "6.6.6.5", method="GET", path="/api/product/list",
                hour=14)
            record("已封IP直封block", r["action"] == ACTION_BLOCK
                   and r["blocked"] is True)

            # blacklisted 信誉直封(评分前的硬规则)
            await self.svc.unblock_ip("6.6.6.5")
            r = await self.svc.process_request(
                "6.6.6.5", method="GET", path="/api/product/list",
                hour=14)
            record("blacklisted直封", r["action"] == ACTION_BLOCK,
                   str(r["action"]))

            # 正常请求: enforce 下仍放行
            r = await self.svc.process_request(
                "6.6.6.6", method="GET", path="/api/product/list",
                query="page=1", ua="Mozilla/5.0", member_id=1, hour=14)
            record("enforce正常放行", r["action"] == ACTION_ALLOW)
        finally:
            os.environ["SECURITY_ENFORCE_LEVEL"] = "observe"

        # ============================================================
        # 07 fail-open
        # ============================================================
        print("[07 fail-open]")
        original_do = self.svc._do_process

        async def _boom(*a, **kw):
            raise RuntimeError("存储故障")

        self.svc._do_process = _boom
        try:
            r = await self.svc.process_request(
                "5.5.5.5", method="GET", path="/api/product/list")
        finally:
            self.svc._do_process = original_do
        record("内部异常放行(fail-open)",
               r["action"] == ACTION_ALLOW, str(r))

        # ============================================================
        # 08 学习注册(第26档案)
        # ============================================================
        print("[08 学习注册]")
        from services.ai_learning_service import (
            SCORER_REGISTRY, DECISION_THRESHOLDS, default_weights,
        )
        record("第26档案已注册",
               "security_threat_gate" in SCORER_REGISTRY)
        record("档案batch=10", SCORER_REGISTRY.get(
            "security_threat_gate", {}).get("batch") == 10)
        record("四档阈值表", DECISION_THRESHOLDS.get(
            "security_threat_gate") == [
                (70.0, "allow"), (50.0, "throttle"),
                (25.0, "challenge"), (0.0, "block")])
        record("默认权重可取", default_weights(
            "security_threat_gate") == ThreatGateScorer.WEIGHTS)

        # ============================================================
        # 09 中间件挂载与回退
        # ============================================================
        print("[09 中间件挂载]")
        from services.security_service import get_gateway_mode
        record("默认网关on", get_gateway_mode() == "on")
        os.environ["SECURITY_GATEWAY_MODE"] = "off"
        record("一键回退off", get_gateway_mode() == "off")
        os.environ["SECURITY_GATEWAY_MODE"] = "on"

        from core.security_gateway import (
            SecurityGatewayMiddleware, _BodyPeeker,
        )
        record("BodyPeeker可导入", _BodyPeeker is not None)

        # main.py 挂载次序: 网关在 JWT 与 CORS 之间
        import main as main_mod
        record("网关已挂载main", any(
            m.cls is SecurityGatewayMiddleware
            for m in main_mod.app.user_middleware), "user_middleware检查")
        order = [m.cls.__name__ for m in main_mod.app.user_middleware]
        record("挂载次序CORS外网关中JWT内", (
            "SecurityGatewayMiddleware" in order
            and "JWTAuthMiddleware" in order
            and order.index("SecurityGatewayMiddleware")
            < order.index("JWTAuthMiddleware")), str(order))

        # ============================================================
        # 10 统计
        # ============================================================
        print("[10 统计]")
        stats = await self.svc.stats()
        record("统计四要素", all(k in stats for k in (
            "gatewayMode", "enforceLevel", "events", "blocks")))
        record("统计事件分布", "byAction" in stats["events"])
        record("观察态observe", stats["enforceLevel"] == "observe")


runner = Run()


def main():
    reset_store()
    asyncio.run(runner.run())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
