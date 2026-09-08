/**
 * 信用先享后付 API · 对接后端 /api/credit/*
 * 信用分/额度查询 → 先享后付下单(AI智能授信) → 还款(额度恢复+逾期惩罚)
 */
import { request } from './request';
import { getMemberId } from '@/services/auth-service';

/** 先享后付订单状态 */
export const PAYLATER_STATUS_NAME: Record<string, string> = {
  review: '审批中',
  active: '待还款',
  repaid: '已还清',
  rejected: '已拒绝',
  overdue: '已逾期',
};

/** 信用流水类型 */
export const LOG_TYPE_NAME: Record<string, string> = {
  earn: '信用获取',
  deduct: '信用扣减',
  adjust: '人工调整',
  upgrade: '信用升级',
  downgrade: '信用降级',
  blacklist: '加入黑名单',
  restore: '信用恢复',
  paylater: '先享后付',
  paylater_repay: '先享后付还款',
};

/** 等级门槛(L3 起可用先享后付) */
export const LEVEL_GATE = 3;

/** 等级 → 所需最低分(与后端 level_from_score 对齐) */
export const LEVEL_MIN_SCORE: Record<string, number> = {
  L1: 0, L2: 400, L3: 550, L4: 700, L5: 800,
};

/** 等级 → 免息期(天) */
export const LEVEL_FREE_DAYS: Record<string, number> = {
  L1: 0, L2: 0, L3: 15, L4: 30, L5: 45,
};

export const paylaterStatusName = (s: string): string => PAYLATER_STATUS_NAME[s] || s;
export const logTypeName = (t: string): string => LOG_TYPE_NAME[t] || t;

export interface CreditScoreVO {
  userId: number;
  bambooScore: number;
  creditLevel: string;
  status: string;
  paylaterQuota: number;
  paylaterUsed: number;
}

export interface PaylaterQuotaVO {
  userId: number;
  creditLevel: string;
  bambooScore: number;
  totalQuota: number;
  usedQuota: number;
  availableQuota: number;
  interestFreeDays: number;
  status: string;
}

export interface PaylaterOrderVO {
  id: number;
  orderNo: string;
  accountType: string;
  source: string;
  amount: number;
  status: string;
  riskLevel: string;
  riskFlags: string[];
  createdAt: string;
  dueDate: string;
  approvedAt?: string;
  reviewedBy?: string;
  repaidAt?: string;
  overdueDays?: number;
  overdueFees?: number;
  penaltyFees?: number;
  repayTotal?: number;
}

export interface CreditLogVO {
  id: number;
  type: string;
  scoreBefore: number;
  scoreAfter: number;
  delta: number;
  levelBefore: string;
  levelAfter: string;
  reason: string;
  operator: string;
  createdAt: string;
}

function toOrder(o: any): PaylaterOrderVO {
  return {
    id: o.id ?? o.orderId ?? 0,
    orderNo: o.orderNo || o.order_no || '',
    accountType: o.accountType || o.account_type || 'member',
    source: o.source || 'order',
    amount: o.amount ?? 0,
    status: o.status || '',
    riskLevel: o.riskLevel || o.risk_level || '',
    riskFlags: o.riskFlags || o.risk_flags || [],
    createdAt: o.createdAt || o.created_at || '',
    dueDate: o.dueDate || o.due_date || '',
    approvedAt: o.approvedAt || o.approved_at,
    reviewedBy: o.reviewedBy || o.reviewed_by,
    repaidAt: o.repaidAt || o.repaid_at,
    overdueDays: o.overdueDays ?? o.overdue_days,
    overdueFees: o.overdueFees ?? o.overdue_fees,
    penaltyFees: o.penaltyFees ?? o.penalty_fees,
    repayTotal: o.repayTotal ?? o.repay_total,
  };
}

function toLog(l: any): CreditLogVO {
  return {
    id: l.id ?? 0,
    type: l.type || '',
    scoreBefore: l.scoreBefore ?? l.score_before ?? 0,
    scoreAfter: l.scoreAfter ?? l.score_after ?? 0,
    delta: l.delta ?? 0,
    levelBefore: l.levelBefore || l.level_before || '',
    levelAfter: l.levelAfter || l.level_after || '',
    reason: l.reason || '',
    operator: l.operator || '',
    createdAt: l.createdAt || l.created_at || '',
  };
}

