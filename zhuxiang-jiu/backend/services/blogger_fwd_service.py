"""40号 P6e·自主合规转发引擎服务(设计文档《40号 P6 升级方案》§7)

从"流量抓取"到"可信价值共振"——跟随模式授权化升级:
    转发 ≠ 搬运, 转发 = 信用担保行为(AI 每转发一条内容,
    即本站对其合规性/真实性/价值观的背书)。
    流程铁律: 先确权、再合规、后分发、全溯源。

四层架构:
    1. 智能权利确权层: 双重授权(平台 CC/MCN/白名单 + 原作者
       明确同意)→ 哈希存证; 撤回 → 秒级下架关联转发内容
    2. 多模态合规深审层: 深审分(文本/元数据/价值观确定性规则)
       三档: ≥90 自动进入二创 / 70-89 人工复审队列 /
       <70 拒绝+原因反馈
    3. 增值二创与溯源层: 策展说明(确定性模板)+ 信值钩子植入
       (P5b 钩子库复用) + 来源标注强制(缺失即拒绝)
    4. 利益分配层: 分润建议书(pending——人工审批后方可给付,
       永不自动)

红线(宪法域):
    - 未获双重授权的内容绝不转发(无论流量多高/契合度多好)
    - 撤回授权 → 关联转发内容秒级下架(全量, 不分先例)
    - 价值观冲突(炫富/焦虑营销/歧视)即使不违法也拒绝转发
    - 分润永不自动——仅生成建议书, 给付须人工审批
    - 深审判定全确定性(词表+规则+布尔逻辑), LLM 禁入
"""

import hashlib
import json
import logging
from datetime import datetime, UTC

from repositories.blogger_repository import (
    BloggerRepository, PLATFORMS,
)
from services.blogger_service import BloggerService

logger = logging.getLogger(__name__)


# ============================================================
# P6e 常量(设计文档 §7)
# ============================================================

# 平台授权类型(第一重)
PLATFORM_AUTH_CC = "cc"                 # 开放 CC 协议
PLATFORM_AUTH_MCN = "mcn"               # MCN 合作合约
PLATFORM_AUTH_WHITELIST = "whitelist"   # 官方转载白名单
PLATFORM_AUTH_KINDS = (PLATFORM_AUTH_CC, PLATFORM_AUTH_MCN,
                      PLATFORM_AUTH_WHITELIST)

# 授权/内容状态
AUTH_STATUS_ACTIVE = "active"
AUTH_STATUS_REVOKED = "revoked"
REVIEW_STATUS_AUTO = "auto"             # 深审 ≥90 自动进入二创
REVIEW_STATUS_MANUAL = "manual"         # 深审 70-89 人工复审
REVIEW_STATUS_REJECTED = "rejected"     # 深审 <70 拒绝
PUBLISH_STATUS_PUBLISHED = "published"
PUBLISH_STATUS_TAKEDOWN = "takedown"

# 深审三档阈值
DEEP_REVIEW_AUTO_LINE = 90.0
DEEP_REVIEW_MANUAL_LINE = 70.0

# 价值观冲突词表(确定性——与本站"互助/诚信/可持续"理念冲突)
# 即使不违法也拒绝转发(设计文档 §7.2)
VALUES_CONFLICT_WORDS = (
    "炫富", "攀比", "焦虑营销", "贩卖焦虑", "歧视", "地域黑",
    "性别对立", "容貌焦虑", "内卷躺平对立", "饭圈互撕",
)

# 风险词表(复用 40号 RISK_BLOCK_WORDS 口径, 深审一票否决层)
RISK_HARD_BLOCK_WORDS = ("政治", "未成年", "医疗事故", "灾害",
                         "地震", "洪水", "疫情")

# 深审扣分项(确定性规则——文本轨)
DEDUP_HARD_BLOCK = 100.0      # 风险词命中 → 直接拒绝
DEDUP_VALUES_CONFLICT = 40.0  # 价值观冲突 → 大幅扣分
DEDUP_NO_SOURCE_META = 10.0   # 来源元数据缺失 → 扣分

# 分润建议状态(永不自动给付)
REVENUE_PENDING = "pending"
REVENUE_APPROVED = "approved"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _evidence_hash(payload: dict) -> str:
    """存证哈希(SHA256 40 位截断——授权合规免责凭证)"""
    digest = json.dumps(payload, ensure_ascii=False,
                        sort_keys=True, default=str)
    return hashlib.sha256(digest.encode("utf-8")).hexdigest()[:40]


