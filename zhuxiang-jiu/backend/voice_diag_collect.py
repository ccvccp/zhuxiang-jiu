"""小竹语音全链路诊断数据收集(24h 窗口, 喂千问 max 复查)

六维聚合: ASR 识别质量 / 连接层健康 / 意图执行 / TTS 合成 /
唤醒链路 / 会话积分。输出 JSON + 原始日志样本(WARNING/ERROR
全量 + 最近语音行), 供大模型诊断与人工复核。
"""
import json
import os
import re
import subprocess

p = subprocess.run(
    ["docker", "logs", "zhuxiang-backend-1", "--since",
     os.environ.get("DIAG_SINCE", "24h")],
    capture_output=True, text=True, timeout=180)
log = (p.stdout or "") + "\n" + (p.stderr or "")
lines = [l for l in log.splitlines() if re.search(
    r"ws_asr|voice48|voice78|xiaozhu|llm_tts|tts_", l)]

stat = {
    "asr": {"final_total": 0, "empty": 0, "tiny": 0, "ok": 0,
            "long_ambient": 0, "wakeword_in_final": 0,
            "auth_failed": 0, "closure_1006": 0, "err_after_close": 0},
    "conn": {"ws_accepted": 0, "v2_armed": 0, "v2_arm": 0,
             "dump_open": 0, "dump_close": 0},
    "intent": {"turns_total": 0, "track_rule": 0, "track_llm": 0,
               "action_top": {}, "total_ms": []},
    "tts": {"ok": 0, "bad_response": 0, "stream_failed": 0,
            "total_ms": [], "preheat": 0, "bytes": 0},
    "session": {"open": 0},
    "points": {},
}

def bump(d, k):
    d[k] = d.get(k, 0) + 1

for l in lines:
    if "ws_asr_final" in l:
        stat["asr"]["final_total"] += 1
        m = re.search(r"text='(.*)' failed", l)
        t = m.group(1) if m else ""
        if not t or t == "None":
            stat["asr"]["empty"] += 1
        elif len(t) <= 2:
            stat["asr"]["tiny"] += 1
        else:
            stat["asr"]["ok"] += 1
        if len(t) >= 20:
            stat["asr"]["long_ambient"] += 1
        if "小竹" in t:
            stat["asr"]["wakeword_in_final"] += 1
    elif "ws_asr_auth_failed" in l:
        stat["asr"]["auth_failed"] += 1
        if "1006" in l:
            stat["asr"]["closure_1006"] += 1
    elif "ws_asr_error" in l:
        stat["asr"]["err_after_close"] += 1
    elif '"WebSocket /api/xiaozhu/ws/asr"' in l:
        stat["conn"]["ws_accepted"] += 1
    elif "ws_asr_v2_armed" in l:
        stat["conn"]["v2_armed"] += 1
    elif "ws_asr_v2_arm" in l:
        stat["conn"]["v2_arm"] += 1
    elif "ws_asr_dump open" in l:
        stat["conn"]["dump_open"] += 1
    elif "ws_asr_dump closed" in l:
        stat["conn"]["dump_close"] += 1
    elif "voice48_timing" in l:
        m = re.search(r"total_ms=(\d+) action=([\w.]+) track=(\w+)", l)
        if m:
            stat["intent"]["turns_total"] += 1
            stat["intent"]["total_ms"].append(int(m.group(1)))
            act = m.group(2)
            top = stat["intent"]["action_top"]
            top[act] = top.get(act, 0) + 1
            if m.group(3) == "rule":
                stat["intent"]["track_rule"] += 1
            else:
                stat["intent"]["track_llm"] += 1
    elif "voice48_session_open" in l:
        stat["session"]["open"] += 1
    elif "voice78_tts_timing" in l:
        m = re.search(r"total_ms=(\d+) bytes=(\d+)", l)
        if m:
            stat["tts"]["ok"] += 1
            stat["tts"]["total_ms"].append(int(m.group(1)))
            stat["tts"]["bytes"] += int(m.group(2))
    elif "llm_tts_bad_response" in l:
        stat["tts"]["bad_response"] += 1
    elif "llm_tts_stream_failed" in l:
        stat["tts"]["stream_failed"] += 1
    elif "voice78_tts_preheat" in l:
        stat["tts"]["preheat"] += 1
    elif "voice48_points" in l:
        m = re.search(r"kind=(\w+)", l)
        if m:
            bump(stat["points"], m.group(1))


def pct(arr, q):
    if not arr:
        return 0
    s = sorted(arr)
    return s[min(len(s) - 1, int(len(s) * q))]


summary = {
    "asr": stat["asr"],
    "conn": stat["conn"],
    "intent": {
        "turns_total": stat["intent"]["turns_total"],
        "track_rule": stat["intent"]["track_rule"],
        "track_llm": stat["intent"]["track_llm"],
        "action_top": stat["intent"]["action_top"],
        "total_ms_p50": pct(stat["intent"]["total_ms"], 0.5),
        "total_ms_p90": pct(stat["intent"]["total_ms"], 0.9),
    },
    "tts": {
        "ok": stat["tts"]["ok"],
        "bad_response": stat["tts"]["bad_response"],
        "stream_failed": stat["tts"]["stream_failed"],
        "preheat": stat["tts"]["preheat"],
        "bytes_mb": round(stat["tts"]["bytes"] / 1048576, 1),
        "total_ms_p50": pct(stat["tts"]["total_ms"], 0.5),
        "total_ms_p90": pct(stat["tts"]["total_ms"], 0.9),
    },
    "session": stat["session"],
    "points": stat["points"],
}

warn_err = [l for l in lines if "WARNING" in l or "ERROR" in l]
recent = lines[-300:]
out = {
    "window": "24h",
    "summary": summary,
    "warn_err_all": warn_err[-150:],
    "recent_samples": recent,
}
with open("/tmp/voice_diag.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(json.dumps(summary, ensure_ascii=False, indent=1))
print("warn_err_lines:", len(warn_err),
      "samples:", len(recent),
      "-> /tmp/voice_diag.json")
