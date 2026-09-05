"""55号·二维码AI智能管理 意图理解引擎(qr55_intent_service)

计划(docs/55号_二维码AI智能管理模块实施计划.md §一):
    规则轨优先——48号小竹意图范式平移:
    - 关键词/同义词精确命中优先(可解释/零幻觉)
    - LLM 不进路由决策链(白名单注册表铁律)
    - 三态解析: resolved 命中 / partial 模糊(多候选)
      / clarify 需澄清(无候选)

设计:
    - parse_intent: 自然语言 → (serviceId 候选,
      置信度, 参数抽取)——纯函数确定性规则
    - 多候选→partial(带候选列表供澄清)
    - 参数抽取: 引号/「」包裹+关键词后接值
    - audience 过滤: 按会员画像缩小候选域(千面)
"""

import logging
import re

logger = logging.getLogger("qr55_intent_service")

MODEL_VERSION = "v1-qr55-intent"

# 置信度口径(规则轨——命中词条长度加权)
FULL_MATCH = 1.0
PARTIAL_MATCH = 0.6
AMBIGUOUS_MATCH = 0.4

# 参数抽取模式(引号/书名号/冒号后接值)
_PARAM_PATTERNS = (
    r'[\'"]([^\'"]{1,40})[\'"]',
    r'[「『]([^」』]{1,40})[」』]',
)


class Qr55IntentService:
    """意图理解引擎(规则轨——白名单映射+三态解析)"""

    def parse_intent(self, text: str,
                     audience: str = None) -> dict:
        """自然语言 → 意图解析(三态)

        Returns:
            {status: resolved/partial/clarify,
             serviceId, confidence, candidates,
             params, intentText}

        Raises:
            ValueError: 空输入
        """
        from services.qr55_registry import (
            INTENT_KEYWORDS, SERVICE_REGISTRY,
            match_services,
        )
        text = (text or "").strip()
        if not text:
            raise ValueError("意图文本不能为空")

        # 候选域(active 优先——pending/retired 不生成)
        domain = {s["serviceId"]: s
                  for s in match_services(audience)}
        if not domain:
            domain = {s["serviceId"]: s
                      for s in match_services()}

        # 关键词命中打分(词条长度加权——长词更精确)
        scores: dict[str, float] = {}
        for sid, keywords in INTENT_KEYWORDS.items():
            if sid not in domain:
                continue
            best = 0.0
            for kw in keywords:
                if kw in text:
                    ratio = min(1.0,
                                len(kw) / max(1,
                                              len(text)))
                    best = max(best,
                               FULL_MATCH * 0.7
                               + ratio * 0.3)
            if best > 0:
                scores[sid] = best

        # 参数抽取(确定性模式)
        params = self._extract_params(text)

        if not scores:
            return {
                "status": "clarify",
                "serviceId": None,
                "confidence": 0.0,
                "candidates": [],
                "params": params,
                "intentText": text[:80],
                "question": "请问您需要哪类服务?"
                            "(办事办理/信息查询/表格下载"
                            "/意见反馈)",
                "note": "无候选——需澄清(规则轨零命中)",
                "modelVersion": MODEL_VERSION,
            }

        ranked = sorted(scores.items(),
                        key=lambda kv: -kv[1])
        top_sid, top_score = ranked[0]

        # 多候选且分差小 → partial(澄清带候选)
        if len(ranked) > 1 \
                and ranked[1][1] >= top_score - 0.15:
            candidates = [
                {"serviceId": sid,
                 "label": domain[sid]["label"],
                 "confidence": round(score, 3)}
                for sid, score in ranked[:4]]
            return {
                "status": "partial",
                "serviceId": top_sid,
                "confidence": round(top_score, 3),
                "candidates": candidates,
                "params": params,
                "intentText": text[:80],
                "question": (
                    f"您是想「{domain[top_sid]['label']}」"
                    "吗? 还请确认。"),
                "note": "多候选歧义——候选澄清",
                "modelVersion": MODEL_VERSION,
            }

        svc = domain[top_sid]
        return {
            "status": "resolved",
            "serviceId": top_sid,
            "label": svc["label"],
            "confidence": round(top_score, 3),
            "candidates": [],
            "params": params,
            "intentText": text[:80],
            "note": "规则轨精确命中",
            "modelVersion": MODEL_VERSION,
        }

    @staticmethod
    def _extract_params(text: str) -> dict:
        """确定性参数抽取(引号/书名号包裹值——
        位置无关白名单注入)"""
        found = []
        for pattern in _PARAM_PATTERNS:
            found.extend(re.findall(pattern, text))
        params: dict = {}
        for i, value in enumerate(found):
            params[f"param{i + 1}"] = value
        return params

    def generate_clarify(self, intent_result: dict,
                         member_id: int = None) -> dict:
        """澄清问句生成(mock 确定性模板——
        LLM real 轨 P1 接入, 此处零依赖)"""
        status = intent_result.get("status")
        if status == "resolved":
            return {
                "success": True, "needClarify": False,
                "question": "",
                "modelVersion": MODEL_VERSION,
            }
        question = intent_result.get("question") or \
            "请描述您需要的服务"
        return {
            "success": True, "needClarify": True,
            "question": question,
            "candidates": intent_result.get(
                "candidates") or [],
            "modelVersion": MODEL_VERSION,
            "note": "mock 确定性澄清模板"
                    "(LLM_ENABLED=on 后 real 润色)",
        }
