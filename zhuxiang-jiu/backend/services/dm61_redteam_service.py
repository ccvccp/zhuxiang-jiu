"""61号·AI智能系统升级决策 红队七向量
(dm61_redteam_service, P5)

计划(docs/61号_AI智能系统升级决策模块实施计划.md
§七 P5):
    RT-01 标签伪造(语义欺骗——措辞
           伪装降敏感级; 域外标签
           缩小影响面)
    RT-02 矩阵操纵(伪造信值降档——
           伪造 tier/预算想降 L 级)
    RT-03 沙箱逃逸(注入执行语义——
           eval/PII 想绕过静态关)
    RT-04 先验投毒(伪造案例库——
           外部数据想污染先验概率)
    RT-05 裁决伪造(越权 decide——
           跳步/重复/不存在)
    RT-06 反馈污染(刷采纳——
           重复反馈刷奖励)
    RT-07 图谱污染(错误因果注入——
           检索参数注入想污染图谱)

设计(58/63号确定性红队范式——不依赖
LLM, 全部向量离线可复现):
    每向量: 构造攻击载荷 → 调用目标面
    → 断言防御行为(拒绝/拦截/强制/
    封闭) → 留痕。

前置: DM61_MODE=shadow/assist(决策面
开放——off 态无攻击面)。
"""

import logging
import os

from core.helpers import ts

from repositories.dm61_repository import (
    Dm61Repository,
)

logger = logging.getLogger("dm61_redteam")

MODEL_VERSION = "v1-dm61-redteam"

# 红队专用会员(向量隔离)
RT_REQUESTER = "rt-attacker"


