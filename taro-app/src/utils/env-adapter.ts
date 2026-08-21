/**
 * env-adapter.ts · 环境适配层（Taro 多端版）
 * ============================================================
 * 统一 H5/微信小程序/支付宝小程序/抖音 的存储与网络接口
 * 基于 Taro API（process.env.TARO_ENV 自动识别平台）
 *
 * v2.1.0 对齐 js/env-adapter.js v1.3.0:
 *   · request 增加超时控制(默认 10s, fail-fast)
 *   · 超时后调用 RequestTask.abort 取消真实请求(避免晚到污染)
 *   · 超时 setTimeout 清理(避免高频请求定时器泄漏)
 *   · 支付宝 storage 返回值结构归一化(兼容 {data} 与直返)
 *   · 默认 Content-Type: application/json
 * ============================================================
 */

import Taro from '@tarojs/taro';

interface StorageAPI {
  get(key: string): any;
  set(key: string, value: any): boolean;
  remove(key: string): boolean;
}

interface RequestOpts {
  url: string;
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE';
  data?: any;
  header?: Record<string, string>;
  timeout?: number;
}

interface ResponseData {
  ok: boolean;
  status: number;
  json: () => Promise<any>;
  data: any;
}

class EnvAdapter {
  version = '2.1.0';

  getEnv(): string {
    return process.env.TARO_ENV || 'h5';
  }

  isH5(): boolean {
    return this.getEnv() === 'h5';
  }

  isWechatMini(): boolean {
    return this.getEnv() === 'weapp';
  }

  isAlipayMini(): boolean {
    return this.getEnv() === 'alipay';
  }

  storage: StorageAPI = {
    get(key: string): any {
      try {
        const val = Taro.getStorageSync(key);
        // 支付宝端 Taro.getStorageSync 可能返回 {data} 结构, 归一化(对齐 js 版)
        const raw = (val && typeof val === 'object' && 'data' in val && Object.keys(val).length === 1)
          ? (val as any).data : val;
        if (raw === '' || raw === undefined || raw === null) return null;
        if (typeof raw === 'string') {
          try { return JSON.parse(raw); } catch { return raw; }
        }
        return raw;
      } catch (e) {
        console.error('[EnvAdapter] storage.get failed:', e);
        return null;
      }
    },

    set(key: string, value: any): boolean {
      try {
        Taro.setStorageSync(key, value);
        return true;
      } catch (e) {
        console.error('[EnvAdapter] storage.set failed:', e);
        return false;
      }
    },

    remove(key: string): boolean {
      try {
        Taro.removeStorageSync(key);
        return true;
      } catch (e) {
        console.error('[EnvAdapter] storage.remove failed:', e);
        return false;
      }
    }
  };

  async request(opts: RequestOpts): Promise<ResponseData> {
    const timeout = opts.timeout || 10000;
    // 默认 Content-Type(对齐 js 版,后端 FastAPI 按 JSON 解析)
    const header = Object.assign({ 'Content-Type': 'application/json' }, opts.header || {});
    const method = opts.method || 'GET';

    let requestTask: any = null;
    let timerId: any = null;

    const realPromise = new Promise<ResponseData>((resolve, reject) => {
      requestTask = Taro.request({
        url: opts.url,
        method: method as any,
        data: opts.data,
        header,
        success: (res: any) => {
          resolve({
            ok: res.statusCode >= 200 && res.statusCode < 300,
            status: res.statusCode,
            json: () => Promise.resolve(res.data),
            data: res.data
          });
        },
        fail: (err: any) => {
          reject(new Error(err.errMsg || 'Taro.request failed'));
        }
      });
    });

    const timeoutPromise = new Promise<ResponseData>((_, reject) => {
      timerId = setTimeout(() => {
        // 超时取消真实请求(避免晚到响应污染状态)
        if (requestTask && typeof requestTask.abort === 'function') {
          try { requestTask.abort(); } catch (e) { /* 已完成/已取消 */ }
        }
        reject(new Error(`请求超时(${timeout}ms · ${method} ${opts.url})`));
      }, timeout);
    });

    // 超时竞争 + 定时器清理(对齐 js 版 v1.3.0,避免高频请求定时器泄漏)
    return Promise.race([realPromise, timeoutPromise]).then((res) => {
      if (timerId) clearTimeout(timerId);
      return res;
    }, (err) => {
      if (timerId) clearTimeout(timerId);
      throw err;
    });
  }
}

const envAdapter = new EnvAdapter();
export default envAdapter;
