"""40号 P7e·雷达2.0 自治理与进化服务(设计文档《40号 P7 规划方案》§7)

引擎五: 效果归因闭环 + 雷达效能周报 + 阈值自适应(只紧不松) +
中枢看板

架构口径:
    - 效果归因闭环: 每个任务包带唯一溯源 ID(traceId) →
      P6b 派发脚本 shortCode → attract 漏斗全程跟踪
      (点击→注册→下单) → 漏斗结果回填 radar_scores 最新快照
      (P7b 转化潜力的统计源——"高分事件是否真带来高转化"闭环)
    - 雷达效能周报(确定性聚合): 触发数/命中率(高分→高转化
      一致率)/误报率(L1 被人工否决率)/漏报案例库(L3 事后
      高转化事件自动入库学习)
    - 阈值自适应(收紧不放松): 派发任务违规率异常升高
      (×3 且样本≥20, 三检测器范式) → 自动收紧 L1 阈值
      (+10, 上限 95) + 人工审计通知——**阈值只可自动收紧,
      放宽须 46号建议书**(宪法红线: 误报优化不放松风控)
    - 看板: 事件/分级/任务/效能四区聚合(纯读取观测面)

红线(宪法域):
    - 阈值只可自动收紧: tighten 轨单调不增(75→85→95 封顶)
    - 违规处置回流永不自动惩罚: report_violation 仅留痕标记
      (降档/下架等惩罚经 46号审批——宪法域)
    - LLM 禁入: 归因=计数/周报=聚合/收紧=阈值比较/看板=汇总
"""

import logging
from datetime import datetime, UTC

from repositories.radar_repository import (
    RadarRepository, TASK_STATUS_DISPATCHED, TASK_STATUS_REJECTED,
    GRADE_L3,
)

logger = logging.getLogger(__name__)


# ============================================================
# P7e 常量(设计文档 §7)
# ============================================================

# 违规率异常检测(三检测器范式: ×3 且样本≥20)
VIOLATION_BASELINE_RATE = 0.05   # 违规率基线(处置回流口径)
VIOLATION_SURGE_RATIO = 3.0     # 异常倍数
VIOLATION_MIN_SAMPLE = 20       # 最小样本

# 阈值收紧轨(只紧不松)
TIGHTEN_STEP = 10               # 每次收紧 +10
L1_LINE_DEFAULT = 75            # 默认 L1 线(设计 §4.2)
L1_LINE_CAP = 95                # 收紧上限(宪法域封顶)

# 命中判定(高分→高转化一致率)
HIT_CONVERSION_LINE = 0.5       # 转化率达标线(冷启动均值口径)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def funnel_conversion(clicks: int, registered: int,
                       activated: int) -> float:
    """漏斗转化率(P7b 同口径: (注册×0.4+激活×0.6)/点击 归一化)"""
    if clicks <= 0:
        return 0.0
    rate = (registered * 0.4 + activated * 0.6) / clicks
    return round(max(0.0, min(1.0, rate)), 4)


