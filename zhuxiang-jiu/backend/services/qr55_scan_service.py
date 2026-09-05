"""55号·二维码AI智能管理 扫码核销(qr55_scan_service, P1)

计划(docs/55号_二维码AI智能管理模块实施计划.md §三):
    扫码核销全链:
    ① 验签(HMAC 四态——P0 底座)
    ② nonce 一次性消费(防重放——replayed 态)
    ③ 隐私预算扣减(49号 check_and_spend——
       L0 零成本永不降级红线; 失败→降级公开版)
    ④ 千面落地页渲染(信值深度/无障碍样式)
    ⑤ 跨端续接标记(continueOn 移动/PC)
    ⑥ 状态翻转(active→redeemed)+scan 埋点

核销态机:
    ok(首次) → redeemed + 落地页
    expired → 410 语义提示刷新
    tampered → 403 语义阻断+tamper 埋点
    replayed → 409 语义提示刷新新码
    budget_blocked → 降级公开版页面(千面降级)
"""

import logging

from core.helpers import ts

from repositories.qr55_repository import (
    Qr55Repository,
)

logger = logging.getLogger("qr55_scan_service")

MODEL_VERSION = "v1-qr55-scan"


class Qr55ScanService:
    """55号扫码核销(验签→防重放→预算→千面落地)"""

    def __init__(self):
        self.repo = Qr55Repository()

    # ============================================================
    # 核销主链
    # ============================================================

    async def scan(self, code: str,
                   member_id: int = None,
                   continue_on: str = "mobile",
                   accessibility: bool = False
                   ) -> dict:
        """扫码核销(验签+防重放+预算+千面落地)

        Args:
            code: 签名码(P0 五段格式)
            member_id: 扫码者(预算主体——缺省码属主)
            continue_on: 跨端续接 mobile/pc
            accessibility: 无障碍样式

        Raises:
            ValueError: 格式非法/off 态
        """
        import os
        mode = os.environ.get("QR55_MODE", "off")
        if mode != "on":
            raise ValueError(
                f"QR55_MODE={mode}(默认 off——核销面"
                f"关闭)")

        # ① 验签(四态)
        from services.qr55_crypto import verify_code
        result = verify_code(code)

        if result.get("status") == "tampered":
            await self._track(None, member_id,
                              "tamper", {
                                  "reason": result.get(
                                      "reason"),
                                  "codeHead": (
                                      code or "")[:32],
                              })
            return {
                "success": False,
                "status": "tampered",
                "reason": "验签失败——码可能被篡改, "
                          "已阻断并上报",
                "codeStatus": "tampered",
            }

        if result.get("status") == "expired":
            # 验签已过——nonce 可信, 反查码实例挂链
            # (P2 修复: expire 事件缺 codeId 导致
            # 生成过剩信号无法按码聚合)
            nonce = result.get("nonce")
            code_rec = await self.repo.get_by_nonce(
                nonce) if nonce else None
            await self._track(
                (code_rec or {}).get("codeId"),
                member_id, "expire", {
                    "serviceId":
                        result.get("serviceId"),
                })
            return {
                "success": False,
                "status": "expired",
                "reason": "码已过期——请重新生成",
                "codeStatus": "expired",
            }

        # ② nonce 一次性消费(防重放)
        nonce = result.get("nonce")
        code_rec = await self.repo.get_by_nonce(nonce)
        first = await self.repo.consume_nonce(nonce)
        if not first:
            await self._track(
                (code_rec or {}).get("codeId"),
                member_id, "replay", {
                    "nonce": (nonce or "")[:8],
                })
            return {
                "success": False,
                "status": "replayed",
                "reason": "码已被使用(一次性)——"
                          "请生成新码",
                "codeStatus": "replayed",
            }

        service_id = result.get("serviceId")
        payload = result.get("payload") or {}
        code_id = (code_rec or {}).get("codeId") or 0

        # ③ 隐私预算扣减(49号——fail-soft 降级)
        budget_mode, budget_note = \
            await self._spend_budget(
                member_id, service_id)

        # ④ 千面落地页
        landing = await self._render_landing(
            service_id, payload, member_id,
            accessibility, budget_mode)

        # ⑤ 状态翻转+计数
        if code_rec is not None:
            code_rec["status"] = "redeemed"
            code_rec["scanCount"] = int(
                code_rec.get("scanCount") or 0) + 1
            code_rec["redeemedAt"] = ts()
            await self.repo.update_code(code_rec)

        # ⑥ scan 埋点
        await self._track(code_id, member_id, "scan", {
            "serviceId": service_id,
            "budgetMode": budget_mode,
            "continueOn": continue_on,
            "landing": {
                "depth": landing.get("depth"),
                "degraded": landing.get("degraded"),
            },
        })

        return {
            "success": True,
            "status": "redeemed",
            "codeId": code_id,
            "serviceId": service_id,
            "landing": landing,
            "crossDevice": {
                "continueOn": continue_on,
                "note": "跨端续接——状态实时同步"
                        "(P4 服务闭环追踪)",
            },
            "budget": {"mode": budget_mode,
                       "note": budget_note},
            "redeemedAt": ts(),
        }

    # ============================================================
    # 预算扣减(49号 织入——超预算降级公开版)
    # ============================================================

    @staticmethod
    async def _spend_budget(member_id: int,
                            service_id: str
                            ) -> tuple:
        """预算扣减(返回 mode, note)

        mode: spent 正常扣减 / zero_cost 零成本
              / degraded 降级公开版 / skip 主体缺失
        """
        from services.qr55_registry import get_service
        svc = get_service(service_id) or {}
        cost = float(svc.get("privacyCost") or 0.0)

        if cost <= 0:
            return "zero_cost", \
                "公开服务零成本(永不降级红线)"

        if member_id is None:
            return "skip", \
                "扫码主体缺失——跳过扣减(观测)"

        try:
            from services.xiaozhu_privacy_service \
                import XiaozhuPrivacyService
            spend = await \
                XiaozhuPrivacyService(
                ).check_and_spend(int(member_id), cost)
            return "spent", (
                f"已扣减 {spend.get('spent')}"
                f"(余 {spend.get('remaining')})")
        except ValueError:
            # 预算不足 → 降级公开版(千面降级铁律)
            return "degraded", \
                "隐私预算不足——降级公开版页面"
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qr55_budget_spend_failed: %s", exc)
            return "skip", \
                f"预算服务异常——跳过扣减({str(exc)[:40]})"

    # ============================================================
    # 千面落地页(信值深度+无障碍+预算降级)
    # ============================================================

    async def _render_landing(self, service_id: str,
                             payload: dict,
                             member_id: int,
                             accessibility: bool,
                             budget_mode: str) -> dict:
        """渲染个性化落地页(千面千码)"""
        from services.qr55_registry import get_service
        svc = get_service(service_id) or {}

        grade = await self._member_grade(member_id) \
            if member_id is not None else None
        depth_map = {
            "healthy": "full", "watch": "standard",
            "strained": "basic",
            "critical": "minimal", None: "standard",
        }
        depth = depth_map.get(grade, "standard")

        landing = {
            "serviceId": service_id,
            "label": svc.get("label"),
            "route": svc.get("route"),
            "template": svc.get("template"),
            "depth": depth,
            "accessibility": {
                "style": "high_contrast"
                if accessibility else "standard",
                "voiceTrigger": accessibility,
            },
            "degraded": budget_mode == "degraded",
            "degradeNote": "公开版(最小必要字段)"
                           if budget_mode == "degraded"
                           else "",
            "params": payload.get("params") or {},
            "note": "千面适配——同一码按扫码者"
                    "身份/预算/无障碍自适应",
        }
        return landing

    # --------------------------------------------------------
    # 事件埋点(fail-soft)
    # --------------------------------------------------------

    @staticmethod
    async def _member_grade(member_id: int) -> str | None:
        """会员信值档位(45号——fail-soft)"""
        try:
            from repositories.trust_value_repository \
                import TrustValue45Repository
            rec = await TrustValue45Repository(
            ).get_profile(int(member_id))
            return (rec or {}).get("grade")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qr55_scan_grade_failed %s: %s",
                member_id, exc)
            return None

    async def _track(self, code_id, member_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "codeId": int(code_id or 0),
                "memberId": int(member_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qr55_scan_track_failed %s: %s",
                event_type, exc)
