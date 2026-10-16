// ============================================================
// products-live.js —— 产品列表 live 化(前端开发实施计划 T1.1)
// 挂载协议: 页面先以 data.js PRODUCTS 渲染(不白屏) → 本模块异步拉取
//           /api/product/list 成功后以适配数据重渲染; 失败保持 mock(回落铁律)。
// 设计约束(技术架构全景/规格书 §1.2):
//   - 双模式同构: live 数据适配为 renderProductCard 既有字段, UI 零改动
//   - 所有后端文本 escapeHtml 后入 DOM(防注入)
//   - 不编造占位: 拉取失败即留 mock, 不用假数据填充
// 后端契约(实测 2026-10-16, 本地 8000):
//   GET /api/product/list?page=1&page_size=100 →
//   {success, total, products:[{product_id:"ZX42-2026B01", name, subtitle,
//    series, price, original_price, volume, tags[], featured, ...}]}
//   id 形态为字符串 SKU——与 data.js int id 属两套数据源, 详情页消费属 T1.2。
// ============================================================
window.ProductsLive = (function () {
    'use strict';

    var state = { mode: 'mock', products: [] };

    // ---------- HTML 转义(入 DOM 前统一过闸) ----------
    function esc(v) {
        if (v === null || v === undefined) return '';
        return String(v)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // ---------- 后端 summary → 前端卡片字段适配 ----------
    // renderProductCard 消费: id/name/category/series/desc/price/volume/tag;
    // 排序消费: price/tag('新品'); Cart.add 消费: id/name/price。
    function adapt(p) {
        var tags = Array.isArray(p.tags) ? p.tags : [];
        return {
            id: esc(p.product_id),
            name: esc(p.name),
            category: '竹香型',                 // 全系主打香型(后端无此维度, 前端固定)
            series: esc(p.series),
            price: Number(p.price) || 0,
            originalPrice: Number(p.original_price) || 0,
            volume: esc(p.volume),
            desc: esc(p.subtitle),
            tag: tags.length ? esc(tags[0]) : (p.featured ? '主打' : ''),
            isNew: tags.indexOf('新品') >= 0
        };
    }

    // ---------- 拉取(全量一次性, 产品池规模小; 筛选/排序由页面本地执行) ----------
    function fetchAll() {
        // 10s 超时: 后端不可达时快速回落, 不挂起页面
        var ctl = ('AbortController' in window) ? new AbortController() : null;
        var timer = ctl ? setTimeout(function () { ctl.abort(); }, 10000) : null;
        return fetch('/api/product/list?page=1&page_size=100', {
            method: 'GET',
            headers: { 'Accept': 'application/json' },
            signal: ctl ? ctl.signal : undefined
        }).then(function (r) {
            if (timer) clearTimeout(timer);
            if (!r.ok) throw new Error('http_' + r.status);
            return r.json();
        }).then(function (j) {
            if (!j || j.success !== true || !Array.isArray(j.products) || !j.products.length) {
                throw new Error('shape');
            }
            return j.products.map(adapt);
        });
    }

    // ---------- T1.2 详情适配(detail 全字段 → 详情页模板字段) ----------
    // 页面模板消费: name/desc/category/series/alcohol/volume/origin/price/originalPrice/id
    function adaptDetail(p) {
        var attrs = p.attributes || {};
        return {
            id: esc(p.product_id),
            name: esc(p.name),
            category: esc(attrs.aroma) || '竹香型',
            series: esc(p.series),
            alcohol: p.alcohol !== null && p.alcohol !== undefined ? esc(p.alcohol) + '度' : esc(attrs.alcohol),
            volume: esc(p.volume),
            origin: esc(p.origin),
            price: Number(p.price) || 0,
            originalPrice: Number(p.original_price) || 0,
            desc: esc(p.description || p.subtitle),
            tag: (Array.isArray(p.tags) && p.tags.length) ? esc(p.tags[0]) : (p.featured ? '主打' : ''),
            isNew: Array.isArray(p.tags) && p.tags.indexOf('新品') >= 0
        };
    }

    // ---------- T1.2 详情拉取: GET /api/product/{id} → {product, related[]} ----------
    function fetchDetail(id) {
        return fetch('/api/product/' + encodeURIComponent(id), {
            method: 'GET',
            headers: { 'Accept': 'application/json' }
        }).then(function (r) {
            if (!r.ok) throw new Error('http_' + r.status);
            return r.json();
        }).then(function (j) {
            if (!j || j.success !== true || !j.product || !j.product.product_id) {
                throw new Error('shape');
            }
            var related = Array.isArray(j.related) ? j.related.map(adapt) : [];
            return { product: adaptDetail(j.product), related: related };
        });
    }

    // ---------- 对外 API ----------
    return {
        /** 异步加载; 成功后 mode=live 并回调 onReady(由页面重渲染), 失败静默留 mock */
        load: function (onReady) {
            return fetchAll().then(function (items) {
                state.products = items;
                state.mode = 'live';
                document.body.setAttribute('data-products-mode', 'live');
                if (typeof onReady === 'function') onReady();
            }).catch(function (err) {
                // 回落铁律: 失败保持 data.js 渲染, 不白屏不报错弹窗
                document.body.setAttribute('data-products-mode', 'mock');
                console.warn('[ProductsLive] live 拉取失败, 回落 data.js 静态渲染:', err && err.message);
            });
        },
        /** 当前生效产品数组(live 适配数据); mock 态返回 null, 页面用 PRODUCTS */
        get: function () { return state.mode === 'live' ? state.products : null; },
        isLive: function () { return state.mode === 'live'; },
        /** Cart.add 兼容入口: 按 id 在 live 池查产品(mock 池查不到时回退到这里) */
        findById: function (id) {
            for (var i = 0; i < state.products.length; i++) {
                if (state.products[i].id === id) return state.products[i];
            }
            return null;
        },
        /** T1.2 详情拉取; Promise<{product, related[]}>; 失败 reject 由页面回落 mock */
        loadDetail: function (id) {
            return fetchDetail(id).then(function (res) {
                // 详情产品注册进 live 池: 供 Cart.add 回退 findById 查找(详情页未走列表 load)
                if (!state.products.some(function (x) { return x.id === res.product.id; })) {
                    state.products.push(res.product);
                }
                document.body.setAttribute('data-products-mode', 'live');
                return res;
            });
        }
    };
})();
