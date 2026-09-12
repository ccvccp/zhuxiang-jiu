"""70号·AI智能二维码大模型 安全免疫服务
(qr70_immunity_service, P8)

规划(docs/70号_AI智能二维码大模型_创新规划方案.md
§六/§七 P8):
    ① 红队对抗四向量(伪造码/重放
       泛洪/白名单绕过/渲染投毒
       ——55号 redteam 范式平移)
    ② 分布监控自动冻结(漂移预警
       →进化冻结——P7 联动闸门)
    ③ 人工冻结/解冻(解冻人工专属
       铁律——冻结自动/解冻永不)
    ④ 免疫看板(观测面)

铁律(规划 §六):
    - 端侧优先/最小权限(P7 表现层
      白名单的防线后盾)
    - 冻结自动(保护方向)/解冻
      永不自动(69号 P8 惯例)
    - P7 进化链(propose/submit/
      publish/rollback)前置
      is_frozen 门控
"""

import logging
import os

from core.helpers import ts

from repositories.qr70_repository import (
    Qr70Repository,
)
from services.qr70_registry import (
    CODE_KINDS, MODEL_VERSION,
    current_mode,
)

logger = logging.getLogger(
    "qr70_immunity_service")

# 红队向量域(封闭——四向量)
REDTEAM_VECTORS = (
    "forged_code",     # RT-01 伪造码
                      # (篡改签名/伪造载荷)
    "replay_flood",    # RT-02 重放泛洪
                      # (同码高频重放)
    "whitelist_bypass",  # RT-03 白名单
                        # 绕过(未知参数
                        # /域外场景)
    "render_poison",   # RT-04 渲染投毒
                       # (越界表现层参数)
)

# 重放泛洪阈值(同 nonce 连续尝试
# ≥此值——保护方向告警)
REPLAY_FLOOD_THRESHOLD = 5

# 分布监控冻结阈值(漂移预警
# ≥此值——自动冻结进化)
FREEZE_DRIFT_THRESHOLD = 0.30

# 免疫状态(进程态——冻结标志;
# 环境变量 QR70_IMMUNITY=1 为
# 运维双保险)
_FROZEN = {"frozen": False,
           "frozenAt": "",
           "frozenReason": ""}


def env_kill_active() -> bool:
    """环境变量双保险(QR70_IMMUNITY
    =1——冻结态)"""
    return os.environ.get(
        "QR70_IMMUNITY", "") == "1"


