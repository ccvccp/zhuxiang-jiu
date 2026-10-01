"""73号(sv)·数字人 GPU 轨批量编排器(生产拆链正式化, 2026-10-01 实录)

用法: python dh_batch.py --sids sv73_xxx[,sv73_yyy] [--boot] [--no-shutdown]
      python dh_batch.py --sid sv73_xxx                      # 单条
      python dh_batch.py --boot --sids s1,s2                 # 自动开机+跑批+自动关机

链路(build_dh_dev 四步的拆链形态——GPU 机 import 巨网不可控的分段铁律):
  0.(--boot) AutoDL API 开机(adh_power on) → 轮询 SSH 就绪
  1. 拉生产 storyboard(SSH 容器管道取 token)
  2. 本地 TTS(78号竹语; LLM_API_KEY 经生产容器管道注入, 不落盘)
  3. 算力机推理(分块 base64+md5 双端校验上传 wav/sb → importlib
     独立加载 sv73_digital_human_service → 5090D → {sid}_dh.mp4
     → 分块下载回本地)
  4. attach 生产回填(POST /api/sv73/render/attach)
  5.(默认) 远端 shutdown 自动关机(AutoDL 官方指令, 停止计费)

成本口径(2026-10-01 实录): 全链 ~3-4 分钟/条(含模型加载 60-90s),
￥1.88-2.88/时 → ￥0.1-0.19/条; 批量摊薄加载属 P2 优化(服务常驻),
当前 ≤￥1/条红线达标, 不做进程级摊薄。

配置 env:
  ADH_HOST/ADH_PORT/ADH_USER/ADH_PASSWORD   算力机 SSH(同 adh_run)
  ADH_API_TOKEN / ADH_TOKEN_FILE            AutoDL 开发者 Token——
                         余额止损检查(wallet/balance, 未配则跳过);
                         --boot 开机仅适用"容器实例 Pro"形态(普通实例
                         控制台人工开机, adh_power.py 形态实证)
  SV73_DH_IMAGE_REMOTE   远端口播基准图绝对路径(默认
                         /root/autodl-tmp/assets/ip/zhuxiaomei_front.jpg
                         ——口播基准图 2026-10-02 换版(竹林美女解说
                         竹奕酒), 每次跑批自动从本地
                         assets/ip/zhuxiaomei_front.jpg 上传校验)
  SV73_API               生产 API 基址(默认 https://zxjiu.com)
"""

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import paramiko

PROD_HOST = "root@47.236.61.117"
PROD_CONTAINER = "zhuxiang-backend-1"
API = "https://zxjiu.com"
ADL_API = "https://api.autodl.com"


def wallet_balance() -> float | None:
    """AutoDL 余额(¥)——Token 未配/查询失败返回 None(跳过检查)"""
    import os
    tok = os.environ.get("ADH_API_TOKEN", "").strip()
    f = os.environ.get("ADH_TOKEN_FILE", "").strip()
    if not tok and f and os.path.isfile(f):
        tok = open(f, encoding="utf-8").read().strip()
    if not tok:
        return None
    req = urllib.request.Request(
        ADL_API + "/api/v1/dev/wallet/balance", data=b"{}",
        method="POST",
        headers={"Authorization": tok,
                 "Content-Type": "application/json"})
    try:
        b = json.loads(urllib.request.urlopen(req, timeout=20).read())
        if b.get("code") == "Success":
            return int((b.get("data") or {}).get("assets") or 0) / 1000
    except Exception:  # noqa: BLE001
        return None
    return None

ADH = {
    "host": "connect.weste.seetacloud.com",
    "port": 37632, "user": "root", "password": "dtFrMiCbH2qM",
}
# 口播基准图: 本地(随代码版本) → 每次跑批前自动上传远端(md5 校验)
DH_IMAGE_LOCAL = Path(__file__).resolve().parent / "assets" / "ip" / "zhuxiaomei_front.jpg"
DH_IMAGE_REMOTE = "/root/assets/ip/zhuxiaomei_front.jpg"
# 远端布局(2026-10-01 Pro 迁移: SadTalker 挪入系统盘随镜像, 数据盘
# /root/autodl-tmp 不再依赖——Pro create 全新实例无数据盘)
DH_SADTALKER_DIR = "/root/SadTalker"
REMOTE_ROOT = "/root/_prodyvid"
DH_SVC_LOCAL = (Path(__file__).resolve().parent / "services"
                / "sv73_digital_human_service.py")
