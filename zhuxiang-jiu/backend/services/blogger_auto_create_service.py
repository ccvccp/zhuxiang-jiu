"""40号 P5b·自主创作工坊服务(设计文档《40号 P5 升级方案》§4)

多版本并行生成(个性化钩子矩阵) → 合规内生校验(生成时词表约束
+ AI 标识水印强制) → A/B 小流量实验(胜出固化入策略库)
→ UGC 素材库(授权登记 + 分成建议——永不自动给付)

架构口径:
    - 确定性生成: 钩子×结构的参数化模板, 候选词表 = 全量 − 禁用词表
      (设计文档"违禁词 token 概率置零"的确定性实现); LLM 禁入判定链
    - 每版本独立 attract 短码(独立归因追踪)
    - A/B 胜出判据: 置信样本 ≥ CONFIDENT_CLICKS(50) 且复合 reward
      对比; 平局取合规分高者(β 铁律延伸——合规优先于流量)
    - 策略库排行: winCount 降序 → avgReward 降序, 驱动后续选题默认钩子

红线(宪法域):
    - AI 标识水印强制: 每版本末尾固定携带, 缺失即生成失败(ValueError)
    - 三审闸门复用: 版本逐版过 compliance_gate, 硬拒版本不入实验
    - UGC 分成永不自动: 仅生成 pending 建议书, 给付须人工审批
"""

import logging
from datetime import datetime, UTC

from repositories.blogger_repository import (
    BloggerRepository, PLATFORMS,
)
from repositories.promo_repository import (
    DRINKING_ACTION_WORDS, AUTHORITY_BACKING_WORDS,
    EFFICACY_CLAIM_WORDS, BANNED_WORDS,
    REQUIRED_DISCLAIMER, REQUIRED_AGE_TIP,
)
from services.blogger_service import BloggerService

logger = logging.getLogger(__name__)


# ============================================================
# 钩子库(设计文档 §4.1: 个性化钩子矩阵, 5 类人群)
# ============================================================

HOOK_PRICE_ANCHOR = "hook_price_anchor"          # 学生党→价格锚点
HOOK_SCENE_GRASS = "hook_scene_grass"            # 宝妈圈→场景种草
HOOK_EMOTIONAL_COMPANY = "hook_emotional"        # 银发族→情感陪伴
HOOK_GIFT_FACE = "hook_gift_face"                # 商务人士→礼赠体面
HOOK_TASTING_PRO = "hook_tasting_pro"            # 酒友圈→品鉴专业

# 人群画像 → 主钩子 + 副钩子(实验变体来源)
AUDIENCE_HOOKS = {
    "student": (HOOK_PRICE_ANCHOR, HOOK_SCENE_GRASS,
                HOOK_TASTING_PRO),
    "mom": (HOOK_SCENE_GRASS, HOOK_EMOTIONAL_COMPANY,
            HOOK_GIFT_FACE),
    "senior": (HOOK_EMOTIONAL_COMPANY, HOOK_TASTING_PRO,
               HOOK_GIFT_FACE),
    "business": (HOOK_GIFT_FACE, HOOK_TASTING_PRO,
                 HOOK_PRICE_ANCHOR),
    "wine_lover": (HOOK_TASTING_PRO, HOOK_SCENE_GRASS,
                   HOOK_GIFT_FACE),
}
AUDIENCES = tuple(AUDIENCE_HOOKS.keys())

# 结构库(设计文档 §4.1: 叙事结构 2 类)
STRUCTURE_PAIN_SOLUTION = "structure_pain_solution"  # 痛点-解法-CTA
STRUCTURE_STORY_LADDER = "structure_story_ladder"    # 故事递进-CTA
STRUCTURES = (STRUCTURE_PAIN_SOLUTION, STRUCTURE_STORY_LADDER)

HOOK_NAMES = {
    HOOK_PRICE_ANCHOR: "价格锚点钩",
    HOOK_SCENE_GRASS: "场景种草钩",
    HOOK_EMOTIONAL_COMPANY: "情感陪伴钩",
    HOOK_GIFT_FACE: "礼赠体面钩",
    HOOK_TASTING_PRO: "品鉴专业钩",
}
STRUCTURE_NAMES = {
    STRUCTURE_PAIN_SOLUTION: "痛点解法结构",
    STRUCTURE_STORY_LADDER: "故事递进结构",
}

