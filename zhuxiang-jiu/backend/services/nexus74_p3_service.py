"""74号·NexusFlow(智枢·流)AI智能全域发布
大模型 P3 发布编排与自愈服务
(nexus74_p3_service)

规划(docs/74号_NexusFlow智枢流_AI智能全域
发布大模型_创新规划方案.md §三 ④/§六 P3):
    ① 发布编排(三级适配器诚实工程):
       A 档=微信公众号 API 直连(无凭证时
       归因 auth_expired——不伪造成功);
       B 档=五平台半自动(适配包+人工操作
       +回执登记——数据诚实铁律);
       C 档=预留
    ② 自愈重试(错误归因分级: 内容违规
       不可自动重试/接口临时故障指数
       退避 60→120→240/凭证缺失换档建议;
       重试上限 3——满后建议换 B 档人工)
    ③ B 档回执登记(成功/驳回/限流——
       状态机 awaiting_manual→终态)
    ④ 频次封顶(单平台每日 3 篇——
       打扰保护)+静默窗(北京时间夜间
       免打扰, 可配置)
    ⑤ 配额看板+适配器健康(观测面)

铁律(规划 §九):
    - 发布显式性: B 档平台操作永远
      人工确认(full 档亦然); auto 自主
      仅 full 档 A 档低风险域(compliance
      pass 才可自主)
    - 合规前置: block/legal_risk 永不
      进入发布队列(adapt 已拦截)
    - 数据诚实: B 档回执未登记视为
      未发布
    - 影子期留痕不派发(前置保护不适用)
    - 发布/重试=决策面(off 409);
      回执登记=数据诚实(不受 MODE)

异常约定(71号口径):
    KeyError → 404(源/适配/发布不存在)
    ValueError → 409(参数/状态机/门控)
"""

import logging
import os
from datetime import datetime, timedelta, \
    timezone, UTC

from core.helpers import ts

from repositories.nexus74_repository import (
    Nexus74Repository,
)
from services.nexus74_registry import (
    ADAPTER_TIERS,
    DAILY_PUBLISH_CAP,
    ERROR_KINDS,
    MAX_PUBLISH_RETRY,
    PLATFORMS, PLATFORM_NAMES,
    PUBLISH_STATES,
    RECEIPT_RESULTS,
    RETRY_BACKOFF_BASE,
    SILENCE_HOURS_DEFAULT,
    TIER_ADVICE,
    current_mode,
)

logger = logging.getLogger("nexus74_p3_service")

_BJT = timezone(timedelta(hours=8))
_WECHAT_DRYRUN_ENV = "NEXUS74_WECHAT_DRYRUN"
_WECHAT_FAILSIM_ENV = "NEXUS74_WECHAT_FAILSIM"
_RETRIABLE_KINDS = ("api_transient",
                    "quota_exceeded")


def _bj_now(now: str) -> datetime:
    """北京时间解析(空→当前; 非法→409)"""
    raw = (now or "").strip()
    if not raw:
        return datetime.now(_BJT)
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            f"时刻格式非法(ISO 8601): {raw}"
        ) from exc
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_BJT)


def _bj_day(now: str) -> str:
    return _bj_now(now).strftime("%Y-%m-%d")


def _bj_hour(now: str) -> int:
    return _bj_now(now).hour


def _at(now: str) -> str:
    """时刻落库(注入 now→确定性 UTC ISO;
    空→真实 ts——生产口径)"""
    raw = (now or "").strip()
    if not raw:
        return ts()
    return _bj_now(raw) \
        .astimezone(UTC).isoformat()


