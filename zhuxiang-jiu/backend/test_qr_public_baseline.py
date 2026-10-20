"""39号扫码登录公开面基线（R2 死条目修复锁定, 2026-10-21）

竹盾发现: 白名单 EXACT 无参形态(/api/entry/qr/scan 等)永不匹配实际
{qr_id} 路由——scan/exchange/cancel 三端点被中间件 401 永拦, 扫码登录
协议链路断裂。修复=新增 POST 公开正则段。本测试锁修复后行为:

    公开: scan(扫码动作, 无身份依赖) / exchange(PC 未登录态用一次性
          loginTicket 换 JWT——防重放靠 ticket 60s 一次性, 非身份认证)
          / cancel(取消, 幂等)
    登录态: confirm(设计内: X-Member-Id 由 JWT 注入, entry_routes 注释明示)
    不回归: create(EXACT) / status(GET 前缀) 公开面不变
    不误放: 正则 $ 锚定, 深路径/其他动作不放行
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from core.auth_middleware import is_public_path  # sys.path 注入后导入


def test_qr_scan_exchange_cancel_public():
    """修复主断言: 带 qr_id 段三端点 POST 公开(原 401 永拦)"""
    for action in ("scan", "exchange", "cancel"):
        assert is_public_path(f"/api/entry/qr/abc-123/{action}", "POST"), action


def test_qr_confirm_still_authed():
    """confirm 保持登录态(设计内: 手机端已登录才可确认换票)"""
    assert not is_public_path("/api/entry/qr/abc-123/confirm", "POST")


def test_qr_existing_public_not_regressed():
    """原公开面不回归: create EXACT / status GET 前缀 / ws GET 放行"""
    assert is_public_path("/api/entry/qr/create", "POST")
    assert is_public_path("/api/entry/qr/abc-123/status", "GET")
    assert is_public_path("/api/entry/qr/abc-123/ws", "GET")


def test_qr_post_regex_no_overreach():
    """正则不误放: 深路径/陌生动作/其他模块路径不放行

    注: /api/entry/qr/ 下 GET 一律走 GET 前缀放行(status/ws 设计内),
    无法以 GET 断言"仅 POST 档", 故以 DELETE 等方法与路径形态验证。
    """
    assert not is_public_path("/api/entry/qr/abc-123/scan/x", "POST")
    assert not is_public_path("/api/entry/qr/abc-123/bogus", "POST")
    assert not is_public_path("/api/entry/qr/abc-123/scan", "DELETE")
    assert not is_public_path("/api/other/qr/abc-123/scan", "POST")


def test_qr_dead_exact_entries_removed():
    """EXACT 死条目已删除(避免假公开登记, registry 漂移治理)"""
    from core.auth_middleware import PUBLIC_EXACT
    for dead in ("/api/entry/qr/scan", "/api/entry/qr/exchange", "/api/entry/qr/cancel"):
        assert dead not in PUBLIC_EXACT, dead
    assert "/api/entry/qr/create" in PUBLIC_EXACT  # 活条目保留
