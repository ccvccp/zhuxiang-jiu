"""70号·AI智能二维码大模型 认证码统一服务
(qr70_auth_service, P2)

规划(docs/70号_AI智能二维码大模型_创新规划方案.md
§4.2/§七 P2):
    ① 统一码型封装(三认证链的 70号
       承接面——39号扫码登录/48号
       confirmToken/69号 FIDO)
    ② 设备指纹漂移校准观测(fast 弱漂移
       /drifted 强漂移——确定性规则)
    ③ 失败模式基线(快环纯统计——P7 慢环
       二次验证阈值建议书消费底座)

铁律(规划 §4.2):
    - 39/48/69号零改动: 本服务只做
      统一封装与观测, 三链各自的
      challenge/confirm/扫码会话
      仍由其自身服务负责
    - 认证升级为保护方向可自动
      (全留痕); 阈值变更走慢环 46号
      审批(永不在快环调整)
    - LLM 禁入判定链(漂移/失败模式
      归类=确定性规则)
    - 失败模式统计仅积累基线, 永不
      直接触发策略变更(观测指标铁律)
"""

import logging

from core.helpers import ts

from repositories.qr70_repository import (
    Qr70Repository,
)
from services.qr70_registry import (
    service_id_of,
    MODEL_VERSION, current_mode,
)

logger = logging.getLogger("qr70_auth_service")

# ============================================================
# 认证通道域(封闭——三基座+统一面)
# ============================================================

AUTH_CHANNELS = (
    "entry_qr",     # 39号 扫码登录
    "confirm",      # 48号 confirmToken
    "biometric",    # 69号 FIDO
)

AUTH_CHANNEL_LABELS: dict = {
    "entry_qr": "扫码登录(39号)",
    "confirm": "确认令牌(48号)",
    "biometric": "生物特征(69号)",
}

# 设备指纹漂移域(封闭——确定性规则)
DRIFT_LEVELS = (
    "none",     # 一致(同指纹)
    "fast",     # 弱漂移(已知设备+异指纹
                # → 快速通道降级为标准)
    "drifted",  # 强漂移(未知设备+异指纹
                # → 升级验证档位)
)

# 失败模式域(封闭——P7 慢环消费)
FAILURE_MODES = (
    "code_wrong",        # 数字码错误(48号 3 次作废)
    "code_expired",      # 令牌过期
    "challenge_expired",  # 挑战过期(69号)
    "coercion",          # 胁迫降级(69号 degraded)
    "mismatch",          # 指纹/票据不匹配
    "replayed",          # 重放
)

AUTH_SESSION_CODE_ID = "auth-session"

# 漂移判定阈值(确定性; 变更走慢环审批)
DRIFT_THRESHOLDS = {
    "fast": 60,     # 风控分 <60 且指纹不一致 → fast
    "drifted": 70,  # 风控分 ≥70 或完全未知 → drifted
}


