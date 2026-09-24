/**
 * 48号·小竹智能语音中枢看板(P0-P4 六区块 + 49号P4 FC 分区)
 * 范式: js/trust-risk-dashboard.js(47号)平移——ES5、localStorage
 * 连接、区块化加载(手动刷新, 不进自动刷新)。
 * 依赖后端: /api/xiaozhu/*(48号 xiaozhu_routes; admin)
 * 区块: ①使用总览 ②指令命中 ③高敏台账 ④积分账本
 *       ⑤共创队列 ⑥治理桥接 ⑦FC 分区(49号P4)
 */
'use strict';

var API_BASE_KEY = 'xiaozhuDash.apiBase';
var state = { apiBase: localStorage.getItem(API_BASE_KEY)
              || 'http://localhost:8000' };
/* P5.2 鉴权: 登录后叠加 Authorization Bearer(strict 模式), 未登录保留 compat 兼容头 */
function adminHeaders() {
    var h = { 'X-Role': 'admin', 'Content-Type': 'application/json' };
    var auth = (typeof Auth !== 'undefined') ? Auth.apiHeaders() : null;
    return auth ? Object.assign(h, auth) : h;
}

function api(path) { return state.apiBase + path; }

function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                 '"': '&quot;', "'": '&#39;' }[c];
    });
}

async function fetchJson(url, options, label) {
    try {
        var resp = await fetch(url, options);
        var text = await resp.text();
        var body = {};
        try { body = JSON.parse(text); } catch (e) { body = { raw: text }; }
        if (!resp.ok) {
            var detail = (body && (body.detail || body.error)) || resp.status;
            throw new Error(label + ' HTTP ' + resp.status + ': ' + detail);
        }
        return body;
    } catch (e) {
        if (e instanceof TypeError) {
            throw new Error(label + ' 无法连接后端(检查地址/跨域)');
        }
        throw e;
    }
}

function showError(msg) {
    var el = document.getElementById('errBar');
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(function () { el.style.display = 'none'; }, 8000);
}

function showInfo(msg) {
    var el = document.getElementById('infoBar');
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(function () { el.style.display = 'none'; }, 5000);
}

function markUpdate() {
    document.getElementById('lastUpdate').textContent =
        '更新于 ' + new Date().toLocaleTimeString();
}

function saveConn() {
    var el = document.getElementById('apiBase');
    state.apiBase = el.value.trim().replace(/\/+$/, '');
    if (!state.apiBase) { state.apiBase = 'http://localhost:8000'; }
    el.value = state.apiBase;
    localStorage.setItem(API_BASE_KEY, state.apiBase);
    loadAll();
}

function cells(target, items) {
    document.getElementById(target).innerHTML =
        items.map(function (c) {
            return '<div class="ov-cell"><div class="k">' + esc(c.k) +
                '</div><div class="v ' + (c.cls || '') + '">' + esc(c.v) +
                '</div></div>';
        }).join('');
}

function pct(v) {
    return (v == null) ? '—' : v + '%';
}

function kindChips(counts) {
    var parts = Object.keys(counts || {}).map(function (k) {
        return '<span class="sig-chip">' + esc(k) + ' ×' +
            esc(counts[k]) + '</span>';
    });
    return parts.join('') || '—';
}

/* ============================================================
 * 六区块加载(单聚合端点)
 * ============================================================ */

