"""73号(sv)·数字人 GPU 轨批量编排器(生产拆链正式化, 2026-10-01 实录)

用法: python dh_batch.py --sids sv73_xxx[,sv73_yyy] [--no-boot] [--no-shutdown]
      python dh_batch.py --sid sv73_xxx                      # 单条
      python dh_batch.py --sids s1,s2                        # 自动开机+跑批+自动关机
      python dh_batch.py --pro <uuid> --sids s1,s2           # 指定 Pro 实例全自动

模板支持(2026-10-04 A1 起):
  dh_oral  单镜 15s 口播(原形态)
  dh_mix   多镜混合: 口播钩子 9s(GPU)+产品卡/合规卡(本地 Ken Burns
           合成 compose_mix→{sid}_mix.mp4 为 attach 成片)

周产量口径(2026-10-04 定标): 5 条/周——单次跑批 5 条一次开机
连跑, 摊薄模型加载 60-90s(周中段可 --no-shutdown 补批)。

链路(build_dh_dev 四步的拆链形式——GPU 机 import 巨网不可控的分段铁律):
  0. AutoDL 自动开机(ensure 候选回退: --pro > ADH_PRO_UUIDS >
     ADH_INSTANCE_UUID; 无库存自动换下一台) → snapshot 实时坐标
  1. 拉生产 storyboard(SSH 容器管道取 token)
  2. 本地 TTS(78号竹语; LLM_API_KEY 经生产容器管道注入, 不落盘;
     dh_mix 仅 TTS 口播镜 voiceover——全量拼接会拉长 GPU 出片)
  3. 算力机推理(分块 base64+md5 双端校验上传 wav/sb → importlib
     独立加载 sv73_digital_human_service → 5090D → {sid}_dh.mp4
     → 分块下载回本地)
  4. attach 生产回填(POST /api/sv73/render/attach; dh_mix 先本地
     compose_mix 合成多镜成片)
  5.(默认) 远端 shutdown 自动关机(普通实例容器指令 / Pro 实例 API
    power_off)——按量计费形态下保持在线=持续扣费, 跑完即关;
    --no-shutdown 仅限同会话连续补批(用完手动关机止费)

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
  SV73_API               生产 API 基址(默认 https://zyjiu.com)
"""

import argparse
import base64
import functools
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import paramiko

# 长跑进度实时(2026-10-17): 管道下 python 块缓冲曾致 30 分钟无输出
print = functools.partial(print, flush=True)  # noqa: A001

PROD_HOST = "root@47.236.61.117"
PROD_CONTAINER = "zhuxiang-backend-1"
API = "https://zyjiu.com"
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

