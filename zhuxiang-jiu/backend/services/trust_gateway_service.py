"""45号·P5 对外服务接口平台(五类开放 API)

计划(docs/45号_信值模块实施计划.md §八 8.1):
    五类 API(全部走 44号 Key 网关鉴权——路径登记入 44号
    台账并标 published 后, ApiKeyMiddleware 自动接管
    双头校验/QPS/日配额; 45号侧只做业务语义):

        ① 信值查询 API   信值分/分层明细/修复建议(脱敏)
        ② 兑换核销 API   商户端验证+销毁 TV(幂等键+防重放)
        ③ 行为存证 API   角色自愿提交修复/正向证据
        ④ 信用分转换 API 与本站信用分单向同步(防重放
                          nonce+时间窗)
        ⑤ 监管审计 API   只读审计视图(操作日志全留存)

安全控制(45号侧业务层; 传输层频控由 44号 Key 网关承担):
    - 防重放: nonce+时间窗(±300s), 已用 nonce 拒绝
    - 幂等键: redeemByIdempotencyKey 重复请求返回原结果
    - 审计日志: 每次 open 面调用全留痕(谁/何时/查了谁)

设计铁律:
    - 红线 1: 转账类 API 永不开放(TV 不可兑现金/不可
      二级交易)——开放面只有查询/核销/存证/转换/审计
    - fail-open 铁律不适用审计 API(只读, 异常即报错)
"""

import hashlib
import logging
import time

from core.helpers import ts

from repositories.trust_value_repository import (
    TrustValue45Repository,
)

logger = logging.getLogger(__name__)

# 防重放时间窗(秒, ±5 分钟)
NONCE_WINDOW_SECONDS = 300

# 幂等键 TTL(秒, 24h 内重复请求返回原结果)
IDEMPOTENCY_TTL = 86400

# 审计日志留存量(每档案上限, 防 Redis 膨胀)
AUDIT_LOG_MAX = 200


