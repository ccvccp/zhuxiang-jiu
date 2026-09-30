"""AutoDL 实例 API 开关机工具(数字人 GPU 轨自动编排配套)

用法: python adh_power.py <on|off|status|list|balance|images|create|snapshot>
      on/off/status [--uuid <uuid>]          实例电源(Pro 形态)
      balance                               余额(¥, 通用)
      images                                私有镜像列表(image_uuid)
      create --image <uuid> [--gpu 5090-p] [--disk 0] [--name x]
                                            创建 Pro 实例(私有镜像启动)
      snapshot --uuid <uuid>                SSH 连接信息(dh_batch --pro 用)
Token: ADH_API_TOKEN env 或 ADH_TOKEN_FILE env 指向的文件(单行)
实例 UUID: ADH_INSTANCE_UUID env 或 --uuid(缺省走 list 探测唯一实例)

API(官方开放 API, 2026-10-01 实证):
  POST /api/v1/dev/wallet/balance        账户余额(¥=assets/1000)
  GET  /api/v1/dev/instance/pro/status   {instance_uuid}
  POST /api/v1/dev/instance/pro/list     实例列表(列 uuid/GPU/状态)
  POST /api/v1/dev/instance/pro/power_on {instance_uuid, payload:"gpu"}
  POST /api/v1/dev/instance/pro/power_off {instance_uuid}
鉴权: Authorization: <token> ——开发者 Token(控制台→设置→开发者Token,
长期有效, 与网页 session 无关——第三方 autodl-cli 同机制)。

⚠ 形态实证(2026-10-01): 开发者 API 实例面仅覆盖"容器实例 Pro"
(autodl-cli 开源全端点=/api/v1/dev/instance/pro/*); 网页控制台租的
普通实例(本项目 5090D)不在 API 体系(pro/list 空实证)——普通实例
开机走控制台人工(约 10 秒)或 P2 迁移 Pro 形态; 关机不受限(容器内
shutdown 官方指令, dh_batch 收尾默认执行)。balance 对两种形态通用。
"""

import json
import os
import sys
import time
import urllib.request

API_HOST = "https://api.autodl.com"


def _token() -> str:
    tok = os.environ.get("ADH_API_TOKEN", "").strip()
    if tok:
        return tok
    f = os.environ.get("ADH_TOKEN_FILE", "").strip()
    if f and os.path.isfile(f):
        return open(f, encoding="utf-8").read().strip()
    raise SystemExit("缺 ADH_API_TOKEN(或 ADH_TOKEN_FILE)")


def _uuid(argv: list) -> str:
    if "--uuid" in argv:
        return argv[argv.index("--uuid") + 1]
    u = os.environ.get("ADH_INSTANCE_UUID", "").strip()
    if u:
        return u
    return ""


def _opt(argv: list, flag: str) -> str:
    """命令行 --flag value 取值(缺省空串)"""
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return ""


