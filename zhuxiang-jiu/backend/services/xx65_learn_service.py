"""65号·网店及商品AI智能管理 回流
服务(xx65_learn, P4)

计划(docs/65号_网店及商品AI智能管理
模块实施计划.md §八 P4):
    第39档案回流——productId 1:1
    幂等(QC 铁律):

    ┌────────────────────────────────────┐
    │ published 商品(终态扫描)            │
    │   ├─ complianceFlag=False → shop_ok │
    │   └─ complianceFlag=True           │
    │       → shop_flagged(负信号)       │
    │            ↓                       │
    │ 44号 ai_learning.submit_feedback   │
    │ (scorerId=shop_operation            │
    │  content_compliance 因子)          │
    │            ↓                       │
    │ pooledFeedbackId>0 回写            │
    │ (双轮 labeled=0 幂等)              │
    └────────────────────────────────────┘

铁律:
    - /feedback/collect 不受 XX65_MODE
      影响(回流通道永不关停——宪法
      口径, 对齐 64号 P4)
    - 池双写 fail-soft——失败仅计
      poolFailed, 下轮重试
    - 确定性映射, LLM 不进回流链
"""

import logging

from core.helpers import ts
from repositories.xx65_repository import (
    Xx65Repository,
)

logger = logging.getLogger("xx65_learn")

MODEL_VERSION = "v1-xx65-learn"

SCORER_ID = "shop_operation"

# 单轮扫描上限
COLLECT_LIMIT = 200

# 回流信号(确定性映射——商品
# 合规标记为唯一判定源)
SIGNAL_MAP = {
    "shop_ok": {
        "source": "shop_ok",
        "correct": True,
        "note": "商品发布且无合规"
                "标记——内容合规"
                "正信号",
    },
    "shop_flagged": {
        "source": "shop_flagged",
        "correct": False,
        "note": "商品被防御③巡检"
                "标记——内容合规"
                "负信号",
    },
}


def learn_mode() -> str:
    """回流调度开关(XX65_LEARN_MODE,
    默认 off——仅调度循环受控;
    collect 手动触发不受影响)"""
    import os
    mode = os.environ.get(
        "XX65_LEARN_MODE") or "off"
    return mode if mode in (
        "off", "on") else "off"


