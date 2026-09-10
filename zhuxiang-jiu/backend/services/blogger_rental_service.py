"""40号 P6f-1·引流能力开放服务(设计文档《40号 P6f 规划方案》§3)

超级会员(tier≥pro)经既有 API 网关租用 AI 引流员:
    选题推荐(只读) → 脚本生成(受控写, 复用 P6b 全链) →
    渲染(mock 轨; real 须 admin 审批) → 作品/漏斗(仅自己)

架构口径:
    - 开放最小侵入: 不新建网关——ApiKeyService/中间件/限流原样
      复用, /open/av/* 只是新的受保护路由前缀
    - ownerId 命名空间隔离: 租用脚本/作品带 memberId 贯穿,
      会员仅可见自己的作品与漏斗(平台自营=0)
    - 租用账本: 每次受控写记 ledger(endpoint×units);
      计费单位: 脚本=1 / mock 渲染=5 / real 渲染=50(双闸审批)
    - 租金建议书: 月度聚合(单位数×单价)→pending——
      admin approve 后从会员信值扣减(47号), 永不自动
    - 降级保护: pause(全局自治暂停)时开放端点组降级为只读
      (选题/漏斗可看, 生成/渲染暂停——观测不中断, 行为冻结)

红线(宪法域):
    - 租用资格: tier≥pro + API Key active(双门槛)
    - 隔离: 作品/漏斗仅自己可见(越权访问 404)
    - 租金/扣减永不自动: 建议书 pending → admin approve
    - real 渲染须 admin 审批(成本与合规双闸)
    - pause 降级只读(观测永不关停)
"""

import logging
from datetime import datetime, UTC

from repositories.blogger_repository import (
    BloggerRepository, PLATFORMS, DOMAINS,
)
from services.blogger_service import BloggerService
from services.blogger_av_create_service import (
    BloggerAVCreateService, HOOK_NAMES, PERSONA_TYPES,
)

logger = logging.getLogger(__name__)


# ============================================================
# P6f-1 常量(设计文档 §3.2)
# ============================================================

# 租用资格: API Key tier 须为 pro(超级会员)
RENTAL_REQUIRED_TIER = "pro"

# 计费单位(确定性: 脚本=1 / mock 渲染=5 / real 渲染=50)
UNIT_PRICE_SCRIPT = 1
UNIT_PRICE_RENDER_MOCK = 5
UNIT_PRICE_RENDER_REAL = 50

# 租金建议书状态(永不自动——admin approve 后生效)
BILL_PENDING = "pending"
BILL_APPROVED = "approved"
BILL_REJECTED = "rejected"

# 开放端点组名(账本 endpoint 维度)
EP_TOPICS = "open/av/topics"
EP_SCRIPTS = "open/av/scripts"
EP_RENDERS = "open/av/renders"

