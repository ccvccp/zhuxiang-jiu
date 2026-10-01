/**
 * 统一鉴权 Token 管理工具(P5.2: strict 模式全站可用的最后一块拼图)
 *
 * 设计要点:
 *   - 会员 JWT 为统一凭证: POST /api/auth/login 返回 accessToken(2h)+refreshToken(7d)
 *     双令牌; strict 模式下所有 /api 请求携带 Authorization: Bearer <accessToken>
 *   - 管理员登录: 先走 POST /api/admin/login 校验管理员凭证(会话体系),
 *     再用管理员绑定的会员手机号换取 JWT——知识库/AI 学习管理端点走
 *     会员 JWT 注入 x-role: admin 路径(中间件 inject_identity)
 *   - 无独立登录服务时退化为兼容模式提示(compat 下 X-Role 兼容头仍可用)
 *
 * 登录页使用:
 *     Auth.login({ phone, password })            → 会员登录
 *     Auth.adminLogin({ username, password, totpCode? }) → 管理员登录
 * 管理页使用:
 *     Auth.apiHeaders()   → { Authorization: 'Bearer ...' } 或 null(未登录)
 *     Auth.requireLogin() → 未登录跳转 login.html(带 redirect 参数)
 *     Auth.mountBadge(container) → 登录态 UI(昵称+退出)
 *
 * 存储: localStorage 'zhuxiang.auth'(JSON: token/refreshToken/memberId/
 *       nickname/role/phone/expiresAt)
 */
'use strict';

