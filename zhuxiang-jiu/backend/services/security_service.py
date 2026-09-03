"""43号·AI智能安全管理业务服务(P0)

功能(设计文档 §2.1-2.3):
    - 特征扫描: 注入特征(SQLi/XSS/遍历/命令注入/扫描器) +
      路径异常(探针路径) + 身份风险(未认证打敏感端点)
    - IP 信誉: 冷启动 80 / 攻击扣分(-10/-30/-60) / 冷却恢复 / 钉住
    - 决策管线: ThreatGateScorer(第26档案) → allow/throttle/
      challenge/block 四档, observe 灰度仅留痕
    - 封禁表: TTL 自动解封(懒清理)
    - 事件流水: 仅可疑档入流水(防爆炸), 供 P1 裁决与 P2 学习回流

安全铁律(网关自身不能成为故障点):
    - process_request 任何异常 → 放行(fail-open) + 日志
    - SECURITY_GATEWAY_MODE=off → 全放行(一键回退)
    - observe/shadow → 只留痕不处置, enforce 才真正处置

返回约定(参考 invoice_service):
    - 业务校验失败: raise ValueError → 路由层映射 409
"""

import os
import re
import logging
from datetime import datetime, UTC
from urllib.parse import unquote_plus

from core.helpers import ts
from repositories.security_repository import (
    Security43Repository,
    REPUTATION_NORMAL, REPUTATION_SUSPICIOUS, REPUTATION_BLACKLISTED,
    REPUTATION_COLD_START, reputation_status,
)
from services.ai_scoring_service import SCORERS

logger = logging.getLogger(__name__)


# ============================================================
# P0 常量(环境变量运行时动态读, 不冻结)
# ============================================================

ACTION_ALLOW = "allow"
ACTION_THROTTLE = "throttle"
ACTION_CHALLENGE = "challenge"
ACTION_BLOCK = "block"
ACTIONS = (ACTION_ALLOW, ACTION_THROTTLE, ACTION_CHALLENGE, ACTION_BLOCK)

# 档位 → IP 信誉扣分(设计文档 §2.3)
PENALTY_MAP = {
    ACTION_THROTTLE: 10.0,
    ACTION_CHALLENGE: 30.0,
    ACTION_BLOCK: 60.0,
}

# 网关快道白名单(健康检查永久放行 + 挑战应答端点防死锁)
GATEWAY_WHITELIST = (
    "/api/decision/health",
    "/api/monitor/health",
    "/api/maintenance/health",
    # 43号 P1: 挑战应答端点自身不得被挑战(enforce 下死循环);
    # mock 口径应答即过, P3 真验证码(极验/hCaptcha)接入后收紧
    "/api/security/challenge/verify",
)

# 未认证请求打敏感端点 → 身份风险降分(其余满分)
SENSITIVE_PREFIXES = (
    "/api/admin/", "/api/finance/", "/api/invoice/internal/",
    "/api/security/admin/",
)
SENSITIVE_LOGIN_PATHS = ("/api/admin/login",)
IDENTITY_UNAUTH_SCORE = 20.0


def _env(name: str, default: str) -> str:
    """运行时动态读环境变量(不模块级冻结)"""
    return os.environ.get(name, default)


# ============================================================
# P1 申诉状态机(误报 → 申诉 → 裁决)
# ============================================================

APPEAL_STATUS_PENDING = "pending"    # 待裁决
APPEAL_STATUS_APPROVED = "approved"  # 误报, 已恢复(信誉返还+解封)
APPEAL_STATUS_REJECTED = "rejected"  # 确认攻击, 归档

# 事件裁决口径(P2 学习真值: confirmed=AI拦对 / false_positive=AI拦错)
VERDICT_PENDING = "pending"
VERDICT_CONFIRMED = "confirmed"
VERDICT_FALSE_POSITIVE = "false_positive"

# 挑战通行证 TTL(验证通过后免挑战时长)
CHALLENGE_PASS_TTL = 900


def get_gateway_mode() -> str:
    """网关总开关: on(启用) / off(一键回退现状)"""
    return _env("SECURITY_GATEWAY_MODE", "on").lower()


def get_enforce_level() -> str:
    """灰度: observe(留痕) / shadow(模拟) / enforce(真处置)"""
    return _env("SECURITY_ENFORCE_LEVEL", "observe").lower()


# ============================================================
# 攻击特征库(P0 静态内置; P2 接 Redis 热更新)
# ============================================================