# 凭证治理(2026-10-17): 明文默认已移除, ADH_* env 注入(同 adh_run;
# Pro 实例经 --pro/snapshot 动态覆盖不受影响)
ADH = {
    "host": os.environ.get("ADH_HOST", "connect.westd.seetacloud.com"),
    "port": int(os.environ.get("ADH_PORT", "31568")),
    "user": os.environ.get("ADH_USER", "root"),
    "password": os.environ.get("ADH_PASSWORD", "").strip(),
}
# 口播基准图: persona → (本地, 远端)——每次跑批前全部自动上传远端
# (md5 校验); 2026-10-05 第二人设小竹帅哥入列(竹林帅哥解说竹奕酒
# 定版图 1.9MB, SadTalker 非标分辨率由 dh 服务 720x1280 归一处理)
# 2026-10-06 网站角色库 +4 中老年(1728x2304 竖版统一规格; 儿童
# role_tong_* 不入出片链——广告法§40, 见 sv73_script_service 出片闸)
_ASSET_IP = Path(__file__).resolve().parent / "assets" / "ip"
_ROLES = _ASSET_IP / "roles"
DH_IMAGES = {
    "zhuxiaomei": (_ASSET_IP / "zhuxiaomei_front.jpg",
                   "/root/assets/ip/zhuxiaomei_front.jpg"),
    "zhuxiaomei_autumn": (_ASSET_IP / "zhuxiaomei_autumn.jpg",
                          "/root/assets/ip/zhuxiaomei_autumn.jpg"),
    "zhuxiaomei_spring": (_ASSET_IP / "zhuxiaomei_spring.jpg",
                          "/root/assets/ip/zhuxiaomei_spring.jpg"),
    "zhuxiaomei_winter": (_ASSET_IP / "zhuxiaomei_winter.jpg",
                          "/root/assets/ip/zhuxiaomei_winter.jpg"),
    "xiaozhushuaige": (_ASSET_IP / "xiaozhushuaige_front.jpg",
                       "/root/assets/ip/xiaozhushuaige_front.jpg"),
    "xiaozhushuaige_autumn": (_ASSET_IP / "xiaozhushuaige_autumn.jpg",
                              "/root/assets/ip/"
                              "xiaozhushuaige_autumn.jpg"),
    "xiaozhushuaige_winter": (_ASSET_IP / "xiaozhushuaige_winter.jpg",
                              "/root/assets/ip/"
                              "xiaozhushuaige_winter.jpg"),
    "xiaozhushuaige_spring": (_ASSET_IP / "xiaozhushuaige_spring.jpg",
                              "/root/assets/ip/"
                              "xiaozhushuaige_spring.jpg"),
    "role_zhongnian_f": (_ROLES / "role_zhongnian_f.jpg",
                         "/root/assets/ip/roles/role_zhongnian_f.jpg"),
    "role_zhongnian_f_autumn": (
        _ROLES / "role_zhongnian_f_autumn.jpg",
        "/root/assets/ip/roles/role_zhongnian_f_autumn.jpg"),
    "role_zhongnian_f_spring": (
        _ROLES / "role_zhongnian_f_spring.jpg",
        "/root/assets/ip/roles/role_zhongnian_f_spring.jpg"),
    "role_zhongnian_f_winter": (
        _ROLES / "role_zhongnian_f_winter.jpg",
        "/root/assets/ip/roles/role_zhongnian_f_winter.jpg"),
    "role_zhongnian_m": (_ROLES / "role_zhongnian_m.jpg",
                         "/root/assets/ip/roles/role_zhongnian_m.jpg"),
    "role_zhongnian_m_autumn": (
        _ROLES / "role_zhongnian_m_autumn.jpg",
        "/root/assets/ip/roles/role_zhongnian_m_autumn.jpg"),
    "role_zhongnian_m_spring": (
        _ROLES / "role_zhongnian_m_spring.jpg",
        "/root/assets/ip/roles/role_zhongnian_m_spring.jpg"),
    "role_zhongnian_m_winter": (
        _ROLES / "role_zhongnian_m_winter.jpg",
        "/root/assets/ip/roles/role_zhongnian_m_winter.jpg"),
    "role_laonian_f": (_ROLES / "role_laonian_f.jpg",
                       "/root/assets/ip/roles/role_laonian_f.jpg"),
    "role_laonian_f_autumn": (
        _ROLES / "role_laonian_f_autumn.jpg",
        "/root/assets/ip/roles/role_laonian_f_autumn.jpg"),
    "role_laonian_f_spring": (
        _ROLES / "role_laonian_f_spring.jpg",
        "/root/assets/ip/roles/role_laonian_f_spring.jpg"),
    "role_laonian_f_winter": (
        _ROLES / "role_laonian_f_winter.jpg",
        "/root/assets/ip/roles/role_laonian_f_winter.jpg"),
    "role_laonian_m": (_ROLES / "role_laonian_m.jpg",
                       "/root/assets/ip/roles/role_laonian_m.jpg"),
    "role_laonian_m_autumn": (
        _ROLES / "role_laonian_m_autumn.jpg",
        "/root/assets/ip/roles/role_laonian_m_autumn.jpg"),
    "role_laonian_m_spring": (
        _ROLES / "role_laonian_m_spring.jpg",
        "/root/assets/ip/roles/role_laonian_m_spring.jpg"),
    "role_laonian_m_winter": (
        _ROLES / "role_laonian_m_winter.jpg",
        "/root/assets/ip/roles/role_laonian_m_winter.jpg"),
    # 2026-10-17 青年双角色(竹林才俊/佳人, 素材 3072x4096 竖版;
    # default + alt 两档)
    "role_qingnian_m": (_ROLES / "role_qingnian_m.jpg",
                        "/root/assets/ip/roles/role_qingnian_m.jpg"),
    "role_qingnian_m_alt": (_ROLES / "role_qingnian_m_alt.jpg",
                            "/root/assets/ip/roles/"
                            "role_qingnian_m_alt.jpg"),
    "role_qingnian_f": (_ROLES / "role_qingnian_f.jpg",
                        "/root/assets/ip/roles/role_qingnian_f.jpg"),
    "role_qingnian_f_alt": (_ROLES / "role_qingnian_f_alt.jpg",
                            "/root/assets/ip/roles/"
                            "role_qingnian_f_alt.jpg"),
}
# 兼容旧引用(缺省人设)
DH_IMAGE_LOCAL = DH_IMAGES["zhuxiaomei"][0]
DH_IMAGE_REMOTE = DH_IMAGES["zhuxiaomei"][1]


