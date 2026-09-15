/**
 * 支付模块 API 客户端 + 支付拉起(05号收款)
 * ============================================================
 * 业务端点(钱包充值/ SVIP 购买)只创建支付单, 实际收款走本模块:
 *   1. 业务端点返回 payNo(待支付)
 *   2. startPay 发起渠道支付(mock 渠道返回即 paid; real 渠道
 *      返回 paying + 真实预支付参数 payParams)
 *   3. completePay 按方式拉起支付(P1-3) 并轮询终态, paid 后业务
 *      权益自动入账/开通(后端回调分发, 前端只需刷新数据)
 *
 * 拉起方式(real 渠道 payParams, 后端 P0-1 回填):
 *   - 小程序 jsapi: Taro.requestPayment(五元组 appId/timeStamp/
 *     nonceStr/package/signType/paySign)
 *   - H5 微信 h5: h5Url 跳转; 支付宝 wap/page: payUrl 跳转
 *     (跳转前落 storage, 返回后 resumePendingPay 恢复轮询)
 *   - 扫码(native codeUrl / 支付宝 qrCode): onQrCode 回调渲染
 *     二维码, 页面继续轮询
 *   - 用户取消: 关闭支付单(reason=USER_CANCEL) → 「支付未完成」
 */

import Taro from '@tarojs/taro';
import { request } from './request';

/** 支付状态字典(后端 PAY_STATUS_*) */
export const PAY_STATUS_NAME: Record<string, string> = {
  pending: '待支付',
  paying: '支付中',
  paid: '已支付',
  failed: '支付失败',
  closed: '已关闭',
  refunding: '退款中',
  refunded: '已退款',
};

/** 跨页待支付恢复键(重定向支付返回后轮询) */
const PENDING_PAY_KEY = 'pending_pay_no';

/** 渠道预支付参数(后端 startPay 响应 payParams; real 渠道回填) */
export interface PayParamsVO {
  channel?: string;
  method?: string;
  actualAmount?: number;
  expireTime?: string;
  /** 微信 jsapi 五元组(小程序拉起) */
  appId?: string;
  timeStamp?: string;
  nonceStr?: string;
  package?: string;
  signType?: string;
  paySign?: string;
  /** 微信 h5 跳转链接 */
  h5Url?: string;
  /** 微信 native 扫码码 */
  codeUrl?: string;
  /** 支付宝 wap/page 跳转链接 */
  payUrl?: string;
  /** 支付宝 qr 扫码码 */
  qrCode?: string;
}

/** 发起支付返回 */
export interface PayStartVO {
  payNo: string;
  status: string;              // paying | paid(mock 自动落账)
  statusName: string;
  actualAmount?: number;
  channelMode?: string;        // mock | real | mock_fallback
  dispatch?: PayDispatchVO;    // mock 落账时携带业务分发结果
  payParams?: PayParamsVO;     // real 渠道真实预支付参数
}

/** 支付成功业务分发结果(后端 _dispatch_business) */
export interface PayDispatchVO {
  granted: boolean;
  business?: any;              // 钱包入账/SVIP 开通明细
  msg?: string;
  error?: string;
}

/** 支付单详情 */
export interface PayDetailVO {
  payNo: string;
  orderId: string;
  orderType: string;           // wallet_deposit | member_svip | retail...
  status: string;
  statusName: string;
  actualAmount: number;
  expireTime?: string;
  dispatchError?: string;
}

/** 支付流程终态结果 */
export interface PayResultVO {
  paid: boolean;
  status: string;              // paid | closed | failed | refunded | cancelled | timeout
  dispatch?: PayDispatchVO;
}

export const PaymentAPI = {
  /** 发起渠道支付(待支付 → 支付中 → mock 自动落账/real 返回预支付参数) */
  async startPay(payNo: string): Promise<PayStartVO> {
    const res = await request<any>({
      url: `/api/payment/${payNo}/start`,
      method: 'POST',
      data: {},
    });
    return {
      payNo: res.payNo || payNo,
      status: res.status || '',
      statusName: res.statusName || PAY_STATUS_NAME[res.status] || '',
      actualAmount: res.payParams?.actualAmount,
      channelMode: res.channelMode,
      dispatch: res.dispatch,
      payParams: res.payParams,
    };
  },

  /** 查询支付单详情(轮询终态用) */
  async getPay(payNo: string): Promise<PayDetailVO> {
    const res = await request<any>({ url: `/api/payment/${payNo}` });
    return {
      payNo: res.payNo || payNo,
      orderId: res.orderId || '',
      orderType: res.orderType || '',
      status: res.status || '',
      statusName: res.statusName || PAY_STATUS_NAME[res.status] || '',
      actualAmount: Number(res.actualAmount ?? 0),
      expireTime: res.expireTime || '',
      dispatchError: res.dispatchError || '',
    };
  },

  /** 关闭支付单(用户取消/超时放弃) */
  async closePay(payNo: string, reason = 'USER_CANCEL'): Promise<any> {
    return await request<any>({
      url: `/api/payment/${payNo}/close`,
      method: 'POST',
      data: { reason },
    });
  },
};

