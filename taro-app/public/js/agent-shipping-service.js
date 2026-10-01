/**
 * agent-shipping-service.js  ·  代理商区域发货服务认领模块（基于工具包）
 * ============================================================
 * 用途:
 *   实现代理商对区域的"发货+售后"服务认领,以及厂家按为网店发货数量
 *   给予代理商 5% 同品分润作为服务费的计提。
 *
 * 业务规则(对齐《代理商管理模块设计文档》§区域发货服务认领):
 *   · 一个区域同一时间只能被一个代理商认领(一区一代理)
 *   · 认领区域内的网站订单由该代理商发货+售后;未认领区域由厂家直供
 *   · 代理商为网店发货的订单,厂家按订单金额 5% 计提同品分润作为服务费
 *   · 服务费以"同品"(等值产品)形式发放,记录状态 待发放/已发放
 *
 * 基于 toolkit/ 工具包:
 *   · UpgradeLogger         → 结构化事务日志
 *   · TransactionTemplate   → 事务编排(BEGIN/COMMIT/ROLLBACK/异步任务)
 *
 * 双 API 设计(与 inventory-service.js 一致):
 *   1) 事务入口  : claim(agentId, region) / release(agentId, region)
 *   2) 共享核心  : accrueServiceFee(dbRef, payload, logger)
 *                  在调用方(checkout)事务内执行,写 dbRef.db.service_fees,
 *                  不开子事务(随外层事务提交/回滚,保证原子性)
 *   3) 只读查询  : resolveShipper(region) / listClaims() / getServiceFeeSettlement(agentId)
 *
 * 使用示例:
 *   await AgentShippingService.claim(1, '山东泰安');
 *   const shipper = AgentShippingService.resolveShipper('山东泰安');
 *   // checkout 内部: AgentShippingService.accrueServiceFee(dbRef, {...}, ctx.logger)
 *
 * 浏览器环境:
 *   需先加载 toolkit/upgrade-logger.js + toolkit/transaction-template.js
 *   全局名: AgentShippingService / window.AgentShippingService
 * ============================================================
 */

