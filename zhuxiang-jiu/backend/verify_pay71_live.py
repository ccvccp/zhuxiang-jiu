"""71号·AI智能支付端口大模型 Docker 实机综合验收

运行方式:
    # 容器以 off 态启动(默认 compose 即可——HTTP 断言含 off 铁律);
    # 管道进程内自行注入 PAY71_MODE=shadow 不影响 uvicorn 常态
    docker compose -p zhuxiang-jiu up -d
    python verify_pay71_live.py [基址]

覆盖(九期核心链综合, 真实容器 Redis 态, ×2 轮幂等):
    01 零影响: 健康检查(与 71号 无关的既有端点)
    02 HTTP 观测面(off 不受影响): ports/predict/allocation/
       selfheal/recon 五字典面 Bearer 200
    03 鉴权语义: 无头 403 + 裸头 X-Role 403(46+1 剥头实证)
    04 HTTP 决策面 off 铁律: orchestrate/compute/redteam 409
    05 容器内管道(shadow 态进程注入)九期核心链:
       P0 panorama/model_status + P1 前兆窗口基线
       + P6 决策链路图 + P7 漂移检测/治理视图
       + P8 红队四向量全防御(allDefended=4)

清种 pattern 用宽匹配 zhuxiang:pay71*(60号 live 曾因带冒号
pattern 永不匹配下划线表名键致"幂等"清种失效——教训勿再犯)。
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.1:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []

CONTAINER = "zhuxiang-jiu-backend-1"
REDIS = "zhuxiang-jiu-redis-1"


def _admin_token() -> str:
    """46+1 安全修复: 裸头 X-Role 被 auth_middleware 剥除
    → admin Bearer 走容器内服务层注册轨(HTTP register 无
    role 字段防提权, 54号实机验收范式); 已注册回退 login。"""
    pipe = (
        "import asyncio\n"
        "async def m():\n"
        "    from services.auth_service import AuthService\n"
        "    try:\n"
        "        r = await AuthService().register(\n"
        "            phone='13800000710',\n"
        "            password='test123456',\n"
        "            role='admin')\n"
        "    except ValueError:\n"
        "        r = await AuthService().login(\n"
        "            '13800000710', 'test123456')\n"
        "    print(r.get('accessToken', ''))\n"
        "asyncio.run(m())\n"
    )
    out = subprocess.run(
        ["docker", "exec", CONTAINER, "python", "-c", pipe],
        capture_output=True, text=True, timeout=60)
    lines = [l for l in (out.stdout or "").splitlines()
             if l.strip()]
    if not lines:
        raise RuntimeError(
            "admin token 获取失败: " + (out.stderr or "")[:200])
    return lines[-1].strip()


ADMIN = {"Authorization": "Bearer " + _admin_token()}
BARE = {"X-Role": "admin"}


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def call(method, path, body=None, headers=None,
         expect=(200,)):
    data = json.dumps(body).encode() if body is not None \
        else None
    req = urllib.request.Request(BASE + path, data=data,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            code, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text[:200]}
    return code in expect, (code, parsed)


def redis_del_keys(pattern: str) -> None:
    out = subprocess.run(
        ["docker", "exec", REDIS,
         "redis-cli", "--scan", "--pattern", pattern],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", REDIS, "redis-cli",
             "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


# 容器内管道: 九期核心链(shadow 态进程注入, 纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['PAY71_MODE'] = 'shadow'\n"
    "os.environ.pop('PAY71_KILL', None)\n"
    "os.environ.pop('PAY71_IMMUNITY', None)\n"
    "async def m():\n"
    "    out = {}\n"
    "    from services.pay71_p0_service import (\n"
    "        Pay71P0Service)\n"
    "    from services.pay71_p1_service import (\n"
    "        Pay71P1Service)\n"
    "    from services.pay71_p6_service import (\n"
    "        Pay71P6Service)\n"
    "    from services.pay71_p7_service import (\n"
    "        Pay71P7Service)\n"
    "    from services.pay71_p8_service import (\n"
    "        Pay71P8Service)\n"
    # P0 底座: 全景 + 模型状态
    "    p0 = await Pay71P0Service().panorama()\n"
    "    out['p0_ports'] = len(\n"
    "        p0.get('ports') or {})\n"
    # P1 前兆窗口基线(空窗)
    "    w = await Pay71P1Service(\n"
    "        ).precursor_window('wechat')\n"
    "    out['p1_score'] = w.get(\n"
    "        'windowScore')\n"
    # P6 决策链路图
    "    g = await Pay71P6Service(\n"
    "        ).tracegraph()\n"
    "    out['p6_keys'] = len(g)\n"
    # P7 漂移检测 + 治理视图
    "    d = await Pay71P7Service(\n"
    "        ).detect_drift()\n"
    "    out['p7_drift'] = len(d)\n"
    "    gv = await Pay71P7Service(\n"
    "        ).governance_view()\n"
    "    out['p7_gov'] = len(gv)\n"
    # P8 红队四向量
    "    rt = await Pay71P8Service(\n"
    "        ).run_redteam()\n"
    "    vectors = rt.get('vectors') or []\n"
    "    out['p8_vectors'] = len(vectors)\n"
    "    out['p8_defended'] = sum(\n"
    "        1 for v in vectors\n"
    "        if v.get('defended') is True)\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n"
)


def run_pipeline() -> dict:
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", PIPELINE],
        capture_output=True, text=True, timeout=180)
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError(
        "管道无 JSON 输出: "
        + (out.stderr or out.stdout or "")[:300])


def verify_round(round_no: int) -> None:
    print(f"\n========== 第 {round_no} 轮 ==========")

    # 01 零影响: 健康检查
    ok, (code, _) = call(
        "GET", "/api/decision/health")
    record("零影响: 健康检查 200", ok,
           f"status={code}")

    # 02 观测面(off 不受影响)
    for path, label in (
            ("/api/pay71/ports", "端口池"),
            ("/api/pay71/predict/dict", "预判字典"),
            ("/api/pay71/allocation/dict", "调配字典"),
            ("/api/pay71/selfheal/dict", "自愈字典"),
            ("/api/pay71/recon/dict", "对账字典")):
        ok, (code, body) = call(
            "GET", path, headers=ADMIN)
        record(f"观测面 {label} 200", ok,
               f"status={code}")

    # 03 鉴权语义(46+1)
    ok, (code, _) = call(
        "GET", "/api/pay71/ports")
    record("无头 403", code == 403,
           f"status={code}")
    ok, (code, _) = call(
        "GET", "/api/pay71/ports", headers=BARE)
    record("裸头 X-Role 403(46+1 剥头)",
           code == 403, f"status={code}")

    # 04 决策面 off 铁律 + 保护面永续铁律
    # (71号设计: orchestrate 属保护面, off/KILL 均不关停)
    ok, (code, _) = call(
        "POST", "/api/pay71/selfheal/orchestrate",
        body={"portId": "wechat"}, headers=ADMIN)
    record("保护面永续(orchestrate off 200)",
           ok, f"status={code}")
    for path, body, label in (
            ("/api/pay71/allocation/compute",
             {"memberId": 10, "amount": 100.0},
             "调配计算"),
            ("/api/pay71/immunity/redteam",
             {}, "红队执行")):
        ok, (code, _) = call(
            "POST", path, body=body,
            headers=ADMIN, expect=(409,))
        record(f"决策面 off 409({label})", ok,
               f"status={code}")

    # 05 容器内管道: 九期核心链
    r = run_pipeline()
    record("P0 全景 7 端口",
           r.get("p0_ports") == 7,
           str(r.get("p0_ports")))
    record("P1 前兆窗口基线可查",
           isinstance(r.get("p1_score"),
                      (int, float)),
           str(r.get("p1_score")))
    record("P6 决策链路图结构",
           r.get("p6_keys", 0) > 0,
           str(r.get("p6_keys")))
    record("P7 漂移检测+治理视图",
           r.get("p7_drift", 0) > 0
           and r.get("p7_gov", 0) > 0,
           f"{r.get('p7_drift')}/{r.get('p7_gov')}")
    record("P8 红队四向量全防御",
           r.get("p8_vectors") == 4
           and r.get("p8_defended") == 4,
           str((r.get("p8_vectors"),
                r.get("p8_defended"))))


def main() -> int:
    for rnd in (1, 2):
        # 清种(宽 pattern: 实际键为 zhuxiang:pay71_<table>:*
        # 60号 live 曾因 zhuxiang:pay60:* 带冒号失配致幂等失效)
        redis_del_keys("zhuxiang:pay71*")
        verify_round(rnd)
    print("\n" + "=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"71号 live 综合验收: "
          f"{PASS} 通过 / {FAIL} 失败")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
