"""P4.1 部署加固: LLM 开关状态日志 单元测试

验证 log_feature_status 三场景输出正确:
1. 未配置 key → 全轨关闭提示
2. key + 全轨 on → 各轨 on + 模型清单
3. key + 总开关 off → 各轨 off(回退 rule)
"""
import logging
import os
import sys

sys.path.insert(0, ".")

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def _capture_status():
    cap = _LogCapture()
    lg = logging.getLogger("services.llm_client")
    lg.addHandler(cap)
    try:
        from services.llm_client import log_feature_status
        log_feature_status()
    finally:
        lg.removeHandler(cap)
    return "\n".join(cap.lines)


async def main():
    for k in ("LLM_API_KEY", "LLM_ENABLED", "KNOWLEDGE_CHAT_LLM",
              "KNOWLEDGE_EMBEDDING", "KNOWLEDGE_MEDIA_LLM",
              "KNOWLEDGE_CRAWL_LLM", "KNOWLEDGE_RERANK"):
        os.environ.pop(k, None)

    # 1. 未配置 key
    out = _capture_status()
    record("P41-未配置key提示全关",
           "LLM_API_KEY" in out and "未配置" in out
           and "llm 轨全部关闭" in out)
    record("P41-未配置key不输出各轨",
           "RAG llm 合成" not in out)

    # 2. key + 全轨 on
    os.environ["LLM_API_KEY"] = "test-key"
    for k in ("KNOWLEDGE_CHAT_LLM", "KNOWLEDGE_EMBEDDING",
              "KNOWLEDGE_MEDIA_LLM", "KNOWLEDGE_CRAWL_LLM",
              "KNOWLEDGE_RERANK"):
        os.environ[k] = "on"
    out = _capture_status()
    record("P41-全轨on输出五轨生效",
           all(f": on (KNOWLEDGE_{t}" in out for t in
               ("CHAT_LLM", "EMBEDDING", "MEDIA_LLM", "CRAWL_LLM",
                "RERANK")))
    record("P41-输出模型清单",
           "chat=glm-4-flash" in out and "rerank=rerank" in out)
    # 密钥不泄漏
    record("P41-日志不泄漏密钥", "test-key" not in out)

    # 3. key + 总开关 off → 全部回退
    os.environ["LLM_ENABLED"] = "off"
    out = _capture_status()
    record("P41-总开关off各轨回退rule",
           out.count("off(回退 rule)") == 5)

    # 清理
    os.environ.pop("LLM_ENABLED", None)
    os.environ.pop("LLM_API_KEY", None)
    for k in ("KNOWLEDGE_CHAT_LLM", "KNOWLEDGE_EMBEDDING",
              "KNOWLEDGE_MEDIA_LLM", "KNOWLEDGE_CRAWL_LLM",
              "KNOWLEDGE_RERANK"):
        os.environ.pop(k, None)

    print("\n".join(RESULTS))
    print("-" * 60)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(main()))
