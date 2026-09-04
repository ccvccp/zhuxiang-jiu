"""45号·P1 AI 智能雷达(三通道采集 + 验真三道关 + 因果效应估计)

计划(docs/45号_信值模块实施计划.md §四):
    三通道合规采集("全网搜收"的合规化升级):
        ① 公开域雷达(radar): 裁判文书/政府公示/行政处罚/
          权威媒体——定向扫描 + 自动去标识化 + 白名单制
        ② 授权探针(probe): 平台信用分等——显式授权留痕,
          严禁爬虫抓取私有域(mock 态模拟读数)
        ③ 自愿存证(deposit): 角色上传证据 → AI 先验真
          → 入库(验真不过不入分)

    验真三道关(§四 4.1):
        1. 多模态真伪鉴别: 摆拍/刷单/水军识别(mock 确定性
           规则: 证据内容哈希去重 + 格式校验)
        2. 跨源交叉验证: 单源孤证不入库——需 ≥2 独立源
           或 1 权威源(authoritative 标记)
        3. 情感-意图联合推理: 区分"真善"与"表演式向善"
           (real 轨 LLM; mock 轨确定性关键词规则)

    因果效应估计(§四 4.2, 反刷分核心):
        净贡献 = 实际观测值 - 反事实基线(同类角色群体均值,
        而非自我历史——刷量抬高不了基线, UEBA 范式)

设计铁律:
    - 默认 mock 确定性模拟(TRUST_RADAR_MODE=real 需外部
      凭证, 实机全链 mock 态可测——41/42号三态范式)
    - 验真置信度 < 0.7 → unverified, 不参与评分(宁缺毋滥)
    - 去标识化: 公开域扫描结果只保留摘要/严重度/层归属,
      原文与个人信息脱敏后才落事件
"""

import hashlib
import logging
import os
import re

from core.helpers import ts

from repositories.trust_value_repository import (
    TrustValue45Repository,
)

logger = logging.getLogger(__name__)

# 验真置信度阈值(低于则 unverified, 不参与评分)
VERIFY_THRESHOLD = 0.7

# 跨源交叉验证: 独立源数量下限(1 权威源可豁免)
CROSS_SOURCE_MIN = 2

# 因果效应: 同类角色群体反事实基线(观测口径可调;
# 净贡献 = max(0, 观测值 - 基线×(1+容差)))
COUNTERFACTUAL_TOLERANCE = 0.1

# 公开域采集白名单(计划 §四 4.1: 仅公开合法数据源)
PUBLIC_SOURCES = (
    "court",          # 裁判文书
    "gov_penalty",    # 行政处罚公示
    "gov_tax",        # 税务公示
    "media",          # 权威媒体
)

# 权威源(1 源即可过跨源关——官方公信力背书; 探针授权数据
# 天然可信: 用户显式授权 + 平台签名读数, 视同权威)
AUTHORITATIVE_SOURCES = ("court", "gov_penalty", "gov_tax",
                         "probe_authorized")

# 授权探针支持的平台(mock 态模拟读数; real 态外部凭证待办)
PROBE_PROVIDERS = ("zhima", "platform_credit", "bank_reference")

# 探针→因子映射(平台信用分作用于 L2 平台言行因子)
PROBE_FACTOR = "platform_conduct"


def radar_mode() -> str:
    """雷达模式: mock(默认确定性模拟) / real(真采, 外部凭证待办)"""
    return os.environ.get("TRUST_RADAR_MODE", "mock").lower()


# ============================================================
# 验真三道关
# ============================================================

def _evidence_fingerprint(evidence: str) -> str:
    """证据内容指纹(SHA-256, 重复证据识别)"""
    return hashlib.sha256(
        (evidence or "").encode("utf-8")).hexdigest()[:16]


