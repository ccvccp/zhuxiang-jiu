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
import os
from datetime import datetime, UTC

from core.helpers import ts
from repositories.security_repository import (
    Security43Repository,
)

logger = logging.getLogger(__name__)

# 导入段数上限默认(P6-1 环境变量化: SECURITY_THREATINTEL_
# MAX_CIDRS 可调, 聚合全量 ~180k 段时提额)
MAX_IMPORT_CIDRS = 20000
# 命中降档后的信誉值(31: suspicious 区间 30<x≤60——
# 30 会落入 blacklisted(≤30)触发直封, 与"不直封"设计矛盾)
THREATINTEL_REPUTATION_CAP = 31.0


def max_import_cidrs() -> int:
    """单次导入段数上限(P6-1 环境变量, 默认 20000 兼容)"""
    try:
        return max(1000, int(os.environ.get(
            "SECURITY_THREATINTEL_MAX_CIDRS",
            str(MAX_IMPORT_CIDRS))))
    except ValueError:
        return MAX_IMPORT_CIDRS


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
        limit = max_import_cidrs()
        if len(cidrs) > limit:
            raise ValueError(
                f"导入段数 {len(cidrs)} 超上限 {limit}")
        if not cidrs:
            raise ValueError("netset 内容无有效 CIDR 段")
        return cidrs

    # ========================================================
    # 导入
    # ========================================================

    async def import_netset(self, content: str,
                            source: str = "firehol_level1",
                            replace: bool = True) -> dict:
        """导入 netset 内容(幂等可重复, P6-1 按源替换+批量写)

        Args:
            content: netset 文本(IP/CIDR 逐行)
            source: 情报源标识(导入元信息留痕+按源替换键)
            replace: True 先清**同源**旧段(P6-1 聚合口径——
                导入 level2 只清 level2, 多源互不干扰;
                单源默认 firehol_level1 行为不变)

        Returns:
            {imported, skipped(替换清除), source}
        """
        cidrs = self.parse_netset(content)
        cleared = 0
        if replace:
            cleared = await self.repo.clear_threatintel(
                source=source)
        meta = {"source": source, "importedAt": _now_iso()}
        entries = [
            (cidr, {"actorKey": f"threatintel:{cidr}", **meta})
            for cidr in cidrs]
        # P6-1: pipeline 批量写(180k 段拆 round-trip;
        # 源计数器由仓储层同点维护——clear 负抵扣 +
        # save 正增量, 旁路调用不漂移)
        await self.repo.save_many_threatintel(entries)
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
    # 统计(P6-1 计数器化——查询零 list, 180k 段毫秒级)
    # 计数器写入由仓储层维护(save_many 正增量 /
    # clear 负抵扣·全清重置), 本层只读 + 兜底重建
    # ========================================================

    async def _load_source_counts(self,
                                  rebuild: bool = False
                                  ) -> dict | None:
        """读源计数器(rebuild=True 触发全量重建)

        Returns:
            {source: count} 或 None(计数器缺失且未重建)
        """
        from repositories.backend import (
            is_redis_mode, get_redis_client,
            get_in_memory_store, _k,
        )
        try:
            if is_redis_mode():
                client = await get_redis_client()
                key = _k("security43", "threatintel",
                         "srcstats")
                if rebuild:
                    await client.delete(key)
                raw = await client.hgetall(key)
                if not raw:
                    return None
                return {k: int(v) for k, v in raw.items()}
            store = get_in_memory_store()
            if rebuild:
                store.pop("_security43_srcstats", None)
            bucket = store.get("_security43_srcstats")
            if not bucket:
                return None
            return {k: int(v) for k, v in bucket.items()}
        except Exception as exc:
            logger.warning("srcstats_load_skip: %s", exc)
            return None

    async def _rebuild_source_counts(self) -> dict:
        """全量重建源计数器(兜底: 计数器缺失/漂移时)"""
        records = await self.repo.list_threatintel()
        counts = {}
        latest = None
        for r in records:
            src = r.get("source") or "unknown"
            counts[src] = counts.get(src, 0) + 1
            imported = r.get("importedAt")
            if imported and (latest is None
                             or imported > latest):
                latest = imported
        # 写回计数器
        from repositories.backend import (
            is_redis_mode, get_redis_client,
            get_in_memory_store, _k,
        )
        try:
            if is_redis_mode():
                client = await get_redis_client()
                key = _k("security43", "threatintel",
                         "srcstats")
                await client.delete(key)
                if counts:
                    await client.hset(key, mapping=counts)
            else:
                store = get_in_memory_store()
                store["_security43_srcstats"] = counts
        except Exception as exc:
            logger.warning("srcstats_rebuild_write_skip: %s",
                           exc)
        return {"counts": counts, "latest": latest}

    async def stats(self) -> dict:
        """情报表统计(段数/来源分布/最近导入/订阅状态/匹配策略)

        P6-1 计数器化: sources/totalCidrs 优先读计数器
        (毫秒级); 计数器缺失 → 全量重建一次(兜底口径);
        计数器在位但合计 ≠ 区间缓存段数(漂移) → 重建一次。
        """
        counts = await self._load_source_counts()
        latest = None
        if counts is None:
            # 兜底: 计数器缺失(旧数据/旁路删键)→ 全量重建
            # (重建内部已写回, 下次调用毫秒级)
            rebuilt = await self._rebuild_source_counts()
            counts = rebuilt["counts"]
            latest = rebuilt["latest"]
        else:
            # 交叉校验: 计数器合计 vs 区间缓存段数(计划 §三③)
            # ——缓存版本戳一致才可比(未建/过期跳过防误重建)
            cached = self.repo.threatintel_cached_segments()
            if cached is not None and \
                    sum(counts.values()) != cached:
                rebuilt = await self._rebuild_source_counts()
                counts = rebuilt["counts"]
                latest = rebuilt["latest"]
        sources = dict(counts or {})
        total = sum(sources.values())

        # P5-3: 自动订阅状态实况(enabled/最近拉取/失败计数/
        # degraded——连续失败 ≥3 次外显, 可接 P5-2 告警)
        # P6-1: sources 每源状态(degradedSources 聚合口径)
        try:
            from services.threatintel_feed import (
                feed_enabled, degraded_sources,
            )
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
            try:
                agg = await degraded_sources()
                auto["sources"] = agg["states"]
                auto["degradedSources"] = agg[
                    "degradedSources"]
            except Exception:
                pass
        except Exception:
            auto = {"enabled": False, "degraded": False,
                    "consecutiveFailures": 0}

        # P5-6: 匹配策略实况(linear/bisect + 段数)——
        # 聚合多源时可直接确认二分已生效
        try:
            match = self.repo.threatintel_match_mode()
        except Exception:
            match = {"mode": "linear", "segments": 0}
        return {
            "success": True,
            "totalCidrs": total,
            "sources": sources,
            "lastImportedAt": latest,
            "auto": auto,
            "matchMode": match.get("mode"),
            "matchSegments": match.get("segments"),
        }

    # ========================================================
    # 信誉联动(网关 ensure_reputation 后调用)
    # ========================================================

    async def apply_to_reputation(self, ip: str,
                                  reputation: dict) -> dict:
        """命中情报段 → 信誉降档(不直封, 只降到 cap)

        由 Security43Service._do_process ② 处调用;
        未命中原样返回(零影响)。

        P5-4 两级串联:
            第一级 Firehol 段命中(免费) → 降档 31 + 留痕 → 返回
            第二级 AbuseIPDB 实时置信度(配额) → 三级强度递进:
                ≥75 降档 31(同口径不直封) / 25-75 轻扣 -10 /
                <25 零影响
        """
        hit = await self.check_ip(ip)
        if hit is not None:
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
                logger.info(
                    "security_threatintel_hit ip=%s cidr=%s "
                    "score→%s", ip, hit.get("cidr"),
                    reputation["score"])
            return reputation   # 第一级出口(不再花配额)

        # ---- P5-4 第二级: Firehol 未命中 → AbuseIPDB 实时置信度
        # (异常不阻断网关 fail-soft; score=None 零影响)
        # 仅 real/mock_fallback 态联动——mock 态是客户端测试口径,
        # 确定性分数不参与信誉联动(未配置零影响)
        try:
            from services.abuseipdb_client import (
                abuseipdb_mode, check_ip as ab_check,
            )
            if abuseipdb_mode() == "mock":
                return reputation
            r = await ab_check(ip)
            score = r.get("score")
            if score is None:
                return reputation
            current = float(reputation.get("score") or 0)
            if score >= 75:
                # 高置信恶意 → 降档 31(同 Firehol 口径, 不直封)
                if current > THREATINTEL_REPUTATION_CAP:
                    reputation["score"] = \
                        THREATINTEL_REPUTATION_CAP
                    from repositories.security_repository import (
                        reputation_status,
                    )
                    reputation["status"] = reputation_status(
                        reputation["score"])
                    await self._record_abuseipdb_event(
                        ip, reputation, score, r.get("source"),
                        tier="hit")
                    await self.repo.save_reputation(reputation)
                    logger.info(
                        "security_abuseipdb_hit ip=%s score=%s "
                        "source=%s rep→%s", ip, score,
                        r.get("source"), reputation["score"])
            elif score >= 25:
                # 中置信 → 轻度扣分 -10(下限 31 防误杀过深;
                # 已扣过(≤70)不重复扣——与 Firehol
                # "已降档不重复降"同口径)
                if current > 70:
                    reputation["score"] = max(
                        THREATINTEL_REPUTATION_CAP, current - 10)
                    from repositories.security_repository import (
                        reputation_status,
                    )
                    reputation["status"] = reputation_status(
                        reputation["score"])
                    await self._record_abuseipdb_event(
                        ip, reputation, score, r.get("source"),
                        tier="low")
                    await self.repo.save_reputation(reputation)
                    logger.info(
                        "security_abuseipdb_low ip=%s score=%s "
                        "rep→%s", ip, score, reputation["score"])
            # score < 25 → 零影响
        except Exception as exc:
            logger.warning("security_abuseipdb_skip ip=%s: %s",
                           ip, exc)
        return reputation

    async def _record_abuseipdb_event(self, ip: str,
                                      reputation: dict, score: int,
                                      source: str,
                                      tier: str) -> None:
        """AbuseIPDB 联动留痕(tier=hit 降档 / low 轻扣)

        复用 threatintel_hit 事件口径(裁决/申诉全链路),
        因子名单列区分来源。
        """
        event_id = await self.repo.next_id("event")
        label = ("AbuseIPDB 高置信命中" if tier == "hit"
                 else "AbuseIPDB 中置信轻扣")
        event = {
            "eventId": event_id,
            "ip": ip, "memberId": 0, "method": "GET",
            "path": "(threatintel)", "query": "", "ua": "",
            "action": "threatintel_hit",
            "score": reputation["score"],
            "factors": [{
                "name": f"abuseipdb{'_low' if tier == 'low' else ''}",
                "label": label,
                "score": float(score),
                "detail": f"置信度{score}(实时, source="
                          f"{source or 'mock'})"}],
            "enforced": False,
            "verdict": "pending",
            "eventFed": False,
            "createdAt": ts(),
        }
        await self.repo.save_event(event)
