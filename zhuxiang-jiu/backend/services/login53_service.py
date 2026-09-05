"""53号·小竹智能登录引擎 服务层(login53_service)

P0 范围(计划 §九 P0):
    - 角色四态判定引擎(new/active/dormant/high_risk
      ——档案+登录史+风控标记聚合)
    - 态势感知(Pre-Login AI): 行为基线指纹匹配
      (>95%→静默/一键)+意图预判标签+隐私预算预检
      (49号只读探针)
    - 价值钩子生成(登录前投放——45/50号只读聚合)

off 语义:
    LOGIN53_MODE=off → 编排面关闭(直通存量 39号
    entry 登录——零接管); registry/查询观测面不受影响。
"""

import logging
import math

from core.helpers import ts

from repositories.login53_repository import (
    Login53Repository,
)
from services.login53_registry import (
    AUTH_CHANNELS, PORTAL_STATES, current_mode,
    registry_view,
)
from services.login53_scripts import render_script

logger = logging.getLogger("login53_service")

# 态势感知阈值(行为基线匹配度——mock 指纹通道)
BASELINE_MATCH_SILENT = 0.95
BASELINE_MATCH_ONE_TAP = 0.70

# 意图预判标签集(登录后直达页映射)
INTENT_PAGES = {
    "shopping": "商品列表页",
    "trust_check": "信值报告页",
    "repair": "修复任务页",
    "social": "社区动态页",
    "browse": "首页(随便逛逛)",
}


