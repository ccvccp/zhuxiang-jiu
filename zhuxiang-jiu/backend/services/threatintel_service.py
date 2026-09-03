"""43号·P4-3 威胁情报接入服务(Firehol netset)

计划 §四(docs/43号P4_运营成熟化实施计划.md):
    - 外源: Firehol IPList(GitHub 开源, CC0 许可,
      firehol_level1.netset 约 2k 段已知恶意段)
    - 导入: POST /admin/threatintel/import(netset 文本或
      文件内容 → CIDR 批量入 security43 威胁情报表)
    - 信誉联动: 网关 ensure_reputation 后置检查——命中情报段
      → 信誉降档 suspicious(≤30), 不直封(防情报误伤共享出口)
    - 防误杀: 命中只降档 + 申诉通道兜底 + 管理端单 IP pin 加白

netset 格式(Firehol 标准):
    # 注释行
    1.2.3.4
    5.6.7.0/24
"""

import logging
from datetime import datetime, UTC

from core.helpers import ts
from repositories.security_repository import (
    Security43Repository,
)

logger = logging.getLogger(__name__)

# 导入段数上限(防误传大文件撑爆存储)
MAX_IMPORT_CIDRS = 20000
# 命中降档后的信誉值(31: suspicious 区间 30<x≤60——
# 30 会落入 blacklisted(≤30)触发直封, 与"不直封"设计矛盾)
THREATINTEL_REPUTATION_CAP = 31.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ThreatIntelService:
    """威胁情报服务(43号 P4-3)"""

    def __init__(self, repo: Security43Repository
                 = Security43Repository()):
        self.repo = repo

    # ========================================================
    # netset 解析
    # ========================================================

    @staticmethod
    def parse_netset(content: str) -> list[str]:
        """解析 netset 文本 → CIDR 列表(去注释/去重/校验格式)

        Raises:
            ValueError: 段数超上限 / 全部行非法
        """
        import ipaddress
        seen = set()
        invalid = 0
        for line in (content or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                # 规范化: 1.2.3.4 → 1.2.3.4/32
                network = ipaddress.ip_network(
                    line, strict=False)
                seen.add(str(network))
            except ValueError:
                invalid += 1
        cidrs = sorted(seen)
        if len(cidrs) > MAX_IMPORT_CIDRS:
            raise ValueError(
                f"导入段数 {len(cidrs)} 超上限 {MAX_IMPORT_CIDRS}")
        if not cidrs:
            raise ValueError("netset 内容无有效 CIDR 段")
        return cidrs

    # ========================================================
    # 导入
    # ========================================================

    async def import_netset(self, content: str,
                            source: str = "firehol_level1",
                            replace: bool = True) -> dict:
        """导入 netset 内容(默认全量替换, 幂等可重复)

        Args:
            content: netset 文本(IP/CIDR 逐行)
            source: 情报源标识(导入元信息留痕)
            replace: True 先清空旧段(全量导入口径)

        Returns:
            {imported, skipped(替换清除), source}
        """
        cidrs = self.parse_netset(content)
        cleared = 0
        if replace:
            cleared = await self.repo.clear_threatintel()
        meta = {"source": source, "importedAt": _now_iso()}
        for cidr in cidrs:
            # actorKey 冗余入记录体(_list 读取与过滤依据)
            await self.repo.save_threatintel(
                cidr, {"actorKey": f"threatintel:{cidr}", **meta})
        logger.info("threatintel_imported source=%s count=%s "
                    "cleared=%s", source, len(cidrs), cleared)
        return {"success": True, "imported": len(cidrs),
                "cleared": cleared, "source": source}

    # ========================================================
    # 命中查询(网关信誉联动)
    # ========================================================

    async def check_ip(self, ip: str) -> dict | None:
        """IP 命中情报段查询(命中返回段信息, 未命中 None)"""
        return await self.repo.match_threatintel(ip)

    # ========================================================
    # 统计
    # ========================================================

    async def stats(self) -> dict:
        """情报表统计(段数/来源分布/最近导入/自动订阅状态)"""
        records = await self.repo.list_threatintel()
        sources = {}
        latest = None
        for r in records:
            src = r.get("source") or "unknown"
            sources[src] = sources.get(src, 0) + 1
            imported = r.get("importedAt")
            if imported and (latest is None or imported > latest):
                latest = imported

        # P5-3: 自动订阅状态实况(enabled/最近拉取/失败计数/
        # degraded——连续失败 ≥3 次外显, 可接 P5-2 告警)
        try:
            from services.threatintel_feed import feed_enabled
            auto_state = (
                await self.repo.get_threatintel_auto_state()) or {}
            failures = int(auto_state.get("consecutiveFailures")
                           or 0)
            auto = {
                "enabled": feed_enabled(),
                "lastAutoImportAt":
                    auto_state.get("lastAutoImportAt") or None,
                "lastAutoStatus":
                    auto_state.get("lastAutoStatus") or None,
                "consecutiveFailures": failures,
                "degraded": failures >= 3,
            }
        except Exception:
            auto = {"enabled": False, "degraded": False,
                    "consecutiveFailures": 0}
        return {
            "success": True,
            "totalCidrs": len(records),
            "sources": sources,
            "lastImportedAt": latest,
            "auto": auto,
        }

    # ========================================================
    # 信誉联动(网关 ensure_reputation 后调用)
    # ========================================================

    async def apply_to_reputation(self, ip: str,
                                  reputation: dict) -> dict:
        """命中情报段 → 信誉降档(不直封, 只降到 cap)

        由 Security43Service._do_process ② 处调用;
        未命中原样返回(零影响)。
        """
        hit = await self.check_ip(ip)
        if hit is None:
            return reputation
        current = float(reputation.get("score") or 0)
        if current > THREATINTEL_REPUTATION_CAP:
            reputation["score"] = THREATINTEL_REPUTATION_CAP
            from repositories.security_repository import (
                reputation_status,
            )
            reputation["status"] = reputation_status(
                reputation["score"])
            # 降档事件留痕(审计可见, 可申诉)
            event_id = await self.repo.next_id("event")
            event = {
                "eventId": event_id,
                "ip": ip, "memberId": 0, "method": "GET",
                "path": "(threatintel)", "query": "", "ua": "",
                "action": "threatintel_hit",
                "score": reputation["score"],
                "factors": [{"name": "threatintel",
                             "label": "威胁情报命中",
                             "score": reputation["score"],
                             "detail": f"段{hit.get('cidr')}"
                                       f"({hit.get('source')})"}],
                "enforced": False,
                "verdict": "pending",
                "eventFed": False,
                "createdAt": ts(),
            }
            await self.repo.save_event(event)
            await self.repo.save_reputation(reputation)
            logger.info("security_threatintel_hit ip=%s cidr=%s "
                        "score→%s", ip, hit.get("cidr"),
                        reputation["score"])
        return reputation