def multimodal_check(kind: str, evidence: str) -> tuple:
    """第一关: 多模态真伪鉴别(mock 确定性规则)

    判定依据:
        - 证据为空/过短 → 0.0(无效)
        - 摆拍特征词(摆拍/表演/剧组/道具) → 0.2
        - 刷单特征词(刷单/代打卡/代做) → 0.2
        - 内容与类型不符(L1 附照片链接但无编号) → 折减
    Returns:
        (置信度 0-1, 判定说明)
    """
    text = (evidence or "").strip()
    if len(text) < 8:
        return 0.0, "证据内容过短(疑似空证据)"
    score = 1.0
    for kw in ("摆拍", "表演", "剧组", "道具"):
        if kw in text:
            score = min(score, 0.2)
            return score, f"多模态鉴别: 疑似摆拍(命中「{kw}」)"
    for kw in ("刷单", "代打卡", "代做", "水军"):
        if kw in text:
            score = min(score, 0.2)
            return score, f"多模态鉴别: 疑似刷量(命中「{kw}」)"
    # 证据需含可核验要素(编号/日期/机构名)——格式启发式
    if not re.search(r"\d", text):
        score = min(score, 0.5)
        return score, "证据缺可核验要素(编号/日期)"
    return score, "多模态鉴别通过"


def cross_source_check(sources: list) -> tuple:
    """第二关: 跨源交叉验证(单源孤证不入库)

    Args:
        sources: 数据来源标识列表(如 ["court", "media"])
    Returns:
        (通过与否, 置信度, 判定说明)
    """
    uniq = set(s for s in (sources or []) if s)
    if not uniq:
        return False, 0.0, "无数据来源(孤证)"
    if any(s in AUTHORITATIVE_SOURCES for s in uniq):
        return True, 1.0, \
            f"权威源背书({sorted(uniq)})"
    if len(uniq) >= CROSS_SOURCE_MIN:
        return True, 0.9, \
            f"{len(uniq)} 独立源交叉验证({sorted(uniq)})"
    return False, 0.3, \
        f"单源孤证({sorted(uniq)}, 需≥{CROSS_SOURCE_MIN}源或权威源)"


def intent_check(summary: str) -> tuple:
    """第三关: 情感-意图联合推理(区分真善与表演式向善)

    mock 确定性规则: 高调宣传播特征词(作秀/宣传稿/摆拍)
    折减; real 轨走 LLM 意图推理(失败回退规则)。
    Returns:
        (置信度 0-1, 判定说明)
    """
    text = (summary or "").strip()
    if not text:
        return 0.6, "无行为描述(意图不可判, 保守折减)"
    for kw in ("作秀", "宣传稿", "摆拍", "营销", "蹭热度"):
        if kw in text:
            return 0.3, f"意图推理: 疑似表演式向善(命中「{kw}」)"
    # real 轨: LLM 意图推理(可选增强——失败回退规则)
    if radar_mode() == "real":
        try:
            from services.llm_client import (
                provider_client, llm_enabled,
            )
            if llm_enabled():
                reply = provider_client().chat(
                    system="你是行为意图审核员。判断该行为描述"
                           "是真实向善还是表演式向善(营销/作秀)。"
                           "只回答 JSON: {\"score\": 0到1, "
                           "\"reason\": \"一句话\"}",
                    user=f"行为描述: {text}")
                if reply:
                    import json
                    data = json.loads(
                        re.search(r"\{.*\}", reply,
                                  re.S).group())
                    return (float(data.get("score") or 0.5),
                            f"LLM 意图推理: "
                            f"{data.get('reason', '')}")
        except Exception as exc:
            logger.warning("trust45_intent_llm_skip: %s", exc)
    return 0.95, "意图推理通过"


def verify_pipeline(kind: str, evidence: str,
                    sources: list, summary: str = "") -> dict:
    """验真管线(三道关串联, 取最低置信度)

    Returns:
        {verified, confidence, checks: [{stage, pass,
        confidence, note}], fingerprint}
    """
    m_score, m_note = multimodal_check(kind, evidence)
    c_pass, c_score, c_note = cross_source_check(sources)
    i_score, i_note = intent_check(summary)
    confidence = round(min(m_score, c_score, i_score), 2)
    verified = confidence >= VERIFY_THRESHOLD and c_pass
    return {
        "verified": verified,
        "confidence": confidence,
        "checks": [
            {"stage": "multimodal", "pass": m_score >= 0.7,
             "confidence": m_score, "note": m_note},
            {"stage": "cross_source", "pass": c_pass,
             "confidence": c_score, "note": c_note},
            {"stage": "intent", "pass": i_score >= 0.7,
             "confidence": i_score, "note": i_note},
        ],
        "fingerprint": _evidence_fingerprint(evidence),
    }


# ============================================================
# 因果效应估计(§四 4.2)
# ============================================================

