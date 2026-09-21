"""小竹 P0 Fun-ASR(阿里百炼千问3-ASR-Flash)接入测试

覆盖:
    - provider 分发: 默认 zhipu / aliyun 无 key 回退 / aliyun+key 启用
    - 百炼轨请求构造: base64 data URI(wav/mp3)、热词 system 实体
      词表、asr_options.language、OpenAI 兼容端点与鉴权头
    - 响应解析 / 异常 / 空响应 / 空文件 / 超大文件守卫
    - 双轨互备: aliyun 失败自动回退 zhipu, 智谱轨忽略 hotwords
    - hub 透传: hotwords 经 transcribe_upload 传入 + model 随 provider
    - 小竹三源热词聚合: env 种子 + 在售产品名 + 误听修正右词

网络层全 mock(urllib.request.urlopen 手工替换), 不真调云端。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_xiaozhu_funasr.py
"""
import asyncio
import base64
import json
import os
import sys
import tempfile
import urllib.request

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
for _k in ("ASR_PROVIDER", "DASHSCOPE_API_KEY", "DASHSCOPE_ASR_MODEL",
           "DASHSCOPE_BASE_URL", "ASR_HOTWORDS"):
    os.environ.pop(_k, None)

from repositories.store import reset_store

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


# ------------------------------------------------------------
# mock 网络层: 手工替换 urllib.request.urlopen
# ------------------------------------------------------------
CAP = {}
_orig_urlopen = urllib.request.urlopen


def _fake_urlopen_factory(resp_body=None, exc=None):
    def _fake(req, timeout=None):
        CAP["url"] = req.full_url
        CAP["headers"] = dict(req.header_items())
        CAP["body"] = json.loads(req.data.decode("utf-8"))
        CAP["calls"] = CAP.get("calls", 0) + 1
        if exc is not None:
            raise exc

        class _Resp:
            def read(self):
                return json.dumps(resp_body).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return _Resp()
    return _fake


def _write_audio(suffix, payload):
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(payload)
        return f.name


