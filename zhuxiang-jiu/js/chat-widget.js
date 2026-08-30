/**
 * AI 智能客服聊天窗组件(P4.3: chat 前端化, 透出 ragMode/citations/置信度)
 *
 * 零依赖原生 JS 浮动组件, 挂载方式:
 *     <script src="js/chat-widget.js"><\/script>
 *     <script>ChatWidget.mount({ apiBase: 'http://localhost:8000' });<\/script>
 * 或不传参(默认 http://localhost:8000, 可经 localStorage.chatWidget.apiBase 覆盖)。
 *
 * 对接端点(backend/routes/chat_routes.py):
 *     POST /api/chat/sessions                      创建会话(X-Member-Id)
 *     POST /api/chat/sessions/{id}/messages        发消息(响应 data.aiReply 含
 *                                                  content/aiConfidence/ragMode/citations)
 *     GET  /api/chat/sessions/{id}/messages        历史消息
 *     POST /api/chat/sessions/{id}/transfer        转人工(发"转人工"关键词自动触发)
 *     POST /api/chat/sessions/{id}/satisfaction    满意度评价(1-5)
 *
 * 响应包装为 {success, data, count}(非 {code,msg,data}); aiReply 中:
 *     - 知识库命中: ragMode=direct/synthesized + citations[] + knowledgeId
 *     - 旧FAQ兜底:  ragMode=legacy + citations=[](空数组)
 *     - 未命中/转人工: 无 ragMode/citations 字段(前端判空)
 * 置信度字段名是 aiConfidence(不是 confidence)。
 *
 * 会员身份: 无登录体系, 生成随机 memberId 存 localStorage(chatWidget.memberId),
 * compat 模式经 X-Member-Id 头直连; strict 模式需 JWT(见页脚说明)。
 */
'use strict';

