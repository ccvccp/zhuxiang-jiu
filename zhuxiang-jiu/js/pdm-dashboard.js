/* 38号·AI智能产品管理模块 v1.0 · 产品管理看板脚本
 * 页面: ai-pdm-dashboard.html
 * 鉴权: X-Member-Id + X-Role 头(页面身份输入框)
 * localStorage 键: pdmDash.apiBase / pdmDash.memberId / pdmDash.role
 */
'use strict';

var API_BASE_KEY = 'pdmDash.apiBase';
var state = {
    apiBase: localStorage.getItem(API_BASE_KEY) || 'http://localhost:8000',
    memberId: localStorage.getItem('pdmDash.memberId') || '2',
    role: localStorage.getItem('pdmDash.role') || 'admin',
    products: [],
    timer: null,
};

/* ========= 基础工具 ========= */

function $(id) { return document.getElementById(id); }

function headers() {
    return {
        'X-Member-Id': state.memberId,
        'X-Role': state.role,
    };
}

async function fetchJson(url, options, label) {
    var opts = Object.assign({ headers: headers() }, options || {});
    if (opts.body && typeof opts.body === 'object') {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(opts.body);
    }
    var resp = await fetch(url, opts);
    var body = null;
    try { body = await resp.json(); } catch (e) { body = null; }
    if (!resp.ok) {
        var msg = (body && (body.detail || body.error)) || resp.status;
        throw new Error(label + ': ' + msg);
    }
    return body;
}

function showError(msg) {
    var el = $('errorBanner');
    el.textContent = '数据加载失败：' + msg;
    el.style.display = 'block';
    setTimeout(function () { el.style.display = 'none'; }, 8000);
}

function showInfo(msg) {
    var el = $('infoBanner');
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(function () { el.style.display = 'none'; }, 5000);
}

function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
}

var STATUS_BADGE = {
    draft: ['草稿', 'weak'], ai_reviewing: ['AI预审中', 'purple'],
    manual_reviewing: ['待人工审', 'blue'], rejected: ['已驳回', 'red'],
    on_sale: ['在售', 'green'], off_sale: ['已下架', 'orange'],
};

function statusBadge(st) {
    var m = STATUS_BADGE[st] || [st, 'weak'];
    return '<span class="badge ' + m[1] + '">' + m[0] + '</span>';
}

/* ========= 工具栏 ========= */

function saveApiBase() {
    state.apiBase = $('apiBase').value.trim().replace(/\/+$/, '');
    localStorage.setItem(API_BASE_KEY, state.apiBase);
    showInfo('后端地址已保存: ' + state.apiBase);
    refreshData();
}

function saveIdentity() {
    state.memberId = $('memberId').value.trim() || '2';
    state.role = $('roleSel').value;
    localStorage.setItem('pdmDash.memberId', state.memberId);
    localStorage.setItem('pdmDash.role', state.role);
}

function toggleAutoRefresh() {
    if ($('autoRefresh').checked) { startTimer(); }
    else { clearInterval(state.timer); state.timer = null; }
}

function startTimer() {
    if (state.timer) clearInterval(state.timer);
    state.timer = setInterval(refreshData, 30000);
}

/* ========= ① 全景统计 ========= */

async function loadOverview() {
    var d = await fetchJson(state.apiBase + '/api/pdm/report/overview', null, '全景统计');
    var o = d.data || {};
    var sc = o.statusCounts || {};
    var ai = o.aiStats || {};
    var im = o.images || {};
    var rm = (im.reviewModes || {});
    $('ovSummary').textContent = '今日上下架 ' +
        ((o.today || {}).listed || 0) + '/' + ((o.today || {}).delisted || 0);
    var html = '';
    html += '<div class="ov-group"><div class="ov-gtitle">商品状态</div><div class="ov-cells">' +
        ['draft', 'ai_reviewing', 'manual_reviewing', 'rejected', 'on_sale', 'off_sale']
            .map(function (k) {
                var m = STATUS_BADGE[k] || [k];
                return '<div class="ov-cell"><b>' + (sc[k] || 0) + '</b><span>' + m[0] + '</span></div>';
            }).join('') + '</div></div>';
    html += '<div class="ov-group"><div class="ov-gtitle">AI 预审 / 图片</div><div class="ov-cells">' +
        '<div class="ov-cell"><b>' + (ai.fastTrack || 0) + '</b><span>快车道</span></div>' +
        '<div class="ov-cell"><b>' + (ai.manualReview || 0) + '</b><span>人工审</span></div>' +
        '<div class="ov-cell"><b>' + (ai.passRate || 0) + '%</b><span>通过率</span></div>' +
        '<div class="ov-cell"><b>' + (im.total || 0) + '</b><span>图库</span></div>' +
        '<div class="ov-cell"><b>' + (im.flagged || 0) + '</b><span>被标记</span></div>' +
        '<div class="ov-cell"><b>' + (rm.vision || 0) + '</b><span>vision审图</span></div>' +
        '</div></div>';
    $('ovWrap').innerHTML = html;
}

