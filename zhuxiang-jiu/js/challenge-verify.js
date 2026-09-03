/* 43号·安全挑战页逻辑(配套 challenge.html, P3-2)
 * 流程: 解析 URL 参数(token/redirect) → 调 verify 端点 →
 *       通过后跳回原页面。
 * 三态: mock(应答非空即过) / real(极验v4 SDK, 凭证配置后启用) /
 *       mock_fallback。
 * 真实轨 SDK 说明: 凭证到手后在 <head> 引入极验 gt4.js
 *   (本地托管 /static/gt4.js)并初始化 captchaBox 区块,
 *   回调中拿 captchaToken 后走同一 doVerify 提交口径;
 *   未配置时保持 mock 输入框(与后端三态开关默认一致)。
 */
'use strict';

(function () {
    var params = new URLSearchParams(location.search);
    var token = params.get('token') || '';
    var redirect = params.get('redirect') || '/';

    function msg(text, cls) {
        var el = document.getElementById('msg');
        el.textContent = text;
        el.className = 'msg ' + (cls || '');
    }

    window.doVerify = async function () {
        var btn = document.getElementById('submitBtn');
        var answer = document.getElementById('answerInput').value.trim();
        // real 态: captchaToken 由极验组件回调写入全局变量
        var captchaToken = window.__captchaToken || '';
        if (!answer && !captchaToken) {
            msg('请先完成验证操作', 'err');
            return;
        }
        btn.disabled = true;
        btn.textContent = '验证中…';
        try {
            var resp = await fetch('/api/security/challenge/verify', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    token: token, answer: answer,
                    captchaToken: captchaToken
                })
            });
            var body = await resp.json();
            if (resp.ok && body.success) {
                msg('验证通过, 正在返回…', 'ok');
                setTimeout(function () {
                    location.href = decodeURIComponent(redirect);
                }, 800);
            } else {
                msg((body.detail || '验证失败, 请重试'), 'err');
                btn.disabled = false;
                btn.textContent = '完成验证';
            }
        } catch (e) {
            msg('网络异常: ' + e.message, 'err');
            btn.disabled = false;
            btn.textContent = '完成验证';
        }
    };

    // 回车提交
    document.getElementById('answerInput').addEventListener(
        'keyup', function (e) {
            if (e.key === 'Enter') { window.doVerify(); }
        });
})();