async function loadAll() {
    try {
        var b = await fetchJson(api('/api/xiaozhu/dashboard'),
            { headers: adminHeaders() }, '语音中枢看板');
        var zones = b.zones || {};
        markUpdate();
        if ((b.zoneErrors || []).length) {
            showInfo('部分区块降级: ' + (b.zoneErrors || []).join(', '));
        }

        // ① 使用总览
        var u = zones.usage || {};
        if (u.error) {
            cells('ovUsage', [{ k: '使用总览', v: '区块异常',
                cls: 'red' }]);
        } else {
            cells('ovUsage', [
                { k: '会话量', v: u.sessions || 0, cls: 'blue' },
                { k: '语音会话', v: u.voiceSessions || 0 },
                { k: '轮次总量', v: u.turns || 0 },
                { k: '指令命中', v: u.commandTurns || 0,
                  cls: 'green' },
                { k: '直达率', v: pct(u.directRate),
                  cls: u.directRate != null && u.directRate < 50
                      ? 'yellow' : 'green' },
                { k: '语音轮次占比', v: pct(u.voiceShare) },
            ]);
            document.getElementById('usageNote').textContent =
                (u.note || '') + ' · 未唤醒 ' + (u.notWoken || 0) +
                ' · 转写失败 ' + (u.asrFailed || 0);
        }

        // ② 指令命中排行
        var c = zones.commands || {};
        if (c.error) {
            cells('ovCommands', [{ k: '指令命中', v: '区块异常',
                cls: 'red' }]);
        } else {
            cells('ovCommands', [
                { k: '指令种类', v: c.totalActions || 0, cls: 'blue' },
                { k: '兜底轮次', v: c.fallbackTurns || 0,
                  cls: 'yellow' },
                { k: '兜底率', v: pct(c.fallbackRate),
                  cls: c.fallbackRate != null && c.fallbackRate > 30
                      ? 'red' : 'green' },
            ]);
            var rk = c.ranking || [];
            document.getElementById('cmdRanking').innerHTML =
                rk.map(function (e, i) {
                    return '<tr><td>' + (i + 1) + '</td><td>' +
                        '<span class="kind-pill">' + esc(e.action) +
                        '</span></td><td>' + esc(e.hits) + '</td></tr>';
                }).join('') ||
                '<tr><td colspan="3" class="dash-empty">' +
                '暂无指令命中</td></tr>';
        }

        // ③ 高敏操作台账
        var cf = zones.confirm || {};
        if (cf.error) {
            cells('ovConfirm', [{ k: '高敏台账', v: '区块异常',
                cls: 'red' }]);
        } else {
            cells('ovConfirm', [
                { k: '令牌发放', v: cf.issued || 0, cls: 'blue' },
                { k: '核销成功', v: cf.confirmed || 0, cls: 'green' },
                { k: '通过率', v: pct(cf.passRate),
                  cls: cf.passRate != null && cf.passRate < 60
                      ? 'yellow' : 'green' },
                { k: '码错', v: cf.wrongCode || 0, cls: 'red' },
                { k: '过期', v: cf.expired || 0, cls: 'gray' },
                { k: '冷静期拦截', v: cf.cooldown || 0, cls: 'red' },
                { k: '幂等去重', v: cf.duplicate || 0, cls: 'gray' },
            ]);
            document.getElementById('confirmNote').textContent =
                cf.note || '';
        }

        // ④ 积分账本
        var p = zones.points || {};
        if (p.error) {
            cells('ovPoints', [{ k: '积分账本', v: '区块异常',
                cls: 'red' }]);
        } else {
            cells('ovPoints', [
                { k: '累计发放', v: p.awarded || 0, cls: 'green' },
                { k: '累计兑换', v: p.redeemed || 0, cls: 'blue' },
                { k: '余额总量', v: p.balanceTotal || 0 },
                { k: '持分会员', v: p.holders || 0, cls: 'blue' },
                { k: '流水条数', v: p.ledgerCount || 0, cls: 'gray' },
            ]);
            document.getElementById('pointsChips').innerHTML =
                kindChips(p.byKind);
        }

        // ⑤ 共创队列 + 失败聚类
        var co = zones.cocreate || {};
        if (co.error) {
            cells('ovCocreate', [{ k: '共创队列', v: '区块异常',
                cls: 'red' }]);
        } else {
            cells('ovCocreate', [
                { k: '待审共创', v: co.pendingCount || 0,
                  cls: (co.pendingCount || 0) > 0 ? 'yellow' : 'green' },
                { k: '已上架', v: co.approvedCount || 0, cls: 'green' },
                { k: '失败案例', v: co.failuresTotal || 0,
                  cls: (co.failuresTotal || 0) > 0 ? 'yellow'
                      : 'green' },
            ]);
            var pending = co.pending || [];
            document.getElementById('pendingList').innerHTML =
                pending.map(function (r) {
                    return '<tr><td>' + esc(r.cmdId) + '</td><td>' +
                        esc(r.phrase) + '</td><td>' +
                        '<span class="kind-pill">' + esc(r.action) +
                        '</span></td><td>' +
                        '<button class="dash-btn small approve" ' +
                        'onclick="reviewCustom(' + esc(r.cmdId) +
                        ', true)">上架</button> ' +
                        '<button class="dash-btn small reject" ' +
                        'onclick="reviewCustom(' + esc(r.cmdId) +
                        ', false)">驳回</button></td></tr>';
                }).join('') ||
                '<tr><td colspan="4" class="dash-empty">' +
                '暂无待审共创指令</td></tr>';
            var tp = co.topPhrases || [];
            document.getElementById('failureChips').innerHTML =
                tp.map(function (t) {
                    return '<span class="sig-chip">' + esc(t.phrase) +
                        ' ×' + esc(t.count) + '</span>';
                }).join('') || '暂无高频未兜住短语';
        }

        // ⑥ 治理桥接
        var f = zones.fairness || {};
        if (f.error) {
            cells('ovFairness', [{ k: '治理桥接', v: '区块异常',
                cls: 'red' }]);
        } else {
            cells('ovFairness', [
                { k: '采样档案', v: f.scorerId || '—', cls: 'blue' },
                { k: '等级分组', v: (f.groups || []).length },
            ]);
            var gs = f.groups || [];
            document.getElementById('fairnessList').innerHTML =
                gs.map(function (g) {
                    return '<tr><td><span class="kind-pill">' +
                        esc(g.group) + '</span></td><td>' +
                        esc(g.turns) + '</td><td>' + pct(g.directRate) +
                        '</td></tr>';
                }).join('') ||
                '<tr><td colspan="3" class="dash-empty">' +
                '暂无等级分组</td></tr>';
            document.getElementById('fairnessNote').textContent =
                f.note || '';
        }

        // ⑦ FC 分区(49号P4)
        var fc = zones.fc || {};
        if (fc.error) {
            cells('ovFc', [{ k: 'FC 分区', v: '区块异常',
                cls: 'red' }]);
        } else {
            var fr = fc.fallbackRate;
            cells('ovFc', [
                { k: 'FC 调用量', v: fc.calls || 0, cls: 'blue' },
                { k: '失败降级率', v: pct(fr),
                  cls: fr != null && fr > 30 ? 'red' : 'green' },
                { k: '成本合计', v: fc.privacyCostTotal || 0,
                  cls: 'yellow' },
                { k: '预算账户', v: (fc.budget || {}).accounts || 0 },
                { k: '当日累计消耗',
                  v: (fc.budget || {}).usedTodayTotal || 0 },
                { k: 'token 拒绝总数',
                  v: (fc.consentRejects || {}).total || 0,
                  cls: ((fc.consentRejects || {}).total || 0) > 0
                      ? 'yellow' : 'gray' },
            ]);
            document.getElementById('fcNote').textContent =
                fc.note || '';
            var rej = fc.consentRejects || {};
            var rejRows = [
                ['notFound(伪造/作废)', rej.notFound],
                ['expired(超时)', rej.expired],
                ['used(重放)', rej.used],
                ['crossUser(跨用户)', rej.crossUser],
                ['actionMismatch(动作劫持)', rej.actionMismatch],
            ];
            document.getElementById('rejectList').innerHTML =
                rejRows.map(function (r) {
                    return '<tr><td>' + esc(r[0]) + '</td><td>' +
                        esc(r[1] || 0) + '</td></tr>';
                }).join('');
            document.getElementById('fcToolChips').innerHTML =
                kindChips(fc.byKind) + ' · ' + kindChips(fc.byTool);
        }

        // ⑨ 反馈评价(v2 A——👍/👎 落痕聚合)
        var fb = zones.feedback || {};
        if (fb.error) {
            cells('ovFeedback', [{ k: '反馈评价', v: '区块异常',
                cls: 'red' }]);
        } else {
            cells('ovFeedback', [
                { k: '👍 有帮助', v: fb.up || 0, cls: 'green' },
                { k: '👎 没帮助', v: fb.down || 0, cls: 'red' },
                { k: '负反馈占比', v: pct(fb.downShare),
                  cls: fb.downShare != null && fb.downShare > 40
                      ? 'red' : 'green' },
                { k: '反馈总数', v: fb.total || 0, cls: 'blue' },
            ]);
            document.getElementById('feedbackNote').textContent =
                fb.note || '';
            document.getElementById('negList').innerHTML =
                (fb.recentNegative || []).map(function (n) {
                    return '<tr><td>' + esc(n.seq) + '</td><td>' +
                        '<span class="kind-pill">' + esc(n.intent) +
                        '</span></td><td>' + esc(n.rawText) +
                        '</td><td>' + esc(n.reply) + '</td><td>' +
                        esc(n.feedbackAt || '') + '</td></tr>';
                }).join('') ||
                '<tr><td colspan="5" class="dash-empty">' +
                '暂无负反馈</td></tr>';
        }

        // ⑩ ASR 误听自学习(v2 C——词条/命中数观测)
        var af = zones.asrfixes || {};
        if (af.error) {
            renderAsrFixes(null);
        } else {
            renderAsrFixes(af.fixes || []);
        }
    } catch (e) {
        showError(e.message);
    }
    // ⑧ 支付安全观测(三期独立端点——分区 fail-soft 不阻塞主区块)
    loadVoicepay();
    // ⑪ 学习进化队列(P3 独立端点——fail-soft 不阻塞主区块)
    loadLearnQueue();
}