/* ========= ② 商品管理 ========= */

async function loadProducts() {
    saveIdentity();
    var status = $('statusFilter').value;
    var url = state.apiBase + '/api/pdm/products?limit=100' +
        (status ? '&status=' + status : '');
    var d = await fetchJson(url, null, '商品列表');
    state.products = d.data || [];
    $('productCount').textContent = '共 ' + (d.count || 0) + ' 件';
    var rows = state.products.map(function (p) {
        var actions = [];
        if (p.pdmStatus === 'draft' || p.pdmStatus === 'rejected') {
            actions.push('<button class="btn-mini" onclick="submitProduct(\'' + p.product_id + '\')">提交审核</button>');
        }
        if (p.pdmStatus === 'on_sale') {
            actions.push('<button class="btn-mini danger" onclick="delistProduct(\'' + p.product_id + '\')">下架</button>');
        }
        if (p.pdmStatus === 'off_sale') {
            actions.push('<button class="btn-mini" onclick="listProduct(\'' + p.product_id + '\')">上架</button>');
        }
        if (p.pdmStatus === 'manual_reviewing') {
            actions.push('<button class="btn-mini" onclick="reviewProduct(\'' + p.product_id + '\', true)">通过</button>');
            actions.push('<button class="btn-mini danger" onclick="reviewProduct(\'' + p.product_id + '\', false)">驳回</button>');
        }
        if (p.pdmStatus !== 'off_sale' && state.role === 'admin') {
            actions.push('<button class="btn-mini danger" onclick="forceDelist(\'' + p.product_id + '\')">紧急下架</button>');
        }
        return '<tr>' +
            '<td class="cap-id">' + esc(p.product_id) + '</td>' +
            '<td>' + esc(p.name) + '</td>' +
            '<td>' + esc(p.series) + '</td>' +
            '<td class="num">¥' + (p.price || 0) + '</td>' +
            '<td class="num">' + (p.stock || 0) + '</td>' +
            '<td>' + statusBadge(p.pdmStatus) + '</td>' +
            '<td class="num">v' + (p.currentVersion || 1) + '</td>' +
            '<td>' + actions.join(' ') + '</td></tr>';
    }).join('');
    $('productBody').innerHTML = rows ||
        '<tr><td colspan="8" class="dash-empty">暂无商品</td></tr>';
}

async function createProduct() {
    saveIdentity();
    var payload = {
        name: $('newName').value.trim(),
        price: parseFloat($('newPrice').value),
        alcohol: parseInt($('newAlcohol').value, 10) || 42,
        stock: parseInt($('newStock').value, 10) || 0,
        description: $('newDesc').value.trim(),
    };
    if (!payload.name || !payload.price) { showInfo('请填写商品名与售价'); return; }
    try {
        await fetchJson(state.apiBase + '/api/pdm/products',
            { method: 'POST', body: payload }, '创建草稿');
        showInfo('草稿已创建: ' + payload.name);
        $('newName').value = ''; $('newPrice').value = ''; $('newDesc').value = '';
        loadProducts();
    } catch (e) { showError(e.message); }
}

async function submitProduct(pid) {
    try {
        await fetchJson(state.apiBase + '/api/pdm/products/' + pid + '/submit',
            { method: 'POST' }, '提交审核');
        showInfo('已提交 AI 预审: ' + pid);
        loadProducts(); loadPending(); loadOverview();
    } catch (e) { showError(e.message); }
}

async function reviewProduct(pid, approved) {
    var note = approved ? '' : (prompt('驳回理由:', '不合规') || '不合规');
    try {
        await fetchJson(state.apiBase + '/api/pdm/products/' + pid + '/review',
            { method: 'POST', body: { approved: approved, note: note } }, '终审');
        showInfo('终审完成: ' + pid);
        loadProducts(); loadPending(); loadOverview();
    } catch (e) { showError(e.message); }
}