def run_sync_tests():
    from services import llm_client as lc
    client = lc.provider_client

    # ============================================================
    # P: provider 分发与助手函数
    # ============================================================
    print("[P provider 分发]")
    check("P1 默认 zhipu", lc.asr_provider() == "zhipu",
          f"got {lc.asr_provider()}")
    os.environ["ASR_PROVIDER"] = "aliyun"
    check("P2 aliyun 无 key 自动回退 zhipu",
          lc.asr_provider() == "zhipu", f"got {lc.asr_provider()}")
    check("P4a zhipu 模型名", lc.current_asr_model() == "glm-asr-2512",
          f"got {lc.current_asr_model()}")
    check("P5a asr_ready 全无 False", lc.asr_ready() is False)
    os.environ["DASHSCOPE_API_KEY"] = "sk-test"
    check("P3 aliyun+key 启用", lc.asr_provider() == "aliyun",
          f"got {lc.asr_provider()}")
    check("P4b aliyun 模型名",
          lc.current_asr_model() == "qwen3-asr-flash",
          f"got {lc.current_asr_model()}")
    check("P5b asr_ready aliyun 轨 True(LLM off 仍 True)",
          lc.asr_ready() is True)
    os.environ["DASHSCOPE_ASR_MODEL"] = "fun-test-model"
    check("P4c 自定义模型名透传",
          lc.current_asr_model() == "fun-test-model")
    os.environ.pop("DASHSCOPE_ASR_MODEL")

    # ============================================================
    # A: 百炼轨请求构造与解析(mock 网络)
    # ============================================================
    print("[A 百炼轨请求构造]")
    wav = _write_audio(".wav", b"RIFF-fake-wav-bytes")
    urllib.request.urlopen = _fake_urlopen_factory(
        {"choices": [{"message": {"content": "小竹，来一瓶竹香"}}]})
    CAP.clear()
    out = client._transcribe_aliyun(wav, ["竹香", "竹奕"])
    check("A1 转写文本解析", out == "小竹，来一瓶竹香", f"out={out}")
    check("A2 OpenAI 兼容端点",
          CAP["url"].endswith(
              "/compatible-mode/v1/chat/completions"), CAP["url"])
    check("A3 Bearer 鉴权头",
          CAP["headers"].get("Authorization") == "Bearer sk-test",
          str(CAP["headers"]))
    body = CAP["body"]
    check("A4 model/stream/language",
          body["model"] == "qwen3-asr-flash" and body["stream"] is False
          and body["asr_options"]["language"] == "zh", str(body))
    msgs = body["messages"]
    check("A5 system 热词词表 + user 音频",
          len(msgs) == 2 and msgs[0]["role"] == "system"
          and "竹香" in msgs[0]["content"] and "竹奕" in msgs[0]["content"]
          and msgs[1]["role"] == "user"
          and msgs[1]["content"][0]["type"] == "input_audio")
    data_uri = msgs[1]["content"][0]["input_audio"]["data"]
    check("A6 wav data URI 前缀+base64 还原",
          data_uri.startswith("data:audio/wav;base64,")
          and base64.b64decode(
              data_uri.split(",", 1)[1]) == b"RIFF-fake-wav-bytes")

    mp3 = _write_audio(".mp3", b"ID3-fake-mp3")
    urllib.request.urlopen = _fake_urlopen_factory(
        {"choices": [{"message": {"content": "ok"}}]})
    CAP.clear()
    client._transcribe_aliyun(mp3, None)
    m2 = CAP["body"]["messages"]
    check("A7 mp3 mediatype + 无热词无 system",
          m2[0]["content"][0]["input_audio"]["data"].startswith(
              "data:audio/mpeg;base64,")
          and len(m2) == 1 and m2[0]["role"] == "user")

    urllib.request.urlopen = _fake_urlopen_factory(
        exc=RuntimeError("network-down"))
    check("A8 网络异常 None(上层回退)",
          client._transcribe_aliyun(wav, None) is None)
    urllib.request.urlopen = _fake_urlopen_factory(
        {"choices": [{"message": {"content": "   "}}]})
    check("A9 空转写 None", client._transcribe_aliyun(wav, None) is None)
    empty = _write_audio(".wav", b"")
    CAP["calls"] = 0
    check("A10 空文件 None 不发请求",
          client._transcribe_aliyun(empty, None) is None
          and CAP["calls"] == 0)
    big = _write_audio(".wav", b"x" * (7 * 1024 * 1024 + 1))
    CAP["calls"] = 0
    check("A11 超 7MB 拒发 None",
          client._transcribe_aliyun(big, None) is None
          and CAP["calls"] == 0)
    urllib.request.urlopen = _orig_urlopen

    # ============================================================
    # D: transcribe 分发与双轨互备
    # ============================================================
    print("[D 双轨分发互备]")
    calls = []

    def fake_aliyun(p, h=None):
        calls.append(("aliyun", h))
        return "百炼文本"

    def fake_zhipu(p):
        calls.append(("zhipu", None))
        return "智谱文本"

    client._transcribe_aliyun = fake_aliyun
    client._transcribe_zhipu = fake_zhipu
    try:
        out = client.transcribe("x.wav", ["竹香"])
        check("D1 aliyun 成功不触 zhipu",
              out == "百炼文本"
              and calls == [("aliyun", ["竹香"])], str(calls))

        def fake_aliyun_fail(p, h=None):
            calls.append(("aliyun", h))
            return None

        client._transcribe_aliyun = fake_aliyun_fail
        calls.clear()
        out = client.transcribe("x.wav", ["竹香"])
        check("D2 aliyun 失败自动回退 zhipu",
              out == "智谱文本"
              and [c[0] for c in calls] == ["aliyun", "zhipu"],
              str(calls))
        os.environ["ASR_PROVIDER"] = "zhipu"
        calls.clear()
        out = client.transcribe("x.wav", ["竹香"])
        check("D3 zhipu 轨忽略 hotwords",
              out == "智谱文本" and calls == [("zhipu", None)],
              str(calls))
    finally:
        del client._transcribe_aliyun
        del client._transcribe_zhipu