var ChatWidget = (function () {
    var LS_API = 'chatWidget.apiBase';
    var LS_MEMBER = 'chatWidget.memberId';
    var LS_SESSION = 'chatWidget.sessionId';

    var state = {
        apiBase: null, memberId: null, sessionId: null,
        open: false, sending: false, transferred: false, rated: false, loaded: false,
    };

    /* RAG 模式徽章文案 */
    var MODE_LABEL = {
        direct: '精确命中',
        synthesized: '知识融合',
        legacy: 'FAQ',
    };

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function memberId() {
        if (!state.memberId) {
            var saved = localStorage.getItem(LS_MEMBER);
            state.memberId = saved || String(Math.floor(100000 + Math.random() * 900000));
            if (!saved) localStorage.setItem(LS_MEMBER, state.memberId);
        }
        return state.memberId;
    }

    function apiBase() {
        if (!state.apiBase) {
            state.apiBase = localStorage.getItem(LS_API) || 'http://localhost:8000';
        }
        return state.apiBase;
    }

    /* ========= API ========= */
    async function apiJson(path, options) {
        options = options || {};
        var headers = { 'Content-Type': 'application/json', 'X-Member-Id': memberId() };
        var init = { method: options.method || 'GET', headers: headers };
        if (options.body) init.body = JSON.stringify(options.body);
        var res = await fetch(apiBase() + path, init);
        var body = null;
        try { body = await res.json(); } catch (e) { /* 非 JSON */ }
        if (!res.ok) {
            var msg = (body && (body.detail || body.msg)) ? (body.detail || body.msg) : ('HTTP ' + res.status);
            throw new Error(msg);
        }
        if (!body || body.success !== true) {
            throw new Error((body && (body.msg || body.message)) || '响应格式异常');
        }
        return body.data;
    }

    async function ensureSession() {
        if (state.sessionId) return state.sessionId;
        var s = await apiJson('/api/chat/sessions', {
            method: 'POST',
            body: { userId: parseInt(memberId(), 10), sessionType: 'presale', ageConfirmed: true },
        });
        state.sessionId = s.sessionId;
        localStorage.setItem(LS_SESSION, s.sessionId);
        return s.sessionId;
    }

    async function loadHistory() {
        if (state.loaded) return;
        state.loaded = true;
        try {
            var sid = await ensureSession();
            var msgs = (await apiJson('/api/chat/sessions/' + encodeURIComponent(sid) + '/messages?limit=50')) || [];
            msgs.forEach(function (m) {
                appendMessage(m.senderType, m.content, null);
            });
        } catch (err) {
            appendMessage('system', '历史消息加载失败：' + err.message);
        }
    }

    /* ========= 发送与渲染 ========= */
    async function sendMessage() {
        var input = document.getElementById('cw-input');
        var text = input.value.trim();
        if (!text || state.sending) return;
        input.value = '';
        state.sending = true;
        appendMessage('user', text);
        var typingId = appendTyping();
        try {
            var sid = await ensureSession();
            var r = await apiJson('/api/chat/sessions/' + encodeURIComponent(sid) + '/messages', {
                method: 'POST',
                body: { senderType: 'user', senderId: parseInt(memberId(), 10), content: text },
            });
            removeTyping(typingId);
            var ai = r.aiReply;
            if (ai) {
                appendMessage('ai', ai.content, ai);
                if (ai.transferred) {
                    state.transferred = true;
                    appendMessage('system', '已为您转接人工客服，请稍候。');
                    showRateBar();
                }
            } else {
                appendMessage('system', '当前会话已转人工或已结束，AI 暂不回复。');
            }
        } catch (err) {
            removeTyping(typingId);
            appendMessage('system', '发送失败：' + err.message);
        } finally {
            state.sending = false;
        }
    }

    function appendMessage(senderType, content, ai) {
        var log = document.getElementById('cw-log');
        if (!log) return null;
        var wrap = document.createElement('div');
        wrap.className = 'cw-msg ' + (senderType === 'user' ? 'cw-me' : 'cw-' + senderType);

        var html = '<div class="cw-bubble">' +
            '<div class="cw-text">' + esc(content) + '</div>';

        /* AI 回复: 透出 ragMode 徽章 + 置信度 + 引用溯源(P3.2) */
        if (senderType === 'ai' && ai) {
            var mode = ai.ragMode;
            if (mode) {
                html += '<div class="cw-meta"><span class="cw-badge m-' + esc(mode) + '">' +
                    esc(MODE_LABEL[mode] || mode) + '</span>' +
                    '<span class="cw-conf">置信度 ' + (ai.aiConfidence != null ? ai.aiConfidence : '--') + '</span></div>';
            } else if (ai.aiConfidence != null) {
                html += '<div class="cw-meta"><span class="cw-conf">置信度 ' + ai.aiConfidence + '</span></div>';
            }
            var cites = ai.citations || [];
            if (cites.length) {
                html += '<div class="cw-cites"><div class="cw-cites-title">引用知识(' + cites.length + ')</div>';
                cites.forEach(function (c, i) {
                    html += '<div class="cw-cite"><span class="cw-no">' + (i + 1) + '</span>' +
                        '<span>' + esc(c.question) + '</span><span class="cw-sim">' + esc(c.similarity) + '</span></div>';
                });
                html += '</div>';
            }
        }
        html += '</div>';
        wrap.innerHTML = html;
        log.appendChild(wrap);
        log.scrollTop = log.scrollHeight;
        return wrap;
    }

    function appendTyping() {
        var log = document.getElementById('cw-log');
        if (!log) return null;
        var wrap = document.createElement('div');
        wrap.className = 'cw-msg cw-ai cw-typing';
        wrap.innerHTML = '<div class="cw-bubble"><div class="cw-text">正在思考…</div></div>';
        log.appendChild(wrap);
        log.scrollTop = log.scrollHeight;
        return wrap;
    }

    function removeTyping(node) { if (node && node.parentNode) node.parentNode.removeChild(node); }

    function showRateBar() {
        var bar = document.getElementById('cw-rate');
        if (bar) bar.style.display = 'flex';
    }

    async function rate(score) {
        if (state.rated) return;
        try {
            await apiJson('/api/chat/sessions/' + encodeURIComponent(state.sessionId) + '/satisfaction', {
                method: 'POST', body: { satisfaction: score },
            });
            state.rated = true;
            appendMessage('system', '感谢您的评价（' + score + ' 星）！');
            document.getElementById('cw-rate').style.display = 'none';
        } catch (err) {
            appendMessage('system', '评价失败：' + err.message);
        }
    }

    async function transferHuman() {
        try {
            var sid = await ensureSession();
            await apiJson('/api/chat/sessions/' + encodeURIComponent(sid) + '/transfer', {
                method: 'POST', body: { reason: '用户主动转人工' },
            });
            state.transferred = true;
            appendMessage('system', '已转接人工客服，客服人员即将接入。');
            showRateBar();
        } catch (err) {
            appendMessage('system', '转人工失败：' + err.message);
        }
    }

    function newSession() {
        if (!confirm('开启新的咨询会话？')) return;
        state.sessionId = null;
        state.transferred = false;
        state.rated = false;
        state.loaded = false;
        localStorage.removeItem(LS_SESSION);
        document.getElementById('cw-log').innerHTML = '';
        openPanel();
    }

    /* ========= UI ========= */
    function buildDom(opts) {
        if (document.getElementById('cw-root')) return;

        var css = document.createElement('style');
        css.textContent = [
            '#cw-root{position:fixed;right:20px;bottom:20px;z-index:999;font-family:inherit}',
            '#cw-fab{width:56px;height:56px;border-radius:50%;border:none;cursor:pointer;background:linear-gradient(135deg,#355c44,#4a7c59);color:#fff;font-size:24px;box-shadow:0 4px 16px rgba(0,0,0,.25);transition:transform .2s}',
            '#cw-fab:hover{transform:scale(1.08)}',
            '#cw-panel{display:none;position:absolute;right:0;bottom:68px;width:360px;max-width:calc(100vw - 40px);height:480px;max-height:calc(100vh - 120px);background:#fff;border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,.22);flex-direction:column;overflow:hidden}',
            '#cw-panel.show{display:flex}',
            '#cw-head{background:linear-gradient(135deg,#355c44,#4a7c59);color:#fff;padding:12px 16px;display:flex;align-items:center;gap:8px}',
            '#cw-head b{font-size:14px;flex:1}',
            '#cw-head button{background:none;border:1px solid rgba(255,255,255,.5);color:#fff;border-radius:6px;padding:3px 10px;font-size:11px;cursor:pointer}',
            '#cw-sub{font-size:11px;opacity:.8;display:block;margin-top:2px}',
            '#cw-log{flex:1;overflow-y:auto;padding:14px;background:#f7f8f7;display:flex;flex-direction:column;gap:10px}',
            '.cw-msg{display:flex;max-width:88%}',
            '.cw-me{align-self:flex-end}',
            '.cw-ai,.cw-system{align-self:flex-start}',
            '.cw-bubble{background:#fff;border:1px solid #e4e8e4;border-radius:10px;padding:9px 12px;font-size:13px;line-height:1.65;word-break:break-word}',
            '.cw-me .cw-bubble{background:#355c44;border-color:#355c44;color:#fff}',
            '.cw-system .cw-bubble{background:#f0f4f0;color:#66736a;font-size:12px}',
            '.cw-text{white-space:pre-wrap}',
            '.cw-meta{display:flex;gap:8px;align-items:center;margin-top:6px;flex-wrap:wrap}',
            '.cw-badge{font-size:10px;border-radius:999px;padding:1px 8px;white-space:nowrap}',
            '.cw-badge.m-direct{background:#e8f4ec;color:#2e8b57;border:1px solid rgba(46,139,87,.3)}',
            '.cw-badge.m-synthesized{background:#eef2fb;color:#3b5998;border:1px solid rgba(59,89,152,.3)}',
            '.cw-badge.m-legacy{background:#f4f4f4;color:#999;border:1px solid #ddd}',
            '.cw-conf{font-size:10px;color:#98a29a}',
            '.cw-cites{margin-top:8px;border-top:1px dashed #e4e8e4;padding-top:6px}',
            '.cw-cites-title{font-size:10px;color:#98a29a;margin-bottom:4px}',
            '.cw-cite{display:flex;gap:6px;align-items:baseline;font-size:11px;color:#66736a;padding:2px 0}',
            '.cw-no{background:#4a7c59;color:#fff;border-radius:50%;min-width:15px;height:15px;text-align:center;line-height:15px;font-size:9px;flex-shrink:0}',
            '.cw-sim{margin-left:auto;color:#98a29a;font-size:10px;flex-shrink:0}',
            '.cw-typing .cw-text{color:#98a29a}',
            '#cw-rate{display:none;gap:6px;padding:8px 14px;background:#fff;border-top:1px solid #eef1ee;align-items:center;font-size:12px;color:#66736a}',
            '#cw-rate button{background:none;border:none;font-size:16px;cursor:pointer;color:#d9ce9f}',
            '#cw-rate button:hover{transform:scale(1.2)}',
            '#cw-foot{display:flex;padding:10px 12px;background:#fff;border-top:1px solid #eef1ee;gap:8px}',
            '#cw-input{flex:1;border:1px solid #e4e8e4;border-radius:8px;padding:8px 10px;font-size:13px;outline:none}',
            '#cw-input:focus{border-color:#4a7c59}',
            '#cw-send{border:none;border-radius:8px;background:#355c44;color:#fff;padding:0 14px;font-size:13px;cursor:pointer}',
            '#cw-send:disabled{opacity:.5;cursor:not-allowed}',
            '#cw-tip{font-size:10px;color:#98a29a;padding:0 14px 8px;background:#fff;text-align:center}',
        ].join('');
        document.head.appendChild(css);

        var root = document.createElement('div');
        root.id = 'cw-root';
        root.innerHTML =
            '<button id="cw-fab" title="AI 智能客服">💬</button>' +
            '<div id="cw-panel">' +
              '<div id="cw-head"><b>竹香酒 AI 客服<span id="cw-sub">知识库实时检索 · 引用可溯源</span></b>' +
                '<button onclick="ChatWidget.transferHuman()">转人工</button>' +
                '<button onclick="ChatWidget.newSession()">新会话</button>' +
                '<button onclick="ChatWidget.toggle()">收起</button></div>' +
              '<div id="cw-log"></div>' +
              '<div id="cw-rate">本次服务：<button onclick="ChatWidget.rate(1)">★</button><button onclick="ChatWidget.rate(2)">★</button><button onclick="ChatWidget.rate(3)">★</button><button onclick="ChatWidget.rate(4)">★</button><button onclick="ChatWidget.rate(5)">★</button></div>' +
              '<div id="cw-foot"><input type="text" id="cw-input" placeholder="请输入您的问题…" onkeydown="if(event.key===\'Enter\')ChatWidget.sendMessage()">' +
                '<button id="cw-send" onclick="ChatWidget.sendMessage()">发送</button></div>' +
              '<div id="cw-tip">⚠️ 过量饮酒有害健康 · 未成年人禁止购买和饮用酒类</div>' +
            '</div>';
        document.body.appendChild(root);

        document.getElementById('cw-fab').addEventListener('click', function () { ChatWidget.toggle(); });
    }

    function toggle() {
        state.open = !state.open;
        var panel = document.getElementById('cw-panel');
        var fab = document.getElementById('cw-fab');
        if (state.open) {
            panel.classList.add('show');
            fab.textContent = '✕';
            document.getElementById('cw-input').focus();
            loadHistory();
            if (!state.loaded) appendMessage('system', '您好，我是竹香酒 AI 客服，可以咨询产品、酿造工艺、订单等问题～');
        } else {
            panel.classList.remove('show');
            fab.textContent = '💬';
        }
    }

    function openPanel() {
        if (!state.open) toggle();
        else loadHistory();
    }

    /* ========= 挂载入口 ========= */
    function mount(opts) {
        opts = opts || {};
        if (opts.apiBase) {
            state.apiBase = String(opts.apiBase).replace(/\/+$/, '');
            localStorage.setItem(LS_API, state.apiBase);
        }
        /* 恢复上次会话 */
        var savedSession = localStorage.getItem(LS_SESSION);
        if (savedSession) {
            state.sessionId = savedSession;
            state.loaded = false;
        }
        buildDom(opts);
    }

    return {
        mount: mount,
        toggle: toggle,
        sendMessage: sendMessage,
        transferHuman: transferHuman,
        newSession: newSession,
        rate: rate,
        _state: state,
    };
})();

/* data-main 属性自动挂载(可选): <script src="js/chat-widget.js" data-api-base="http://localhost:8000"><\/script> */
(function () {
    var script = document.currentScript;
    if (script && script.dataset.apiBase) {
        ChatWidget.mount({ apiBase: script.dataset.apiBase });
    }
})();
