"""G2 推理强度分级: 复杂问句深度档 XIAOZHU_LLM_MODEL_DEEP

小竹Agent方案真缺口 G2: 简单 FAQ 走快速档(省成本省延迟),
比较类/推荐理由类/长文本切深度档; 未配 env 全走快速档
零影响。显式 model 优先于 profile 默认档。
"""
# ---------------------------------------------------------------------------
# [SCRIPT-STYLE TEST | 脚本式测试]  pytest 收集为 0 属预期（IMP-12 台账文件）。
#   运行方式 : python test_xiaozhu_g2_tiering.py        （cwd 必须是 zhuxiang-jiu/backend）
#   结果判定 : 看脚本尾部打印的 pass/fail 计数，不要看 pytest 输出。
#   勿误判   : pytest --collect-only 对本文件收集 0 条并以退出码 5
#              「no tests collected」结束——这是本文件的正常形态。
#              禁止据此判定测试损坏，禁止把退出码 5 当失败上报。
#   成因     : 用例以 record()/模块级计数函数组织，或 Test* 类内方法未命名
#              为 test_*，不满足 pytest.ini 的 python_functions=test_* 规则。
#   台账     : backend/scriptstyle_tests_manifest.json（机器可读）
#   闸门     : backend/test_scriptstyle_gate.py 校验清单一致性与 banner 存在性
# ---------------------------------------------------------------------------

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "on"
os.environ["LLM_API_KEY"] = "test-key-for-mock"

PASS = 0
FAIL = 0


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


async def main():
    from services.xiaozhu_service import _deep_model_for
    import services.llm_client as _llm_mod
    import repositories.backend as _be

    print("[01 未配深档 → 全快速档(零影响)]")
    os.environ.pop("XIAOZHU_LLM_MODEL_DEEP", None)
    record("未配 env 复杂句也返回空",
           _deep_model_for("这两款酒对比哪个好，"
                           "给我分析下理由") == "")

    print("[02 复杂度启发式]")
    os.environ["XIAOZHU_LLM_MODEL_DEEP"] = "glm-4-plus"
    record("比较类命中深档",
           _deep_model_for("经典和珍藏区别大吗")
           == "glm-4-plus")
    record("长文本(>40字)命中深档",
           _deep_model_for("我" * 41) == "glm-4-plus")
    record("简单 FAQ 走快速档",
           _deep_model_for("你是谁") == "")
    record("简单指令走快速档",
           _deep_model_for("小竹看新品") == "")

    print("[03 classify 透传 model + chat 显式优先]")
    rc = type("RC", (), {
        "d": {}, "ttls": {},
        "get": None, "set": None, "incr": None})()
    seen = {"n": 0, "models": []}

    async def _fake_gRC():
        return rc

    _be.is_redis_mode = lambda: False  # 缓存关, 直测透传

    def _fake_chat(system, user, temperature=0.3,
                   model="", timeout=0, profile=""):
        seen["n"] += 1
        seen["models"].append(model or "(fast)")
        return ('{"intent": "chat", "reply": "好嘞"}')

    _orig_chat = _llm_mod.LLMProviderClient.chat \
        if hasattr(_llm_mod, "LLMProviderClient") else None
    _llm_mod.provider_client.chat = _fake_chat

    from services.xiaozhu_service import XiaozhuService
    svc = XiaozhuService()
    await svc._llm_dialog_intent(
        {"sessionId": 1}, "你是谁")
    await svc._llm_dialog_intent(
        {"sessionId": 1}, "经典和珍藏区别大吗")
    record("简单句走快速档",
           seen["models"][0] == "(fast)",
           str(seen["models"]))
    record("复杂句切深度档",
           seen["models"][1] == "glm-4-plus",
           str(seen["models"]))

    print("[04 chat() 显式 model 不被 profile 覆盖]")
    import urllib.request as _urq
    _orig_urlopen = _urq.urlopen
    cap = {}

    class _FakeResp:
        def read(self):
            import json as _j
            return _j.dumps({"choices": [{"message": {
                "content": "ok"}}]}).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        cap["model"] = _llm_mod.json.loads(
            req.data.decode("utf-8"))["model"]
        return _FakeResp()

    os.environ["XIAOZHU_LLM_BASE_URL"] = \
        "https://xz.example.com/v1"
    os.environ["XIAOZHU_LLM_MODEL"] = "qwen-turbo"
    os.environ["LLM_ENABLED"] = "on"
    _urq.urlopen = _fake_urlopen
    _llm_mod.provider_client.chat = \
        _orig_chat if _orig_chat else _llm_mod.provider_client.chat
    # 还原实例方法为类方法后再走真 chat
    del _llm_mod.provider_client.chat
    r = _llm_mod.provider_client.chat(
        "s", "u", profile="xiaozhu", model="glm-4-plus")
    record("显式 model 优先(profile 档让位)",
           cap.get("model") == "glm-4-plus",
           str(cap.get("model")))
    r2 = _llm_mod.provider_client.chat(
        "s", "u", profile="xiaozhu")
    _urq.urlopen = _orig_urlopen
    record("未传 model 用 profile 默认档",
           cap.get("model") == "qwen-turbo",
           str(cap.get("model")))

    os.environ.pop("XIAOZHU_LLM_BASE_URL", None)
    os.environ.pop("XIAOZHU_LLM_MODEL", None)
    os.environ.pop("XIAOZHU_LLM_MODEL_DEEP", None)
    os.environ.pop("LLM_API_KEY", None)

    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