REMOTE_SVC = "/root/services/sv73_digital_human_service.py"


# ---------- 生产管道 ----------

def prod_token() -> str:
    pipe = (
        "import asyncio\n"
        "async def m():\n"
        "    from services.auth_service import AuthService\n"
        "    try:\n"
        "        r = await AuthService().register("
        "phone='13800000731', password='test123456', role='admin')\n"
        "    except ValueError:\n"
        "        r = await AuthService().login("
        "'13800000731', 'test123456')\n"
        "    print(r.get('accessToken', ''))\n"
        "asyncio.run(m())\n"
    )
    b64 = base64.b64encode(pipe.encode()).decode()
    out = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=15", PROD_HOST,
         "docker", "exec", PROD_CONTAINER, "python", "-c",
         f'"import base64;exec(base64.b64decode(\'{b64}\'))"'],
        capture_output=True, text=True, timeout=90)
    lines = [l for l in (out.stdout or "").splitlines() if l.strip()]
    if not lines:
        raise RuntimeError("token 获取失败: " + (out.stderr or "")[:200])
    return lines[-1].strip()


def prod_llm_key() -> str:
    out = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=15", PROD_HOST,
         "docker", "exec", PROD_CONTAINER, "printenv", "LLM_API_KEY"],
        capture_output=True, text=True, timeout=60)
    key = (out.stdout or "").strip()
    if not key:
        raise RuntimeError("LLM_API_KEY 获取失败: " + (out.stderr or "")[:150])
    return key


def http(method, path, body=None, token=""):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})})
    try:
        r = urllib.request.urlopen(req, timeout=90)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"detail": raw[:150].decode("utf-8", "replace")}


# ---------- 算力机 SSH ----------

def ssh_connect(tries=30, wait=10, overrides=None):
    last = ""
    conn = dict(ADH, **(overrides or {}))
    for _ in range(tries):
        try:
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(conn["host"], port=int(conn["port"]),
                      username=conn["user"],
                      password=conn["password"],
                      look_for_keys=False, allow_agent=False, timeout=15)
            return c
        except Exception as e:  # noqa: BLE001
            last = str(e)[:120]
            time.sleep(wait)
    raise RuntimeError("SSH 就绪超时: " + last)


def run(ssh, cmd, timeout=290):
    _, so, se = ssh.exec_command(cmd, timeout=timeout)
    out = so.read().decode("utf-8", "replace")
    code = so.channel.recv_exit_status()
    return code, out, se.read().decode("utf-8", "replace")


def push(ssh, local: Path, remote: str, chunk=60000):
    """分块 base64 上传 + md5 双端校验(2026-10-01 b64 未解码坑修复版)"""
    data = local.read_bytes()
    md5 = hashlib.md5(data).hexdigest()
    b64 = base64.b64encode(data).decode()
    ssh.exec_command(f"rm -f '{remote}'")[1].channel.recv_exit_status()
    for i in range(0, len(b64), chunk):
        code, _, err = run(ssh, f"printf '%s' '{b64[i:i+chunk]}'"
                                f" >> '{remote}'", timeout=60)
        if code != 0:
            raise RuntimeError(f"上传分块失败 {local.name}: {err[:100]}")
    code, out, _ = run(
        ssh, f"base64 -d '{remote}' > '{remote}.bin' && mv"
             f" '{remote}.bin' '{remote}' && md5sum '{remote}'")
    rmd5 = (out.split() or [""])[0]
    if rmd5 != md5:
        raise RuntimeError(f"md5 不一致: {local.name}")
    print(f"    upload {local.name}: {len(data)}B md5 ok")