class Xx65LearnService:
    """65号回流服务(P4——第39档案
    productId 1:1 幂等)"""

    def __init__(self):
        self.repo = Xx65Repository()

    async def collect_feedback(
            self, limit: int = COLLECT_LIMIT
    ) -> dict:
        """触发一轮商品回流(终态
        扫描→两信号→44号池双写)

        productId 1:1 幂等:
        pooledFeedbackId>0 即已
        入池——双轮 labeled=0
        """
        products = await \
            self.repo.list_products(
                status="published",
                limit=limit)
        summary = {
            "scanned": len(products),
            "labeled": 0,
            "skipped": 0,
            "poolSubmitted": 0,
            "poolFailed": 0,
            "signals": {},
            "errors": [],
            "collectedAt": ts(),
            "success": True,
            "note": "商品回流——productId"
                    " 1:1 幂等(合规标记"
                    "→第39档案 content"
                    "_compliance 因子)",
        }
        for product in products:
            try:
                result = await \
                    self._process(product)
            except Exception as exc:
                summary["errors"].append(
                    f"product:"
                    f"{product.get('productId')}"
                    f":{str(exc)[:80]}")
                continue
            kind = result.get("kind")
            if kind == "skip":
                summary["skipped"] += 1
                continue
            signal_key = result.get(
                "signal")
            if signal_key:
                summary["signals"][
                    signal_key] = \
                    summary["signals"].get(
                        signal_key, 0) + 1
            summary["labeled"] += 1
            if result.get("pooled"):
                summary[
                    "poolSubmitted"] += 1
            elif result.get("poolError"):
                summary[
                    "poolFailed"] += 1
        return summary

    async def _process(
            self, product: dict
    ) -> dict:
        """单商品处理(幂等判定+
        信号映射+池双写)"""
        product_id = int(
            product.get("productId")
            or 0)
        # 幂等: 已入池标记
        if int(product.get(
                "pooledFeedbackId")
                or 0) > 0:
            return {"kind": "skip",
                    "reason":
                        "already_pooled"}
        # 信号判定(确定性——
        # complianceFlag 唯一源)
        flagged = bool(
            product.get(
                "complianceFlag"))
        signal = SIGNAL_MAP[
            "shop_flagged"
            if flagged
            else "shop_ok"]
        # 44号池双写(fail-soft)
        pool_id, pool_err = \
            await self._write_pool(
                product, signal)
        # 幂等回写(pooledFeedbackId
        # >0 即已入池; 池失败回写 0
        # 下轮重试)
        if pool_id:
            fresh = await \
                self.repo.get_product(
                    product_id)
            if fresh is not None:
                fresh[
                    "pooledFeedbackId"] \
                    = pool_id
                fresh["poolReward"] = \
                    1.0 if signal[
                        "correct"] \
                    else -1.0
                fresh["pooled"] = True
                fresh["updatedAt"] = ts()
                await \
                    self.repo.save_product(
                        fresh,
                        create=False)
        return {
            "kind": "labeled",
            "productId": product_id,
            "signal": signal["source"],
            "pooled": bool(pool_id),
            "poolError": pool_err,
        }

    async def _write_pool(
            self, product: dict,
            signal: dict
    ) -> tuple:
        """44号 ai_learning 池双写
        (第39档案 content_compliance
        因子——确定性映射)"""
        try:
            from services.ai_learning_service import (
                submit_feedback,
            )
            result = await \
                submit_feedback({
                    "scorerId": SCORER_ID,
                    "factors": [{
                        "name":
                            "content"
                            "_compliance",
                        "score": 1.0
                        if signal[
                            "correct"]
                        else 0.0,
                        "weight": 0.20,
                    }],
                    "scoreAtDecision":
                        80.0,
                    "actualAction":
                        "flagged"
                        if not signal[
                            "correct"]
                        else "published",
                    "correct": signal[
                        "correct"],
                    "reward": 1.0
                    if signal[
                        "correct"]
                    else -1.0,
                    "note":
                        "65号回流 productId="
                        f"{product.get('productId')}",
                    "source":
                        "xx65_shop",
                })
            return (result.get(
                        "feedbackId")
                    or 0, "")
        except Exception as exc:
            logger.warning(
                "xx65_pool_write"
                "_failed: %s", exc)
            return (0, str(exc)[:100])

    async def learn_status(
            self) -> dict:
        """回流状态观测(第39档案
        回流实况——八因子观测口径)"""
        products = await \
            self.repo.list_products(
                limit=500)
        published = [
            p for p in products
            if p.get("status")
            == "published"]
        pooled = [
            p for p in published
            if int(p.get(
                "pooledFeedbackId")
                or 0) > 0]
        flagged = [
            p for p in published
            if p.get(
                "complianceFlag")]
        # 内容合规率观测口径
        # (一次过审/总发布)
        compliance_rate = round(
            1 - len(flagged)
            / len(published), 4) \
            if published else 1.0
        drafts = await \
            self.repo.list_drafts(
                limit=500)
        ai_adopted = [
            d for d in drafts
            if d.get("llmTrack")
            not in ("rule", "", None)]
        ai_adoption = round(
            len(ai_adopted)
            / len(drafts), 4) \
            if drafts else 0.0
        events = await \
            self.repo.list_compliance(
                limit=500)
        hit_events = [
            e for e in events
            if (e.get("findings")
                or [])]
        factors = {
            "contentCompliance":
                compliance_rate,
            "aiAdoption":
                ai_adoption,
            "complianceEvents":
                len(events),
            "complianceHits":
                len(hit_events),
            "publishedProducts":
                len(published),
            "pooledProducts":
                len(pooled),
            "note": "八因子观测口径"
                    "实时聚合——回流仅"
                    "content_compliance"
                    "单因子(确定性映射)",
        }
        return {
            "success": True,
            "learnMode": learn_mode(),
            "pooledProducts":
                len(pooled),
            "scorer": {
                "scorerId": SCORER_ID,
                "modelVersion":
                    MODEL_VERSION,
            },
            "signals": {
                k: v["note"]
                for k, v in
                SIGNAL_MAP.items()},
            "factors": factors,
            "note": "回流状态——productId"
                    " 1:1 幂等(通道不受"
                    "XX65_MODE 影响)",
            "generatedAt": ts(),
        }