/* ============================================================
 * ⑪ 学习进化队列(P3: 👎→队列→建议→采纳→词条生效)
 * ============================================================ */

async function loadLearnQueue() {
    try {
        var filter = document.getElementById('learnFilter');
        var status = filter ? filter.value : 'pending';
        var j = await fetchJson(api(
            '/api/xiaozhu/dashboard/learn-queue?status=' + status),
            { headers: adminHeaders() });
        var stats = j.stats || {};
        document.getElementById('learnStats').textContent =
            '待处理 ' + (stats.pending || 0) + ' · 已采纳 '
            + (stats.adopted || 0) + ' · 已忽略 '
            + (stats.dismissed || 0);
        document.getElementById('learnLlmState').textContent =
            j.llmSuggestOn ? 'AI建议: 开' : 'AI建议: 关(env XIAOZHU_LEARN_LLM)';
        renderLearnQueue(j.queue || {});
    } catch (e) { /* fail-soft: 区块失败不阻塞 */ }
}

function renderLearnQueue(queue) {
    var body = document.getElementById('learnQueueBody');
    var entries = Object.keys(queue);
    if (!entries.length) {
        body.innerHTML = '<tr><td colspan="6" class="dash-empty">' +
            '暂无学习条目——用户点👎后自动入队</td></tr>';
        return;
    }
    body.innerHTML = entries.slice(0, 50).map(function (k) {
        var q = queue[k];
        var sug = q.suggestion || null;
        var adopted = q.adopted || null;
        var pending = q.status === 'pending';
        var t = (q.feedbackAt || '').replace('T', ' ').slice(5, 16);
        var sugCell = adopted
            ? '<b style="color:#355c44">' + esc(adopted.wrong) + '→'
              + esc(adopted.right) + '</b>(已生效)'
            : (sug
                ? '<b>' + esc(sug.wrong) + '→' + esc(sug.right)
                  + '</b><span style="color:#888">(置信'
                  + Math.round((sug.confidence || 0) * 100) + '%)</span>'
                : '<span style="color:#aaa">—</span>');
        var ops = '';
        if (pending) {
            ops = '<button onclick="suggestLearn(\'' + esc(k)
                  + '\')" style="padding:2px 8px;border:1px solid #355c44;'
                  + 'border-radius:4px;background:#fff;color:#355c44;'
                  + 'font-size:11px;cursor:pointer">AI建议</button> '
                  + '<button onclick="adoptLearn(\'' + esc(k) + '\', '
                  + (sug ? '\'' + esc(sug.wrong) + '\', \''
                         + esc(sug.right) + '\'' : 'null, null')
                  + ')" style="padding:2px 8px;border:1px solid #355c44;'
                  + 'border-radius:4px;background:#355c44;color:#fff;'
                  + 'font-size:11px;cursor:pointer">'
                  + (sug ? '采纳建议' : '采纳') + '</button> '
                  + '<button onclick="dismissLearn(\'' + esc(k)
                  + '\')" style="padding:2px 8px;border:1px solid #999;'
                  + 'border-radius:4px;background:#fff;color:#666;'
                  + 'font-size:11px;cursor:pointer">忽略</button>';
        } else {
            ops = '<span style="color:#aaa">'
                  + (q.status === 'adopted' ? '已采纳' : '已忽略') + '</span>';
        }
        return '<tr><td>' + esc(t) + '</td><td>'
            + esc(q.rawText || '') + '</td><td>'
            + esc(q.intent || '') + '</td><td>'
            + esc((q.reply || '').slice(0, 40)) + '</td><td>'
            + sugCell + '</td><td>' + ops + '</td></tr>';
    }).join('');
}

