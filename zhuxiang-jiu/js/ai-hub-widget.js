/**
 * 竹香AI 智能中枢入口组件(35号·AI Hub P0: 多模态输入条 + 角色能力面板)
 *
 * 在 chat-widget 基础上升级(设计文档 5.1.1, 对标 ChatGPT 三键输入条 + 微信按住说话):
 *     [＋] [文本输入框............] [🎤按住说话] [📷图片] [➤发送]
 *
 * 新增能力(对接 backend/routes/hub_routes.py):
 *     GET  /api/hub/panel          角色能力面板 chips(≤6, 点击注入快捷指令)
 *     POST /api/hub/asr            语音转文字(按住说话→松手→文字预览可改再发)
 *     POST /api/hub/input/intent   意图分类(埋点, 展示意图徽章)
 * 兼容: 保留 ChatWidget.mount() 全部协议(旧页面零改动);
 *       HUB 不可用时自动隐藏 🎤/📷, 降级为纯文本(与旧版一致)。
 *
 * 语音交互范式(微信/讯飞双保险):
 *     按住🎤 → MediaRecorder 录音(webm/opus) → 松手上传 /api/hub/asr
 *     → 文字预览条(可编辑) → 点发送才发出(避免识别错误直接发出)
 * 图片: 📷 → input[type=file] → P0 仅提示上传成功并引导文字提问
 *      (GLM-4V 视觉问答链路 P1 开通)。
 *
 * 挂载:
 *     <script src="js/ai-hub-widget.js"><\/script>
 *     <script>AIHubWidget.mount({ apiBase: 'http://localhost:8000', role: 'member' });<\/script>
 * 角色取值: guest / member / cs_staff / admin(影响能力 chips 面板)。
 */
'use strict';