async def run_async_tests():
    reset_store()
    from services import llm_client as lc
    from services.hub_service import HubService
    from services.xiaozhu_service import XiaozhuService
    from repositories.xiaozhu_repository import Xiaozhu48Repository
    from repositories.product_repository import ProductRepository

    # ============================================================
    # H: hub 透传与结构化降级
    # ============================================================
    print("[H hub 透传]")
    os.environ["ASR_PROVIDER"] = "aliyun"
    os.environ["DASHSCOPE_API_KEY"] = "sk-test"
    rec = {}

    def fake_transcribe(path, hotwords=None):
        rec["hotwords"] = hotwords
        rec["path"] = path
        return "小竹，来两件竹奕"

    lc.provider_client.transcribe = fake_transcribe
    try:
        r = await HubService().transcribe_upload(
            b"RIFF-fake", "a.wav", member_id=None,
            hotwords=["竹香", "竹奕"])
        check("H1 hub 透传 hotwords + 成功",
              r.get("success") is True
              and r.get("text") == "小竹，来两件竹奕"
              and rec["hotwords"] == ["竹香", "竹奕"], str(r))
        check("H2 model 随 provider",
              r.get("model") == "qwen3-asr-flash", str(r.get("model")))
    finally:
        del lc.provider_client.transcribe

    # 未配置(zhipu 轨 + LLM off)→ 结构化降级不抛异常
    os.environ["ASR_PROVIDER"] = "zhipu"
    os.environ.pop("DASHSCOPE_API_KEY")
    r2 = await HubService().transcribe_upload(
        b"RIFF-fake", "a.wav", member_id=None)
    check("H3 未配置结构化降级",
          r2.get("success") is False
          and "未配置" in str(r2.get("error", ""))
          and r2.get("fallback_hint") == "keyboard", str(r2))

    # ============================================================
    # X: 小竹三源热词聚合 + handle_voice 注入
    # ============================================================
    print("[X 三源热词聚合]")
    os.environ["ASR_PROVIDER"] = "aliyun"
    os.environ["DASHSCOPE_API_KEY"] = "sk-test"
    os.environ["ASR_HOTWORDS"] = "竹香,竹奕"
    await ProductRepository().save_product({
        "product_id": "p-asr-1", "name": "竹韵佳酿·竹香便携",
        "status": "on_sale", "price": 99})
    await ProductRepository().save_product({
        "product_id": "p-asr-2", "name": "下架款",
        "status": "off_sale", "price": 9})
    repo = Xiaozhu48Repository()
    await repo.save_asr_fix("奏结", "结算")
    svc = XiaozhuService()
    words = await svc._asr_hotwords()
    check("X1 三源聚合(env+在售产品+误听右词)",
          "竹香" in words and "竹奕" in words
          and "竹韵佳酿·竹香便携" in words and "结算" in words,
          str(words))
    check("X2 下架产品与误听左词不入表",
          "下架款" not in words and "奏结" not in words, str(words))
    check("X3 去重", len(words) == len(set(words)), str(words))
    os.environ["ASR_HOTWORDS"] = "竹香," + "超" * 21
    words2 = await svc._asr_hotwords()
    check("X4 超长词(>20)过滤",
          "竹香" in words2 and all(len(w) <= 20 for w in words2),
          str(words2))

    # handle_voice 全链注入热词(经 HubService mock 捕获调用参数)
    from unittest.mock import patch, AsyncMock
    os.environ["ASR_HOTWORDS"] = "竹香,竹奕"
    sid = (await svc.open_session(3001, "voice"))["sessionId"]
    with patch("services.hub_service.HubService") as mh:
        mh.return_value.transcribe_upload = AsyncMock(
            return_value={"success": True, "text": "小竹，来一件竹香"})
        await svc.handle_voice(sid, b"fake-audio", 3001)
        kw = mh.return_value.transcribe_upload.call_args.kwargs
    check("X5 handle_voice 注入热词",
          "hotwords" in kw and "竹韵佳酿·竹香便携" in kw.get(
              "hotwords", []) and "竹香" in kw.get("hotwords", []),
          str(kw.get("hotwords")))
    await svc.delete_session(sid)


async def main():
    run_sync_tests()
    await run_async_tests()
    print()
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    for line in RESULTS:
        if "[FAIL]" in line:
            print(line)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