async function suggestLearn(key) {
    try {
        var j = await fetchJson(
            api('/api/xiaozhu/dashboard/learn-queue/' + key
                + '/suggest'),
            { method: 'POST', headers: adminHeaders() });
        var r = j.result || {};
        if (r.error) { showError(r.error); return; }
        if (!r.suggestion) {
            showError(r.note || 'AI 未给出建议(该轮可能非误听)');
            return;
        }
        showInfo ? showInfo('AI 建议: ' + r.suggestion.wrong + '→'
            + r.suggestion.right) : null;
        loadLearnQueue();
    } catch (e) { showError(e.message); }
}

async function adoptLearn(key, wrong, right) {
    if (!wrong || !right) {
        var w = prompt('误听词(ASR 原文子串):', '');
        if (!w) return;
        var r2 = prompt('修正词(正确说法):', '');
        if (!r2) return;
        wrong = w;
        right = r2;
    }
    try {
        await fetchJson(
            api('/api/xiaozhu/dashboard/learn-queue/' + key
                + '/adopt'),
            { method: 'POST', headers: adminHeaders(),
              body: JSON.stringify({ wrong: wrong, right: right }) });
        loadLearnQueue();
    } catch (e) { showError(e.message); }
}

async function dismissLearn(key) {
    try {
        await fetchJson(
            api('/api/xiaozhu/dashboard/learn-queue/' + key
                + '/dismiss'),
            { method: 'POST', headers: adminHeaders() });
        loadLearnQueue();
    } catch (e) { showError(e.message); }
}

