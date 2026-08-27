/**
 * 钱包 API · 对接后端 /api/wallet/*
 * 开通条件: 会员等级 ≥ L2(成长值 ≥ 500)
 */
import { request } from './request';

// 钱包首页信息
export interface WalletInfoVO {
  userId: string;
  status: string;            // active/frozen
  statusName: string;        // 正常/冻结
  totalAssets: number;       // 总资产 = 活期 + 定期 + 待结补贴
  currentBalance: number;    // 活期余额(可消费/可提现)
  regularTotal: number;      // 定期总额
  pendingInterest: number;   // 待结补贴
  totalDeposit: number;      // 累计充值
  totalWithdraw: number;     // 累计提现
  totalInterest: number;     // 累计补贴
  totalReward: number;       // 累计奖励
  totalRebate: number;       // 累计返利
  claimableRewardCount: number; // 可领奖品数
}

// 交易流水
export interface WalletTxVO {
  txNo: string;
  type: string;              // deposit/withdraw/consume/refund/interest/rebate/transfer_regular
  direction: string;         // IN / OUT
  amount: number;
  balanceAfter: number;
  payChannel?: string;
  status: string;            // success/processing/failed
  description: string;
  createdAt: string;
}

// 流水类型 → 中文名
export const TX_TYPE_NAME: Record<string, string> = {
  deposit: '充值',
  withdraw: '提现',
  consume: '消费',
  refund: '退款',
  interest: '补贴',
  rebate: '返利',
  transfer_regular: '定期转入',
};

const mapTx = (t: any): WalletTxVO => ({
  txNo: String(t.txNo || t.tx_no || ''),
  type: t.type || '',
  direction: t.direction || '',
  amount: Number(t.amount || 0),
  balanceAfter: Number(t.balanceAfter ?? 0),
  payChannel: t.payChannel || '',
  status: t.status || 'success',
  description: t.description || '',
  createdAt: t.createdAt || t.created_at || '',
});

export const WalletAPI = {
  /** 开通钱包(等级 ≥ L2) */
  async open(): Promise<void> {
    await request<any>({ url: '/api/wallet/open', method: 'POST', data: {} });
  },

  /** 钱包信息(未开通抛 404) */
  async info(): Promise<WalletInfoVO> {
    const res = await request<any>({ url: '/api/wallet/info' });
    return {
      userId: String(res.userId || ''),
      status: res.status || 'active',
      statusName: res.statusName || '正常',
      totalAssets: res.totalAssets || 0,
      currentBalance: res.currentBalance || 0,
      regularTotal: res.regularTotal || 0,
      pendingInterest: res.pendingInterest || 0,
      totalDeposit: res.totalDeposit || 0,
      totalWithdraw: res.totalWithdraw || 0,
      totalInterest: res.totalInterest || 0,
      totalReward: res.totalReward || 0,
      totalRebate: res.totalRebate || 0,
      claimableRewardCount: res.claimableRewardCount || 0,
    };
  },

  /** 充值(最低 ¥100, 进入活期) */
  async deposit(amount: number, payChannel = 'wechat'): Promise<{ balanceAfter: number }> {
    const res = await request<any>({
      url: '/api/wallet/deposit',
      method: 'POST',
      data: { amount, payChannel },
    });
    return { balanceAfter: res.balanceAfter || 0 };
  },

  /** 提现申请(<5000 自动通过, ≥5000 需审核) */
  async withdraw(amount: number, payChannel = 'bank', bankAccount = ''): Promise<{ withdrawNo: string; status: string; statusName: string }> {
    const res = await request<any>({
      url: '/api/wallet/withdraw',
      method: 'POST',
      data: { amount, payChannel, bankAccount },
    });
    return {
      withdrawNo: res.withdrawNo || '',
      status: res.status || '',
      statusName: res.statusName || '',
    };
  },

  /** 交易明细(可按类型筛选) */
  async transactions(type?: string, limit = 50): Promise<WalletTxVO[]> {
    const qs = type ? `?type=${type}&limit=${limit}` : `?limit=${limit}`;
    const res = await request<any>({ url: `/api/wallet/transactions${qs}` });
    return (res.transactions || []).map(mapTx);
  },

  /** 当日补贴预估 */
  async dailyInterest(): Promise<{ daily: number; monthly: number; yearly: number }> {
    const res = await request<any>({ url: '/api/wallet/interest/daily' });
    return {
      daily: res.dailyInterest ?? res.daily ?? 0,
      monthly: res.monthlyEstimate ?? res.monthly ?? 0,
      yearly: res.yearlyEstimate ?? res.yearly ?? 0,
    };
  },

  /** 收益规则(活期/定期档位) */
  async interestRules(): Promise<any> {
    return await request<any>({ url: '/api/wallet/interest/rules' });
  },
};