_SIGNATURE_PATTERNS = {
    "sqli": re.compile(
        r"(union\s+select|select\s+.+\s+from|drop\s+table|"
        r"insert\s+into|delete\s+from|update\s+.+\s+set|"
        r"'\s*(or|and)\s*\d+\s*=\s*\d*|--\s*$|1\s*=\s*1)",
        re.IGNORECASE),
    "xss": re.compile(
        r"(<script|javascript:|on(error|load|click|mouseover)\s*=|"
        r"<img[^>]+src\s*=\s*['\"]?\s*javascript)",
        re.IGNORECASE),
    "traversal": re.compile(r"(\.\./|\.\.\\|%2e%2e|/etc/passwd)",
                           re.IGNORECASE),
    "command_injection": re.compile(
        r"(;\s*(rm|cat|ls|wget|curl|nc|bash|sh)\s|\$\(|`[^`]+`)",
        re.IGNORECASE),
    "scanner_ua": re.compile(
        r"(sqlmap|nikto|nmap|masscan|zgrab|acunetix|nessus|"
        r"dirbuster|wpscan|hydra)",
        re.IGNORECASE),
}

# 探针路径(扫描器必踩)
_PROBE_PATH_PATTERNS = re.compile(
    r"(\.env|wp-admin|wp-login|phpmyadmin|admin\.php|\.git|\.svn|"
    r"config\.php|\.aws|\.ssh|backup\.sql|\.DS_Store)",
    re.IGNORECASE)

_SIGNATURE_PENALTY = 50.0  # 每命中一类特征扣 50(1类=可疑, 2类=确定性)


def scan_payload(text: str) -> float:
    """注入特征扫描 → 特征分(100=干净, 每类命中 -50)"""
    if not text:
        return 100.0
    hits = sum(1 for p in _SIGNATURE_PATTERNS.values()
               if p.search(text))
    if hits == 0:
        return 100.0
    return max(0.0, 100.0 - hits * _SIGNATURE_PENALTY)


def scan_path(path: str) -> float:
    """路径异常扫描 → 异常分(100=正常, 探针路径 0, 深度>8 层 40)"""
    if not path:
        return 100.0
    if _PROBE_PATH_PATTERNS.search(path):
        return 0.0
    if path.count("/") > 8:
        return 40.0
    return 100.0


def scan_identity(path: str, member_id: int) -> float:
    """身份风险: 未认证打敏感端点 → 降分"""
    if not member_id:
        for prefix in SENSITIVE_PREFIXES:
            if path.startswith(prefix):
                return IDENTITY_UNAUTH_SCORE
    return 100.0