/* ============================================================
 * ⑫ 语音数据周报(P4: 近 7 天聚合+环比)
 * ============================================================ */

async function loadWeekly() {
    var box = document.getElementById('weeklyBody');
    box.innerHTML = '<div class="dash-empty">统计中…</div>';
    try {
        var j = await fetchJson(
            api('/api/xiaozhu/dashboard/voice-weekly'),
            { headers: adminHeaders() });
        var r = j.report;
        if (!r) {
            box.innerHTML = '<div class="dash-empty">暂无数据</div>';
            return;
        }
        document.getElementById('weeklyMeta').textContent =
            '窗口: ' + (r.window.from || '').slice(0, 10) + ' ~ '
            + (r.window.to || '').slice(0, 10);
        var u = r.usage || {}, a = r.asr || {}, f = r.feedback || {},
            lr = r.learning || {}, fx = r.fixes || {},
            mom = r.mom || {};
        var pct = function (v) {
            return v === null || v === undefined
                ? '—' : (v > 0 ? '+' : '') + v + '%';
        };
        var intents = (r.intents || {}).top || [];
        var hits = fx.topHits || [];
        box.innerHTML =
            '<table class="dash-table"><thead><tr>'
            + '<th>指标</th><th>本周</th><th>环比</th>'
            + '<th>指标</th><th>数值</th></tr></thead><tbody>'
            + '<tr><td>语音会话</td><td>' + (u.sessions || 0)
            + '</td><td>' + pct(mom.sessions) + '</td>'
            + '<td>识别失败率</td><td>'
            + ((a.failRate || 0) * 100).toFixed(1) + '%</td></tr>'
            + '<tr><td>语音轮次</td><td>' + (u.voiceTurns || 0)
            + '</td><td>' + pct(mom.voiceTurns) + '</td>'
            + '<td>平均延迟</td><td>' + (a.avgLatencyMs || 0)
            + 'ms(峰 ' + (a.maxLatencyMs || 0) + ')</td></tr>'
            + '<tr><td>参与会员</td><td>' + (u.members || 0)
            + '</td><td>—</td>'
            + '<td>踩率(👎/总反馈)</td><td>'
            + ((f.downRate || 0) * 100).toFixed(1) + '% (👍'
            + (f.up || 0) + '/👎' + (f.down || 0) + ')</td></tr>'
            + '<tr><td>文本轮次</td><td>' + (u.textTurns || 0)
            + '</td><td>—</td>'
            + '<td>学习队列</td><td>待审 ' + (lr.pending || 0)
            + ' · 已采纳 ' + (lr.adopted || 0) + '</td></tr>'
            + '<tr><td>意图 top3</td><td colspan="2">'
            + (intents.slice(0, 3).map(function (i) {
                return i.intent + '×' + i.count;
            }).join('、') || '—')
            + '</td><td>误听词条</td><td>共 ' + (fx.total || 0)
            + ' 条 · 学习命中累计 ' + (fx.learnHits || 0) + '</td></tr>'
            + '<tr><td>热词命中 top3</td><td colspan="4">'
            + (hits.slice(0, 3).map(function (h) {
                return h.wrong + '→' + h.right + '(×' + h.hits + ')';
            }).join('、') || '—') + '</td></tr>'
            + '</tbody></table>';
    } catch (e) {
        box.innerHTML = '<div class="dash-empty">生成失败: '
            + esc(e.message) + '</div>';
    }
}