var Auth = (function () {
    var STORAGE_KEY = 'zhuxiang.auth';
    var LOGIN_PAGE = 'login.html';
    // access token 有效期(秒, 与后端 JWT_ACCESS_TTL 一致); 本地提前 60s 视为过期
    var ACCESS_TTL = 7200;
    var EXPIRE_MARGIN = 60;

    // ---------- 存储 ----------
    function read() {
        try {
            var raw = localStorage.getItem(STORAGE_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch (e) { return null; }
    }

    function write(data) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    }

    function clear() {
        localStorage.removeItem(STORAGE_KEY);
    }

    // ---------- 查询 ----------
    function isLoggedIn() {
        var s = read();
        if (!s || !s.token) return false;
        return (s.expiresAt || 0) > Date.now() + EXPIRE_MARGIN * 1000;
    }

    function hasRefresh() {
        var s = read();
        return !!(s && s.refreshToken);
    }

    function getToken() {
        return isLoggedIn() ? read().token : null;
    }

    function getInfo() {
        var s = read();
        if (!s) return null;
        return { memberId: s.memberId, nickname: s.nickname || '',
                 role: s.role || 'member', phone: s.phone || '' };
    }

    // ---------- API ----------
    function apiBase() {
        // 与管理页共用 apiBase 约定: knowledgeDash.apiBase / aiLearningDash.apiBase
        return localStorage.getItem('knowledgeDash.apiBase')
            || localStorage.getItem('aiLearningDash.apiBase')
            || 'http://localhost:8000';
    }

    /** 登录成功后持久化会话 */
    function saveSession(body) {
        write({
            token: body.accessToken,
            refreshToken: body.refreshToken || null,
            memberId: body.memberId,
            nickname: body.nickname || '',
            role: body.role || 'member',
            phone: body.phone || '',
            expiresAt: Date.now() + (body.expiresIn || ACCESS_TTL) * 1000,
        });
    }

    /**
     * 会员登录: POST /api/auth/login { phone, password }
     * 成功返回 { success:true, info }, 失败返回 { success:false, error }
     */
    async function login(params) {
        try {
            var res = await fetch(apiBase() + '/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ phone: params.phone, password: params.password }),
            });
            var body = null;
            try { body = await res.json(); } catch (e) { /* 非 JSON */ }
            if (!res.ok) {
                return { success: false,
                         error: (body && body.detail) || ('HTTP ' + res.status) };
            }
            if (!body || !body.success || !body.accessToken) {
                return { success: false, error: (body && body.detail) || '登录响应异常' };
            }
            saveSession(body);
            return { success: true, info: getInfo() };
        } catch (e) {
            return { success: false, error: '网络错误: ' + e.message };
        }
    }

    /**
     * 管理员登录: 两段式
     *   1) POST /api/admin/login { username, password, totpCode? } 校验管理员凭证
     *   2) 用管理员会员手机号走会员登录换 JWT(统一凭证)
     * adminPhone 缺省用用户名(本地 mock 管理员账号手机号即用户名场景);
     * admin/login 成功但会员登录失败时返回明确错误。
     */
    async function adminLogin(params) {
        // 段1: 校验管理员凭证
        try {
            var res = await fetch(apiBase() + '/api/admin/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username: params.username,
                    password: params.password,
                    totpCode: params.totpCode || null,
                }),
            });
            var body = null;
            try { body = await res.json(); } catch (e) { /* 非 JSON */ }
            if (!res.ok) {
                return { success: false,
                         error: (body && body.detail) || ('HTTP ' + res.status) };
            }
            var data = body && body.data;
            if (!data) {
                return { success: false, error: '管理员登录响应异常' };
            }
            // 2FA 待二次验证: 透传提示(管理页登录暂不支持 2FA 流, 由用户走后端)
            if (data.twoFactorRequired) {
                return { success: false,
                         error: '该管理员已开启双因素认证, 暂请在会员登录入口使用绑定手机号登录' };
            }
        } catch (e) {
            return { success: false, error: '网络错误: ' + e.message };
        }
        // 段2: 用绑定手机号换统一 JWT(默认尝试 用户名 本身作为手机号)
        var r = await login({ phone: params.adminPhone || params.username,
                              password: params.password });
        if (!r.success) {
            return { success: false,
                     error: '管理员凭证正确, 但会员登录失败(' + r.error
                            + '); 请用管理员绑定的会员手机号在会员登录入口登录' };
        }
        return r;
    }

    /** 登出(本地清除; 后端黑名单为 best-effort, 失败不阻断) */
    async function logout() {
        var s = read();
        clear();
        if (s && s.token) {
            try {
                await fetch(apiBase() + '/api/auth/logout', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json',
                               'Authorization': 'Bearer ' + s.token },
                });
            } catch (e) { /* best-effort */ }
        }
    }

    /**
     * 刷新令牌: POST /api/auth/refresh { refreshToken }
     * 成功更新本地会话返回 true
     */
    async function refresh() {
        var s = read();
        if (!s || !s.refreshToken) return false;
        try {
            var res = await fetch(apiBase() + '/api/auth/refresh', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refreshToken: s.refreshToken }),
            });
            var body = null;
            try { body = await res.json(); } catch (e) { /* 非 JSON */ }
            if (res.ok && body && body.success && body.accessToken) {
                saveSession(body);
                return true;
            }
        } catch (e) { /* 网络错误 */ }
        return false;
    }

    // ---------- 管理页接入 ----------
    /**
     * 统一请求头: 已登录返回 { Authorization: 'Bearer ...' }, 未登录返回 null
     * 管理页 fetch 封装统一改为: headers = { ...原头, ...(Auth.apiHeaders() || {}) }
     */
    function apiHeaders() {
        var t = getToken();
        return t ? { 'Authorization': 'Bearer ' + t } : null;
    }

    /** 未登录跳转登录页(带 redirect 回跳参数); 已登录返回 true */
    function requireLogin() {
        if (isLoggedIn()) return true;
        var back = encodeURIComponent(location.pathname.split('/').pop()
                                      + location.search);
        location.href = LOGIN_PAGE + '?redirect=' + back;
        return false;
    }

    /**
     * 挂载登录态 UI 到指定容器(注入: 昵称徽章 + 退出按钮)
     * container: DOM 元素(通常为页头或工具栏右侧)
     */
    function mountBadge(container) {
        if (!container) return;
        var s = read();
        if (!s) return;
        var wrap = document.createElement('span');
        wrap.className = 'auth-badge';
        wrap.style.cssText = 'display:inline-flex;align-items:center;gap:8px;'
            + 'margin-left:auto;font-size:12px;color:#fff;opacity:.92;';
        var name = document.createElement('span');
        name.textContent = (s.nickname || s.phone || '已登录')
            + (s.role === 'admin' ? '(管理员)' : '');
        var btn = document.createElement('button');
        btn.textContent = '退出';
        btn.style.cssText = 'background:rgba(255,255,255,.18);border:1px solid '
            + 'rgba(255,255,255,.45);color:#fff;border-radius:6px;padding:2px 10px;'
            + 'font-size:11px;cursor:pointer;';
        btn.onclick = async function () {
            btn.disabled = true;
            btn.textContent = '退出中…';
            await logout();
            location.href = LOGIN_PAGE;
        };
        wrap.appendChild(name);
        wrap.appendChild(btn);
        container.appendChild(wrap);
    }

    return {
        login: login,
        adminLogin: adminLogin,
        logout: logout,
        refresh: refresh,
        isLoggedIn: isLoggedIn,
        hasRefresh: hasRefresh,
        getToken: getToken,
        getInfo: getInfo,
        apiHeaders: apiHeaders,
        requireLogin: requireLogin,
        mountBadge: mountBadge,
    };
})();

if (typeof window !== 'undefined') {
    window.Auth = Auth;
}