# AI 标识水印(宪法域: 强制携带, 广告法 AI 标识合规)
AI_WATERMARK = "AI 创作·内容仅供参考"

# 全量禁用词表(生成时约束: 候选词表 = 全量 − 禁用)
FORBIDDEN_WORDS = tuple(dict.fromkeys(
    DRINKING_ACTION_WORDS + AUTHORITY_BACKING_WORDS
    + EFFICACY_CLAIM_WORDS + BANNED_WORDS))

# A/B 置信样本线(设计文档 §4.3: ≥50 点击)
CONFIDENT_CLICKS = 50
# UGC 分成建议状态
REVENUE_PENDING = "pending"
REVENUE_APPROVED = "approved"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sanitize_text(text: str) -> tuple[str, list[str]]:
    """合规内生校验: 禁用词置零(设计文档"token 概率置零"确定性实现)

    将命中的禁用词替换为安全占位"▇"; 返回(净化文本, 命中词列表)。
    模板槽位词表本身已安全, 此函数是生成后的兜底防线。
    """
    cleaned = str(text or "")
    hits = [w for w in FORBIDDEN_WORDS if w in cleaned]
    for w in hits:
        cleaned = cleaned.replace(w, "▇")
    return cleaned, hits


# ============================================================
# 多版本生成模板(确定性: 钩子开头 × 结构主体 × 合规尾)
# ============================================================

_HOOK_OPENERS = {
    HOOK_PRICE_ANCHOR: "预算有限也想喝点好的? 这篇帮你算明白。",
    HOOK_SCENE_GRASS: "周末小聚的餐桌上, 少不了一瓶有说法的酒。",
    HOOK_EMOTIONAL_COMPANY: "陪老爸喝一杯的时光, 值得认真挑一瓶酒。",
    HOOK_GIFT_FACE: "送礼选酒, 面子和心意可以一次到位。",
    HOOK_TASTING_PRO: "入口绵甜、落口回甘——竹香型白酒的品鉴笔记。",
}

_STRUCT_BODIES = {
    STRUCTURE_PAIN_SOLUTION: (
        "很多人在{topic}上犯难: 想要口感层次又怕踩坑。\n"
        "竹香型白酒的解法很直接——以竹叶提取物入曲, "
        "入口绵甜、落口回甘, 新手也喝得明白。\n"
        "点击 {link} 看看适合你的那一款。"
    ),
    STRUCTURE_STORY_LADDER: (
        "关于{topic}, 有个值得聊的发现:\n"
        "从选粮到蒸馏, 竹香型工艺把「清雅」两个字做进了酒体, "
        "老友聚会开一瓶, 话匣子自然就打开了。\n"
        "同款戳 {link}。"
    ),
}

_TAIL = ("内容出处: 竹香酒官方内容库\n"
         "（{disclaimer}，{age}周岁以下请勿饮酒）\n{watermark}")


def build_version_body(hook: str, structure: str, topic: str,
                       link: str) -> str:
    """确定性版本文案生成(钩子开头 × 结构主体 × 合规尾 + AI水印)"""
    opener = _HOOK_OPENERS.get(hook, _HOOK_OPENERS[HOOK_SCENE_GRASS])
    body = _STRUCT_BODIES.get(
        structure, _STRUCT_BODIES[STRUCTURE_PAIN_SOLUTION]).format(
        topic=topic, link=link or "主页链接")
    tail = _TAIL.format(disclaimer=REQUIRED_DISCLAIMER,
                        age=REQUIRED_AGE_TIP, watermark=AI_WATERMARK)
    return f"{opener}\n{body}\n{tail}"