async function listProduct(pid) {
    try {
        await fetchJson(state.apiBase + '/api/pdm/products/' + pid + '/list',
            { method: 'POST' }, '上架');
        showInfo('已上架: ' + pid);
        loadProducts(); loadOverview();
    } catch (e) { showError(e.message); }
}

async function delistProduct(pid) {
    var reason = prompt('下架原因(必填):', '例行下架');
    if (!reason) return;
    try {
        await fetchJson(state.apiBase + '/api/pdm/products/' + pid + '/delist',
            { method: 'POST', body: { reason: reason } }, '下架');
        showInfo('已下架: ' + pid);
        loadProducts(); loadOverview();
    } catch (e) { showError(e.message); }
}

async function forceDelist(pid) {
    var reason = prompt('紧急下架原因(必填):', '负面舆情');
    if (!reason) return;
    try {
        await fetchJson(state.apiBase + '/api/pdm/products/' + pid + '/force-delist',
            { method: 'POST', body: { reason: reason } }, '紧急下架');
        showInfo('已紧急下架: ' + pid);
        loadProducts(); loadOverview();
    } catch (e) { showError(e.message); }
}

/* ========= ③ 待审队列 ========= */

async function loadPending() {
    var d = await fetchJson(state.apiBase + '/api/pdm/reviews/pending?limit=50',
        null, '待审队列');
    var list = d.data || [];
    $('pendingCount').textContent = '待审 ' + (d.count || 0) + ' 件';
    var rows = list.map(function (p) {
        var ai = p.aiReview || {};
        var factors = (ai.factors || []).map(function (f) {
            return f.name + '=' + f.score + '(w' + f.weight + ')';
        }).join(' / ');
        var aiBadge = ai.action === 'fast_track' ?
            '<span class="ai-badge pass">快车道</span>' :
            (ai.action === 'manual_review' ?
                '<span class="ai-badge mid">人工审</span>' :
                '<span class="ai-badge fail">拒</span>');
        return '<tr>' +
            '<td class="cap-id">' + esc(p.product_id) + '</td>' +
            '<td>' + esc(p.name) + '</td>' +
            '<td class="num"><b>' + (ai.score || '-') + '</b></td>' +
            '<td>' + aiBadge + '</td>' +
            '<td style="white-space:normal;max-width:420px;font-size:12px;">' + esc(factors) + '</td>' +
            '<td><button class="btn-mini" onclick="reviewProduct(\'' + p.product_id + '\', true)">通过</button> ' +
            '<button class="btn-mini danger" onclick="reviewProduct(\'' + p.product_id + '\', false)">驳回</button></td></tr>';
    }).join('');
    $('pendingBody').innerHTML = rows ||
        '<tr><td colspan="6" class="dash-empty">暂无待审商品</td></tr>';
}

/* ========= ④ 版本与回滚 ========= */

async function loadVersions() {
    saveIdentity();
    var pid = $('verProductId').value.trim();
    if (!pid) { showInfo('请输入商品ID'); return; }
    var d = await fetchJson(state.apiBase + '/api/pdm/products/' + pid + '/versions',
        null, '版本列表');
    var list = d.data || [];
    $('verCount').textContent = '共 ' + (d.count || 0) + ' 版';
    var rows = list.map(function (v) {
        return '<tr>' +
            '<td class="num">v' + v.version + '</td>' +
            '<td>' + (v.changeType === 'substantive' ?
                '<span class="badge red">实质变更</span>' :
                '<span class="badge weak">微调</span>') + '</td>' +
            '<td class="num">' + (v.operator || '-') + '</td>' +
            '<td>' + esc(v.note || '') + '</td>' +
            '<td>' + esc(String(v.createdAt || '').slice(0, 19)) + '</td>' +
            '<td><button class="btn-mini" onclick="rollbackVersion(\'' + pid + '\',' + v.version + ')">回滚</button></td></tr>';
    }).join('');
    $('versionBody').innerHTML = rows ||
        '<tr><td colspan="6" class="dash-empty">无版本记录</td></tr>';
}

async function rollbackVersion(pid, ver) {
    if (!confirm('回滚到 v' + ver + '?(在售商品将回落 draft 重审)')) return;
    try {
        await fetchJson(state.apiBase + '/api/pdm/products/' + pid + '/versions/rollback',
            { method: 'POST', body: { version: ver } }, '版本回滚');
        showInfo('已回滚到 v' + ver);
        loadVersions(); loadProducts();
    } catch (e) { showError(e.message); }
}

