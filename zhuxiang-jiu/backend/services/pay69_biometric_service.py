"""69号·AI智能支付大模型 生物特征服务
(pay69_biometric_service, P4)

规划(docs/69号_AI智能支付大模型_创新规划方案.md
§4.3/§七 P4):
    ① FIDO strong 档落地(挑战-应答
       一次性——48号 confirmToken 语义)
    ② 活体意图验证(不仅验"你是谁", 还
       验"你是否自愿"——胁迫语义线索
       确定性特征表→静默降级密码验证
       +人工介入留痕)
    ③ 端侧模板终身学习(版本账本+置信度
       哈希——原始特征永不上传铁律)

铁律(规划 §4.3):
    - 生物模板端侧存储(合规红线)——
      服务端仅 version+confidence 哈希
    - 降级/人工介入为安全动作可自动
      (保护用户方向)但全留痕
    - 胁迫检测永不做资金操作
"""

import hashlib
import logging
import secrets

from core.helpers import ts

from repositories.pay69_repository import (
    Pay69Repository,
)
from services.pay69_registry import (
    BIOMETRIC_CHALLENGE_TTL,
    BIOMETRIC_METHODS,
    BIOMETRIC_RESULTS,
    COERCION_SIGNS,
    COERCION_THRESHOLD,
    MODEL_VERSION,
    TEMPLATE_VERSION_MAX,
)

logger = logging.getLogger("pay69_bio")


