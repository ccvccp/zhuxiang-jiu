/**
 * 48号·小竹智能语音中枢浮层组件 P0
 * 范式: js/ai-hub-widget.js(35号)按住说话平移——ES5、
 * localStorage 连接、会话轮次流、卡片渲染、页面直达。
 * 依赖后端: /api/xiaozhu/*(48号 xiaozhu_routes)
 * 用法:
 *   XiaozhuWidget.mount({apiBase, memberId,
 *                        jump: function(path){...}})
 */
'use strict';

var XIAOZHU_STATE = {
    apiBase: localStorage.getItem('xiaozhu.apiBase')
             || 'http://localhost:8000',
    memberId: localStorage.getItem('xiaozhu.memberId') || '',
    sessionId: null,
    busy: false,
};

function xzApi(path) { return XIAOZHU_STATE.apiBase + path; }

function xzEsc(s) {
    return String(s == null ? '' : s).replace(
        /[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                     '"': '&quot;', "'": '&#39;' }[c];
        });
}

async function xzFetch(url, options, label) {
    try {
        var resp = await fetch(url, options);
        var text = await resp.text();
        var body = {};
        try { body = JSON.parse(text); } catch (e) {
            body = { raw: text };
        }
        if (!resp.ok) {
            var detail = (body && (body.detail || body.error))
                || resp.status;
            throw new Error(label + ' HTTP ' + resp.status
                + ': ' + detail);
        }
        return body;
    } catch (e) {
        if (e instanceof TypeError) {
            throw new Error(label + ' 无法连接后端(检查地址/跨域)');
        }
        throw e;
    }
}

function xzHeaders() {
    return {
        'X-Member-Id': XIAOZHU_STATE.memberId || '0',
    };
}

/* ---- 卡片渲染(P0 六类: product_list/product_detail/
      guide/nav/promo/help/human) ---- */
function xzRenderCard(card) {
    if (!card || !card.type) { return ''; }
    var h = '<div class="xz-card xz-card-' + xzEsc(card.type)
        + '">';
    if (card.type === 'product_list'
        || card.type === 'product_detail') {
        (card.items || []).forEach(function (p) {
            h += '<div class="xz-card-item"><b>'
                + xzEsc(p.name) + '</b>'
                + (p.price != null
                    ? ' <span class="xz-price">¥'
                      + xzEsc(p.price) + '</span>' : '')
                + (p.subtitle
                    ? '<div class="xz-sub">'
                      + xzEsc(p.subtitle) + '</div>' : '')
                + '</div>';
        });
    } else if (card.type === 'help') {
        (card.items || []).forEach(function (c) {
            h += '<div class="xz-card-item"><b>'
                + xzEsc(c.label) + '</b>'
                + '<div class="xz-sub">试说: '
                + xzEsc((c.examples || [])[0] || '')
                + '</div></div>';
        });
    } else if (card.type === 'nav') {
        h += '<div class="xz-card-item">正在前往 '
            + xzEsc(card.subject) + '</div>';
    } else {
        h += '<div class="xz-card-item">'
            + xzEsc(card.subject || '') + '</div>';
    }
    return h + '</div>';
}

/* ---- 轮次气泡 ---- */
function xzAppendTurn(who, text, card) {
    var flow = document.getElementById('xzFlow');
    var div = document.createElement('div');
    div.className = 'xz-turn xz-' + who;
    var html = '<div class="xz-bubble">' + xzEsc(text)
        + '</div>';
    if (card) { html += xzRenderCard(card); }
    div.innerHTML = html;
    flow.appendChild(div);
    flow.scrollTop = flow.scrollHeight;
}

function xzShowError(msg) {
    xzAppendTurn('sys', '⚠ ' + msg, null);
}

/* ---- 会话 ---- */
async function xzOpenSession() {
    try {
        var b = await xzFetch(xzApi('/api/xiaozhu/sessions'), {
            method: 'POST',
            headers: Object.assign(
                { 'Content-Type': 'application/json' },
                xzHeaders()),
            body: JSON.stringify({ channel: 'voice' }),
        }, '开启会话');
        XIAOZHU_STATE.sessionId = b.sessionId;
        xzAppendTurn('sys',
            '小竹在——请说「小竹 + 指令」(试试: 小竹, 看新品)',
            null);
    } catch (e) {
        xzShowError(e.message);
    }
}

