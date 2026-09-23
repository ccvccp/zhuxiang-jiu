"""小竹 P1 流式语音识别测试(百炼 qwen-audio-3.0-asr-flash-streaming)

覆盖:
    - AsrStreamSession 状态机: run-task(热词 vocabulary)/task-started/
      partial 透传/finish-task/task-finished→final(实测 is_sentence_end
      恒 None——最终=最后一次 partial)/连接失败回退/close 幂等
    - WS 路由: 首条消息鉴权(无效 token 拒/有效放行)/ready/二进制帧
      转发/finish→final
    - /voice textTranscript: 跳过重复转写直进指令链(audioMeta 记
      streamBytes), audioBase64 整段轨不受影响

网络层全 mock(websockets.connect / AuthService 手工替换), 不真连云。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_xiaozhu_stream.py
"""
import asyncio
import json
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["ASR_STREAM_MAX_CONN"] = "10"

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


class ScriptWS:
    """百炼 WS 替身: inbox 队列驱动 reader(真实 async for 消费),
    记录收到的指令与二进制帧"""

    def __init__(self):
        self.inbox = asyncio.Queue()
        self.sent = []          # 文本指令(run-task/finish-task)
        self.binary = 0
        self.closed = False

    def push(self, event: str, payload: dict | None = None):
        """推百炼事件(task-started/result-generated/...)"""
        self.inbox.put_nowait(json.dumps({
            "header": {"event": event}, "payload": payload or {}}))

    async def send(self, data):
        if isinstance(data, (bytes, bytearray)):
            self.binary += 1
        else:
            self.sent.append(json.loads(data))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.closed:
            raise StopAsyncIteration
        item = await self.inbox.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self):
        self.closed = True
        self.inbox.put_nowait(None)


async def run_session_tests():
    import services.asr_stream_service as stream_mod
    from services.asr_stream_service import AsrStreamSession

    print("[A AsrStreamSession 状态机]")
    pushes = []
    connect_args = []

    async def send_json(m):
        pushes.append(m)

    ws = ScriptWS()

    async def fake_connect(url, additional_headers=None):
        connect_args.append((url, additional_headers))
        return ws

    orig_connect = stream_mod.websockets.connect
    stream_mod.websockets.connect = fake_connect
    try:
        os.environ["DASHSCOPE_API_KEY"] = "sk-test"
        s = AsrStreamSession(send_json)
        # start: 连上后推 task-started
        ws.push("task-started")
        ok = await s.start(["竹香", "竹奕"])
        await asyncio.sleep(0.05)   # 让 reader 消费 started
        check("A1 start 成功", ok is True)
        rt = ws.sent[0]
        check("A2 run-task 构造(模型/pcm/热词 vocabulary)",
              rt["header"]["action"] == "run-task"
              and rt["payload"]["model"]
              == "qwen-audio-3.0-asr-flash-streaming"
              and rt["payload"]["parameters"]["format"] == "pcm"
              and rt["payload"]["parameters"]["sample_rate"] == 16000
              and rt["payload"]["parameters"]["vocabulary"]
              == {"竹香": 5, "竹奕": 5},
              json.dumps(rt, ensure_ascii=False))
        check("A3 连接带 bearer 头",
              connect_args[0][1]["Authorization"] == "bearer sk-test")

        await s.feed(b"\x01" * 6400)
        await s.feed(b"")
        check("A4 二进制帧转发+空帧跳过", ws.binary == 1)

        # partial 流: 两次中间结果 + finish→task-finished
        ws.push("result-generated",
                {"output": {"sentence": {"text": "小竹"}}})
        await asyncio.sleep(0.05)
        ws.push("result-generated",
                {"output": {"sentence": {
                    "text": "小竹，来一瓶竹香。"}}})
        await asyncio.sleep(0.05)
        check("A5 partial 透传",
              [p["text"] for p in pushes]
              == ["小竹", "小竹，来一瓶竹香。"], str(pushes))

        async def finish_and_close():
            ws.push("task-finished")

        fin = asyncio.create_task(finish_and_close())
        final = await s.finish()
        await fin
        await asyncio.sleep(0.05)
        check("A6 final=最后一次 partial",
              final == "小竹，来一瓶竹香。", f"final={final}")
        check("A7 finish-task 已发",
              any(m["header"]["action"] == "finish-task"
                  for m in ws.sent))
        await s.close()
        await s.close()
        check("A8 close 幂等", ws.closed is True)
        check("A9 连接计数归零", stream_mod.active_conns() == 0,
              f"active={stream_mod.active_conns()}")

        # task-failed → finish 返回 None
        ws2 = ScriptWS()
        stream_mod.websockets.connect = fake_connect
        s2 = AsrStreamSession(send_json)

        async def fake_connect2(url, additional_headers=None):
            return ws2
        stream_mod.websockets.connect = fake_connect2
        ws2.push("task-started")
        check("A10 二次会话 start", await s2.start(["竹香"]) is True)
        ws2.push("task-failed",
                 {"output": {"error": "boom"}})

        async def push_failed():
            ws2.push("task-failed",
                     {"header": {}})
        pf = asyncio.create_task(push_failed())
        final2 = await s2.finish()
        await pf
        check("A11 task-failed → finish None", final2 is None)
        await s2.close()

        # 连接失败 → False 且不占计数
        def boom(url, additional_headers=None):
            raise RuntimeError("conn-down")
        stream_mod.websockets.connect = boom
        s3 = AsrStreamSession(send_json)
        check("A12 连接失败回 False",
              await s3.start(["竹香"]) is False)
        check("A13 失败不占连接数",
              stream_mod.active_conns() == 0)
    finally:
        stream_mod.websockets.connect = orig_connect
        os.environ.pop("DASHSCOPE_API_KEY", None)


