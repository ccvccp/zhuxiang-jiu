"""70号·AI智能二维码大模型 码语义中枢服务
(qr70_hub_service, P0)

规划(docs/70号_AI智能二维码大模型_创新规划方案.md
§七 P0):
    ① 六类码字典公示(码型×场景×权限×
       生命周期——封闭注册表)
    ② 生成统一管道(55号 qr55_crypto
       HMAC 签名+TTL+nonce——零改动调用)
    ③ 核销统一管道(55号 verify_code
       四态+消费策略状态机)
    ④ 愉悦度观测底座(快环纯统计
       ——P7 引擎消费)

铁律(规划 §1.2/§八):
    - 生成/核销走 55号 qr55_crypto 纯
      函数(55号零改动); 70号永不写
      55/69号表(叠加式)
    - LLM 禁入判定链(码型归属=注册表
      查表, 无意图推理)
    - public 码永不消费(消费者溯源
      人人可扫); once 码 nonce 核销
      即失效(防重放)
    - 愉悦度=观测指标(纯统计基线),
      策略变更走慢环 46号审批
      (规划 §5.2 快环边界)
"""

import logging

from core.helpers import ts

from repositories.qr70_repository import (
    Qr70Repository,
)
from services.qr70_registry import (
    CODE_KINDS, CODE_REGISTRY, LIFECYCLE_STATES,
    CONSUME_POLICIES, SCENES,
    get_code_meta, codes_of_kind,
    service_id_of, registry_view,
    MODEL_VERSION, current_mode,
)

logger = logging.getLogger("qr70_hub_service")


