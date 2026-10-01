"""48号P0 小竹智能语音中枢 Docker 实机验收

运行方式:
    python verify_xiaozhu_p0_live.py [基址]

前置: 容器已运行(含 P0 代码, 镜像已重建)。

覆盖(计划 §四, 真实容器):
    01 正常业务零影响
    02 会话开启 + 指令集
    03 唤醒直达 E2E(看新品→产品卡片+jump)
    04 免唤醒连续对话 E2E(指代消解)
    05 八指令逐一实测(导航/优惠/转人工/帮助/信值引导)
    06 未唤醒反语音霸权(不执行只提示)
    07 语音降级(无 key: 结构化失败+keyboard 兜底)
    08 隐私红线(PII mask 落库 + 一键清除级联)
    09 鉴权与业务回归

每轮验收前清理 zhuxiang:voice48:* 残留, ×2 轮幂等验证
(2026-10-01 起全量清理改为显式开启: VOICE48_FULL_WIPE=1——
生产 voice48 已承载真实观测/业务数据, P3-1 唤醒分值轮次
watch_xiaozhu_wake.py 的判定数据源, 默认只靠脚本自身
§08 会话级 DELETE 级联自清, 不再全量 wipe)。

鉴权(2026-10-01 Bearer 改造):
    AUTH_MODE=strict 后旧 X-Member-Id 裸头全 401——改为
    种子账号登录取 accessToken, 全程 Authorization: Bearer
    (身份头由中间件 inject_identity 从 JWT 注入, 客户端
    不再自传 X-Member-Id)。种子口径与 e2e_auth_helper.py
    同源: member1=13800000001/test123456; 经 XIAOZHU_E2E_
    PHONE/PASSWORD 可覆盖。刷新轨不引入——access 2h TTL 对
    验收短周期足够(e2e_auth_helper 同款设计决策); member1
    有 AI 风控短信二验可能, 命中时换管理员号(13800000002)。
"""
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []
# Bearer 轨(e2e_auth_helper 种子口径; env 可覆盖)
E2E_PHONE = os.environ.get("XIAOZHU_E2E_PHONE", "13800000001")
E2E_PASSWORD = os.environ.get("XIAOZHU_E2E_PASSWORD",
                               "test123456")
# §06 第二成员(member 级免唤醒窗跨会话 5 分钟——主成员
# §03-05 已唤醒, 反语音霸权检查须换"从未唤醒"的成员)
E2E_PHONE_2 = os.environ.get("XIAOZHU_E2E_PHONE_2",
                             "13800000002")   # 管理员号
AUTH: dict = {}        # login_e2e_member() 填充 Bearer 头
MEMBER_ID: int = 0     # 登录响应 memberId(会话归属断言用)


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def clear_voice48() -> None:
    """全量清理 voice48 键空间(破坏性——默认关闭)

    容器名 2026-10-01 修正: zhuxiang-jiu-redis-1(旧年代
    遗留名, 静默 no-op)→zhuxiang-redis-1(生产实际)。修正
    即生效, 故同步加防护门: 生产 voice48 已承载 P3-1 唤醒
    分值观察轮次/信值绑定/积分账本等真实数据, 全量 wipe
    仅一次性环境经 VOICE48_FULL_WIPE=1 显式开放。
    """
    if os.environ.get("VOICE48_FULL_WIPE") != "1":
        print("[WARN] VOICE48_FULL_WIPE!=1, 跳过 voice48 全量"
              "清理(保护生产观测数据); 一次性环境可显式开启")
        return
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:voice48:*"],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def call(method, path, body=None, headers=None,
         expect=(200,)):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                  method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            code, text = resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed)


def login_headers(phone: str, password: str) -> tuple[dict, int]:
    """strict 模式 Bearer 登录(返回 请求头+memberId; 失败即退)"""
    ok, (code, body) = call("POST", "/api/auth/login",
                            body={"phone": phone,
                                  "password": password})
    token = (body or {}).get("accessToken")
    if not (ok and token):
        print(f"[FATAL] E2E 登录失败({phone}): "
              f"{code} {str(body)[:200]}")
        print("        (member1 有 AI 风控短信二验可能——"
              "换管理员号: XIAOZHU_E2E_PHONE=13800000002)")
        sys.exit(2)
    member_id = int((body or {}).get("memberId") or 0)
    return {"Authorization": f"Bearer {token}"}, member_id


def login_e2e_member() -> None:
    """主成员 Bearer 轨引导(失败即退——验收无身份不可跑)"""
    global AUTH, MEMBER_ID
    AUTH, MEMBER_ID = login_headers(E2E_PHONE, E2E_PASSWORD)
    print(f"[AUTH] Bearer 就绪 member={MEMBER_ID} ({E2E_PHONE})")


