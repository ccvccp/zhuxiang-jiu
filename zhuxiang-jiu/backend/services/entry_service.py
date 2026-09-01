"""39号·AI智能网站入口管理模块业务逻辑层(P0)

核心业务(设计文档 §2):
    - 统一入口网关: AI 预判推荐登录方式 + 六通道一页(§2.1)
    - 扫码登录: QR 令牌轮询协议(创建/扫码/确认/ticket 换令牌)(§2.2)
    - AI 风控决策引擎: auth_risk 评分器接入登录决策链(§2.4)
    - 设备指纹 + 可信设备管理(30 天免登录)(§2.3/§2.4)
    - 驻留埋点: 登录事件流水 + 注册归并挂接(§2.6)

复用(设计文档 §1.4):
    - 30号 auth_service: 密码/短信校验 + _login_by_member_id 签发
    - auth_risk 评分器(已建成): 8 因子 4 级动作(allow/step_up/
      challenge/block), 接入决策链闭环
    - attract.attach_registration: 注册归并三合一

降级铁律(设计文档 §2.4): 评分器异常 → 默认 step_up(不裸放)

锁保护:
    扫码确认/换令牌: entry:qr:{qrId}
    生物凭证(P1): entry:bio:{memberId}
"""

import hashlib
import logging
import secrets
from datetime import datetime, UTC

from core.locks import get_lock
from repositories.entry_repository import (
    EntryRepository,
    QR_PENDING, QR_SCANNED, QR_CONFIRMED, QR_EXPIRED, QR_CANCELLED,
    QR_TTL_SECONDS, LOGIN_TICKET_TTL,
    TRUST_DAYS_DEFAULT,
    GUARD_ALLOW, GUARD_STEP_UP, GUARD_CHALLENGE, GUARD_BLOCK,
    MODE_PASSWORD, MODE_SMS, MODE_QR, MODE_FINGERPRINT, MODE_FACE,
    MODE_OAUTH,
)

logger = logging.getLogger(__name__)

# 内置 IP 信誉简表(可扩展; 黑名单段直接触发硬拦截)
IP_REPUTATION_TABLE = {
    "127.0.0.1": "clean", "localhost": "clean",
    "10.": "clean", "192.168.": "clean", "172.16.": "clean",
}
IP_RISK_TYPES = ("clean", "proxy", "vpn", "tor", "blacklist")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _now_ts() -> float:
    import time
    return time.time()


def hash_device_id(fingerprint: str) -> str:
    """弱特征指纹 → 设备 ID(UA/屏幕/语言/时区摘要, 不含持久标识)"""
    return "DV" + hashlib.sha256(
        (fingerprint or "").encode("utf-8")).hexdigest()[:16]