/* ========= ⑤ 图片中心 ========= */

async function loadImages() {
    saveIdentity();
    var status = $('imageFilter').value;
    var url = state.apiBase + '/api/pdm/images?limit=100' +
        (status ? '&status=' + status : '');
    var d = await fetchJson(url, null, '图库');
    var list = d.data || [];
    $('imageCount').textContent = '共 ' + (d.count || 0) + ' 张';
    var rows = list.map(function (img) {
        var review = img.aiReview || {};
        var violations = (review.violationNames || []).join('/') || '无违规';
        var st = img.status === 'usable' ? '<span class="badge green">可用</span>' :
            (img.status === 'flagged' ? '<span class="badge red">被标记</span>' :
                '<span class="badge weak">已销毁</span>');
        var actions = [];
        if (img.status === 'flagged') {
            actions.push('<button class="btn-mini" onclick="reuploadImage(' + img.imageId + ')">重传</button>');
            actions.push('<button class="btn-mini danger" onclick="destroyImage(' + img.imageId + ')">销毁</button>');
        }
        return '<tr>' +
            '<td class="num">' + img.imageId + '</td>' +
            '<td><img class="thumb" src="' + esc(img.url) + '" onerror="this.style.opacity=0.2"></td>' +
            '<td class="cap-id" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;">' + esc(img.url) + (img.generated ? ' <span class="badge purple">AI生成</span>' : '') + '</td>' +
            '<td class="num">' + (img.size ? Math.round(img.size / 1024) + 'KB' : '-') + '</td>' +
            '<td style="white-space:normal;max-width:240px;font-size:12px;">[' + (review.mode || '-') + '] ' + esc(violations) + ' 质量分' + (review.quality != null ? review.quality : '-') + '</td>' +
            '<td>' + st + '</td>' +
            '<td>' + actions.join(' ') + '</td></tr>';
    }).join('');
    $('imageBody').innerHTML = rows ||
        '<tr><td colspan="7" class="dash-empty">图库为空</td></tr>';
}

async function uploadImage() {
    saveIdentity();
    var file = $('imageFile').files[0];
    if (!file) { showInfo('请选择图片文件'); return; }
    var reader = new FileReader();
    reader.onload = async function () {
        var b64 = String(reader.result).split(',')[1] || '';
        var ext = '.' + (file.name.split('.').pop() || 'png').toLowerCase();
        try {
            var d = await fetchJson(state.apiBase + '/api/pdm/images',
                { method: 'POST', body: { dataBase64: b64, ext: ext, productName: $('imageProductName').value.trim() } }, '上传图片');
            var review = (d.data || {}).aiReview || {};
            showInfo('上传成功(审图: ' + (review.mode || '-') + ' 质量分 ' +
                (review.quality != null ? review.quality : '-') + ')');
            loadImages(); loadOverview();
        } catch (e) { showError(e.message); }
    };
    reader.readAsDataURL(file);
}

async function reuploadImage(imageId) {
    var file = $('imageFile').files[0];
    if (!file) { showInfo('请先在选择框中放入新图片, 再点重传'); return; }
    var reader = new FileReader();
    reader.onload = async function () {
        var b64 = String(reader.result).split(',')[1] || '';
        var ext = '.' + (file.name.split('.').pop() || 'png').toLowerCase();
        try {
            await fetchJson(state.apiBase + '/api/pdm/images/' + imageId + '/reupload',
                { method: 'POST', body: { dataBase64: b64, ext: ext } }, '重传');
            showInfo('重传完成, 已重新审图');
            loadImages();
        } catch (e) { showError(e.message); }
    };
    reader.readAsDataURL(file);
}

async function destroyImage(imageId) {
    if (!confirm('销毁图片 #' + imageId + '?(不可恢复)')) return;
    try {
        await fetchJson(state.apiBase + '/api/pdm/images/' + imageId + '/destroy',
            { method: 'POST' }, '销毁');
        showInfo('已销毁');
        loadImages(); loadOverview();
    } catch (e) { showError(e.message); }
}

/* ========= ⑥ AI 设计工坊 ========= */

function showDesign(text) {
    $('designResult').style.display = 'block';
    $('designBody').textContent = text;
}

