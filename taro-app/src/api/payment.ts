/**
 * 支付模块 API 客户端(05号收款)
 * ============================================================
 * 业务端点(钱包充值/ SVIP 购买)只创建支付单, 实际收款走本模块:
 *   1. 业务端点返回 payNo(待支付)
 *   2. startPay 发起渠道支付(mock 渠道返回即 paid; real 渠道
 *      返回 paying 由真实渠道回调落账)
 *   3. completePay 轮询支付终态, paid 后业务权益自动入账/开通
 *      (后端回调分发, 前端只需刷新数据)
 */

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

/** 发起支付返回 */
export interface PayStartVO {
  payNo: string;
  status: string;              // paying | paid(mock 自动落账)
  statusName: string;
  actualAmount?: number;
  channelMode?: string;        // mock | real | mock_fallback
  dispatch?: PayDispatchVO;    // mock 落账时携带业务分发结果
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

export const PaymentAPI = {
  /** 发起渠道支付(待支付 → 支付中 → mock 渠道自动落账为 paid) */
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
};

/** 支付终态判定 */
function isFinalStatus(status: string): boolean {
  return ['paid', 'failed', 'closed', 'refunded'].includes(status);
}

/**
 * 完成支付(通用支付流程)
 * 发起渠道支付 → mock 渠道立即 paid; real 渠道轮询等待回调落账。
 *
 * @param payNo 业务端点返回的支付单号
 * @param timeoutMs 轮询超时(real 渠道等待回调), 默认 60s
 * @returns { paid, status, dispatch } — paid=true 即权益已入账/开通
 */
export async function completePay(
  payNo: string,
  timeoutMs = 60000,
): Promise<{ paid: boolean; status: string; dispatch?: PayDispatchVO }> {
  const start = await PaymentAPI.startPay(payNo);
  if (start.status === 'paid') {
    return { paid: true, status: start.status, dispatch: start.dispatch };
  }

  // real 渠道: 轮询支付单状态等待渠道回调
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 2000));
    try {
      const detail = await PaymentAPI.getPay(payNo);
      if (detail.status === 'paid') {
        return { paid: true, status: detail.status };
      }
      if (isFinalStatus(detail.status)) {
        return { paid: false, status: detail.status };
      }
    } catch (e) {
      // 轮询查询失败静默重试(网络抖动不中断支付等待)
      console.warn('[payment] 轮询支付状态失败, 重试:', e);
    }
  }
  return { paid: false, status: 'timeout' };
}