class Qr70HubService:
    """70号码语义中枢(P0)"""

    def __init__(self):
        self.repo = Qr70Repository()

    # ============================================================
    # ① 六类码字典公示(观测面)
    # ============================================================

    def code_dict(self) -> dict:
        """六类码注册表自描述(含生命周期
        /消费策略口径)"""
        view = registry_view()
        view["codes"] = [
            dict(v, codeId=k)
            for k, v in CODE_REGISTRY.items()
        ]
        return view

    def code_detail(self, code_id: str) -> dict:
        """单码型详情(注册表+serviceId)

        Raises:
            KeyError: 码型域外
        """
        meta = get_code_meta(code_id)
        if meta is None:
            raise KeyError(
                f"码型不存在(codeId={code_id})")
        detail = dict(meta)
        detail["codeId"] = code_id
        detail["serviceId"] = \
            service_id_of(code_id)
        return detail

    def kind_codes(self, kind: str) -> dict:
        """按码类列码型

        Raises:
            KeyError: 码类域外
        """
        if kind not in CODE_KINDS:
            raise KeyError(
                f"码类不存在(kind={kind})")
        return {
            "kind": kind,
            "codes": codes_of_kind(kind),
        }

    # ============================================================
    # ② 生成统一管道(决策面——off 409)
    # ============================================================

    async def generate(self, member_id: int,
                       code_id: str,
                       params: dict | None = None,
                       scene: str = "") -> dict:
        """六类码统一生成(55号 qr55_crypto
        签名——ZXBJ-QR55:qr70-{codeId})

        参数白名单铁律: 仅码型注册 params
        可携带(未知参数拒绝——55号口径)

        Raises:
            KeyError: 码型域外
            ValueError: 场景域外/参数白名单外
        """
        meta = get_code_meta(code_id)
        if meta is None:
            raise KeyError(
                f"码型不存在(codeId={code_id})")
        if meta.get("status") != "active":
            raise ValueError(
                f"码型已退役(codeId={code_id})")
        scene = str(scene or "")
        if scene:
            if scene not in SCENES:
                raise ValueError(
                    f"场景域外(scene={scene})")
            if scene not in meta.get(
                    "scenes", ()):
                raise ValueError(
                    f"场景与码型不符"
                    f"(codeId={code_id}, "
                    f"scene={scene})")
        # 参数白名单(未知参数拒绝)
        params = dict(params or {})
        allowed = set(meta.get("params") or ())
        unknown = set(params) - allowed
        if unknown:
            raise ValueError(
                f"参数白名单外: {sorted(unknown)}"
                f"(允许: {sorted(allowed)})")
        from services.qr55_crypto import (
            generate_code,
        )
        issued = generate_code(
            service_id_of(code_id), params,
            int(member_id or 0),
            ttl_seconds=int(
                meta["ttlSeconds"]))
        seq = await self.repo.next_code_seq()
        record = {
            "codeSeq": seq,
            "codeId": code_id,
            "kind": meta["kind"],
            "scene": scene,
            "memberId": int(member_id or 0),
            "params": params,
            "code": issued["code"],
            "nonce": issued["nonce"],
            "exp": int(issued["exp"]),
            "status": "generated",
            "generatedAt": ts(),
            "scannedAt": "",
            "redeemedAt": "",
        }
        await self.repo.save_code(
            issued["nonce"], record)
        await self.repo.save_event({
            "type": "code_generated",
            "codeId": code_id,
            "memberId": int(member_id or 0),
            "detail": {
                "kind": meta["kind"],
                "scene": scene,
                "params": params,
            },
            "at": ts(),
        })
        record = dict(record)
        record["modelVersion"] = MODEL_VERSION
        record["mode"] = current_mode()
        record["consumePolicy"] = meta[
            "consumePolicy"]
        return record

    # ============================================================
    # ③ 核销统一管道(决策面——off 409)
    # ============================================================

    async def redeem(self, code: str,
                     operator_id: int = 0) -> dict:
        """六类码统一核销(55号 verify_code
        四态+消费策略状态机)

        消费策略:
            once    nonce 核销即失效
                    (重复核销=重放拒绝)
            session TTL 内可重复验签
                    (首扫 generated→scanned)
            public  永不消费(消费者溯源)

        Raises:
            ValueError: 码格式非法
            KeyError: 码事件不存在(非70号域)
        """
        from services.qr55_crypto import (
            verify_code,
        )
        verdict = verify_code(str(code or ""))
        if verdict.get("status") != "ok":
            # 过期/篡改——同步实例状态留痕
            # (nonce 从码尾段提取——69号 P5
            # 同款: 四态中仅 ok 附 nonce)
            raw = str(code or "")
            nonce = (raw.rsplit(".", 1)[-1]
                     if "." in raw else "")
            rec = await self.repo\
                .find_code_by_nonce(nonce)
            if rec and rec.get("status") \
                    in ("generated", "scanned"):
                rec["status"] = "expired"
                rec["redeemedAt"] = ts()
                await self.repo.save_code(
                    nonce, rec)
            return {
                "redeemed": False,
                "verifyStatus": verdict.get(
                    "status"),
                "reason": verdict.get(
                    "reason", ""),
                "modelVersion": MODEL_VERSION,
            }
        nonce = verdict.get("nonce", "")
        payload = verdict.get("payload") or {}
        rec = await self.repo\
            .find_code_by_nonce(nonce)
        if rec is None:
            raise KeyError(
                f"码事件不存在(nonce="
                f"{str(nonce)[:8]}…——非70号域码)")
        meta = get_code_meta(rec["codeId"])
        policy = (meta or {}).get(
            "consumePolicy", "once")
        service_id = (payload.get("serviceId")
                      or "")
        # 域校验(serviceId 必须是本码型)
        if service_id != service_id_of(
                rec["codeId"]):
            return {
                "redeemed": False,
                "verifyStatus": "tampered",
                "reason": "serviceId 与码型"
                          "不符",
                "modelVersion": MODEL_VERSION,
            }
        # ---- 消费策略状态机 ----
        if policy == "public":
            # 公开码: 永不消费(人人可扫)
            result = {
                "redeemed": False,
                "scanned": True,
                "verifyStatus": "ok",
                "codeId": rec["codeId"],
                "kind": rec["kind"],
                "consumePolicy": policy,
                "status": rec["status"],
                "note": "公开码可重复扫"
                        "(永不消费)",
            }
        elif policy == "session":
            # 会话码: TTL 内可重复验签
            if rec["status"] == "generated":
                rec["status"] = "scanned"
                rec["scannedAt"] = ts()
                await self.repo.save_code(
                    nonce, rec)
            result = {
                "redeemed": False,
                "scanned": True,
                "verifyStatus": "ok",
                "codeId": rec["codeId"],
                "kind": rec["kind"],
                "consumePolicy": policy,
                "status": rec["status"],
                "note": "会话码 TTL 内"
                        "可重复验签",
            }
        else:
            # once: nonce 核销即失效
            if rec["status"] != "generated":
                # 重复核销=重放拒绝
                await self.repo.save_event({
                    "type": "code_replay_rejected",
                    "codeId": rec["codeId"],
                    "memberId": int(
                        operator_id or 0),
                    "detail": {
                        "status": rec["status"],
                    },
                    "at": ts(),
                })
                return {
                    "redeemed": False,
                    "verifyStatus": "replayed",
                    "reason": "码已核销"
                              "(nonce 一次性)",
                    "codeId": rec["codeId"],
                    "modelVersion": MODEL_VERSION,
                }
            rec["status"] = "redeemed"
            rec["redeemedAt"] = ts()
            await self.repo.save_code(
                nonce, rec)
            result = {
                "redeemed": True,
                "verifyStatus": "ok",
                "codeId": rec["codeId"],
                "kind": rec["kind"],
                "consumePolicy": policy,
                "status": rec["status"],
                "memberId": rec.get(
                    "memberId", 0),
            }
        await self.repo.save_event({
            "type": "code_redeem",
            "codeId": rec["codeId"],
            "memberId": int(operator_id or 0),
            "detail": {
                "redeemed": result.get(
                    "redeemed", False),
                "policy": policy,
                "status": result.get(
                    "status", ""),
            },
            "at": ts(),
        })
        result["modelVersion"] = MODEL_VERSION
        return result

    async def void_code(self, nonce: str) -> dict:
        """码作废(安全方向动作——人工/审计)"""
        rec = await self.repo\
            .find_code_by_nonce(nonce)
        if rec is None:
            raise KeyError(
                f"码实例不存在(nonce="
                f"{str(nonce)[:8]}…)")
        rec["status"] = "voided"
        await self.repo.save_code(nonce, rec)
        await self.repo.save_event({
            "type": "code_voided",
            "codeId": rec["codeId"],
            "detail": {"nonce": str(nonce)},
            "at": ts(),
        })
        return rec

    # ============================================================
    # ④ 观测面(留痕/事件/状态)
    # ============================================================

    async def codes_view(
            self, limit: int = 50,
            kind: str = "") -> dict:
        """码实例留痕视图(生命周期可审计)"""
        codes = await self.repo.list_codes(
            limit=limit, kind=kind)
        # 完整码值不回列表(防泄漏)
        for c in codes:
            c.pop("code", None)
        return {
            "modelVersion": MODEL_VERSION,
            "lifecycleStates":
                list(LIFECYCLE_STATES),
            "count": len(codes),
            "codes": codes,
        }

    async def code_detail_by_nonce(
            self, nonce: str) -> dict:
        """单码实例详情(生命周期/扫码史)

        Raises:
            KeyError: 实例不存在
        """
        rec = await self.repo\
            .find_code_by_nonce(nonce)
        if rec is None:
            raise KeyError(
                f"码实例不存在(nonce="
                f"{str(nonce)[:8]}…)")
        rec = dict(rec)
        rec.pop("code", None)
        return rec

    async def events_view(
            self, limit: int = 50) -> dict:
        """全链事件视图(生成/核销/重放
        拒绝/作废——四可审计)"""
        return {
            "modelVersion": MODEL_VERSION,
            "events": await self.repo.list_events(
                limit=limit),
        }

    async def model_status(self) -> dict:
        """模型状态(观测面——off 不受影响)"""
        codes = await self.repo.list_codes(
            limit=500)
        by_status: dict = {}
        for c in codes:
            s = c.get("status", "")
            by_status[s] = by_status.get(s, 0) + 1
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "codeKindCount": len(CODE_KINDS),
            "codeTypeCount": len(CODE_REGISTRY),
            "lifecycleStates":
                list(LIFECYCLE_STATES),
            "consumePolicies":
                list(CONSUME_POLICIES),
            "codeInstanceCount": len(codes),
            "statusDistribution": by_status,
        }

    # ============================================================
    # ⑤ 愉悦度观测底座(快环纯统计
    # ——不受 QR70_MODE 影响)
    # ============================================================

    async def report_joy(
            self, code_id: str,
            duration_ms: int = 0,
            completed: bool = True,
            mis_touch: bool = False,
            member_id: int = 0) -> dict:
        """愉悦度观测样本上报(扫码耗时/
        完成/误触——快环纯统计)

        愉悦度=观测指标铁律(规划 §5.2):
        本上报仅积累统计基线, 永不直接
        触发任何策略变更

        Raises:
            KeyError: 码型域外
            ValueError: 耗时负数
        """
        if get_code_meta(code_id) is None:
            raise KeyError(
                f"码型不存在(codeId={code_id})")
        duration = int(duration_ms or 0)
        if duration < 0:
            raise ValueError("耗时不可为负")
        record = {
            "codeId": code_id,
            "memberId": int(member_id or 0),
            "durationMs": duration,
            "completed": bool(completed),
            "misTouch": bool(mis_touch),
            "reportedAt": ts(),
        }
        await self.repo.save_joy(record)
        await self.repo.save_event({
            "type": "joy_sample",
            "codeId": code_id,
            "memberId": int(member_id or 0),
            "detail": {
                "durationMs": duration,
                "completed": bool(completed),
                "misTouch": bool(mis_touch),
            },
            "at": ts(),
        })
        record["modelVersion"] = MODEL_VERSION
        return record

    async def joy_stats(self) -> dict:
        """愉悦度统计基线(按码型聚合
        ——确定性均值/完成率/误触率)"""
        samples = await self.repo.list_joy()
        by_code: dict = {}
        for s in samples:
            cid = s.get("codeId", "")
            bucket = by_code.setdefault(cid, {
                "samples": [],
                "completeCount": 0,
                "misTouchCount": 0,
            })
            bucket["samples"].append(
                int(s.get("durationMs") or 0))
            if s.get("completed"):
                bucket["completeCount"] += 1
            if s.get("misTouch"):
                bucket["misTouchCount"] += 1
        stats = []
        for cid, b in sorted(
                by_code.items()):
            n = len(b["samples"])
            stats.append({
                "codeId": cid,
                "sampleCount": n,
                "avgDurationMs": round(
                    sum(b["samples"]) / n, 2)
                if n else 0.0,
                "completeRate": round(
                    b["completeCount"] / n, 4)
                if n else 0.0,
                "misTouchRate": round(
                    b["misTouchCount"] / n, 4)
                if n else 0.0,
            })
        return {
            "modelVersion": MODEL_VERSION,
            "note": "愉悦度=观测指标(纯统计"
                    "基线, 策略变更走慢环 46号"
                    "审批——规划 §5.2)",
            "codeCount": len(stats),
            "stats": stats,
        }
