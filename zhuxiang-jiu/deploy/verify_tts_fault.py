"""P0-B: TTS 流式故障注入验证——synthesize 容错台账

驱动 fault_proxy.py 六毒轮转, 进程内 env 覆写
LLM_BASE_URL=http://127.0.0.1:9999/api/paas/v4(仅本进程,
生产容器零影响), 逐毒调 provider_client().synthesize():
验证「每种毒 → 后端容错路径正确(None 降级不崩/慢流韧性/
正常透传)」并输出台账。

运行环境: 生产容器内(容器 python3.12 与生产运行时一致;
宿主系统 python3.11 解析不了容器代码的 3.12 语法):
    docker cp fault_proxy.py verify_tts_fault.py \
        zhuxiang-backend-1:/tmp/faultlab/   # 目录不存在先 exec mkdir
    docker exec -w /tmp/faultlab zhuxiang-backend-1 \
        python3 verify_tts_fault.py

判定(容错红线):
    normal    → 返回 WAV(bytes, RIFF 头) —— 对照基线
    delay2s   → 仍成功, 耗时 ≥2s —— 首包延迟韧性
    slow_drip → 仍成功(慢流读完整) —— 慢流不断韧性
    cut       → None(RST 断流 → except → 静默降级)
    abort     → None(立即断连同路径)
    500       → None(非 audio ctype → bad_response 降级)
每毒后跑一遍 normal 复归验证(文档回归基线原则: 注入后
必须确认系统完全恢复, 避免残留状态)。
"""
import json
import logging
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(os.path.dirname(HERE), "backend")
PROXY = os.path.join(HERE, "fault_proxy.py")
ENV_FILE = os.path.join(BACKEND, ".env")

PORT = 9999
BASE = f"http://127.0.0.1:{PORT}/api/paas/v4"
POISONS = ["normal", "delay2s", "slow_drip", "cut", "abort", "500"]

PASS = FAIL = 0


def record(name, passed, detail=""):
    global PASS, FAIL
    mark = "PASS" if passed else "FAIL"
    if passed:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{mark}] {name}" + (f" — {detail}"
                                  if detail else ""))


def load_env():
    """注入本进程环境: 容器 env(生产 key 所在)优先, .env 兜底

    只提取本验证所需键(LLM_API_KEY/TTS_MODEL/TTS_VOICE/
    LLM_TIMEOUT), 全程不打印值——密钥不落日志不落台账。
    """
    import subprocess as sp
    wanted = ("LLM_API_KEY", "TTS_MODEL", "TTS_VOICE",
              "LLM_TIMEOUT")
    try:
        out = sp.run(
            ["docker", "exec", "zhuxiang-backend-1", "printenv"],
            capture_output=True, text=True,
            timeout=15).stdout
        for line in out.splitlines():
            k, sep, v = line.partition("=")
            if sep and k in wanted and not os.environ.get(k):
                os.environ[k] = v
    except Exception as exc:
        print(f"   (容器 env 提取失败, 走 .env 兜底: {exc})")
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k in wanted:
                    os.environ.setdefault(k.strip(),
                                          v.strip().strip('"'))


def start_proxy(mode):
    env = dict(os.environ)
    env["FAULT_MODE"] = mode
    env["PORT"] = str(PORT)
    p = subprocess.Popen(
        [sys.executable, PROXY], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.2)  # aiohttp 起服窗口
    return p


APP_SNAPSHOT = "/tmp/faultlab_app"


def snapshot_app():
    """定位被验证代码: 容器内执行 → 直接 /app(生产镜像真实
    代码, 容器 python3.12 与生产一致); 宿主执行 → docker cp
    快照(宿主系统 python3.11 与容器 3.12 语法面不同, 快照
    仅作分析用, 正式验证必须在容器内跑)"""
    global APP_SNAPSHOT
    if os.path.isdir("/app/services"):
        APP_SNAPSHOT = "/app"
        print("   容器内执行: 直接验证生产代码 /app")
        return
    import subprocess as sp
    import shutil
    if os.path.exists(APP_SNAPSHOT):
        shutil.rmtree(APP_SNAPSHOT)
    r = sp.run(["docker", "cp",
                "zhuxiang-backend-1:/app", APP_SNAPSHOT],
               capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        print(f"!! 容器代码快照失败: {r.stderr[:200]}")
        sys.exit(1)
    print("   已快照生产容器代码 → " + APP_SNAPSHOT)


def run_round(mode, key):
    """一轮: 起 proxy(mode) → synthesize → 收台账 → 杀 proxy"""
    p = start_proxy(mode)
    try:
        os.environ["LLM_BASE_URL"] = BASE
        os.environ["LLM_API_KEY"] = key
        # 快照代码: 与生产镜像逐字一致; reload 隔离轮次 env
        sys.path.insert(0, APP_SNAPSHOT)
        for m in [k for k in list(sys.modules)
                  if k.split(".")[0] in ("services", "core")]:
            del sys.modules[m]
        import services.llm_client as lc
        client = lc.provider_client  # 模块级实例(非函数)
        t0 = time.monotonic()
        wav = client.synthesize("好的，这是一段合成的测试语音。")
        dt = time.monotonic() - t0
        return wav, dt
    finally:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def main():
    load_env()
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        print("!! LLM_API_KEY 缺失")
        sys.exit(1)
    snapshot_app()
    print(f"毒反代验证: 6 毒 × synthesize 容错台账\n")
    results = {}
    for mode in POISONS:
        wav, dt = run_round(mode, key)
        size = len(wav) if isinstance(wav, bytes) else None
        is_wav = isinstance(wav, bytes) and wav[:4] == b"RIFF"
        results[mode] = (is_wav, size, round(dt, 2))
        print(f"[{mode}] {dt:.2f}s → "
              f"{'WAV ' + str(size) + 'B' if is_wav else wav}")

        if mode == "normal":
            record("normal 透传返回 WAV", is_wav, str(size))
        elif mode == "delay2s":
            record("delay2s 慢首包仍成功", is_wav and dt >= 1.8,
                   f"{dt:.2f}s is_wav={is_wav}")
        elif mode == "slow_drip":
            record("slow_drip 慢流读完整仍成功",
                   is_wav and dt >= 1.5,
                   f"{dt:.2f}s is_wav={is_wav}")
        else:  # cut / abort / 500 → 一律 None 静默降级
            record(f"{mode} 断连/故障 → None 降级",
                   wav is None, str(type(wav)) + str(size))

    # 回归基线: 末轮再跑一次 normal(恢复确认)
    wav, dt = run_round("normal", key)
    record("回归基线: 注毒后 normal 复归",
           isinstance(wav, bytes) and wav[:4] == b"RIFF",
           str(len(wav) if wav else None))

    print(f"\n台账: {json.dumps(results, ensure_ascii=False)}")
    print(f"\nTTS 毒注入容错: {PASS} pass / {FAIL} fail")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    logging.disable(logging.WARNING)
    main()