class RadarGovernService:
    """40号 P7e·雷达2.0 自治理与进化(归因/周报/阈值/看板)"""

    def __init__(self, repo: RadarRepository = None):
        self.repo = repo if repo is not None else RadarRepository()

    # ============================================================
    # 1. 效果归因闭环(traceId → attract 漏斗 → score 回填)
    # ============================================================

    async def attribute_task(self, task_id: int = None,
                             trace_id: str = None) -> dict:
        """任务漏斗归因(task→script→shortCode→attract 全链)

        流程:
            ① 定位任务(task_id 或 traceId)——须已派发
            ② P6b 脚本 shortCode → attract 点击/注册/下单计数
            ③ 漏斗结果回填任务(funnelTrace) + 事件最新评分
               快照(clicks/registered/activated——P7b 转化
               统计源)
            ④ 命中判定: 转化率≥0.5 → hit(高分→高转化一致)

        Raises:
            KeyError: 任务不存在
            ValueError: 任务未派发(无脚本可归因)
        """
        if task_id is not None:
            task = await self.repo.get_task(int(task_id))
        elif trace_id:
            task = await self.repo.find_task_by_trace(trace_id)
        else:
            raise ValueError("task_id 与 trace_id 须至少其一")
        if task is None:
            raise KeyError(
                f"任务不存在(taskId={task_id}, "
                f"traceId={trace_id})")
        if task.get("status") != TASK_STATUS_DISPATCHED:
            raise ValueError(
                f"仅已派发任务可归因(当前{task.get('status')})")
        script_id = int(task.get("dispatchScriptId") or 0)
        from repositories.blogger_repository import (
            BloggerRepository)
        script = await BloggerRepository().get_av_script(
            script_id) if script_id else None
        if script is None:
            raise ValueError(
                f"派发脚本不存在(scriptId={script_id})"
                "——派发回执异常")
        short_code = str(script.get("shortCode") or "")
        # attract 漏斗计数(确定性计数, LLM 禁入)
        clicks = registered = activated = 0
        if short_code:
            from repositories.attract_repository import (
                AttractRepository)
            attract = AttractRepository()
            click_list = await attract.list_clicks(
                code=short_code, limit=100000)
            clicks = len(click_list)
            attrs = await attract.list_attributions(limit=100000)
            code_attrs = [a for a in attrs
                         if a.get("code") == short_code]
            registered = sum(
                1 for a in code_attrs if a.get("memberId"))
            activated = sum(
                1 for a in code_attrs if a.get("orderId"))
        conv = funnel_conversion(clicks, registered, activated)
        funnel = {"clicks": clicks, "registered": registered,
                  "activated": activated, "conversionRate": conv,
                  "attributedAt": _now_iso()}
        # 任务回填(funnelTrace——P7e 周报统计源)
        task = await self.repo.update_task(task["taskId"], {
            "funnelTrace": funnel})
        # 事件最新评分快照回填(P7b 转化统计源)
        event_id = int(task.get("eventId") or 0)
        scores = await self.repo.list_scores(event_id=event_id,
                                            limit=1)
        if scores:
            await self.repo._update(
                self.repo.TABLE_SCORES, scores[0]["scoreId"], {
                    "clicks": clicks, "registered": registered,
                    "activated": activated})
        logger.info("p7e_task_attributed taskId=%s clicks=%s "
                    "reg=%s act=%s conv=%s",
                    task["taskId"], clicks, registered,
                    activated, conv)
        return {"task": task, "funnel": funnel,
                "hit": conv >= HIT_CONVERSION_LINE}

    async def report_violation(self, task_id: int,
                               note: str = "") -> dict:
        """违规处置回流(内容违规/下架处置——仅留痕标记)

        红线: 违规标记仅作收紧检测信号; 降档/下架等惩罚
        处置经 46号审批, 本方法永不自动执行惩罚。

        Raises:
            KeyError: 任务不存在
            ValueError: 任务未派发 / 重复标记
        """
        task = await self.repo.get_task(int(task_id))
        if task is None:
            raise KeyError(f"任务不存在(taskId={task_id})")
        if task.get("status") != TASK_STATUS_DISPATCHED:
            raise ValueError(
                f"仅已派发任务可标记违规(当前{task.get('status')})")
        if task.get("violationMarked"):
            raise ValueError("任务已标记违规(勿重复)")
        task = await self.repo.update_task(task_id, {
            "violationMarked": True,
            "violationNote": (note or "内容违规/下架处置回流")
            [:300], "violatedAt": _now_iso()})
        return task

    # ============================================================
    # 2. 阈值自适应(自动轨——只紧不松)
    # ============================================================

    async def tighten_threshold(self, reason: str = "") -> dict:
        """L1 阈值自适应收紧(违规率异常: ×3 且样本≥20)

        收紧轨: 当前线 +10(上限 95) → threshold 留痕入库
        (P7b 评分经 get_current_l1_line 生效)。
        红线: 只紧不松——本轨永不降线; 放宽须 46号建议书。

        Returns:
            {tightened, currentLine, newLine, stats, auditNote}
        """
        tasks = await self.repo.list_tasks(limit=2000)
        dispatched = [t for t in tasks
                      if t.get("status") == TASK_STATUS_DISPATCHED]
        sample = len(dispatched)
        violations = sum(1 for t in dispatched
                         if t.get("violationMarked"))
        rate = round(violations / sample, 4) if sample else 0.0
        anomaly = (sample >= VIOLATION_MIN_SAMPLE
                   and rate > VIOLATION_SURGE_RATIO
                   * VIOLATION_BASELINE_RATE)
        current = await self.repo.get_current_l1_line(
            default=L1_LINE_DEFAULT)
        if not anomaly:
            return {"tightened": False, "currentLine": current,
                    "newLine": current,
                    "stats": {"sample": sample,
                               "violations": violations,
                               "violationRate": rate,
                               "baselineRate":
                                   VIOLATION_BASELINE_RATE},
                    "auditNote": "违规率无异常(×3 且样本≥20 "
                                 "未触发)——阈值不变"}
        if current >= L1_LINE_CAP:
            return {"tightened": False, "currentLine": current,
                    "newLine": current,
                    "stats": {"sample": sample,
                              "violations": violations,
                              "violationRate": rate,
                              "baselineRate":
                                  VIOLATION_BASELINE_RATE},
                    "auditNote": f"已达收紧上限({L1_LINE_CAP})"
                                 "——持续异常须 46号建议书人工审计"}
        new_line = min(L1_LINE_CAP, current + TIGHTEN_STEP)
        report_id = await self.repo.next_id("report")
        record = {
            "reportId": report_id,
            "kind": "threshold",
            "action": "tighten",
            "oldLine": current, "newLine": new_line,
            "sample": sample, "violations": violations,
            "violationRate": rate,
            "baselineRate": VIOLATION_BASELINE_RATE,
            "reason": (reason or "派发任务违规率异常(×3 且"
                       "样本≥20)——自动收紧")[:500],
            "createdAt": _now_iso(),
        }
        await self.repo.save_efficiency(record)
        logger.warning("p7e_threshold_tightened %s→%s "
                       "(violationRate=%s sample=%s)",
                       current, new_line, rate, sample)
        return {"tightened": True, "currentLine": current,
                "newLine": new_line, "changeId": report_id,
                "stats": {"sample": sample,
                          "violations": violations,
                          "violationRate": rate,
                          "baselineRate":
                              VIOLATION_BASELINE_RATE},
                "auditNote": f"L1 阈值已自动收紧 "
                             f"{current}→{new_line}(只紧不松; "
                             "放宽须 46号建议书)——已通知人工审计"}

    # ============================================================
    # 3. 雷达效能周报(确定性聚合)
    # ============================================================

    async def generate_weekly_report(self) -> dict:
        """效能周报生成(触发数/命中率/误报率/漏报案例库)

        - 触发数: 任务包总数(L1 响应量)
        - 命中率: 已派发且归因转化率≥0.5 占已派发比
          (高分→高转化一致率)
        - 误报率: 人工否决占已裁决比(L1 被否决率)
        - 漏报案例库: L3 评分快照事后高转化
          (clicks>0 且转化率≥0.5)——自动入库学习
        """
        tasks = await self.repo.list_tasks(limit=2000)
        dispatched = [t for t in tasks
                     if t.get("status") == TASK_STATUS_DISPATCHED]
        rejected = [t for t in tasks
                    if t.get("status") == TASK_STATUS_REJECTED]
        adjudicated = len(dispatched) + len(rejected)
        # 命中率: 漏斗回流转化率≥0.5(未归因=未命中)
        hits = 0
        for t in dispatched:
            funnel = t.get("funnelTrace") or {}
            if funnel_conversion(
                    int(funnel.get("clicks") or 0),
                    int(funnel.get("registered") or 0),
                    int(funnel.get("activated") or 0)) \
                    >= HIT_CONVERSION_LINE:
                hits += 1
        hit_rate = round(hits / len(dispatched), 4) \
            if dispatched else 0.0
        fp_rate = round(len(rejected) / adjudicated, 4) \
            if adjudicated else 0.0
        # 漏报案例库: L3 事后高转化(快照漏斗字段回流口径)
        scores = await self.repo.list_scores(limit=5000)
        missed = []
        for s in scores:
            if s.get("grade") != GRADE_L3:
                continue
            clicks = int(s.get("clicks") or 0)
            if clicks <= 0:
                continue
            conv = funnel_conversion(
                clicks, int(s.get("registered") or 0),
                int(s.get("activated") or 0))
            if conv >= HIT_CONVERSION_LINE:
                missed.append({
                    "scoreId": s.get("scoreId"),
                    "eventId": s.get("eventId"),
                    "title": s.get("title", ""),
                    "category": s.get("category", ""),
                    "conversionRate": conv,
                    "lesson": "L3 观察储备事件事后高转化"
                              "——契合映射或转化统计待校准"
                              "(46号建议书轨)"})
        report_id = await self.repo.next_id("report")
        current_line = await self.repo.get_current_l1_line(
            default=L1_LINE_DEFAULT)
        report = {
            "reportId": report_id,
            "kind": "weekly",
            "triggered": len(tasks),
            "dispatched": len(dispatched),
            "rejected": len(rejected),
            "hits": hits,
            "hitRate": hit_rate,
            "falsePositiveRate": fp_rate,
            "missedCases": missed,
            "currentL1Line": current_line,
            "violationMarked": sum(
                1 for t in dispatched
                if t.get("violationMarked")),
            "createdAt": _now_iso(),
        }
        await self.repo.save_efficiency(report)
        logger.info("p7e_weekly_report reportId=%s triggered=%s "
                    "hitRate=%s fpRate=%s missed=%s",
                    report_id, len(tasks), hit_rate, fp_rate,
                    len(missed))
        return report

    async def list_reports(self, kind: str = None,
                           limit: int = 20) -> list[dict]:
        """效能记录查询(weekly 周报/threshold 阈值留痕)"""
        return await self.repo.list_efficiency(kind=kind,
                                               limit=limit)

    # ============================================================
    # 4. 雷达中枢看板(四区聚合——纯读取观测面)
    # ============================================================

    async def dashboard(self) -> dict:
        """看板四区: 事件/分级/任务/效能(确定性汇总)"""
        events = await self.repo.list_events(limit=5000)
        tasks = await self.repo.list_tasks(limit=2000)
        by_grade = {}
        for e in events:
            g = e.get("grade") or "unscored"
            by_grade[g] = by_grade.get(g, 0) + 1
        by_lifecycle = {}
        for e in events:
            lc = e.get("lifecycle") or "new"
            by_lifecycle[lc] = by_lifecycle.get(lc, 0) + 1
        by_status = {}
        for t in tasks:
            s = t.get("status") or "pending"
            by_status[s] = by_status.get(s, 0) + 1
        reports = await self.repo.list_efficiency(
            kind="weekly", limit=1)
        current_line = await self.repo.get_current_l1_line(
            default=L1_LINE_DEFAULT)
        return {
            "events": {"total": len(events),
                       "byGrade": by_grade,
                       "byLifecycle": by_lifecycle},
            "tasks": {"total": len(tasks),
                      "byStatus": by_status,
                      "violations": sum(
                          1 for t in tasks
                          if t.get("violationMarked"))},
            "efficiency": (reports[0] if reports
                           else {"note": "暂无周报"}),
            "threshold": {"currentL1Line": current_line,
                          "cap": L1_LINE_CAP,
                          "tightenHistory": len(
                              await self.repo.list_efficiency(
                                  kind="threshold", limit=1000))},
            "generatedAt": _now_iso(),
        }
