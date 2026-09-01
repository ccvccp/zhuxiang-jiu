/* 39号·AI智能网站入口管理模块 v1.0 · AI 智能入口页脚本
 * 页面: login.html(升级版)
 * 职责: 设备指纹采集 / AI 预判问候 / 统一登录(风控自适应) /
 *       step_up 轻量二次 / 扫码登录 QR 轮询协议 / 跳转
 * 依赖: js/auth.js(Auth.apiBase 复用同一后端地址)
 */
'use strict';

var Entry = {
    pollTimer: null,
    currentQrId: null,

    /* ---------- 设备指纹(弱特征拼接, 无持久隐私标识) ---------- */
    fingerprint: function () {
        try {
            var parts = [
                'ua=' + navigator.userAgent,
                'lang=' + (navigator.language || ''),
                'plat=' + (navigator.platform || ''),
                'screen=' + screen.width + 'x' + screen.height,
                'depth=' + (screen.colorDepth || ''),
                'tz=' + (Intl.DateTimeFormat().resolvedOptions().timeZone || ''),
            ];
            return parts.join('|');
        } catch (e) {
            return 'ua=' + (navigator.userAgent || 'unknown');
        }
    },

    /* ---------- API 基址(复用 auth.js 配置) ---------- */
    api: function () {
        if (window.Auth && Auth.apiBase) return Auth.apiBase;
        return (localStorage.getItem('zhuxiang.apiBase')
                || 'http://localhost:8000');
    },

    /* ---------- AI 预判(设备识别 + 问候) ---------- */
    recognize: async function () {
        try {
            var fp = encodeURIComponent(this.fingerprint());
            var resp = await fetch(
                this.api() + '/api/entry/recognize?fingerprint=' + fp);
            var body = await resp.json();
            var data = (body || {}).data || {};
            var greet = document.getElementById('greetBanner');
            if (data.greeting) {
                greet.textContent = data.greeting + ' · AI 已为你推荐登录方式';
                greet.style.display = 'block';
            } else {
                var modes = (data.recommendedModes || []).join(' / ');
                greet.textContent = '欢迎使用 AI 智能入口(推荐: ' + modes + ')';
                greet.style.display = 'block';
            }
        } catch (e) { /* 预判失败不阻断登录 */ }
    },

    /* ---------- 统一登录(风控自适应: allow 直发 / step_up 二次) ---------- */
    login: async function (payload) {
        payload.fingerprint = this.fingerprint();
        var resp = await fetch(this.api() + '/api/entry/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        var body = await resp.json().catch(function () { return {}; });
        if (!resp.ok) {
            return { success: false, error: (body || {}).detail || resp.status };
        }
        return body.data || {};
    },

    /* step_up 二次验证(短信) */
    stepUp: async function (memberId, phone, smsCode) {
        var resp = await fetch(this.api() + '/api/entry/step-up/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ memberId: memberId, phone: phone,
                                   smsCode: smsCode,
                                   fingerprint: this.fingerprint() }),
        });
        var body = await resp.json().catch(function () { return {}; });
        if (!resp.ok) {
            return { success: false, error: (body || {}).detail || resp.status };
        }
        return { success: true, data: body.data || {} };
    },

    /* 发送验证码(复用 30号 auth 短信通道) */
    sendSms: async function (phone) {
        var resp = await fetch(this.api() + '/api/sms/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ phone: phone }),
        });
        return resp.ok;
    },

    /* ---------- 扫码登录(QR 轮询协议) ---------- */
    qrCreate: async function () {
        var resp = await fetch(this.api() + '/api/entry/qr/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fingerprint: this.fingerprint() }),
        });
        var body = await resp.json().catch(function () { return {}; });
        return (body || {}).data || null;
    },

    qrStatus: async function (qrId) {
        var resp = await fetch(this.api() + '/api/entry/qr/'
                               + qrId + '/status');
        var body = await resp.json().catch(function () { return {}; });
        return (body || {}).data || null;
    },

    /* 手机端扫码确认(演示: 用当前 localStorage 登录态) */
    qrConfirm: async function (qrId) {
        var memberId = localStorage.getItem('zhuxiang.memberId')
            || localStorage.getItem('zhuxiang.auth.memberId');
        var auth = localStorage.getItem('zhuxiang.auth');
        var token = '';
        try { token = (JSON.parse(auth) || {}).token || ''; } catch (e) {}
        var resp = await fetch(this.api() + '/api/entry/qr/'
                               + qrId + '/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json',
                       'X-Member-Id': memberId || '' },
        });
        var body = await resp.json().catch(function () { return {}; });
        return (body || {}).data || null;
    },

    qrExchange: async function (qrId, ticket) {
        var resp = await fetch(this.api() + '/api/entry/qr/'
                               + qrId + '/exchange', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ loginTicket: ticket }),
        });
        var body = await resp.json().catch(function () { return {}; });
        if (!resp.ok) {
            return { success: false, error: (body || {}).detail || resp.status };
        }
        return { success: true, data: (body || {}).data || {} };
    },

    qrCancel: async function (qrId) {
        try {
            await fetch(this.api() + '/api/entry/qr/' + qrId + '/cancel',
                        { method: 'POST' });
        } catch (e) {}
    },
};

