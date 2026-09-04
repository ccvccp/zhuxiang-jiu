"""45号·P4 自进化闭环(申诉复核 + 裁决回流 + 归因报告 + 伦理补丁)

计划(docs/45号_信值模块实施计划.md §七):
    自我优化闭环(42/43/44号裁决回流范式平移):
        用户申诉(信值变动异议)
            → 人工复核(裁决: 计算正确 upheld / 计算错误
               overturned)
            → 真值回流: collect(已裁决未回流申诉 → 第28档案
               Hedge 反馈)/run(乘性更新+护栏)/status(视图)
            → 层内因子权重调优(层间 50/30/20 宪法护栏永不动)

    翻转语义(复核翻转即触发分数重算):
        overturned → 反向事件入库(delta 取反) + L1 负向
        事件的熔断计数回退 + 重算——"计算错误"意味着该变动
        从未发生, 而非叠加一次补偿。

    可解释性强制(§七 7.2, 归因报告):
        每次信值分变动附人类可读归因(禁止黑箱); LLM 三态:
        mock(代码模板, 数字来自计算层——LLM 幻觉不进入数据,
        42号发票摘要同口径)/real(润色归因文案)。

    外部注入通道(伦理补丁, §七 7.1):
        新法规/重大社会事件 → 人工编写补丁(β 映射更新) →
        版本留痕 → 注入运行时映射表。

存储:
    trust45_appeals: {appealId, trustId, eventId, layer,
    factor, delta, scoreAtAppeal, factorSnapshot, reason,
    status: pending|upheld|overturned, verdict, reviewerNote,
    appealFed, createdAt, decidedAt}
    trust45_patches: {patchId, kind, payload, note, version,
    appliedAt}
"""

import json
import logging
from datetime import datetime, UTC

from core.helpers import ts

from repositories.trust_value_repository import (
    TrustValue45Repository,
)
from services.trust_scoring_service import (
    TrustProfileService, TrustValueScorer,
)

logger = logging.getLogger(__name__)

SCORER_ID = "trust_value"

# 申诉窗口(天)——归因报告提示口径
APPEAL_WINDOW_DAYS = 7

# 申诉目标类型
APPEAL_STATUS_PENDING = "pending"
APPEAL_STATUS_UPHELD = "upheld"            # 复核维持: 计算正确
APPEAL_STATUS_OVERTURNED = "overturned"   # 复核翻转: 计算错误

LAYER_NAMES = {"L1": "法治合规", "L2": "社会伦理",
               "L3": "社会贡献"}


# ============================================================
# 存储基建(申诉/补丁双表, 双模式)
# ============================================================

async def _save_appeal(repo, record: dict,
                       new: bool = False) -> None:
    """申诉落库(new=True 时维护索引; 更新不重复入队)"""
    import repositories.backend as be
    if be.is_redis_mode():
        client = await be.get_redis_client()
        mapping = {}
        for k, v in record.items():
            if isinstance(v, (dict, list)):
                mapping[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, bool):
                mapping[k] = 1 if v else 0
            elif v is None:
                mapping[k] = ""
            else:
                mapping[k] = v
        pipe = client.pipeline(transaction=False)
        pipe.hset(be._k(
            "trust45", "appeals", record["appealId"]),
            mapping=mapping)
        if new:
            pipe.lpush(be._k("trust45", "appeals_all"),
                       record["appealId"])
        await pipe.execute()
        return
    repo._ensure_store()
    repo.store.setdefault("trust45_appeals", {})[
        record["appealId"]] = dict(record)


async def _get_appeal(repo, appeal_id: int) -> dict | None:
    import repositories.backend as be
    if be.is_redis_mode():
        client = await be.get_redis_client()
        data = await client.hgetall(be._k(
            "trust45", "appeals", appeal_id))
        if not data:
            return None
        return _parse_appeal(data)
    repo._ensure_store()
    rec = repo.store.get("trust45_appeals", {}).get(appeal_id)
    return _restore_appeal(dict(rec)) if rec else None