class Security43Service:
    """43号安全管理业务服务"""

    # 态势频次系数缓存 (expiry, rate_factor)
    # 异步侧(posture observe/手动切换)刷新, 网关同步读缓存——
    # 避免 ASGI 请求路径上引入 await 之外的存储往返
    _POSTURE_CACHE: tuple = (0.0, 1.5)

    def __init__(self, repo: Security43Repository = Security43Repository()):
        self.repo = repo

    def _posture_factor(self) -> float:
        """当前态势频次系数(60s 进程内缓存; 冷启动默认 peace ×1.5)"""
        import time as _time
        expiry, factor = Security43Service._POSTURE_CACHE
        return factor if expiry > _time.time() else 1.5

    @classmethod
    def _refresh_posture_cache(cls, factor: float) -> None:
        """态势变化后刷新系数缓存(posture_service 异步侧调用)"""
        import time as _time
        cls._POSTURE_CACHE = (_time.time() + 60, factor)

    # --------------------------------------------------------
    # IP 信誉库(设计文档 §2.3)
    # --------------------------------------------------------

    async def ensure_reputation(self, ip: str) -> dict:
        """获取或冷启动创建信誉记录(中性分 80, 不因新面孔误杀)"""
        record = await self.repo.get_reputation(ip)
        if record is not None:
            return record
        record = {
            "ip": ip,
            "score": REPUTATION_COLD_START,
            "status": reputation_status(REPUTATION_COLD_START),
            "attackCount": 0,
            "requestCount": 0,
            "recoverCount": 0,
            "lastPenaltyAt": None,
            "pinned": False,
            "createdAt": ts(),
        }
        await self.repo.save_reputation(record)
        return record

    async def apply_penalty(self, ip: str, action: str) -> dict:
        """按处置档位扣信誉分(block -60 / challenge -30 / throttle -10)

        pinned(钉住)不受恢复影响, 但扣分仍生效。
        """
        record = await self.ensure_reputation(ip)
        penalty = PENALTY_MAP.get(action, 0.0)
        if penalty <= 0:
            return record
        record["score"] = max(0.0,
                              float(record.get("score") or 0) - penalty)
        record["status"] = reputation_status(record["score"])
        record["attackCount"] = int(record.get("attackCount") or 0) + 1
        record["lastPenaltyAt"] = datetime.now(UTC).timestamp()
        record["recoverCount"] = 0
        await self.repo.save_reputation(record)
        return record

    async def recover_reputation(self, ip: str) -> dict:
        """冷却恢复: 距上次扣分超过冷却期且攒够 N 次正常请求 → +1 分

        pinned 钉住不恢复; 恢复极慢(默认百次+1), 只防永久污点。
        """
        record = await self.ensure_reputation(ip)
        if record.get("pinned"):
            return record
        cooldown = float(_env("SECURITY_REPUTATION_COOLDOWN",
                              str(24 * 3600)))
        every = int(_env("SECURITY_RECOVER_EVERY", "100"))
        last = record.get("lastPenaltyAt") or 0
        now = datetime.now(UTC).timestamp()
        request_count = int(record.get("requestCount") or 0)
        if now - float(last) < cooldown:
            return record
        if request_count - (record.get("_recoveredAt") or 0) < every:
            if "_recoveredAt" in record:
                del record["_recoveredAt"]
            return record
        record["score"] = min(100.0,
                              float(record.get("score") or 0) + 1)
        record["status"] = reputation_status(record["score"])
        record["requestCount"] = request_count
        await self.repo.save_reputation(record)
        return record

    async def pin_reputation(self, ip: str, pinned: bool) -> dict:
        """管理端钉住/解钉(钉住=不受冷却恢复影响)"""
        record = await self.ensure_reputation(ip)
        record["pinned"] = bool(pinned)
        await self.repo.save_reputation(record)
        return record

    # --------------------------------------------------------
    # 封禁表(TTL 自动解封)
    # --------------------------------------------------------

    async def is_blocked(self, ip: str) -> bool:
        return await self.repo.get_block(ip) is not None

    async def block_ip(self, ip: str, reason: str = "",
                       event_id: int = None) -> dict:
        """封禁 IP(TTL 默认 900s, 到点自动解封)"""
        ttl = int(_env("SECURITY_BAN_TTL", "900"))
        record = {
            "ip": ip,
            "reason": reason or "威胁评分低于封禁线",
            "eventId": event_id,
            "expireAt": datetime.now(UTC).timestamp() + ttl,
            "createdAt": ts(),
        }
        await self.repo.save_block(record)
        logger.info("security_ip_blocked ip=%s ttl=%ss reason=%s",
                    ip, ttl, reason)
        return record

    async def unblock_ip(self, ip: str) -> bool:
        """手动解封(误报兜底)"""
        if await self.repo.get_block(ip) is None:
            return False
        await self.repo.remove_block(ip)
        return True

    # --------------------------------------------------------
    # 决策管线(设计文档 §2.1 安全网关核心)
    # --------------------------------------------------------

    async def process_request(
        self, ip: str, method: str = "GET", path: str = "/",
        query: str = "", body_text: str = "", ua: str = "",
        member_id: int = 0, hour: int = None,
    ) -> dict:
        """单请求安全决策(网关中间件每请求调用)

        Returns:
            {action, scoring, event, enforced, blocked}
            action 为实际处置动作(observe 恒 allow 放行, 事件记录
            wouldAction); 任何内部异常 fail-open 返回 allow。
        """
        try:
            return await self._do_process(
                ip, method, path, query, body_text, ua, member_id, hour)
        except Exception as exc:  # fail-open: 安全网关不能锁死网站
            logger.exception("security_gateway_error ip=%s: %s", ip, exc)
            return {"action": ACTION_ALLOW, "scoring": None,
                    "event": None, "enforced": False, "blocked": False}

    async def _do_process(self, ip, method, path, query, body_text,
                          ua, member_id, hour) -> dict:
        # ① 封禁表直查(blacklisted 直封口径, enforce 生效)
        enforce = get_enforce_level() == "enforce"
        if enforce and await self.is_blocked(ip):
            return {"action": ACTION_BLOCK, "scoring": None,
                    "event": None, "enforced": True, "blocked": True}

        # ①' 挑战通行证: 验证通过的 IP 在 TTL 内豁免挑战档
        #    (throttle/block 不豁免——通行证不是免死金牌)
        has_pass = await self.repo.has_challenge_pass(ip)

        # ② 特征预计算(query 需 URL 解码, 攻击载荷通常编码传输)
        reputation = await self.ensure_reputation(ip)
        query_decoded = unquote_plus(query or "")
        scan_text = " ".join(filter(
            None, [path, query_decoded, body_text, ua]))
        payload_score = scan_payload(scan_text)
        path_score = scan_path(path)
        identity_score = scan_identity(path, member_id)

        # ②' UEBA(P2): 网关顺带行为采集 + 四检测器偏离合议
        #     (零侵入: 计数直方图; 偏离注入 identity_risk 只降不升;
        #      冷启动无基线完全豁免; UEBA off 跳过)
        #     P3-1: D4 输入实时查询(24h 403/401 加权堆积)
        behavior_deviation = None
        try:
            from services.ueba_service import get_ueba_mode
            if member_id and get_ueba_mode() == "on":
                from services.ueba_service import UebaService
                ueba = UebaService()
                hour_now = (int(hour) if hour is not None
                            else datetime.now(UTC).hour)
                current_ops = await ueba.record_behavior(
                    member_id, path, hour=hour_now)
                forbidden_hits = await self.get_forbidden_hits(member_id)
                behavior_deviation = await ueba.compute_deviation(
                    member_id, path, hour=hour_now,
                    current_hour_ops=current_ops,
                    forbidden_hits=forbidden_hits)
                if behavior_deviation is not None:
                    identity_score = min(
                        identity_score,
                        float(behavior_deviation["score"]))
        except Exception as exc:  # UEBA 异常不阻断网关(fail-open)
            logger.warning("security_ueba_skip ip=%s: %s", ip, exc)

        # ③ 频次计数(IP 维度; 会员维度叠加取较差值)
        #    态势缩放(P2b): rate_limit × 当前态势系数
        #    (peace ×1.5 宽松 / alert ×1.0 / wartime ×0.3 收紧)
        window = int(_env("SECURITY_RATE_WINDOW", "60"))
        base_rate_limit = int(_env("SECURITY_RATE_LIMIT", "120"))
        rate_limit = int(base_rate_limit * self._posture_factor())
        ip_count = await self.repo.count_request(f"ip:{ip}", window)
        count = ip_count
        if member_id:
            member_count = await self.repo.count_request(
                f"member:{member_id}", window)
            count = max(ip_count, member_count)

        # ④ 黑名单直通 block(blacklisted 不再走评分)
        rep_status = reputation.get("status")
        if rep_status == REPUTATION_BLACKLISTED:
            scoring = None
            action = ACTION_BLOCK
        else:
            # ⑤ 第26档案评分
            ctx = {
                "ip": ip, "memberId": member_id,
                "reputation": float(reputation.get("score") or 0),
                "requestCount": count, "rateLimit": rate_limit,
                "payloadSignature": payload_score,
                "pathAnomaly": path_score,
                "identityRisk": identity_score,
            }
            if hour is not None:
                ctx["hour"] = int(hour)
            scoring = await SCORERS["security_threat_gate"].score(ctx)
            action = scoring["action"]

        # ⑤' 通行证豁免: 挑战档在通行证有效期内升级为放行
        #     (评分快照保留原貌; 豁免仍留痕——审计口径,
        #      通行证放行的挑战档事件 action 记 challenge_exempt)
        chall_exempt = False
        if action == ACTION_CHALLENGE and has_pass:
            chall_exempt = True
            action = ACTION_ALLOW

        # ⑤'' UEBA 行为预警(方案 §5.1): 偏离总分 <60 生成
        #      behavior_alert 事件, 复用 P1 裁决/申诉全链路;
        #      默认 pending 人工裁决, 不自动处置(防误报铁律)
        behavior_alert_event = None
        if behavior_deviation is not None and \
                float(behavior_deviation["score"]) < 60:
            behavior_alert_event = await self._record_event(
                ip, method, path, query, ua, member_id,
                "behavior_alert", None, enforced=False,
                factors=[{"name": d["code"], "label": "行为偏离",
                          "score": float(behavior_deviation["score"]),
                          "detail": d["detail"]}
                         for d in behavior_deviation["deviations"]])

        # ⑤''' 态势窗口观测(P2b): 可疑事件计数 + 窗口节拍升降级
        #      (异常不阻断请求; pinned/manual 只更新 EMA)
        try:
            await self._observe_posture_tick(action)
        except Exception as exc:
            logger.warning("security_posture_observe_skip: %s", exc)

        # ⑥ observe/shadow: 只留痕不处置(灰度铁律)
        enforced = enforce
        effective_action = action if enforce else ACTION_ALLOW

        # ⑦ 处置副作用(仅 enforce): 扣分 + 封禁
        event = None
        if chall_exempt:
            # 通行证豁免: 不处置只留痕(审计可见)
            event = await self._record_event(
                ip, method, path, query, ua, member_id,
                "challenge_exempt", scoring, enforced=False)
            # 豁免仍计正常流量(计数累计 + 冷却恢复)
            reputation["requestCount"] = \
                int(reputation.get("requestCount") or 0) + 1
            await self.repo.save_reputation(reputation)
        elif action != ACTION_ALLOW:
            if enforce:
                await self.apply_penalty(ip, action)
                if action == ACTION_BLOCK:
                    await self.block_ip(
                        ip, reason=f"威胁分过低", event_id=None)
            event = await self._record_event(
                ip, method, path, query, ua, member_id, action,
                scoring, enforced=enforce)
        else:
            # 正常请求: 计数累计 + 冷却恢复(懒触发)
            reputation["requestCount"] = \
                int(reputation.get("requestCount") or 0) + 1
            await self.repo.save_reputation(reputation)
            await self._try_recover(ip)

        return {"action": effective_action, "scoring": scoring,
                "event": event, "enforced": enforced,
                "blocked": action == ACTION_BLOCK and enforce,
                "behaviorAlert": behavior_alert_event}

    # --------------------------------------------------------
    # P3-1: 响应侧观测(D4 试探偏离——403/401 堆积实时统计)
    # --------------------------------------------------------

    async def observe_response(self, ip: str, member_id: int,
                              status_code: int | None) -> None:
        """网关放行后的响应钩子: 403/401 计入会员 24h 加权堆积

        由 SecurityGatewayMiddleware._finish 调用(fail-open,
        异常由调用方兜底)。仅统计带身份的请求(未认证 401 归
        member 0 不累积——网关层已由 identity_risk 因子覆盖)。
        """
        if status_code not in (401, 403):
            return
        weight = 1.0 if status_code == 403 else 0.5
        if not member_id:
            # 未认证请求打敏感端点: 已由 scan_identity 覆盖;
            # 此处仅记 IP 维度日志供观察
            logger.info("security_unauth_forbidden ip=%s status=%s",
                        ip, status_code)
            return
        total = await self.repo.count_forbidden(member_id,
                                                weight=weight)
        logger.info("security_forbidden_recorded ip=%s member=%s "
                    "status=%s total=%.1f", ip, member_id,
                    status_code, total)

    async def get_forbidden_hits(self, member_id: int) -> float:
        """查询会员 24h 加权 403/401 堆积数(D4 输入)"""
        if not member_id:
            return 0.0
        return await self.repo.get_forbidden(member_id)

    async def _try_recover(self, ip: str) -> None:
        """正常流量冷却恢复(fail-open: 恢复失败不影响请求)"""
        try:
            await self.recover_reputation(ip)
        except Exception as exc:
            logger.warning("security_recover_skip ip=%s: %s", ip, exc)

    # --------------------------------------------------------
    # P2b: 态势窗口观测(节拍内计数, 窗口满触发升降级评估)
    # --------------------------------------------------------

    _WINDOW_COUNT = 0
    _WINDOW_AT = 0.0

    async def _observe_posture_tick(self, action: str) -> None:
        """可疑动作计入当前窗口; 窗口满(POSTURE_WINDOW 秒)评估"""
        import time as _time
        if action not in (ACTION_CHALLENGE, ACTION_BLOCK,
                          "behavior_alert"):
            return
        now = _time.time()
        if now - Security43Service._WINDOW_AT >= \
                int(_env("SECURITY_POSTURE_WINDOW", "300")):
            # 新窗口: 用上一窗口累计触发评估, 然后重置
            count = Security43Service._WINDOW_COUNT
            Security43Service._WINDOW_COUNT = 1
            Security43Service._WINDOW_AT = now
            if count > 0:
                await self._evaluate_posture(count)
        else:
            Security43Service._WINDOW_COUNT += 1

    async def _evaluate_posture(self, count: int) -> None:
        """触发一次态势窗口评估(升降级 + 缓存刷新)"""
        from services.posture_service import (
            PostureService, POSTURE_RATE_FACTOR,
        )
        result = await PostureService().observe_window(count)
        Security43Service._refresh_posture_cache(
            POSTURE_RATE_FACTOR.get(result["posture"], 1.0))
        if result.get("changed"):
            logger.warning("security_posture_shifted posture=%s "
                           "ema=%s", result["posture"],
                           result["densityEma"])

    async def _record_event(self, ip, method, path, query, ua,
                            member_id, action, scoring,
                            enforced: bool,
                            factors: list = None) -> dict:
        """可疑请求入事件流水(正常放行不入, 防流水爆炸)

        factors 显式传入时覆盖评分快照(behavior_alert 等合成事件用)
        """
        event_id = await self.repo.next_id("event")
        event = {
            "eventId": event_id,
            "ip": ip,
            "memberId": int(member_id or 0),
            "method": method,
            "path": path[:200],
            "query": (query or "")[:200],
            "ua": (ua or "")[:200],
            "action": action,
            "score": (scoring or {}).get("score"),
            "factors": (factors if factors is not None
                        else (scoring or {}).get("factors") or []),
            "enforced": enforced,
            "verdict": "pending",   # P1 裁决: confirmed/false_positive
            "eventFed": False,      # P2 学习回流幂等标记
            "createdAt": ts(),
        }
        await self.repo.save_event(event)
        logger.info("security_event_recorded id=%s ip=%s action=%s "
                    "score=%s enforced=%s", event_id, ip, action,
                    event["score"], enforced)
        return event

    # --------------------------------------------------------
    # 查询(P0 观察/测试用, P1 管理端点)
    # --------------------------------------------------------

    async def list_events(self, action: str = None,
                          limit: int = 200) -> list[dict]:
        return await self.repo.list_events(action=action, limit=limit)

    async def list_blocks(self) -> list[dict]:
        return await self.repo.list_blocks()

    async def list_reputations(self) -> list[dict]:
        return await self.repo.list_reputations()

    async def stats(self) -> dict:
        """管理端态势总览(事件按档位/裁决分布 + 误报率)"""
        events = await self.repo.list_events(limit=1000)
        by_action = {}
        for e in events:
            a = e.get("action") or "unknown"
            by_action[a] = by_action.get(a, 0) + 1
        decided = [e for e in events
                   if e.get("verdict") not in (VERDICT_PENDING, None)]
        false_pos = sum(1 for e in decided
                        if e.get("verdict") == VERDICT_FALSE_POSITIVE)
        appeals = await self.repo.list_appeals(limit=1000)
        return {
            "success": True,
            "gatewayMode": get_gateway_mode(),
            "enforceLevel": get_enforce_level(),
            "events": {
                "total": len(events),
                "byAction": by_action,
                "pending": sum(1 for e in events
                               if e.get("verdict") == VERDICT_PENDING),
                "confirmed": sum(1 for e in decided
                                  if e.get("verdict")
                                  == VERDICT_CONFIRMED),
                "falsePositive": false_pos,
                # 误报率(对齐42号 falsePositiveRate 口径, P2 学习真值源)
                "falsePositiveRate": (
                    round(false_pos / len(decided), 4)
                    if decided else 0.0),
            },
            "appeals": {
                "total": len(appeals),
                "pending": sum(1 for a in appeals
                               if a.get("status")
                               == APPEAL_STATUS_PENDING),
            },
            "blocks": len(await self.repo.list_blocks()),
            "reputations": len(await self.repo.list_reputations()),
        }

    # --------------------------------------------------------
    # P1: 挑战验证(mock 应答, 一次通过 → TTL 通行证)
    # --------------------------------------------------------

    async def verify_challenge(self, ip: str, token: str = "",
                               answer: str = "") -> dict:
        """挑战应答验证(mock: 应答非空即通过)

        通过 → 颁发 IP 通行证(TTL 900s, 挑战档豁免);
        mock 口径不含真实验证码(P3 极验/hCaptcha 通道预留)。

        Raises:
            ValueError: 应答为空(未完成验证)
        """
        if not str(answer or "").strip():
            raise ValueError("验证失败: 应答为空, 请完成安全验证")
        await self.repo.grant_challenge_pass(
            ip, ttl=CHALLENGE_PASS_TTL)
        # 验证通过留痕(事件流水, 供观察挑战漏斗)
        event_id = await self.repo.next_id("event")
        event = {
            "eventId": event_id,
            "ip": ip, "memberId": 0, "method": "POST",
            "path": "/api/security/challenge/verify",
            "query": "", "ua": "",
            "action": "verify_pass",
            "score": None, "factors": [],
            "enforced": get_enforce_level() == "enforce",
            "verdict": VERDICT_CONFIRMED,  # 通过验证=真人信号
            "eventFed": False,
            "createdAt": ts(),
        }
        await self.repo.save_event(event)
        logger.info("security_challenge_verified ip=%s token=%s",
                    ip, (token or "")[:32])
        return {"success": True, "ip": ip,
                "passTtl": CHALLENGE_PASS_TTL}

    # --------------------------------------------------------
    # P1: 误报申诉(会员端 → 管理端裁决, 42号申诉范式平移)
    # --------------------------------------------------------

    async def submit_appeal(self, member_id: int, event_id: int,
                           reason: str = "") -> dict:
        """会员对 challenge/block 事件提交误报申诉

        Raises:
            KeyError: 事件不存在
            ValueError: 非本会员事件 / 事件无可申诉处置 / 已有申诉
        """
        event = await self.repo.get_event(int(event_id))
        if event is None:
            raise KeyError(f"安全事件 {event_id} 不存在")
        if int(event.get("memberId") or 0) != int(member_id):
            raise ValueError("仅事件当事人可申诉")
        if event.get("action") not in (ACTION_CHALLENGE, ACTION_BLOCK,
                                        "challenge_exempt"):
            raise ValueError(f"事件处置档位 {event.get('action')}, "
                             "仅挑战/封禁事件可申诉")
        existing = await self.repo.get_appeal_by_event(int(event_id))
        if existing is not None:
            raise ValueError(f"该事件已有申诉(appealId="
                             f"{existing.get('appealId')}, 状态 "
                             f"{existing.get('status')})")

        appeal_id = await self.repo.next_id("appeal")
        appeal = {
            "appealId": appeal_id,
            "eventId": int(event_id),
            "memberId": int(member_id),
            "ip": event.get("ip"),
            "reason": str(reason or "").strip()
                     or "对安全处置有异议",
            "status": APPEAL_STATUS_PENDING,
            "reviewer": "",
            "reviewNote": "",
            "createdAt": ts(),
            "decidedAt": None,
        }
        await self.repo.save_appeal(appeal)
        logger.info("security_appeal_submitted appeal=%s event=%s "
                    "member=%s", appeal_id, event_id, member_id)
        return {"success": True, "appeal": appeal}

    async def decide_appeal(self, appeal_id: int, approve: bool,
                           reviewer: str = "admin",
                           note: str = "") -> dict:
        """管理员裁决申诉

        approve=True(误报): 事件置 false_positive + IP 信誉返还扣分
            + 解除封禁;
        approve=False(确认攻击): 事件置 confirmed 归档。

        Raises:
            KeyError: 申诉不存在
            ValueError: 已裁决
        """
        appeal = await self.repo.get_appeal(int(appeal_id))
        if appeal is None:
            raise KeyError(f"申诉 {appeal_id} 不存在")
        if appeal.get("status") != APPEAL_STATUS_PENDING:
            raise ValueError(f"申诉状态 {appeal.get('status')}, "
                             "仅待裁决申诉可处理")

        appeal["status"] = (APPEAL_STATUS_APPROVED if approve
                            else APPEAL_STATUS_REJECTED)
        appeal["reviewer"] = reviewer
        appeal["reviewNote"] = note
        appeal["decidedAt"] = ts()
        await self.repo.save_appeal(appeal)

        event = await self.repo.get_event(
            int(appeal.get("eventId") or 0))
        if event is not None:
            event["verdict"] = (VERDICT_FALSE_POSITIVE if approve
                                else VERDICT_CONFIRMED)
            await self.repo.save_event(event)
            if approve:
                await self._restore_victim(event)

        logger.info("security_appeal_decided appeal=%s approve=%s "
                    "reviewer=%s", appeal_id, approve, reviewer)
        return {"success": True, "appeal": appeal}

    async def _restore_victim(self, event: dict) -> None:
        """误报恢复: 返还该事件处置对应的信誉扣分 + 解封"""
        ip = event.get("ip") or ""
        if not ip:
            return
        penalty = PENALTY_MAP.get(event.get("action") or "", 0.0)
        if penalty > 0:
            record = await self.ensure_reputation(ip)
            record["score"] = min(100.0,
                                  float(record.get("score") or 0)
                                  + penalty)
            record["status"] = reputation_status(record["score"])
            record["attackCount"] = max(
                0, int(record.get("attackCount") or 0) - 1)
            await self.repo.save_reputation(record)
        await self.unblock_ip(ip)

    # --------------------------------------------------------
    # P1: 事件直接裁决(管理端, 不经申诉)
    # --------------------------------------------------------

    async def decide_event(self, event_id: int, confirm: bool,
                           reviewer: str = "admin",
                           note: str = "") -> dict:
        """事件裁决: 确认攻击/误报(P2 学习真值)

        confirm=False(误报)时同步恢复 IP 信誉与封禁。

        Raises:
            KeyError: 事件不存在
            ValueError: 已裁决
        """
        event = await self.repo.get_event(int(event_id))
        if event is None:
            raise KeyError(f"安全事件 {event_id} 不存在")
        if event.get("verdict") != VERDICT_PENDING:
            raise ValueError(f"事件已裁决({event.get('verdict')})")

        event["verdict"] = (VERDICT_CONFIRMED if confirm
                            else VERDICT_FALSE_POSITIVE)
        event["reviewer"] = reviewer
        event["reviewNote"] = note
        await self.repo.save_event(event)
        if not confirm:
            await self._restore_victim(event)
        logger.info("security_event_decided event=%s confirm=%s "
                    "reviewer=%s", event_id, confirm, reviewer)
        return {"success": True, "event": event}

    # --------------------------------------------------------
    # P1: 会员端状态
    # --------------------------------------------------------

    async def my_status(self, member_id: int, ip: str = "") -> dict:
        """我的安全状态(当前 IP 信誉/封禁/通行证/我的事件)"""
        reputation = (await self.ensure_reputation(ip)
                      if ip else None)
        events = await self.repo.list_events(limit=500)
        mine = [e for e in events
                if int(e.get("memberId") or 0) == int(member_id)]
        appeals = await self.repo.list_appeals(
            member_id=int(member_id), limit=100)
        return {
            "success": True,
            "memberId": int(member_id),
            "ip": ip,
            "reputation": ({k: reputation[k] for k in
                            ("score", "status", "pinned")}
                           if reputation else None),
            "blocked": (await self.is_blocked(ip)
                        if ip else False),
            "challengePass": (await self.repo.has_challenge_pass(ip)
                              if ip else False),
            "myEvents": {
                "total": len(mine),
                "pending": sum(1 for e in mine
                               if e.get("verdict")
                               == VERDICT_PENDING),
            },
            "myAppeals": {
                "total": len(appeals),
                "pending": sum(1 for a in appeals
                               if a.get("status")
                               == APPEAL_STATUS_PENDING),
            },
        }

    # --------------------------------------------------------
    # P1: 管理端 IP 处置
    # --------------------------------------------------------

    async def admin_ban_ip(self, ip: str, reason: str = "",
                           ttl: int = None) -> dict:
        """手动封禁 IP(管理端, 默认走 SECURITY_BAN_TTL)"""
        block = await self.block_ip(ip, reason=reason or "管理员手动封禁")
        return {"success": True, "block": block}

    async def admin_unban_ip(self, ip: str) -> dict:
        """手动解封(误报兜底)"""
        ok = await self.unblock_ip(ip)
        if not ok:
            raise KeyError(f"IP {ip} 未在封禁表中")
        return {"success": True, "ip": ip}

    # --------------------------------------------------------
    # P2b: 学习回流(事件裁决真值 → 第26档案, 42号P2范式平移)
    # --------------------------------------------------------

    async def collect_event_feedback(self) -> dict:
        """批量回流: 已裁决且未回流的事件 → 决策正确性反馈

        真值口径: confirmed=AI拦对(正反馈) /
                  false_positive=AI拦错(负反馈)。
        单条失败不阻断批量; eventFed 幂等标记。
        """
        from services.ai_learning_service import submit_feedback

        events = await self.repo.list_events(limit=1000)
        submitted, skipped, results = 0, 0, []
        for event in events:
            if event.get("eventFed"):
                skipped += 1
                continue
            verdict = event.get("verdict")
            if verdict == VERDICT_PENDING:
                skipped += 1
                continue
            factors = event.get("factors") or []
            if not factors:
                skipped += 1
                continue
            # verify_pass(真人信号)与 behavior_alert 无完整六因子
            # 快照, 仅 threat_gate 评分事件回流
            action = event.get("action")
            if action in ("verify_pass", "challenge_exempt",
                          "behavior_alert"):
                skipped += 1
                continue
            correct = verdict == VERDICT_CONFIRMED
            expected = action if correct else \
                ("allow" if action != "allow" else "challenge")
            try:
                result = await submit_feedback({
                    "scorerId": "security_threat_gate",
                    "factors": factors,
                    "scoreAtDecision": float(
                        event.get("score") or 0),
                    "actualAction": action,
                    "expectedAction": expected,
                    "correct": correct,
                    "reward": 0.5 if correct else -0.5,
                    "note": f"eventId={event.get('eventId')} "
                            f"verdict={verdict}",
                    "source": "security43",
                })
                event["eventFed"] = True
                await self.repo.save_event(event)
                results.append(result)
                submitted += 1
            except (KeyError, ValueError) as exc:
                skipped += 1
                logger.warning("security_event_feed_skip event=%s: %s",
                               event.get("eventId"), exc)
        return {"submitted": submitted, "skipped": skipped,
                "results": results}

    async def run_learning(self) -> dict:
        """触发第26档案一轮 Hedge 学习(反馈不足抛 ValueError)"""
        from services.ai_learning_service import run_learning_cycle
        return await run_learning_cycle("security_threat_gate")

    async def learning_status(self) -> dict:
        """第26档案学习状态(裁决事件计数/权重视图)"""
        from services.ai_learning_service import (
            SCORER_REGISTRY, get_weights_view,
        )
        events = await self.repo.list_events(limit=2000)
        decided = [e for e in events
                   if e.get("verdict")
                   not in (VERDICT_PENDING, None)]
        return {
            "success": True,
            "scorer": "security_threat_gate",
            "registry": SCORER_REGISTRY.get("security_threat_gate"),
            "events": {
                "total": len(events),
                "decided": len(decided),
                "fed": sum(1 for e in events if e.get("eventFed")),
                "confirmed": sum(1 for e in decided
                                 if e.get("verdict")
                                 == VERDICT_CONFIRMED),
                "falsePositive": sum(
                    1 for e in decided
                    if e.get("verdict") == VERDICT_FALSE_POSITIVE),
            },
            "weights": await get_weights_view(
                "security_threat_gate"),
        }