/* ================= 页面逻辑 ================= */

function switchTab(name) {
    document.querySelectorAll('.login-tab').forEach(function (t) {
        t.classList.toggle('active', t.dataset.tab === name);
    });
    document.querySelectorAll('.login-form').forEach(function (f) {
        f.classList.toggle('active', f.id === 'form-' + name);
    });
    hideError();
    if (name !== 'qr') { qrStopPoll(); }
}

function showError(msg) {
    var el = document.getElementById('errorBanner');
    el.textContent = msg;
    el.style.display = 'block';
}
function hideError() {
    document.getElementById('errorBanner').style.display = 'none';
}

/** 登录成功: 会话写入 localStorage(与 auth.js 结构一致)后回跳 */
function entryAfterLogin(tokens, memberId) {
    var session = {
        token: tokens.accessToken,
        refreshToken: tokens.refreshToken,
        memberId: memberId,
        role: tokens.role || 'member',
        expiresAt: Date.now() + (tokens.expiresIn || 7200) * 1000,
    };
    localStorage.setItem('zhuxiang.auth', JSON.stringify(session));
    localStorage.setItem('zhuxiang.memberId', String(memberId));
    var params = new URLSearchParams(location.search);
    var back = params.get('redirect') || '';
    if (back && !back.startsWith('/') && back.indexOf(':') < 0) {
        location.href = back;
        return;
    }
    location.href = 'knowledge-dashboard.html';
}

/* ---------- 密码登录(39号统一端点 + step_up 分支) ---------- */
async function doMemberLogin(e) {
    e.preventDefault();
    hideError();
    var btn = document.getElementById('m-submit');
    btn.disabled = true; btn.textContent = '登录中…';
    var phone = document.getElementById('m-phone').value.trim();
    var password = document.getElementById('m-password').value;
    var r = await Entry.login({ mode: 'password', phone: phone,
                                password: password });
    btn.disabled = false; btn.textContent = '登 录';
    if (r.status === 'authenticated') {
        entryAfterLogin(r.tokens, r.memberId);
        return;
    }
    if (r.status === 'step_up_required') {
        // AI 风控轻量二次(短信): 引导输入验证码
        var code = prompt('AI 风控提示: 该登录需要短信二次核验。\n'
                          + '已向 ' + phone + ' 发送验证码(演示通道见后端日志), 请输入:');
        if (!code) { showError('已取消二次验证'); return; }
        await Entry.sendSms(phone);
        var s = await Entry.stepUp(r.memberId, phone, code);
        if (s.success) {
            entryAfterLogin(s.data.tokens, s.data.memberId);
        } else {
            showError('二次验证失败: ' + (s.error || '验证码错误'));
        }
        return;
    }
    showError('登录失败: ' + (r.error || '未知错误'));
}

/* ---------- 扫码登录 ---------- */
function drawQrPlaceholder(payload) {
    var canvas = document.getElementById('qrCanvas');
    var ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#355c44';
    ctx.fillRect(8, 8, 30, 30);
    ctx.fillRect(canvas.width - 38, 8, 30, 30);
    ctx.fillRect(8, canvas.height - 38, 30, 30);
    ctx.fillStyle = '#666';
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('扫码登录', canvas.width / 2, canvas.height / 2 - 6);
    ctx.fillStyle = '#999';
    ctx.font = '9px monospace';
    ctx.fillText(payload.slice(0, 22), canvas.width / 2,
                 canvas.height / 2 + 12);
}