async def _list_appeals(repo, status: str = None) -> list[dict]:
    import repositories.backend as be
    if be.is_redis_mode():
        client = await be.get_redis_client()
        ids = await client.lrange(
            be._k("trust45", "appeals_all"), 0, -1)
        result = []
        for i in range(0, len(ids), 500):
            pipe = client.pipeline(transaction=False)
            for aid in ids[i:i + 500]:
                pipe.hgetall(be._k(
                    "trust45", "appeals", int(aid)))
            for data in await pipe.execute():
                if data:
                    result.append(_parse_appeal(data))
    else:
        repo._ensure_store()
        result = [_restore_appeal(dict(r)) for r in
                  repo.store.get("trust45_appeals", {}).values()]
    if status:
        result = [a for a in result if a.get("status") == status]
    result.sort(key=lambda a: -(int(a.get("appealId") or 0)))
    return result


def _parse_appeal(data: dict) -> dict:
    out = {}
    for k, v in (data or {}).items():
        if k in ("appealId", "trustId", "eventId"):
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                out[k] = v
        elif k in ("delta", "scoreAtAppeal"):
            try:
                out[k] = float(v or 0)
            except (TypeError, ValueError):
                out[k] = v
        elif k == "factorSnapshot":
            try:
                out[k] = json.loads(v) if v else {}
            except (TypeError, ValueError):
                out[k] = {}
        elif k == "appealFed":
            out[k] = v in (1, "1", True, "True", "true")
        else:
            out[k] = v
    return out


def _restore_appeal(rec: dict) -> dict:
    rec.setdefault("appealFed", False)
    rec.setdefault("factorSnapshot",
                   rec.get("factorSnapshot") or {})
    return rec


async def _next_appeal_id(repo) -> int:
    import repositories.backend as be
    if be.is_redis_mode():
        client = await be.get_redis_client()
        return await client.incr(
            be._k("trust45", "appeals", "seq"))
    repo._ensure_store()
    seq = repo.store.get("_trust45_appeals_seq", 0) + 1
    repo.store["_trust45_appeals_seq"] = seq
    return seq


async def _save_patch(repo, record: dict) -> None:
    import repositories.backend as be
    if be.is_redis_mode():
        client = await be.get_redis_client()
        mapping = {k: (json.dumps(v, ensure_ascii=False)
                       if isinstance(v, (dict, list))
                       else ("" if v is None else v))
                   for k, v in record.items()}
        await client.hset(be._k(
            "trust45", "patches", record["patchId"]),
            mapping=mapping)
        await client.lpush(be._k(
            "trust45", "patches_all"), record["patchId"])
        return
    repo._ensure_store()
    repo.store.setdefault("trust45_patches", {})[
        record["patchId"]] = dict(record)


async def _list_patches(repo) -> list[dict]:
    import repositories.backend as be
    if be.is_redis_mode():
        client = await be.get_redis_client()
        ids = await client.lrange(
            be._k("trust45", "patches_all"), 0, -1)
        result = []
        for i in range(0, len(ids), 500):
            pipe = client.pipeline(transaction=False)
            for pid in ids[i:i + 500]:
                pipe.hgetall(be._k(
                    "trust45", "patches", int(pid)))
            for data in await pipe.execute():
                if data:
                    out = {}
                    for k, v in data.items():
                        if k in ("patchId", "version"):
                            try:
                                out[k] = int(v)
                            except (TypeError, ValueError):
                                out[k] = v
                        elif k == "payload":
                            try:
                                out[k] = json.loads(v) if v \
                                    else {}
                            except (TypeError, ValueError):
                                out[k] = {}
                        else:
                            out[k] = v
                    result.append(out)
        result.sort(key=lambda p: -(p.get("patchId") or 0))
        return result
    repo._ensure_store()
    result = []
    for r in repo.store.get("trust45_patches",
                            {}).values():
        out = dict(r)
        out["payload"] = out.get("payload") or {}
        result.append(out)
    result.sort(key=lambda p: -(p.get("patchId") or 0))
    return result


async def _next_patch_id(repo) -> int:
    import repositories.backend as be
    if be.is_redis_mode():
        client = await be.get_redis_client()
        return await client.incr(
            be._k("trust45", "patches", "seq"))
    repo._ensure_store()
    seq = repo.store.get("_trust45_patches_seq", 0) + 1
    repo.store["_trust45_patches_seq"] = seq
    return seq


