/**
 * API 请求封装 · 对接后端 FastAPI (http://127.0.0.1:8000)
 * ============================================================
 * 微信小程序真机预览: 需在开发者工具 "详情 → 本地设置" 勾选
 *   "不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书"
 *
 * 后端字段结构(与 mock 不同, 需在各自 api 文件做映射):
 *   product: product_id / name / price / stock / alcohol / volume / series / subtitle
 *   order:   order_id / status / items / priceDetail
 */

import Taro from '@tarojs/taro';

// 后端基地址(开发环境)
// H5 预览用 127.0.0.1, 真机预览用电脑局域网 IP(192.168.0.106)
export const API_BASE = 'http://192.168.0.106:8000';

// 当前登录会员(测试用: 李四, member_id=2, L5钻石会员)
export const CURRENT_MEMBER_ID = '2';

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
  const { url, method = 'GET', data, headers = {} } = options;

  const header: Record<string, string> = {
    'Content-Type': 'application/json',
    'X-Member-Id': CURRENT_MEMBER_ID,
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

    // 业务错误
    const errMsg = (res.data && res.data.detail) || `请求失败(${res.statusCode})`;
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