/* ============================================================
 * ⑩ ASR 误听词条管理(v2 C——增删, 即时生效)
 * ============================================================ */

function renderAsrFixes(fixes) {
    var body = document.getElementById('asrFixBody');
    if (fixes === null) {
        body.innerHTML = '<tr><td colspan="5" class="dash-empty">' +
            '区块异常</td></tr>';
        return;
    }
    body.innerHTML = fixes.map(function (f) {
        var protectedSeed = f.source === 'builtin' || f.source === 'dialect';
        return '<tr><td>' + esc(f.wrong) + '</td><td>' + esc(f.to) +
            '</td><td>' + esc(f.hits || 0) + '</td><td>' +
            esc(f.source || 'manual') + '</td><td>' +
            (protectedSeed ? '—'
                : '<button onclick="delAsrFix(\'' +
                  esc(f.wrong) + '\')" style="padding:2px 8px;' +
                  'border:1px solid #c0392b;border-radius:4px;' +
                  'background:#fff;color:#c0392b;font-size:11px;' +
                  'cursor:pointer">删除</button>') +
            '</td></tr>';
    }).join('') ||
        '<tr><td colspan="5" class="dash-empty">暂无词条</td></tr>';
}

async function addAsrFix() {
    var wrong = document.getElementById('fixWrong').value.trim();
    var right = document.getElementById('fixRight').value.trim();
    if (!wrong || !right) {
        showError('请填写误听词和修正词');
        return;
    }
    try {
        await fetchJson(
            api('/api/xiaozhu/dashboard/asr-fixes'),
            { method: 'POST',
              headers: adminHeaders(),
              body: JSON.stringify({ wrong: wrong, right: right }) },
            '添加误听词条');
        showInfo('词条已添加: ' + wrong + ' → ' + right
                 + '（即时生效）');
        document.getElementById('fixWrong').value = '';
        document.getElementById('fixRight').value = '';
        loadAll();
    } catch (e) {
        showError(e.message);
    }
}

async function delAsrFix(wrong) {
    if (!window.confirm('确认删除词条「' + wrong + '」?')) return;
    try {
        await fetchJson(
            api('/api/xiaozhu/dashboard/asr-fixes?wrong='
                + encodeURIComponent(wrong)),
            { method: 'DELETE', headers: adminHeaders() },
            '删除误听词条');
        showInfo('词条已删除: ' + wrong);
        loadAll();
    } catch (e) {
        showError(e.message);
    }
}

/* ============================================================
 * ⑧ 支付安全观测(三期 L1-L3 shadow 观察期数据源)
 * ============================================================ */