def dh_image_for(persona: str,
                 outfit: str = "default") -> str:
    """storyboard persona(+outfit) → 远端基准图路径

    outfit="spring" 等装束档 → "{persona}_{outfit}" 键(2026-10-09
    IP 双主角春节装入列); 档位未注册回落该人设常装, 人设未注册
    回落竹小妹(fail-soft 双层)。
    """
    if outfit and outfit != "default":
        key = f"{persona}_{outfit}"
        if key in DH_IMAGES:
            return DH_IMAGES[key][1]
    return DH_IMAGES.get(persona, DH_IMAGES["zhuxiaomei"])[1]


# 装束档全局(--outfit 注入; "default"=常装, "spring"=春节装——
# 2026-10-09 IP 双主角春节照入列)
OUTFIT = "default"
# 远端布局(2026-10-01 Pro 迁移: SadTalker 挪入系统盘随镜像, 数据盘
# /root/autodl-tmp 不再依赖——Pro create 全新实例无数据盘)
DH_SADTALKER_DIR = "/root/SadTalker"
REMOTE_ROOT = "/root/_prodyvid"
DH_SVC_LOCAL = (Path(__file__).resolve().parent / "services"
                / "sv73_digital_human_service.py")
REMOTE_SVC = "/root/services/sv73_digital_human_service.py"


# ---------- 生产管道 ----------

def prod_admin_creds() -> tuple[str, str]:
    """生产 admin 凭证(2026-10-17 治理): 明文默认已移除, 须 env 注入"""
    phone = os.environ.get("PROD_ADMIN_PHONE", "").strip()
    pwd = os.environ.get("PROD_ADMIN_PASSWORD", "").strip()
    if not (phone and pwd):
        raise RuntimeError(
            "PROD_ADMIN_PHONE/PROD_ADMIN_PASSWORD 未配置"
            "(环境变量注入, 本脚本不读 .env——明文默认 2026-10-17 已移除, "
            "见 .env.example)")
    # 自有 env 凭证, 仅放行数字/字母: 该值拼入远端 pipe 字符串, 防注入
    if not (phone.isdigit() and pwd.isalnum()):
        raise ValueError(
            "PROD_ADMIN_PHONE 须纯数字 / PROD_ADMIN_PASSWORD 须字母数字")
    return phone, pwd


