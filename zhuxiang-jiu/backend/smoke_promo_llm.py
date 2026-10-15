"""P1 探针: 36 号 agent 两档模型实测 + llm_enabled 状态"""
import logging
import time

logging.basicConfig(level=logging.INFO,
                    format="%(name)s %(message)s")

from services.llm_client import (  # noqa: E402
    llm_enabled, provider_client,
)

print("llm_enabled:", llm_enabled())
for m in ("glm-5.3", "glm-4-flash"):
    t0 = time.time()
    r = provider_client.chat(
        "你是测试助手, 只输出 JSON。",
        '回复: {"ok": true}',
        model=m)
    print(f"model={m} -> {repr(r)[:90]} "
          f"({time.time() - t0:.1f}s)")

from repositories.promo_repository import (  # noqa: E402
    PROMO_LLM_MODEL, PROMO_LLM_FALLBACK_MODEL,
)
print("PROMO 主档:", PROMO_LLM_MODEL,
      "| 备档:", PROMO_LLM_FALLBACK_MODEL)