const AgentShippingService = (function () {
    'use strict';

    const STORAGE_KEY = 'zhuxiang_shipping_db_v1';

    // ---------- 配置 ----------
    const CONFIG = {
        // 厂家给代理商的同品分润服务费率(按为网店发货的订单金额计)
        SERVICE_FEE_RATE: 0.05,
        // 认领状态
        CLAIM_ACTIVE: '已认领',
        CLAIM_RELEASED: '已退出',
    };

    let mode = 'mock'; // 'mock' | 'live'
    let apiBase = '/api/agent-shipping';

    // ---------- Mock DB ----------
    function readDB() {
        try {
            return EnvAdapter.storage.get(STORAGE_KEY) || initMockDB(true);
        } catch (e) {
            return initMockDB(true);
        }
    }

    function writeDB(db) {
        EnvAdapter.storage.set(STORAGE_KEY, db);
    }

    function initMockDB(forceWrite) {
        const existing = forceWrite ? null : EnvAdapter.storage.get(STORAGE_KEY);
        if (existing && !forceWrite) return existing;

        // 代理商名录(与 agent-upgrade-service.js mock 对齐,供认领校验)
        const db = {
            agents: [
                { id: 1, name: '张三酒业', agent_level: '市级', region: '山东泰安' },
                { id: 2, name: '李四酒业', agent_level: '核心', region: '山东济南' },
                { id: 3, name: '王五酒业', agent_level: '战略', region: '北京' },
            ],
            shipping_claims: [],  // 认领记录
            service_fees: [],     // 厂家→代理商 服务费流水(镜像;实际原子记录在 checkout DB)
            tx_log: [],           // BEGIN/COMMIT/ROLLBACK
        };
        writeDB(db);
        return db;
    }

    // ---------- Mock 事务适配器(快照模式,与 checkout/inventory 一致) ----------
    function createAdapter(dbRef) {
        return {
            begin(ctx) {
                const snapshot = JSON.parse(JSON.stringify(dbRef.db));
                dbRef.db.tx_log.push({ type: 'BEGIN', time: new Date().toISOString() });
                ctx.logger.info('阶段2-开启事务', '事务已开启(快照已建立)', {
                    txLogLen: dbRef.db.tx_log.length,
                });
                return snapshot;
            },
            commit(_snapshot, ctx) {
                dbRef.db.tx_log.push({ type: 'COMMIT', time: new Date().toISOString(), steps: 3 });
                writeDB(dbRef.db);
                ctx.logger.info('阶段5-事务提交', '事务提交成功(已写入)', {
                    claimId: ctx.claimId || null,
                });
            },
            rollback(snapshot, ctx) {
                if (snapshot) {
                    snapshot.tx_log.push({ type: 'ROLLBACK', time: new Date().toISOString() });
                    dbRef.db = snapshot;
                    writeDB(snapshot);
                    ctx.logger.error('回滚', '事务已回滚(快照恢复)', {
                        claimId: ctx.claimId || '(未创建)',
                    });
                }
            },
        };
    }

    function round2(n) { return Math.round(n * 100) / 100; }

    // ============================================================
    //  共享核心: accrueServiceFee(在调用方事务内执行)
    // ============================================================
    /**
     * 厂家按订单金额 5% 计提同品分润服务费,写入调用方(checkout)事务的 DB,
     * 随外层事务提交/回滚,保证原子性。不开子事务。
     *
     * @param {Object} dbRef   - 调用方 dbRef 包装对象 { db }
     * @param {Object} payload - { agentId, agentName, region, orderNo, shippedQty, orderAmount }
     * @param {Object} logger  - 调用方 ctx.logger(UpgradeLogger 实例)
     * @returns {Object} { serviceFee, record }
     */
    function accrueServiceFee(dbRef, payload, logger) {
        const o = payload || {};
        // 确保表存在(与 InventoryService.applyDeduct 的 ensureTables 同模式)
        if (!dbRef || !dbRef.db) throw new Error('accrueServiceFee: dbRef 缺失');
        if (!Array.isArray(dbRef.db.service_fees)) dbRef.db.service_fees = [];

        const agentId = Number(o.agentId);
        const orderAmount = Number(o.orderAmount);
        if (!Number.isFinite(agentId) || agentId <= 0) {
            throw new Error('服务费计提: 代理商ID无效');
        }
        if (!Number.isFinite(orderAmount) || orderAmount < 0) {
            throw new Error('服务费计提: 订单金额无效');
        }

        const fee = round2(orderAmount * CONFIG.SERVICE_FEE_RATE);
        const feeId = 'SF' + Date.now() + '-' + Math.floor(Math.random() * 1000);

        const record = {
            id: feeId,
            agent_id: agentId,
            agent_name: o.agentName || '',
            order_no: o.orderNo || null,
            region: o.region || '',
            shipped_qty: o.shippedQty || 0,
            order_amount: round2(orderAmount),
            service_fee: fee,
            service_rate: CONFIG.SERVICE_FEE_RATE,
            settled_as: '同品', // 厂家以等值产品发放
            status: '待发放',
            created_at: new Date().toISOString(),
        };
        dbRef.db.service_fees.push(record);

        // 镜像写入本服务 DB(供代理商端汇总查询;非原子,仅记账)
        try {
            const sdb = readDB();
            sdb.service_fees.push({ ...record });
            writeDB(sdb);
        } catch (e) { /* 镜像失败不影响主事务 */ }

        if (logger && logger.info) {
            logger.info('阶段8-分润计算', '厂家→代理商服务费已计提(同品分润)', {
                agent: record.agent_name,
                region: record.region,
                orderNo: record.order_no,
                orderAmount: record.order_amount,
                rate: CONFIG.SERVICE_FEE_RATE,
                serviceFee: fee,
                settledAs: '同品',
            });
        }
        return { serviceFee: fee, record: record };
    }

    // ============================================================
    //  只读查询: resolveShipper(无事务)
    // ============================================================
    /**
     * 根据区域解析发货方:已认领→该代理商;未认领→厂家直供
     * @param {String} region
     * @returns {Object} { shipper:'agent'|'manufacturer', agentId, agentName, claimId }
     */
    function resolveShipper(region) {
        if (!region) {
            return { shipper: 'manufacturer', agentId: null, agentName: '厂家直供', claimId: null, region: region || '' };
        }
        const db = readDB();
        const claim = (db.shipping_claims || []).find(
            c => c.region === region && c.status === CONFIG.CLAIM_ACTIVE
        );
        if (claim) {
            return {
                shipper: 'agent',
                agentId: claim.agent_id,
                agentName: claim.agent_name,
                claimId: claim.id,
                region: region,
            };
        }
        return { shipper: 'manufacturer', agentId: null, agentName: '厂家直供', claimId: null, region: region };
    }

    // ============================================================
    //  事务入口: claim(认领)
    // ============================================================
    async function claim(agentId, region) {
        const Logger = (typeof window !== 'undefined') ? window.UpgradeLogger : null;
        const Template = (typeof window !== 'undefined') ? window.TransactionTemplate : null;
        if (!Logger || !Template) {
            throw new Error('AgentShippingService 需要工具包,请先加载 toolkit/upgrade-logger.js 和 toolkit/transaction-template.js');
        }

        const dbRef = { db: readDB() };
        const adapter = createAdapter(dbRef);
        const template = new Template({ name: 'agent_shipping_claim', adapter: adapter });

        const result = await template.run({
            context: {
                agentId, region,
                claimId: null, agentName: null,
            },

            // ---------- 事务前只读结构校验 ----------
            preflight: async (ctx) => {
                ctx.logger.info('阶段1-参数校验', '开始校验认领请求', {
                    agentId: ctx.agentId, region: ctx.region,
                });
                if (!ctx.agentId || typeof ctx.agentId !== 'number') {
                    ctx.logger.error('阶段1-参数校验', '代理商ID无效');
                    return { abort: true, reason: '代理商ID无效' };
                }
                if (!ctx.region || typeof ctx.region !== 'string' || !ctx.region.trim()) {
                    ctx.logger.error('阶段1-参数校验', '区域不能为空');
                    return { abort: true, reason: '区域不能为空' };
                }
            },

            // ---------- 事务内阶段 ----------
            stages: [
                // 阶段2: 开启事务
                {
                    name: '阶段2-开启事务',
                    action: async (ctx) => {
                        ctx.conn = await ctx.template.adapter.begin(ctx);
                    },
                },

                // 阶段3: 认领校验(业务校验在事务内,失败抛错触发回滚)
                {
                    name: '阶段3-认领校验',
                    action: async (ctx) => {
                        const agent = dbRef.db.agents.find(a => a.id === ctx.agentId);
                        if (!agent) {
                            throw new Error(`代理商不存在: id=${ctx.agentId}`);
                        }
                        const existing = dbRef.db.shipping_claims.find(
                            c => c.region === ctx.region && c.status === CONFIG.CLAIM_ACTIVE
                        );
                        if (existing) {
                            throw new Error(`区域已被认领: ${ctx.region} (代理商${existing.agent_name})`);
                        }
                        ctx.agentName = agent.name;
                        ctx.logger.info('阶段3-认领校验', '校验通过,区域可认领', {
                            agent: agent.name, region: ctx.region,
                        });
                    },
                },

                // 阶段4: 写入认领记录
                {
                    name: '阶段4-写入认领',
                    action: async (ctx) => {
                        ctx.claimId = 'SC' + Date.now() + '-' + Math.floor(Math.random() * 1000);
                        dbRef.db.shipping_claims.push({
                            id: ctx.claimId,
                            agent_id: ctx.agentId,
                            agent_name: ctx.agentName,
                            region: ctx.region,
                            status: CONFIG.CLAIM_ACTIVE,
                            service_rate: CONFIG.SERVICE_FEE_RATE,
                            claimed_at: new Date().toISOString(),
                            shipped_qty: 0,
                            service_fee_accrued: 0,
                        });
                        ctx.logger.info('阶段4-写入认领', '认领记录已写入', {
                            claimId: ctx.claimId, agent: ctx.agentName, region: ctx.region,
                        });
                    },
                },

                // 阶段5: 提交事务
                {
                    name: '阶段5-提交事务',
                    action: async (ctx) => {
                        ctx.logger.info('阶段5-事务提交', '准备提交事务', {
                            executedStages: ctx.logger.executedStages(),
                            totalElapsedMs: ctx.logger.totalElapsedMs(),
                        });
                        await ctx.template.adapter.commit(ctx.conn, ctx);
                        ctx.conn = null;
                    },
                },
            ],

            // ---------- 事务后异步任务 ----------
            asyncTasks: [
                {
                    name: '阶段6-通知代理',
                    action: (ctx) => {
                        ctx.logger.info('阶段6-通知代理', '认领成功通知已发送', {
                            claimId: ctx.claimId, region: ctx.region,
                        });
                    },
                },
                {
                    name: '阶段6-区块链存证',
                    action: (ctx) => {
                        ctx.logger.info('阶段6-区块链存证', '认领记录上链完成', {
                            claimId: ctx.claimId,
                            hash: '0x' + Date.now().toString(16),
                        });
                    },
                },
            ],
        });

        // ========== 结果形状转换(与 checkout 一致) ==========
        const logs = result.logs.map(l => ({ ...l, msg: l.message }));
        if (result.aborted) {
            return { success: false, error: result.reason, logs };
        }
        if (result.success) {
            const ctx = result.ctx;
            return {
                success: true,
                claimId: ctx.claimId,
                details: {
                    claimId: ctx.claimId,
                    agentId: ctx.agentId,
                    agentName: ctx.agentName,
                    region: ctx.region,
                    status: CONFIG.CLAIM_ACTIVE,
                    serviceRate: CONFIG.SERVICE_FEE_RATE,
                },
                logs,
                asyncOps: ['agent_notify', 'blockchain_notarize'],
            };
        }
        return {
            success: false,
            error: result.error,
            failedStage: result.failedStage,
            executedStages: result.executedStages,
            logs,
        };
    }

    // ============================================================
    //  事务入口: release(释放认领)
    // ============================================================
    async function release(agentId, region) {
        const Logger = (typeof window !== 'undefined') ? window.UpgradeLogger : null;
        const Template = (typeof window !== 'undefined') ? window.TransactionTemplate : null;
        if (!Logger || !Template) {
            throw new Error('AgentShippingService 需要工具包,请先加载 toolkit/upgrade-logger.js 和 toolkit/transaction-template.js');
        }

        const dbRef = { db: readDB() };
        const adapter = createAdapter(dbRef);
        const template = new Template({ name: 'agent_shipping_release', adapter: adapter });

        const result = await template.run({
            context: {
                agentId, region,
                claimId: null, agentName: null,
            },

            preflight: async (ctx) => {
                ctx.logger.info('阶段1-参数校验', '开始校验释放请求', {
                    agentId: ctx.agentId, region: ctx.region,
                });
                if (!ctx.agentId || typeof ctx.agentId !== 'number') {
                    return { abort: true, reason: '代理商ID无效' };
                }
                if (!ctx.region || typeof ctx.region !== 'string' || !ctx.region.trim()) {
                    return { abort: true, reason: '区域不能为空' };
                }
            },

            stages: [
                {
                    name: '阶段2-开启事务',
                    action: async (ctx) => { ctx.conn = await ctx.template.adapter.begin(ctx); },
                },
                // 阶段3: 释放校验(业务校验在事务内)
                {
                    name: '阶段3-释放校验',
                    action: async (ctx) => {
                        const claim = dbRef.db.shipping_claims.find(
                            c => c.region === ctx.region
                                && c.agent_id === ctx.agentId
                                && c.status === CONFIG.CLAIM_ACTIVE
                        );
                        if (!claim) {
                            throw new Error(`无有效认领可释放: 代理商${ctx.agentId} / 区域${ctx.region}`);
                        }
                        ctx.claimId = claim.id;
                        ctx.agentName = claim.agent_name;
                        ctx.logger.info('阶段3-释放校验', '校验通过,存在有效认领', {
                            claimId: claim.id,
                        });
                    },
                },
                // 阶段4: 置为已退出
                {
                    name: '阶段4-释放认领',
                    action: async (ctx) => {
                        const claim = dbRef.db.shipping_claims.find(c => c.id === ctx.claimId);
                        claim.status = CONFIG.CLAIM_RELEASED;
                        claim.released_at = new Date().toISOString();
                        ctx.logger.info('阶段4-释放认领', '认领已置为已退出', {
                            claimId: ctx.claimId, region: ctx.region,
                        });
                    },
                },
                {
                    name: '阶段5-提交事务',
                    action: async (ctx) => {
                        await ctx.template.adapter.commit(ctx.conn, ctx);
                        ctx.conn = null;
                    },
                },
            ],

            asyncTasks: [
                {
                    name: '阶段6-通知代理',
                    action: (ctx) => {
                        ctx.logger.info('阶段6-通知代理', '释放认领通知已发送', { claimId: ctx.claimId });
                    },
                },
            ],
        });

        const logs = result.logs.map(l => ({ ...l, msg: l.message }));
        if (result.aborted) {
            return { success: false, error: result.reason, logs };
        }
        if (result.success) {
            const ctx = result.ctx;
            return {
                success: true,
                claimId: ctx.claimId,
                details: {
                    claimId: ctx.claimId,
                    agentId: ctx.agentId,
                    agentName: ctx.agentName,
                    region: ctx.region,
                    status: CONFIG.CLAIM_RELEASED,
                },
                logs,
                asyncOps: ['agent_notify'],
            };
        }
        return {
            success: false,
            error: result.error,
            failedStage: result.failedStage,
            executedStages: result.executedStages,
            logs,
        };
    }

    // ============================================================
    //  只读查询 API
    // ============================================================
    function listClaims(statusFilter) {
        const claims = readDB().shipping_claims || [];
        if (!statusFilter) return claims;
        return claims.filter(c => c.status === statusFilter);
    }

    function listServiceFees(db) {
        const src = db || readDB();
        return src.service_fees || [];
    }

    function getServiceFeeSettlement(agentId) {
        const fees = (readDB().service_fees || []).filter(f => f.agent_id === agentId);
        const pending = fees.filter(f => f.status === '待发放');
        const settled = fees.filter(f => f.status === '已发放');
        const sum = (arr) => round2(arr.reduce((s, f) => s + f.service_fee, 0));
        return {
            agentId: agentId,
            totalCount: fees.length,
            pendingCount: pending.length,
            pendingAmount: sum(pending),
            settledAmount: sum(settled),
            settledAs: '同品',
        };
    }

    // ---------- Live: 调用后端 API(P5.1) ----------
    // 通用 live 请求: 非 2xx 时解析后端错误体{success:false, error},
    // 保持与 mock 失败形状一致(调用方统一按 result.success 判断)
    async function liveRequest(path, method, data) {
        const r = await EnvAdapter.request({
            url: apiBase + path,
            method: method,
            data: data,
            header: { 'Content-Type': 'application/json' },
        });
        if (!r.ok) {
            try {
                // GET 分支 EnvAdapter 直接填充 res.data; 非 GET 走 lazy json()
                const body = method === 'GET' ? r.data : await r.json();
                if (body && typeof body === 'object' && 'success' in body) return body;
            } catch (e) { /* 非 JSON 错误体, 走 HTTP 错误 */ }
            throw new Error('HTTP ' + r.status);
        }
        return method === 'GET' ? r.data : await r.json();
    }

    function liveError(e) {
        return { success: false, error: e.message, logs: [{ step: '回滚', level: 'ERROR', msg: 'API调用失败: ' + e.message }] };
    }

    // ---------- 公共 API ----------
    return {
        CONFIG: CONFIG,
        init() {
            if (!EnvAdapter.storage.get(STORAGE_KEY)) initMockDB(true);
        },
        setMode(m) { mode = m; return this; },
        setApiBase(base) { apiBase = base; return this; },
        getMode() { return mode; },
        resetMock() { EnvAdapter.storage.remove(STORAGE_KEY); initMockDB(true); return this; },
        getMockDB() { return readDB(); },
        // 事务入口(mock/live 双模式: live 走后端事务, 响应形状一致)
        async claim(agentId, region) {
            if (mode === 'live') {
                try { return await liveRequest('/claim', 'POST', { agentId: agentId, region: region }); }
                catch (e) { return liveError(e); }
            }
            return await claim(agentId, region);
        },
        async release(agentId, region) {
            if (mode === 'live') {
                try { return await liveRequest('/release', 'POST', { agentId: agentId, region: region }); }
                catch (e) { return liveError(e); }
            }
            return await release(agentId, region);
        },
        // 共享核心(供 checkout 在其事务内委托; live 模式下由后端事务内部完成, 前端不再调用)
        accrueServiceFee,
        // 只读查询
        resolveShipper,
        // listClaims 双模式: mock 同步返回数组; live 返回 Promise(富记录数组,
        // 调用方需 await, 字段: claimId/agentId/agentName/region/status/serviceRate/claimedAt)
        listClaims(statusFilter) {
            if (mode === 'live') {
                return liveRequest('/claims?detail=true', 'GET')
                    .then(function (body) {
                        const claims = (body && body.claims) || [];
                        if (!statusFilter) return claims;
                        return claims.filter(function (c) { return c.status === statusFilter; });
                    })
                    .catch(function (e) { return []; });
            }
            const claims = readDB().shipping_claims || [];
            if (!statusFilter) return claims;
            return claims.filter(c => c.status === statusFilter);
        },
        listServiceFees(db) {
            const src = db || readDB();
            return src.service_fees || [];
        },
        // getServiceFeeSettlement 双模式: mock 同步返回统计对象; live 返回 Promise
        getServiceFeeSettlement(agentId) {
            if (mode === 'live') {
                return liveRequest('/settlement?agentId=' + encodeURIComponent(agentId), 'GET')
                    .then(function (body) {
                        if (body && body.success) {
                            return body.details || body;   // 优先取 details, 兼容顶层平铺
                        }
                        return { agentId: agentId, totalCount: 0, pendingCount: 0,
                                 pendingAmount: 0, settledAmount: 0, settledAs: '同品' };
                    })
                    .catch(function (e) { return null; });
            }
            const fees = (readDB().service_fees || []).filter(f => f.agent_id === agentId);
            const pending = fees.filter(f => f.status === '待发放');
            const settled = fees.filter(f => f.status === '已发放');
            const sum = (arr) => round2(arr.reduce((s, f) => s + f.service_fee, 0));
            return {
                agentId: agentId,
                totalCount: fees.length,
                pendingCount: pending.length,
                pendingAmount: sum(pending),
                settledAmount: sum(settled),
                settledAs: '同品',
            };
        },
    };
})();

// 暴露到 window 全局
if (typeof window !== 'undefined') {
    window.AgentShippingService = AgentShippingService;
}
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { AgentShippingService };
}
