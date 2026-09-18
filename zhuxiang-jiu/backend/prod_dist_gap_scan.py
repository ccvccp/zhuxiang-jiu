"""生产 dist 调用点盘点: 提取全部 /api 路径, 与 strict 白名单比对找游客可达性缺口"""
import glob
import re

# --- 1. 提取 dist 全部 API 调用路径(静态源码扫描) ---
api_paths = set()
template_re = re.compile(r"/api/[a-zA-Z0-9/_$\{\}.-]+")

# dist js
import glob
for js in glob.glob("/var/www/zxjiu/dist/js/*.js"):
    try:
        src = open(js, encoding="utf-8", errors="ignore").read()
    except OSError:
        continue
    for m in template_re.findall(src):
        api_paths.add(m)

# --- 2. 白名单(与 core/auth_middleware.PUBLIC_EXACT/PUBLIC_GET_PREFIXES 同口径) ---
PUBLIC_EXACT = {
    "/api/auth/register", "/api/auth/login", "/api/auth/login/sms",
    "/api/auth/refresh", "/api/auth/logout", "/api/sms/send",
    "/api/sms/verify",
    "/api/auth/oauth/wechat/url", "/api/auth/oauth/wechat/callback",
    "/api/auth/oauth/alipay/url", "/api/auth/oauth/alipay/callback",
    "/api/auth/oauth/qq/url", "/api/auth/oauth/qq/callback",
    "/api/auth/oauth/bind-phone",
    "/api/member/login", "/api/member/register",
    "/api/member/login/bonus", "/api/admin/login",
    "/api/entry/recognize", "/api/entry/login", "/api/entry/step-up/verify",
    "/api/entry/qr/create", "/api/entry/qr/scan",
    "/api/entry/qr/exchange", "/api/entry/qr/cancel",
    "/api/entry/registration-merge", "/api/entry/bio/challenge",
    "/api/entry/bio/verify",
    "/api/payment/callback/pay", "/api/payment/callback/refund",
    "/api/payment/callback/payout", "/api/logistics/callback/track",
    "/api/decision/health", "/api/monitor/health",
    "/api/maintenance/health",
}
PUBLIC_GET_PREFIXES = (
    "/api/product", "/api/activity/list", "/api/activity/stats/",
    "/api/activity/leaderboard/", "/api/ads", "/api/agreements",
    "/api/groupbuy/products", "/api/groupbuy/tiers",
    "/api/payment/channels/active", "/api/entry/qr/",
)


def is_public(path: str, method_hint: str = "GET") -> bool:
    if path in PUBLIC_EXACT:
        return True
    if method_hint == "GET" and any(
            path.startswith(p) for p in PUBLIC_GET_PREFIXES):
        return True
    return False


# --- 3. 比对: 含模板变量的路径归并前缀展示 ---
static_paths = {p for p in api_paths if "{" not in p and "$" not in p}
dynamic_paths = {p for p in api_paths if p not in static_paths}

gaps = sorted(p for p in static_paths if not is_public(p))
dyn_prefixes = sorted({re.sub(r"/[^/]*$", "/*", p) for p in dynamic_paths})

print(f"dist 调用点总数: 静态 {len(static_paths)} + 模板 {len(dynamic_paths)}")
print(f"\n--- 静态路径白名单缺口({len(gaps)} 条, strict 下游客/裸头 401) ---")
for p in gaps:
    print(" ", p)
print(f"\n--- 模板路径前缀({len(dyn_prefixes)} 条, 人工判定) ---")
for p in dyn_prefixes[:20]:
    print(" ", p)