def _days_since(ts_str: str) -> float:
    if not ts_str:
        return 999.0
    try:
        then = datetime.fromisoformat(str(ts_str))
        return max(0.0, (datetime.now(UTC) - then)
                   .total_seconds() / 86400)
    except (TypeError, ValueError):
        return 999.0


# ============================================================
# 申诉与归因服务
# ============================================================


class TrustAppealService:
    """申诉复核(P4; 提交→复核→翻转重算→回流真值)"""

    def __init__(self,
                 repo: TrustValue45Repository =
                 TrustValue45Repository()):
        self.repo = repo

    async def submit_appeal(self, trust_id: int, event_id: int,
                            reason: str) -> dict:
        """提交申诉(信值变动异议; 7 日窗口)

        Raises:
            KeyError: 档案/事件不存在
            ValueError: 窗口已过/理由为空/重复申诉
        """
        profile = await self.repo.get_profile(trust_id)
        if profile is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")
        reason = (reason or "").strip()
        if not reason or len(reason) > 500:
            raise ValueError("申诉理由必填(1-500 字符)")

        event = await self._find_event(event_id)
        if event is None or event.get("trustId") != trust_id:
            raise KeyError(
                f"变动事件 {event_id} 不存在(或不属于该档案)")

        days = _days_since(event.get("ts"))
        if days > APPEAL_WINDOW_DAYS:
            raise ValueError(
                f"申诉窗口已过(事件发生 {days:.0f} 天, "
                f"窗口 {APPEAL_WINDOW_DAYS} 天)")

        # 同事件重复申诉拒绝
        existing = await _list_appeals(self.repo)
        if any(a.get("eventId") == event_id
               and a.get("status") ==
               APPEAL_STATUS_PENDING for a in existing):
            raise ValueError("该变动已有待裁决申诉(勿重复提交)")

        appeal_id = await _next_appeal_id(self.repo)
        record = {
            "appealId": appeal_id, "trustId": trust_id,
            "eventId": event_id,
            "layer": event.get("layer"),
            "factor": event.get("factor"),
            "delta": float(event.get("delta") or 0),
            "scoreAtAppeal": profile.get("score") or 0,
            "factorSnapshot": dict(
                profile.get("factors") or {}),
            "reason": reason,
            "status": APPEAL_STATUS_PENDING,
            "verdict": "", "reviewerNote": "",
            "appealFed": False,
            "createdAt": ts(), "decidedAt": "",
        }
        await _save_appeal(self.repo, record, new=True)
        logger.info("trust45_appeal_submitted appealId=%s "
                    "trustId=%s event=%s", appeal_id,
                    trust_id, event_id)
        return {"success": True, "appealId": appeal_id,
                "status": APPEAL_STATUS_PENDING,
                "note": f"申诉已受理(窗口 {APPEAL_WINDOW_DAYS} "
                        f"天内有效), 等待人工复核"}

    async def list_appeals(self, status: str = None) -> dict:
        """申诉队列(管理端)"""
        appeals = await _list_appeals(self.repo, status)
        return {"success": True, "total": len(appeals),
                "appeals": appeals}

    async def decide_appeal(self, appeal_id: int,
                            uphold: bool,
                            note: str = "") -> dict:
        """人工复核裁决(计算正确性真值源)

        upheld(维持)=计算正确(正反馈);
        overturned(翻转)=计算错误——反向事件+熔断计数回退+
        重算(该变动"从未发生", 非补偿叠加)。

        Raises:
            KeyError: 申诉不存在
            ValueError: 已裁决
        """
        appeal = await _get_appeal(self.repo, appeal_id)
        if appeal is None:
            raise KeyError(f"申诉 {appeal_id} 不存在")
        if appeal.get("status") != APPEAL_STATUS_PENDING:
            raise ValueError(
                f"申诉已裁决({appeal.get('status')}), "
                f"不可重复裁决")

        appeal["status"] = (APPEAL_STATUS_UPHELD if uphold
                            else APPEAL_STATUS_OVERTURNED)
        appeal["verdict"] = ("计算正确" if uphold
                             else "计算错误")
        appeal["reviewerNote"] = (note or "")[:500]
        appeal["decidedAt"] = ts()

        after = None
        if not uphold:
            # 翻转: 反向事件(delta 取反)+ L1 熔断计数回退
            after = await self._reverse_event(appeal)
        await _save_appeal(self.repo, appeal)
        logger.info("trust45_appeal_decided appealId=%s "
                    "verdict=%s", appeal_id,
                    appeal["verdict"])
        return {"success": True, "appealId": appeal_id,
                "status": appeal["status"],
                "verdict": appeal["verdict"],
                "scoreAfter": after,
                "note": ("复核维持: 计算正确" if uphold else
                         "复核翻转: 反向事件已入库, 分数已重算")}

    async def _reverse_event(self, appeal: dict) -> float:
        """翻转执行: 反向事件 + 熔断计数回退 + 重算"""
        trust_id = int(appeal["trustId"])
        delta = float(appeal.get("delta") or 0)
        profile = await self.repo.get_profile(trust_id)
        if profile is None:
            return 0.0

        # L1 负向事件的熔断计数回退(该违规"从未发生")
        if appeal.get("layer") == "L1" and delta < 0:
            sev = dict(profile.get("l1Severity") or {})
            # 找原事件 severity(从事件记录取)
            event = await self._find_event(
                int(appeal.get("eventId") or 0))
            sev_key = (event or {}).get("severity") or "general"
            cnt = int(sev.get(sev_key) or 0)
            if cnt <= 1:
                sev.pop(sev_key, None)
            else:
                sev[sev_key] = cnt - 1
            profile["l1Severity"] = sev
            await self.repo.save_profile(profile)

        svc = TrustProfileService(repo=self.repo)
        result = await svc.record_event(
            trust_id, appeal.get("layer") or "L1",
            appeal.get("factor") or "", -delta,
            source="appeal_reversal",
            summary=f"[申诉翻转] 事件{appeal.get('eventId')}"
                   f" 计算错误, 反向回滚 "
                   f"{'+' if -delta > 0 else ''}{-delta}")
        return result.get("score")

    async def _find_event(self, event_id: int) -> dict | None:
        import repositories.backend as be
        if be.is_redis_mode():
            client = await be.get_redis_client()
            data = await client.hgetall(be._k(
                "trust45", "trust45_events", event_id))
            return self.repo._deserialize(data) if data else None
        self.repo._ensure_store()
        ev = self.repo.store.get("trust45_events", {}).get(
            event_id)
        return dict(ev) if ev else None

    # --------------------------------------------------------
    # 归因报告(可解释性强制, §七 7.2)
    # --------------------------------------------------------

    async def attribution(self, trust_id: int,
                          event_id: int) -> dict:
        """信值变动归因报告(LLM 三态; 禁止黑箱)

        Raises:
            KeyError: 档案/事件不存在
        """
        profile = await self.repo.get_profile(trust_id)
        if profile is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")
        event = await self._find_event(event_id)
        if event is None or event.get("trustId") != trust_id:
            raise KeyError(
                f"变动事件 {event_id} 不存在(或不属于该档案)")

        layer = event.get("layer") or "L1"
        weight = TrustValueScorer.CONSTITUTION.get(layer, 0)
        delta = float(event.get("delta") or 0)
        before = float(event.get("scoreBefore") or 0)
        after = float(event.get("scoreAfter") or 0)
        role_name = ("个人" if profile.get("role") == "person"
                     else "企业/机构")

        # mock 确定性模板(数字永远来自计算层)
        report = (
            f"【信值变动归因】\n"
            f"角色: {role_name} #{trust_id} | "
            f"层级: {layer} {LAYER_NAMES.get(layer, '')}"
            f"(权重 {weight:.0%})\n"
            f"行为: {event.get('summary') or '-'}\n"
            f"依据: 因子 {event.get('factor')} "
            f"{'+' if delta > 0 else ''}{delta:.1f} 分\n"
            f"变动: 信值分 {before:.1f} → {after:.1f}"
            f"({after - before:+.1f})\n"
            f"数据来源: {event.get('source')}\n"
            f"说明: 每次变动均留痕可审计; "
            f"禁止黑箱是本系统的宪法级约束。\n"
            f"申诉: 如有异议, {APPEAL_WINDOW_DAYS} 日内提交 "
            f"POST /api/trust/appeals"
        )
        mode = "mock"

        # real 轨: LLM 润色(失败回退 mock——42号同口径)
        try:
            from services.llm_client import (
                llm_enabled, provider_client,
            )
            if llm_enabled():
                reply = provider_client().chat(
                    system="你是信值系统归因报告编辑。只润色"
                           "文字表述, 不得改动任何数字与事实, "
                           "输出不超过 9 行。",
                    user=report)
                if reply and reply.strip():
                    report = reply.strip()
                    mode = "real"
        except Exception as exc:
            logger.warning("trust45_attribution_llm_skip: %s",
                           exc)

        return {"success": True, "trustId": trust_id,
                "eventId": event_id, "mode": mode,
                "report": report}