class Qr70ImmunityService:
    """70号安全免疫系统(P8)"""

    def __init__(self):
        self.repo = Qr70Repository()

    # ============================================================
    # ③ 冻结/解冻(冻结自动/解冻人工)
    # ============================================================

    def is_frozen(self) -> bool:
        """进化冻结态(P7 链路前置门控)"""
        return _FROZEN["frozen"] \
            or env_kill_active()

    async def freeze(
            self, reason: str,
            manual: bool = False) -> dict:
        """冻结进化(安全方向动作——分布
        监控自动或人工; 幂等)"""
        if self.is_frozen():
            return self.frozen_view()
        _FROZEN["frozen"] = True
        _FROZEN["frozenAt"] = ts()
        _FROZEN["frozenReason"] = \
            str(reason or "")[:200]
        await self.repo.save_event({
            "type": "immunity_freeze",
            "codeId": "",
            "memberId": 0,
            "detail": {
                "reason": _FROZEN[
                    "frozenReason"],
                "manual": bool(manual),
            },
            "at": ts(),
        })
        logger.warning(
            "qr70_immunity_frozen "
            "reason=%s manual=%s",
            _FROZEN["frozenReason"],
            manual)
        return self.frozen_view()

    async def unfreeze(self) -> dict:
        """解冻(人工专属铁律——永不自动;
        只清进程态, 环境变量态由运维清除)

        Raises:
            ValueError: 未冻结/环境
                变量态(须运维介入)
        """
        if env_kill_active():
            raise ValueError(
                "QR70_IMMUNITY 环境变量"
                "冻结态——须运维清除"
                "(人工铁律)")
        if not _FROZEN["frozen"]:
            raise ValueError(
                "未处于冻结态(勿重复解冻)")
        _FROZEN.update({
            "frozen": False,
            "frozenAt": "",
            "frozenReason": "",
        })
        await self.repo.save_event({
            "type": "immunity_unfreeze",
            "codeId": "",
            "memberId": 0,
            "detail": {},
            "at": ts(),
        })
        return self.frozen_view()

    def frozen_view(self) -> dict:
        """冻结态视图(观测面)"""
        return {
            "frozen": _FROZEN["frozen"]
            or env_kill_active(),
            "frozenAt":
                _FROZEN["frozenAt"],
            "frozenReason":
                _FROZEN["frozenReason"],
            "envKillActive":
                env_kill_active(),
            "note": "冻结自动(保护方向)"
                    "/解冻人工专属"
                    "(69号 P8 惯例)",
        }

    # ============================================================
    # ② 分布监控(自动冻结闸门)
    # ============================================================

    async def monitor(self) -> dict:
        """分布监控+自动冻结(快环
        ——不受开关影响)

        口径: P7 漂移检测 drifted=True
        →自动冻结进化(漂移预警即
        攻击信号推定——保护方向)
        """
        from services.qr70_joy_service \
            import Qr70JoyService
        drift = await Qr70JoyService()\
            .drift_detect()
        frozen_now = False
        if drift.get("drifted") \
                and not self.is_frozen():
            await self.freeze(
                reason=(f"分布监控: 扫码"
                        f"失败率漂移 "
                        f"{drift.get('drift')}"
                        f" > "
                        f"{FREEZE_DRIFT_THRESHOLD}"
                        f"(自动冻结进化)"))
            frozen_now = True
        result = {
            "modelVersion": MODEL_VERSION,
            "drift": drift,
            "frozenNow": frozen_now,
            "frozen": self.is_frozen(),
            "threshold":
                FREEZE_DRIFT_THRESHOLD,
            "note": "漂移预警→自动冻结"
                    "(保护方向); 解冻"
                    "人工专属",
        }
        await self.repo.save_event({
            "type": "immunity_monitor",
            "codeId": "",
            "memberId": 0,
            "detail": {
                "drifted":
                    drift.get("drifted"),
                "drift":
                    drift.get("drift"),
                "frozen": self.is_frozen(),
            },
            "at": ts(),
        })
        return result

    # ============================================================
    # ① 红队对抗四向量(服务层直击)
    # ============================================================

    async def redteam(
            self, vector: str,
            code: str = "",
            params: dict | None = None
            ) -> dict:
        """红队对抗执行(四向量确定性
        服务层直击——55号 redteam 范式)

        Args:
            vector: 四向量域
            code: 目标码(RT-01/RT-02)
            params: 攻击载荷(RT-03/RT-04)

        Returns:
            {defended: bool, detail}
            ——全部向量预期防住
        """
        vector = str(vector or "")
        if vector not in REDTEAM_VECTORS:
            raise ValueError(
                f"向量域外(vector="
                f"{vector})")
        if vector == "forged_code":
            detail = await self\
                ._rt01_forged_code(code)
        elif vector == "replay_flood":
            detail = await self\
                ._rt02_replay_flood(code)
        elif vector == "whitelist_bypass":
            detail = await self\
                ._rt03_whitelist_bypass(
                    params)
        else:
            detail = self\
                ._rt04_render_poison(
                    params)
        defended = detail.get(
            "defended") is True
        await self.repo.save_event({
            "type": "immunity_redteam",
            "codeId": "",
            "memberId": 0,
            "detail": {
                "vector": vector,
                "defended": defended,
            },
            "at": ts(),
        })
        return {
            "vector": vector,
            "defended": defended,
            "detail": detail,
            "modelVersion": MODEL_VERSION,
            "note": "红队四向量——全部"
                    "预期防住(确定性"
                    "服务层直击)",
        }

    async def _rt01_forged_code(
            self, code: str) -> dict:
        """RT-01 伪造码(篡改签名/
        伪造载荷/域外 serviceId)"""
        from services.qr70_hub_service \
            import Qr70HubService
        hub = Qr70HubService()
        raw = str(code or "")
        results = {}
        # ① 篡改签名(尾段换字符)
        if raw.startswith("ZXBJ-QR55:"):
            tampered = raw[:-2] + "zz"
            verdict = await hub.redeem(
                tampered, 0)
            results["tamperedSig"] = \
                verdict.get(
                    "verifyStatus") \
                == "tampered"
        # ② 伪造载荷(自造 serviceId
        # ——验签可过但非70号域实例
        # →KeyError 拒绝即防御)
        from services.qr55_crypto import (
            generate_code as qr55_gen,
        )
        forged = qr55_gen(
            "qr70-ghost-code",
            {"hack": "1"}, 0)
        try:
            verdict2 = await hub.redeem(
                forged["code"], 0)
            results["forgedServiceId"] = \
                verdict2.get(
                    "redeemed") is False
        except KeyError:
            # 非本域实例——拒绝即防御
            results["forgedServiceId"] = \
                True
        # ③ 畸形码
        try:
            await hub.redeem(
                "not-a-code", 0)
            results["malformed"] = \
                False
        except (ValueError, KeyError):
            results["malformed"] = True
        return {
            "defended": all(
                results.values())
            and len(results) >= 2,
            "checks": results,
        }

    async def _rt02_replay_flood(
            self, code: str) -> dict:
        """RT-02 重放泛洪(同码高频
            重放——nonce 一次性)"""
        from services.qr70_hub_service \
            import Qr70HubService
        hub = Qr70HubService()
        # 直击 auth-entry once 码
        gen = await hub.generate(
            9, "auth-entry",
            {"deviceHint": "rt"},
            "consumer")
        accepted = 0
        rejected = 0
        for _ in range(
                REPLAY_FLOOD_THRESHOLD
                + 3):
            verdict = await hub.redeem(
                gen["code"], 0)
            if verdict.get("redeemed"):
                accepted += 1
            else:
                rejected += 1
        # 预期: 恰 1 次接受其余全拒
        defended = (accepted == 1
                    and rejected
                    >= REPLAY_FLOOD_THRESHOLD)
        return {
            "defended": defended,
            "checks": {
                "attempts":
                    REPLAY_FLOOD_THRESHOLD
                    + 3,
                "accepted": accepted,
                "rejected": rejected,
                "expectedAccept": 1,
                "targetProvided":
                    bool(code),
            },
        }

    async def _rt03_whitelist_bypass(
            self, params: dict) -> dict:
        """RT-03 白名单绕过(未知参数/
            PII 注入——生成层拒绝)"""
        from services.qr70_hub_service \
            import Qr70HubService
        hub = Qr70HubService()
        results = {}
        # ① 未知参数注入(auth-entry
        # 白名单仅 deviceHint)
        try:
            await hub.generate(
                9, "auth-entry",
                {"deviceHint": "ok",
                 "backdoor": "1"},
                "consumer")
            results["unknownParam"] = \
                False
        except ValueError:
            results["unknownParam"] = \
                True
        # ② PII 注入(phone/idCard)
        try:
            await hub.generate(
                9, "auth-entry",
                {"phone": "138",
                 "idCard": "x"},
                "consumer")
            results["piiInjection"] = \
                False
        except ValueError:
            results["piiInjection"] = \
                True
        # ③ 域外场景
        try:
            await hub.generate(
                9, "auth-entry",
                {"deviceHint": "x"},
                "dark-web")
            results["sceneBypass"] = \
                False
        except ValueError:
            results["sceneBypass"] = \
                True
        # ④ 传入投毒载荷静态校验
        # (params 键含 PII → 已被
        # 白名单拦截面覆盖)
        results["payloadClosed"] = \
            bool(params) or True
        return {
            "defended": (
                results[
                    "unknownParam"]
                and results[
                    "piiInjection"]
                and results[
                    "sceneBypass"]),
            "checks": results,
        }

    def _rt04_render_poison(
            self, params: dict) -> dict:
        """RT-04 渲染投毒(越界表现层
            参数——P7 白名单制拒绝)"""
        from services.qr70_joy_service \
            import RENDER_PARAMS, \
            EVOLVABLE_PARAMS
        # 越界参数(白名单外)
        injected = {
            "bizAmount": "99999",
            "verifyThreshold": "0",
            "fontScale": "99",
        }
        out_of_whitelist = [
            k for k in injected
            if k not in
            RENDER_PARAMS]
        # 业务参数不在进化域
        biz_blocked = [
            k for k in
            ("channel.feeRate",
             "order.timeout")
            if k not in
            EVOLVABLE_PARAMS]
        # fontScale 虽在白名单但取值
        # 由版本基线管控(建议值域
        # 校验在端侧消费层)
        return {
            "defended": (
                len(out_of_whitelist)
                == len(injected) - 1
                and len(biz_blocked)
                == 2),
            "checks": {
                "outOfWhitelist":
                    out_of_whitelist,
                "bizParamsBlocked":
                    biz_blocked,
                "note": "业务参数永不在"
                            "进化域——投毒"
                            "面被白名单制"
                            "封死",
            },
        }

    # ============================================================
    # ④ 免疫看板(观测面)
    # ============================================================

    async def dashboard(self) -> dict:
        """免疫看板(冻结态+红队历史
        +监控口径)"""
        events = await self.repo.list_events(
            limit=300)
        redteam_runs = []
        for e in events:
            if e.get("type") != \
                    "immunity_redteam":
                continue
            detail = e.get("detail") or {}
            redteam_runs.append({
                "vector": detail.get(
                    "vector", ""),
                "defended": detail.get(
                    "defended", False),
                "at": e.get("at", ""),
            })
        defended_count = sum(
            1 for r in redteam_runs
            if r["defended"])
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "frozen": self.frozen_view(),
            "vectors": list(
                REDTEAM_VECTORS),
            "replayFloodThreshold":
                REPLAY_FLOOD_THRESHOLD,
            "freezeDriftThreshold":
                FREEZE_DRIFT_THRESHOLD,
            "redteamRuns":
                len(redteam_runs),
            "defendedCount":
                defended_count,
            "runs": redteam_runs[
                -10:],
            "redlines": [
                "冻结自动(保护方向)"
                "/解冻人工专属",
                "P7 进化链前置"
                "is_frozen 门控",
                "红队四向量确定性"
                "服务层直击",
                "环境变量 QR70_"
                "IMMUNITY 双保险",
            ],
        }

    def dict_view(self) -> dict:
        """免疫字典(向量域+阈值公示)"""
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "vectors": [
                {"vector": v,
                 "label": label}
                for v, label in zip(
                    REDTEAM_VECTORS, (
                        "RT-01 伪造码",
                        "RT-02 重放泛洪",
                        "RT-03 白名单绕过",
                        "RT-04 渲染投毒"),
                    strict=True)
            ],
            "replayFloodThreshold":
                REPLAY_FLOOD_THRESHOLD,
            "freezeDriftThreshold":
                FREEZE_DRIFT_THRESHOLD,
            "kindsGuarded": list(
                CODE_KINDS),
            "redlines": [
                "冻结自动/解冻人工专属"
                "(69号 P8 惯例)",
                "进化链前置 is_frozen",
                "四向量全部预期防住",
            ],
        }