def _expired(expires_at: str) -> bool:
    """授权是否过期(空=永久; 非法格式视为已过期——安全默认)"""
    if not (expires_at or "").strip():
        return False
    try:
        return datetime.fromisoformat(expires_at) <= datetime.now(UTC)
    except (TypeError, ValueError):
        return True


def compute_deep_score(origin_title: str, origin_summary: str,
                       source_meta: dict = None) -> tuple:
    """多模态合规深审评分(确定性规则, LLM 禁入)

    文本轨: 风险词一票否决(直接 0) + 价值观冲突大幅扣分;
    元数据轨: 来源真实性字段缺失扣分。
    Returns:
        (深审分 0-100, 拒绝原因列表)
    """
    text = f"{origin_title or ''} {origin_summary or ''}"
    reasons = []
    score = 100.0
    # 一票否决: 风险词
    for w in RISK_HARD_BLOCK_WORDS:
        if w in text:
            reasons.append(f"风险词命中({w})——一票否决")
            return 0.0, reasons
    # 价值观冲突(即使不违法也拒绝转发)
    for w in VALUES_CONFLICT_WORDS:
        if w in text:
            score -= DEDUP_VALUES_CONFLICT
            reasons.append(f"价值观冲突({w})")
    # 来源元数据(真实性)
    meta = source_meta or {}
    if not meta.get("originUrl"):
        score -= DEDUP_NO_SOURCE_META
        reasons.append("来源 URL 缺失")
    if not meta.get("creatorVerified"):
        score -= DEDUP_NO_SOURCE_META
        reasons.append("创作者身份未验证")
    score = max(0.0, min(100.0, score))
    return round(score, 1), reasons


def classify_deep_review(score: float) -> str:
    """深审三档分流(确定性阈值)"""
    if score >= DEEP_REVIEW_AUTO_LINE:
        return REVIEW_STATUS_AUTO
    if score >= DEEP_REVIEW_MANUAL_LINE:
        return REVIEW_STATUS_MANUAL
    return REVIEW_STATUS_REJECTED


def build_curation_note(origin_title: str, hook_name: str) -> str:
    """策展说明(确定性模板——"为什么我们推荐这条内容")"""
    return (f"为什么我们推荐这条内容: 「{origin_title}」与我们"
            f"核实过的实用价值高度契合。本站在获得原作者授权后"
            f"进行合规转发, 并叠加「{hook_name}」视角为你关联"
            "站内信值权益。内容真实性已按授权链核验。")


def build_source_label(creator_name: str, platform_auth: str
                       ) -> str:
    """强制性来源标注(禁止任何去标识/模糊化)"""
    return f"转自@{creator_name}(授权类型:{platform_auth})"