/* ---- 文本发送(键盘兜底/无障碍) ---- */
async function xzSendText() {
    var input = document.getElementById('xzInput');
    var text = input.value.trim();
    if (!text || XIAOZHU_STATE.busy) { return; }
    input.value = '';
    xzAppendTurn('user', text, null);
    XIAOZHU_STATE.busy = true;
    try {
        var b = await xzFetch(
            xzApi('/api/xiaozhu/sessions/'
                  + XIAOZHU_STATE.sessionId + '/text'),
            { method: 'POST',
              headers: Object.assign(
                  { 'Content-Type': 'application/json' },
                  xzHeaders()),
              body: JSON.stringify({ text: text }) },
            '指令');
        xzAppendTurn('bot', b.reply || '', b.card);
        if (b.jump && window.XiaozhuWidget
                && window.XiaozhuWidget.jump) {
            window.XiaozhuWidget.jump(b.jump);
        }
    } catch (e) {
        xzShowError(e.message);
    } finally {
        XIAOZHU_STATE.busy = false;
    }
}

/* ---- 按住说话(35号 MediaRecorder 范式) ---- */
var xzRecorder = null;
var xzChunks = [];

async function xzStartRecord() {
    if (XIAOZHU_STATE.busy) { return; }
    try {
        var stream = await navigator.mediaDevices
            .getUserMedia({ audio: true });
        xzRecorder = new MediaRecorder(stream);
        xzChunks = [];
        xzRecorder.ondataavailable = function (e) {
            if (e.data.size > 0) { xzChunks.push(e.data); }
        };
        xzRecorder.onstop = xzUploadAudio;
        xzRecorder.start();
        document.getElementById('xzMicBtn').textContent
            = '🎙 松开发送';
    } catch (e) {
        xzShowError('麦克风不可用, 请用键盘输入');
    }
}

function xzStopRecord() {
    if (xzRecorder && xzRecorder.state === 'recording') {
        xzRecorder.stop();
        xzRecorder.stream.getTracks()
            .forEach(function (t) { t.stop(); });
        document.getElementById('xzMicBtn').textContent
            = '🎤 按住说话';
    }
}

async function xzUploadAudio() {
    if (!xzChunks.length) { return; }
    var blob = new Blob(xzChunks, { type: 'audio/webm' });
    if (blob.size > 2 * 1024 * 1024) {
        xzShowError('音频过大(上限 2MB/60s)');
        return;
    }
    XIAOZHU_STATE.busy = true;
    xzAppendTurn('user', '🎙 语音…', null);
    try {
        var b64 = await new Promise(function (resolve,
                                              reject) {
            var fr = new FileReader();
            fr.onload = function () {
                resolve(String(fr.result)
                    .split(',')[1] || '');
            };
            fr.onerror = reject;
            fr.readAsDataURL(blob);
        });
        var b = await xzFetch(
            xzApi('/api/xiaozhu/sessions/'
                  + XIAOZHU_STATE.sessionId + '/voice'),
            { method: 'POST',
              headers: Object.assign(
                  { 'Content-Type': 'application/json' },
                  xzHeaders()),
              body: JSON.stringify({
                  audioBase64: b64,
                  filename: 'audio.webm' }) },
            '语音');
        var turns = document.querySelectorAll('.xz-user');
        if (turns.length) {
            turns[turns.length - 1].querySelector('.xz-bubble')
                .textContent = '🎙 ' + (b.commandText || '语音');
        }
        xzAppendTurn('bot', b.reply || '', b.card);
        if (b.jump && window.XiaozhuWidget
                && window.XiaozhuWidget.jump) {
            window.XiaozhuWidget.jump(b.jump);
        }
    } catch (e) {
        xzShowError(e.message);
    } finally {
        XIAOZHU_STATE.busy = false;
    }
}