def call(method: str, path: str, body=None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API_HOST + path, data=data, method=method,
        headers={"Authorization": _token(),
                 "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw)
        except ValueError:
            return {"code": "HTTP%d" % e.code,
                    "msg": raw[:150].decode("utf-8", "replace")}
    except urllib.error.URLError as e:
        return {"code": "URLError", "msg": str(e)[:150]}


def _resolve_uuid(argv: list) -> str:
    u = _uuid(argv)
    if u:
        return u
    b = call("POST", "/api/v1/dev/instance/pro/list",
             {"page_index": 1, "page_size": 20})
    items = ((b.get("data") or {}).get("list")
             if isinstance(b.get("data"), dict) else b.get("data")) or []
    if not items:
        print(json.dumps(b, ensure_ascii=False)[:300])
        raise SystemExit("实例列表为空/失败——先跑 list 核对 Token")
    if len(items) == 1:
        it = items[0]
        print("唯一实例: %s (%s)" % (
            it.get("instance_uuid"), it.get("status")))
        return it.get("instance_uuid", "")
    for it in items:
        print("  %s %s %s" % (it.get("instance_uuid"),
                               it.get("gpu_alias", ""),
                               it.get("status")))
    raise SystemExit("多实例——ADH_INSTANCE_UUID 指定其一")


def main() -> int:
    argv = sys.argv[2:] if len(sys.argv) > 2 else []
    action = sys.argv[1] if len(sys.argv) > 1 else "status"

    if action == "list":
        b = call("POST", "/api/v1/dev/instance/pro/list",
                 {"page_index": 1, "page_size": 20})
        items = ((b.get("data") or {}).get("list")
                 if isinstance(b.get("data"), dict) else b.get("data")) or []
        for it in items:
            print(json.dumps(it, ensure_ascii=False)[:220])
        if not items:
            print(json.dumps(b, ensure_ascii=False)[:300])
        return 0

    if action == "balance":
        # 账户余额(¥=assets/1000)——dh_batch 跑批前置止损检查
        b = call("POST", "/api/v1/dev/wallet/balance", {})
        d = b.get("data") or {}
        yuan = int(d.get("assets") or 0) / 1000
        spent = int(d.get("accumulate") or 0) / 1000
        print(f"余额 ¥{yuan:.2f}(累计消费 ¥{spent:.2f})")
        return 0 if b.get("code") == "Success" else 1

    if action == "images":
        # 私有镜像列表(保存镜像后查 image_uuid)
        b = call("POST", "/api/v1/dev/image/private/list",
                 {"page_index": 1, "page_size": 20})
        items = ((b.get("data") or {}).get("list")
                 if isinstance(b.get("data"), dict) else []) or []
        for it in items:
            print(f"{it.get('image_uuid')}  {it.get('image_name')}")
        if not items:
            print("(空——先在控制台『保存镜像』)", json.dumps(
                b, ensure_ascii=False)[:200])
        return 0

    if action == "create":
        # Pro 实例创建(5090-p + 私有镜像——2026-10-01 迁移实施)
        img = _opt(argv, "--image")
        if not img:
            print("create 须 --image <私有镜像UUID>(images 命令查)")
            return 2
        body = {
            "req_gpu_amount": 1,
            "expand_system_disk_by_gb": int(_opt(argv, "--disk") or "0"),
            "gpu_spec_uuid": _opt(argv, "--gpu") or "5090-p",
            "image_uuid": img,
            "cuda_v_from": 128,   # 镜像含 torch cu128——主机驱动须 ≥12.8
            "instance_name": _opt(argv, "--name") or "zhuxiang-dh",
        }
        b = call("POST", "/api/v1/dev/instance/pro/create", body)
        d = b.get("data")
        print("create:", b.get("code"), d, b.get("msg", ""))
        if b.get("code") == "Success" and d:
            print("CREATED", d)   # 实例 uuid——后续 on/snapshot/off 用
        return 0 if b.get("code") == "Success" else 1

    if action == "snapshot":
        # 实例详情(SSH 地址/端口/密码——dh_batch --pro 动态连接用)
        u = _uuid(argv)
        if not u:
            print("snapshot 须 --uuid 或 ADH_INSTANCE_UUID")
            return 2
        b = call("GET", "/api/v1/dev/instance/pro/snapshot",
                 {"instance_uuid": u})
        d = b.get("data") or {}
        if b.get("code") != "Success":
            print("snapshot 失败:", json.dumps(b, ensure_ascii=False)[:200])
            return 1
        print("ssh:", d.get("ssh_command"))
        print("SNAPSHOT_JSON", json.dumps(
            {"host": d.get("proxy_host"), "port": d.get("ssh_port"),
             "password": d.get("root_password"),
             "status_note": d.get("usage_info", {}).get("valid")},
            ensure_ascii=False))
        return 0

    u = _resolve_uuid(argv)

    if action == "status":
        b = call("GET", "/api/v1/dev/instance/pro/status",
                 {"instance_uuid": u})
        print("status:", b.get("data"), b.get("msg", ""))
    elif action == "on":
        b = call("POST", "/api/v1/dev/instance/pro/power_on",
                 {"instance_uuid": u, "payload": "gpu"})
        print("power_on:", b.get("code"), b.get("msg", ""))
        if b.get("code") != "Success":
            return 1
        # 轮询 status → running(开机含调度约 10-60s)
        for _ in range(24):
            time.sleep(5)
            s = call("GET", "/api/v1/dev/instance/pro/status",
                     {"instance_uuid": u})
            st = s.get("data")
            print("  ...", st)
            if st == "running":
                print("POWER_ON OK")
                return 0
        print("超时未 running——控制台核对")
        return 1
    elif action == "off":
        b = call("POST", "/api/v1/dev/instance/pro/power_off",
                 {"instance_uuid": u})
        print("power_off:", b.get("code"), b.get("msg", ""))
        return 0 if b.get("code") == "Success" else 1
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    import urllib.error  # noqa: F401
    sys.exit(main())