async function loadVoicepay() {
    try {
        var b = await fetchJson(
            api('/api/xiaozhu/voicepay/overview'),
            { headers: adminHeaders() }, '支付安全观测');
        var s = b.stats || {};
        cells('ovVoicepay', [
            { k: '档位', v: b.mode || 'off',
              cls: b.mode === 'assist' ? 'green'
                : (b.mode === 'shadow' ? 'yellow' : 'gray') },
            { k: '支付指令', v: s.attempts || 0 },
            { k: 'L1 拦截', v: s.l1Blocked || 0,
              cls: (s.l1Blocked || 0) > 0 ? 'red' : '' },
            { k: 'L2 拦截', v: s.l2Blocked || 0,
              cls: (s.l2Blocked || 0) > 0 ? 'red' : '' },
            { k: 'L2 边缘复核', v: s.l2Review || 0, cls: 'yellow' },
            { k: 'L3 提级', v: s.l3Escalated || 0,
              cls: (s.l3Escalated || 0) > 0 ? 'red' : '' },
            { k: 'shadow 放行', v: s.shadowOverrides || 0,
              cls: 'blue' },
            { k: '4 位码发出', v: s.confirmIssued || 0 },
            { k: '核销成单', v: s.paid || 0, cls: 'green' },
        ]);
        var r = b.rules || {};
        document.getElementById('vpRules').textContent =
            'L1 规则: 频次 ' + (r.freqMax || '-') + ' 次/'
            + ((r.freqWindowSec || 0) / 60) + '分钟 · 单笔限额 日 ¥'
            + (r.amountLimitDay || '-') + ' / 深夜(' + (r.nightHours || '')
            + '时) ¥' + (r.amountLimitNight || '-')
            + ' · L2 ' + ((b.scorer || {}).id || '') + '(batch '
            + ((b.scorer || {}).batch || '') + ')';
        var log = b.log || [];
        var rows = log.slice().reverse().map(function (x) {
            var note = [];
            if (x.shadowOverride) { note.push('shadow放行'); }
            if ((x.l1Rules || []).length) {
                note.push('L1:' + x.l1Rules.join('+'));
            }
            if (x.l3Verdict) { note.push('L3:' + x.l3Verdict); }
            return '<tr><td>' + esc((x.ts || '').slice(5, 19))
                + '</td><td>' + esc(x.memberId) + '</td><td>'
                + esc(x.decision) + '</td><td>'
                + esc(x.l2Score == null ? '—' : x.l2Score)
                + '</td><td>' + esc(x.l3Verdict || '—')
                + '</td><td>' + esc(note.join(' · ') || '—')
                + '</td></tr>';
        }).join('');
        document.getElementById('vpLog').innerHTML = rows
            || '<tr><td colspan="6" class="dash-empty">'
              + '暂无风控留痕</td></tr>';
    } catch (e) {
        cells('ovVoicepay', [{ k: '支付安全观测',
            v: '区块异常', cls: 'red' }]);
        document.getElementById('vpRules').textContent =
            String(e.message || e);
    }
}

/* ============================================================
 * 干预闭环(共创审核 / 公平性桥接 / 红队复跑)
 * ============================================================ */

async function reviewCustom(cmdId, approve) {
    try {
        await fetchJson(
            api('/api/xiaozhu/commands/custom/' + cmdId + '/review'),
            { method: 'POST', headers: adminHeaders(),
              body: JSON.stringify({ approve: approve,
                                     note: '看板一键' +
                                           (approve ? '上架' : '驳回') }) },
            '共创审核');
        showInfo('共创指令 ' + cmdId +
                 (approve ? ' 已上架(贡献者 +100)' : ' 已驳回'));
        loadAll();
    } catch (e) {
        showError(e.message);
    }
}

async function runFairnessBridge() {
    try {
        var b = await fetchJson(
            api('/api/xiaozhu/dashboard/fairness-bridge'),
            { method: 'POST', headers: adminHeaders() }, '公平性桥接');
        showInfo('桥接完成: 上报 ' + (b.bridged || 0) + ' 组(' +
                 ((b.groups || []).join(', ') || '无有效分组') + ')');
        loadAll();
    } catch (e) {
        showError(e.message);
    }
}

async function runRedteam() {
    try {
        showInfo('红队用例集执行中(四类向量 14 用例跑真网关)…');
        var b = await fetchJson(api('/api/xiaozhu/fc/redteam'),
            { method: 'POST', headers: adminHeaders() }, '红队复跑');
        if ((b.breached || 0) > 0) {
            var names = (b.cases || []).filter(function (c) {
                return !c.blocked;
            }).map(function (c) { return c.caseId; }).join(', ');
            showError('红队发现 ' + b.breached + ' 例突破(' +
                     names + ')——上线阻断, 须修复后重跑');
        } else {
            showInfo('红队复跑完成: ' + (b.blocked || 0) + '/' +
                     (b.total || 0) + ' 全部阻断(breached=0)');
        }
        loadAll();
    } catch (e) {
        showError(e.message);
    }
}

/* ============================================================
 * 初始化
 * ============================================================ */

(function init() {
    document.getElementById('apiBase').value = state.apiBase;
    loadAll();
})();
