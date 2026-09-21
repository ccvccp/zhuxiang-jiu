"""小竹·语音数据周报服务(P4)

链路: 近 7 天窗口聚合(scan_sessions/scan_turns/asr_fixes/
learn_queue 全表只读) → 环比(14 天扫描分两段, 上期不依赖历史
快照) → 快照存 Redis(dashboard 展示+当日幂等) → 管理员站内信
(MessageService 逐一发送, 单人失败不阻断)。

触发: 调度器每小时检查(周一 08:00 后且当日未生成)自动跑;
dashboard 端点 force 即时生成(不站内信防轰炸)。
统计红线: 只读聚合不写业务表; 站内信 category=system
priority=p2(运营观察, 非紧急)。
"""

import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger("xiaozhu_weekly")

# 站内信触达上限(防管理员列表异常时轰炸)
_NOTIFY_MAX_ADMINS = 20


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class XiaozhuWeeklyService:
    """语音数据周报(聚合 + 环比 + 快照 + 站内信)"""

    def __init__(self, repo=None):
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository,
        )
        self.repo = repo if repo is not None \
            else Xiaozhu48Repository()

    # --------------------------------------------------------
    # 聚合
    # --------------------------------------------------------

    async def build_weekly_report(self) -> dict:
        """近 7 天聚合(环比自动取 7-14 天段)"""
        now = datetime.now(UTC)
        frm = (now - timedelta(days=7)).isoformat()
        prev_from = (now - timedelta(days=14)).isoformat()

        sessions = await self.repo.scan_sessions(limit=5000)
        turns = await self.repo.scan_turns(limit=20000)

        def _s_ts(s):
            # 会话时间主键 startedAt(缺省回退 lastActiveAt)
            return str(s.get("startedAt")
                       or s.get("lastActiveAt") or "")

        cur_s = [s for s in sessions if _s_ts(s) >= frm]
        cur_t = [t for t in turns
                 if str(t.get("ts") or "") >= frm]
        prev_s = [s for s in sessions
                  if prev_from <= _s_ts(s) < frm]
        prev_t = [t for t in turns
                  if prev_from <= str(t.get("ts") or "") < frm]

        usage = self._usage(cur_s, cur_t)
        mom = {
            "sessions": self._mom(usage["sessions"],
                                  len(prev_s)),
            "voiceTurns": self._mom(
                usage["voiceTurns"],
                sum(1 for t in prev_t
                    if (t.get("channel") or "") == "voice")),
        }
        fixes = await self.repo.list_asr_fixes()
        queue = await self.repo.list_learn_queue()
        return {
            "window": {"from": frm, "to": now.isoformat()},
            "generatedAt": _now_iso(),
            "usage": usage,
            "mom": mom,
            "asr": self._asr(cur_t),
            "intents": self._intents(cur_t),
            "feedback": self._feedback(cur_t),
            "learning": self._learning(queue),
            "fixes": self._fixes(fixes),
        }

    @staticmethod
    def _usage(sessions: list, turns: list) -> dict:
        voice = [t for t in turns
                 if (t.get("channel") or "") == "voice"]
        return {
            "sessions": len(sessions),
            "voiceTurns": len(voice),
            "textTurns": len(turns) - len(voice),
            "members": len({s.get("memberId") for s in sessions
                            if s.get("memberId")}),
        }

    @staticmethod
    def _mom(cur: int, prev: int) -> int | None:
        """环比 %(上期 0 → None 不误判)"""
        if prev <= 0:
            return None
        return round((cur - prev) / prev * 100)

    @staticmethod
    def _asr(turns: list) -> dict:
        voice = [t for t in turns
                 if (t.get("channel") or "") == "voice"]
        failed = sum(1 for t in voice
                     if (t.get("intent") or "")
                     == "asr_failed")
        lats = [float(t.get("latencyMs") or 0)
                for t in voice
                if t.get("latencyMs")]
        return {
            "voiceTurns": len(voice),
            "failedTurns": failed,
            "failRate": round(
                failed / len(voice), 4) if voice else 0.0,
            "avgLatencyMs": round(
                sum(lats) / len(lats)) if lats else 0,
            "maxLatencyMs": round(max(lats)) if lats else 0,
        }

    @staticmethod
    def _intents(turns: list) -> dict:
        counts: dict[str, int] = {}
        for t in turns:
            it = str(t.get("intent") or "unknown")
            counts[it] = counts.get(it, 0) + 1
        top = sorted(counts.items(),
                     key=lambda kv: kv[1], reverse=True)[:5]
        unknown = counts.get("unknown", 0)
        return {
            "top": [{"intent": k, "count": v}
                    for k, v in top],
            "unknownRate": round(
                unknown / len(turns), 4) if turns else 0.0,
        }

    @staticmethod
    def _feedback(turns: list) -> dict:
        up = sum(1 for t in turns
                 if t.get("feedback") == "up")
        down = sum(1 for t in turns
                   if t.get("feedback") == "down")
        return {
            "up": up, "down": down,
            "downRate": round(
                down / (up + down), 4) if (up + down) else 0.0,
        }

    @staticmethod
    def _learning(queue: dict) -> dict:
        stats = {"pending": 0, "adopted": 0, "dismissed": 0}
        for e in queue.values():
            s = e.get("status")
            if s in stats:
                stats[s] += 1
        return stats

    @staticmethod
    def _fixes(fixes: dict) -> dict:
        by_src: dict[str, int] = {}
        hit_list = []
        learn_hits = 0
        for wrong, rec in fixes.items():
            rec = rec or {}
            src = str(rec.get("source") or "manual")
            by_src[src] = by_src.get(src, 0) + 1
            hits = int(rec.get("hits") or 0)
            if hits > 0:
                hit_list.append(
                    (hits, wrong, str(rec.get("to") or ""),
                     src))
            if src == "learn":
                learn_hits += hits
        hit_list.sort(reverse=True)
        return {
            "total": len(fixes),
            "bySource": by_src,
            "learnHits": learn_hits,
            "topHits": [{"wrong": w, "right": r, "hits": h,
                         "source": s}
                        for h, w, r, s in hit_list[:5]],
        }

    # --------------------------------------------------------
    # 触达(站内信)
    # --------------------------------------------------------

    def _compose(self, r: dict) -> tuple:
        """报告 → (标题, 站内信正文)"""
        u = r.get("usage") or {}
        a = r.get("asr") or {}
        f = r.get("fixes") or {}
        lr = r.get("learning") or {}
        fb = r.get("feedback") or {}
        mom_s = r.get("mom", {}).get("sessions")
        mom_v = r.get("mom", {}).get("voiceTurns")

        def pct(v):
            return f"{v:+d}%" if v is not None else "—"

        top_intents = "、".join(
            f"{i['intent']}×{i['count']}"
            for i in (r.get("intents") or {}).get("top", [])[:3]
        ) or "—"
        top_fixes = "、".join(
            f"{h['wrong']}→{h['right']}×{h['hits']}"
            for h in f.get("topHits", [])[:3]) or "—"
        fail_pct = a.get("failRate", 0) * 100
        down_pct = fb.get("downRate", 0) * 100
        title = (f"小竹语音周报: {u.get('sessions', 0)} 会话 "
                 f"{u.get('voiceTurns', 0)} 语音轮 "
                 f"(环比{pct(mom_s)})")
        content = (
            f"【使用】会话 {u.get('sessions', 0)}"
            f"(环比{pct(mom_s)}) · 语音轮 "
            f"{u.get('voiceTurns', 0)}(环比{pct(mom_v)}) · "
            f"会员 {u.get('members', 0)} · 文本轮 "
            f"{u.get('textTurns', 0)}\n"
            f"【识别】失败率 {fail_pct:.1f}%"
            f"(失败 {a.get('failedTurns', 0)} 轮) · "
            f"平均延迟 {a.get('avgLatencyMs', 0)}ms"
            f"(峰值 {a.get('maxLatencyMs', 0)}ms)\n"
            f"【意图top3】{top_intents}\n"
            f"【反馈】👍 {fb.get('up', 0)} / "
            f"👎 {fb.get('down', 0)}"
            f"(踩率 {down_pct:.1f}%)\n"
            f"【学习】待审 {lr.get('pending', 0)} · "
            f"已采纳 {lr.get('adopted', 0)} · "
            f"累计学习命中 {f.get('learnHits', 0)}\n"
            f"【误听热词top3】{top_fixes}\n"
            f"详情: 小竹看板 ⑫ 区块(dashboard 手动生成实时数据)"
        )
        return title, content

    async def run_weekly_round(
            self, force: bool = False,
            notify: bool = True) -> dict:
        """生成周报(当日幂等) + 快照 + 管理员站内信

        force=True 跳过幂等(仍站内信由 notify 控制);
        返回 {generated: bool, report/skipReason}
        """
        snap = await self.repo.load_weekly_snapshot()
        today = _now_iso()[:10]
        if not force and snap \
                and str(snap.get("generatedAt")
                       or "")[:10] == today:
            return {"generated": False,
                    "skipReason": "今日已生成",
                    "report": snap}
        report = await self.build_weekly_report()
        await self.repo.save_weekly_snapshot(report)
        sent = failed = 0
        if notify:
            sent, failed = await self._notify(report)
        report["notify"] = {"sent": sent, "failed": failed}
        await self.repo.save_weekly_snapshot(report)
        logger.info("xiaozhu_weekly_generated sessions=%s "
                    "voiceTurns=%s notify_sent=%s",
                    report["usage"]["sessions"],
                    report["usage"]["voiceTurns"], sent)
        return {"generated": True, "report": report}

    async def _notify(self, report: dict) -> tuple:
        """管理员站内信(逐一发送, 单人失败不阻断)"""
        try:
            from repositories.member_repository import (
                MemberRepository,
            )
            from services.message_service import (
                CATEGORY_SYSTEM, CHANNEL_INMAIL,
                MessageService, PRIORITY_P2,
            )
            admins = [int(m["id"]) for m in
                      await MemberRepository().list_all()
                      if m.get("role") == "admin" and m.get("id")]
        except Exception as exc:
            logger.warning("xiaozhu_weekly_admins_failed: %s", exc)
            return 0, 0
        if not admins:
            return 0, 0
        title, content = self._compose(report)
        svc = MessageService()
        sent = failed = 0
        for admin_id in admins[:_NOTIFY_MAX_ADMINS]:
            try:
                await svc.send_message(
                    admin_id, CHANNEL_INMAIL, title, content,
                    category=CATEGORY_SYSTEM,
                    priority=PRIORITY_P2)
                sent += 1
            except Exception as exc:
                failed += 1
                logger.warning(
                    "xiaozhu_weekly_send_failed admin=%s: %s",
                    admin_id, exc)
        return sent, failed
