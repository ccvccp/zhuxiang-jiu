"""LLM provider 客户端(P3.3: llm 轨统一接入点, 纯标准库)

设计对齐项目惯例(attract D-11 / knowledge D-14/D-18 的 llm 轨预留):
    - 纯标准库: urllib.request POST OpenAI 兼容 /chat/completions
      端点(智谱 GLM / DeepSeek / 通义等均兼容该协议), 不引入 SDK
    - 模块级单例 provider_client, 未配置 key 时整体不可用
    - 调用方降级: client 不吞异常语义, 返回 None 表示"本次不可用",
      由各调用方按既有惯例回退 rule 轨并打 *_llm_*_fallback_rule 日志

环境变量(对齐 AI_ 前缀动态读取惯例):
    LLM_API_KEY    必填, 缺失即 llm 轨关闭(默认 "")
    LLM_BASE_URL   OpenAI 兼容端点(默认智谱)
    LLM_MODEL      模型名(默认 glm-4-flash)
    LLM_TIMEOUT    请求超时秒(默认 15, 对齐 crawl_run)
    LLM_ENABLED    总开关, off 强制走 rule(默认 on)

用法:
    from services.llm_client import provider_client
    text = provider_client.chat("system prompt", "user prompt")
    if text is None:  # 未配置/失败 → 调用方回退 rule
        ...
"""

import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)

_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "15"))


def llm_enabled() -> bool:
    """llm 轨总开关(LLM_ENABLED=off 或未配置 API key 时关闭)"""
    if os.environ.get("LLM_ENABLED", "on").strip().lower() == "off":
        return False
    return bool(os.environ.get("LLM_API_KEY", "").strip())


class LLMProviderClient:
    """OpenAI 兼容 /chat/completions 客户端(urllib, 纯标准库)"""

    def chat(self, system: str, user: str,
             temperature: float = 0.3) -> str | None:
        """单轮对话补全, 失败/未配置返回 None(调用方回退 rule)

        Returns:
            模型回复文本; 未配置 key、请求失败、响应异常均返回 None。
        """
        if not llm_enabled():
            return None
        api_key = os.environ["LLM_API_KEY"].strip()
        base_url = os.environ.get(
            "LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"
        ).rstrip("/")
        model = os.environ.get("LLM_MODEL", "glm-4-flash")
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/chat/completions", data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            content = (body.get("choices") or [{}])[0].get(
                "message", {}).get("content")
            if not content or not str(content).strip():
                logger.warning("llm_chat_empty_response model=%s", model)
                return None
            return str(content).strip()
        except Exception as exc:
            logger.warning("llm_chat_failed(回退rule): %s", exc)
            return None


provider_client = LLMProviderClient()
