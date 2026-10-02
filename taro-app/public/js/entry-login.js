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
        /* 公开入口零配置铁律(2026-09-29 生产部署): login.html 是公开
         * 用户总入口, 不可依赖运营者 localStorage 预配置(auth.js 亦未导出
         * apiBase)——非本地域名默认同域相对路径, 开箱即用 */
        if (location.hostname !== 'localhost'
                && location.hostname !== '127.0.0.1') return '';
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

    /* ---------- 会员注册(公开; 注册即登录, 后端直发双令牌) ---------- */
    register: async function (payload) {
        var resp = await fetch(this.api() + '/api/entry/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        var body = await resp.json().catch(function () { return {}; });
        if (!resp.ok) {
            /* 全局异常格式 {"success":false,"error":msg}(core/errors) */
            return { success: false,
                     error: (body || {}).error
                            || (body || {}).detail || resp.status };
        }
        return body.data || {};
    },

    /* 角色落地数据(§2.5: 连登激励+角色 chips——登录成功后消费;
     * 携带 accessToken(Bearer——生产 strict 下 landing 个性化
     * streak 数据按已登录口径; 白名单兜底公开); 拉取失败返回
     * null 由调用方 fail-soft 直跳) */
    landing: async function (role, memberId, accessToken) {
        try {
            var headers = accessToken
                ? { 'Authorization': 'Bearer ' + accessToken } : {};
            var resp = await fetch(
                this.api() + '/api/entry/landing?role='
                + encodeURIComponent(role || 'member')
                + '&memberId='
                + encodeURIComponent(memberId || ''),
                { headers: headers });
            var body = await resp.json();
            return (body || {}).data || null;
        } catch (e) { return null; }
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

/** 登录成功: 会话写入 localStorage(与 auth.js 结构一致)后
 * 角色爽人落地(设计 §2.5, 2026-09-29 补): 拉取 landing
 * (连登激励+角色 chips)→欢迎横幅→按角色分流(member 回商城
 * 用户中心, 运营角色保持知识看板口径); landing 失败 fail-soft
 * 直跳不阻断(此前所有角色一律落知识看板=运营工具页硬伤) */
async function entryAfterLogin(tokens, memberId, role) {
    var session = {
        token: tokens.accessToken,
        refreshToken: tokens.refreshToken,
        memberId: memberId,
        role: role || 'member',
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
    role = role || 'member';
    var ld = await Entry.landing(role, memberId,
                                 tokens.accessToken);
    var greet = document.getElementById('greetBanner');
    if (greet && ld) {
        var reward = (ld.streakMilestone
                      && ld.streakMilestone.day > 0)
            ? '(' + ld.streakMilestone.hint + ')'
            : '';
        greet.textContent = (ld.greeting || '登录成功')
            + (reward ? ' ' + reward : '');
        greet.style.display = 'block';
    }
    var target = (role === 'member')
        ? '/#/pages/mine/index'        /* §2.5 会员回商城用户中心 */
        : 'knowledge-dashboard.html';  /* 运营角色保持看板口径 */
    setTimeout(function () { location.href = target; },
               ld ? 1600 : 0);
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
        entryAfterLogin(r.tokens, r.memberId, 'member');
        return;
    }
    if (r.status === 'challenge_required'
        && r.challengeMode === 'face') {
        // §1.3 第三级强核验: 有刷脸凭证必须核身(不给短信捷径);
        // PC 端无生物传感器 UI → 引导手机端完成(降级有兜底铁律)
        showError('当前登录风险等级较高, 该账号已绑定刷脸凭证, '
                  + '需完成刷脸核身后才能登录。请改用手机端'
                  + '(商城「我的-账号安全」内刷脸登录), '
                  + '或前往手机端解绑刷脸凭证后使用短信核验。');
        return;
    }
    if (r.status === 'step_up_required') {
        // AI 风控轻量二次(短信): 先发码 → 再收码 → 核验
        // (既有缺陷修复 2026-09-29 浏览器实测发现: 原实现 prompt
        //  在 sendSms 之前——用户被索要尚未发送的验证码, 必然失败)
        var smsOk = await Entry.sendSms(phone);
        if (!smsOk) {
            showError('验证码发送失败, 请稍后重试或改用扫码登录');
            return;
        }
        var code = prompt('AI 风控提示: 该登录需要短信二次核验。\n'
                          + '验证码已发送至 ' + phone + ', 请输入:');
        if (!code) { showError('已取消二次验证'); return; }
        var s = await Entry.stepUp(r.memberId, phone, code);
        if (s.success) {
            entryAfterLogin(s.data.tokens, s.data.memberId,
                            'member');
        } else {
            showError('二次验证失败: ' + (s.error || '验证码错误'));
        }
        return;
    }
    showError('登录失败: ' + (r.error || '未知错误'));
}

/* ---------- 会员注册(注册即登录, 复用登录后分流) ---------- */
async function doRegister(e) {
    e.preventDefault();
    hideError();
    var btn = document.getElementById('r-submit');
    btn.disabled = true; btn.textContent = '注册中…';
    var payload = {
        phone: document.getElementById('r-phone').value.trim(),
        password: document.getElementById('r-password').value,
        nickname: document.getElementById('r-nickname').value.trim(),
        birthdate: document.getElementById('r-birthdate').value,
        ageConfirmed: document.getElementById('r-adult').checked,
    };
    var r = await Entry.register(payload);
    if (r.success && r.accessToken) {
        btn.textContent = '注册成功 ✓';
        /* 注册即登录: 服务层直发双令牌, 会员角色直接走 §2.5 落地分流 */
        entryAfterLogin(
            { accessToken: r.accessToken, refreshToken: r.refreshToken,
              expiresIn: r.expiresIn }, r.memberId, 'member');
        return;
    }
    btn.disabled = false; btn.textContent = '注 册';
    showError('注册失败: ' + (r.error || '请检查填写内容'));
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
    /* §2.2 P2 预留落地(2026-09-29): WebSocket 推送优先(确认即返
     * ≤0.5s, 较 2s 轮询快 4 倍; 消息体与轮询同构无缝切换);
     * 握手失败/断线回落 2s 轮询(降级有兜底铁律——X5 等内嵌
     * 内核 WS 不可靠) */
    if (window.WebSocket && Entry.currentQrId) {
        try {
            var base = Entry.api().replace(/^http/, 'ws');
            var ws = new WebSocket(base + '/api/entry/qr/'
                                   + Entry.currentQrId + '/ws');
            Entry.qrWs = ws;
            ws.onmessage = function (ev) {
                var st;
                try { st = JSON.parse(ev.data); } catch (e) { return; }
                qrHandleStatus(st);
            };
            ws.onclose = ws.onerror = function () {
                if (Entry.qrWs === ws) {
                    Entry.qrWs = null;
                    qrStartTimer();   /* 回落轮询(降级有兜底) */
                }
            };
            return;
        } catch (e) { /* WS 构造失败(极端环境) → 轮询兜底 */ }
    }
    qrStartTimer();
}

function qrStartTimer() {
    Entry.pollTimer = setInterval(async function () {
        if (!Entry.currentQrId) { qrStopPoll(); return; }
        var st = await Entry.qrStatus(Entry.currentQrId);
        if (!st) return;
        qrHandleStatus(st);
    }, 2000);
}

/* QR 状态机(WS 推送与轮询共用——协议兼容的核心) */
async function qrHandleStatus(st) {
    if (!st || !st.status) return;
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
                entryAfterLogin(ex.data.tokens, ex.data.memberId,
                                'member');
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
}

function qrStopPoll() {
    if (Entry.pollTimer) {
        clearInterval(Entry.pollTimer);
        Entry.pollTimer = null;
    }
    if (Entry.qrWs) {
        try { Entry.qrWs.onclose = Entry.qrWs.onerror = null;
              Entry.qrWs.close(); } catch (e) {}
        Entry.qrWs = null;
    }
}

/* ============================================================
 * WebAuthn 指纹登录(39号 P2 真实轨前端接入, 2026-09-30)
 * 平台认证器本地验证生物特征, 原始生物数据永不上送;
 * 凭证 ID 存本机 localStorage(每设备各自绑定);
 * 浏览器不可用/非安全上下文时整个 Tab 隐藏零影响。
 * ============================================================ */
var WA_CRED_KEY = 'zhuxiang.webauthnCred';

function waB64uToBuf(s) {
    s = String(s).replace(/-/g, '+').replace(/_/g, '/');
    var bin = atob(s + '='.repeat((4 - s.length % 4) % 4));
    var arr = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) { arr[i] = bin.charCodeAt(i); }
    return arr.buffer;
}
function waBufToB64u(buf) {
    var arr = new Uint8Array(buf), s = '';
    for (var i = 0; i < arr.length; i++) { s += String.fromCharCode(arr[i]); }
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function waAvailable() {
    return typeof window.PublicKeyCredential !== 'undefined'
        && (window.isSecureContext === true
            || location.hostname === 'localhost'
            || location.hostname === '127.0.0.1');
}

async function waPost(path, body, token, memberId) {
    var h = { 'Content-Type': 'application/json' };
    if (token) { h['Authorization'] = 'Bearer ' + token; }
    if (memberId) { h['X-Member-Id'] = String(memberId); }
    var r = await fetch(Entry.api() + path, {
        method: 'POST', headers: h,
        body: JSON.stringify(body || {}) });
    var data = null;
    try { data = await r.json(); } catch (e) {}
    if (!r.ok) {
        throw new Error((data
                         && (data.detail || data.error))
                        || ('HTTP ' + r.status));
    }
    return (data && data.data) || data;
}

/** 登录: 本机凭证 → 挑战 → 平台认证器 → 真实验签 → 会话 */
async function doWebauthnLogin(e) {
    if (e) { e.preventDefault(); }
    hideError();
    var credId = localStorage.getItem(WA_CRED_KEY);
    if (!credId) {
        showError('本机尚未绑定指纹——请在下方验证身份完成绑定');
        return;
    }
    var btn = document.getElementById('waLoginBtn');
    btn.disabled = true;
    btn.textContent = '请触摸传感器…';
    try {
        var begin = await waPost(
            '/api/entry/webauthn/login/begin',
            { credentialId: credId });
        var pk = begin.publicKey || {};
        var cred = await navigator.credentials.get({
            publicKey: {
                challenge: waB64uToBuf(pk.challenge),
                rpId: pk.rpId,
                allowCredentials: (pk.allowCredentials || [])
                    .map(function (c) {
                        return { type: c.type,
                                 id: waB64uToBuf(c.id) }; }),
                userVerification: pk.userVerification || 'preferred',
                timeout: pk.timeout || 60000,
            } });
        var res = await waPost(
            '/api/entry/webauthn/login/complete',
            { credentialId: credId,
              response: {
                  id: cred.id, rawId: waBufToB64u(cred.rawId),
                  type: cred.type,
                  response: {
                      clientDataJSON: waBufToB64u(
                          cred.response.clientDataJSON),
                      authenticatorData: waBufToB64u(
                          cred.response.authenticatorData),
                      signature: waBufToB64u(
                          cred.response.signature),
                      userHandle: cred.response.userHandle
                          ? waBufToB64u(cred.response.userHandle)
                          : null,
                  } } });
        if (res.status !== 'authenticated' || !res.tokens) {
            throw new Error((res.decision
                             && res.decision.reason)
                            || '风控要求补充验证');
        }
        await entryAfterLogin(res.tokens, res.memberId, 'member');
    } catch (err) {
        showError('指纹登录失败: ' + (err && err.message
                                      ? err.message : err));
        btn.disabled = false;
        btn.textContent = '🫆 触摸指纹登录';
    }
}

/** 绑定: 密码验证身份 → 注册挑战 → 平台认证器 → 本机留凭证 ID */
async function doWebauthnBind(e) {
    if (e) { e.preventDefault(); }
    hideError();
    var phone = (document.getElementById('wa-phone').value || '')
        .trim();
    var pwd = document.getElementById('wa-password').value || '';
    if (!/^1[3-9]\d{9}$/.test(phone) || !pwd) {
        showError('请填写手机号与密码以验证身份');
        return;
    }
    var btn = document.getElementById('waBindBtn');
    btn.disabled = true;
    btn.textContent = '验证中…';
    try {
        /* ① 密码登录拿真实身份(风控自适应同主登录轨) */
        var r = await Entry.login({
            mode: 'password', phone: phone, password: pwd });
        if (r.status !== 'authenticated' || !r.tokens
            || !r.memberId) {
            throw new Error('密码验证未通过'
                            + (r.error ? ': ' + r.error : ''));
        }
        var token = r.tokens.accessToken;
        btn.textContent = '请触摸传感器绑定…';
        /* ② 注册挑战 + 平台认证器创建凭证 */
        var begin = await waPost(
            '/api/entry/webauthn/register/begin', {},
            token, r.memberId);
        var pk = begin.publicKey || {};
        var cred = await navigator.credentials.create({
            publicKey: {
                challenge: waB64uToBuf(pk.challenge),
                rp: pk.rp || {},
                user: pk.user || {},
                pubKeyCredParams: pk.pubKeyCredParams || [],
                authenticatorSelection: pk.authenticatorSelection
                    || { authenticatorAttachment: 'platform',
                         userVerification: 'preferred' },
                timeout: pk.timeout || 60000,
                attestation: pk.attestation || 'none',
            } });
        /* ③ 注册完成(凭证落服务端 bio 表) + 本机留 ID */
        var rec = await waPost(
            '/api/entry/webauthn/register/complete',
            { response: {
                  id: cred.id, rawId: waBufToB64u(cred.rawId),
                  type: cred.type,
                  response: {
                      clientDataJSON: waBufToB64u(
                          cred.response.clientDataJSON),
                      attestationObject: waBufToB64u(
                          cred.response.attestationObject),
                  } } },
            token, r.memberId);
        localStorage.setItem(WA_CRED_KEY, rec.credentialId);
        document.getElementById('waBindArea').style.display = 'none';
        document.getElementById('waStatus').innerHTML =
            '✅ 本机指纹绑定成功， 下次可直接触摸登录';
        btn.disabled = false;
        btn.textContent = '验证并绑定本机指纹';
        /* 绑定即已完成身份验证——顺延进入会话(与密码登录一致) */
        await entryAfterLogin(r.tokens, r.memberId, 'member');
    } catch (err) {
        showError('绑定失败: ' + (err && err.message
                                   ? err.message : err));
        btn.disabled = false;
        btn.textContent = '验证并绑定本机指纹';
    }
}

/* 可用性引导: 指纹 Tab 仅在浏览器支持 + 安全上下文时显示;
 * ENTRY_WEBAUTHN_MODE 未开启时端点 409, 提示语义自然呈现 */
(function waBootstrap() {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', waBootstrap);
        return;
    }
    if (!waAvailable()) { return; }
    var tab = document.getElementById('waTab');
    if (!tab) { return; }
    tab.style.display = '';
    if (localStorage.getItem(WA_CRED_KEY)) {
        var area = document.getElementById('waBindArea');
        if (area) { area.style.display = 'none'; }
    }
})();

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
          expiresIn: 7200 }, r.memberId || 2, 'admin'); return; }
    showError('管理员登录失败: ' + r.error);
}

/* ---------- 初始化 ---------- */
(function init() {
    // 已登录直接回跳(§2.5 角色分流: member 回商城用户中心,
    // 运营角色回知识看板——旧版一律送知识看板, member 落 404 硬伤)
    try {
        var auth = JSON.parse(localStorage.getItem('zhuxiang.auth') || 'null');
        if (auth && auth.token && Date.now() < (auth.expiresAt || 0)) {
            var params = new URLSearchParams(location.search);
            var back = params.get('redirect') || '';
            if (back && !back.startsWith('/') && back.indexOf(':') < 0) {
                location.href = back;
            } else {
                location.href = (auth.role === 'member')
                    ? '/#/pages/mine/index'
                    : 'knowledge-dashboard.html';
            }
            return;
        }
    } catch (e) {}
    Entry.recognize();  // AI 预判(失败不阻断)
})();