function qrSetStatus(text) {
    document.getElementById('qrStatus').innerHTML = text;
}

async function qrStart() {
    hideError();
    var qr = await Entry.qrCreate();
    if (!qr || !qr.qrId) {
        showError('二维码生成失败, 请检查后端地址');
        return;
    }
    Entry.currentQrId = qr.qrId;
    drawQrPlaceholder(qr.qrPayload);
    qrSetStatus('二维码已生成(<b>' + qr.expiresIn + 's</b> 有效) — 请用手机端扫码');
    document.getElementById('qrGen').style.display = 'none';
    document.getElementById('qrCancel').style.display = 'block';
    qrStartPoll();
}

function qrStartPoll() {
    qrStopPoll();
    Entry.pollTimer = setInterval(async function () {
        if (!Entry.currentQrId) { qrStopPoll(); return; }
        var st = await Entry.qrStatus(Entry.currentQrId);
        if (!st) return;
        if (st.status === 'pending') return;
        if (st.status === 'scanned') {
            qrSetStatus('<b>已扫码</b> — 请在手机端点击确认');
            return;
        }
        if (st.status === 'confirmed') {
            // 演示口径: 本页代表手机端确认(真实场景由手机端调用)
            var conf = await Entry.qrConfirm(Entry.currentQrId);
            if (conf && conf.loginTicket) {
                var ex = await Entry.qrExchange(Entry.currentQrId,
                                                conf.loginTicket);
                if (ex.success) {
                    qrStopPoll();
                    entryAfterLogin(ex.data.tokens, ex.data.memberId);
                    return;
                }
            }
            qrSetStatus('已确认, 等待票据兑换…');
            return;
        }
        if (st.status === 'expired') {
            qrStopPoll();
            qrSetStatus('二维码已过期, 请重新生成');
            qrResetButtons();
            return;
        }
        if (st.status === 'cancelled') {
            qrStopPoll();
            qrSetStatus('已取消');
            qrResetButtons();
        }
    }, 2000);
}

function qrStopPoll() {
    if (Entry.pollTimer) {
        clearInterval(Entry.pollTimer);
        Entry.pollTimer = null;
    }
}

function qrResetButtons() {
    document.getElementById('qrGen').style.display = 'block';
    document.getElementById('qrCancel').style.display = 'none';
}

async function qrCancel() {
    qrStopPoll();
    if (Entry.currentQrId) {
        await Entry.qrCancel(Entry.currentQrId);
        Entry.currentQrId = null;
    }
    qrSetStatus('已取消 — 点击"生成二维码"重新开始');
    qrResetButtons();
}

/* ---------- 管理员登录(保持既有两段式) ---------- */
async function doAdminLogin(e) {
    e.preventDefault();
    hideError();
    var btn = document.getElementById('a-submit');
    btn.disabled = true; btn.textContent = '登录中…';
    var r = await Auth.adminLogin({
        username: document.getElementById('a-username').value.trim(),
        password: document.getElementById('a-password').value,
        totpCode: document.getElementById('a-totp').value.trim() || null,
        adminPhone: document.getElementById('a-phone').value.trim() || null,
    });
    btn.disabled = false; btn.textContent = '管理员登录';
    if (r.success) { entryAfterLogin(
        { accessToken: r.token || '', refreshToken: '',
          expiresIn: 7200 }, r.memberId || 2); return; }
    showError('管理员登录失败: ' + r.error);
}

/* ---------- 初始化 ---------- */
(function init() {
    // 已登录直接回跳(与旧版口径一致)
    try {
        var auth = JSON.parse(localStorage.getItem('zhuxiang.auth') || 'null');
        if (auth && auth.token && Date.now() < (auth.expiresAt || 0)) {
            var params = new URLSearchParams(location.search);
            var back = params.get('redirect') || '';
            if (back && !back.startsWith('/') && back.indexOf(':') < 0) {
                location.href = back;
            } else {
                location.href = 'knowledge-dashboard.html';
            }
            return;
        }
    } catch (e) {}
    Entry.recognize();  // AI 预判(失败不阻断)
})();
