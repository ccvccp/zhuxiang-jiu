"""63号·AI智能后台管理 红队七向量
(ab63_redteam_service, P5)

计划(docs/63号_AI智能后台管理模块实施计划.md
§九 P5):
    RT-01 权限提升(伪造上下文降风险分——
           四轴确定性: 场景轴域外值
           fail-soft 不加分)
    RT-02 护航绕过(阻断级跳过——block
           预检拦截: 不可提交)
    RT-03 分流操纵(刷高 Publish_Score——
           高危标签强制 L3 满分亦然)
    RT-04 审核越权(非双人裁决 L3——
           同人拒绝+终审须第三人)
    RT-05 申诉刷分(恶意申诉翻转——
           disputed 单通道+翻转留痕)
    RT-06 培训逃避(重复驳回不学习——
           rejectCount 增量重推防御)
    RT-07 模板注入(恶意行业模板——
           封闭注册域外拒绝)

设计(58号确定性红队范式——不依赖 LLM,
全部向量离线可复现):
    每向量: 构造攻击载荷 → 调用目标面 →
    断言防御行为(拒绝/拦截/强制/封闭) →
    留痕。

前置: AB63_MODE=shadow/assist(决策面
开放——off 态无攻击面)。
"""

import logging
import os

from core.helpers import ts

from repositories.ab63_repository import (
    Ab63Repository,
)

logger = logging.getLogger("ab63_redteam")

MODEL_VERSION = "v1-ab63-redteam"

# 红队专用会员(向量隔离——不复用真实域)
RT_MEMBER = 9951

CLEAN = "居家养老服务 服务有效期90天 退改政策可退"
BLOCKED = "提供假发票开具服务"
EXAG = ("全市最好的居家养老服务 "
        "服务有效期90天 退改政策可退")