class Dm61RedteamService:
    """61号红队验证(七向量——确定性)"""

    def __init__(self):
        self.repo = Dm61Repository()

    # ============================================================
    # 红队入口(七向量全量)
    # ============================================================

    async def run_all(self) -> dict:
        """执行七向量红队全量(RT-01~07)

        前置: DM61_MODE=shadow/assist(决策面
        开放——off 态无攻击面)。
        """
        mode = os.environ.get("DM61_MODE", "off")
        if mode == "off":
            raise ValueError(
                "红队需要 DM61_MODE=shadow/assist"
                "(决策面开放——off 态无攻击面)")

        vectors = {}
        vectors["RT-01"] = await \
            self._rt01_tag_forgery()
        vectors["RT-02"] = await \
            self._rt02_matrix_manipulation()
        vectors["RT-03"] = await \
            self._rt03_sandbox_escape()
        vectors["RT-04"] = await \
            self._rt04_prior_poisoning()
        vectors["RT-05"] = await \
            self._rt05_decision_forgery()
        vectors["RT-06"] = await \
            self._rt06_feedback_pollution()
        vectors["RT-07"] = await \
            self._rt07_graph_pollution()

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
    # RT-01 标签伪造(语义欺骗)
    # ============================================================

    async def _rt01_tag_forgery(self) -> dict:
        """三路: 措辞伪装(小调整)想降
        观测类但含支付关键词——确定性
        命中 payment_opt sensitive;
        域外标签注入想缩小影响面
        ——fail-safe 最大影响;
        无关键词伪装——兜底 observe
        不可被措辞操纵升级"""
        from services.dm61_registry import (
            parse_semantic_tag,
            predict_impact,
        )
        results = []

        # 路 ①: 措辞伪装("仅微调"但
        # 含"支付结算"——关键词轨刚性)
        s = parse_semantic_tag(
            "支付结算费率仅微调小改", "")
        results.append({
            "path": "措辞伪装",
            "rejected":
                s["tag"] == "payment_opt"
                and s["sensitivity"]
                == "sensitive"})

        # 路 ②: 域外标签注入(想缩小
        # 影响面——fail-safe 最大)
        i = predict_impact("innocent_tag")
        results.append({
            "path": "域外标签注入",
            "rejected":
                i["sensitivity"] == "critical"
                and i["impactPct"] == 8.0
                and i["roleCount"] == 5})

        # 路 ③: 纯伪装无关键词(想被
        # 误判高敏——兜底恒 observe
        # 不可被操纵)
        s2 = parse_semantic_tag(
            "无伤大雅的调整而已", "")
        results.append({
            "path": "伪装降级",
            "rejected":
                s2["source"] == "fallback"
                and s2["sensitivity"]
                == "observe"})

        defended = all(r["rejected"]
                       for r in results)
        return {
            "vector": "标签伪造(语义欺骗)",
            "defended": defended,
            "results": results,
            "defense": "确定性关键词轨"
                       "(首命中刚性)+域外"
                       "fail-safe 最大影响"
                       "+兜底观测类封闭",
        }

    # ============================================================
    # RT-02 矩阵操纵(伪造信值降档)
    # ============================================================

    async def _rt02_matrix_manipulation(self
                                        ) -> dict:
        """三路: 伪造高 tier+充足预算想
        降核心重构级——标签强制 L3
        刚性; 伪造预算想避开耗尽
        强制——restricted tier 置信
        反向高; budget<0.1 强制 L3
        不可绕"""
        results = []
        prev = os.environ.get("DM61_MODE")
        os.environ["DM61_MODE"] = "shadow"
        try:
            from services.dm61_service import (
                Dm61Service,
            )
            from services.dm61_assess_service import (
                Dm61AssessService,
            )
            base = Dm61Service()
            asvc = Dm61AssessService()

            # 路 ①: 权限变更+全优因子
            # ——仍强制 L3(标签铁律)
            r1 = await base.create_request(
                title="后台权限角色调整",
                hour=3)
            a1 = await asvc.assess(
                r1["requestId"],
                tier="trusted",
                error_budget=1.0,
                history_fail_rate=0.0)
            results.append({
                "path": "全优因子降 L3",
                "rejected":
                    a1["level"] == "L3"
                    and a1["forcedL3Tag"]
                    is True})

            # 路 ②: restricted 伪造高
            # 置信(反向因子刚性)
            r2 = await base.create_request(
                title="支付结算费率优化",
                hour=3)
            a2 = await asvc.assess(
                r2["requestId"],
                tier="restricted",
                error_budget=0.5,
                history_fail_rate=0.0)
            conf = (a2.get("factors")
                    or {}).get(
                "confidence") or {}
            results.append({
                "path": "restricted 反向",
                "rejected":
                    conf.get("score")
                    == 70.0})

            # 路 ③: 预算耗尽伪造想绕
            # (0.05<0.1 强制 L3)
            r3 = await base.create_request(
                title="文案微调",
                hour=3)
            a3 = await asvc.assess(
                r3["requestId"],
                tier="trusted",
                error_budget=0.05,
                history_fail_rate=0.0)
            results.append({
                "path": "预算耗尽强制",
                "rejected":
                    a3["level"] == "L3"
                    and a3["budgetForcedL3"]
                    is True})
        finally:
            if prev is None:
                os.environ.pop(
                    "DM61_MODE", None)
            else:
                os.environ["DM61_MODE"] = prev

        defended = all(r["rejected"]
                       for r in results)
        return {
            "vector": "矩阵操纵(伪造信值"
                      "降档)",
            "defended": defended,
            "results": results,
            "defense": "标签强制 L3 铁律"
                       "+置信度反向刚性"
                       "+预算耗尽强制 L3",
        }

    # ============================================================
    # RT-03 沙箱逃逸(注入执行语义)
    # ============================================================

    async def _rt03_sandbox_escape(self) -> dict:
        """两路: 变更文本含 eval 想绕
        静态关推演——确定性扫描阻断;
        PII 明文想绕——PII 红线
        拦截"""
        from services.dm61_sim_service import (
            Dm61SimService,
        )
        results = []
        prev = os.environ.get("DM61_MODE")
        os.environ["DM61_MODE"] = "shadow"
        try:
            from services.dm61_service import (
                Dm61Service,
            )
            from services.dm61_assess_service import (
                Dm61AssessService,
            )
            base = Dm61Service()
            asvc = Dm61AssessService()
            ssvc = Dm61SimService()

            # 路 ①: eval 注入
            r1 = await base.create_request(
                title="界面适配调整", hour=3)
            await asvc.assess(
                r1["requestId"],
                tier="standard",
                error_budget=0.9,
                history_fail_rate=0.0)
            s1 = await ssvc.simulate(
                r1["requestId"],
                change_text="x = eval(user)")
            results.append({
                "path": "eval 注入",
                "rejected":
                    s1["verdict"]
                    == "blocked"})

            # 路 ②: PII 明文
            r2 = await base.create_request(
                title="界面适配调整二",
                hour=3)
            await asvc.assess(
                r2["requestId"],
                tier="standard",
                error_budget=0.9,
                history_fail_rate=0.0)
            s2 = await ssvc.simulate(
                r2["requestId"],
                change_text="联系 13812345678")
            results.append({
                "path": "PII 明文",
                "rejected":
                    s2["verdict"]
                    == "blocked"})
        finally:
            if prev is None:
                os.environ.pop(
                    "DM61_MODE", None)
            else:
                os.environ["DM61_MODE"] = prev

        defended = all(r["rejected"]
                       for r in results)
        return {
            "vector": "沙箱逃逸(注入执行"
                      "语义)",
            "defended": defended,
            "results": results,
            "defense": "静态关确定性扫描"
                       "(56号敏感 API/PII "
                       "红线零改动复用)"
                       "——文本注入必拦截",
        }

    # ============================================================
    # RT-04 先验投毒(伪造案例库)
    # ============================================================

    async def _rt04_prior_poisoning(self) -> dict:
        """三路: 案例库无外部写入口
        (仅终态决策派生——GET cases
        只读); 未走全链的伪造决策
        (recommended 态无 outcome)
        不入先验; 检索域外标签
        空结果(确定性过滤)"""
        from services.dm61_graph_service import (
            Dm61GraphService,
        )
        results = []
        gsvc = Dm61GraphService()

        # 路 ①: 域外标签先验——
        # 空结果中性(不可被伪造污染)
        prior = await gsvc \
            .prior_probability(
                tag="fake_tag")
        results.append({
            "path": "域外标签先验",
            "rejected":
                prior["sampleSize"] == 0
                and prior["failRate"]
                == 0.0})

        # 路 ②: recommended 态决策
        # (未裁决)不入案例库——
        # 只有 outcome 的终态入池
        view = await gsvc.cases_view()
        # 全部案例均有 outcome
        all_outcome = all(
            c.get("outcome")
            for c in (view.get(
                "recent") or []))
        results.append({
            "path": "非终态不入库",
            "rejected":
                all_outcome
                or view["total"] == 0})

        # 路 ③: 相似检索域外结果
        # 过滤(确定性——空集)
        cases = await gsvc.similar_cases(
            outcome="fake_outcome")
        results.append({
            "path": "域外结果过滤",
            "rejected":
                cases["total"] == 0})

        defended = all(r["rejected"]
                       for r in results)
        return {
            "vector": "先验投毒(伪造"
                      "案例库)",
            "defended": defended,
            "results": results,
            "defense": "案例库封闭派生"
                       "(仅本模块终态决策"
                       "——无外部写入口)"
                       "+检索确定性过滤",
        }

    # ============================================================
    # RT-05 裁决伪造(越权 decide)
    # ============================================================

    async def _rt05_decision_forgery(self) -> dict:
        """三路: 不存在 decisionId
        ——404; 重复裁决(已 decided)
        ——状态机拒绝; recommended
        之前无法裁决(先 assess
        →recommend 状态机)"""
        from services.dm61_decision_service import (
            Dm61DecisionService,
        )
        dsvc = Dm61DecisionService()
        results = []

        # 路 ①: 不存在的决策号
        try:
            await dsvc.decide(
                99999, action="adopted")
            results.append({
                "path": "不存在决策",
                "rejected": False})
        except KeyError:
            results.append({
                "path": "不存在决策",
                "rejected": True})

        # 路 ②: 重复裁决(造链后
        # rejected 再 adopted)
        prev = os.environ.get("DM61_MODE")
        os.environ["DM61_MODE"] = "shadow"
        try:
            from services.dm61_service import (
                Dm61Service,
            )
            from services.dm61_assess_service import (
                Dm61AssessService,
            )
            base = Dm61Service()
            asvc = Dm61AssessService()
            r = await base.create_request(
                title="支付结算费率优化",
                hour=3)
            await asvc.assess(
                r["requestId"],
                tier="standard",
                error_budget=0.3,
                history_fail_rate=0.05)
            rec = await dsvc.recommend(
                r["requestId"])
            # 终审不受开关影响(off 亦可)
            await dsvc.decide(
                rec["decisionId"],
                action="rejected",
                decided_by="甲")
            try:
                await dsvc.decide(
                    rec["decisionId"],
                    action="adopted",
                    decided_by="甲")
                results.append({
                    "path": "重复裁决",
                    "rejected": False})
            except ValueError:
                results.append({
                    "path": "重复裁决",
                    "rejected": True})

            # 路 ③: 未推荐(assessed 态)
            # 无决策记录可裁
            r2 = await base.create_request(
                title="算法权重调整", hour=3)
            await asvc.assess(
                r2["requestId"],
                tier="standard",
                error_budget=0.3,
                history_fail_rate=0.05)
            # assessed 态——decisionId
            # 尚不存在(下一个号空缺)
            try:
                await dsvc.decide(
                    rec["decisionId"] + 100,
                    action="adopted")
                results.append({
                    "path": "跳步裁决",
                    "rejected": False})
            except KeyError:
                results.append({
                    "path": "跳步裁决",
                    "rejected": True})
        finally:
            if prev is None:
                os.environ.pop(
                    "DM61_MODE", None)
            else:
                os.environ["DM61_MODE"] = prev

        defended = all(r["rejected"]
                       for r in results)
        return {
            "vector": "裁决伪造(越权"
                      "decide)",
            "defended": defended,
            "results": results,
            "defense": "状态机封闭"
                       "(recommended→decided "
                       "单次)+决策号存在性"
                       "校验(404)",
        }

    # ============================================================
    # RT-06 反馈污染(刷采纳)
    # ============================================================

    async def _rt06_feedback_pollution(self) -> dict:
        """两路: 重复反馈刷采纳量
        ——1:1 拒绝; 动作域外
        (hacked)——域校验拒绝"""
        from services.dm61_feedback_service import (
            Dm61FeedbackService,
        )
        fsvc = Dm61FeedbackService()
        results = []

        prev = os.environ.get("DM61_MODE")
        os.environ["DM61_MODE"] = "shadow"
        try:
            from services.dm61_service import (
                Dm61Service,
            )
            from services.dm61_assess_service import (
                Dm61AssessService,
            )
            from services.dm61_decision_service import (
                Dm61DecisionService,
            )
            base = Dm61Service()
            asvc = Dm61AssessService()
            dsvc = Dm61DecisionService()
            r = await base.create_request(
                title="支付结算费率优化",
                hour=3)
            await asvc.assess(
                r["requestId"],
                tier="standard",
                error_budget=0.3,
                history_fail_rate=0.05)
            rec = await dsvc.recommend(
                r["requestId"])

            # 路 ①: 正常反馈后重复刷
            await fsvc.submit(
                rec["decisionId"],
                action="adopted",
                outcome="good")
            try:
                await fsvc.submit(
                    rec["decisionId"],
                    action="adopted",
                    outcome="good")
                results.append({
                    "path": "重复反馈",
                    "rejected": False})
            except ValueError:
                results.append({
                    "path": "重复反馈",
                    "rejected": True})

            # 路 ②: 动作域外(存在决策
            # +非法动作——域校验拒绝)
            try:
                await fsvc.submit(
                    rec["decisionId"],
                    action="hacked")
                results.append({
                    "path": "动作域外",
                    "rejected": False})
            except ValueError:
                results.append({
                    "path": "动作域外",
                    "rejected": True})
        finally:
            if prev is None:
                os.environ.pop(
                    "DM61_MODE", None)
            else:
                os.environ["DM61_MODE"] = prev

        defended = all(r["rejected"]
                       for r in results)
        return {
            "vector": "反馈污染(刷采纳)",
            "defended": defended,
            "results": results,
            "defense": "决策 1:1(重复"
                       "拒绝)+动作/结果"
                       "域封闭校验",
        }

    # ============================================================
    # RT-07 图谱污染(错误因果注入)
    # ============================================================

    async def _rt07_graph_pollution(self) -> dict:
        """两路: 检索参数注入(特殊
        字符标签)——确定性过滤空结果
        不崩溃; risk 参数非数字
        ——容错跳过不崩溃"""
        from services.dm61_graph_service import (
            Dm61GraphService,
        )
        gsvc = Dm61GraphService()
        results = []

        # 路 ①: 特殊字符标签注入
        cases = await gsvc.similar_cases(
            tag="<script>alert(1)"
                "</script>")
        results.append({
            "path": "标签注入",
            "rejected":
                cases["total"] == 0
                and cases.get(
                    "success") is True})

        # 路 ②: risk 非数字容错
        cases2 = await gsvc.similar_cases(
            risk="not-a-number")
        results.append({
            "path": "risk 非数字",
            "rejected":
                cases2.get(
                    "success") is True})

        # 路 ③: 归因报告不存在
        # 决策——404(不可伪造)
        try:
            await gsvc \
                .attribution_report(
                    99999)
            results.append({
                "path": "归因 404",
                "rejected": False})
        except KeyError:
            results.append({
                "path": "归因 404",
                "rejected": True})

        defended = all(r["rejected"]
                       for r in results)
        return {
            "vector": "图谱污染(错误"
                      "因果注入)",
            "defended": defended,
            "results": results,
            "defense": "检索确定性过滤"
                       "(域外空集)+参数"
                       "容错+归因存在性"
                       "校验",
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
                "requestId": 0,
                "eventType": "redteam",
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dm61_redteam_track_failed: %s",
                exc)