class Login53Service:
    """53号智能登录引擎服务(P0: 四态+态势感知)"""

    def __init__(self):
        self.repo = Login53Repository()

    # --------------------------------------------------------
    # 注册表视图(观测面——不受开关影响)
    # --------------------------------------------------------

    @staticmethod
    def registry() -> dict:
        return registry_view()

    # --------------------------------------------------------
    # 角色四态判定引擎
    # --------------------------------------------------------

    async def resolve_portal_state(
            self, member_id: int) -> dict:
        """角色四态判定(档案+登录史+风控标记聚合)

        判定优先级(互斥):
            high_risk(43号风控标记) > dormant(>30天)
            > new(注册<7天或无登录史) > active

        判定后写回档案(状态迁移留痕——lastStateAt)。
        """
        profile = await self.repo.get_profile(member_id)
        now = ts()

        # 43号风控标记(高危最优先——安全兜底)
        high_risk = bool(profile and
                         (profile.get("riskFlagged")
                          or profile.get("riskFlagged")
                          == 1))
        # 登录史(53号事件+39号设备档案双源)
        last_login_at = str(
            (profile or {}).get("lastLoginAt") or "")
        days_since = self._days_between(
            last_login_at, now)
        # 账龄(会员注册时间)
        account_age_days = \
            await self._account_age_days(member_id)

        if high_risk:
            state = "high_risk"
        elif last_login_at and days_since > 30:
            state = "dormant"
        elif (not last_login_at
              or account_age_days < 7):
            state = "new"
        else:
            state = "active"

        # 档案写回(首次建档+状态迁移留痕)
        record = dict(profile or {})
        record.update({
            "memberId": member_id,
            "portalState": state,
            "lastStateAt": now,
            "lastLoginAt": last_login_at
            or record.get("lastLoginAt", ""),
            "accountAgeDays": account_age_days,
            "daysSinceLogin": days_since
            if last_login_at else -1,
        })
        if (profile or {}).get("portalState") != state:
            record["stateChangedAt"] = now
        await self.repo.save_profile(record)

        meta = PORTAL_STATES[state]
        return {
            "memberId": member_id,
            "portalState": state,
            "stateLabel": meta["label"],
            "criteria": meta["criteria"],
            "portal": meta["portal"],
            "hook": meta["hook"],
            "daysSinceLogin":
                days_since if last_login_at else None,
            "accountAgeDays": account_age_days,
        }

    @staticmethod
    def _days_between(from_ts: str,
                      to_ts: str) -> int:
        """两 ISO 时间戳间隔天数(空值 999——沉睡兜底)"""
        if not from_ts:
            return 999
        try:
            from datetime import datetime
            f = datetime.fromisoformat(
                str(from_ts)[:19])
            t = datetime.fromisoformat(
                str(to_ts)[:19])
            return abs((t - f).days)
        except ValueError:
            return 999

    @staticmethod
    async def _account_age_days(member_id: int) -> int:
        """会员账龄(注册时间→今天; 查不到 365 兜底)"""
        try:
            from repositories.member_repository import (
                MemberRepository,
            )
            member = await MemberRepository().get_by_id(
                member_id)
            if member and member.get("created_at"):
                return Login53Service._days_between(
                    str(member["created_at"]), ts())
        except Exception as exc:  # noqa: BLE001
            logger.warning("login53_account_age_failed "
                           "%s: %s", member_id, exc)
        return 365

    # --------------------------------------------------------
    # 态势感知(Pre-Login AI)
    # --------------------------------------------------------

    async def prelogin_sense(self, member_id: int,
                             fingerprint: str = "",
                             visit_source: str = "",
                             hour: int = None) -> dict:
        """登录前态势感知(计划 §九 P0):
        行为基线匹配→静默/一键/常规 + 意图预判
        +隐私预算预检(49号只读探针)

        - 基线匹配: 档案指纹摘要 vs 当前指纹
      (mock 通道——确定性哈希相似度, >95% 静默/
        >70% 一键/其余常规)
        - 意图预判: 访问时段+来源标签
        - 预算预检: 49号剩余预算只读探针
          (不足→提前提示+调整入口——避免登录后挫败)
        """
        mode = current_mode()
        if mode != "on":
            raise ValueError(
                f"LOGIN53_MODE={mode}(默认 off——"
                f"编排面关闭, 直通存量 39号登录)")

        profile = await self.repo.get_profile(member_id)
        baseline_fp = str(
            (profile or {}).get("baselineFingerprint")
            or "")

        # ① 行为基线匹配(mock 指纹通道——确定性
        #    相似度, 无真实采集)
        match = self._fingerprint_match(
            baseline_fp, fingerprint)
        if match >= BASELINE_MATCH_SILENT:
            auth_level = "silent"
        elif match >= BASELINE_MATCH_ONE_TAP:
            auth_level = "one_tap"
        else:
            auth_level = "regular"

        # ② 意图预判(时段+来源——规则标签)
        intent = self._predict_intent(
            hour, visit_source, profile)

        # ③ 隐私预算预检(49号只读探针——
        #    不扣减只读取; 读取失败 fail-soft)
        budget = await self._budget_probe(member_id)

        # ④ 推荐通道(风险感知排序——预算充足优先
        #    生物; 不足则零成本通道)
        recommended = self._recommend_channels(
            budget)

        return {
            "memberId": member_id,
            "authLevel": auth_level,
            "baselineMatch": match,
            "intent": intent,
            "intentPage": INTENT_PAGES.get(
                intent, INTENT_PAGES["browse"]),
            "budget": budget,
            "recommendedChannels": recommended,
            "preloginAt": ts(),
        }

    @staticmethod
    def _fingerprint_match(baseline: str,
                           current: str) -> float:
        """指纹匹配度(mock 通道——确定性口径)

        基线为空(首次设备)=0.0(常规);
        完全一致=1.0; 部分一致=前缀重合度
        (mock 指纹为十六进制摘要——位置重合率)。
        """
        if not baseline or not current:
            return 0.0
        if baseline == current:
            return 1.0
        n = min(len(baseline), len(current))
        if n == 0:
            return 0.0
        hits = sum(1 for i in range(n)
                   if baseline[i] == current[i])
        return round(hits / n, 4)

    @staticmethod
    def _predict_intent(hour: int, source: str,
                        profile: dict | None) -> str:
        """意图预判(规则标签——时段+来源+历史)

        时段规则: 6-11 信任核查晨检/11-14 购物
        /14-18 浏览/18-23 社交; 来源链接显式
        标签优先; 历史高频意图兜底。
        """
        if source in INTENT_PAGES:
            return source
        top_intent = (profile or {}).get("topIntent")
        if hour is None:
            return top_intent or "browse"
        if 6 <= hour < 11:
            return "trust_check"
        if 11 <= hour < 14:
            return "shopping"
        if 14 <= hour < 18:
            return top_intent or "browse"
        return "social"

    @staticmethod
    async def _budget_probe(member_id: int) -> dict:
        """49号隐私预算只读探针(不扣减)

        读取当日剩余预算; 无账户(未用过语音面)
        =满额 1.0; 异常 fail-soft(不阻断态势感知)。
        """
        try:
            from repositories.xiaozhu_repository import (
                Xiaozhu48Repository,
            )
            xrepo = Xiaozhu48Repository()
            budget = await xrepo.get_privacy_budget(
                member_id)
            if budget is None:
                return {"remaining": 1.0,
                        "dayKey": ts()[:10],
                        "firstUse": True}
            remaining = round(
                1.0 - float(
                    budget.get("usedToday") or 0.0), 4)
            return {"remaining": max(0.0, remaining),
                    "dayKey": budget.get("dayKey")
                    or "",
                    "firstUse": False}
        except Exception as exc:  # noqa: BLE001
            logger.warning("login53_budget_probe_"
                           "failed %s: %s", member_id, exc)
            return {"remaining": None, "error":
                    "probe_failed",
                    "note": "预算探针 fail-soft"}

    @staticmethod
    def _recommend_channels(budget: dict) -> list[str]:
        """通道推荐(预算感知排序)

        预算充足(≥0.05): 生物优先(voice/face/
        passkey); 不足: 零成本通道(qr/passkey)。
        """
        remaining = budget.get("remaining")
        if remaining is None or remaining >= 0.05:
            return ["voice", "face", "passkey",
                    "qr", "fingerprint"]
        return ["passkey", "qr"]

    # --------------------------------------------------------
    # 价值钩子生成(登录前投放)
    # --------------------------------------------------------

    async def generate_hook(self,
                            member_id: int,
                            script_key: str = None,
                            params: dict = None) -> dict:
        """价值钩子生成(登录前投放——
        45号信值+50号积分+待办只读聚合)

        认证完成前投放话术钩子(如"您昨日志愿服务
        的信值已到账")——等待时间转化为价值感知时间。
        """
        mode = current_mode()
        if mode != "on":
            raise ValueError(
                f"LOGIN53_MODE={mode}(默认 off——"
                f"编排面关闭)")

        # 钩子素材聚合(45/50号只读——fail-soft)
        hook_data = await self._collect_hook_data(
            member_id)
        # 显式 params 优先于聚合素材(调用方指定胜出)
        render_params = dict(hook_data)
        render_params.update(params or {})

        # 默认钩子话术(唤醒即认证场景)
        key = script_key or "wake_login"
        script = render_script(key, render_params)
        return {
            "memberId": member_id,
            "script": script,
            "hookData": hook_data,
            "generatedAt": ts(),
        }

    @staticmethod
    async def _collect_hook_data(
            member_id: int) -> dict:
        """钩子素材(45号信值+50号积分+待办——
        全部 fail-soft 只读聚合)"""
        data = {"nickname": "用户", "score": "—",
                "delta": "持平", "taskCount": "0"}
        # 昵称(会员档案)
        try:
            from repositories.member_repository import (
                MemberRepository,
            )
            member = await MemberRepository().get_by_id(
                member_id)
            if member:
                data["nickname"] = (
                    member.get("nickname")
                    or "用户")
        except Exception as exc:  # noqa: BLE001
            logger.warning("login53_hook_member_"
                           "failed %s: %s", member_id,
                           exc)
        # 信值分(45号经 48号绑定桥)
        try:
            from repositories.xiaozhu_repository import (
                Xiaozhu48Repository,
            )
            xrepo = Xiaozhu48Repository()
            binding = await xrepo.get_binding(member_id)
            if binding:
                from repositories.trust_value_repository \
                    import TrustValue45Repository
                trust_id = int(
                    binding.get("trustId") or 0)
                profile45 = await \
                    TrustValue45Repository().get_profile(
                        trust_id)
                if profile45:
                    data["score"] = str(
                        profile45.get("score")
                        or "—")
        except Exception as exc:  # noqa: BLE001
            logger.warning("login53_hook_trust_"
                           "failed %s: %s", member_id,
                           exc)
        return data

    # --------------------------------------------------------
    # 基线指纹登记(态势感知数据源)
    # --------------------------------------------------------

    async def register_baseline_fingerprint(
            self, member_id: int,
            fingerprint: str) -> dict:
        """登记/刷新行为基线指纹(登录成功后调用——
        下次态势感知的匹配参照)"""
        profile = await self.repo.get_profile(member_id)
        record = dict(profile or {})
        record.update({
            "memberId": member_id,
            "baselineFingerprint": fingerprint,
            "baselineUpdatedAt": ts(),
        })
        await self.repo.save_profile(record)
        return {"success": True,
                "memberId": member_id,
                "baselineUpdatedAt":
                    record["baselineUpdatedAt"]}
