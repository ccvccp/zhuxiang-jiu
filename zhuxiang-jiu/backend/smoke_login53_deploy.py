"""53号上线部署冒烟验证(部署清单 §四——八项)

运行方式(容器 healthy 后):
    python smoke_login53_deploy.py
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.2:8000"
PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name} — {detail}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def call(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode() or "{}")
        except ValueError:
            body = {}
        return e.code, body


def main() -> int:
    admin = {"X-Role": "admin"}
    member = {"X-Member-Id": "1"}

    # 1 健康检查
    code, _ = call("GET", "/api/decision/health")
    record("① 健康检查", code == 200, str(code))

    # 2 既有业务零影响(35号面板)
    code, _ = call("GET", "/api/hub/panel")
    record("② 35号 Hub 面板", code == 200, str(code))

    # 3 存量登录零接管(39号 entry——注册+密码登录)
    phone = "13900005399"
    code_reg, body_reg = call("POST", "/api/auth/register", {
        "phone": phone, "password": "Smoke#53x",
        "nickname": "冒烟53"})
    if code_reg in (200, 409):   # 409=已注册(重复冒烟)
        code_login, body_login = call("POST", "/api/entry/login", {
            "mode": "password", "phone": phone,
            "password": "Smoke#53x"})
        status = ((body_login.get("data") or {})
                  .get("status"))
        record("③ 存量登录零接管(39号)",
               code_login == 200 and status in (
                   "authenticated", "step_up_required"),
               f"HTTP {code_login} → {status}")
    else:
        record("③ 存量登录零接管(39号)", False,
               f"register HTTP {code_reg}")

    # 4 观测面 registry(五通道+四态+六指标+话术17)
    code, body = call("GET", "/api/login53/registry",
                      headers=admin)
    ok = code == 200 and len(body.get("channels") or {}) == 5 \
        and len(body.get("portalStates") or {}) == 4 \
        and len(body.get("metrics") or {}) == 6 \
        and (body.get("scripts") or {}).get("total") == 17
    record("④ 观测面 registry 自描述", ok, str(code))

    # 5 编排面 off 铁律
    code, body = call("POST", "/api/login53/auth/orchestrate",
                      body={"channel": "passkey"},
                      headers=member)
    detail = str(body.get("detail")
                  or body.get("error") or "")[:24]
    record("⑤ 编排面 off 409 铁律",
           code == 409 and "off" in detail,
           f"HTTP {code} {detail}")

    # 6 快照/看板可达(Redis 持久化——非空属预期)
    code_l, body_l = call("GET", "/api/login53/metrics/latest",
                          headers=admin)
    has_snap = (body_l.get("snapshot") or {}) != {}
    code_d, body_d = call("GET", "/api/login53/dashboard",
                         headers=admin)
    ok = code_l == 200 and code_d == 200 \
        and "byChannel" in body_d
    record("⑥ 快照/看板可达", ok,
           f"latest={code_l}(快照{'存在' if has_snap else '空态'})"
           f" dashboard={code_d}")

    # 7 鉴权(无头 403)
    code_o, _ = call("POST", "/api/login53/auth/orchestrate",
                     body={"channel": "passkey"})
    code_r, _ = call("GET", "/api/login53/registry")
    record("⑦ 鉴权 403(编排+管理面)",
           code_o == 403 and code_r == 403,
           f"{code_o}/{code_r}")

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"冒烟: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
