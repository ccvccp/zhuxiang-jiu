"""78号P1·G3 spike: glm-tts 流式接口实证(首块延迟/音色兼容/SSE 规格)

查证项(《78号_竹语TTS_P1路线启动评估方案.md》§二 G3):
    S1 模型名流式接受度: cogtts vs glm-tts(stream:true 是否被
       现用 cogtts 接受——决定切模型名与否)
    S2 音色兼容矩阵: glm-tts 流式下 9 音色全测(重点 78号档案
       6 档, male/female 官方枚举未列——查证是否仍可用)
    S3 首块延迟多次采样: 流式首声下限(决定 P1 验收线可达性)

SSE 规格探测: 智谱流式响应为 data: {json} 分块, 音频字段
路径(output.audio/audio/data)动态探测; pcm 采样率未文档化
——按 24kHz 16bit mono(48000B/s) 推算音频时长, 用常识时长
反推采样率是否成立。
跑法: 生产容器 docker exec(容器注入 LLM_API_KEY)
"""
import base64
import http.client
import json
import os
import sys
import time
from urllib.parse import urlparse

sys.path.insert(0, "/app")

_BASE = os.environ.get(
    "LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
_KEY = os.environ["LLM_API_KEY"].strip()


def stream_probe(model: str, text: str, voice: str) -> dict:
    """一次流式合成探测: SSE 逐块计时/字节/字段结构"""
    payload = json.dumps({
        "model": model, "input": text, "voice": voice,
        "response_format": "pcm", "encode_format": "base64",
        "stream": True, "speed": 1.0,
    }, ensure_ascii=False).encode("utf-8")
    u = urlparse(_BASE)
    conn = http.client.HTTPSConnection(
        u.hostname, u.port or 443, timeout=30)
    s = {"model": model, "voice": voice, "ok": False,
         "http": None, "first_ms": None, "done_ms": None,
         "chunks": 0, "audio_bytes": 0, "first_bytes": 0,
         "first_keys": None, "err": None}
    try:
        conn.request(
            "POST", u.path + "/audio/speech", body=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {_KEY}"})
        resp = conn.getresponse()
        s["http"] = resp.status
        if resp.status != 200:
            s["err"] = resp.read()[:200].decode("utf-8", "ignore")
            return s
        t_hdr = time.monotonic()
        first_at = None
        while True:
            line = resp.readline()
            if not line:
                break
            if not line.startswith(b"data:"):
                continue
            body = line[5:].strip()
            if not body or body == b"[DONE]":
                continue
            if first_at is None:
                first_at = time.monotonic()
                s["first_ms"] = round((first_at - t_hdr) * 1000)
            s["chunks"] += 1
            if s["chunks"] <= 2 or s["chunks"] == 3:
                # 前两块+末块原文 dump(字段路径探测)
                s.setdefault("raw", []).append(
                    body[:600].decode("utf-8", "ignore"))
            try:
                obj = json.loads(body)
                if s["first_keys"] is None:
                    s["first_keys"] = sorted(obj.keys())
                # 实证路径: choices[0].delta.content(chat delta 流
                # 内嵌 base64 pcm)——S0 dump 定案
                ch = (obj.get("choices") or [{}])[0]
                delta = ch.get("delta") or {}
                audio = delta.get("content")
                piece = {
                    "t_ms": round(
                        (time.monotonic() - t_hdr) * 1000),
                    "role": delta.get("role"),
                    "b64": len(audio) if audio else 0,
                    "finish": ch.get("finish_reason")}
                s.setdefault("pieces", []).append(piece)
                err = obj.get("error")
                if err and not s["err"]:
                    s["err"] = json.dumps(
                        err, ensure_ascii=False)[:160]
                if audio:
                    raw = base64.b64decode(audio)
                    if not s["first_bytes"]:
                        s["first_bytes"] = len(raw)
                        s["first_audio_ms"] = piece["t_ms"]
                    s["audio_bytes"] += len(raw)
            except Exception:  # noqa: S110
                pass
        s["done_ms"] = round((time.monotonic() - t_hdr) * 1000)
        s["ok"] = s["audio_bytes"] > 0
        if s["ok"]:
            # pcm 时长推算(24kHz 16bit mono=48000B/s 假设)
            s["audio_sec"] = round(s["audio_bytes"] / 48000, 2)
            s["first_sec"] = round(s["first_bytes"] / 48000, 3)
    except Exception as exc:
        s["err"] = f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()
    return s


def _fmt(s: dict) -> str:
    return (f"http={s['http']} ok={s['ok']} "
            f"first_ms={s['first_ms']} first_chunk="
            f"{s.get('first_sec')}s({s['first_bytes']}B) "
            f"done_ms={s['done_ms']} chunks={s['chunks']} "
            f"audio={s['audio_bytes']}B/{s.get('audio_sec')}s "
            f"keys={s['first_keys']} err={s['err']}")


def main() -> None:
    print("== [S0] 块级明细(首音频块延迟/分块节奏——短/中/长句) ==")
    for tag, txt in (("4字", "好的——"),
                     ("13字", "好的，我为您推荐竹香经典。"),
                     ("44字", "好的——我为您推荐竹奕·竹香经典 52度 500ml，"
                      "您看这款怎么样？需要就说「需要」。")):
        s = stream_probe("glm-tts", txt, "tongtong")
        print(f"[{tag}] ok={s['ok']} 首音频块={s.get('first_audio_ms')}ms "
              f"总完={s['done_ms']}ms 音频={s['audio_bytes']}B"
              f"/{s.get('audio_sec')}s 块数={s['chunks']}")
        for p in (s.get("pieces") or [])[:14]:
            print(f"    t={p['t_ms']}ms b64={p['b64']} "
                  f"role={p['role']} finish={p['finish']}")
        if s["err"]:
            print(f"    err={s['err']}")

    print("== [S1] 模型名流式接受度(cogtts vs glm-tts) ==")
    for m in ("cogtts", "glm-tts"):
        s = stream_probe(m, "好的，我为您推荐竹香经典。", "tongtong")
        print(f"{m:9} ok={s['ok']} 首音频={s.get('first_audio_ms')}ms "
              f"总完={s['done_ms']}ms 音频={s['audio_bytes']}B err={s['err']}")

    print("== [S2] glm-tts 流式音色兼容矩阵(78号6档+角色档) ==")
    for v in ("tongtong", "xiaochen", "chuichui", "female",
              "male", "luodo", "jam", "kazi", "douji"):
        s = stream_probe("glm-tts", "好的——", v)
        print(f"{v:9} http={s['http']} ok={s['ok']} "
              f"first_ms={s['first_ms']} err={(s['err'] or '')[:90]}")

    print("== [S3] 首音频块延迟多次采样(4字, tongtong) ==")
    for i in range(3):
        s = stream_probe("glm-tts", "好的——", "tongtong")
        print(f"#{i + 1} 首音频={s.get('first_audio_ms')}ms "
              f"首块时长={s.get('first_sec')}s "
              f"总完={s['done_ms']}ms err={s['err']}")


if __name__ == "__main__":
    main()