class FakeSession:
    """WS 路由集测替身(计数进类属性)"""
    fed_total = 0
    # ws_asr_final 观测日志无条件访问 session.failed——
    # 缺属性 AttributeError 杀死路由致 TestClient 永久挂
    # (挂起根因); 真实 AsrStreamSession 有此属性
    failed = None

    def __init__(self, send_json):
        self._send_json = send_json

    async def start(self, hotwords):
        return True

    async def feed(self, pcm):
        FakeSession.fed_total += 1

    async def finish(self, timeout=8):
        return "小竹，来一瓶竹香。"

    async def close(self):
        pass


async def run_http_tests():
    """WS 路由 + /voice textTranscript(fastapi TestClient)"""
    print("[H WS 路由 + textTranscript]")
    reset_store()
    from unittest.mock import AsyncMock, patch
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)

    # H1 鉴权失败 → error + 关闭
    with client.websocket_connect(
            "/api/xiaozhu/ws/asr") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "bad"}))
        resp = json.loads(ws.receive_text())
        check("H1 无效 token 拒绝",
              resp.get("type") == "error"
              and "鉴权失败" in resp.get("error", ""), str(resp))

    # H2-H4 全流程: 鉴权→ready→二进制×2→finish→final
    FakeSession.fed_total = 0
    with patch("services.auth_service.AuthService.get_current_member",
               AsyncMock(return_value={"memberId": 3001,
                                       "role": "member"})), \
            patch("services.asr_stream_service.AsrStreamSession",
                  FakeSession), \
            patch("services.xiaozhu_service.XiaozhuService"
                 "._asr_hotwords",
                 AsyncMock(return_value=["竹香"])), \
            client.websocket_connect(
                "/api/xiaozhu/ws/asr") as ws:
        ws.send_text(json.dumps(
            {"type": "auth", "token": "ok-token"}))
        ready = json.loads(ws.receive_text())
        check("H2 ready 就绪", ready.get("type") == "ready",
              str(ready))
        ws.send_bytes(b"\x01" * 6400)
        ws.send_bytes(b"\x02" * 6400)
        ws.send_text(json.dumps({"type": "finish"}))
        final = json.loads(ws.receive_text())
        check("H3 final 返回",
              final == {"type": "final",
                        "text": "小竹，来一瓶竹香。"}, str(final))
    check("H4 二进制帧已转发", FakeSession.fed_total == 2,
          f"fed={FakeSession.fed_total}")

    # H5-H7 /voice textTranscript 全链(asyncio store 真实跑)
    r = client.post("/api/xiaozhu/sessions",
                    headers={"X-Member-Id": "3001"},
                    json={"channel": "voice"})
    sid = r.json()["sessionId"]
    r = client.post(f"/api/xiaozhu/sessions/{sid}/voice",
                    headers={"X-Member-Id": "3001"},
                    json={"textTranscript": "小竹，看看有什么新品",
                          "durationSec": 3.2, "streamBytes": 102400})
    j = r.json()
    check("H5 textTranscript 全链",
          r.status_code == 200 and j.get("success") is True
          and (j.get("turn") or {}).get("rawText")
          == "小竹，看看有什么新品", str(j)[:200])
    check("H6 指令链生效",
          (j.get("turn") or {}).get("intent") == "product.new",
          str((j.get("turn") or {}).get("intent")))
    check("H7 audioMeta 记流式统计",
          ((j.get("turn") or {}).get("audioMeta") or {})
          .get("sizeBytes") == 102400,
          str((j.get("turn") or {}).get("audioMeta")))

    # H8 两可都缺 → 409
    r = client.post(f"/api/xiaozhu/sessions/{sid}/voice",
                    headers={"X-Member-Id": "3001"}, json={})
    check("H8 缺 audioBase64 与 textTranscript 409",
          r.status_code == 409, f"code={r.status_code}")

    # H9 整段 audioBase64 轨不受影响(mock 转写成功)
    with patch("services.hub_service.HubService") as mh:
        mh.return_value.transcribe_upload = AsyncMock(
            return_value={"success": True, "text": "小竹，查优惠"})
        r = client.post(f"/api/xiaozhu/sessions/{sid}/voice",
                        headers={"X-Member-Id": "3001"},
                        json={"audioBase64": "UklERg==",
                              "filename": "a.wav", "durationSec": 2})
        j = r.json()
        check("H9 audioBase64 整段轨兼容",
              r.status_code == 200
              and (j.get("turn") or {}).get("rawText")
              == "小竹，查优惠", str(j)[:150])
    client.delete(f"/api/xiaozhu/sessions/{sid}",
                  headers={"X-Member-Id": "3001"})


async def main():
    await run_session_tests()
    await run_http_tests()
    print()
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    for line in RESULTS:
        if "[FAIL]" in line:
            print(line)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