# ============================================================
# 学习回流三连(44号 P5 范式平移)
# ============================================================


class TrustLearningService:
    """裁决真值回流(第28档案 Hedge; 层内宪法护栏永不动)"""

    SCORER_ID = SCORER_ID

    def __init__(self,
                 repo: TrustValue45Repository =
                 TrustValue45Repository()):
        self.repo = repo

    async def collect_appeal_feedback(self) -> dict:
        """批量回流: 已裁决且未回流的申诉 → 第28档案反馈

        真值口径: upheld(计算正确)=正反馈 +0.5 /
        overturned(计算错误)=负反馈 -0.5。
        单条失败不阻断; appealFed 幂等标记。
        """
        from services.ai_learning_service import submit_feedback
        appeals = await _list_appeals(self.repo)
        submitted, skipped, results = 0, 0, []
        for appeal in appeals:
            if appeal.get("appealFed"):
                skipped += 1
                continue
            status = appeal.get("status")
            if status not in (APPEAL_STATUS_UPHELD,
                              APPEAL_STATUS_OVERTURNED):
                skipped += 1   # pending 未裁决
                continue
            correct = status == APPEAL_STATUS_UPHELD
            snapshot = appeal.get("factorSnapshot") or {}
            factors = [
                {"name": name, "score": float(val or 0),
                 "contribution": float(val or 0)}
                for name, val in snapshot.items()
                if name in TrustValueScorer.WEIGHTS]
            if not factors:
                skipped += 1
                continue
            try:
                result = await submit_feedback({
                    "scorerId": self.SCORER_ID,
                    "factors": factors,
                    "scoreAtDecision": float(
                        appeal.get("scoreAtAppeal") or 0),
                    "actualAction": "score_change",
                    "expectedAction": ("score_change" if correct
                                      else "no_change"),
                    "correct": correct,
                    "reward": 0.5 if correct else -0.5,
                    "note": f"appealId={appeal.get('appealId')} "
                            f"event={appeal.get('eventId')} "
                            f"status={status}",
                    "source": "trust45",
                })
                submitted += 1
                results.append(result)
                appeal["appealFed"] = True
                await _save_appeal(self.repo, appeal)
            except (KeyError, ValueError) as exc:
                skipped += 1
                logger.warning("trust45_feedback_skip appeal=%s:"
                               " %s",
                               appeal.get("appealId"), exc)
        return {"success": True, "submitted": submitted,
                "skipped": skipped, "results": results}

    async def run_learning(self) -> dict:
        """触发第28档案一轮 Hedge 学习(层内护栏约束)"""
        from services.ai_learning_service import run_learning_cycle
        return await run_learning_cycle(self.SCORER_ID)

    async def learning_status(self) -> dict:
        """学习状态视图(档案/申诉统计/权重/宪法护栏)"""
        from services.ai_learning_service import (
            SCORER_REGISTRY, get_weights_view,
        )
        appeals = await _list_appeals(self.repo)
        decided = [a for a in appeals if a.get("status") in (
            APPEAL_STATUS_UPHELD, APPEAL_STATUS_OVERTURNED)]
        fed = [a for a in decided if a.get("appealFed")]
        return {
            "success": True,
            "scorer": self.SCORER_ID,
            "registry": SCORER_REGISTRY.get(self.SCORER_ID),
            "appeals": {
                "total": len(appeals),
                "pending": len([a for a in appeals
                                if a.get("status") ==
                                APPEAL_STATUS_PENDING]),
                "decided": len(decided),
                "fed": len(fed),
                "upheld": len([a for a in decided
                               if a.get("status") ==
                               APPEAL_STATUS_UPHELD]),
                "overturned": len([a for a in decided
                                   if a.get("status") ==
                                   APPEAL_STATUS_OVERTURNED]),
            },
            "weights": await get_weights_view(self.SCORER_ID),
            "constitution": dict(
                TrustValueScorer.CONSTITUTION),
            "constitutionNote": "层间 50/30/20 为宪法常量, "
                                "Hedge 学习只调层内因子相对权重"
                                "(层内归一化保证数学恒定)",
        }


