"""G1 高频答案缓存强化: 归一化 key + TTL 分层 + 命中计数

小竹Agent方案真缺口 G1: 意图缓存已有(300s), 本批强化
①key 归一化(标点/空白差异合并) ②chat 3600s/command 300s
分层 ③hits/misses 计数观测。Fake Redis 全链验证。
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "on"

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


class FakeRC:
    """内存 Redis fake(get/set ex/incr)"""

    def __init__(self):
        self.d = {}
        self.ttls = {}

    async def get(self, k):
        return self.d.get(k)

    async def set(self, k, v, ex=None):
        self.d[k] = v
        self.ttls[k] = ex

    async def incr(self, k):
        self.d[k] = int(self.d.get(k) or 0) + 1
        return self.d[k]


async def main():
    import repositories.backend as _be
    from services.xiaozhu_service import XiaozhuService
    import services.llm_client as _llm_mod
    svc = XiaozhuService()
    rc = FakeRC()
    _orig_rm, _orig_gRC = _be.is_redis_mode, _be.get_redis_client
    _be.is_redis_mode = lambda: True
    async def _fake_gRC():
        return rc
    _be.get_redis_client = _fake_gRC
    _orig_cls = _llm_mod.provider_client.classify_dialog_intent
    calls = {"n": 0}

    def _fake_classify(text, ctx, actions, model=""):
        calls["n"] += 1
        return {"intent": "chat", "reply": "我是小竹，"
                "您的语音导购小助手"}

    _llm_mod.provider_client.classify_dialog_intent = _fake_classify
    session = {"sessionId": 1}

    print("[01 归一化 key + 命中跳过 LLM]")
    r1 = await svc._llm_dialog_intent(session, "你是谁？")
    record("首次走 LLM 写缓存",
           calls["n"] == 1 and r1.get("intent") == "chat")
    r2 = await svc._llm_dialog_intent(session, "你是谁")
    record("标点变体命中缓存(不再调 LLM)",
           calls["n"] == 1 and r2.get("reply") == r1.get("reply"))
    r3 = await svc._llm_dialog_intent(session, " 你 是 谁 ？ ")
    record("空白+标点变体命中", calls["n"] == 1)

    print("[02 TTL 分层(chat 3600 / command 300)]")
    chat_keys = [k for k in rc.ttls
                 if k.startswith("zhuxiang:xiaozhu:intent:")
                 and rc.ttls[k] == 3600]
    record("chat 缓存 ex=3600", len(chat_keys) == 1,
           str(rc.ttls.get(chat_keys[0]) if chat_keys
               else None))

    def _fake_cmd(text, ctx, actions, model=""):
        calls["n"] += 1
        return {"intent": "command:product.new"}

    _llm_mod.provider_client.classify_dialog_intent = _fake_cmd
    await svc._llm_dialog_intent(session, "小竹看新品")
    cmd_keys = [k for k in rc.ttls
                if k.startswith("zhuxiang:xiaozhu:intent:")
                and rc.ttls[k] == 300]
    record("command 缓存 ex=300", len(cmd_keys) == 1)

    print("[03 hit/miss 计数观测]")
    record("misses ≥2(首次+command)",
           int(rc.d.get("zhuxiang:xiaozhu:intent:misses")
               or 0) >= 2)
    record("hits ≥2(两次变体命中)",
           int(rc.d.get("zhuxiang:xiaozhu:intent:hits")
               or 0) >= 2)

    print("[04 affirm 上下文敏感不缓存]")
    def _fake_affirm(text, ctx, actions, model=""):
        calls["n"] += 1
        return {"intent": "affirm"}

    _llm_mod.provider_client.classify_dialog_intent = _fake_affirm
    before = len([k for k in rc.d
                  if k.startswith("zhuxiang:xiaozhu:intent:")])
    await svc._llm_dialog_intent(session, "要的")
    after = len([k for k in rc.d
                 if k.startswith("zhuxiang:xiaozhu:intent:")])
    record("affirm 不新增缓存键", before == after,
           f"{before}->{after}")

    # 还原
    _be.is_redis_mode, _be.get_redis_client = _orig_rm, _orig_gRC
    _llm_mod.provider_client.classify_dialog_intent = _orig_cls

    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