/* ---- 挂载 ---- */
var XiaozhuWidget = {
    jump: null,

    mount: function (opts) {
        opts = opts || {};
        if (opts.apiBase) {
            XIAOZHU_STATE.apiBase = opts.apiBase;
        }
        if (opts.memberId != null) {
            XIAOZHU_STATE.memberId = String(opts.memberId);
        }
        this.jump = opts.jump || null;

        if (!document.getElementById('xiaozhu-root')) {
            var root = document.createElement('div');
            root.id = 'xiaozhu-root';
            root.innerHTML =
                '<style>'
                + '#xiaozhu-root{position:fixed;right:20px;'
                + 'bottom:20px;width:340px;max-width:90vw;'
                + 'z-index:9999;font-size:13px;'
                + 'font-family:system-ui,sans-serif}'
                + '.xz-panel{background:#fff;border:1px solid '
                + '#d8cbb0;border-radius:12px;box-shadow:0 4px '
                + '20px rgba(90,70,40,.18);overflow:hidden;'
                + 'display:flex;flex-direction:column;'
                + 'height:440px}'
                + '.xz-head{background:linear-gradient(135deg,'
                + '#5b8a72,#3d6b52);color:#fff;padding:10px '
                + '14px;font-weight:600;font-size:14px;'
                + 'display:flex;justify-content:space-between}'
                + '.xz-head small{opacity:.8;font-weight:400}'
                + '.xz-flow{flex:1;overflow-y:auto;padding:10px;'
                + 'background:#faf8f3;'
                + 'display:flex;flex-direction:column;gap:8px}'
                + '.xz-turn{display:flex;'
                + 'flex-direction:column;max-width:88%}'
                + '.xz-user{align-self:flex-end}'
                + '.xz-bot,.xz-sys{align-self:flex-start}'
                + '.xz-bubble{padding:7px 11px;'
                + 'border-radius:10px;line-height:1.5;'
                + 'white-space:pre-wrap;word-break:break-all}'
                + '.xz-user .xz-bubble{background:#5b8a72;'
                + 'color:#fff;border-bottom-right-radius:2px}'
                + '.xz-bot .xz-bubble{background:#fff;'
                + 'border:1px solid #e4dcc8;'
                + 'border-bottom-left-radius:2px}'
                + '.xz-sys .xz-bubble{background:#fff7e0;'
                + 'color:#8a5a00;font-size:12px}'
                + '.xz-card{margin-top:4px;background:#fff;'
                + 'border:1px solid #e4dcc8;border-radius:8px;'
                + 'padding:6px 8px;width:100%}'
                + '.xz-card-item{padding:5px 4px;'
                + 'border-bottom:1px dashed #eee;'
                + 'font-size:12px}'
                + '.xz-card-item:last-child{border-bottom:none}'
                + '.xz-price{color:#c0392b;font-weight:600}'
                + '.xz-sub{color:#999;font-size:11px;'
                + 'margin-top:2px}'
                + '.xz-inputbar{display:flex;gap:6px;padding:8px;'
                + 'background:#f6f2e9;'
                + 'border-top:1px solid #e4dcc8}'
                + '.xz-inputbar input{flex:1;padding:7px 10px;'
                + 'border:1px solid #d8cbb0;'
                + 'border-radius:6px;font-size:13px}'
                + '#xzMicBtn{padding:7px 10px;border:none;'
                + 'border-radius:6px;background:#5b8a72;'
                + 'color:#fff;cursor:pointer;'
                + 'white-space:nowrap;font-size:12px}'
                + '</style>'
                + '<div class="xz-panel">'
                + '<div class="xz-head"><span>🎋 小竹</span>'
                + '<small>唤我直达</small></div>'
                + '<div class="xz-flow" id="xzFlow"></div>'
                + '<div class="xz-inputbar">'
                + '<input id="xzInput" placeholder="或键入指令…">'
                + '<button id="xzMicBtn">🎤 按住说话</button>'
                + '</div></div>';
            document.body.appendChild(root);
            var mic = document.getElementById('xzMicBtn');
            mic.addEventListener('mousedown', xzStartRecord);
            mic.addEventListener('mouseup', xzStopRecord);
            mic.addEventListener('mouseleave', xzStopRecord);
            var input = document.getElementById('xzInput');
            input.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') { xzSendText(); }
            });
        }
        xzOpenSession();
    },
};

window.XiaozhuWidget = XiaozhuWidget;
