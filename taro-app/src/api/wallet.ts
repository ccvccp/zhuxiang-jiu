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
  totalAssets: number;       // 总资产 = 活期 + 定期 + 待结收益
  currentBalance: number;    // 活期余额(可消费/可提现)
  regularTotal: number;      // 定期总额
  pendingInterest: number;   // 待结收益
  totalDeposit: number;      // 累计充值
  totalWithdraw: number;     // 累计提现
  totalInterest: number;     // 累计收益
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
  interest: '收益',
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

/** 定期存单 */
export interface WalletDepositVO {
  depositNo: string;
  amount: number;
  period: number;            // 存期(月)
  annualRate: number;
  expectedInterest: number;
  rewardType: string;
  rewardValue: number;
  startDate: string;
  endDate: string;
  status: string;            // active/matured/settled/early_settled
  matured?: boolean;
}

/** 奖品 */
export interface WalletRewardVO {
  rewardNo: string;
  depositNo: string;
  rewardType: string;
  rewardValue: number;
  status: string;            // claimable/claimed/shipped/signed/expired
  createdAt: string;
}

/** 定期状态 → 中文名 */
export const DEPOSIT_STATUS_NAME: Record<string, string> = {
  active: '存入中',
  matured: '已到期',
  settled: '已结清',
  early_settled: '提前结清',
};

/** 奖品状态 → 中文名 */
export const REWARD_STATUS_NAME: Record<string, string> = {
  claimable: '可领取',
  claimed: '待发货',
  shipped: '配送中',
  signed: '已签收',
  expired: '已过期',
};

/** 定期档位(与后端 DEPOSIT_TIERS 对齐) */
export const DEPOSIT_TIERS: Array<{ period: number; min: number; rate: string }> = [
  { period: 3, min: 1000, rate: '3.0%' },
  { period: 6, min: 2000, rate: '5.0%' },
  { period: 12, min: 5000, rate: '3.0%' },
  { period: 24, min: 10000, rate: '3.5%' },
];

const mapDeposit = (d: any): WalletDepositVO => ({
  depositNo: d.depositNo || d.deposit_no || '',
  amount: Number(d.amount || 0),
  period: Number(d.period || 0),
  annualRate: Number(d.annualRate ?? d.annual_rate ?? 0),
  expectedInterest: Number(d.expectedInterest ?? d.expected_interest ?? 0),
  rewardType: d.rewardType || d.reward_type || '',
  rewardValue: Number(d.rewardValue ?? d.reward_value ?? 0),
  startDate: d.startDate || d.start_date || '',
  endDate: d.endDate || d.end_date || '',
  status: d.status || 'active',
  matured: Boolean(d.matured),
});

const mapReward = (r: any): WalletRewardVO => ({
  rewardNo: r.rewardNo || r.reward_no || '',
  depositNo: r.depositNo || r.deposit_no || '',
  rewardType: r.rewardType || r.reward_type || '',
  rewardValue: Number(r.rewardValue ?? r.reward_value ?? 0),
  status: r.status || 'claimable',
  createdAt: r.createdAt || r.created_at || '',
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

  /** 当日收益预估 */
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

  /** 活期转定期(存期 3/6/12/24 月, 各档最低起存) */
  async transferRegular(amount: number, period: number): Promise<any> {
    return await request<any>({
      url: '/api/wallet/transfer-regular',
      method: 'POST',
      data: { amount, period },
    });
  },

  /** 我的定期存单列表 */
  async deposits(status?: string, limit = 50): Promise<WalletDepositVO[]> {
    const qs = status ? `?status=${status}&limit=${limit}` : `?limit=${limit}`;
    const res = await request<any>({ url: `/api/wallet/deposits${qs}` });
    return (res.deposits || []).map(mapDeposit);
  },

  /** 定期到期取出(本金+收益入账, 奖品转可领取) */
  async settleDeposit(depositNo: string): Promise<any> {
    return await request<any>({
      url: `/api/wallet/deposit/${depositNo}/settle`,
      method: 'POST',
      data: {},
    });
  },

  /** 定期提前取出(1% 手续费, 损失收益与奖品) */
  async earlySettleDeposit(depositNo: string): Promise<any> {
    return await request<any>({
      url: `/api/wallet/deposit/${depositNo}/early-settle`,
      method: 'POST',
      data: {},
    });
  },

  /** 我的奖品列表 */
  async rewards(status?: string, limit = 50): Promise<WalletRewardVO[]> {
    const qs = status ? `?status=${status}&limit=${limit}` : `?limit=${limit}`;
    const res = await request<any>({ url: `/api/wallet/rewards${qs}` });
    return (res.rewards || []).map(mapReward);
  },

  /** 领取奖品(claimable → claimed 等待发货; addressId=0 稍后填写) */
  async claimReward(rewardNo: string, addressId = 0): Promise<any> {
    return await request<any>({
      url: `/api/wallet/reward/${rewardNo}/claim`,
      method: 'POST',
      data: { addressId },
    });
  },

  /** 奖品签收(shipped → signed) */
  async signReward(rewardNo: string): Promise<any> {
    return await request<any>({
      url: `/api/wallet/reward/${rewardNo}/sign`,
      method: 'POST',
      data: {},
    });
  },
};