class Ab63RedteamService:
    """63号红队验证(七向量——确定性)"""

    def __init__(self):
        self.repo = Ab63Repository()

    # ============================================================
    # 红队入口(七向量全量)
    # ============================================================

    async def run_all(self) -> dict:
        """执行七向量红队全量(RT-01~07)

        前置: AB63_MODE=shadow/assist(决策面
        开放——off 态无攻击面)。
        """
        mode = os.environ.get("AB63_MODE", "off")
        if mode == "off":
            raise ValueError(
                "红队需要 AB63_MODE=shadow/assist"
                "(决策面开放——off 态无攻击面)")

        vectors = {}
        vectors["RT-01"] = await \
            self._rt01_permission_escalation()
        vectors["RT-02"] = await \
            self._rt02_guard_bypass()
        vectors["RT-03"] = await \
            self._rt03_score_manipulation()
        vectors["RT-04"] = await \
            self._rt04_review_bypass()
        vectors["RT-05"] = await \
            self._rt05_appeal_abuse()
        vectors["RT-06"] = await \
            self._rt06_training_evasion()
        vectors["RT-07"] = await \
            self._rt07_template_injection()

        defended = sum(
            1 for v in vectors.values()
            if v.get("defended"))
        result = {
            "success": True,
            "vectors": vectors,
            "summary": {
                "total": len(vectors),
                "defended": defended,
                "allDefended":
                    defended == len(vectors),
            },
            "note": "红队七向量——确定性"
                    "离线可复现",
            "ranAt": ts(),
        }

        # 留痕(dashboard 防御区读回)
        await self._track({
            "action": "redteam_run",
            "defended": defended,
            "total": len(vectors),
            "ranAt": result["ranAt"],
        })
        return result

    # ============================================================
    # RT-01 权限提升(伪造上下文降风险分)
    # ============================================================

    async def _rt01_permission_escalation(self
                                          ) -> dict:
        """三路: 伪造低敏场景(降 penalty
        刷分)——SCENE_PENALTY 域外键
        缺省不加分/伪造高 tier 越权
        batch——四轴域外拒绝/伪造
        合规率>1 越界——clamp 封顶"""
        from services.ab63_registry import (
            evaluate_permission,
        )
        results = []

        # 路 ①: 伪造场景轴域外值
        # (period="fake"——域外键
        #  dict 取不到 penalty=0 但
        # 也不降基线; 域内高敏正常惩罚)
        honest = evaluate_permission(
            "ally_merchant", "batch_ops",
            tier="standard",
            compliance_rate=0.8,
            period="peak", sensitivity="high")
        forged = evaluate_permission(
            "ally_merchant", "batch_ops",
            tier="standard",
            compliance_rate=0.8,
            period="fake",
            sensitivity="ultra_low")
        # 伪造域外不能比诚实域内更高分
        results.append({
            "path": "场景域外伪造",
            "rejected": forged["score"]
            <= honest["score"]})

        # 路 ②: 低 tier 伪造高分
        # 刷 batch(40+(-30)+12=22<70)
        r = evaluate_permission(
            "ally_merchant", "batch_ops",
            tier="restricted",
            compliance_rate=1.0,
            period="normal",
            sensitivity="low")
        results.append({
            "path": "restricted 越权 batch",
            "rejected": r["granted"] is False})

        # 路 ③: 合规率越界 clamp
        # (>1 封顶 1.0——bonus≤15)
        r2 = evaluate_permission(
            "ally_merchant", "basic_crud",
            tier="trusted",
            compliance_rate=99.0)
        # 70+20+15=105→granted, 但
        # bonus 封顶 15 不放大
        results.append({
            "path": "合规率越界 clamp",
            "rejected": r2["reason"]["factors"]
            ["complianceBonus"] <= 15.0})

        defended = all(r["rejected"]
                       for r in results)
        return {
            "vector": "权限提升(伪造上下文"
                      "降风险分)",
            "defended": defended,
            "results": results,
            "defense": "四轴确定性计算——"
                       "域外键缺省不加分+"
                       "tier 修正刚性+"
                       "合规率 clamp 封顶",
        }

    # ============================================================
    # RT-02 护航绕过(阻断级跳过)
    # ============================================================

    async def _rt02_guard_bypass(self) -> dict:
        """两路: block 内容直提
        submit——预检拦截; 篡改
        guardId 绕过——提交必经
        内部护航检测(不可注入)"""
        from services.ab63_submission_service import (
            Ab63SubmissionService,
        )
        svc = Ab63SubmissionService()
        results = []

        # 路 ①: block 内容直提
        try:
            await svc.submit(
                RT_MEMBER, "ally_merchant",
                content=BLOCKED)
            results.append({
                "path": "block 内容直提",
                "rejected": False})
        except ValueError:
            results.append({
                "path": "block 内容直提",
                "rejected": True})

        # 路 ②: 无内容+高危标签
        # (空内容拒绝)
        try:
            await svc.submit(
                RT_MEMBER, "ally_merchant",
                content="  ",
                tags=["funds"])
            results.append({
                "path": "空内容+高危标签",
                "rejected": False})
        except ValueError:
            results.append({
                "path": "空内容+高危标签",
                "rejected": True})

        defended = all(r["rejected"]
                       for r in results)
        return {
            "vector": "护航绕过(阻断级跳过)",
            "defended": defended,
            "results": results,
            "defense": "submit 内部强制护航"
                       "预检——block 拦截"
                       "不可跳过(API 无"
                       "guardId 注入口)",
        }

    # ============================================================
    # RT-03 分流操纵(刷高 Publish_Score)
    # ============================================================

    async def _rt03_score_manipulation(self
                                       ) -> dict:
        """两路: 满分+高危标签——
        强制 L3(forcedBy=highRiskTag);
        伪造 tier=trusted 但 warn
        级护航——分恒 <90 不可 L1"""
        from services.ab63_registry import (
            compute_publish_score,
            route_review_tier,
        )
        results = []

        # 路 ①: 满分+高危标签
        r = route_review_tier(
            100.0, "trusted", ["funds"])
        results.append({
            "path": "满分+高危标签",
            "rejected": r["tier"] == "L3"
            and r["autoPublished"]
            is False})

        # 路 ②: warn 级护航伪造 trusted
        s = compute_publish_score(
            "warn", "trusted", "low")
        r2 = route_review_tier(
            s["score"], "trusted", [])
        # warn=0.7×0.6+1×0.3+1×0.1=82<90
        results.append({
            "path": "warn 伪造 trusted",
            "rejected": r2["tier"] != "L1"
            and s["score"] < 90.0})

        defended = all(r["rejected"]
                       for r in results)
        return {
            "vector": "分流操纵(刷高"
                      "Publish_Score)",
            "defended": defended,
            "results": results,
            "defense": "高危标签强制 L3"
                       "(满分亦然)+护航档"
                       "刚性映射分上限",
        }

    # ============================================================
    # RT-04 审核越权(非双人裁决 L3)
    # ============================================================

    async def _rt04_review_bypass(self) -> dict:
        """三路: L3 同人双裁——
        拒绝; 终审非第三人——拒绝;
        未审先终态跳步(直接 final)——
        reviewType 序不可跳"""
        from services.ab63_submission_service import (
            Ab63SubmissionService,
        )
        svc = Ab63SubmissionService()
        results = []

        # 造 L3(高危标签)
        sub = await svc.submit(
            RT_MEMBER, "ally_merchant",
            content=CLEAN,
            tags=["identity"],
            tier="trusted")
        sid = sub["subId"]

        # 路 ①: 同人双裁
        await svc.review(
            sid, approve=True,
            reviewer="甲")
        try:
            await svc.review(
                sid, approve=True,
                reviewer="甲")
            results.append({
                "path": "同人双裁",
                "rejected": False})
        except ValueError:
            results.append({
                "path": "同人双裁",
                "rejected": True})

        # 路 ②: 终审非第三人
        await svc.review(
            sid, approve=True,
            reviewer="乙")
        try:
            await svc.review(
                sid, approve=True,
                reviewer="甲")
            results.append({
                "path": "终审非第三人",
                "rejected": False})
        except ValueError:
            results.append({
                "path": "终审非第三人",
                "rejected": True})

        # 路 ③: 跳步终审(新 L3 直接
        # 合规官——但 first 未做)
        sub2 = await svc.submit(
            RT_MEMBER, "ally_merchant",
            content=CLEAN,
            tags=["medical"],
            tier="trusted")
        try:
            await svc.review(
                sub2["subId"], approve=True,
                reviewer="合规官")
            r = await svc.review(
                sub2["subId"],
                approve=True,
                reviewer="合规官")
            # 合规官首裁=first 非 final
            # (任何人首裁都是 first)
            results.append({
                "path": "跳步终审",
                "rejected": r.get(
                    "reviewType")
                != "final"})
        except ValueError:
            results.append({
                "path": "跳步终审",
                "rejected": True})

        defended = all(r["rejected"]
                       for r in results)
        return {
            "vector": "审核越权(非双人"
                      "裁决 L3)",
            "defended": defended,
            "results": results,
            "defense": "L3 三步状态机——"
                       "first/second/final"
                       "序不可跳+同人拒绝+"
                       "终审第三人",
        }

    # ============================================================
    # RT-05 申诉刷分(恶意申诉翻转)
    # ============================================================

    async def _rt05_appeal_abuse(self) -> dict:
        """两路: 非终态申诉——
        拒绝; adjusted 终态再申诉
        ——拒绝(单通道)"""
        from services.ab63_submission_service import (
            Ab63SubmissionService,
        )
        svc = Ab63SubmissionService()
        results = []

        # 路 ①: 非终态(L2 pending)申诉
        sub = await svc.submit(
            RT_MEMBER, "ally_merchant",
            content=CLEAN,
            tier="standard")
        try:
            await svc.appeal(sub["subId"])
            results.append({
                "path": "非终态申诉",
                "rejected": False})
        except ValueError:
            results.append({
                "path": "非终态申诉",
                "rejected": True})

        # 路 ②: adjusted 终态再申诉
        sub2 = await svc.submit(
            RT_MEMBER, "ally_merchant",
            content=EXAG,
            tier="standard")
        await svc.review(
            sub2["subId"], approve=False,
            reviewer="审核员")
        await svc.appeal(sub2["subId"])
        await svc.resolve_appeal(
            sub2["subId"], overturn=True,
            adjudicator="合规官")
        try:
            await svc.appeal(sub2["subId"])
            results.append({
                "path": "adjusted 再申诉",
                "rejected": False})
        except ValueError:
            results.append({
                "path": "adjusted 再申诉",
                "rejected": True})

        defended = all(r["rejected"]
                       for r in results)
        return {
            "vector": "申诉刷分(恶意申诉"
                      "翻转)",
            "defended": defended,
            "results": results,
            "defense": "申诉单通道——"
                       "仅 published/rejected"
                       "可申诉+adjusted 终态"
                       "封闭(翻转全留痕)",
        }

    # ============================================================
    # RT-06 培训逃避(重复驳回不学习)
    # ============================================================

    async def _rt06_training_evasion(self) -> dict:
        """两路: 过期后无新驳回——
        不重推(历史频次); 新增驳回
        ——重推(增量证据)"""
        from services.ab63_submission_service import (
            Ab63SubmissionService,
        )
        from services.ab63_training_service import (
            Ab63TrainingService,
        )
        sub_svc = Ab63SubmissionService()
        train_svc = Ab63TrainingService()
        results = []

        # 造高频驳回(EXAG×2)
        for _ in range(2):
            s = await sub_svc.submit(
                RT_MEMBER,
                "ally_merchant",
                content=EXAG,
                tier="standard")
            await sub_svc.review(
                s["subId"], approve=False,
                reviewer="审核员")

        # 推送+完成(逃避: 完成后
        # 不改行为继续驳回)
        p = await train_svc.push()
        tid = (p.get("pushes")
               or [{}])[0].get(
            "trainingId")
        if tid:
            await train_svc.complete(tid)

        # 路 ①: 完成后无新驳回——不重推
        p2 = await train_svc.push()
        results.append({
            "path": "完成后无新驳回",
            "rejected": p2["pushed"] == 0})

        # 路 ②: 新增驳回——重推
        # (增量证据——无论历史频次
        # 基数, 新证据必触发)
        s3 = await sub_svc.submit(
            RT_MEMBER, "ally_merchant",
            content=EXAG,
            tier="standard")
        await sub_svc.review(
            s3["subId"], approve=False,
            reviewer="审核员")
        p3 = await train_svc.push()
        results.append({
            "path": "新驳回重推",
            "rejected": p3["pushed"] == 1
            and p3["pushes"][0]
            ["memberId"] == RT_MEMBER
            and p3["pushes"][0]
            ["ruleId"]
            == "GUARD_EXAGGERATION"
            and p3["pushes"][0]
            ["rejectCount"] > 2})

        defended = all(r["rejected"]
                       for r in results)
        return {
            "vector": "培训逃避(重复驳回"
                      "不学习)",
            "defended": defended,
            "results": results,
            "defense": "rejectCount 增量"
                       "快照——重推需新"
                       "驳回证据(逃避即"
                       "再犯即再推)",
        }

    # ============================================================
    # RT-07 模板注入(恶意行业模板)
    # ============================================================

    async def _rt07_template_injection(self) -> dict:
        """两路: 恶意行业名
        (industry 注入)——封闭
        池匹配失败降级通用域;
        角色域外模板请求——拒绝"""
        from services.ab63_service import (
            Ab63Service,
        )
        svc = Ab63Service()
        results = []

        # 路 ①: 恶意行业注入
        # (渲染不崩溃+降级通用)
        r = await svc.render_workbench(
            RT_MEMBER, "ally_merchant",
            novice=True,
            industry="<script>alert(1)"
                     "</script>")
        rec = (r.get("renderOptions")
               or {}).get(
            "templateRecommendation") \
            or []
        results.append({
            "path": "恶意行业注入",
            "rejected": bool(rec)
            and rec[0]
            != "<script>alert(1)"
                "</script>"})

        # 路 ②: 域外角色模板请求
        try:
            await svc.render_workbench(
                RT_MEMBER, "super_admin")
            results.append({
                "path": "域外角色模板",
                "rejected": False})
        except ValueError:
            results.append({
                "path": "域外角色模板",
                "rejected": True})

        defended = all(r["rejected"]
                       for r in results)
        return {
            "vector": "模板注入(恶意行业"
                      "模板)",
            "defended": defended,
            "results": results,
            "defense": "行业池封闭匹配"
                       "(industry in pool)"
                       "+角色域封闭校验",
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "grantId": 0,
                "eventType": "redteam",
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ab63_redteam_track_failed: %s",
                exc)
