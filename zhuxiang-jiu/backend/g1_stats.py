"""78号P1·G1 期中基线报告取数: P50/P90 分布分析(宿主机跑)

数据源:
  a) docker logs(当前容器, P1 部署 16:04 起): voice78_tts_timing
     五段 / tts_stream_first 首块 / voice78_tts_preheat 预合成 /
     voice48_timing 轮次
  b) nginx access.log: /api/xiaozhu/tts* 请求量(全时段, 含
     P1.5 时代——容器 rebuild 不影响)
输出: 结构化统计(stdout) → 人工落报告
"""
import re
import subprocess


def sh(cmd: str) -> str:
    return subprocess.run(
        cmd, shell=True, capture_output=True,
        text=True, timeout=60).stdout


def pct(xs: list, p: float):
    if not xs:
        return None
    xs = sorted(xs)
    k = min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))
    return xs[k]


def dist(name: str, xs: list):
    if not xs:
        print(f"  {name}: 样本 0")
        return
    print(f"  {name}: n={len(xs)} min={min(xs)} "
          f"P50={pct(xs, 50)} P90={pct(xs, 90)} "
          f"max={max(xs)}")


logs = sh("docker logs zhuxiang-backend-1 2>&1")
print("== 容器窗口 ==")
print(sh("docker inspect zhuxiang-backend-1 "
         "--format '{{.State.StartedAt}}'").strip())

print("== [A] voice78_tts_timing 五段(分整句/流式) ==")
g0 = re.findall(
    r"voice78_tts_timing conn_ms=(\d+) up_ms=(\d+) "
    r"acoustic_ms=(\d+) dl_ms=(\d+) total_ms=(\d+) "
    r"bytes=(\d+)", logs)
g1 = re.findall(
    r"voice78_tts_timing stream=1 conn_ms=(\d+) "
    r"first_chunk_ms=(\S+) total_ms=(\d+)", logs)
print(f" -- 整句(非流式) n={len(g0)}")
for i, seg in enumerate(("conn", "up", "acoustic",
                         "dl", "total")):
    dist(seg, [int(r[i]) for r in g0])
dist("bytes", [int(r[5]) for r in g0])
print(f" -- 流式(stream=1) n={len(g1)}")
dist("total_ms", [int(r[2]) for r in g1])
print(f"  first_chunk(见[B], 与 total 差=下载尾延)")

print("== [B] 流式首块 first_chunk_ms(智谱上游波动) ==")
fc = [int(x) for x in re.findall(
    r"voice78_tts_stream_first first_chunk_ms=(\d+)", logs)]
dist("first_chunk_ms", fc)

print("== [C] 预合成写入(热路径建设) ==")
ph = re.findall(r"voice78_tts_preheat text=(\S*) bytes=(\d+)", logs)
print(f"  n={len(ph)} texts={[t for t, _ in ph]}")

print("== [D] voice48_timing 轮次(执行/asr) ==")
tot = [int(x) for x in re.findall(
    r"voice48_timing sid=\d+ total_ms=(\d+) action=", logs)]
asr = [int(x) for x in re.findall(
    r"voice48_timing sid=\d+ asr_ms=(\d+)", logs)]
dist("total_ms(执行)", tot)
dist("asr_ms", asr)
acts = re.findall(r"voice48_timing sid=\d+ total_ms=\d+ "
                  r"action=(\S+) track=(\S+)", logs)
print(f"  actions={[a for a, _ in acts]}")

print("== [E] nginx access: /api/xiaozhu/tts* 请求量(全时段) ==")
acc = sh("grep -c 'xiaozhu/tts?' /var/log/nginx/access.log; "
         "true")
acc_s = sh("grep -c 'xiaozhu/tts/stream' /var/log/nginx/access.log; "
           "true")
print(f"  /tts(整句) 请求累计: {acc.strip()}")
print(f"  /tts/stream(流式) 请求累计: {acc_s.strip()}")
today = sh("grep 'xiaozhu/tts' /var/log/nginx/access.log "
           "| awk '{print $4}' | cut -d: -f1 "
           "| sort | uniq -c | tail -8")
print("  按日分布(末8天):")
print(today)

print("== [F] 样本量评估(vs D1 门槛 200 轮) ==")
print(f"  当前容器轮次样本 n={len(tot) + len(asr)}"
      f"(窗口=容器启动至今; 完整周样本 09-30 统计)")