class Nexus74P3Service:
    """74号 P3 发布编排与自愈"""

    def __init__(self):
        self.repo = Nexus74Repository()

    # ============================================================
    # 静默窗(北京时间——可配置)
    # ============================================================

    async def get_silence(self) -> dict:
        """静默窗配置(观测面)"""
        rec = await self.repo.get_silence()
        if not rec:
            return {
                "enabled": True,
                "hours": list(
                    SILENCE_HOURS_DEFAULT),
                "updatedAt": "",
                "default": True,
            }
        rec.setdefault("default", False)
        return rec

    async def set_silence(self,
                          enabled: bool,
                          hours: list) -> dict:
        """静默窗设置(0-23 时段)

        Raises:
            ValueError: 时段域外
        """
        if not isinstance(hours, list) \
                or not all(
                    isinstance(h, int)
                    and 0 <= h <= 23
                    for h in hours):
            raise ValueError(
                "静默时段须为 0-23 整数列表")
        record = {
            "id": "default",
            "enabled": bool(enabled),
            "hours": sorted(set(hours)),
            "updatedAt": ts(),
        }
        await self.repo.save_silence(record)
        return record

    async def _in_silence(self,
                          now: str) -> bool:
        s = await self.get_silence()
        hours = set(s.get("hours") or [])
        return bool(s.get("enabled")) \
            and _bj_hour(now) in hours

    # ============================================================
    # 每日封顶(单平台——打扰保护)
    # ============================================================

    async def _count_published_today(
            self, platform: str,
            now: str) -> int:
        day = _bj_day(now)
        pubs = await self.repo \
            .list_publications(limit=500)
        count = 0
        for p in pubs:
            if p.get("platform") == platform \
                    and p.get("status") \
                    == "published":
                at = p.get("publishedAt") \
                    or ""
                if at and _bj_day(at) == day:
                    count += 1
        return count

    # ============================================================
    # A 档适配器(微信直连——诚实工程)
    # ============================================================

    def _adapter_a_wechat(
            self, publication_id: int) -> dict:
        """A 档微信直连(无凭证→auth_expired
        归因——不伪造成功; DRYRUN=演练;
        FAILSIM=测试注入)"""
        sim = os.environ.get(
            _WECHAT_FAILSIM_ENV, "")
        if sim in ERROR_KINDS:
            return {
                "ok": False,
                "kind": sim,
                "message": (
                    "失败模拟("
                    f"{_WECHAT_FAILSIM_ENV}"
                    f"={sim})"),
            }
        if os.environ.get(
                _WECHAT_DRYRUN_ENV) == "1":
            return {
                "ok": True,
                "externalId": (
                    "wx-dryrun-"
                    f"{publication_id}"),
            }
        return {
            "ok": False,
            "kind": "auth_expired",
            "message": (
                "微信公众号 API 凭证未配置"
                f"({_WECHAT_DRYRUN_ENV}=1 "
                "可演练直连链路)"),
        }

    # ============================================================
    # ① 发布编排(三级适配器——决策面)
    # ============================================================

    @staticmethod
    def _build_package(
            adaptation: dict,
            shadow: bool) -> dict:
        """B 档人工操作适配包"""
        return {
            "title": adaptation.get(
                "title", ""),
            "summary": adaptation.get(
                "summary", ""),
            "tags": adaptation.get(
                "tags", []),
            "talkingPoints":
                adaptation.get(
                    "talkingPoints", {}),
            "steps": [
                "1 打开平台创作者后台",
                "2 粘贴标题/正文/标签",
                "3 发布后登记回执: POST "
                "/publications/{id}/receipt",
            ],
            "ironRule": (
                "B 档回执未登记视为未发布"
                "(数据诚实铁律)"),
            "shadow": shadow,
        }

    async def publish(
            self, source_id: int,
            platform: str,
            adaptation_id: int = 0,
            auto: bool = False,
            now: str = "") -> dict:
        """发布执行(A 档直连/B 档适配包
        ——MODE 门控: off 409/shadow
        留痕/assist 人工/full A 档自主)

        Raises:
            KeyError: 源/适配不存在
            ValueError: 门控/静默窗/封顶/
                域外
        """
        mode = current_mode()
        if mode == "off":
            raise ValueError(
                "NEXUSFLOW74_MODE=off——"
                "发布面关闭(观测面不受影响)")
        if platform not in PLATFORMS:
            raise ValueError(
                f"平台域外({platform})")
        source = await self.repo.get_source(
            source_id)
        if not source:
            raise KeyError(source_id)

        # 适配版本解析(指定或最新)
        if adaptation_id:
            adaptation = await self.repo \
                .get_adaptation(
                    adaptation_id)
            if not adaptation:
                raise KeyError(adaptation_id)
            if adaptation["platform"] \
                    != platform \
                    or adaptation["sourceId"] \
                    != source_id:
                raise ValueError(
                    "适配版本与平台/源不匹配")
        else:
            adapts = await self.repo \
                .list_adaptations(
                    source_id=source_id,
                    limit=200)
            candidates = [a for a in adapts
                          if a["platform"]
                          == platform]
            if not candidates:
                raise ValueError(
                    f"无 {platform} 适配版本"
                    "——先 POST /adapt")
            adaptation = candidates[-1]
        _bj_now(now)  # 格式校验(非法→409)

        tier = ADAPTER_TIERS[platform]
        publication_id = await self.repo \
            .next_id("publication")
        record = {
            "publicationId":
                publication_id,
            "sourceId": source_id,
            "adaptationId":
                adaptation["adaptationId"],
            "platform": platform,
            "platformName": PLATFORM_NAMES[
                platform],
            "adapterTier": tier,
            "mode": mode,
            "autoPublished": False,
            "retryCount": 0,
            "backoffSeconds": 0,
            "error": {},
            "receipt": {},
            "externalId": "",
            "needsReview": bool(
                adaptation.get(
                    "needsReview")),
            "complianceState":
                adaptation.get(
                    "complianceState", ""),
            "package": {},
            "publishedAt": "",
            "receiptAt": "",
            "createdAt": ts(),
        }

        # 影子期——留痕不派发
        # (前置保护不适用: 无真实触达)
        if mode == "shadow":
            record["status"] = "shadowed"
            record["package"] = \
                self._build_package(
                    adaptation, shadow=True)
            await self.repo.save_publication(
                record)
            logger.info(
                "nexus74_publish shadowed "
                "id=%s platform=%s",
                publication_id, platform)
            return record

        # 前置保护(仅真实发布):
        # 静默窗→每日封顶
        if await self._in_silence(now):
            hour = _bj_hour(now)
            raise ValueError(
                f"静默窗内(北京时间 {hour} 时)"
                "——不发布")
        if await self._count_published_today(
                platform, now) \
                >= DAILY_PUBLISH_CAP:
            raise ValueError(
                f"单平台每日封顶 "
                f"{DAILY_PUBLISH_CAP} 已满"
                "——明日再发")

        if tier == "B":
            # B 档永远人工(full 亦然——
            # 平台操作显式性铁律)
            record["status"] = \
                "awaiting_manual"
            record["package"] = \
                self._build_package(
                    adaptation, shadow=False)
            await self.repo.save_publication(
                record)
            logger.info(
                "nexus74_publish awaiting "
                "id=%s platform=%s(B 档)",
                publication_id, platform)
            return record

        # A 档直连(微信)
        if auto:
            if mode != "full":
                raise ValueError(
                    "auto 自主发布仅 full 档"
                    "(A 档低风险域——assist "
                    "须人工显式调用)")
            if record["complianceState"] \
                    != "pass":
                raise ValueError(
                    "review_required 内容不可"
                    "自主发布(人工确认优先)")
            record["autoPublished"] = True
        result = self._adapter_a_wechat(
            publication_id)
        if result["ok"]:
            record["status"] = "published"
            record["externalId"] = \
                result["externalId"]
            record["publishedAt"] = _at(now)
        else:
            record["status"] = "failed"
            record["backoffSeconds"] = \
                RETRY_BACKOFF_BASE * (
                    2 ** record["retryCount"])
            record["error"] = {
                "kind": result["kind"],
                "message": result["message"],
                "tierAdvice": TIER_ADVICE[
                    result["kind"]],
                "backoffSeconds":
                    record[
                        "backoffSeconds"],
                "retriable": (
                    result["kind"]
                    in _RETRIABLE_KINDS),
            }
        await self.repo.save_publication(
            record)
        logger.info(
            "nexus74_publish id=%s "
            "platform=%s status=%s",
            publication_id, platform,
            record["status"])
        return record

    # ============================================================
    # ② 自愈重试(A 档 failed——决策面)
    # ============================================================

    async def retry(
            self, publication_id: int,
            now: str = "") -> dict:
        """自愈重试(错误归因分级——
        指数退避序列 60→120→240→480;
        上限满→建议换 B 档人工)

        Raises:
            KeyError: 发布不存在
            ValueError: 门控/状态机/归因
        """
        mode = current_mode()
        if mode == "off":
            raise ValueError(
                "NEXUSFLOW74_MODE=off——"
                "发布面关闭")
        if mode == "shadow":
            raise ValueError(
                "影子期不执行适配器"
                "(留痕不派发)")
        pub = await self.repo \
            .get_publication(publication_id)
        if not pub:
            raise KeyError(publication_id)
        if pub["status"] != "failed":
            raise ValueError(
                "仅 failed 可重试(当前 "
                f"{pub['status']})")
        if pub["adapterTier"] != "A":
            raise ValueError(
                "B 档无自愈重试"
                "(人工操作+回执登记)")
        _bj_now(now)
        err = pub.get("error") or {}
        kind = err.get("kind", "unknown")
        if kind == "content_violation":
            raise ValueError(
                "内容违规——不可自动重试"
                "(修改内容后重新适配)")
        if kind == "auth_expired":
            raise ValueError(
                "凭证缺失——不自动重试"
                "(配置凭证或换 B 档人工发布)")
        if pub["retryCount"] \
                >= MAX_PUBLISH_RETRY:
            raise ValueError(
                f"重试上限 {MAX_PUBLISH_RETRY}"
                " 已满——建议换 B 档人工发布"
                "(switch_B_manual)")

        pub["retryCount"] = \
            int(pub.get("retryCount", 0)) + 1
        result = self._adapter_a_wechat(
            publication_id)
        if result["ok"]:
            pub["status"] = "published"
            pub["externalId"] = \
                result["externalId"]
            pub["publishedAt"] = _at(now)
            pub["error"] = {}
            pub["backoffSeconds"] = 0
        else:
            pub["status"] = "failed"
            pub["backoffSeconds"] = \
                RETRY_BACKOFF_BASE * (
                    2 ** pub["retryCount"])
            pub["error"] = {
                "kind": result["kind"],
                "message": result["message"],
                "tierAdvice": TIER_ADVICE[
                    result["kind"]],
                "backoffSeconds":
                    pub["backoffSeconds"],
                "retriable": (
                    result["kind"]
                    in _RETRIABLE_KINDS),
            }
            if pub["retryCount"] \
                    >= MAX_PUBLISH_RETRY:
                pub["error"]["tierAdvice"] = \
                    "switch_B_manual"
        await self.repo.save_publication(pub)
        logger.info(
            "nexus74_retry id=%s count=%s "
            "status=%s",
            publication_id,
            pub["retryCount"], pub["status"])
        return pub

    # ============================================================
    # ③ B 档回执登记(数据诚实——不受 MODE)
    # ============================================================

    async def receipt(
            self, publication_id: int,
            result: str,
            message: str = "",
            external_id: str = "",
            now: str = "") -> dict:
        """B 档人工回执登记(状态机
        awaiting_manual→published/
        rejected/throttled)

        Raises:
            KeyError: 发布不存在
            ValueError: 档位/状态机/域外
        """
        pub = await self.repo \
            .get_publication(publication_id)
        if not pub:
            raise KeyError(publication_id)
        if pub["adapterTier"] != "B":
            raise ValueError(
                "仅 B 档(半自动)登记回执"
                "——A 档直连无回执")
        if pub["status"] != "awaiting_manual":
            raise ValueError(
                "状态机: 仅 awaiting_manual"
                " 可登记(当前 "
                f"{pub['status']})")
        if result not in RECEIPT_RESULTS:
            raise ValueError(
                f"回执结果域外({result}): "
                f"{'/'.join(RECEIPT_RESULTS)}")
        _bj_now(now)
        at = _at(now)
        pub["status"] = result
        pub["receipt"] = {
            "result": result,
            "message": message,
            "receiptAt": at,
        }
        pub["receiptAt"] = at
        if external_id:
            pub["externalId"] = external_id
        if result == "published":
            pub["publishedAt"] = at
        await self.repo.save_publication(pub)
        logger.info(
            "nexus74_receipt id=%s "
            "result=%s",
            publication_id, result)
        return pub

    # ============================================================
    # ④⑤ 配额看板+适配器健康(观测面)
    # ============================================================

    async def quota_status(
            self, now: str = "") -> dict:
        """频次封顶/静默窗状态
        (观测面——常开)"""
        platforms = []
        for p in PLATFORMS:
            n = await \
                self._count_published_today(
                    p, now)
            platforms.append({
                "platform": p,
                "platformName":
                    PLATFORM_NAMES[p],
                "adapterTier":
                    ADAPTER_TIERS[p],
                "todayPublished": n,
                "cap": DAILY_PUBLISH_CAP,
                "remaining": max(
                    0, DAILY_PUBLISH_CAP - n),
            })
        silence = await self.get_silence()
        hour = _bj_hour(now) if now \
            else datetime.now(_BJT).hour
        return {
            "mode": current_mode(),
            "beijingHour": hour,
            "beijingDay": _bj_day(now),
            "silence": {
                "enabled": bool(
                    silence.get("enabled")),
                "hours": silence.get(
                    "hours", []),
                "active": (
                    bool(silence.get(
                        "enabled"))
                    and hour in set(
                        silence.get(
                            "hours") or [])),
            },
            "platforms": platforms,
            "note": ("单平台每日封顶+"
                     "夜间静默窗(北京时间)"
                     "——打扰保护"),
        }

    def healthz(self) -> dict:
        """适配器健康(A 档连通性——
        诚实工程: 无凭证报 no_credentials)"""
        dryrun = os.environ.get(
            _WECHAT_DRYRUN_ENV) == "1"
        creds = bool(os.environ.get(
            "WECHAT_MP_APPID"))
        adapters = [{
            "platform": "wechat_mp",
            "platformName": "微信公众号",
            "tier": "A",
            "dryrun": dryrun,
            "credentialsConfigured":
                creds,
            "status": (
                "ready(dryrun 演练)"
                if dryrun
                else ("ready"
                      if creds
                      else "no_credentials")),
        }]
        for p in PLATFORMS:
            if ADAPTER_TIERS[p] == "B":
                adapters.append({
                    "platform": p,
                    "platformName":
                        PLATFORM_NAMES[p],
                    "tier": "B",
                    "mode": "manual+receipt",
                    "status": (
                        "ready(人工操作+"
                        "回执登记)"),
                })
        return {
            "adapters": adapters,
            "note": ("A 档无凭证时发布归因 "
                     "auth_expired(诚实工程); "
                     "B 档永远人工+回执"),
        }

    # ============================================================
    # 发布记录(观测面)
    # ============================================================

    async def list_publications(
            self, platform: str = "",
            status: str = "",
            limit: int = 100) -> list[dict]:
        """发布记录列表(筛选)"""
        if status and status \
                not in PUBLISH_STATES:
            raise ValueError(
                f"发布状态域外({status})")
        return await self.repo \
            .list_publications(
                platform=platform or None,
                status=status or None,
                limit=limit)

    async def get_publication(
            self, publication_id: int) -> dict:
        """发布详情(含 error 归因)

        Raises:
            KeyError: 发布不存在
        """
        pub = await self.repo \
            .get_publication(publication_id)
        if not pub:
            raise KeyError(publication_id)
        return pub