class Pay69BiometricService:
    """69号生物特征(P4)"""

    def __init__(self):
        self.repo = Pay69Repository()

    # ============================================================
    # ① FIDO 挑战-应答(一次性)
    # ============================================================

    async def issue_challenge(
            self, member_id: int,
            method: str = "face") -> dict:
        """发起 FIDO 挑战(strong 档——
        48号 confirmToken 语义: 一次性
        +TTL+GETDEL 防重放)

        Raises:
            ValueError: 方式域外
        """
        if method not in BIOMETRIC_METHODS:
            raise ValueError(
                f"生物方式域外: {method}"
                f"(须 {BIOMETRIC_METHODS})")
        challenge = secrets.token_hex(16)
        record = {
            "challenge": challenge,
            "memberId": int(member_id or 0),
            "method": method,
            "issuedAt": ts(),
        }
        await self.repo.save_challenge(
            challenge, record,
            ttl=BIOMETRIC_CHALLENGE_TTL)
        return {
            "challenge": challenge,
            "method": method,
            "ttlSeconds": BIOMETRIC_CHALLENGE_TTL,
            "issuedAt": record["issuedAt"],
        }

    # ============================================================
    # ② 活体意图验证(胁迫→降级+人工)
    # ============================================================

    def coerce_score(
            self, signs: list) -> float:
        """胁迫线索确定性计分(命中权重
        叠加, 封顶 1.0)"""
        score = 0.0
        for s in signs or []:
            score += COERCION_SIGNS.get(
                str(s), 0.0)
        return round(min(score, 1.0), 4)

    async def verify(
            self, member_id: int,
            challenge: str,
            match: bool,
            signs: list = None,
            confidence: float = 1.0) -> dict:
        """生物验证(挑战消费+特征匹配+
        胁迫判定→结果域封闭)

        语义(活体意图验证):
            verified: 特征匹配+无胁迫
            degraded: 特征匹配但胁迫线索
                      ≥阈值→静默降级密码
                      验证+人工介入留痕
                      (安全方向可自动+全留痕)
            failed: 特征不匹配
            challenge_expired: 挑战过期/
                      不存在(防重放)

        Raises:
            ValueError: 结果域外(不可达)
        """
        ch = await self.repo.pop_challenge(
            challenge)
        method = (ch["method"]
                  if ch else "face")
        coerce = self.coerce_score(signs)
        if not ch:
            result = "challenge_expired"
        elif not match:
            result = "failed"
        elif coerce >= COERCION_THRESHOLD:
            result = "degraded"
        else:
            result = "verified"
        record = {
            "memberId": int(member_id or 0),
            "method": method,
            "result": result,
            "match": bool(match and ch),
            "coerceScore": coerce,
            "coerceSigns": [str(s)
                            for s in (signs or [])],
            "confidence": round(
                max(0.0, min(1.0,
                             float(confidence
                                   or 0))), 4),
            "challengeValid": bool(ch),
            "degradedTo": ("password+manual"
                            if result
                            == "degraded"
                            else ""),
            "verifiedAt": ts(),
        }
        if result not in BIOMETRIC_RESULTS:
            raise ValueError(
                f"结果域外(不可达): {result}")
        await self.repo.save_bio_event(
            dict(record))
        await self.repo.save_event({
            "type": "biometric_verify",
            "memberId": int(member_id or 0),
            "detail": {
                "method": method,
                "result": result,
                "coerceScore": coerce,
            },
            "at": ts(),
        })
        return record

    # ============================================================
    # ③ 端侧模板版本账本(原始特征
    #    永不上传)
    # ============================================================

    async def register_template(
            self, member_id: int,
            method: str = "face",
            template_version: int = None,
            confidence_hash: str = "",
            confidence: float = 1.0) -> dict:
        """端侧模板版本登记/增量(服务端
        只记 version+置信度哈希——原始
        特征永不上传合规红线)

        增量语义: 端侧完成微调后上报新
        版本号; 服务端版本+1 顺序校验
        (防回滚攻击——版本不可回退)

        Raises:
            ValueError: 方式域外/版本
            回退/超上限
        """
        if method not in BIOMETRIC_METHODS:
            raise ValueError(
                f"生物方式域外: {method}")
        existing = await self.repo.get_template(
            member_id, method)
        if existing is None:
            new_version = int(
                template_version or 1)
            if new_version != 1:
                raise ValueError(
                    f"首登记版本须为 1: "
                    f"{new_version}")
        else:
            current = int(
                existing.get("version", 1))
            expected = current + 1
            if template_version is None:
                new_version = expected
            else:
                new_version = int(
                    template_version)
                if new_version != expected:
                    raise ValueError(
                        f"版本须顺序+1: 期望 "
                        f"{expected}, 实际 "
                        f"{new_version}")
        if not (1 <= new_version
                <= TEMPLATE_VERSION_MAX):
            raise ValueError(
                f"版本超上限: {new_version}")
        record = {
            "memberId": int(member_id or 0),
            "method": method,
            "version": new_version,
            "confidenceHash": str(
                confidence_hash or ""),
            "confidence": round(
                max(0.0, min(1.0,
                             float(confidence
                                   or 0))), 4),
            "registeredAt": ts(),
        }
        await self.repo.save_template(
            member_id, method, record)
        # 置信度下降→建议补充验证(不
        # 自动降档——熵引擎 P2 消费)
        if record["confidence"] < 0.75:
            await self.repo.save_event({
                "type": "template_confidence_low",
                "memberId": int(member_id or 0),
                "detail": {
                    "method": method,
                    "confidence": record[
                        "confidence"],
                    "advice":
                        "建议补充验证"
                        "(熵引擎行为轴参考)",
                },
                "at": ts(),
            })
        return record

    async def template_view(
            self, member_id: int,
            method: str = "face") -> dict:
        """模板版本账本视图(观测面
        ——服务端仅版本+哈希, 无特征)"""
        existing = await self.repo.get_template(
            member_id, method)
        return {
            "memberId": int(member_id),
            "method": method,
            "registered": existing is not None,
            "template": existing,
            "rawDataStored": False,
        }

    # ============================================================
    # 观测面
    # ============================================================

    def biometric_dict(self) -> dict:
        """生物特征字典公示(方式/结果/
        胁迫线索表+阈值——观测面)"""
        return {
            "modelVersion": MODEL_VERSION,
            "methods": list(
                BIOMETRIC_METHODS),
            "results": list(
                BIOMETRIC_RESULTS),
            "coercionSigns": dict(
                COERCION_SIGNS),
            "coercionThreshold":
                COERCION_THRESHOLD,
            "challengeTtl":
                BIOMETRIC_CHALLENGE_TTL,
            "templateVersionMax":
                TEMPLATE_VERSION_MAX,
            "ironRules": (
                "端侧模板存储(原始特征"
                "永不上传——合规红线)",
                "胁迫→降级+人工(安全方向"
                "可自动+全留痕)",
                "胁迫检测永不做资金操作"),
        }

    async def bio_events_view(
            self, member_id: int = None,
            limit: int = 50) -> dict:
        """生物验证事件视图(观测面)"""
        events = await self.repo.list_bio_events(
            member_id, limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "count": len(events),
            "events": events,
        }


def hash_confidence(member_id: int,
                    salt: str = "") -> str:
    """置信度哈希工具(端侧上报用——
    原始特征单向哈希)"""
    payload = f"{member_id}:{salt}:{ts()}"
    return hashlib.sha256(
        payload.encode("utf-8")).hexdigest()