var AIHubWidget = (function () {
    var LS_API = 'chatWidget.apiBase';
    var LS_MEMBER = 'chatWidget.memberId';
    var LS_SESSION = 'chatWidget.sessionId';

    var state = {
        apiBase: null, memberId: null, sessionId: null, role: 'guest',
        open: false, sending: false, transferred: false, rated: false, loaded: false,
        hubOk: false, asrOk: false,
        recording: false, recorder: null, recChunks: [], recStart: 0,
        pendingAsr: null,   // 语音预览态: { text }
    };

    var MODE_LABEL = { direct: '精确命中', synthesized: '知识融合', legacy: 'FAQ' };
    var INTENT_LABEL = {
        'product.price': '问价', 'product.recommend': '推荐', 'order.query': '订单',
        'order.aftersale': '售后', 'knowledge.qa': '知识问答', 'chat.human': '转人工',
        'role.profit': '分润', 'role.dispatch': '派单', 'credit.query': '积分',
        'ops.health': 'AI健康', 'media.image_qa': '图片问答', 'chat.general': '通用',
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
        if (state.role === 'admin') headers['X-Role'] = 'admin';
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
            msgs.forEach(function (m) { appendMessage(m.senderType, m.content, null); });
        } catch (err) {
            appendMessage('system', '历史消息加载失败：' + err.message);
        }
    }

    /* ========= Hub 面板探测(降级开关) ========= */
    async function probeHub() {
        try {
            var p = await apiJson('/api/hub/panel?role=' + encodeURIComponent(state.role));
            state.hubOk = !!p.hubEnabled;
            state.asrOk = !!p.asrEnabled;
            renderChips(p.chips || []);
        } catch (e) {
            state.hubOk = false; state.asrOk = false;
        }
        var mic = document.getElementById('hub-mic');
        var cam = document.getElementById('hub-cam');
        if (mic) mic.style.display = state.asrOk ? '' : 'none';
        if (cam) cam.style.display = state.hubOk ? '' : 'none';
    }

    function renderChips(chips) {
        var bar = document.getElementById('hub-chips');
        if (!bar) return;
        bar.innerHTML = '';
        chips.slice(0, 6).forEach(function (c) {
            var b = document.createElement('button');
            b.className = 'hub-chip';
            b.textContent = c.label;
            b.title = c.quick;
            b.onclick = function () {
                var input = document.getElementById('cw-input');
                if (input) { input.value = c.quick; input.focus(); }
            };
            bar.appendChild(b);
        });
        bar.style.display = chips.length ? 'flex' : 'none';
    }

    /* ========= 意图徽章(发送前埋点) ========= */
    async function trackIntent(text) {
        try {
            await fetch(apiBase() + '/api/hub/input/intent', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Member-Id': memberId() },
                body: JSON.stringify({ text: text }),
            });
        } catch (e) { /* 埋点失败不阻断 */ }
    }

    /* ========= 发送与渲染 ========= */
    async function sendMessage() {
        var input = document.getElementById('cw-input');
        var text = input.value.trim();
        if (!text || state.sending) return;
        input.value = '';
        state.sending = true;
        appendMessage('user', text);
        trackIntent(text);
        var typingId = appendTyping();
        try {
            var sid = await ensureSession();
            var r = await apiJson('/api/chat/sessions/' + encodeURIComponent(sid) + '/messages', {
                method: 'POST',
                body: { senderType: 'user', senderId: parseInt(memberId(), 10), content: text,
                        messageType: state.pendingAsr ? 'voice' : 'text',
                        asrText: state.pendingAsr ? text : undefined },
            });
            removeTyping(typingId);
            state.pendingAsr = null;
            hideAsrPreview();
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
        state.pendingAsr = null;
        localStorage.removeItem(LS_SESSION);
        document.getElementById('cw-log').innerHTML = '';
        hideAsrPreview();
        openPanel();
    }

    /* ========= 按住说话(微信范式: 松手→ASR→预览可改再发) ========= */
    async function micDown(ev) {
        if (!state.asrOk || state.recording) return;
        ev && ev.preventDefault && ev.preventDefault();
        if (!navigator.mediaDevices || !window.MediaRecorder) {
            appendMessage('system', '当前浏览器不支持语音录制，请使用文字输入。');
            return;
        }
        try {
            var stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            state.recorder = new MediaRecorder(stream);
            state.recChunks = [];
            state.recStart = Date.now();
            state.recorder.ondataavailable = function (e) { if (e.data.size) state.recChunks.push(e.data); };
            state.recorder.onstop = onRecordStop;
            state.recorder.start();
            state.recording = true;
            setMicUi(true);
        } catch (e) {
            appendMessage('system', '麦克风不可用：' + (e.message || '请检查浏览器权限'));
        }
    }

    function micUp() {
        if (!state.recording) return;
        try { state.recorder && state.recorder.state !== 'inactive' && state.recorder.stop(); } catch (e) { /* noop */ }
        state.recording = false;
        setMicUi(false);
        if (state.recorder && state.recorder.stream) {
            state.recorder.stream.getTracks().forEach(function (t) { t.stop(); });
        }
    }

    function setMicUi(on) {
        var mic = document.getElementById('hub-mic');
        if (!mic) return;
        mic.textContent = on ? '🔴' : '🎤';
        mic.classList.toggle('rec', on);
        var tip = document.getElementById('hub-rec-tip');
        if (tip) tip.style.display = on ? 'block' : 'none';
    }

    async function onRecordStop() {
        var dur = Date.now() - state.recStart;
        var blob = new Blob(state.recChunks, { type: 'audio/webm' });
        if (dur < 400 || blob.size === 0) return;   // 误触
        if (blob.size > 2 * 1024 * 1024) {
            appendMessage('system', '语音过长（上限 60 秒 / 2MB），请缩短后重试。');
            return;
        }
        appendMessage('system', '🎙️ 语音识别中…（' + Math.round(dur / 1000) + '″）');
        try {
            var b64 = await blobToBase64(blob);
            var res = await fetch(apiBase() + '/api/hub/asr', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Member-Id': memberId() },
                body: JSON.stringify({ audio_b64: b64, fmt: 'webm' }),
            });
            var body = null;
            try { body = await res.json(); } catch (e) { /* 非 JSON */ }
            if (!body || !body.success) {
                appendMessage('system', '语音识别失败：' + ((body && body.error) || '请改用键盘输入'));
                return;
            }
            showAsrPreview(body.text || '');
        } catch (err) {
            appendMessage('system', '语音上传失败：' + err.message);
        }
    }

    function blobToBase64(blob) {
        return new Promise(function (resolve, reject) {
            var fr = new FileReader();
            fr.onload = function () {
                // data:audio/webm;codecs=opus;base64,XXXX → XXXX
                resolve(String(fr.result).split(',')[1] || '');
            };
            fr.onerror = reject;
            fr.readAsDataURL(blob);
        });
    }

    function showAsrPreview(text) {
        state.pendingAsr = { text: text };
        var bar = document.getElementById('hub-asr-preview');
        var input = document.getElementById('cw-input');
        if (!bar || !input) return;
        input.value = text;
        bar.style.display = 'flex';
        input.focus();
    }

    function hideAsrPreview() {
        state.pendingAsr = null;
        var bar = document.getElementById('hub-asr-preview');
        if (bar) bar.style.display = 'none';
    }

    /* ========= 图片输入(P0 提示轨, GLM-4V 问答 P1) ========= */
    function pickImage() {
        var fi = document.getElementById('hub-file');
        if (fi) fi.click();
    }

    function onImagePicked(ev) {
        var f = ev.target.files && ev.target.files[0];
        ev.target.value = '';
        if (!f) return;
        if (!/^image\//.test(f.type)) {
            appendMessage('system', '仅支持图片文件。');
            return;
        }
        appendMessage('system', '📷 已选择图片「' + f.name + '」(' + Math.round(f.size / 1024) + 'KB)。' +
            '图片智能问答即将上线，当前可先用文字描述您的问题～');
    }

    /* ========= UI ========= */
    function buildDom() {
        if (document.getElementById('hub-root')) return;

        var css = document.createElement('style');
        css.textContent = [
            '#hub-root{position:fixed;right:20px;bottom:20px;z-index:999;font-family:inherit}',
            '#cw-fab{width:56px;height:56px;border-radius:50%;border:none;cursor:pointer;background:linear-gradient(135deg,#355c44,#4a7c59);color:#fff;font-size:24px;box-shadow:0 4px 16px rgba(0,0,0,.25);transition:transform .2s}',
            '#cw-fab:hover{transform:scale(1.08)}',
            '#cw-panel{display:none;position:absolute;right:0;bottom:68px;width:360px;max-width:calc(100vw - 40px);height:500px;max-height:calc(100vh - 120px);background:#fff;border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,.22);flex-direction:column;overflow:hidden}',
            '#cw-panel.show{display:flex}',
            '#cw-head{background:linear-gradient(135deg,#355c44,#4a7c59);color:#fff;padding:12px 16px;display:flex;align-items:center;gap:8px}',
            '#cw-head b{font-size:14px;flex:1}',
            '#cw-head button{background:none;border:1px solid rgba(255,255,255,.5);color:#fff;border-radius:6px;padding:3px 10px;font-size:11px;cursor:pointer}',
            '#cw-sub{font-size:11px;opacity:.8;display:block;margin-top:2px}',
            '#hub-chips{display:none;gap:6px;padding:8px 12px 4px;background:#fff;flex-wrap:wrap}',
            '.hub-chip{border:1px solid #cfe0d4;background:#f2f7f3;color:#355c44;border-radius:999px;padding:3px 12px;font-size:11px;cursor:pointer;transition:all .15s}',
            '.hub-chip:hover{background:#355c44;color:#fff}',
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
            '#hub-asr-preview{display:none;gap:8px;align-items:center;padding:6px 12px;background:#fff8e6;border-top:1px solid #f0e4bd;font-size:11px;color:#8a6d1a}',
            '#hub-asr-preview button{border:none;border-radius:6px;padding:2px 8px;font-size:11px;cursor:pointer}',
            '#hub-asr-preview .ap-cancel{background:#eee;color:#666}',
            '#cw-foot{display:flex;padding:10px 12px;background:#fff;border-top:1px solid #eef1ee;gap:6px;align-items:center}',
            '#hub-mic,#hub-cam{border:none;background:#f2f7f3;border-radius:50%;width:34px;height:34px;font-size:16px;cursor:pointer;flex-shrink:0;transition:all .15s;line-height:1}',
            '#hub-mic:hover,#hub-cam:hover{background:#e2ede5}',
            '#hub-mic.rec{background:#fdecea;animation:hub-pulse 1s infinite}',
            '@keyframes hub-pulse{0%,100%{box-shadow:0 0 0 0 rgba(220,53,69,.4)}50%{box-shadow:0 0 0 8px rgba(220,53,69,0)}}',
            '#cw-input{flex:1;border:1px solid #e4e8e4;border-radius:8px;padding:8px 10px;font-size:13px;outline:none}',
            '#cw-input:focus{border-color:#4a7c59}',
            '#cw-send{border:none;border-radius:8px;background:#355c44;color:#fff;padding:0 14px;font-size:13px;cursor:pointer}',
            '#cw-send:disabled{opacity:.5;cursor:not-allowed}',
            '#hub-rec-tip{display:none;font-size:10px;color:#c0392b;padding:2px 14px;background:#fff;text-align:center}',
            '#cw-tip{font-size:10px;color:#98a29a;padding:0 14px 8px;background:#fff;text-align:center}',
        ].join('');
        document.head.appendChild(css);

        var root = document.createElement('div');
        root.id = 'hub-root';
        root.innerHTML =
            '<button id="cw-fab" title="竹香AI 智能助手">🤖</button>' +
            '<div id="cw-panel">' +
              '<div id="cw-head"><b>竹香AI<span id="cw-sub">智能中枢 · 语音/文字/图片多模态入口</span></b>' +
                '<button onclick="AIHubWidget.transferHuman()">转人工</button>' +
                '<button onclick="AIHubWidget.newSession()">新会话</button>' +
                '<button onclick="AIHubWidget.toggle()">收起</button></div>' +
              '<div id="hub-chips"></div>' +
              '<div id="cw-log"></div>' +
              '<div id="cw-rate">本次服务：<button onclick="AIHubWidget.rate(1)">★</button><button onclick="AIHubWidget.rate(2)">★</button><button onclick="AIHubWidget.rate(3)">★</button><button onclick="AIHubWidget.rate(4)">★</button><button onclick="AIHubWidget.rate(5)">★</button></div>' +
              '<div id="hub-asr-preview">🎙️ 语音已转文字(可编辑) <button class="ap-cancel" onclick="AIHubWidget.cancelAsr()">撤销</button></div>' +
              '<div id="hub-rec-tip">🔴 正在录音…松开麦克风按钮结束</div>' +
              '<div id="cw-foot">' +
                '<button id="hub-mic" title="按住说话" style="display:none">🎤</button>' +
                '<button id="hub-cam" title="图片提问" style="display:none" onclick="AIHubWidget.pickImage()">📷</button>' +
                '<input type="text" id="cw-input" placeholder="请输入您的问题…" onkeydown="if(event.key===\'Enter\')AIHubWidget.sendMessage()">' +
                '<button id="cw-send" onclick="AIHubWidget.sendMessage()">发送</button>' +
              '</div>' +
              '<input type="file" id="hub-file" accept="image/*" style="display:none" onchange="AIHubWidget.onImagePicked(event)">' +
              '<div id="cw-tip">⚠️ 过量饮酒有害健康 · 未成年人禁止购买和饮用酒类</div>' +
            '</div>';
        document.body.appendChild(root);

        document.getElementById('cw-fab').addEventListener('click', function () { AIHubWidget.toggle(); });

        /* 按住说话(鼠标+触摸双轨) */
        var mic = document.getElementById('hub-mic');
        mic.addEventListener('pointerdown', micDown);
        mic.addEventListener('pointerup', micUp);
        mic.addEventListener('pointerleave', micUp);
        mic.addEventListener('pointercancel', micUp);
        mic.addEventListener('contextmenu', function (e) { e.preventDefault(); });
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
            probeHub();
            if (!state.loaded) appendMessage('system', '您好，我是竹香AI助手。支持文字、按住🎤说话、📷图片多种方式提问～');
        } else {
            panel.classList.remove('show');
            fab.textContent = '🤖';
            micUp();
        }
    }

    function openPanel() {
        if (!state.open) toggle();
        else loadHistory();
    }

    function cancelAsr() {
        hideAsrPreview();
        var input = document.getElementById('cw-input');
        if (input) input.value = '';
    }

    /* ========= 挂载入口 ========= */
    function mount(opts) {
        opts = opts || {};
        if (opts.apiBase) {
            state.apiBase = String(opts.apiBase).replace(/\/+$/, '');
            localStorage.setItem(LS_API, state.apiBase);
        }
        state.role = opts.role || 'guest';
        var savedSession = localStorage.getItem(LS_SESSION);
        if (savedSession) {
            state.sessionId = savedSession;
            state.loaded = false;
        }
        buildDom();
    }

    return {
        mount: mount,
        toggle: toggle,
        sendMessage: sendMessage,
        transferHuman: transferHuman,
        newSession: newSession,
        rate: rate,
        pickImage: pickImage,
        onImagePicked: onImagePicked,
        cancelAsr: cancelAsr,
        micDown: micDown,
        micUp: micUp,
        _state: state,
    };
})();

/* data-main 属性自动挂载: <script src="js/ai-hub-widget.js" data-api-base="..." data-role="member"><\/script> */
(function () {
    var script = document.currentScript;
    if (script && script.dataset.apiBase) {
        AIHubWidget.mount({ apiBase: script.dataset.apiBase, role: script.dataset.role });
    }
})();
