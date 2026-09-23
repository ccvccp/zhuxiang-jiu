"""78号P1·G1 终版一键取数(D1 决策门 09-30 自动执行)

双源合并:
  A) 服务端埋点(docker logs): voice78_tts_timing 五段 /
     tts_stream_first 首块 / preheat 计数 / voice48_timing 轮次
  B) 客户端 [LAT] 聚合(POST /lat/stats): 四段 P50/P90/
     h2 占比 / s2>5s 异常——观察期看板数据
输出: 结构化文本(stdout, crontab 落盘 /opt/zhuxiang/g1_final.txt)
跑法: 09-30 09:00 crontab 自动执行(观察窗 09-23~09-29 闭合后);
      亦可随时手动 python3 g1_final.py 出期中快照。
"""
import json
import re
import subprocess
import urllib.request


def sh(cmd: str) -> str:
    return subprocess.run(
        cmd, shell=True, capture_output=True,
        text=True, timeout=120).stdout


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1,
                   int(round(p / 100 * (len(xs) - 1))))]


def dist(name, xs, unit="ms"):
    if not xs:
        print(f"  {name}: 样本 0")
        return
    print(f"  {name}: n={len(xs)} min={min(xs)} "
          f"P50={pct(xs, 50)} P90={pct(xs, 90)} "
          f"max={max(xs)}{unit}")


def server_side() -> None:
    print("== [A] 服务端埋点(当前容器窗口) ==")
    logs = sh("docker logs zhuxiang-backend-1 2>&1")
    g0 = re.findall(
        r"voice78_tts_timing conn_ms=(\d+) up_ms=(\d+) "
        r"acoustic_ms=(\d+) dl_ms=(\d+) total_ms=(\d+) "
        r"bytes=(\d+)", logs)
    g1 = re.findall(
        r"voice78_tts_timing stream=1 conn_ms=(\d+) "
        r"first_chunk_ms=(\S+) total_ms=(\d+)", logs)
    print(f" -- 整句非流式 n={len(g0)}")
    for i, seg in enumerate(("conn", "up", "acoustic",
                             "dl", "total")):
        dist(seg, [int(r[i]) for r in g0])
    print(f" -- 流式 n={len(g1)}")
    dist("流式 total", [int(r[2]) for r in g1])
    fc = [int(x) for x in re.findall(
        r"voice78_tts_stream_first first_chunk_ms=(\d+)", logs)]
    dist("智谱首块 first_chunk(双档观察)", fc)
    ph = re.findall(r"voice78_tts_preheat text=(\S*)", logs)
    print(f"  预合成写入: n={len(ph)}")
    tot = [int(x) for x in re.findall(
        r"voice48_timing sid=\d+ total_ms=(\d+) action=", logs)]
    dist("轮次执行 total_ms", tot)
    acc = sh("grep 'xiaozhu/tts' /var/log/nginx/access.log "
             "| awk '{print $4}' | cut -d: -f1 "
             "| sort | uniq -c | tail -9")
    print("  nginx /tts* 按日(末9天):")
    print(acc)


def client_side() -> None:
    print("== [B] 客户端 [LAT] 聚合(观察期看板, days=7) ==")
    tok = json.loads(sh(
        "curl -s -X POST http://localhost:8000/api/auth/login "
        "-H 'Content-Type: application/json' "
        "-d '{\"phone\":\"13800000001\","
        "\"password\":\"test123456\"}'"
    ))
    token = tok.get("accessToken") \
        or (tok.get("data") or {}).get("accessToken") or ""
    req = urllib.request.Request(
        "http://localhost:8000/api/xiaozhu/lat/stats?days=7",
        headers={"Authorization": f"Bearer {token}",
                 "X-Member-Id": "1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        j = json.loads(r.read().decode("utf-8"))
    t = j.get("total") or {}
    print(f"  样本 n={t.get('n')} h2占比={t.get('h2pct')}% "
          f"s2>5s异常={t.get('slowN')} 轮")
    for k, name in (("s1", "vad→submit"), ("s2", "submit→resp"),
                    ("s3", "resp→play"), ("tt", "端到端首声")):
        o = t.get(k) or {}
        print(f"  {name}: P50={o.get('p50')} "
              f"P90={o.get('p90')} max={o.get('max')}")
    for d in (j.get("days") or []):
        if d.get("n"):
            print(f"    {d.get('day')}: n={d.get('n')} "
                  f"s2P50={(d.get('s2') or {}).get('p50')} "
                  f"h2%={d.get('h2pct')} 异常={d.get('slowN')}")
    slow = t.get("slow") or []
    if slow:
        print("  异常明细(末5):")
        for r in slow[-5:]:
            print(f"    {r.get('ts')} s2={r.get('s2')}ms "
                  f"proto={r.get('proto')}")


def main() -> None:
    print("78号P1·G1 终版取数(D1 决策门数据包)")
    sh("date >> /dev/null")
    server_side()
    client_side()
    print("== [C] D1 判读参考 ==")
    print("  s2 P50<500ms → H2 收益保持, GA 立项必要性低")
    print("  慢档占比(first_chunk>500ms 比例)<10% → P1 验收线达成")
    print("  s2>5s 异常仅零星且 proto=h2 → 服务端波动型(非网络)")


if __name__ == "__main__":
    main()
