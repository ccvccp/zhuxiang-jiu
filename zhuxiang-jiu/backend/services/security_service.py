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

# 网关快道白名单(健康检查永久放行, Docker healthcheck 依赖)
GATEWAY_WHITELIST = (
    "/api/decision/health",
    "/api/monitor/health",
    "/api/maintenance/health",
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

    def __init__(self, repo: Security43Repository = Security43Repository()):
        self.repo = repo

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

        # ② 特征预计算(query 需 URL 解码, 攻击载荷通常编码传输)
        reputation = await self.ensure_reputation(ip)
        query_decoded = unquote_plus(query or "")
        scan_text = " ".join(filter(
            None, [path, query_decoded, body_text, ua]))
        payload_score = scan_payload(scan_text)
        path_score = scan_path(path)
        identity_score = scan_identity(path, member_id)

        # ③ 频次计数(IP 维度; 会员维度叠加取较差值)
        window = int(_env("SECURITY_RATE_WINDOW", "60"))
        rate_limit = int(_env("SECURITY_RATE_LIMIT", "120"))
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

        # ⑥ observe/shadow: 只留痕不处置(灰度铁律)
        enforced = enforce
        effective_action = action if enforce else ACTION_ALLOW

        # ⑦ 处置副作用(仅 enforce): 扣分 + 封禁
        event = None
        if action != ACTION_ALLOW:
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
                "blocked": action == ACTION_BLOCK and enforce}

    async def _try_recover(self, ip: str) -> None:
        """正常流量冷却恢复(fail-open: 恢复失败不影响请求)"""
        try:
            await self.recover_reputation(ip)
        except Exception as exc:
            logger.warning("security_recover_skip ip=%s: %s", ip, exc)

    async def _record_event(self, ip, method, path, query, ua,
                            member_id, action, scoring,
                            enforced: bool) -> dict:
        """可疑请求入事件流水(正常放行不入, 防流水爆炸)"""
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
            "factors": (scoring or {}).get("factors") or [],
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
        """P0 观察统计(事件按档位/是否已执行分布)"""
        events = await self.repo.list_events(limit=1000)
        by_action = {}
        for e in events:
            a = e.get("action") or "unknown"
            by_action[a] = by_action.get(a, 0) + 1
        return {
            "success": True,
            "gatewayMode": get_gateway_mode(),
            "enforceLevel": get_enforce_level(),
            "events": {
                "total": len(events),
                "byAction": by_action,
                "pending": sum(1 for e in events
                               if e.get("verdict") == "pending"),
            },
            "blocks": len(await self.repo.list_blocks()),
            "reputations": len(await self.repo.list_reputations()),
        }