class EntryService:
    """39号·AI智能网站入口管理模块业务逻辑层"""

    def __init__(self):
        self.repo = EntryRepository()

    # ============================================================
    # 设备指纹与识别(AI 预判入口, 设计文档 §2.1)
    # ============================================================

    async def recognize(self, fingerprint: str,
                        ip: str = "") -> dict:
        """入口 AI 预判: 设备识别 → 推荐登录方式排序 + 问候

        口径(设计文档 §1.3): 可信设备(未过期)→生物/免登录优先;
        历史短信用户→短信优先; 全新设备→默认排序。
        """
        device_id = hash_device_id(fingerprint)
        profile = await self.repo.get_fingerprint(device_id)
        known = profile is not None
        # 该设备的历史成功通道(事件流水反查)
        history_modes = []
        if known:
            events = await self.repo.list_events(limit=200)
            seen = set()
            for e in sorted(events, key=lambda x: x.get("eventId", 0),
                            reverse=True):
                mode = e.get("mode")
                if mode and mode not in seen and e.get("success"):
                    seen.add(mode)
                    history_modes.append(mode)
        # 推荐排序: 历史(最近使用优先) → 其余通道补齐
        all_modes = [MODE_QR, MODE_SMS, MODE_PASSWORD,
                     MODE_FINGERPRINT, MODE_FACE, MODE_OAUTH]
        ranked = history_modes + [m for m in all_modes
                                  if m not in history_modes]
        greeting = ""
        if known:
            last = str(profile.get("lastSeenAt") or "")[:10]
            greeting = f"欢迎回来(上次访问 {last})"
        return {
            "deviceId": device_id,
            "knownDevice": known,
            "recommendedModes": ranked[:4],
            "greeting": greeting,
            "fingerprintHint": "弱特征摘要(UA/屏幕/语言/时区), 可随时删除",
        }

    async def register_fingerprint(self, device_id: str) -> dict:
        """记录/刷新设备指纹简档(幂等)"""
        profile = await self.repo.get_fingerprint(device_id)
        now = _now_iso()
        if profile is None:
            record = {"deviceId": device_id,
                      "firstSeenAt": now, "lastSeenAt": now,
                      "seenCount": 1}
        else:
            record = dict(profile)
            record["lastSeenAt"] = now
            record["seenCount"] = int(record.get("seenCount", 1)) + 1
        return await self.repo.save_fingerprint(device_id, record)

    # ============================================================
    # AI 风控决策引擎(设计文档 §2.4)
    # ============================================================

    async def guard(self, member_id: int, mode: str,
                    fingerprint: str = "", ip: str = "",
                    password_status: str = None) -> dict:
        """登录决策: 采集上下文 → auth_risk 评分 → 动作

        降级铁律: 评分器异常 → 默认 step_up(不裸放)。
        决策落库留痕(entry:decisions)。
        """
        device_id = hash_device_id(fingerprint) if fingerprint else ""
        # 设备匹配因子: 可信清单命中 → 常用设备(False=非新设备)
        new_device = None
        if device_id:
            device = await self.repo.get_device(member_id, device_id)
            new_device = device is None
        # 失败计数(IP 维窗口)
        failed = self.repo.get_failed_attempts(f"ip:{ip or 'unknown'}")
        # IP 信誉(内置简表, 未命中 clean)
        ip_risk = "clean"
        for prefix, risk in IP_REPUTATION_TABLE.items():
            if (ip or "").startswith(prefix):
                ip_risk = risk
                break
        # 账龄
        account_age_days = 365.0
        try:
            from repositories.member_repository import MemberRepository
            member = await MemberRepository().get_by_id(member_id)
            if member and member.get("created_at"):
                created = str(member["created_at"])[:10]
                account_age_days = max(
                    0.0, (datetime.now(UTC)
                          - datetime.fromisoformat(created
                                                   + "T00:00:00+00:00")
                          ).days)
        except Exception:
            account_age_days = 365.0
        ctx = {
            "memberId": member_id,
            "failedAttempts": failed,
            "newDevice": new_device,
            "ipRiskType": ip_risk,
            "loginHour": datetime.now(UTC).hour,
            "accountAgeDays": account_age_days,
            "passwordStatus": password_status,
        }
        # 评分(异常降级 step_up 铁律)
        ai_result = None
        degraded = False
        try:
            from services.ai_scoring_auth_service import AuthRiskScorer
            ai_result = await AuthRiskScorer().score(ctx)
        except Exception as exc:
            logger.warning("entry_guard_score_failed: %s", exc)
            degraded = True
        if ai_result is None or not ai_result.get("success"):
            ai_result = {"score": 40.0, "action": GUARD_STEP_UP,
                         "factors": [], "hardBlocked": False}
            degraded = True
        action = ai_result["action"]
        # 可信设备豁免: 未过期 trustedUntil 的设备 risk<70 时放行为 allow
        if device_id and action in (GUARD_ALLOW, GUARD_STEP_UP):
            device = await self.repo.get_device(member_id, device_id)
            if device and str(device.get("trustedUntil", "")) \
                    > _now_iso():
                action = GUARD_ALLOW
        decision_id = await self.repo.next_id("decision")
        record = {
            "decisionId": decision_id,
            "memberId": member_id,
            "mode": mode,
            "deviceId": device_id,
            "ip": ip or "",
            "riskScore": ai_result.get("score", 0),
            "action": action,
            "degraded": degraded,
            "factors": ai_result.get("factors") or [],
            "hardBlocked": bool(ai_result.get("hardBlocked")),
            "reviewStatus": "none",   # P1 反馈回流口径
            "createdAt": _now_iso(),
        }
        await self.repo.save_decision(record)
        return record

    # ============================================================
    # 统一登录端点(设计文档 §2.1: 密码/短信 + 风险自适应)
    # ============================================================

    async def login(self, mode: str, fingerprint: str = "",
                    ip: str = "", phone: str = "",
                    password: str = "", sms_code: str = "") -> dict:
        """统一登录: 校验 → 风控决策 → allow 直发/step_up 待二次

        Raises:
            ValueError: 参数非法/凭证错误/风控拦截
            KeyError: 会员不存在
        """
        if mode not in (MODE_PASSWORD, MODE_SMS):
            raise ValueError(f"统一登录端点暂不支持该通道({mode}, "
                             f"扫码/生物走专用协议端点)")
        if not phone:
            raise ValueError("手机号不能为空")
        from services.auth_service import AuthService
        auth = AuthService()
        if mode == MODE_PASSWORD:
            result = await auth.login(phone=phone, password=password)
            member_id = int(result.get("memberId") or 0)
        else:
            result = await auth.login_by_sms(phone=phone,
                                             code=sms_code)
            member_id = int(result.get("memberId") or 0)
        if not member_id:
            raise ValueError("登录结果缺少会员ID")
        # 风控决策(密码强度因子: 简化口径 strong)
        decision = await self.guard(
            member_id, mode, fingerprint=fingerprint, ip=ip,
            password_status="strong" if mode == MODE_PASSWORD else None)
        self.repo.clear_failed_attempts(f"ip:{ip or 'unknown'}")
        if decision["action"] == GUARD_BLOCK:
            self.repo.bump_failed_attempts(f"ip:{ip or 'unknown'}")
            reasons = decision.get("hardBlocked")
            raise ValueError(
                f"登录被风控拦截(风险分{decision['riskScore']}"
                f"{', 硬约束命中' if reasons else ''}), "
                f"如有疑问请联系客服申诉")
        # 设备记录(登录即记, 无论决策档位)
        device_id = hash_device_id(fingerprint) if fingerprint else ""
        if device_id:
            await self._record_device(member_id, device_id, ip)
            await self.register_fingerprint(device_id)
        # 事件流水
        await self._record_event(member_id, mode, True,
                                 decision["riskScore"], device_id)
        if decision["action"] == GUARD_ALLOW:
            return {"status": "authenticated", "tokens": result,
                    "memberId": member_id, "decision": decision}
        # step_up / challenge: 返回待二次(不签发令牌)
        return {"status": "step_up_required",
                "memberId": member_id, "decision": decision,
                "stepUpHint": ("短信验证码二次核验" if
                               decision["action"] == GUARD_STEP_UP
                               else "强核验(安全问题/刷脸)")}

    async def step_up_verify(self, member_id: int, phone: str,
                             sms_code: str, fingerprint: str = "",
                             ip: str = "") -> dict:
        """step_up 二次验证完成 → 签发令牌

        Raises:
            ValueError: 验证码错误
        """
        from services.auth_service import AuthService
        auth = AuthService()
        result = await auth.login_by_sms(phone=phone, code=sms_code)
        if int(result.get("memberId") or 0) != member_id:
            raise ValueError("二次验证手机号与登录账号不一致")
        device_id = hash_device_id(fingerprint) if fingerprint else ""
        if device_id:
            await self._record_device(member_id, device_id, ip)
        await self._record_event(member_id, MODE_SMS, True, 0,
                                 device_id, note="step_up")
        return {"status": "authenticated", "tokens": result,
                "memberId": member_id}

    # ============================================================
    # 扫码登录协议(设计文档 §2.2)
    # ============================================================

    async def qr_create(self, fingerprint: str = "") -> dict:
        """PC 创建扫码会话(180s 有效)"""
        qr_id = f"QR{secrets.token_hex(8)}"
        seq = await self.repo.next_id("qr")
        record = {
            "qrId": qr_id,
            "seq": seq,
            "status": QR_PENDING,
            "creatorDevice": hash_device_id(fingerprint)
            if fingerprint else "",
            "confirmMemberId": None,
            "loginTicketHash": "",
            "expiresAt": _now_ts() + QR_TTL_SECONDS,
            "createdAt": _now_iso(),
        }
        await self.repo.save_qr(record)
        return {
            "qrId": qr_id,
            # 前端 canvas 可渲染的载荷(对齐 attract 文本载荷惯例)
            "qrPayload": f"ZXBJ-ENTRY:{qr_id}",
            "expiresIn": QR_TTL_SECONDS,
            "statusUrl": f"/api/entry/qr/{qr_id}/status",
        }

    async def qr_confirm(self, qr_id: str, member_id: int,
                         fingerprint: str = "",
                         ip: str = "") -> dict:
        """手机端(已登录态)扫码确认 → 生成一次性 loginTicket

        Raises:
            KeyError: 会话不存在
            ValueError: 状态非法(非 scanned 可确认态)
        """
        async with get_lock(f"entry:qr:{qr_id}"):
            record = await self.repo.get_qr(qr_id)
            if record is None:
                raise KeyError(f"扫码会话不存在(qrId={qr_id})")
            self._qr_expire_if_due(record, qr_id)
            if record["status"] != QR_SCANNED:
                raise ValueError(
                    f"扫码会话状态不可确认(当前{record['status']})")
            # 手机端同样过风控(高风险手机端也须二次, 设计文档 §2.2)
            decision = await self.guard(
                member_id, MODE_QR, fingerprint=fingerprint, ip=ip)
            if decision["action"] == GUARD_BLOCK:
                raise ValueError(
                    f"确认被风控拦截(风险分{decision['riskScore']})")
            ticket = f"LT{secrets.token_hex(12)}"
            ticket_hash = hashlib.sha256(
                ticket.encode()).hexdigest()[:32]
            await self.repo.update_qr(qr_id, {
                "status": QR_CONFIRMED,
                "confirmMemberId": member_id,
                "loginTicketHash": ticket_hash,
                "confirmedAt": _now_iso(),
                "decisionId": decision["decisionId"],
            })
            await self._record_event(member_id, MODE_QR, True,
                                     decision["riskScore"], "",
                                     note="qr_confirm")
            return {"qrId": qr_id, "status": QR_CONFIRMED,
                    "loginTicket": ticket,
                    "ticketTtl": LOGIN_TICKET_TTL,
                    "confirmSide": "mobile"}

    async def qr_scan(self, qr_id: str,
                      mock_member_id: int = None) -> dict:
        """扫码动作(pending → scanned)

        Mock 轨: mock_member_id 提供时直接带入(单端演示/测试);
        真实轨由手机端 confirm 前置触发(或 P2 WebSocket 推送)。
        """
        record = await self.repo.get_qr(qr_id)
        if record is None:
            raise KeyError(f"扫码会话不存在(qrId={qr_id})")
        self._qr_expire_if_due(record, qr_id)
        if record["status"] != QR_PENDING:
            raise ValueError(
                f"扫码会话状态不可扫(当前{record['status']})")
        fields = {"status": QR_SCANNED, "scannedAt": _now_iso()}
        if mock_member_id is not None:
            fields["confirmMemberId"] = mock_member_id
        await self.repo.update_qr(qr_id, fields)
        return {"qrId": qr_id, "status": QR_SCANNED}

    async def qr_status(self, qr_id: str) -> dict:
        """PC 轮询状态(过期惰性标记; confirmed 携带 ticket 兑换指引)"""
        record = await self.repo.get_qr(qr_id)
        if record is None:
            raise KeyError(f"扫码会话不存在(qrId={qr_id})")
        self._qr_expire_if_due(record, qr_id)
        return {"qrId": qr_id, "status": record.get("status"),
                "seq": record.get("seq"),
                "expiresAt": record.get("expiresAt")}

    async def qr_exchange(self, qr_id: str,
                          login_ticket: str) -> dict:
        """PC 用一次性 loginTicket 换 JWT 双令牌(60s TTL, 幂等拒绝)

        Raises:
            KeyError: 会话不存在
            ValueError: 票据无效/已使用/过期
        """
        async with get_lock(f"entry:qr:{qr_id}"):
            record = await self.repo.get_qr(qr_id)
            if record is None:
                raise KeyError(f"扫码会话不存在(qrId={qr_id})")
            self._qr_expire_if_due(record, qr_id)
            if record.get("status") != QR_CONFIRMED:
                raise ValueError(
                    f"会话未确认或已失效(当前{record.get('status')})")
            ticket_hash = hashlib.sha256(
                (login_ticket or "").encode()).hexdigest()[:32]
            if ticket_hash != record.get("loginTicketHash"):
                raise ValueError("登录票据无效")
            member_id = int(record.get("confirmMemberId") or 0)
            if not member_id:
                raise ValueError("会话缺少确认人, 无法签发")
            # 一次性: 立即失效票据防重放
            await self.repo.update_qr(qr_id, {
                "loginTicketHash": "", "status": QR_EXPIRED,
                "exchangedAt": _now_iso()})
            from services.auth_service import AuthService
            tokens = await AuthService()._login_by_member_id(member_id)
            await self._record_event(member_id, MODE_QR, True, 0,
                                     record.get("creatorDevice", ""),
                                     note="qr_exchange")
            return {"status": "authenticated", "tokens": tokens,
                    "memberId": member_id}

    async def qr_cancel(self, qr_id: str) -> dict:
        """取消扫码会话(任一侧; 幂等)"""
        record = await self.repo.get_qr(qr_id)
        if record is None:
            raise KeyError(f"扫码会话不存在(qrId={qr_id})")
        if record.get("status") in (QR_CONFIRMED, QR_EXPIRED,
                                    QR_CANCELLED):
            return {"qrId": qr_id, "status": record["status"]}
        await self.repo.update_qr(qr_id, {
            "status": QR_CANCELLED, "cancelledAt": _now_iso()})
        return {"qrId": qr_id, "status": QR_CANCELLED}

    def _qr_expire_if_due(self, record: dict, qr_id: str) -> None:
        """过期惰性标记(终局态不覆盖)"""
        if record.get("status") in (QR_EXPIRED, QR_CANCELLED,
                                    QR_CONFIRMED):
            return
        if float(record.get("expiresAt") or 0) < _now_ts():
            record["status"] = QR_EXPIRED
            # best-effort 持久化(轮询读路径不因写失败报错)
            try:
                import asyncio
                asyncio.get_event_loop().create_task(
                    self.repo.update_qr(qr_id,
                                        {"status": QR_EXPIRED}))
            except Exception:
                pass

    # ============================================================
    # 可信设备管理(设计文档 §2.3/§2.4)
    # ============================================================

    async def _record_device(self, member_id: int, device_id: str,
                             ip: str = "") -> dict:
        """登录即记设备(不存在则建; 保留 trustedUntil 不覆盖)"""
        device = await self.repo.get_device(member_id, device_id)
        now = _now_iso()
        if device is None:
            record = {"deviceId": device_id, "memberId": member_id,
                      "deviceName": f"设备{device_id[-4:]}",
                      "lastLoginAt": now, "lastIp": ip or "",
                      "riskAvg": 0.0, "trustedUntil": "",
                      "firstLoginAt": now}
        else:
            record = dict(device)
            record.update({"lastLoginAt": now, "lastIp": ip or ""})
        return await self.repo.save_device(member_id, record)

    async def list_devices(self, member_id: int) -> list[dict]:
        devices = await self.repo.list_devices(member_id)
        now = _now_iso()
        for d in devices:
            d["trusted"] = bool(d.get("trustedUntil")
                                and str(d["trustedUntil"]) > now)
        return devices

    async def trust_device(self, member_id: int, device_id: str,
                           days: int = TRUST_DAYS_DEFAULT) -> dict:
        """开启可信免登录(默认 30 天)

        Raises:
            KeyError: 设备未记录(须先登录一次)
        """
        from datetime import timedelta
        device = await self.repo.get_device(member_id, device_id)
        if device is None:
            raise KeyError(
                f"设备未记录(deviceId={device_id}, 须先登录一次)")
        until = (datetime.now(UTC)
                 + timedelta(days=days)).isoformat()
        await self.repo.save_device(member_id, {
            **device, "trustedUntil": until})
        return {**device, "trustedUntil": until, "trusted": True}

    async def remove_device(self, member_id: int,
                            device_id: str) -> dict:
        """删除设备(吊销信任; 幂等)"""
        device = await self.repo.get_device(member_id, device_id)
        if device is not None:
            await self.repo.delete_device(member_id, device_id)
        return {"deviceId": device_id, "removed": True}

    # ============================================================
    # 事件流水与看板(设计文档 §2.6)
    # ============================================================

    async def _record_event(self, member_id: int, mode: str,
                            success: bool, risk_score: float = 0,
                            device_id: str = "", note: str = "") -> dict:
        event_id = await self.repo.next_id("event")
        record = {"eventId": event_id, "memberId": member_id,
                  "mode": mode, "success": success,
                  "riskScore": risk_score, "deviceId": device_id,
                  "note": note, "createdAt": _now_iso()}
        return await self.repo.save_event(record)

    async def registration_merge(self, member_id: int,
                                 click_id: int = None) -> dict:
        """注册归并挂接(attract 三合一, best-effort 不阻断)"""
        if not click_id:
            return {"merged": False, "reason": "无 click_id"}
        try:
            from services.attract_service import AttractService
            result = await AttractService().attach_registration(
                click_id, member_id)
            return {"merged": True, "result": result}
        except Exception as exc:
            logger.warning("entry_registration_merge_failed: %s", exc)
            return {"merged": False, "reason": str(exc)[:120]}

    async def overview(self) -> dict:
        """入口看板: 通道漏斗/风险分布/决策统计"""
        events = await self.repo.list_events(limit=1000)
        decisions = await self.repo.list_decisions(limit=1000)
        mode_stats = {}
        for e in events:
            m = e.get("mode", "")
            stat = mode_stats.setdefault(
                m, {"attempts": 0, "success": 0})
            stat["attempts"] += 1
            if e.get("success"):
                stat["success"] += 1
        for m, stat in mode_stats.items():
            stat["rate"] = round(
                stat["success"] / stat["attempts"] * 100, 1) \
                if stat["attempts"] else 0.0
        action_stats = {}
        for d in decisions:
            action_stats[d.get("action", "")] = \
                action_stats.get(d.get("action", ""), 0) + 1
        return {
            "modeStats": mode_stats,
            "actionStats": action_stats,
            "totalEvents": len(events),
            "totalDecisions": len(decisions),
            "degradedDecisions": sum(
                1 for d in decisions if d.get("degraded")),
        }