def prod_token() -> str:
    phone, pwd = prod_admin_creds()
    pipe = (
        "import asyncio\n"
        "async def m():\n"
        "    from services.auth_service import AuthService\n"
        "    try:\n"
        "        r = await AuthService().register("
        f"phone='{phone}', password='{pwd}', role='admin')\n"
        "    except ValueError:\n"
        "        r = await AuthService().login("
        f"'{phone}', '{pwd}')\n"
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
    if not conn.get("password"):
        raise RuntimeError(
            "ADH_PASSWORD 未配置(环境变量注入, 见 .env.example; "
            "Pro 实例经 --pro/snapshot 自动取)")
    for _ in range(tries):
        try:
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(conn["host"], port=int(conn["port"]),
                      username=conn["user"],
                      password=conn["password"],
                      look_for_keys=False, allow_agent=False, timeout=15)
            # 死链加速检测(2026-10-17 west-E 代理断连实证):
            # 无 keepalive 时 channel 读可挂死不抛错
            c.get_transport().set_keepalive(15)
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


# push 拼参组大小探测记忆(跨文件复用, 避免每文件重复撞代理击杀区)
_PUSH_TARGET = [360000]


def push(ssh, local: Path, remote: str, chunk=90000):
    """分块 base64 上传 + md5 双端校验(2026-10-01 b64 未解码坑修复版;
    2026-10-17 chunk 60K→90K——exec 通道轮次延迟敏感, 轮次↓1/3,
    90K base64 字符仍在 Linux 单参数 128KB 上限内)"""
    data = local.read_bytes()
    md5 = hashlib.md5(data).hexdigest()
    b64 = base64.b64encode(data).decode()
    # 远端父目录确保(2026-10-08 角色族 roles/ 子目录实证缺失坑)
    rdir = remote.rsplit("/", 1)[0] if "/" in remote else "."
    ssh.exec_command(
        f"mkdir -p '{rdir}'")[1].channel.recv_exit_status()
    ssh.exec_command(f"rm -f '{remote}'")[1].channel.recv_exit_status()
    parts = [b64[i:i + chunk] for i in range(0, len(b64), chunk)]
    # 慢链批量拼参(2026-10-17 west-E 实证): 逐块 exec 往返太慢; 单参数
    # 上限 128KB(90K 安全)。代理对超长命令行敏感(1.4M+ 实证即 10054
    # 断链), 故组上限 720K(击杀区之下), 探测记忆跨文件复用(_PUSH_TARGET
    # 模块级, 失败减半成功倍增), 断链由外层重连 + md5 跳过续传兜底
    from collections import deque
    q = deque(parts)
    while q:
        n = max(1, _PUSH_TARGET[0] // chunk)
        group = [q.popleft() for _ in range(min(n, len(q)))]
        try:
            args = " ".join(f"'{p}'" for p in group)
            code, _, err = run(ssh, "printf '%s' " + args
                               + f" >> '{remote}'", timeout=180)
            ok = code == 0
        except Exception:  # noqa: BLE001  断链/超大命令 EOFError 等
            ok, err = False, "exec 异常(断链或命令超限)"
        if ok:
            _PUSH_TARGET[0] = min(_PUSH_TARGET[0] * 2, 720000)
            continue
        if len(group) == 1:
            raise RuntimeError(
                f"上传分块失败 {local.name}: {str(err)[:100]}")
        for p in reversed(group):
            q.appendleft(p)
        _PUSH_TARGET[0] = max(_PUSH_TARGET[0] // 2, chunk)
    code, out, _ = run(
        ssh, f"base64 -d '{remote}' > '{remote}.bin' && mv"
             f" '{remote}.bin' '{remote}' && md5sum '{remote}'")
    rmd5 = (out.split() or [""])[0]
    if rmd5 != md5:
        raise RuntimeError(f"md5 不一致: {local.name}")
    print(f"    upload {local.name}: {len(data)}B md5 ok")


def _remote_md5s(ssh, remotes: list) -> dict:
    """一次 exec 批量取远端 md5(md5sum 多文件; 缺失/报错行自然跳过)"""
    script = " ".join(f"'{r}'" for r in remotes)
    _, out, _ = run(ssh, f"md5sum {script} 2>/dev/null", timeout=120)
    m = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and len(parts[0]) == 32:
            m[parts[1].strip()] = parts[0]
    return m


def push_many(ssh, items, parallel=1, label="", reconnect=None):
    """批量上传(2026-10-17 提速): ①远端 md5 一次批量预检, 未变文件
    直接跳过(实例文件随盘持久, 同实例复跑 24 图秒级过) ②差异文件逐个
    推, 单文件失败经 reconnect() 重连重试至多 3 次(west-E 代理按连接
    限流实证: 每连接推 1-2 文件即被 10054 重置)。返回(可能重连后的)
    ssh 供调用方续用。默认串行——该代理 2/4 并发通道即断且断后挂死,
    主收益本就在 md5 跳过, 全量上传仅在全新实例发生一次"""
    from concurrent.futures import ThreadPoolExecutor
    need, skipped = [], 0
    remote_md5 = _remote_md5s(ssh, [r for _, r in items])
    for local, remote in items:
        h = hashlib.md5(Path(local).read_bytes()).hexdigest()
        if remote_md5.get(remote) == h:
            skipped += 1
        else:
            need.append((local, remote))
    print(f"    {label}{len(items)} 项: 未变跳过 {skipped} / 待传 {len(need)}")
    if not need:
        return ssh

    def one(t):
        nonlocal ssh
        local, remote = t
        for i in range(5):
            try:
                push(ssh, local, remote)
                return
            except Exception as e:  # noqa: BLE001
                if i == 2 or reconnect is None:
                    raise
                print(f"    [断线重连] {Path(local).name}: "
                      f"{str(e)[:80]}")
                ssh = reconnect()

    if parallel > 1 and len(need) > 1:
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            list(ex.map(one, need))
    else:
        for t in need:
            one(t)
    return ssh


def pull(ssh, remote: str, local: Path):
    """下载(md5 校验)——2026-10-17 修复 O(n²): 原实现 base64|dd bs=1
    skip=N 在管道上不可 lseek, 逐字节丢弃前缀(7MB 累计 6200 万次单字
    节读, 实测 18 分钟+)。现改 b64 先落盘, dd 对常规文件大块切片
    (lseek O(1), 轮次线性)。"""
    _, out, _ = run(ssh, f"md5sum '{remote}'")
    rmd5 = (out.split() or [""])[0]
    tmp = f"/tmp/_pull_{Path(remote).name}.b64"
    _, out, _ = run(ssh, f"base64 '{remote}' > '{tmp}'"
                          f" && wc -c < '{tmp}'")
    size = int(out.strip().splitlines()[-1] or "0") if out.strip() else 0
    buf = b""
    chunk = 900000
    for k in range(0, (size + chunk - 1) // chunk):
        _, out, _ = run(ssh, f"dd if='{tmp}' bs={chunk} skip={k}"
                              f" count=1 2>/dev/null")
        if not out:
            break
        buf += out.encode()
    run(ssh, f"rm -f '{tmp}'")
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
       人物居中构图主体无损。
       ⚠ 2026-10-02 发布实证补充: xhs 平台「检测为高清视频」绿标
       仅 ≥1080P 片出现——720x1280 触发「视频分辨率较低」黄色警告
       (xhs_publish_1_filled.png 截图实证), 5 分钟绿标轮询必然 miss
       (发布本身安全, bot 已同批校准警告特征); 高清根治=输出升
       1080x1920(基准图 1344 宽>1080 原生满足, cover 上采样
       1.071x)——本参数 2026-10-02 已切换, 下批出片生效
    同名覆盖, 返回转码后字节数"""
    import subprocess as sp
    from services.sv73_render_service import LOUDNORM_AF
    tmp = mp4.with_suffix(".h264.mp4")
    r = sp.run([_ffmpeg_bin(), "-y", "-i", str(mp4),
                "-vf", ("scale=1080:1920:"
                        "force_original_aspect_ratio=increase,"
                        "crop=1080:1920"),  # cover: 等比铺满+居中裁
                # 响度标准化(-14 LUFS, 2026-10-05 采纳): dh_oral 单镜
                # 轨发布件在此转码, 与 mix 轨同口径
                "-af", LOUDNORM_AF,
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
    tpl = (sb.get("template") or {}).get("name")
    if tpl not in ("dh_oral", "dh_mix", "drama_monologue"):
        print("    非数字人模板:", tpl)
        return False

    # ② 本地 TTS(竹语)——混合模板仅口播镜(首镜): 全量拼接会把
    #    卡片镜配音也送 GPU, 口播出片时长超计划致 xfade 编排漂移
    import os
    os.environ["LLM_API_KEY"] = key
    os.environ["SV73_TTS_MODE"] = "on"
    tts_sb = sb
    if tpl in ("dh_mix", "drama_monologue"):
        tts_sb = {**sb, "scenes": [sb["scenes"][0]]}
    from services.sv73_render_service import Sv73RenderService
    wav = Sv73RenderService()._tts_audio(tts_sb)
    if wav is None or not Path(wav).is_file():
        print("    TTS 失败")
        return False

    # ③ 上传 → 远端 E2E → 下载(基准图按 storyboard persona 注入——
    #    混人设批次各用各的定版图, 2026-10-05 第二人设)
    run(ssh, f"mkdir -p {REMOTE_ROOT}")
    push(ssh, Path(wav), f"{REMOTE_ROOT}/{sid}_tts.wav")
    push(ssh, Path("storyboards") / f"{sid}_prod.json"
         if (Path("storyboards") / f"{sid}_prod.json").is_file()
         else _dump_sb(sb, sid), f"{REMOTE_ROOT}/{sid}_sb.json")
    script = f"""
import importlib.util, json, os
from pathlib import Path
os.environ['SV73_VIDEO_DIR'] = '{REMOTE_ROOT}'
os.environ['SV73_DH_IMAGE'] = '{dh_image_for(sb.get("persona") or "zhuxiaomei", OUTFIT)}'
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

    # ③b 混合模板多镜合成(本地): 口播镜(刚转码)+卡片镜 Ken Burns
    #    → {sid}_mix.mp4 为 attach 成片(口播单镜留档不 attach)
    rs = Sv73RenderService()
    pages = rs.render_pages(sb)
    if tpl in ("dh_mix", "drama_monologue"):
        final = Path(f"sv73_videos/{sid}_mix.mp4")
        rs.compose_mix(local_mp4, pages[1:], sb, final)
        size = final.stat().st_size
        print(f"    mix: {size}B")
        local_mp4 = final

    # ④ attach
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
                    help="(兼容保留, 空操作——2026-10-17 起自动开机默认开)")
    ap.add_argument("--no-boot", action="store_true",
                    help="跳过自动开机(实例已在线, 或 ADH_* env 手工坐标"
                         "直连模式)")
    ap.add_argument("--no-shutdown", action="store_true",
                    help="跑完不自动关机(默认关——按量计费形态, 保持在线"
                         "=持续扣费; 仅同一会话连续补批时用, 用完记得"
                         "手动关机止费)")
    ap.add_argument("--min-balance", type=float, default=10.0,
                    help="余额止损阈值(¥, 默认 10; 低于即退出不跑)")
    ap.add_argument("--pro", default="",
                    help="首选 Pro 实例 uuid(优先级最高; 缺省读 "
                         "ADH_PRO_UUIDS 有序清单)——snapshot 动态"
                         "取 SSH 连接, 跑批后 API power_off")
    ap.add_argument("--outfit", default="default",
                    help="装束档(default=常装, spring=春节装——IP 双主角"
                         "2026-10-09 入列; 档位未注册回落常装)")
    args = ap.parse_args()
    sids = [s.strip() for s in
            (args.sids or args.sid or "").split(",") if s.strip()]
    if not sids:
        print(__doc__)
        return 2
    global OUTFIT
    OUTFIT = (args.outfit or "default").strip()
    if OUTFIT != "default":
        print(f"[装束] {OUTFIT}")

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

    # SSH 连接(2026-10-17 全自动开关机): ensure 候选序
    # (--pro > ADH_PRO_UUIDS > ADH_INSTANCE_UUID)——已 running 直接用,
    # 未开机则 power_on, 无库存自动回退下一台; snapshot 取实时坐标
    # (Pro 每次开机连接信息都变)。--no-boot: 指定实例仅取 snapshot,
    # 未指定则 ADH_* env 手工坐标直连(无 API 开机能力)
    overrides = None
    used_uuid = ""
    pro_uuid = args.pro or os.environ.get("ADH_INSTANCE_UUID", "").strip()
    from adh_power import candidate_uuids, ensure as adh_ensure
    if not args.no_boot and candidate_uuids(args.pro):
        print("[0/5] AutoDL 自动开机(ensure 候选回退) ...")
        c = adh_ensure(args.pro)
        if not c:
            print("  全部候选实例不可用(见上)——退出")
            return 1
        overrides = {"host": c["host"], "port": int(c["port"]),
                     "user": "root", "password": c["password"]}
        used_uuid = c["uuid"]
        print(f"  [实例] {used_uuid} @ {c['host']}:{c['port']}")
    elif args.no_boot and pro_uuid:
        print("[0/5] --no-boot: 跳过开机, snapshot 取坐标 ...")
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
        used_uuid = pro_uuid
        print(f"  {overrides['host']}:{overrides['port']}")
    else:
        print("[0/5] 无 Pro 实例配置——ADH_* env 手工坐标直连(无自动开机)")
    print("[SSH] 就绪轮询 ...")
    ssh = ssh_connect(overrides=overrides)

    print("[KEY] 生产凭证管道 ...")
    token = prod_token()
    key = prod_llm_key()

    # 素材/服务文件上传(随代码版本——Pro 全新实例不依赖旧数据盘)
    print("[基准图] 全人设定版图上传(md5 校验) ...")
    if not DH_SVC_LOCAL.is_file():
        print(f"  缺服务文件: {DH_SVC_LOCAL}")
        return 1
    for persona, (local, remote) in DH_IMAGES.items():
        if not local.is_file():
            print(f"  缺素材: {local}")
            return 1
    run(ssh, f"mkdir -p {DH_IMAGE_REMOTE.rsplit('/', 1)[0]}"
             f" {REMOTE_ROOT} {REMOTE_SVC.rsplit('/', 1)[0]}")
    ssh = push_many(
        ssh,
        [(local, remote) for local, remote in DH_IMAGES.values()]
        + [(DH_SVC_LOCAL, REMOTE_SVC)],
        label="[基准图] ",
        reconnect=lambda: ssh_connect(overrides=overrides))

    ok = fail = 0
    for sid in sids:
        if build_one(ssh, sid, token, key):
            ok += 1
        else:
            fail += 1

    print(f"== 批量完成: {ok} ok / {fail} fail ==")
    if not args.no_shutdown:
        if used_uuid:
            print(f"[关机] Pro API power_off ({used_uuid}) ...")
            from adh_power import call as adl_call
            ssh.close()
            b = adl_call("POST", "/api/v1/dev/instance/pro/power_off",
                         {"instance_uuid": used_uuid})
            print("    power_off:", b.get("code"), b.get("msg", ""))
        else:
            print("[关机] 容器内 shutdown(官方指令) ...")
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