def main():
    print("=" * 62)
    print("48号·P0 小竹智能语音中枢 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    login_e2e_member()
    clear_voice48()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel?role=member",
                         headers=AUTH)
    record("35号 Hub 面板回归", code == 200, str(code))

    print("\n[02 会话开启 + 指令集]")
    ok, (code, body) = call(
        "POST", "/api/xiaozhu/sessions",
        body={"channel": "voice"}, headers=AUTH)
    sid = body.get("sessionId")
    record("开启会话",
           code == 200 and body.get("success") is True
           and sid >= 1 and body.get("memberId") == MEMBER_ID,
           str(body)[:70])
    ok, (code, body) = call("GET", "/api/xiaozhu/commands",
                            headers=AUTH)
    cmds = body.get("commands") or []
    record("指令集自描述(28 条)",
           code == 200 and len(cmds) == 28
           and body.get("wakeWords") == ["小竹"],
           str(len(cmds)))

    print("\n[03 唤醒直达 E2E]")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，看看有什么新上线产品"},
        headers=AUTH)
    record("看新品直达(卡片+jump)",
           code == 200 and "新品" in body.get("reply", "")
           and (body.get("card") or {}).get("type")
           == "product_list"
           and body.get("jump")
           == "/#/pages/products/index?sort=new"
           and len((body.get("card") or {})
                   .get("items") or []) >= 1,
           str(body.get("reply"))[:50])
    cards_items = (body.get("card") or {}).get("items") or []

    print("\n[04 免唤醒连续对话 E2E]")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "这个多少钱"}, headers=AUTH)
    record("指代消解(这个多少钱)",
           code == 200
           and body.get("wakeHint") is False
           and "元" in body.get("reply", ""),
           str(body.get("reply"))[:50])
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "查优惠"}, headers=AUTH)
    record("免唤醒直接解析(查优惠)",
           code == 200
           and ("活动" in body.get("reply", "")
                or "优惠" in body.get("reply", "")),
           body.get("reply", "")[:40])

    print("\n[05 六指令逐一实测]")
    for text, check in (
            ("小竹，竹香酒多少钱",
             lambda b: "元" in b.get("reply", "")),
            ("小竹，查我的信值",
             lambda b: "绑定" in b.get("reply", "")),
            ("小竹，我的信值余额",
             lambda b: "绑定" in b.get("reply", "")),
            ("小竹，打开购物车",
             lambda b: b.get("jump") == "/#/pages/checkout/index"),
            ("小竹，转人工客服",
             lambda b: "人工" in b.get("reply", "")),
            ("小竹，你能干什么",
             lambda b: (b.get("card") or {}).get("type")
             == "help"),
    ):
        ok, (code, body) = call(
            "POST", f"/api/xiaozhu/sessions/{sid}/text",
            body={"text": text}, headers=AUTH)
        record(f"「{text}」",
               code == 200 and check(body),
               body.get("reply", "")[:40])

    print("\n[06 反语音霸权]")
    # member 级免唤醒窗跨会话(5 分钟)——主成员 §03-05 已
    # 唤醒, 换第二成员(默认管理员号)保证"从未唤醒"前置态
    auth2, _ = login_headers(E2E_PHONE_2, E2E_PASSWORD)
    ok, (code, body) = call(
        "POST", "/api/xiaozhu/sessions",
        body={"channel": "voice"}, headers=auth2)
    sid2 = body.get("sessionId")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid2}/text",
        body={"text": "看新品"}, headers=auth2)
    record("新会话未唤醒不执行",
           code == 200
           and body.get("wakeHint") is True
           and "小竹" in body.get("reply", ""),
           str(body.get("wakeHint")) + body.get("reply",
                                                "")[:30])
    ok, (code, body) = call(
        "DELETE", f"/api/xiaozhu/sessions/{sid2}",
        headers=auth2)
    record("霸权会话自清(sid2 级联)",
           code == 200, str(body.get("removedRecords")))

    print("\n[07 语音降级(无 key)]")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/voice",
        body={"audioBase64": base64.b64encode(
            b"fake-audio").decode()},
        headers=AUTH)
    record("语音降级结构化兜底",
           code == 200
           and "语音" in body.get("reply", "")
           and body.get("fallbackHint") == "keyboard",
           str(body.get("reply"))[:50])

    print("\n[08 隐私红线]")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，我的手机 13812345678 帮查下订单"},
        headers=AUTH)
    ok, (code, view) = call(
        "GET", f"/api/xiaozhu/sessions/{sid}", headers=AUTH)
    turns = view.get("turns") or []
    masked = [t for t in turns
              if "1381234" not in (t.get("rawText") or "")]
    record("PII mask 落库(手机号脱敏)",
           len(masked) == len(turns),
           str([t.get("rawText") for t in turns
                if "手机" in (t.get("rawText") or "")]))
    ok, (code, body) = call(
        "DELETE", f"/api/xiaozhu/sessions/{sid}", headers=AUTH)
    record("一键清除级联",
           code == 200
           and body.get("removedRecords", 0) >= 8,
           str(body.get("removedRecords")))
    ok, (code, _) = call(
        "GET", f"/api/xiaozhu/sessions/{sid}",
        expect=(404,), headers=AUTH)
    record("清除后 404", code == 404, str(code))

    print("\n[09 鉴权与业务回归]")
    ok, (code, _) = call(
        "POST", "/api/xiaozhu/sessions",
        body={"channel": "voice"})
    record("无 Bearer 401(strict 网关)", code == 401, str(code))
    ok, (code, body) = call(
        "GET", "/api/product/list?sort=new")
    record("产品模块回归",
           code == 200
           and (body.get("count") or 0) >= 1,
           str(code))
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