class BloggerAutoCreateService:
    """40号 P5b·自主创作工坊(多版本生成/AB实验/策略库/UGC)"""

    def __init__(self, repo: BloggerRepository = None,
                 blogger_service: BloggerService = None):
        self.repo = repo if repo is not None else BloggerRepository()
        self.svc = (blogger_service if blogger_service is not None
                    else BloggerService())

    # ============================================================
    # 1. 多版本并行生成
    # ============================================================

    async def generate_versions(self, topic: str,
                                audience: str,
                                platform: str = "douyin") -> dict:
        """按人群画像生成 5+ 版本(主钩子×2结构 + 副钩子补足)

        每版本: 独立 attract 短码 → 确定性模板生成 →
        sanitize 兜底 → compliance_gate 复用校验 →
        AI 水印断言 → 入 blogger_ab_versions + 创建实验(running)。

        Raises:
            ValueError: 主题/人群/平台非法
        """
        if not (topic or "").strip():
            raise ValueError("选题主题不能为空")
        if audience not in AUDIENCES:
            raise ValueError(
                f"人群画像无效({audience}, 须为{'/'.join(AUDIENCES)})")
        if platform not in PLATFORMS:
            raise ValueError(
                f"平台无效({platform}, 须为{'/'.join(PLATFORMS)})")
        hooks = AUDIENCE_HOOKS[audience]
        # 版本组合: 主钩子×2结构 + 副钩子×2结构 = 5 版本起
        combos = [(hooks[0], STRUCTURE_PAIN_SOLUTION),
                  (hooks[0], STRUCTURE_STORY_LADDER),
                  (hooks[1], STRUCTURE_PAIN_SOLUTION),
                  (hooks[1], STRUCTURE_STORY_LADDER),
                  (hooks[2], STRUCTURE_PAIN_SOLUTION)]
        experiment_id = await self.repo.next_id("experiment")
        versions, rejected = [], []
        for hook, structure in combos:
            # 每版本独立短码(独立归因)
            short_code, short_link = await self._new_track_code(
                topic, audience)
            raw_body = build_version_body(
                hook, structure, topic.strip(), short_link)
            body, hits = sanitize_text(raw_body)
            # AI 水印宪法域断言(模板必含; sanitize 不可破坏)
            if AI_WATERMARK not in body:
                raise ValueError(
                    f"AI 标识水印缺失(hook={hook})——生成即失败铁律")
            # 三审闸门复用(硬拒版本不入实验)
            gate = BloggerService.compliance_gate(body, {})
            if gate["hardFail"] or gate["score"] < 60:
                rejected.append({"hook": hook, "structure": structure,
                                 "violations": gate["violations"],
                                 "score": gate["score"]})
                continue
            version_id = await self.repo.next_id("aversion")
            version = {
                "versionId": version_id,
                "experimentId": experiment_id,
                "hookType": hook,
                "hookName": HOOK_NAMES.get(hook, hook),
                "structureType": structure,
                "structureName": STRUCTURE_NAMES.get(
                    structure, structure),
                "body": body,
                "sanitizeHits": hits,
                "shortCode": short_code,
                "shortLink": short_link,
                "complianceScore": gate["score"],
                "metrics": {"clicks": 0, "registered": 0,
                            "ordered": 0},
                "reward": 0.0,
                "status": "candidate",
                "createdAt": _now_iso(),
            }
            versions.append(await self.repo.save_ab_version(version))
        if not versions:
            raise ValueError("全部版本被三审闸门拒绝, 无可实验版本")
        experiment = {
            "experimentId": experiment_id,
            "topic": topic.strip(),
            "audience": audience,
            "platform": platform,
            "status": "running",
            "winnerVersionId": 0,
            "versionIds": [v["versionId"] for v in versions],
            "createdAt": _now_iso(),
            "closedAt": "",
        }
        await self.repo.save_experiment(experiment)
        return {"experiment": experiment, "versions": versions,
                "rejected": rejected}

    async def _new_track_code(self, topic: str,
                             audience: str) -> tuple[str, str]:
        """每版本独立 attract 短码(归因载体; 失败返回空, 不阻断)"""
        try:
            from services.attract_service import AttractService
            link = await AttractService().create_short_link(
                note=f"40号P5b自主创作:{audience}:{topic[:20]}")
            return link["code"], link.get("url", "")
        except Exception as exc:
            logger.warning("p5b_track_code_failed: %s", exc)
            return "", ""

    # ============================================================
    # 2. A/B 实验(指标注入 / 胜出评估 / 固化)
    # ============================================================

    async def record_version_metrics(self, version_id: int,
                                     clicks: int = None,
                                     registered: int = None,
                                     ordered: int = None) -> dict:
        """实验版本指标注入(attract 归因回填/测试轨)

        Raises:
            KeyError: 版本不存在
        """
        version = await self.repo.get_ab_version(version_id)
        if version is None:
            raise KeyError(f"实验版本不存在(versionId={version_id})")
        metrics = dict(version.get("metrics") or {})
        for k, v in (("clicks", clicks), ("registered", registered),
                     ("ordered", ordered)):
            if v is not None:
                metrics[k] = max(int(metrics.get(k) or 0), int(v))
        # 复合 reward 就地重算(转化效率×0.5 + 合规×0.3, 风险未知为0)
        from services.blogger_auto_learn_service import (
            compute_conversion_efficiency,
            compute_value_aligned_reward,
        )
        conv = compute_conversion_efficiency(
            metrics.get("registered", 0), metrics.get("ordered", 0),
            metrics.get("clicks", 0))
        version.update({"metrics": metrics, "reward":
                        compute_value_aligned_reward(
                            conv, version.get("complianceScore", 0),
                            0.0)})
        return await self.repo.save_ab_version(version)

    async def promote_experiment(self, experiment_id: int) -> dict:
        """胜出评估 + 固化入策略库

        判据(设计文档 §4.3):
            - 全版本置信样本 ≥ CONFIDENT_CLICKS(50), 否则 409
            - 复合 reward 最高者胜; 平局取合规分高者(β 铁律延伸)
            - 胜出钩子/结构 → 策略库 winCount+1

        Raises:
            KeyError: 实验不存在
            ValueError: 实验非 running / 样本不足 / 自主行为已暂停
        """
        # P5d 铁律: pause 后自主行为一律拒绝(仲裁优先于调度)
        from services.blogger_auto_govern_service import \
            BloggerAutoGovernService
        await BloggerAutoGovernService(
            repo=self.repo, blogger_service=self.svc
        ).require_running_async()
        experiment = await self.repo.get_experiment(experiment_id)
        if experiment is None:
            raise KeyError(f"实验不存在(experimentId={experiment_id})")
        if experiment.get("status") != "running":
            raise ValueError(
                f"实验状态非法(当前{experiment.get('status')}, "
                f"仅 running 可评估)")
        versions = [v for v in await self.repo.list_ab_versions(
            experiment_id=experiment_id, limit=100)]
        if not versions:
            raise ValueError("实验无版本")
        unconfident = [v["versionId"] for v in versions
                       if int((v.get("metrics") or {})
                              .get("clicks") or 0) < CONFIDENT_CLICKS]
        if unconfident:
            raise ValueError(
                f"样本不足(versionId={unconfident} 点击数<"
                f"{CONFIDENT_CLICKS}, A/B 判据未达置信线)")
        # 胜出: reward 降序 → 合规分降序(平局合规优先)
        ranked = sorted(versions, key=lambda v: (
            -float(v.get("reward") or 0),
            -float(v.get("complianceScore") or 0)))
        winner = ranked[0]
        await self.repo.update_ab_version(
            winner["versionId"], {"status": "winner"})
        for v in ranked[1:]:
            await self.repo.update_ab_version(
                v["versionId"], {"status": "lost"})
        # 固化: 胜出钩子+结构入策略库
        hook_strategy = await self._bump_strategy(
            "hook", winner["hookType"], winner["hookName"],
            float(winner.get("reward") or 0))
        struct_strategy = await self._bump_strategy(
            "structure", winner["structureType"],
            winner["structureName"],
            float(winner.get("reward") or 0))
        experiment.update({"status": "promoted",
                           "winnerVersionId": winner["versionId"],
                           "closedAt": _now_iso()})
        await self.repo.update_experiment(experiment_id, experiment)
        return {"experiment": experiment, "winner": winner,
                "strategies": [hook_strategy, struct_strategy]}

    async def _bump_strategy(self, type_: str, name_id: str,
                             display_name: str,
                             reward: float) -> dict:
        """策略库计数(strategyId 幂等创建, winCount+1, avgReward EMA)"""
        existing = await self.repo.find_strategy(type_, name_id)
        if existing is None:
            strategy_id = await self.repo.next_id("strategy")
            strategy = {
                "strategyId": strategy_id, "type": type_,
                "name": name_id, "displayName": display_name,
                "winCount": 1, "useCount": 1,
                "avgReward": round(float(reward), 4),
                "status": "active", "createdAt": _now_iso(),
            }
            return await self.repo.save_strategy(strategy)
        win = int(existing.get("winCount") or 0) + 1
        use = int(existing.get("useCount") or 0) + 1
        avg = float(existing.get("avgReward") or 0.0)
        # EMA 平滑(α=0.3)
        avg_new = round(avg + 0.3 * (reward - avg), 4)
        return await self.repo.update_strategy(
            existing["strategyId"],
            {"winCount": win, "useCount": use,
             "avgReward": avg_new})

    async def list_experiments(self, status: str = None) -> list[dict]:
        """实验列表(附版本指标摘要)"""
        experiments = await self.repo.list_experiments(
            status=status, limit=100)
        out = []
        for e in experiments:
            versions = await self.repo.list_ab_versions(
                experiment_id=e["experimentId"], limit=50)
            e = dict(e)
            e["versionMetrics"] = [
                {"versionId": v["versionId"],
                 "hookName": v.get("hookName"),
                 "structureName": v.get("structureName"),
                 "clicks": int((v.get("metrics") or {})
                               .get("clicks") or 0),
                 "reward": float(v.get("reward") or 0),
                 "complianceScore": v.get("complianceScore"),
                 "status": v.get("status")}
                for v in versions]
            out.append(e)
        return out

    async def list_strategies(self, type: str = None) -> list[dict]:
        """策略库排行(winCount 降序 → avgReward 降序)"""
        return await self.repo.list_strategies(type=type, limit=100)

    # ============================================================
    # 3. UGC 素材库(授权登记 + 分成建议——永不自动给付)
    # ============================================================

    async def register_ugc_asset(self, owner_id: int, title: str,
                                 license_type: str,
                                 commission_rate: float = 0.05
                                 ) -> dict:
        """UGC 素材入库(授权登记)

        Raises:
            ValueError: 参数非法 / 分成比例越界
        """
        if not (title or "").strip():
            raise ValueError("素材标题不能为空")
        if license_type not in ("authorized", "cc_by", "purchased"):
            raise ValueError(
                "授权类型须为 authorized/cc_by/purchased")
        rate = float(commission_rate)
        if not 0.0 <= rate <= 0.5:
            raise ValueError("分成比例须在 [0, 0.5]")
        asset_id = await self.repo.next_id("asset")
        asset = {
            "assetId": asset_id,
            "ownerId": int(owner_id),
            "title": title.strip(),
            "license": license_type,
            "commissionRate": rate,
            "useCount": 0,
            "revenueAmount": 0.0,
            "status": "active",
            "revenueProposals": [],
            "createdAt": _now_iso(),
        }
        return await self.repo.save_ugc_asset(asset)

    async def propose_ugc_revenue(self, asset_id: int,
                                  gmv: float) -> dict:
        """生成分成结算建议书(pending——人工审批后方可给付)

        铁律: 分成永不自动——本方法仅生成建议书并留痕,
        实际给付须 admin 在审批面 approve(P5d 干预通道接入)。

        Raises:
            KeyError: 素材不存在
            ValueError: 素材停用 / GMV 非法 / 已有待审建议
        """
        asset = await self.repo.get_ugc_asset(asset_id)
        if asset is None:
            raise KeyError(f"UGC素材不存在(assetId={asset_id})")
        if asset.get("status") != "active":
            raise ValueError(f"素材已停用({asset.get('status')})")
        if float(gmv or 0) <= 0:
            raise ValueError("结算 GMV 须大于 0")
        proposals = asset.get("revenueProposals") or []
        if any(p.get("status") == REVENUE_PENDING for p in proposals):
            raise ValueError("已有待审分成建议(先处置再提交)")
        amount = round(float(gmv) * float(asset.get("commissionRate")
                                         or 0), 2)
        proposal = {
            "proposalId": len(proposals) + 1,
            "gmv": round(float(gmv), 2),
            "amount": amount,
            "status": REVENUE_PENDING,
            "createdAt": _now_iso(),
            "approvedAt": "",
        }
        proposals.append(proposal)
        use_count = int(asset.get("useCount") or 0) + 1
        return await self.repo.update_ugc_asset(
            asset_id, {"revenueProposals": proposals,
                      "useCount": use_count})
