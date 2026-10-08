"""84号·短剧生产编排 CLI(P1, 2026-10-09)

用法(开发机一站式——多角色分镜轮切):
  python drama84_batch.py --series d84_xxx [--eps 1-6]
      [--outfit autumn] [--pro pro-xxx] [--boot] [--no-shutdown]

流程(每集):
  ① 拉 series json(本地 series84/ 缺则生产拉)
  ② 逐 line 本地 TTS(音色=persona.voice——竹小妹 tongtong/
     帅哥 xiaochen 分角色)
  ③ 全部 wav+manifest 推 GPU 机, 远端单脚本顺序批推理
     (manifest 驱动: 每句换 SV73_DH_IMAGE=该 persona 的
     --outfit 档基准图; SadTalker CLI 逐句进程——模型重载
     为固有限制, 顺序批省 ssh/轮询开销)
  ④ 逐句下载 → 配音(dh 无声+wav) → 片头卡(集号标题)/
     尾卡(未完待续, Pillow) → concat 一集成片
  ⑤ POST /api/drama84/series/{sid}/attach-episode 登记
     content(76号组剧 episode_id)

产物: sv73_videos/d84_{sid}_ep{n}.mp4
"""

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dh_batch import (  # noqa: E402
    http, prod_token, prod_llm_key, ssh_connect, push, pull, run,
    REMOTE_ROOT, REMOTE_SVC, DH_SADTALKER_DIR, DH_IMAGES,
    dh_image_for, OUTFIT, wallet_balance,
)

SERIES_DIR = Path(__file__).resolve().parent / "series84"
OUT_DIR = Path(__file__).resolve().parent / "sv73_videos"


def load_series(sid: str) -> dict:
    p = SERIES_DIR / f"{sid}.json"
    if p.is_file():
        return json.loads(p.read_text("utf-8"))
    # 生产拉取(容器 /app/series84 经卷宿主 /opt/zhuxiang/series84)
    r = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=15",
         "root@47.236.61.117", "cat",
         f"/opt/zhuxiang/series84/{sid}.json"],
        capture_output=True, timeout=60)
    if r.returncode != 0:
        print(f"[fail] series 拉取失败(本地+生产均无): {sid}")
        sys.exit(1)
    SERIES_DIR.mkdir(exist_ok=True)
    p.write_bytes(r.stdout)
    return json.loads(r.stdout)


def tts_line(persona: str, text: str, tag: str) -> Path:
    """单句分角色 TTS(音色=IP_VOICE[persona] 自动路由)"""
    mini = {"scriptId": tag, "persona": persona,
            "template": {"name": "dh_oral"},
            "totalDuration": 8,
            "scenes": [{"index": 0, "role": "drama_oral",
                        "text": "", "voiceover": text,
                        "highlightWords": [], "duration": 8}]}
    from services.sv73_render_service import Sv73RenderService
    wav = Sv73RenderService()._tts_audio(mini)
    if wav is None or not Path(wav).is_file():
        raise RuntimeError(f"TTS 失败: {tag}")
    return Path(wav)


def card_png(text: str, sub: str, out: Path,
             bg=(28, 62, 44)) -> Path:
    """片头/尾卡(1080x1920 深绿底大字)"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1080, 1920), bg)
    d = ImageDraw.Draw(img)
    try:
        f1 = ImageFont.truetype(
            "C:/Windows/Fonts/msyhbd.ttc", 88)
        f2 = ImageFont.truetype(
            "C:/Windows/Fonts/msyh.ttc", 52)
    except OSError:
        f1 = f2 = ImageFont.load_default()
    for font, s, y in ((f1, text, 760), (f2, sub, 940)):
        w = d.textlength(s, font=font)
        d.text(((1080 - w) / 2, y), s, font=font,
               fill=(245, 240, 220))
    out.parent.mkdir(exist_ok=True)
    img.save(out)
    return out


def remote_batch(ssh, manifest: list[dict]) -> bool:
    """远端顺序批推理(manifest: [{tag, image, wav}])"""
    run(ssh, f"mkdir -p {REMOTE_ROOT}")
    for m in manifest:
        push(ssh, Path(m["wav"]),
             f"{REMOTE_ROOT}/{m['tag']}.wav")
    mf = json.dumps(manifest, ensure_ascii=False)
    b64mf = base64.b64encode(mf.encode()).decode()
    script = f"""
import importlib.util, json, os
from pathlib import Path
os.environ['SV73_DH_MODE'] = 'real'
os.environ['SV73_SADTALKER_DIR'] = '{DH_SADTALKER_DIR}'
os.environ['SV73_VIDEO_DIR'] = '{REMOTE_ROOT}'
spec = importlib.util.spec_from_file_location("dh", "{REMOTE_SVC}")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mf = json.loads(__import__('base64').b64decode('{b64mf}'))
for i, m in enumerate(mf, 1):
    os.environ['SV73_DH_IMAGE'] = m['image']
    sb = {{'scriptId': m['tag'], 'template': {{'name': 'dh_oral'}},
          'totalDuration': 8,
          'scenes': [{{'index': 0, 'role': 'drama_oral',
                      'voiceover': '', 'duration': 8}}]}}
    r = mod.Sv73DigitalHumanService().build(
        sb, Path(m['wav'].replace('\\\\', '/')))
    print(f"LINEDONE {{i}}/{{len(mf)}} {{m['tag']}} "
          f"{{r['sizeBytes']}}", flush=True)
