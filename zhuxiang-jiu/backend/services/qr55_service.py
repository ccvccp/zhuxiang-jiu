"""55号·二维码AI智能管理 服务层(qr55_service)

P0 范围(计划 §六 P0):
    - registry 观测面(注册表自描述——白名单封闭)
    - intent 解析演示(规则轨三态——服务层包装)
    - code 生成演示(签名底座包装——P1 正式生码前
      的能力验证, off 态拒绝)
    - 模型状态视图(44号 get_weights_view 复用)
    - 模型事件留痕(P0 基础)

off 语义:
    QR55_MODE=off(默认) → 生成面关闭(意图解析/
    码生成拒绝——存量二维码链路零影响); registry
    观测面不受影响。
"""

import logging
import os

from core.helpers import ts

from repositories.qr55_repository import (
    Qr55Repository,
)

logger = logging.getLogger("qr55_service")

MODE_KEY = "QR55_MODE"

SCORER_ID = "qr_orchestration"


def current_mode() -> str:
    """模块开关(动态读取——运行时可切换)"""
    return os.environ.get(MODE_KEY, "off")


class Qr55Service:
    """55号二维码管理服务(P0: 注册表+意图+签名底座)"""

    def __init__(self):
        self.repo = Qr55Repository()

    # --------------------------------------------------------
    # 观测面(注册表自描述)
    # --------------------------------------------------------

    @staticmethod
    def registry() -> dict:
        """注册表视图(白名单+红线——观测面不受开关影响)"""
        from services.qr55_registry import registry_view
        view = registry_view()
        view.update({
            "scorer": {
                "scorerId": SCORER_ID,
                "factors": 8,
                "strategies": ("direct", "confirm",
                               "clarify"),
            },
            "note": "P0 底座: 注册表+意图引擎+签名底座"
                    "(生码/核销 P1 接入)",
        })
        return view

    # --------------------------------------------------------
    # 意图解析(规则轨三态——观测演示)
    # --------------------------------------------------------

    def parse_intent(self, text: str,
                     audience: str = None) -> dict:
        """意图解析(三态 resolved/partial/clarify
        ——白名单映射, 零 LLM 依赖)"""
        from services.qr55_intent_service import (
            Qr55IntentService,
        )
        result = Qr55IntentService().parse_intent(
            text, audience=audience)
        result["success"] = True
        result["module"] = "qr55"
        return result

    # --------------------------------------------------------
    # 签名码生成(P0 能力验证——off 拒绝)
    # --------------------------------------------------------

    async def generate_code(self, service_id: str,
                            params: dict,
                            member_id: int,
                            ttl_seconds: int = 300) -> dict:
        """生成签名码(白名单校验+HMAC+exp+nonce)

        Raises:
            ValueError: off 态/服务不在白名单/pending
        """
        from services.qr55_registry import get_service
        mode = current_mode()
        if mode != "on":
            raise ValueError(
                f"QR55_MODE={mode}(默认 off——生成面"
                f"关闭, 存量二维码链路零影响)")

        svc = get_service(service_id)
        if svc is None:
            raise ValueError(
                f"服务 {service_id} 不在白名单"
                f"(幻觉链接防护)")
        if svc.get("status") != "active":
            raise ValueError(
                f"服务 {service_id} 状态 "
                f"{svc.get('status')}(pending 需"
                f"46号审批激活)")

        # 参数白名单过滤
        allowed = set(svc.get("params") or [])
        safe_params = {k: v for k, v in
                       (params or {}).items()
                       if k in allowed}

        from services.qr55_crypto import generate_code
        code_result = generate_code(
            service_id, safe_params, member_id,
            ttl_seconds=ttl_seconds)

        # 码实例落库(生成事件 P1 全链埋点)
        code_id = await self._next_code_id()
        record = {
            "codeId": code_id,
            "eventId": 0,
            "memberId": int(member_id),
            "serviceId": service_id,
            "label": svc.get("label"),
            "code": code_result["code"],
            "nonce": code_result["nonce"],
            "params": safe_params,
            "status": "active",
            "privacyCost": svc.get("privacyCost"),
            "accessibility": False,
            "scanCount": 0,
            "createdAt": ts(),
            "expiresAt": code_result["exp"],
        }
        await self.repo.save_code(record)

        return {
            "success": True,
            "codeId": code_id,
            "code": code_result["code"],
            "serviceId": service_id,
            "label": svc.get("label"),
            "params": safe_params,
            "expiresAt": code_result["exp"],
            "ttlSeconds": ttl_seconds,
            "note": "签名载荷: HMAC+exp+nonce"
                    "(防篡改+防重放+时效)",
            "generatedAt": ts(),
        }

    async def _next_code_id(self) -> int:
        return await self.repo._next_seq("codes")

    # --------------------------------------------------------
    # 模型状态视图(44号复用——观测面)
    # --------------------------------------------------------

    async def model_status(self) -> dict:
        """模型状态(champion/challenger/八因子)"""
        from services.ai_learning_service import (
            get_weights_view,
        )
        from services.qr55_scorer import Qr55Scorer
        view = await get_weights_view(SCORER_ID)
        view.update({
            "module": "qr55",
            "mode": current_mode(),
            "scorerId": SCORER_ID,
            "factorsMeta": {
                "intent_confidence": "意图解析置信度",
                "service_match": "服务匹配度",
                "template_fit": "模板适配度",
                "budget_sufficiency": "隐私预算余量",
                "member_trust": "会员信值等级",
                "expiry_freshness": "有效期新鲜度",
                "accessibility_need": "无障碍需求命中",
                "risk_posture": "风险态势",
            },
            "strategies": ["direct", "confirm", "clarify"],
            "note": "44号学习闭环复用——第30档案",
        })
        return {"success": True, "status": view}

    # --------------------------------------------------------
    # 模型事件留痕(P0 基础——P2/P3 接入)
    # --------------------------------------------------------

    async def record_model_event(self, event_type: str,
                                 detail: dict) -> dict:
        event_id = await \
            self.repo.next_model_event_id()
        record = {
            "modelEventId": event_id,
            "eventType": event_type,
            "detail": detail,
            "createdAt": ts(),
        }
        await self.repo.save_model_event(record)
        return record

    async def model_history(self) -> dict:
        """模型事件历史(最新在前)"""
        records = await self.repo.list_model_events(
            limit=100)
        return {"success": True,
                "total": len(records),
                "events": records}
