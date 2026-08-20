/* ============================================================
 * mutex.js · 悲观锁(Mutex) 工具类 (FIFO 队列实现)
 * ------------------------------------------------------------
 * 用途: 提供跨模块复用的并发控制工具,防止异步场景下的竞争条件
 *       · 库存扣减(防超卖)
 *       · 订单号生成(防重复)
 *       · 优惠券核销(防重复使用)
 *       · 任何需要串行化的临界区
 * ------------------------------------------------------------
 * 特性:
 *   · FIFO 等待队列: 每个等待者持有独立 Promise,
 *     release 时只唤醒队首唯一一个等待者(直接交接锁),
 *     彻底消除 thundering herd(旧 while+await 共享 Promise 实现
 *     会唤醒全部 N-1 个等待者、仅 1 个胜出, 呈 O(n²) 唤醒代价)
 *   · 支持单锁/多锁(多锁按 key 升序获取,反向释放,避免死锁)
 *   · 支持 withLock/withLocks 包装函数(自动释放)
 *   · 支持锁状态查询(isLocked)
 *   · 支持清空所有锁(clear,测试用)
 * ------------------------------------------------------------
 * 正确性说明:
 *   · JS 单线程下 acquire 内 "检查 _locked + 设置 _locked" 是同步原子的,
 *     不会出现两个任务同时通过空闲检查
 *   · 交接(hand-off)期间 _locked[key] 保持 true, 不留空窗,
 *     新到者仍会入队, 不会插队
 * ------------------------------------------------------------
 * 用法:
 *   // 单锁
 *   const release = await mutex.acquire('stock:2');
 *   try { ... } finally { release(); }
 *
 *   // 单锁(包装函数)
 *   await mutex.withLock('stock:2', async () => { ... });
 *
 *   // 多锁(包装函数,按 key 升序获取,反向释放)
 *   await mutex.withLocks(['stock:1', 'stock:2'], async () => { ... });
 * ============================================================ */

class Mutex {
    constructor() {
        // 持有标记: key → true(是否被持有)
        this._locked = {};
        // FIFO 等待队列: key → resolve 函数数组(每个 resolve 用 release 函数 resolve)
        this._queues = {};
    }

    /* ---------- 单锁 ---------- */

    /**
     * 获取指定 key 的锁(阻塞直到获取成功, FIFO 公平)
     * @param {string} key 锁标识(如 'stock:2')
     * @returns {Promise<Function>} resolve 后返回 release 函数
     */
    async acquire(key) {
        // 空闲: 直接获取(同步检查+设置, JS 单线程内原子, 无竞争)
        if (!this._locked[key]) {
            this._locked[key] = true;
            return () => this._release(key);
        }
        // 竞争: 入队等待, 每个 waiter 持有独立 Promise
        // release 时只 resolve 队首一个, 避免唤醒全部(消除 thundering herd)
        return new Promise(resolve => {
            (this._queues[key] || (this._queues[key] = [])).push(resolve);
        });
    }

    /**
     * 释放锁(内部方法, 由 acquire 返回的 release 函数调用)
     * 直接把锁交接给队首唯一一个等待者; 无等待者则真正释放
     */
    _release(key) {
        const q = this._queues[key];
        const next = q && q.length ? q.shift() : null;
        if (next) {
            // 交接: 仅唤醒队首一个 waiter, 把它的 release 函数传给它
            // _locked[key] 保持 true, 不留空窗, 新到者仍入队
            next(() => this._release(key));
        } else {
            // 无等待者, 真正释放
            delete this._locked[key];
            delete this._queues[key];
        }
    }

    /**
     * 用单锁包装函数(自动获取/释放)
     * @param {string} key 锁标识
     * @param {Function} fn 异步函数
     * @returns {Promise<any>} fn 的返回值
     */
    async withLock(key, fn) {
        const release = await this.acquire(key);
        try {
            return await fn();
        } finally {
            release();
        }
    }

    /* ---------- 多锁 ---------- */

    /**
     * 用多个锁包装函数(按 key 升序获取,反向释放,避免死锁)
     * @param {string[]} keys 锁标识数组
     * @param {Function} fn 异步函数
     * @returns {Promise<any>} fn 的返回值
     */
    async withLocks(keys, fn) {
        const sorted = [...new Set(keys)].sort();
        const releases = [];
        for (const k of sorted) {
            releases.push(await this.acquire(k));
        }
        try {
            return await fn();
        } finally {
            // 反向释放锁
            for (let i = releases.length - 1; i >= 0; i--) {
                try { releases[i](); } catch (e) { /* ignore */ }
            }
        }
    }

    /* ---------- 状态查询 ---------- */

    /**
     * 检查指定 key 的锁是否被持有
     * @param {string} key 锁标识
     * @returns {boolean} true=被持有(锁定中), false=空闲
     */
    isLocked(key) {
        return !!this._locked[key];
    }

    /**
     * 获取当前所有被持有的锁
     * @returns {string[]} 被持有的锁 key 数组
     */
    getLockedKeys() {
        return Object.keys(this._locked);
    }

    /* ---------- 清理(测试用) ---------- */

    /**
     * 清空所有锁状态(测试用,生产环境慎用)
     * 注: 清空时若仍有 pending waiter, 它们将不再被唤醒(与旧实现一致);
     *     测试应在每轮 Promise.all 完成后再 clear, 避免遗留等待者
     */
    clear() {
        this._locked = {};
        this._queues = {};
    }
}

// 暴露到全局(支持 window 和 global)
if (typeof window !== 'undefined') {
    window.Mutex = Mutex;
    // 全局单例(方便快速使用,不需要每次 new)
    if (!window.mutex) {
        window.mutex = new Mutex();
    }
}
if (typeof global !== 'undefined' && !global.Mutex) {
    global.Mutex = Mutex;
    if (!global.mutex) {
        global.mutex = new Mutex();
    }
}