export const CreditAPI = {
  /** 查询信用分账户(不存在则按会员创建) */
  async score(): Promise<CreditScoreVO> {
    const uid = encodeURIComponent(getMemberId() || '0');
    const res = await request<any>({ url: `/api/credit/score/${uid}` });
    const d = res.data || res;
    return {
      userId: Number(d.userId ?? d.user_id ?? 0),
      bambooScore: d.bambooScore ?? d.bamboo_score ?? 0,
      creditLevel: d.creditLevel || d.credit_level || 'L1',
      status: d.status || 'normal',
      paylaterQuota: d.paylaterQuota ?? d.paylater_quota ?? 0,
      paylaterUsed: d.paylaterUsed ?? d.paylater_used ?? 0,
    };
  },

  /** 查询先享后付额度(总额度/已用/可用/免息期) */
  async quota(): Promise<PaylaterQuotaVO> {
    const uid = encodeURIComponent(getMemberId() || '0');
    const res = await request<any>({ url: `/api/credit/quota/${uid}` });
    const d = res.data || res;
    return {
      userId: Number(d.userId ?? 0),
      creditLevel: d.creditLevel || d.credit_level || 'L1',
      bambooScore: d.bambooScore ?? d.bamboo_score ?? 0,
      totalQuota: d.totalQuota ?? d.total_quota ?? 0,
      usedQuota: d.usedQuota ?? d.used_quota ?? 0,
      availableQuota: d.availableQuota ?? d.available_quota ?? 0,
      interestFreeDays: d.interestFreeDays ?? d.interest_free_days ?? 0,
      status: d.status || 'normal',
    };
  },

  /** 先享后付订单列表 */
  async paylaterOrders(status?: string, limit = 100): Promise<PaylaterOrderVO[]> {
    const uid = encodeURIComponent(getMemberId() || '0');
    const qs = status ? `?status=${status}&limit=${limit}` : `?limit=${limit}`;
    const res = await request<any>({ url: `/api/credit/paylater/orders/${uid}${qs}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toOrder);
  },

  /** 创建先享后付订单(AI 智能授信: 自动通过/转人工/自动拒绝) */
  async createPaylaterOrder(params: {
    amount: number;
    orderNo?: string;
  }): Promise<PaylaterOrderVO> {
    const res = await request<any>({
      url: '/api/credit/paylater/order',
      method: 'POST',
      data: {
        userId: Number(getMemberId()),
        amount: params.amount,
        accountType: 'member',
        orderNo: params.orderNo || '',
        source: 'order',
      },
    });
    return toOrder(res.data || res);
  },

  /** 先享后付还款(恢复额度+逾期费用+信用分惩罚) */
  async repay(orderId: number, repayChannel = 'wallet'): Promise<{
    orderId: number;
    amount: number;
    overdueDays: number;
    overdueFees: number;
    penaltyFees: number;
    repayTotal: number;
    creditPenaltyApplied: boolean;
  }> {
    const res = await request<any>({
      url: '/api/credit/paylater/repay',
      method: 'POST',
      data: { orderId, repayChannel },
    });
    const d = res.data || res;
    return {
      orderId: d.orderId ?? d.order_id ?? 0,
      amount: d.amount ?? 0,
      overdueDays: d.overdueDays ?? d.overdue_days ?? 0,
      overdueFees: d.overdueFees ?? d.overdue_fees ?? 0,
      penaltyFees: d.penaltyFees ?? d.penalty_fees ?? 0,
      repayTotal: d.repayTotal ?? d.repay_total ?? 0,
      creditPenaltyApplied: Boolean(d.creditPenaltyApplied ?? d.credit_penalty_applied),
    };
  },

  /** 信用流水(最近变动) */
  async logs(limit = 30): Promise<CreditLogVO[]> {
    const uid = encodeURIComponent(getMemberId() || '0');
    const res = await request<any>({ url: `/api/credit/list?user_id=${uid}&limit=${limit}` });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toLog);
  },
};
