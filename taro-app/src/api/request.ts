/**
 * API 请求封装 · 对接后端 FastAPI
 * ============================================================
 * 微信小程序真机预览: 需在开发者工具 "详情 → 本地设置" 勾选
 *   "不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书"
 *
 * 后端字段结构(与 mock 不同, 需在各自 api 文件做映射):
 *   product: product_id / name / price / stock / alcohol / volume / series / subtitle
 *   order:   order_id / status / items / priceDetail
 *
 * 地址与会员身份统一在 src/config/index.ts 管理
 */

import Taro from '@tarojs/taro';
import { API_BASE } from '@/config';
import {
  clearSession, getMemberId, getSession, refreshSession, requireLogin,
} from '@/services/auth-service';

interface RequestOptions {
  url: string;
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE';
  data?: any;
  headers?: Record<string, string>;
}

/**
 * 统一请求方法
 * 自动注入 X-Member-Id 头(会员身份)
 * 统一处理错误码(401/403/404/409/500)
 *
 * 调用形式:
 *   request({ url: '/api/product/list' })                     // GET
 *   request({ url: '/api/order/create', method: 'POST', data })  // POST
 */
export async function request<T = any>(options: RequestOptions): Promise<T> {
  return requestWithRetry(options, false);
}

/** _isRetry: 防止刷新后仍 401 的无限循环(仅重试一次) */
async function requestWithRetry<T = any>(
  options: RequestOptions, _isRetry: boolean,
): Promise<T> {
  const { url, method = 'GET', data, headers = {} } = options;

  // 会员身份动态注入(登录后为真实会员 ID, 未登录为空)
  const session = getSession();
  const header: Record<string, string> = {
    'Content-Type': 'application/json',
    'X-Member-Id': getMemberId(),
    ...(session?.accessToken ? { Authorization: `Bearer ${session.accessToken}` } : {}),
    ...headers,
  };

  try {
    const res = await Taro.request({
      url: `${API_BASE}${url}`,
      method,
      data,
      header,
      timeout: 10000,
    });

    // HTTP 状态码处理
    if (res.statusCode >= 200 && res.statusCode < 300) {
      return res.data as T;
    }

    // 401: 令牌过期 → 尝试静默刷新后重试一次; 刷新失败/未登录 → 清会话跳登录
    if (res.statusCode === 401) {
      if (!_isRetry) {
        const refreshed = await refreshSession();
        if (refreshed?.accessToken) {
          return requestWithRetry<T>(options, true);
        }
      }
      clearSession();
      Taro.showToast({ title: '登录已过期, 请重新登录', icon: 'none' });
      requireLogin(); // 会话已清空 → 必定跳转登录页
      throw new Error('登录已过期');
    }

    // 业务错误(兼容 detail / error 两种后端错误格式)
    const errMsg = (res.data && (res.data.detail || res.data.error)) || `请求失败(${res.statusCode})`;
    Taro.showToast({ title: errMsg, icon: 'none' });
    throw new Error(errMsg);
  } catch (e: any) {
    // 网络错误(连接失败/超时)
    if (e.errMsg && e.errMsg.includes('request:fail')) {
      Taro.showToast({ title: '网络连接失败,请检查后端是否启动', icon: 'none' });
    }
    throw e;
  }
}