# ============================================================
# 伦理补丁(外部注入通道, §七 7.1)
# ============================================================


class TrustPatchService:
    """伦理补丁(新法规/社会共识 → β 映射更新, 版本留痕)"""

    def __init__(self,
                 repo: TrustValue45Repository =
                 TrustValue45Repository()):
        self.repo = repo

    async def apply_patch(self, kind: str, payload: dict,
                          note: str = "") -> dict:
        """应用补丁(当前支持 beta_update: β 映射更新)

        Args:
            kind: beta_update
            payload: {factor, repairKind, beta, label?,
                      category: targeted|generic}
        Raises:
            ValueError: 参数非法
        """
        kind = (kind or "").strip().lower()
        if kind != "beta_update":
            raise ValueError(
                f"非法补丁类型: {kind}(当前支持: beta_update)")
        factor = str(payload.get("factor") or "")
        repair_kind = str(payload.get("repairKind") or "")
        beta = payload.get("beta")
        category = str(payload.get("category") or
                       "targeted").strip().lower()
        if factor not in TrustValueScorer.LAYER_OF:
            raise ValueError(f"非法因子: {factor}")
        if category not in ("targeted", "generic"):
            raise ValueError(
                f"非法 category: {category}"
                f"(targeted|generic)")
        try:
            beta = float(beta)
        except (TypeError, ValueError):
            raise ValueError("beta 需为数值") from None
        if not 0.1 <= beta <= 2.0:
            raise ValueError(
                "beta 需在 [0.1, 2.0](修复关联度安全域)")
        if not repair_kind or len(repair_kind) > 64:
            raise ValueError("repairKind 必填(≤64 字符)")

        from services.trust_repair_service import BETA_MAP
        table = BETA_MAP.get(factor) or BETA_MAP["_default"]
        label = str(payload.get("label") or repair_kind)
        # 幂等 upsert: 同 kind 旧条目移除
        for cat in ("targeted", "generic"):
            table[cat] = [t for t in table[cat]
                          if t[0] != repair_kind]
        table[category].append((repair_kind, beta, label))

        patch_id = await _next_patch_id(self.repo)
        await _save_patch(self.repo, {
            "patchId": patch_id, "kind": kind,
            "payload": {"factor": factor,
                        "repairKind": repair_kind,
                        "beta": beta, "label": label,
                        "category": category},
            "note": (note or "")[:500],
            "version": patch_id,
            "appliedAt": ts(),
        })
        logger.info("trust45_patch_applied patchId=%s %s "
                    "%s→%s β=%s", patch_id, factor,
                    repair_kind, category, beta)
        return {"success": True, "patchId": patch_id,
                "kind": kind, "payload": payload,
                "version": patch_id,
                "note": "伦理补丁已注入(BETA_MAP 运行时生效, "
                        "版本留痕可审计)"}

    async def list_patches(self) -> dict:
        """补丁历史(版本审计)"""
        patches = await _list_patches(self.repo)
        return {"success": True, "total": len(patches),
                "patches": patches}