def pull(ssh, remote: str, local: Path):
    code, out, _ = run(ssh, f"md5sum '{remote}'")
    rmd5 = (out.split() or [""])[0]
    code, out, _ = run(ssh, f"wc -c < '{remote}'")
    size = int(out.strip() or "0")
    buf = b""
    chunk = 800000
    for i in range(0, size * 2 + chunk, chunk):
        _, out, _ = run(ssh, f"base64 '{remote}' | dd bs=1 skip={i}"
                               f" count={chunk} 2>/dev/null")
        if not out:
            break
        buf += out.encode()
    data = base64.b64decode(buf)
    if hashlib.md5(data).hexdigest() != rmd5:
        raise RuntimeError(f"下载 md5 不一致: {local.name}")
    local.write_bytes(data)
    print(f"    download {local.name}: {len(data)}B md5 ok")
    return len(data)


# ---------- 单条全链 ----------

def _ffmpeg_bin() -> str:
    """ffmpeg 路径(PATH 优先, 项目自带兜底)——发布转码用"""
    import shutil
    p = shutil.which("ffmpeg")
    if p:
        return p
    for cand in (r"D:\网站架构设计\ffmpeg\ffmpeg.exe",
                 "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if os.path.isfile(cand):
            return cand
    raise RuntimeError("ffmpeg not found(PATH/项目自带均缺)")


def _h264ize(mp4: Path) -> int:
    """SadTalker 出片转 H264 标准竖屏——两个发布坑一次修复
    (2026-10-01 xhs 发布失败实证):
    ① mp4v→avc1: OpenCV VideoWriter 默认 mpeg4 Part 2, xhs 转码器不认
    ② 非标分辨率→720x1280: 基准图竖屏非标(2026-10-01 竹小妹
       852x1514 → 2026-10-02 换版竹林美女 1344x1792), "高清绿标"
       5 分钟不出(转码器只标清标准分辨率)——bot 降级人工模式;
       转标准竖屏后绿标恢复, 全自动闭环不再依赖人工点发布。
       cover 口径(2026-10-02 修正): 换版图 3:4 与 9:16 差 33%,
       直缩会横向压扁变形——scale 铺满+居中 crop 裁左右,
       人物居中构图主体无损
    同名覆盖, 返回转码后字节数"""
    import subprocess as sp
    tmp = mp4.with_suffix(".h264.mp4")
    r = sp.run([_ffmpeg_bin(), "-y", "-i", str(mp4),
                "-vf", ("scale=720:1280:"
                        "force_original_aspect_ratio=increase,"
                        "crop=720:1280"),  # cover: 等比铺满+居中裁
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-movflags", "+faststart", str(tmp)],
               capture_output=True, timeout=300)
    if r.returncode != 0 or not tmp.is_file():
        raise RuntimeError("H264 转码失败: "
                           + r.stderr.decode("utf-8", "replace")[-200:])
    tmp.replace(mp4)
    return mp4.stat().st_size


def build_one(ssh, sid: str, token: str, key: str) -> bool:
    print(f"  == {sid} ==")
    # ① 拉 storyboard
    s, b = http("GET", f"/api/sv73/script/{sid}", token=token)
    if s != 200 or (b.get("data") or {}).get("scriptId") != sid:
        print("    拉取失败:", s, str(b)[:150])
        return False
    sb = b["data"]
    if (sb.get("template") or {}).get("name") != "dh_oral":
        print("    非 dh_oral:", (sb.get("template") or {}).get("name"))
        return False

    # ② 本地 TTS(竹语)
    import os
    os.environ["LLM_API_KEY"] = key
    os.environ["SV73_TTS_MODE"] = "on"
    from services.sv73_render_service import Sv73RenderService
    wav = Sv73RenderService()._tts_audio(sb)
    if wav is None or not Path(wav).is_file():
        print("    TTS 失败")
        return False

    # ③ 上传 → 远端 E2E → 下载
    run(ssh, f"mkdir -p {REMOTE_ROOT}")
    push(ssh, Path(wav), f"{REMOTE_ROOT}/{sid}_tts.wav")
    push(ssh, Path("storyboards") / f"{sid}_prod.json"
         if (Path("storyboards") / f"{sid}_prod.json").is_file()
         else _dump_sb(sb, sid), f"{REMOTE_ROOT}/{sid}_sb.json")
    script = f"""
import importlib.util, json, os
from pathlib import Path
os.environ['SV73_VIDEO_DIR'] = '{REMOTE_ROOT}'
os.environ['SV73_DH_IMAGE'] = '{DH_IMAGE_REMOTE}'
os.environ['SV73_DH_MODE'] = 'real'
os.environ['SV73_SADTALKER_DIR'] = '{DH_SADTALKER_DIR}'
spec = importlib.util.spec_from_file_location("dh", "{REMOTE_SVC}")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
sb = json.loads(Path('{REMOTE_ROOT}/{sid}_sb.json').read_text())
r = mod.Sv73DigitalHumanService().build(sb, Path('{REMOTE_ROOT}/{sid}_tts.wav'))
sz = Path(r['video']).stat().st_size
print('RESULT', json.dumps({{k: r[k] for k in ('scriptId','sizeBytes','engine')}}))
assert sz > 100000
print('E2E PASS')
"""
    b64 = base64.b64encode(script.encode()).decode()
    run(ssh, f"echo {b64} | base64 -d > /tmp/_one.py && rm -f /tmp/_one.log"
             f" && nohup /root/miniconda3/envs/py312/bin/python /tmp/_one.py"
             f" > /tmp/_one.log 2>&1 & echo started")
    ok = False
    for _ in range(8):
        time.sleep(40)
        _, out, _ = run(ssh, "ps aux | grep '[_]one.py' >/dev/null"
                              " && echo R || echo E; tail -2 /tmp/_one.log")
        if out.startswith("E"):
            ok = "E2E PASS" in out
            print("    remote:", out.strip().splitlines()[-1][:100])
            break
    if not ok:
        _, out, _ = run(ssh, "tail -8 /tmp/_one.log")
        print("    远端推理未过:", out.strip()[-300:])
        return False
    local_mp4 = Path(f"sv73_videos/{sid}_dh.mp4")
    size = pull(ssh, f"{REMOTE_ROOT}/{sid}_dh.mp4", local_mp4)
    # 平台兼容转码(mp4v→H264, xhs 转码器不认 mpeg4 Part 2——发布
    # 失败实证修复); attach 产物即转码版
    size = _h264ize(local_mp4)
    print(f"    h264: {size}B")
    # 产物回地清理(Pro 系统盘仅 ~40G: mp4 时间戳文件+中间帧是大头,
    # 下载校验过即远端全清——本地 mp4/wav 是权威产物)
    run(ssh, f"rm -f {REMOTE_ROOT}/*.mp4 {REMOTE_ROOT}/{sid}_tts.wav"
             f" {REMOTE_ROOT}/{sid}_sb.json")
    run(ssh, f"find {REMOTE_ROOT} -mindepth 1 -maxdepth 1 -type d"
             f" -empty -delete")

    # ④ attach
    pages = Sv73RenderService().render_pages(sb)
    s, b = http("POST", "/api/sv73/render/attach",
                {"scriptId": sid, "video": str(local_mp4.resolve()),
                 "pages": [str(Path(p).resolve()) for p in pages],
                 "audioTrack": str(Path(wav).resolve()),
                 "sizeBytes": size}, token=token)
    d = (b.get("data") or {})
    print(f"    attach {s} contentId={d.get('contentId')}")
    return s == 200


def _dump_sb(sb: dict, sid: str) -> Path:
    Path("storyboards").mkdir(exist_ok=True)
    p = Path("storyboards") / f"{sid}_prod.json"
    p.write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")
    return p


# ---------- 主流程 ----------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sid", help="单条剧本号")
    ap.add_argument("--sids", help="逗号分隔多条")
    ap.add_argument("--boot", action="store_true",
                    help="先 AutoDL API 开机(需 ADH_API_TOKEN; 普通实例"
                         "不在 API 体系——控制台人工开机, 见 adh_power.py"
                         "形态实证)")
    ap.add_argument("--no-shutdown", action="store_true",
                    help="跑完不自动关机(默认关)")
    ap.add_argument("--min-balance", type=float, default=10.0,
                    help="余额止损阈值(¥, 默认 10; 低于即退出不跑)")
    ap.add_argument("--pro", default="",
                    help="容器实例Pro uuid(缺省读 ADH_INSTANCE_UUID env——"
                         "create 成功时 setx 自动持久化)——snapshot 动态"
                         "取 SSH 连接, 跑批后 API power_off")
    args = ap.parse_args()
    sids = [s.strip() for s in
            (args.sids or args.sid or "").split(",") if s.strip()]
    if not sids:
        print(__doc__)
        return 2

    # 余额止损(2026-10-01 实证 wallet/balance; Token 未配则跳过)
    bal = wallet_balance()
    if bal is not None:
        print(f"[余额] ¥{bal:.2f}", end="")
        if bal < args.min_balance:
            print(f" < 阈值 ¥{args.min_balance:.2f}——止损退出"
                  "(AutoDL 控制台充值后再跑)")
            return 1
        print(f"(阈值 ¥{args.min_balance:.2f}) ✓")
    else:
        print("[余额] ADH_API_TOKEN 未配置, 跳过检查")

    if args.boot:
        print("[0/5] AutoDL API 开机 ...")
        r = subprocess.run([sys.executable, "adh_power.py", "on"],
                           capture_output=True, text=True, timeout=300)
        print(r.stdout[-400:])
        if "POWER_ON OK" not in r.stdout:
            return 1

    # SSH 连接: --pro=API snapshot 动态取(Pro 实例每次 create 连接信息
    # 都变) / 缺省=普通实例 ADH env 硬编码
    overrides = None
    pro_uuid = args.pro or os.environ.get("ADH_INSTANCE_UUID", "").strip()
    if pro_uuid:
        print(f"[Pro] snapshot 动态连接 ({pro_uuid[:12]}...) ...")
        from adh_power import call as adl_call
        b = adl_call("GET", "/api/v1/dev/instance/pro/snapshot",
                     {"instance_uuid": pro_uuid})
        d = b.get("data") or {}
        if b.get("code") != "Success" or not d.get("proxy_host"):
            print("  snapshot 失败:", json.dumps(b, ensure_ascii=False)[:200])
            return 1
        overrides = {"host": d["proxy_host"],
                     "port": int(d.get("ssh_port") or 0),
                     "user": "root",
                     "password": d.get("root_password", "")}
        print(f"  {overrides['host']}:{overrides['port']}")
    print("[SSH] 就绪轮询 ...")
    ssh = ssh_connect(overrides=overrides)

    print("[KEY] 生产凭证管道 ...")
    token = prod_token()
    key = prod_llm_key()

    # 素材/服务文件上传(随代码版本——Pro 全新实例不依赖旧数据盘)
    print("[基准图] 竹小妹定版图上传(md5 校验) ...")
    if not DH_IMAGE_LOCAL.is_file() or not DH_SVC_LOCAL.is_file():
        print(f"  缺素材: {DH_IMAGE_LOCAL} / {DH_SVC_LOCAL}")
        return 1
    run(ssh, f"mkdir -p {DH_IMAGE_REMOTE.rsplit('/', 1)[0]}"
             f" {REMOTE_ROOT} {REMOTE_SVC.rsplit('/', 1)[0]}")
    push(ssh, DH_IMAGE_LOCAL, DH_IMAGE_REMOTE)
    push(ssh, DH_SVC_LOCAL, REMOTE_SVC)

    ok = fail = 0
    for sid in sids:
        if build_one(ssh, sid, token, key):
            ok += 1
        else:
            fail += 1

    print(f"== 批量完成: {ok} ok / {fail} fail ==")
    if not args.no_shutdown:
        if pro_uuid:
            print("[关机] Pro API power_off ...")
            from adh_power import call as adl_call
            ssh.close()
            b = adl_call("POST", "/api/v1/dev/instance/pro/power_off",
                         {"instance_uuid": pro_uuid})
            print("    power_off:", b.get("code"), b.get("msg", ""))
        else:
            print("[关机] 容器内 shutdown(官方指令, 停止计费) ...")
            try:
                ssh.exec_command("shutdown")
                time.sleep(3)
            finally:
                ssh.close()
            print("    已下发关机——控制台核对状态")
    else:
        ssh.close()
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