class Qr70AuthService:
    """70号认证码统一封装(P2)"""

    def __init__(self):
        self.repo = Qr70Repository()

    # ============================================================
    # ① 统一码型封装(发起认证会话——决策面)
    # ============================================================

    async def auth_begin(
            self, member_id: int,
            channel: str,
            device_fingerprint: str = "",
            risk_hint: int = 0) -> dict:
        """统一认证会话发起(三链的 70号
        承接面)

        生成签名化 auth-session 码(session
        策略——认证过程内可重复验签);
        实际 challenge/扫码会话由三基座
        自身服务发起, 本层绑定其返回的
        原始凭据 ID 做观测聚合锚点

        Raises:
            ValueError: 通道域外
        """
        channel = str(channel or "")
        if channel not in AUTH_CHANNELS:
            raise ValueError(
                f"认证通道域外(channel={channel})")
        from services.qr70_hub_service \
            import Qr70HubService
        record = await Qr70HubService().generate(
            int(member_id or 0),
            AUTH_SESSION_CODE_ID,
            {"channel": channel,
             "fingerprint":
                 str(device_fingerprint
                     or "")[:60]},
            "consumer")
        record["authChannel"] = channel
        record["authChannelLabel"] = \
            AUTH_CHANNEL_LABELS[channel]
        # 漂移基线锚定(会话发起时)
        record["driftLevel"] = "none"
        record["riskHint"] = int(risk_hint or 0)
        return record

    # ============================================================
    # ② 设备指纹漂移校准观测(快环)
    # ============================================================

    async def drift_check(
            self, member_id: int,
            stored_fingerprint: str,
            presented_fingerprint: str,
            risk_score: int = 0,
            trusted_device: bool = False
            ) -> dict:
        """设备指纹漂移校准观测

        确定性规则(观测面——不阻断业务,
        不改三基座行为):
            - 指纹一致 → none
            - 指纹不一致 + 风控分≥70 或
              未知设备 → drifted(强漂移)
            - 指纹不一致 + 其余 → fast(弱漂移)
            - 可信设备(39号 trustedUntil 未过期)
              且风控分<60 → 至少 fast(弱漂移
              仍观测——保护方向留痕)

        Raises:
            ValueError: 风控分域外(0-100)
        """
        risk = int(risk_score or 0)
        if risk < 0 or risk > 100:
            raise ValueError(
                f"风控分域外(0-100, got={risk})")
        stored = str(stored_fingerprint or "")
        presented = str(
            presented_fingerprint or "")
        if presented and presented == stored:
            level = "none"
        elif risk >= DRIFT_THRESHOLDS[
                "drifted"]:
            level = "drifted"
        elif stored and (
                not trusted_device
                or risk < DRIFT_THRESHOLDS[
                    "fast"]):
            level = "fast"
        else:
            level = "drifted"
        record = {
            "memberId": int(member_id or 0),
            "storedFingerprint":
                self._fp_mask(stored),
            "presentedFingerprint":
                self._fp_mask(presented),
            "riskScore": risk,
            "trustedDevice": bool(
                trusted_device),
            "driftLevel": level,
            "checkedAt": ts(),
        }
        await self.repo.save_event({
            "type": "auth_drift",
            "memberId": int(member_id or 0),
            "detail": {
                "driftLevel": level,
                "riskScore": risk,
                "trustedDevice": bool(
                    trusted_device),
            },
            "at": ts(),
        })
        record["modelVersion"] = MODEL_VERSION
        record["note"] = "漂移校准为观测面" \
                         "(不阻断, 不改三基座" \
                         "行为——升级建议由" \
                         "三基座自身执行)"
        return record

    @staticmethod
    def _fp_mask(fp: str) -> str:
        """指纹脱敏(留痕不含完整指纹——
        49号隐私口径)"""
        s = str(fp or "")
        if not s:
            return ""
        return (s[:4] + "…" + s[-2:]) \
            if len(s) > 8 else s[:2] + "…"

    # ============================================================
    # ③ 失败模式基线(快环——P7 慢环消费)
    # ============================================================

    async def failure_report(
            self, member_id: int,
            channel: str,
            failure_mode: str) -> dict:
        """认证失败模式上报(快环纯统计
        ——不受开关影响)

        失败模式统计仅积累基线; 二次验证
        触发阈值的调整走 P7 慢环建议书
        →46号审批(观测指标铁律)

        Raises:
            ValueError: 通道/失败模式域外
        """
        channel = str(channel or "")
        if channel not in AUTH_CHANNELS:
            raise ValueError(
                f"认证通道域外(channel={channel})")
        mode = str(failure_mode or "")
        if mode not in FAILURE_MODES:
            raise ValueError(
                f"失败模式域外(mode={mode})")
        record = {
            "memberId": int(member_id or 0),
            "channel": channel,
            "failureMode": mode,
            "reportedAt": ts(),
        }
        await self.repo.save_event({
            "type": "auth_failure",
            "memberId": int(member_id or 0),
            "detail": {
                "channel": channel,
                "mode": mode,
            },
            "at": ts(),
        })
        record["modelVersion"] = MODEL_VERSION
        return record

    async def failure_stats(self) -> dict:
        """失败模式统计基线(按通道×模式
        ——确定性聚合; 观测面)"""
        events = await self.repo.list_events(
            limit=500)
        by_channel: dict = {
            ch: {m: 0 for m in FAILURE_MODES}
            for ch in AUTH_CHANNELS}
        total = 0
        for e in events:
            if e.get("type") != "auth_failure":
                continue
            detail = e.get("detail") or {}
            ch = detail.get("channel", "")
            mode = detail.get("mode", "")
            if ch in by_channel \
                    and mode in by_channel[ch]:
                by_channel[ch][mode] += 1
                total += 1
        return {
            "modelVersion": MODEL_VERSION,
            "channels": list(AUTH_CHANNELS),
            "failureModes": list(
                FAILURE_MODES),
            "totalFailures": total,
            "byChannel": by_channel,
            "note": "失败模式=观测指标"
                    "(快环基线; 二次验证阈值"
                    "调整走 P7 慢环建议书"
                    "→46号审批)",
        }

    # ============================================================
    # 观测面(字典+状态)
    # ============================================================

    def auth_dict(self) -> dict:
        """认证码统一字典(通道×漂移×
        失败模式域公示)"""
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "codeId": AUTH_SESSION_CODE_ID,
            "serviceId": service_id_of(
                AUTH_SESSION_CODE_ID),
            "channels": [
                {"channel": ch,
                 "label":
                     AUTH_CHANNEL_LABELS[ch]}
                for ch in AUTH_CHANNELS
            ],
            "driftLevels":
                list(DRIFT_LEVELS),
            "driftThresholds": dict(
                DRIFT_THRESHOLDS),
            "failureModes": list(
                FAILURE_MODES),
            "redlines": [
                "39/48/69号零改动: 统一封装"
                "只做观测与承接",
                "漂移校准为观测面(不阻断"
                "业务, 升级由三基座执行)",
                "失败模式=观测指标(阈值调整"
                "走 P7 慢环 46号审批)",
                "指纹脱敏留痕(49号隐私"
                "口径)",
            ],
        }