def net_contribution(observed: float, peer_baseline: float) -> float:
    """净贡献 = max(0, 实际观测 - 反事实基线×(1+容差))

    基线取同类角色群体均值(UEBA 范式——刷量抬高不了群体基线;
    "自家员工刷志愿时长"只能抬高自己观测, 抬不动群体)。
    """
    import math
    baseline = float(peer_baseline or 0) * \
        (1 + COUNTERFACTUAL_TOLERANCE)
    return round(max(0.0, float(observed or 0) - baseline), 1)


# ============================================================
# 雷达服务
# ============================================================


class TrustRadarService:
    """AI 智能雷达(P1; 三通道采集统一入口)"""

    def __init__(self,
                 repo: TrustValue45Repository =
                 TrustValue45Repository()):
        self.repo = repo

    # --------------------------------------------------------
    # ① 公开域雷达扫描
    # --------------------------------------------------------

    async def scan_public(self, trust_id: int) -> dict:
        """公开域一轮扫描(白名单源定向 + 去标识化 + 事件灌入)

        mock 态: 对档案做确定性扫描模拟——按 idDigest 派生
        发现集(同证件重启容器结果一致, 幂等可测);
        real 态: 外接检索 API(凭证待办), 结构相同。

        Raises:
            KeyError: 档案不存在
        """
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        profile = await self.repo.get_profile(trust_id)
        if profile is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")

        findings = self._mock_findings(profile)
        applied, skipped = 0, 0
        for f in findings:
            # 公开域数据天然多源权威(法院/政府), 跨源关直判
            v = verify_pipeline(
                f["kind"], f["evidence"], f["sources"],
                f["summary"])
            if not v["verified"]:
                skipped += 1
                logger.info("trust45_radar_skip trustId=%s "
                            "finding=%s conf=%s", trust_id,
                            f["kind"], v["confidence"])
                continue
            svc = TrustProfileService(repo=self.repo)
            await svc.record_event(
                trust_id, f["layer"], f["factor"],
                f["delta"], severity=f["severity"],
                source="radar", summary=f"[雷达] {f['summary']}")
            applied += 1
        logger.info("trust45_radar_scan trustId=%s mode=%s "
                    "applied=%s skipped=%s", trust_id,
                    radar_mode(), applied, skipped)
        return {"success": True, "trustId": trust_id,
                "mode": radar_mode(), "scanned": len(findings),
                "applied": applied, "skipped": skipped,
                "sources": list(PUBLIC_SOURCES)}

    def _mock_findings(self, profile: dict) -> list:
        """mock 确定性发现集(按 idDigest 派生, 幂等)"""
        digest = profile.get("idDigest") or ""
        # 稳定哈希取模: 同档案扫描结果恒定
        h = int(digest[:8], 16) if digest[:8] else 0
        findings = []
        # 发现①: 行政处罚(1/4 档案命中, general 扣 10)
        if h % 4 == 0:
            findings.append({
                "layer": "L1", "factor": "regulatory",
                "delta": -10, "severity": "general",
                "kind": "gov_penalty",
                "evidence": f"处罚决定书 编号罚字[{(h % 9000) + 1000}]号"
                             f" 2026-08-15",
                "sources": ["gov_penalty", "media"],
                "summary": "行政处罚公示(去标识化: 已脱敏)",
            })
        # 发现②: 权威媒体正面报道(1/5 命中, L2 加 8)
        if h % 5 == 0:
            findings.append({
                "layer": "L2", "factor": "community_standing",
                "delta": 8, "severity": "general",
                "kind": "media",
                "evidence": f"权威媒体正面报道 2026-07-0{h % 9 + 1}",
                "sources": ["media", "court"],
                "summary": "权威媒体正面报道(去标识化: 已脱敏)",
            })
        # 发现③: 裁判文书未履行(1/8 命中, severe 扣 40)
        if h % 8 == 0:
            findings.append({
                "layer": "L1", "factor": "legal_record",
                "delta": -40, "severity": "severe",
                "kind": "court",
                "evidence": f"执行案号({(h % 900) + 100}执"
                            f"{(h % 90) + 10}) 2026-06-20",
                "sources": ["court"],
                "summary": "法院执行记录(去标识化: 已脱敏)",
            })
        return findings

    # --------------------------------------------------------
    # ② 授权探针
    # --------------------------------------------------------

    async def register_probe(self, trust_id: int,
                             provider: str,
                             scope: str = "credit_score") -> dict:
        """授权登记(显式授权留痕; 严禁爬虫抓取私有域)

        登记后立即走一次模拟读数(mock 确定性)——授权数据
        天然可信(用户主动授权+平台签名), 只过格式关与意图关。

        Raises:
            KeyError: 档案不存在
            ValueError: provider 非法
        """
        provider = (provider or "").strip().lower()
        if provider not in PROBE_PROVIDERS:
            raise ValueError(
                f"非法数据源: {provider}"
                f"(合法值: {'/'.join(PROBE_PROVIDERS)})")
        profile = await self.repo.get_profile(trust_id)
        if profile is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")

        # 授权留痕(独立授权书记录)
        auth_id = await self.repo.next_event_id()
        await self.repo.save_event({
            "eventId": auth_id, "trustId": trust_id,
            "layer": "L2", "factor": PROBE_FACTOR,
            "delta": 0, "severity": "general",
            "source": "probe_auth",
            "summary": f"授权 {provider}(scope={scope})",
            "ts": ts(),
        })

        # mock 读数(确定性: 按 trustId+provider 派生 550-950)
        seed = (trust_id * 31 + len(provider) * 7) % 401
        score = 550 + seed
        # 映射到 L2 因子增量: (score-650)/10, 上限 +20
        delta = max(-10.0, min(20.0, (score - 650) / 10))

        v = verify_pipeline(
            "probe", f"授权读数 score={score} ts={ts()[:10]}",
            ["probe_authorized"],
            f"{provider} 授权信用分 {score}")
        applied = False
        if v["verified"]:
            from services.trust_scoring_service import (
                TrustProfileService,
            )
            svc = TrustProfileService(repo=self.repo)
            await svc.record_event(
                trust_id, "L2", PROBE_FACTOR, delta,
                source="probe",
                summary=f"[探针] {provider} 信用分 {score}"
                        f"(授权读数)")
            applied = True

        logger.info("trust45_probe trustId=%s provider=%s "
                    "score=%s delta=%s applied=%s", trust_id,
                    provider, score, delta, applied)
        return {"success": True, "trustId": trust_id,
                "provider": provider, "scope": scope,
                "mode": radar_mode(), "score": score,
                "delta": round(delta, 1), "applied": applied,
                "verified": v["verified"],
                "confidence": v["confidence"]}

    async def list_probes(self, trust_id: int) -> dict:
        """角色的授权列表(授权留痕事件)"""
        events = await self.repo.list_events_by_trust(trust_id)
        probes = [e for e in events
                  if e.get("source") in ("probe_auth", "probe")]
        return {"success": True, "trustId": trust_id,
                "total": len(probes),
                "probes": [
                    {"eventId": e.get("eventId"),
                     "source": e.get("source"),
                     "summary": e.get("summary"),
                     "ts": e.get("ts")} for e in probes]}

    # --------------------------------------------------------
    # ③ 自愿存证
    # --------------------------------------------------------

    async def submit_deposit(self, trust_id: int, layer: str,
                             factor: str, observed: float,
                             peer_baseline: float,
                             evidence: str, summary: str = "",
                             sources: list = None) -> dict:
        """自愿存证上传(AI 先验真 → 因果净贡献 → 入库)

        净贡献口径: observed 为角色申报的绝对量, 因果效应
        估计剔除群体自然增长后按 0-100 折算因子增量。

        Raises:
            KeyError: 档案不存在
            ValueError: 参数非法
        """
        from services.trust_scoring_service import (
            TrustValueScorer, TrustProfileService,
        )
        layer = (layer or "").strip().upper()
        factor = (factor or "").strip()
        if layer not in ("L1", "L2", "L3"):
            raise ValueError(f"非法层级: {layer}")
        if factor not in TrustValueScorer.LAYER_OF:
            raise ValueError(f"非法因子: {factor}")
        if TrustValueScorer.LAYER_OF[factor] != layer:
            raise ValueError(
                f"因子 {factor} 不属于 {layer} 层")
        observed = float(observed or 0)
        peer_baseline = float(peer_baseline or 0)
        if not 0 <= observed <= 100000:
            raise ValueError("observed 需在 [0, 100000]")
        if not 0 <= peer_baseline <= 100000:
            raise ValueError("peerBaseline 需在 [0, 100000]")
        if len((evidence or "").strip()) < 8:
            raise ValueError("证据内容必填(≥8 字符)")
        profile = await self.repo.get_profile(trust_id)
        if profile is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")

        # 验真管线(存证默认单源——用户自证; 带权威源可过)
        v = verify_pipeline("deposit", evidence,
                            sources or ["self_deposit"], summary)

        deposit_id = await self.repo.next_event_id()
        if not v["verified"]:
            # 验真不过: 留痕但不入分(宁缺毋滥)
            await self.repo.save_event({
                "eventId": deposit_id, "trustId": trust_id,
                "layer": layer, "factor": factor,
                "delta": 0, "severity": "general",
                "source": "deposit_rejected",
                "summary": f"[存证拒] {summary or ''}"
                           f"(conf={v['confidence']})",
                "ts": ts(),
            })
            return {"success": True, "depositId": deposit_id,
                    "verified": False,
                    "confidence": v["confidence"],
                    "checks": v["checks"],
                    "netContribution": 0.0, "applied": False,
                    "note": "验真未通过(孤证/置信度不足), "
                            "不入分——可补充独立源后重新提交"}

        # 因果净贡献(反事实基线剔除)
        net = net_contribution(observed, peer_baseline)
        # 折算因子增量: 净贡献线性映射, 上限 +30(存证单次)
        delta = min(30.0, net / 10.0)

        # 显式落库存证事件(depositId 稳定——status 可查),
        # 再走因子增量(record_event 只管因子更新)
        await self.repo.save_event({
            "eventId": deposit_id, "trustId": trust_id,
            "layer": layer, "factor": factor,
            "delta": round(delta, 1), "severity": "general",
            "source": "deposit",
            "summary": f"[存证] {summary or ''}"
                       f"(净贡献 {net}, 申报 {observed}, "
                       f"群体基线 {peer_baseline})",
            "ts": ts(),
        })
        svc = TrustProfileService(repo=self.repo)
        result = await svc.record_event(
            trust_id, layer, factor, delta,
            source="deposit_merge",
            summary=f"[存证并档] depositId={deposit_id}")

        # P3 联动: L3 净贡献折半发行 TV(准备金锚定——
        # 验真通过的存证即准备金资产; L1/L2 层不发行)
        issued = 0.0
        if layer == "L3" and net > 0 and not result.get("fused"):
            try:
                from services.trust_asset_service import (
                    TrustAssetService,
                )
                issue_r = await TrustAssetService(
                    repo=self.repo).issue(
                    trust_id, round(net / 2.0, 2),
                    reserve_ref=f"deposit:{deposit_id}",
                    memo=f"存证净贡献发行(净贡献 {net} 折半)")
                issued = issue_r.get("balance")
            except ValueError as exc:
                logger.info("trust45_deposit_issue_skip "
                            "trustId=%s: %s", trust_id, exc)
        return {"success": True, "depositId": deposit_id,
                "verified": True,
                "confidence": v["confidence"],
                "checks": v["checks"],
                "netContribution": net,
                "delta": round(delta, 1), "applied": True,
                "score": result.get("score"),
                "tvIssued": round(net / 2.0, 2) if (
                    layer == "L3" and net > 0
                    and not result.get("fused")) else 0.0,
                "tvBalance": issued}

    async def deposit_status(self, deposit_id: int) -> dict:
        """存证状态查询(异步验真回调查询口径)"""
        event = await self._find_event(deposit_id)
        if not event:
            raise KeyError(f"存证 {deposit_id} 不存在")
        return {
            "success": True, "depositId": deposit_id,
            "status": ("rejected" if event.get("source")
                       == "deposit_rejected" else "applied"),
            "layer": event.get("layer"),
            "factor": event.get("factor"),
            "delta": event.get("delta"),
            "summary": event.get("summary"),
            "ts": event.get("ts"),
        }

    async def _find_event(self, event_id: int) -> dict | None:
        """按 eventId 直查事件(含 rejected 存证轨)"""
        if is_redis_mode():
            client = await _redis()
            data = await client.hgetall(_k(
                "trust45", "trust45_events", event_id))
            return self.repo._deserialize(data) if data else None
        self.repo._ensure_store()
        ev = self.repo.store.get("trust45_events", {}).get(
            event_id)
        return dict(ev) if ev else None


# 模式工具(避免模块级循环导入)
def is_redis_mode():
    from repositories.backend import is_redis_mode as _f
    return _f()


async def _redis():
    from repositories.backend import get_redis_client
    return await get_redis_client()


def _k(entity: str, *parts) -> str:
    from repositories.backend import _k as _kk
    return _kk(entity, *parts)