async function generateMainImage() {
    saveIdentity();
    var pid = $('designProductId').value.trim();
    if (!pid) { showInfo('请输入商品ID'); return; }
    try {
        var d = await fetchJson(
            state.apiBase + '/api/pdm/products/' + pid + '/design/generate-main-image',
            { method: 'POST' }, 'AI 生成主图');
        var img = d.data.image || {};
        var design = d.data.design || {};
        var review = d.data.review || {};
        showDesign('生成轨: ' + design.track +
            '\nPrompt: ' + design.prompt +
            '\n图 URL: ' + img.url +
            '\n审图: [' + (review.mode || '-') + '] 违规' +
            JSON.stringify(review.violationNames || []) +
            ' 质量分 ' + (review.quality != null ? review.quality : '-') +
            '\n图片已入图库(#' + img.imageId + ', 状态 ' + img.status + ')');
        loadImages(); loadOverview();
    } catch (e) { showError(e.message); }
}

async function optimizeCopy() {
    saveIdentity();
    var pid = $('designProductId').value.trim();
    if (!pid) { showInfo('请输入商品ID'); return; }
    try {
        var d = await fetchJson(
            state.apiBase + '/api/pdm/products/' + pid + '/design/copy-optimize',
            { method: 'POST' }, 'AI 文案优化');
        var c = d.data || {};
        var warn = (c.bannedHits || []).length ?
            ('\n⚠ 禁用词警告: ' + c.bannedHits.join('; ')) : '';
        showDesign('生成轨: ' + c.track + warn +
            '\n标题: ' + c.title +
            '\n副标题: ' + c.subtitle +
            '\n描述: ' + c.description +
            '\n(仅建议不入库, 采纳后经编辑轨落库)');
    } catch (e) { showError(e.message); }
}

async function mainImageAB() {
    saveIdentity();
    var pid = $('designProductId').value.trim();
    if (!pid) { showInfo('请输入商品ID'); return; }
    try {
        var d = await fetchJson(
            state.apiBase + '/api/pdm/products/' + pid + '/design/main-image-ab',
            null, '主图 A/B 建议');
        var a = d.data || {};
        var cand = (a.candidates || []).map(function (c) {
            return 'v' + c.version + ': ' + String(c.main).slice(0, 80);
        }).join('\n');
        showDesign('样本充分: ' + !!a.sufficient +
            '\n候选主图:\n' + (cand || '无') +
            '\n销量/评分: ' + (a.salesTotal != null ? a.salesTotal : '-') +
            '/' + (a.ratingAvg != null ? a.ratingAvg : '-') +
            '\n建议: ' + a.advice +
            '\n(' + a.recommendation + ')');
    } catch (e) { showError(e.message); }
}

/* ========= ⑦ 智能下架建议 ========= */

async function loadAdvice() {
    saveIdentity();
    var d = await fetchJson(state.apiBase + '/api/pdm/listing-advice?limit=50',
        null, '下架建议');
    var list = d.data || [];
    $('adviceCount').textContent = '建议 ' + (d.count || 0) + ' 件';
    var rows = list.map(function (a) {
        return '<tr>' +
            '<td class="cap-id">' + esc(a.productId) + '</td>' +
            '<td>' + esc(a.name) + '</td>' +
            '<td class="num">' + (a.salesMonthly || 0) + '</td>' +
            '<td class="num">' + (a.stock || 0) + '</td>' +
            '<td class="num">' + (a.aiScore != null ? a.aiScore : '-') + '</td>' +
            '<td style="white-space:normal;max-width:360px;font-size:12px;">' +
                esc((a.reasons || []).join('; ')) + '</td>' +
            '<td><button class="btn-mini danger" onclick="delistProduct(\'' + a.productId + '\')">确认下架</button></td></tr>';
    }).join('');
    $('adviceBody').innerHTML = rows ||
        '<tr><td colspan="7" class="dash-empty">暂无建议(所有在售商品健康)</td></tr>';
}

/* ========= 主流程 ========= */

async function refreshData() {
    try {
        await Promise.all([
            loadOverview(), loadProducts(), loadPending(),
            loadImages(), loadAdvice(),
        ]);
        $('lastUpdate').textContent = '最后更新 ' +
            new Date().toLocaleTimeString();
    } catch (e) {
        showError(e.message);
        $('lastUpdate').textContent = '刷新失败 ' +
            new Date().toLocaleTimeString();
    }
}

(function init() {
    $('apiBase').value = state.apiBase;
    $('memberId').value = state.memberId;
    $('roleSel').value = state.role;
    $('memberId').addEventListener('change', refreshData);
    $('roleSel').addEventListener('change', refreshData);
    refreshData();
    startTimer();
})();