# 租用选题池(确定性: 平台×领域热词, 与站内选题口径同源)
_TOPIC_POOL = (
    ("新手选酒避坑指南", "wine"), ("周末家宴配酒攻略", "wine"),
    ("礼盒挑选的门道", "gift"), ("下酒菜搭配心得", "food"),
    ("小酌生活仪式感", "lifestyle"), ("品鉴笔记入门", "wine"),
    ("宴席用酒怎么选", "gift"), ("微醺周末计划", "lifestyle"),
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def month_key_now() -> str:
    """当月键(UTC YYYY-MM——账单聚合口径)"""
    return datetime.now(UTC).strftime("%Y-%m")


class BloggerRentalService:
    """40号 P6f-1·引流能力开放(租用端点组/账本/租金建议书)"""

    def __init__(self, repo: BloggerRepository = None,
                 blogger_service: BloggerService = None):
        self.repo = repo if repo is not None else BloggerRepository()
        self.svc = (blogger_service if blogger_service is not None
                    else BloggerService())

    # ============================================================
    # 1. 租用资格与鉴权(网关前置——路由层调用)
    # ============================================================

    async def require_rental_access(self, member_id: int,
                                    tier: str) -> None:
        """租用资格校验(tier≥pro——开放端点组硬门)

        Raises:
            ValueError: tier 不满足租用门槛
        """
        if tier != RENTAL_REQUIRED_TIER:
            raise ValueError(
                f"租用 AI 引流员须 pro 档 API Key(当前{tier})——"
                "超级会员专属能力")

    async def require_not_paused_readonly(self) -> None:
        """受控写操作前置: pause 时开放端点组降级只读

        Raises:
            ValueError: 全局自治暂停中(降级只读)
        """
        from services.blogger_auto_govern_service import \
            BloggerAutoGovernService
        state = await self.repo.get_autonomy_state()
        if state.get("paused"):
            raise ValueError(
                "全局自治暂停中——开放端点组降级为只读"
                "(选题/漏斗可看, 生成/渲染冻结; 观测不中断)")

    # ============================================================
    # 2. 选题推荐(只读——pause 不受限)
    # ============================================================

    async def recommend_topics(self, member_id: int,
                               platform: str = None,
                               domain: str = None) -> dict:
        """租用选题推荐(确定性: 平台×领域过滤, 热度序)

        Raises:
            ValueError: 平台/领域非法
        """
        if platform and platform not in PLATFORMS:
            raise ValueError(
                f"平台无效({platform}, 须为{'/'.join(PLATFORMS)})")
        if domain and domain not in DOMAINS:
            raise ValueError(
                f"领域无效({domain}, 须为{'/'.join(DOMAINS)})")
        topics = [
            {"topic": t, "domain": d,
             "suggestedHook": HOOK_NAMES.get(
                 "hook_tasting_pro")}
            for t, d in _TOPIC_POOL
            if (not domain or d == domain)
        ]
        return {"memberId": member_id,
                "platform": platform or "all",
                "count": len(topics), "topics": topics}

    # ============================================================
    # 3. 脚本生成(受控写——复用 P6b 全链 + 账本)
    # ============================================================

    async def renter_personas(self, member_id: int) -> list[dict]:
        """租用者人设列表(仅自己的——ownerId 命名空间)"""
        personas = await self.repo.list_personas(limit=1000)
        return [p for p in personas
                if int(p.get("ownerId") or 0) == member_id]

    async def register_renter_persona(self, member_id: int,
                                      name: str,
                                      voice_style: str = "medium",
                                      tone_style: str = "warm"
                                      ) -> dict:
        """租用者人设登记(原创 IP 类; ownerId 隔离)

        Raises:
            ValueError: 参数非法 / 自治暂停(受控写降级)
        """
        await self.require_not_paused_readonly()
        create_svc = BloggerAVCreateService(
            repo=self.repo, blogger_service=self.svc)
        persona = await create_svc.register_persona(
            name, PERSONA_TYPES[0], voice_style=voice_style,
            tone_style=tone_style)
        # ownerId 命名空间标记(租用者人设)
        return await self.repo.update_persona(
            persona["personaId"], {"ownerId": member_id})

    async def generate_rental_script(self, member_id: int,
                                     topic: str, platform: str,
                                     persona_id: int,
                                     hook_type: str,
                                     style: str = "") -> dict:
        """租用脚本生成(复用 P6b 全链合规 + 账本计费)

        隔离: 人设须属于该会员(ownerId 校验)。

        Raises:
            KeyError: 人设不存在(或非本人——越权 404 语义)
            ValueError: 参数/授权/P6b 合规链拒绝 / 暂停降级
        """
        await self.require_not_paused_readonly()
        personas = await self.renter_personas(member_id)
        owned = [p for p in personas
                 if p["personaId"] == persona_id]
        if not owned:
            raise KeyError(
                f"人设不存在或不属于当前会员"
                f"(personaId={persona_id})——租用命名空间隔离")
        create_svc = BloggerAVCreateService(
            repo=self.repo, blogger_service=self.svc)
        script = await create_svc.generate_script(
            topic, platform, persona_id, hook_type,
            style=style)
        # ownerId 标记 + 账本计费(脚本=1 单位)
        script = await self.repo.update_av_script(
            script["scriptId"], {"ownerId": member_id})
        await self._charge(member_id, EP_SCRIPTS,
                           UNIT_PRICE_SCRIPT)
        return script

    # ============================================================
    # 4. 渲染(mock 轨——real 须 admin 审批双闸)
    # ============================================================

    async def render_rental_work(self, member_id: int,
                                  script_id: int,
                                  real: bool = False) -> dict:
        """租用渲染(mock 默认; real 须 admin 预先审批)

        隔离: 脚本须属于该会员。

        Raises:
            KeyError: 脚本不存在(或非本人)
            ValueError: real 未审批 / 暂停降级
        """
        await self.require_not_paused_readonly()
        script = await self.repo.get_av_script(script_id)
        if script is None or int(
                script.get("ownerId") or 0) != member_id:
            raise KeyError(
                f"脚本不存在或不属于当前会员"
                f"(scriptId={script_id})——租用命名空间隔离")
        if real and not script.get("realRenderApproved"):
            raise ValueError(
                "real 渲染须 admin 预先审批(成本与合规双闸)"
                "——租用默认 mock 轨")
        create_svc = BloggerAVCreateService(
            repo=self.repo, blogger_service=self.svc)
        work = await create_svc.render_work(script_id)
        # ownerId 标记 + 账本计费(mock=5 / real=50)
        work = await self.repo.update_av_work(
            work["avWorkId"], {"ownerId": member_id})
        await self._charge(
            member_id, EP_RENDERS,
            UNIT_PRICE_RENDER_REAL if real
            else UNIT_PRICE_RENDER_MOCK)
        return work

    async def admin_approve_real_render(self, script_id: int
                                         ) -> dict:
        """admin 审批 real 渲染轨(成本与合规双闸)

        Raises:
            KeyError: 脚本不存在
        """
        script = await self.repo.get_av_script(script_id)
        if script is None:
            raise KeyError(f"脚本不存在(scriptId={script_id})")
        return await self.repo.update_av_script(script_id, {
            "realRenderApproved": True,
            "realApprovedAt": _now_iso()})

    # ============================================================
    # 5. 作品/漏斗(仅自己——观测面, pause 不受限)
    # ============================================================

    async def renter_works(self, member_id: int,
                           limit: int = 100) -> list[dict]:
        """租用者作品列表(仅自己的——ownerId 隔离)"""
        works = await self.repo.list_av_works(limit=10000)
        return [w for w in works
                if int(w.get("ownerId") or 0) == member_id
                ][:limit]

    async def renter_funnel(self, member_id: int) -> dict:
        """租用者六层漏斗(仅自己作品的聚合——P6d 口径)"""
        works = await self.renter_works(member_id, limit=10000)

        def _m(w: dict, key: str) -> float:
            return float((w.get("metrics") or {}).get(key) or 0)

        def _with(key: str) -> int:
            return sum(1 for w in works if _m(w, key) > 0)

        return {
            "memberId": member_id,
            "works": len(works),
            "withExposure": _with("exposures"),
            "withCompletion": _with("completionRate"),
            "withClicks": _with("clicks"),
            "withRegistered": _with("registered"),
            "withActivated": _with("activated"),
            "withOrdered": _with("ordered"),
            "totals": {
                "exposures": int(sum(
                    _m(w, "exposures") for w in works)),
                "clicks": int(sum(
                    _m(w, "clicks") for w in works)),
                "registered": int(sum(
                    _m(w, "registered") for w in works)),
                "activated": int(sum(
                    _m(w, "activated") for w in works)),
                "ordered": int(sum(
                    _m(w, "ordered") for w in works))}}

    async def report_rental_metrics(self, member_id: int,
                                    work_id: int,
                                    **fields) -> dict:
        """租用者指标上报(仅自己作品; 滚动合并 P6c 口径)

        Raises:
            KeyError: 作品不存在(或非本人)
            ValueError: 率字段越界
        """
        work = await self.repo.get_av_work(work_id)
        if work is None or int(
                work.get("ownerId") or 0) != member_id:
            raise KeyError(
                f"作品不存在或不属于当前会员"
                f"(avWorkId={work_id})——租用命名空间隔离")
        from services.blogger_av_publish_service import \
            BloggerAVPublishService
        pub = BloggerAVPublishService(
            repo=self.repo, blogger_service=self.svc)
        return await pub.report_av_work_metrics(work_id, **fields)

    # ============================================================
    # 6. 租用账本与租金建议书(永不自动)
    # ============================================================

    async def _charge(self, member_id: int, endpoint: str,
                      units: int) -> None:
        """受控写计费(账本留痕——计费单位确定性映射)"""
        ledger_id = await self.repo.next_id("ledger")
        await self.repo.save_rental_entry({
            "ledgerId": ledger_id,
            "memberId": member_id,
            "endpoint": endpoint,
            "units": units,
            "createdAt": _now_iso()})

    async def generate_rental_bill(self, member_id: int,
                                   unit_price: float = 0.1,
                                   month_key: str = None
                                   ) -> dict:
        """生成月度租金建议书(pending——永不自动)

        聚合: 当月账本(脚本单位+渲染单位)×单价;
        admin approve 后从会员信值扣减(47号)。

        Raises:
            ValueError: 无用量 / 已有当月待审建议书
        """
        mk = month_key or month_key_now()
        entries = await self.repo.list_rental_entries(
            member_id=member_id, month_key=mk, limit=5000)
        if not entries:
            raise ValueError(
                f"会员 {member_id} 当月({mk})无租用用量——"
                "无可结算账单")
        bills = await self.repo.list_rental_bills(
            member_id=member_id, limit=1000)
        if any(b.get("status") == BILL_PENDING
               and b.get("monthKey") == mk for b in bills):
            raise ValueError(
                f"当月({mk})已有待审租金建议书——先处置再生成")
        script_units = sum(int(e.get("units") or 0) for e in entries
                           if e.get("endpoint") == EP_SCRIPTS)
        render_units = sum(int(e.get("units") or 0) for e in entries
                           if e.get("endpoint") == EP_RENDERS)
        total_units = script_units + render_units
        amount = round(total_units * float(unit_price), 2)
        bill_id = await self.repo.next_id("bill")
        bill = {
            "billId": bill_id,
            "memberId": member_id,
            "monthKey": mk,
            "scriptUnits": script_units,
            "renderUnits": render_units,
            "totalUnits": total_units,
            "unitPrice": float(unit_price),
            "amount": amount,
            "status": BILL_PENDING,
            "note": f"月度租用账单({mk}): 脚本{script_units}单位"
                    f"+渲染{render_units}单位={total_units}单位"
                    f"×{unit_price}={amount}信值",
            "createdAt": _now_iso(),
            "approvedAt": "",
        }
        return await self.repo.save_rental_bill(bill)

    async def approve_rental_bill(self, bill_id: int) -> dict:
        """admin 审批租金建议书(approve——信值扣减经 47号)

        铁律: 本方法标记审批状态; 信值实际扣减由 admin 在
        47号侧执行(审批留痕两段式——本站建议书制口径)。

        Raises:
            KeyError: 建议书不存在
            ValueError: 非待审状态
        """
        bill = await self.repo.get_rental_bill(bill_id)
        if bill is None:
            raise KeyError(f"租金建议书不存在(billId={bill_id})")
        if bill.get("status") != BILL_PENDING:
            raise ValueError(
                f"建议书已处置(当前{bill.get('status')})")
        return await self.repo.update_rental_bill(bill_id, {
            "status": BILL_APPROVED,
            "approvedAt": _now_iso(),
            "note": bill.get("note", "")
                    + " | 已批准: 信值扣减经 47号执行"})

    async def reject_rental_bill(self, bill_id: int) -> dict:
        """admin 驳回租金建议书

        Raises:
            KeyError: 建议书不存在
            ValueError: 非待审状态
        """
        bill = await self.repo.get_rental_bill(bill_id)
        if bill is None:
            raise KeyError(f"租金建议书不存在(billId={bill_id})")
        if bill.get("status") != BILL_PENDING:
            raise ValueError(
                f"建议书已处置(当前{bill.get('status')})")
        return await self.repo.update_rental_bill(bill_id, {
            "status": BILL_REJECTED})