class BloggerFwdService:
    """40号 P6e·自主合规转发引擎(确权/深审/二创/分润/应急)"""

    def __init__(self, repo: BloggerRepository = None,
                 blogger_service: BloggerService = None):
        self.repo = repo if repo is not None else BloggerRepository()
        self.svc = (blogger_service if blogger_service is not None
                    else BloggerService())

    # ============================================================
    # 1. 智能权利确权层(双重授权)
    # ============================================================

    async def register_auth(self, source_key: str,
                            platform_auth: str,
                            creator_name: str,
                            contact_channel: str,
                            grantor: str,
                            scope: str = "非商用转发",
                            revenue_share: float = 0.3,
                            expires_at: str = "") -> dict:
        """转发双重授权登记(平台+原作者, 哈希存证)

        第一重·平台授权: CC 协议/MCN 合约/官方白名单;
        第二重·原作者授权: 转发邀约(附分润条款)获明确同意。

        Raises:
            ValueError: 参数非法 / 分润比例越界 / 重复登记
        """
        if not (source_key or "").strip():
            raise ValueError("来源键不能为空(作品唯一标识)")
        if platform_auth not in PLATFORM_AUTH_KINDS:
            raise ValueError(
                f"平台授权类型无效({platform_auth}, "
                f"须为{'/'.join(PLATFORM_AUTH_KINDS)})")
        if not (creator_name or "").strip() \
                or not (grantor or "").strip():
            raise ValueError("创作者名称与授权方必填")
        if not (contact_channel or "").strip():
            raise ValueError("联系方式必填(邀约留痕)")
        share = float(revenue_share)
        if not 0.0 <= share <= 1.0:
            raise ValueError("分润比例须在 [0,1]")
        existing = await self.repo.find_fwd_auth_by_source(
            source_key.strip())
        if existing is not None:
            raise ValueError(
                f"该来源已有授权登记(authId={existing['authId']}"
                f", 状态{existing.get('status')})——勿重复")
        auth_id = await self.repo.next_id("auth")
        record = {
            "authId": auth_id,
            "sourceKey": source_key.strip(),
            "platformAuth": platform_auth,
            "creatorName": creator_name.strip(),
            "contactChannel": contact_channel.strip(),
            "grantor": grantor.strip(),
            "scope": scope,
            "revenueShare": share,
            "expiresAt": expires_at,
            "status": AUTH_STATUS_ACTIVE,
            "evidenceHash": _evidence_hash({
                "sourceKey": source_key.strip(),
                "platformAuth": platform_auth,
                "creatorName": creator_name.strip(),
                "grantor": grantor.strip(),
                "scope": scope, "revenueShare": share,
                "expiresAt": expires_at}),
            "createdAt": _now_iso(),
            "revokedAt": "",
        }
        return await self.repo.save_fwd_auth(record)

    async def _require_active_auth(self, auth_id: int) -> dict:
        """双重授权在役校验(转发前置硬门——红线)

        Raises:
            KeyError: 授权不存在
            ValueError: 已撤回 / 已过期
        """
        auth = await self.repo.get_fwd_auth(auth_id)
        if auth is None:
            raise KeyError(f"授权不存在(authId={auth_id})")
        if auth.get("status") != AUTH_STATUS_ACTIVE:
            raise ValueError(
                f"授权已撤回(当前{auth.get('status')})——"
                "未获双重授权的内容绝不转发(红线)")
        if _expired(auth.get("expiresAt")):
            raise ValueError(
                f"授权已过期(expiresAt={auth.get('expiresAt')})——"
                "僵尸转发被拒绝")
        return auth

    async def revoke_auth(self, auth_id: int) -> dict:
        """授权撤回(关联转发内容秒级下架——应急铁律)

        Raises:
            KeyError: 授权不存在
            ValueError: 授权已非在役
        """
        auth = await self.repo.get_fwd_auth(auth_id)
        if auth is None:
            raise KeyError(f"授权不存在(authId={auth_id})")
        if auth.get("status") != AUTH_STATUS_ACTIVE:
            raise ValueError(
                f"授权已非在役(当前{auth.get('status')})")
        auth = await self.repo.update_fwd_auth(auth_id, {
            "status": AUTH_STATUS_REVOKED,
            "revokedAt": _now_iso()})
        # 秒级下架: 该授权的全部已发布转发内容
        contents = await self.repo.list_fwd_contents(
            auth_id=auth_id, publish_status=PUBLISH_STATUS_PUBLISHED,
            limit=10000)
        taken_down = 0
        for c in contents:
            await self.repo.update_fwd_content(
                c["fwdId"], {
                    "publishStatus": PUBLISH_STATUS_TAKEDOWN,
                    "takedownAt": _now_iso(),
                    "takedownReason": "授权撤回——秒级下架"})
            taken_down += 1
        return {"auth": auth, "takenDown": taken_down}

    # ============================================================
    # 2. 多模态合规深审层 + 3. 增值二创与溯源层
    # ============================================================

    async def deep_review(self, auth_id: int, origin_title: str,
                          origin_summary: str = "",
                          source_meta: dict = None) -> dict:
        """多模态合规深审 + 二创增值(一次完成三档分流)

        流程: 双重授权校验(前置硬门) → 深审评分(确定性规则)
        → 三档分流 → 通过档生成策展说明+信值钩子+来源标注
        (溯源三件套, 缺失即拒绝) → 转发内容入库(published)。

        Raises:
            KeyError: 授权不存在
            ValueError: 授权失效 / 深审拒绝 / 溯源缺失
        """
        auth = await self._require_active_auth(auth_id)
        if not (origin_title or "").strip():
            raise ValueError("原内容标题不能为空")
        score, reasons = compute_deep_score(
            origin_title, origin_summary, source_meta)
        review_status = classify_deep_review(score)
        if review_status == REVIEW_STATUS_REJECTED:
            raise ValueError(
                f"深审拒绝(分{score} < {DEEP_REVIEW_MANUAL_LINE}): "
                + "; ".join(reasons))
        # 信值钩子(P5b 钩子库复用——按内容主题确定性匹配)
        hook_id, hook_name = self._match_hook(origin_title)
        # 溯源三件套(策展说明/钩子/来源标注)
        curation = build_curation_note(origin_title, hook_name)
        source_label = build_source_label(
            auth.get("creatorName", ""),
            auth.get("platformAuth", ""))
        fwd_id = await self.repo.next_id("fwd")
        content = {
            "fwdId": fwd_id,
            "authId": auth_id,
            "sourceKey": auth.get("sourceKey", ""),
            "platform": auth.get("platform", "")
            or (source_meta or {}).get("platform", ""),
            "originTitle": origin_title.strip(),
            "deepScore": score,
            "reviewStatus": review_status,
            "reviewReasons": reasons,
            "hook": hook_name,
            "curationNote": curation,
            "sourceLabel": source_label,
            "publishStatus": (PUBLISH_STATUS_PUBLISHED
                              if review_status == REVIEW_STATUS_AUTO
                              else "pending_manual"),
            "playCount": 0,
            "convertCount": 0,
            "revenueProposals": [],
            "createdAt": _now_iso(),
            "takedownAt": "",
            "takedownReason": "",
        }
        return await self.repo.save_fwd_content(content)

    @staticmethod
    def _match_hook(origin_title: str) -> tuple:
        """信值钩子匹配(确定性——标题关键词→P5b 钩子映射)"""
        from services.blogger_auto_create_service import HOOK_NAMES
        title = origin_title or ""
        if any(k in title for k in ("预算", "省钱", "学生",
                                    "性价比", "平价")):
            hook = "hook_price_anchor"
        elif any(k in title for k in ("聚会", "周末", "餐桌",
                                      "家宴")):
            hook = "hook_scene_grass"
        elif any(k in title for k in ("老爸", "父母", "长辈",
                                      "陪伴")):
            hook = "hook_emotional"
        elif any(k in title for k in ("送礼", "礼盒", "面子",
                                      "商务")):
            hook = "hook_gift_face"
        else:
            hook = "hook_tasting_pro"
        return hook, HOOK_NAMES.get(hook, hook)

    # ============================================================
    # 4. 利益分配层(分润建议——永不自动)
    # ============================================================

    async def report_fwd_metrics(self, fwd_id: int,
                                 play_count: int = None,
                                 convert_count: int = None
                                 ) -> dict:
        """转发效果上报(平台回执落地/测试轨; 滚动合并)

        Raises:
            KeyError: 转发内容不存在
        """
        content = await self.repo.get_fwd_content(fwd_id)
        if content is None:
            raise KeyError(f"转发内容不存在(fwdId={fwd_id})")
        fields = {}
        if play_count is not None:
            fields["playCount"] = max(
                int(content.get("playCount") or 0),
                int(play_count))
        if convert_count is not None:
            fields["convertCount"] = max(
                int(content.get("convertCount") or 0),
                int(convert_count))
        return await self.repo.update_fwd_content(fwd_id, fields)

    async def propose_revenue(self, fwd_id: int) -> dict:
        """生成分润结算建议书(pending——人工审批后方可给付)

        铁律: 分润永不自动——本方法仅生成建议书并留痕,
        给付须 admin 在审批面 approve。

        Raises:
            KeyError: 转发内容不存在
            ValueError: 内容已下架 / 无可分润效果 / 已有待审建议
        """
        content = await self.repo.get_fwd_content(fwd_id)
        if content is None:
            raise KeyError(f"转发内容不存在(fwdId={fwd_id})")
        if content.get("publishStatus") == PUBLISH_STATUS_TAKEDOWN:
            raise ValueError(
                f"内容已下架(不可分润, 当前{content.get(
                    'publishStatus')})")
        plays = int(content.get("playCount") or 0)
        converts = int(content.get("convertCount") or 0)
        if plays <= 0 and converts <= 0:
            raise ValueError("无可分润效果(播放/转化为零)")
        proposals = content.get("revenueProposals") or []
        if any(p.get("status") == REVENUE_PENDING
               for p in proposals):
            raise ValueError("已有待审分润建议(先处置再提交)")
        auth = await self.repo.get_fwd_auth(
            int(content.get("authId") or 0)) or {}
        share = float(auth.get("revenueShare") or 0)
        # 基础分润(播放) + 转化分润(转化)
        base_amount = round(plays * 0.01 * share, 2)
        convert_amount = round(converts * 2.0 * share, 2)
        proposal = {
            "proposalId": len(proposals) + 1,
            "plays": plays,
            "converts": converts,
            "baseAmount": base_amount,
            "convertAmount": convert_amount,
            "totalAmount": round(base_amount + convert_amount, 2),
            "status": REVENUE_PENDING,
            "createdAt": _now_iso(),
            "approvedAt": "",
        }
        proposals.append(proposal)
        return await self.repo.update_fwd_content(
            fwd_id, {"revenueProposals": proposals})