/** 支付终态判定 */
function isFinalStatus(status: string): boolean {
  return ['paid', 'failed', 'closed', 'refunded'].includes(status);
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function clearPendingPay(payNo: string): void {
  try {
    if (Taro.getStorageSync(PENDING_PAY_KEY) === payNo) {
      Taro.removeStorageSync(PENDING_PAY_KEY);
    }
  } catch (e) { /* storage 异常静默 */ }
}

/**
 * 拉起渠道支付(平台×方式分发, P1-3)
 *
 * @param params startPay 返回的 payParams(real 渠道含预支付参数)
 * @param payNo 支付单号(取消时关闭)
 * @param onQrCode 扫码方式回调(native/qr: 页面渲染二维码并继续轮询)
 * @returns true=已拉起(或无可拉起参数直接轮询); false=用户取消/关闭
 */
export async function invokePayment(
  params: PayParamsVO, payNo: string,
  onQrCode?: (code: string) => void,
): Promise<boolean> {
  // ① 小程序 jsapi 五元组拉起
  if (process.env.TARO_ENV === 'weapp' && params.paySign
      && params.timeStamp && params.nonceStr && params.package) {
    try {
      await Taro.requestPayment({
        timeStamp: params.timeStamp,
        nonceStr: params.nonceStr,
        package: params.package,
        signType: (params.signType || 'RSA') as any,
        paySign: params.paySign,
      });
      return true;
    } catch (e) {
      console.warn('[payment] requestPayment 取消/失败:', e);
      PaymentAPI.closePay(payNo, 'USER_CANCEL').catch(() => {});
      return false;
    }
  }
  // ② H5 跳转类(微信 h5Url / 支付宝 payUrl)——落 storage 供返回恢复
  const redirectUrl = params.h5Url || params.payUrl;
  if (redirectUrl && typeof window !== 'undefined') {
    try {
      Taro.setStorageSync(PENDING_PAY_KEY, payNo);
    } catch (e) { /* storage 异常仍继续跳转 */ }
    window.location.href = redirectUrl;
    return true;
  }
  // ③ 扫码类(微信 native codeUrl / 支付宝 qrCode)——回调渲染, 页面继续轮询
  const qrCode = params.codeUrl || params.qrCode;
  if (qrCode && onQrCode) {
    onQrCode(qrCode);
    return true;
  }
  // ④ 无可拉起参数(mock 模式/未知方式) → 直接进入轮询
  return true;
}

/**
 * 完成支付(通用支付流程)
 * 发起渠道支付 → mock 渠道立即 paid; real 渠道按方式拉起并轮询
 * 等待渠道回调落账。
 *
 * @param payNo 业务端点返回的支付单号
 * @param opts.onQrCode 扫码方式回调(页面渲染二维码)
 * @param opts.timeoutMs 轮询超时(real 渠道等待回调), 默认 60s
 * @returns { paid, status, dispatch } — paid=true 即权益已入账/开通
 */
export async function completePay(
  payNo: string,
  opts?: { onQrCode?: (code: string) => void; timeoutMs?: number },
): Promise<PayResultVO> {
  const timeoutMs = opts?.timeoutMs ?? 60000;
  const start = await PaymentAPI.startPay(payNo);
  if (start.status === 'paid') {
    return { paid: true, status: start.status, dispatch: start.dispatch };
  }
  if (start.status !== 'paying') {
    return { paid: false, status: start.status };
  }

  // real 渠道: 按方式拉起(取消 → 关单)
  const invoked = await invokePayment(
    start.payParams || {}, payNo, opts?.onQrCode);
  if (!invoked) {
    return { paid: false, status: 'cancelled' };
  }

  // 轮询支付单状态等待渠道回调
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await sleep(2000);
    try {
      const detail = await PaymentAPI.getPay(payNo);
      if (detail.status === 'paid') {
        clearPendingPay(payNo);
        return { paid: true, status: detail.status };
      }
      if (isFinalStatus(detail.status)) {
        clearPendingPay(payNo);
        return { paid: false, status: detail.status };
      }
    } catch (e) {
      // 轮询查询失败静默重试(网络抖动不中断支付等待)
      console.warn('[payment] 轮询支付状态失败, 重试:', e);
    }
  }
  return { paid: false, status: 'timeout' };
}

/**
 * 恢复挂起支付(H5 跳转支付返回后调用, 页面 useDidShow 触发)
 *
 * 检查 storage 中跨页挂起的支付单: paid → 到账刷新; 终态 → 清除;
 * 短窗口(15s)未到账 → 清除挂起(支付单 30 分钟内仍可由渠道回调落账,
 * 用户下次刷新可见)。
 */
export async function resumePendingPay(): Promise<PayResultVO | null> {
  let pending = '';
  try {
    pending = Taro.getStorageSync(PENDING_PAY_KEY) || '';
  } catch (e) { /* storage 异常视为无挂起 */ }
  if (!pending) return null;

  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try {
      const detail = await PaymentAPI.getPay(pending);
      if (detail.status === 'paid') {
        clearPendingPay(pending);
        return { paid: true, status: 'paid' };
      }
      if (isFinalStatus(detail.status)) {
        clearPendingPay(pending);
        return { paid: false, status: detail.status };
      }
    } catch (e) {
      console.warn('[payment] 恢复轮询失败, 重试:', e);
    }
    await sleep(1500);
  }
  clearPendingPay(pending);
  return { paid: false, status: 'timeout' };
}