print('BATCH PASS')
"""
    b64 = base64.b64encode(script.encode()).decode()
    run(ssh, f"echo {b64mf} | base64 -d > {REMOTE_ROOT}/_mf.json"
             f" && echo {b64} | base64 -d > /tmp/_batch84.py"
             f" && rm -f /tmp/_batch84.log"
             f" && nohup /root/miniconda3/envs/py312/bin/python"
             f" /tmp/_batch84.py > /tmp/_batch84.log 2>&1"
             f" & echo started")
    total = len(manifest)
    # 每句 ~90-150s(加载+推理); 上界 = 句数×200s + 300
    deadline = time.time() + total * 200 + 300
    while time.time() < deadline:
        time.sleep(60)
        _, out, _ = run(ssh, "ps aux | grep '[_]batch84.py'"
                              " >/dev/null && echo R || echo E;"
                              " tail -2 /tmp/_batch84.log")
        print("  ...", out.strip().splitlines()[-1][:90]
              if out.strip() else "")
        if out.startswith("E"):
            return "BATCH PASS" in out
    return False


def build_episode(sid: str, series: dict, ep: dict,
                  lines_meta: list[dict], token: str) -> Path:
    """拼装一集: 片头卡+逐句配音+尾卡 → concat"""
    from dh_batch import _h264ize
    import subprocess as sp
    ffmpeg = "ffmpeg"
    ep_no = ep["ep"]
    voiced = []
    for m in lines_meta:
        dh = OUT_DIR / f"{m['tag']}_dh.mp4"
        _h264ize(dh)
        v = OUT_DIR / f"{m['tag']}_voiced.mp4"
        r = sp.run([ffmpeg, "-y", "-i", str(dh), "-i", m["wav"],
                    "-c:v", "copy", "-c:a", "aac", "-shortest",
                    "-movflags", "+faststart", str(v)],
                   capture_output=True, timeout=180)
        if r.returncode != 0:
            raise RuntimeError("配音失败: " + m["tag"])
        voiced.append(v)
    head = card_png(f"第{ep_no}集", ep.get("title") or "",
                    OUT_DIR / f"d84_{sid}_ep{ep_no}_head.png")
    tail = card_png("未完待续", series["title"],
                    OUT_DIR / f"d84_{sid}_ep{ep_no}_tail.png")
    seg = OUT_DIR / f"d84_{sid}_ep{ep_no}_seg.txt"
    seg.write_text(
        f"file '{head}'\n".replace("\\", "/") +
        "".join(f"file '{v}'\n" for v in voiced) +
        f"file '{tail}'\n".replace("\\", "/"),
        encoding="utf-8")
    final = OUT_DIR / f"d84_{sid}_ep{ep_no}.mp4"
    # 卡段补齐流(loop 图+静音), 统一重编码拼接
    r = sp.run(
        [ffmpeg, "-y", "-loop", "1", "-t", "2.2", "-i", str(head),
         *sum([["-i", str(v)] for v in voiced], []),
         "-loop", "1", "-t", "1.8", "-i", str(tail),
         "-filter_complex",
         f"[0:v]scale=1080:1920,format=yuv420p[h];"
         + "".join(f"[{i + 1}:v]scale=1080:1920,"
                   f"format=yuv420p[v{i}];"
                   for i in range(len(voiced)))
         + f"[{len(voiced) + 1}:v]scale=1080:1920,"
           f"format=yuv420p[t];"
         + "".join(f"[{i + 1}:a]anull[a{i}];"
                   for i in range(len(voiced)))
         + "[0:a]anull[ah];"
           f"[{len(voiced) + 1}:a]anull[at];"
           f"[h][ah][v0][a0]"
         + "".join(f"[v{i}][a{i}]" for i in range(len(voiced)))
         + f"[t][at]concat=n={len(voiced) + 2}:v=1:a=1[out]",
         "-map", "[out]",
         "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,"
                "alimiter=limit=0.75:level=false",
         "-c:v", "libx264", "-preset", "fast", "-crf", "20",
         "-c:a", "aac", "-movflags", "+faststart", str(final)],
        capture_output=True, timeout=600)
    if r.returncode != 0 or not final.is_file():
        raise RuntimeError("拼接失败: "
                           + r.stderr.decode("utf-8", "ignore")[-300:])
    return final


def probe_duration(mp4: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries",
         "format=duration", "-of", "csv=p=0", str(mp4)],
        capture_output=True, text=True, timeout=60)
    try:
        return round(float(r.stdout.strip()), 1)
    except ValueError:
        return 75.0


def main() -> int:
    global OUTFIT
    ap = argparse.ArgumentParser(description="84号 剧集生产编排")
    ap.add_argument("--series", required=True)
    ap.add_argument("--eps", default="",
                    help="集号范围如 1-6(空=全部)")
    ap.add_argument("--outfit", default="default")
    ap.add_argument("--pro", default="")
    ap.add_argument("--boot", action="store_true")
    ap.add_argument("--no-shutdown", action="store_true")
    args = ap.parse_args()

    sid = args.series
    series = load_series(sid)
    eps_all = series["episodes"]
    if args.eps:
        a, _, b = args.eps.partition("-")
        rng = range(int(a), int(b or a) + 1)
        eps = [e for e in eps_all if e["ep"] in rng]
    else:
        eps = eps_all
    print(f"[series] {series['title']} 共{len(eps_all)}集, "
          f"本次生产 {len(eps)} 集, outfit={args.outfit}")

    # ① 全部台词 TTS(分角色音色)
    import os
    os.environ["LLM_API_KEY"] = prod_llm_key()
    os.environ["SV73_TTS_MODE"] = "on"
    manifest = []
    for ep in eps:
        for i, ln in enumerate(ep["lines"], 1):
            tag = f"{sid}_ep{ep['ep']}_l{i}"
            wav = tts_line(ln["persona"], ln["text"], tag)
            manifest.append({
                "tag": tag, "persona": ln["persona"],
                "wav": str(wav),
                "image": dh_image_for(ln["persona"],
                                      args.outfit)})
    print(f"[tts] {len(manifest)} 句分角色完成")

    # ② GPU 批推理
    bal = wallet_balance()
    if bal is not None:
        print(f"[余额] ¥{bal:.2f}")
    if args.boot:
        from adh_power import call as adl_call
        b = adl_call("POST", "/api/v1/dev/instance/pro/power_on",
                     {"instance_uuid": args.pro,
                      "payload": "gpu"})
        print("[boot]", b.get("code"))
        for _ in range(24):
            time.sleep(5)
            s = adl_call("GET", "/api/v1/dev/instance/pro/status",
                         {"instance_uuid": args.pro})
            if s.get("data") == "running":
                print("POWER_ON OK")
                break
    overrides = None
    if args.pro:
        from adh_power import call as adl_call
        b = adl_call("GET", "/api/v1/dev/instance/pro/snapshot",
                     {"instance_uuid": args.pro})
        d = b.get("data") or {}
        overrides = {"host": d["proxy_host"],
                     "port": int(d.get("ssh_port") or 0),
                     "user": "root",
                     "password": d.get("root_password", "")}
        print("[pro]", overrides["host"], overrides["port"])
    ssh = ssh_connect(overrides=overrides)
    # 素材上传(基准图全套+服务文件, md5 校验)
    from dh_batch import DH_SVC_LOCAL
    run(ssh, f"mkdir -p {DH_IMAGES['zhuxiaomei'][1].rsplit('/', 1)[0]}"
             f" {REMOTE_ROOT}")
    for persona, (local, remote) in DH_IMAGES.items():
        if local.is_file():
            push(ssh, local, remote)
    push(ssh, DH_SVC_LOCAL, REMOTE_SVC)
    ok = remote_batch(ssh, manifest)
    if not ok:
        _, out, _ = run(ssh, "tail -12 /tmp/_batch84.log")
        print("[fail] 批推理未过:", out.strip()[-400:])
        return 1
    print("[gpu] 批推理 PASS")

    # ③ 下载逐句
    for m in manifest:
        pull(ssh, f"{REMOTE_ROOT}/{m['tag']}_dh.mp4",
             OUT_DIR / f"{m['tag']}_dh.mp4")
    run(ssh, f"rm -f {REMOTE_ROOT}/*.mp4 {REMOTE_ROOT}/*.wav"
             f" {REMOTE_ROOT}/_mf.json")

    # ④ 拼装+登记
    token = prod_token()
    cids = []
    for ep in eps:
        lm = [m for m in manifest
              if m["tag"].startswith(f"{sid}_ep{ep['ep']}_l")]
        final = build_episode(sid, series, ep, lm, token)
        dur = probe_duration(final)
        s, b = http(
            "POST",
            f"/api/drama84/series/{sid}/attach-episode",
            {"ep": ep["ep"], "video": str(final),
             "duration": dur}, token=token)
        cid = ((b.get("data") or {}).get("contentId")
               if s == 200 else f"ERR{s}")
        cids.append(cid)
        print(f"  ep{ep['ep']}: {final.name} "
              f"{final.stat().st_size // 1024}KB {dur}s "
              f"cid={cid}")

    if not args.no_shutdown:
        print("[关机] Pro API power_off ...")
        from adh_power import call as adl_call
        ssh.close()
        b = adl_call("POST", "/api/v1/dev/instance/pro/power_off",
                     {"instance_uuid": args.pro})
        print("    power_off:", b.get("code"))
    print(f"\n[done] contentIds: {','.join(map(str, cids))}")
    print("[next] python drama76_build.py --drama-id "
          f"drama_{sid} --title \"{series['title']}\" "
          f"--free 2 --episodes {','.join(map(str, cids))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