class TrustGatewayService:
    """开放面网关服务(P5; 业务语义层)"""

    def __init__(self,
                 repo: TrustValue45Repository =
                 TrustValue45Repository()):
        self.repo = repo

    # --------------------------------------------------------
    # 防重放(nonce + 时间窗)
    # --------------------------------------------------------

    async def check_nonce(self, nonce: str,
                          timestamp: int) -> dict:
        """防重放校验(19号收款同款范式)

        Args:
            nonce: 请求唯一随机串(≥16 字符)
            timestamp: 请求发起秒级时间戳
        Raises:
            ValueError: 参数非法/窗口外/重复 nonce
        """
        nonce = (nonce or "").strip()
        if len(nonce) < 16:
            raise ValueError("nonce 需 ≥16 字符(防重放)")
        try:
            timestamp = int(timestamp)
        except (TypeError, ValueError):
            raise ValueError("timestamp 需为秒级整数") from None
        now = int(time.time())
        if abs(now - timestamp) > NONCE_WINDOW_SECONDS:
            raise ValueError(
                f"时间窗校验失败(偏离 {abs(now - timestamp)}s, "
                f"窗口 ±{NONCE_WINDOW_SECONDS}s)——疑似重放")

        import repositories.backend as be
        key = be._k("trust45", "nonce", nonce)
        if be.is_redis_mode():
            client = await be.get_redis_client()
            # SET NX EX 原子占位: 已存在即重放
            ok = await client.set(
                key, timestamp, nx=True,
                ex=NONCE_WINDOW_SECONDS * 2)
            if not ok:
                raise ValueError("nonce 已使用(重放攻击拦截)")
            return {"ok": True}
        store = self.repo.store
        store.setdefault("_trust45_nonces", {})
        now_ms = now * 1000
        # 惰性清理过期
        for k in list(store["_trust45_nonces"]):
            if now_ms - store["_trust45_nonces"][k] > \
                    NONCE_WINDOW_SECONDS * 2000:
                del store["_trust45_nonces"][k]
        if nonce in store["_trust45_nonces"]:
            raise ValueError("nonce 已使用(重放攻击拦截)")
        store["_trust45_nonces"][nonce] = now_ms
        return {"ok": True}

    # --------------------------------------------------------
    # 审计日志(操作全留痕)
    # --------------------------------------------------------

    async def _audit(self, trust_id: int, action: str,
                     caller: str, detail: str = "") -> None:
        """开放面调用留痕(谁/何时/查了谁)"""
        import repositories.backend as be
        import json as _json
        entry = {"action": action, "caller": caller,
                 "trustId": trust_id,
                 "detail": (detail or "")[:200], "ts": ts()}
        if be.is_redis_mode():
            client = await be.get_redis_client()
            key = be._k("trust45", "audit_log", trust_id)
            pipe = client.pipeline(transaction=False)
            pipe.lpush(key, _json.dumps(entry,
                                        ensure_ascii=False))
            pipe.ltrim(key, 0, AUDIT_LOG_MAX - 1)
            await pipe.execute()
            return
        store = self.repo.store
        store.setdefault("_trust45_audit_logs", {}).setdefault(
            trust_id, []).insert(0, entry)

    async def audit_log(self, trust_id: int,
                        limit: int = 50) -> dict:
        """监管审计视图: 档案的开放面访问日志(只读)"""
        import repositories.backend as be
        import json as _json
        if be.is_redis_mode():
            client = await be.get_redis_client()
            rows = await client.lrange(
                be._k("trust45", "audit_log", trust_id),
                0, max(0, limit - 1))
            entries = [_json.loads(r) if isinstance(r, str)
                        else r for r in rows]
        else:
            store = self.repo.store
            entries = list(store.get(
                "_trust45_audit_logs", {}).get(trust_id, [])
            )[:limit]
        return {"success": True, "trustId": trust_id,
                "total": len(entries), "entries": entries}

    # --------------------------------------------------------
    # ① 信值查询 API(脱敏: 无证件摘要/无因子快照明细)
    # --------------------------------------------------------

    async def open_query(self, trust_id: int,
                         caller: str) -> dict:
        """开放面信值查询(字段级脱敏 + 修复建议摘要)

        Raises:
            KeyError: 档案不存在
        """
        from services.trust_scoring_service import (
            TrustProfileService, FUSE_ALPHA,
        )
        svc = TrustProfileService(repo=self.repo)
        profile = await svc.get_profile(trust_id)  # KeyError
        await self._audit(trust_id, "open_query", caller,
                          f"score={profile.get('score')}")

        # 脱敏: 摘要掩码去除, 只留业务字段; 修复建议摘要
        from services.trust_repair_service import (
            TrustRepairService,
        )
        repair_plan = await TrustRepairService(
            repo=self.repo).repair_plan(trust_id)
        return {
            "success": True, "trustId": trust_id,
            "role": profile.get("role"),
            "name": profile.get("name"),
            "score": profile.get("score"),
            "grade": profile.get("grade"),
            "fused": profile.get("fused"),
            "fusedLevel": profile.get("fusedLevel"),
            "layers": {layer: {
                "score": v.get("score"),
                "weight": v.get("weight"),
                "contribution": v.get("contribution"),
            } for layer, v in
                (profile.get("layers") or {}).items()},
            "constitution": profile.get("constitution"),
            "repairAdvice": {
                "repairableViolations":
                    repair_plan.get("violationsRepairable"),
                "alpha": profile.get("fuseAlpha"),
                "note": (repair_plan.get("note") or "")[:100],
            },
            "note": "字段级脱敏: 证件摘要与因子快照不开放;"
                    "修复建议为摘要口径(明细需档案本人查询)",
        }

    # --------------------------------------------------------
    # ② 兑换核销 API(幂等键 + 防重放)
    # --------------------------------------------------------

    async def open_redeem_confirm(
            self, redeem_id: int, merchant: str,
            idempotency_key: str, nonce: str,
            timestamp: int) -> dict:
        """商户端核销(数字签名缺外部凭证, 幂等键+nonce 先行)

        幂等语义: 同 idempotencyKey 的重复请求返回原结果
        (首次成功后缓存); 并发防双花由 nonce 原子占位保证。

        Raises:
            ValueError: 参数/nonce/重放
            KeyError: 申请不存在
        """
        await self.check_nonce(nonce, timestamp)
        idempotency_key = (idempotency_key or "").strip()
        if len(idempotency_key) < 8:
            raise ValueError("idempotencyKey 需 ≥8 字符")

        import repositories.backend as be
        import json as _json
        cache_key = be._k("trust45", "idem",
                          "redeem", idempotency_key)
        # 幂等命中: 直接返回原结果
        if be.is_redis_mode():
            client = await be.get_redis_client()
            cached = await client.get(cache_key)
            if cached:
                result = _json.loads(cached)
                result["idempotentReplay"] = True
                return result
        else:
            store = self.repo.store
            cached = store.get("_trust45_idem", {}).get(
                idempotency_key)
            if cached:
                result = dict(cached)
                result["idempotentReplay"] = True
                return result

        # 首次执行(P3 核销语义)
        from services.trust_asset_service import (
            TrustAssetService,
        )
        svc = TrustAssetService(repo=self.repo)
        result = await svc.redeem_confirm(
            redeem_id, merchant)   # KeyError/ValueError 透传
        result["idempotencyKey"] = idempotency_key
        result["idempotentReplay"] = False

        # 结果缓存(幂等 TTL)
        if be.is_redis_mode():
            client = await be.get_redis_client()
            await client.set(
                cache_key, _json.dumps(
                    result, ensure_ascii=False),
                ex=IDEMPOTENCY_TTL)
        else:
            store = self.repo.store
            store.setdefault("_trust45_idem", {})[
                idempotency_key] = dict(result)
        await self._audit(
            int(result.get("trustId") or redeem_id),
            "open_redeem_confirm", merchant,
            f"redeemId={redeem_id} burned="
            f"{result.get('burned')}")
        return result

    # --------------------------------------------------------
    # ③ 行为存证 API(内容安全过滤 + 异步验真口径)
    # --------------------------------------------------------

    SENSITIVE_WORDS = ("身份证号:", "银行卡:", "病历:", "指纹:")

    async def open_deposit(self, trust_id: int,
                           layer: str, factor: str,
                           observed: float,
                           peer_baseline: float,
                           evidence: str, summary: str = "",
                           sources: list = None) -> dict:
        """开放面存证提交(敏感词过滤 → P1 存证通道)

        Raises:
            ValueError: 敏感内容/参数非法
            KeyError: 档案不存在
        """
        evidence = evidence or ""
        for word in self.SENSITIVE_WORDS:
            if word in evidence or word in (summary or ""):
                # 审计完整性: 被拒请求同样留痕(监管可回溯)
                await self._audit(
                    trust_id, "open_deposit_rejected",
                    f"trust:{trust_id}",
                    f"敏感词拦截(「{word}」)")
                raise ValueError(
                    f"内容安全过滤: 疑似敏感信息"
                    f"(「{word}」)——数据最小必要红线")
        from services.trust_radar_service import (
            TrustRadarService,
        )
        result = await TrustRadarService(
            repo=self.repo).submit_deposit(
            trust_id, layer, factor, observed,
            peer_baseline, evidence, summary, sources)
        await self._audit(trust_id, "open_deposit",
                          f"trust:{trust_id}",
                          f"depositId={result.get('depositId')}"
                          f" verified={result.get('verified')}")
        return result

    # --------------------------------------------------------
    # ④ 信用分转换 API(防重放 + 单向)
    # --------------------------------------------------------

    async def open_convert(self, trust_id: int, user_id: int,
                           credit_points: float,
                           nonce: str,
                           timestamp: int) -> dict:
        """开放面信用分→TV 单向转换(nonce 防重放)

        Raises:
            ValueError: nonce/参数
            KeyError: 档案不存在
        """
        await self.check_nonce(nonce, timestamp)
        from services.trust_asset_service import (
            TrustAssetService,
        )
        result = await TrustAssetService(
            repo=self.repo).convert(
            trust_id, user_id, credit_points)
        await self._audit(trust_id, "open_convert",
                          f"user:{user_id}",
                          f"points={credit_points} tv="
                          f"{result.get('amount')}")
        return result

    # --------------------------------------------------------
    # ⑤ 监管审计 API(只读, 全留痕)
    # --------------------------------------------------------

    async def open_audit(self, trust_id: int,
                         caller: str) -> dict:
        """监管审计视图(档案+事件流水+账本+访问日志,
        只读脱敏)

        Raises:
            KeyError: 档案不存在
        """
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_asset_service import (
            TrustAssetService,
        )
        profile = await self.repo.get_profile(trust_id)
        if profile is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")

        svc = TrustProfileService(repo=self.repo)
        view = await svc.get_profile(trust_id)
        events = await self.repo.list_events_by_trust(
            trust_id)
        ledger = await TrustAssetService(
            repo=self.repo).ledger(trust_id, limit=50)
        await self._audit(trust_id, "open_audit", caller,
                          "监管只读视图")
        return {
            "success": True, "trustId": trust_id,
            "profile": {
                "role": view.get("role"),
                "name": view.get("name"),
                "score": view.get("score"),
                "rawScore": view.get("rawScore"),
                "grade": view.get("grade"),
                "fused": view.get("fused"),
                "fusedLevel": view.get("fusedLevel"),
                "l1Severity": view.get("l1Severity"),
                "layers": view.get("layers"),
                "constitution": view.get("constitution"),
                "createdAt": view.get("createdAt"),
                "updatedAt": view.get("updatedAt"),
            },
            "eventCount": len(events),
            "events": [
                {k: e.get(k) for k in
                 ("eventId", "layer", "factor", "delta",
                  "severity", "source", "scoreBefore",
                  "scoreAfter", "ts")}
                for e in events[-20:]],
            "ledger": ledger.get("entries"),
            "accessLog": (await self.audit_log(
                trust_id, limit=20)).get("entries"),
            "note": "监管审计只读视图(证件摘要已脱敏;"
                    "本访问已留痕)",
        }

    # --------------------------------------------------------
    # 治理看板聚合(§八 8.2 六区块数据源)
    # --------------------------------------------------------

    async def dashboard(self) -> dict:
        """治理看板聚合视图(六区块)"""
        profiles = await self.repo.list_profiles()
        persons = [p for p in profiles
                   if p.get("role") == "person"]
        orgs = [p for p in profiles
                if p.get("role") == "org"]
        fused = [p for p in profiles if p.get("fused")]
        grades = {}
        for p in profiles:
            grades[p.get("grade") or "watch"] = \
                grades.get(p.get("grade") or "watch", 0) + 1

        # 事件统计(雷达态势)
        all_events = []
        for p in profiles:
            all_events += await self.repo.list_events_by_trust(
                int(p.get("trustId") or 0))
        by_source = {}
        for e in all_events:
            by_source[e.get("source") or "manual"] = \
                by_source.get(e.get("source") or "manual",
                              0) + 1

        # 资产聚合
        issued_total = 0.0
        burned_total = 0.0
        reserve_total = 0.0
        import repositories.backend as be
        for p in profiles:
            tid = int(p.get("trustId") or 0)
            from services.trust_asset_service import (
                TrustAssetService,
            )
            try:
                b = await TrustAssetService(
                    repo=self.repo).balance(tid)
                issued_total += b.get("issuedTotal") or 0
                burned_total += b.get("burnedTotal") or 0
                reserve_total += b.get("reservePool") or 0
            except KeyError:
                continue

        # 申诉/补丁统计
        from services.trust_learning_service import (
            TrustAppealService, TrustPatchService,
        )
        appeals = await TrustAppealService(
            repo=self.repo).list_appeals()
        patches = await TrustPatchService(
            repo=self.repo).list_patches()

        return {
            "success": True,
            "overview": {
                "total": len(profiles),
                "persons": len(persons),
                "orgs": len(orgs),
                "fused": len(fused),
                "fusedRate": round(
                    len(fused) / len(profiles), 4)
                if profiles else 0.0,
                "byGrade": grades,
            },
            "radar": {
                "eventsTotal": len(all_events),
                "bySource": by_source,
            },
            "assets": {
                "issuedTotal": round(issued_total, 2),
                "burnedTotal": round(burned_total, 2),
                "reservePool": round(reserve_total, 2),
                "reserveCoverage": round(
                    reserve_total / issued_total, 4)
                if issued_total else 1.0,
            },
            "evolution": {
                "appeals": appeals.get("total"),
                "appealsPending": len(
                    [a for a in appeals.get("appeals") or []
                     if a.get("status") == "pending"]),
                "patches": patches.get("total"),
                "patchesLatest":
                    ((patches.get("patches") or [{}])[0]
                     .get("appliedAt")),
            },
        }
