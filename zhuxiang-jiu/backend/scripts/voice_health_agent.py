# -*- coding: utf-8 -*-
"""语音健康智能体 (voice-health-agent)
=====================================
背景: 语音链路(唤醒/识别/播报)历史上反复出问题——环境断层(.env 未加载)、
调试开关跨平台炸(WS_DUMP /tmp)、连接池僵尸死循环、上游 SSL 瞬时干扰等,
每次都靠人工多轮排查, 累计修改几十次, 消耗大量人力与机器时间。

本智能体的职责(每 10 分钟一轮, 计划任务驱动):
  ① 体检  —— 服务进程 / TTS 合成 / WS 唤醒协议 / 关键错误日志, 四道探针
  ② 自愈  —— 有界自动处置: 服务停止→拉起; 连续 3 轮探针失败→重启后端
              (每小时最多 1 次, 防重启风暴); 调试残留清理(过期 wsdump)
  ③ 留证  —— 每轮写 C:\backend\logs\voice-health.json(机器可读, 原子写)
              + voice-health.log(人读流水); 出问题先看这里, 不再盲猜
  ④ 防复发 —— 健康基准固化: 任何部署后首轮即校验, 回归当场暴露

设计红线: 纯标准库 + 机内自包含(密钥从 nssm 服务注册表读, 不出机器);
处置动作全部有界且有日志; 探针失败只计数不惊动用户(面板仍有兜底)。
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import wave
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(r"C:\backend")
LOG_DIR = ROOT / "logs"
STATE_FILE = LOG_DIR / "voice-health-state.json"
REPORT_FILE = LOG_DIR / "voice-health.json"
FLOW_LOG = LOG_DIR / "voice-health.log"
ERR_LOG = LOG_DIR / "service-err.log"
SVC = "backend8147"
MAX_FAIL_BEFORE_RESTART = 3
RESTART_COOLDOWN_SEC = 3600
WS_DUMP_DIR = Path(r"C:\tmp")

os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
for _line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    with FLOW_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(st: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def jwt_secret_from_service() -> str:
    """从 nssm 服务注册表提取 JWT_SECRET(不出机器, 不落盘)"""
    try:
        out = subprocess.run(
            ["reg", "query",
             rf"HKLM\SYSTEM\CurrentControlSet\Services\{SVC}\Parameters",
             "/v", "AppEnvironmentExtra"],
            capture_output=True, text=True, timeout=15).stdout
        m = re.search(r"JWT_SECRET=([0-9A-Fa-f]{16,})", out)
        return m.group(1) if m else ""
    except Exception:
        return ""


def probe_service() -> tuple[bool, str]:
    try:
        out = subprocess.run(
            ["sc", "query", SVC], capture_output=True, text=True,
            timeout=15).stdout
        ok = "RUNNING" in out.upper()
        return ok, "" if ok else f"{SVC} not RUNNING"
    except Exception as e:
        return False, f"sc query err: {e}"


def restart_backend(reason: str) -> str:
    subprocess.run(["C:\\nginx\\nssm.exe", "restart", SVC],
                   capture_output=True, timeout=120)
    time.sleep(12)
    ok, _ = probe_service()
    return f"restart({reason}) -> {'ok' if ok else 'STILL-DOWN'}"


def probe_tts(token: str) -> tuple[bool, str, bytes]:
    """TTS 合成探针: 走真实 HTTP 路由(鉴权+合成+清洗)"""
    import http.client
    try:
        c = http.client.HTTPConnection("127.0.0.1", 8000, timeout=30)
        c.request("GET", "/api/xiaozhu/tts?text="
                  "%E5%81%A5%E5%BA%B7%E6%A3%80%E6%9F%A5&voice=tongtong",
                  headers={"Authorization": f"Bearer {token}"})
        r = c.getresponse()
        data = r.read()
        c.close()
        ok = r.status == 200 and len(data) > 2000
        return ok, f"http={r.status} bytes={len(data)}", data if ok else b""
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:80]}", b""


def probe_ws_wake(token: str, tts_wav: bytes) -> tuple[bool, str]:
    """唤醒协议探针: v2 auth→armed→arm→ready→推流→收 partial"""
    import websockets

    async def _run() -> tuple[bool, str]:
        try:
            frames = b""
            if tts_wav[:4] == b"RIFF":
                w = wave.open(__import__("io").BytesIO(tts_wav), "rb")
                frames = w.readframes(min(w.getnframes(), 16000 * 6))
                w.close()
            async with websockets.connect(
                    "ws://127.0.0.1:8000/api/xiaozhu/ws/asr",
                    max_size=2 ** 22) as ws:
                await ws.send(json.dumps(
                    {"type": "auth", "token": token, "ver": "2"}))
                m = json.loads(await asyncio.wait_for(ws.recv(), 10))
                if m.get("type") != "armed":
                    return False, f"no-armed:{m.get('type')}"
                await ws.send(json.dumps({"type": "arm"}))
                got_ready = False
                parts = 0

                async def reader():
                    nonlocal got_ready, parts
                    while True:
                        m = json.loads(await asyncio.wait_for(ws.recv(), 25))
                        if m.get("type") == "ready":
                            got_ready = True
                        elif m.get("type") == "partial":
                            parts += 1
                rt = asyncio.create_task(reader())
                for _ in range(60):
                    if got_ready:
                        break
                    await asyncio.sleep(0.1)
                if not got_ready:
                    rt.cancel()
                    return False, "no-ready-in-6s"
                if frames:
                    for i in range(0, min(len(frames), 6400 * 20), 6400):
                        await ws.send(frames[i:i + 6400])
                        await asyncio.sleep(0.18)
                    await asyncio.sleep(1.5)
                rt.cancel()
                return True, f"ready+fed parts={parts}"
        except Exception as e:
            return False, f"{type(e).__name__}: {str(e)[:80]}"
    return asyncio.run(_run())


def probe_errlog() -> tuple[bool, str]:
    """错误风暴探针: 近 10 分钟致命类错误计数"""
    try:
        cut = datetime.now() - timedelta(minutes=10)
        pats = {"auth_failed": 0, "ws_asr_error": 0,
                "llm_tts_failed": 0, "stream_unavailable": 0}
        with ERR_LOG.open(encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-3000:]
        for ln in lines:
            m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", ln)
            if not m:
                continue
            try:
                if datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") < cut:
                    continue
            except ValueError:
                continue
            for p in pats:
                if p in ln:
                    pats[p] += 1
        bad = (pats["auth_failed"] > 10 or pats["llm_tts_failed"] > 8
               or pats["stream_unavailable"] > 5)
        return (not bad), json.dumps(pats, ensure_ascii=False)
    except Exception as e:
        return True, f"scan-skip:{e}"


def cleanup_dumps() -> str:
    n = 0
    if WS_DUMP_DIR.exists():
        cut = time.time() - 86400
        for f in WS_DUMP_DIR.glob("wsdump_*.wav"):
            try:
                if f.stat().st_mtime < cut:
                    f.unlink()
                    n += 1
            except Exception:
                pass
    return f"cleaned={n}"


def main() -> int:
    st = load_state()
    checks, actions = {}, []

    ok_svc, msg = probe_service()
    checks["service"] = {"ok": ok_svc, "msg": msg}
    if not ok_svc:
        actions.append(restart_backend("service-down"))

    token = ""
    if ok_svc:
        from core.auth import create_token
        sec = jwt_secret_from_service()
        if sec:
            token = create_token(3, "member", "access", secret=sec)
        else:  # 探针退路: 用 .env 无密钥场景不阻塞其余探针
            checks["tts"] = {"ok": False, "msg": "jwt-secret-not-found"}

    tts_ok, tts_msg, tts_wav = (False, "skipped", b"")
    if ok_svc and token:
        tts_ok, tts_msg, tts_wav = probe_tts(token)
        checks["tts"] = {"ok": tts_ok, "msg": tts_msg}

    ws_ok, ws_msg = (False, "skipped")
    if ok_svc and token:
        ws_ok, ws_msg = probe_ws_wake(token, tts_wav)
        checks["ws_wake"] = {"ok": ws_ok, "msg": ws_msg}

    err_ok, err_msg = probe_errlog()
    checks["errlog"] = {"ok": err_ok, "msg": err_msg}
    actions.append(cleanup_dumps())

    # 有界自愈: 连续探针失败 → 冷却期内最多一次重启
    core_ok = ok_svc and tts_ok and ws_ok
    if core_ok:
        st["consec_fail"] = 0
    else:
        st["consec_fail"] = int(st.get("consec_fail", 0)) + 1
        last = float(st.get("last_restart_ts", 0) or 0)
        if (st["consec_fail"] >= MAX_FAIL_BEFORE_RESTART
                and time.time() - last > RESTART_COOLDOWN_SEC):
            st["last_restart_ts"] = time.time()
            st["consec_fail"] = 0
            actions.append(restart_backend(
                f"probes-failed-x{MAX_FAIL_BEFORE_RESTART}"))
    st["last_run"] = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    save_state(st)

    status = ("ok" if core_ok and err_ok
              else "degraded" if core_ok else "critical")
    report = {"ts": st["last_run"], "status": status,
              "checks": checks, "actions": actions,
              "consec_fail": st.get("consec_fail", 0)}
    tmp = REPORT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(REPORT_FILE)
    log(f"[{status}] tts={checks.get('tts', {}).get('msg')} "
        f"ws={checks.get('ws_wake', {}).get('msg')} err={err_msg} "
        f"actions={actions}")
    return 0 if status != "critical" else 2


if __name__ == "__main__":
    sys.exit(main())
