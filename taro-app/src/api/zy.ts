/**
 * 智启元·AI智能财务大模型 API · 对接后端 /api/zy/*(财务管理模块升级更名)
 * 四层架构: 数据织物 → 问答分析(P0) → 预测沙盘(P1) → 税务优化(P2) → 自主进化(P3)
 * 管理端需携带 X-Role: admin 头(财务敏感域权限管控)
 */
import { request } from './request';
import { getSession } from '@/services/auth-service';

// ============================================================
// 字典
// ============================================================

/** 问答五域字典 */
export const QA_DOMAIN_NAME: Record<string, string> = {
  revenue: '收入', cost: '成本', tax: '税负',
  cash: '现金流', anomaly: '异常',
};

/** 反馈裁决字典 */
export const VERDICT_NAME: Record<string, string> = {
  adopted: '采纳', corrected: '修正', rejected: '拒绝',
};

/** 反馈目标类型字典 */
export const FEEDBACK_TARGET_NAME: Record<string, string> = {
  forecast: '预测', analysis: '分析',
  tax_suggestion: '税务建议', anomaly: '异常', cash_schedule: '资金调度',
};

/** 税务结构字典 */
export const TAX_STRUCTURE_NAME: Record<string, string> = {
  standard: '一般销售', discount: '折扣销售',
  bundle: '组合销售', cross_border: '跨境零售',
};

/** 风险等级字典 */
export const RISK_LEVEL_NAME: Record<string, string> = {
  low: '低', attention: '关注', medium: '中',
  high: '高', critical: '严重',
};

/** 异常类型字典 */
export const ANOMALY_TYPE_NAME: Record<string, string> = {
  spike: '金额突增', drop: '金额骤降', surge: '频率激增',
};

export const qaDomainName = (d: string): string => QA_DOMAIN_NAME[d] || d;
export const verdictName = (v: string): string => VERDICT_NAME[v] || v;
export const feedbackTargetName = (t: string): string => FEEDBACK_TARGET_NAME[t] || t;
export const taxStructureName = (s: string): string => TAX_STRUCTURE_NAME[s] || s;
export const riskLevelName = (r: string): string => RISK_LEVEL_NAME[r] || r;
export const anomalyTypeName = (t: string): string => ANOMALY_TYPE_NAME[t] || t;

// ============================================================
// VO 类型
// ============================================================

/** 月度财务时序行(数据织物口径) */
export interface SeriesRowVO {
  month: string;
  salesAmount: number;
  refundAmount: number;
  netAmount: number;
  costAmount: number;
  taxAmount: number;
  netProfit: number;
  orderCount: number;
  quantity: number;
}

/** 问答响应(意图路由→确定性查询) */
export interface QaReplyVO {
  domain: string;
  intent: string;
  answer: string;
  reasoning: string;
  dataSnapshot: Record<string, any>;
}

/** 杜邦分析 */
export interface DupontVO {
  period: string;
  roe: number;
  factors: { netMargin: number; assetTurnover: number; equityMultiplier: number };
  basis: Record<string, number>;
  interpretation: string;
}

/** 净利环比归因(四因素连环替代) */
export interface AttributionVO {
  period: string;
  prevPeriod: string;
  comparable: boolean;
  delta: number;
  factors: { factor: string; effect: number; explain: string }[];
  note: string;
}

/** 财务健康度(五维 Sigmoid) */
export interface HealthVO {
  month: string;
  totalScore: number;
  grade: string;
  dimensions: { dim: string; raw: number; score: number; explain: string }[];
  prevMonth: string;
}

/** 滚动预测 */
export interface ForecastVO {
  horizon: number;
  rows: Record<string, number>[];
  basis: {
    recentAvg: Record<string, number>;
    fullAvg: Record<string, number>;
    weightScheme: string;
    trendApplied: boolean;
    historyMonths: number;
  };
  determinismNote: string;
}

/** What-if 情景沙盘 */
export interface SandboxVO {
  baseline: Record<string, number | string>;
  assumptions: { priceDelta: number; volumeDelta: number; costDelta: number };
  scenario: Record<string, number>;
  impacts: Record<string, number>;
  mitigations: string[];
  note: string;
}

/** 收入驱动因素 */
export interface DriversVO {
  months: number;
  drivers: {
    factor: string; correlation: number;
    effectiveCorrelation: number;
    direction: string; sensitivity: number;
  }[];
  note: string;
}

/** 税负模拟 */
export interface TaxSimVO {
  amount: number;
  quantity: number;
  structures: {
    structure: string; structureName: string;
    vat: number; consumptionTax: number; incomeTax: number;
    total: number; effectiveRate: number; note: string;
  }[];
  recommendation: {
    best: string; bestName: string; bestTotal: number;
    savingVsWorst: number; note: string;
  };
}

/** 政策匹配 */
export interface TaxPoliciesVO {
  policies: {
    policyId: string; title: string; category: string;
    tags: string[]; content: string; condition: string;
    savingFormula: string; effectiveFrom: string; effectiveTo: string;
    matchedTags: string[]; eligible: boolean; suggestion: string;
  }[];
  matched: number;
  note: string;
}

/** 税务风险热力图 */
export interface TaxHeatmapVO {
  month: string;
  risks: {
    risk: string; signal: string; value: number;
    severity: string; severityName: string; detail: string;
  }[];
  overall: { level: string; levelName: string; topRisk: string };
  note: string;
}

/** 反馈记录 */
export interface FeedbackVO {
  feedbackId: number;
  targetType: string;
  verdict: string;
  note: string;
  correction: Record<string, any>;
  createdAt: string;
  trendDelta?: number;
  trendWeightAfter?: number;
}

/** 异常自发现 */
export interface AnomalyVO {
  type: string;
  month: string;
  value: number;
  baseline: number;
  detail: string;
  severity: string;
  disposition: string;
}

/** 资金智能调度 */
export interface CashScheduleVO {
  days: number;
  dailyInflow: number;
  dailyOutflow: number;
  firstGapDay: number | null;
  maxGap: number;
  rows: { day: number; date: string; netFlow: number; cumulative: number; gap: number }[];
  suggestions: string[];
  note: string;
}

/** 决策备忘录(投资 DCF) */
export interface MemoVO {
  memoId: number;
  type: string;
  assumptions: Record<string, number>;
  npv: number;
  irrApprox: number | null;
  paybackYears: number | null;
  sensitivities: { discountRate: number; npv: number }[];
  conclusion: string;
  assumptionNote: string;
  createdAt: string;
  items?: any[];
  rationale?: string;
  impact?: string;
}

/** 进化日志 */
export interface EvoLogVO {
  logId: number;
  engine: string;
  action: string;
  detail: Record<string, any>;
  createdAt: string;
}

/** 智启元总览 */
export interface ZyStatusVO {
  feedbacks: { total: number; adopted: number; trendWeight: number };
  anomalies: AnomalyVO[];
  note: string;
}

// ============================================================
// 管理端请求头(X-Role: admin + 登录令牌)
// ============================================================

const adminHeaders = (): Record<string, string> => {
  const headers: Record<string, string> = { 'X-Role': 'admin' };
  const session = getSession();
  if (session?.accessToken) {
    headers.Authorization = `Bearer ${session.accessToken}`;
  }
  return headers;
};

// 数值化工具
const num = (v: any): number => Number(v ?? 0);
const str = (v: any): string => String(v ?? '');

function toSeriesRow(r: any): SeriesRowVO {
  return {
    month: str(r.month),
    salesAmount: num(r.salesAmount),
    refundAmount: num(r.refundAmount),
    netAmount: num(r.netAmount),
    costAmount: num(r.costAmount),
    taxAmount: num(r.taxAmount),
    netProfit: num(r.netProfit),
    orderCount: num(r.orderCount),
    quantity: num(r.quantity),
  };
}

function toQaReply(d: any): QaReplyVO {
  return {
    domain: str(d.domain),
    intent: str(d.intent),
    answer: str(d.answer),
    reasoning: str(d.reasoning),
    dataSnapshot: d.dataSnapshot || {},
  };
}

function toFeedback(f: any): FeedbackVO {
  return {
    feedbackId: num(f.feedbackId),
    targetType: str(f.targetType),
    verdict: str(f.verdict),
    note: str(f.note),
    correction: f.correction || {},
    createdAt: str(f.createdAt),
    trendDelta: f.trendDelta != null ? num(f.trendDelta) : undefined,
    trendWeightAfter: f.trendWeightAfter != null ? num(f.trendWeightAfter) : undefined,
  };
}

function toAnomaly(a: any): AnomalyVO {
  return {
    type: str(a.type),
    month: str(a.month),
    value: num(a.value),
    baseline: num(a.baseline),
    detail: str(a.detail),
    severity: str(a.severity),
    disposition: str(a.disposition),
  };
}

function toMemo(m: any): MemoVO {
  return {
    memoId: num(m.memoId),
    type: str(m.type),
    assumptions: m.assumptions || {},
    npv: num(m.npv),
    irrApprox: m.irrApprox != null ? num(m.irrApprox) : null,
    paybackYears: m.paybackYears != null ? num(m.paybackYears) : null,
    sensitivities: (m.sensitivities || []).map((s: any) => ({
      discountRate: num(s.discountRate), npv: num(s.npv),
    })),
    conclusion: str(m.conclusion),
    assumptionNote: str(m.assumptionNote),
    createdAt: str(m.createdAt),
    items: m.items || [],
    rationale: str(m.rationale),
    impact: str(m.impact),
  };
}

export const ZyAPI = {
  // ================= P0: 问答与分析 =================

  /** 自然语言问答(意图路由→确定性查询→数字100%查询层) */
  async qa(question: string): Promise<QaReplyVO> {
    const res = await request<any>({
      url: '/api/zy/qa', method: 'POST',
      data: { question }, headers: adminHeaders(),
    });
    return toQaReply(res.data || res);
  },

  /** 月度财务时序(图表数据源) */
  async series(months = 12): Promise<SeriesRowVO[]> {
    const res = await request<any>({
      url: `/api/zy/series?months=${months}`, headers: adminHeaders(),
    });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toSeriesRow);
  },

  /** 杜邦分析(ROE 三因子分解) */
  async dupont(period?: string): Promise<DupontVO> {
    const q = period ? `?period=${period}` : '';
    const res = await request<any>({
      url: `/api/zy/dupont${q}`, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      period: str(d.period),
      roe: num(d.roe),
      factors: {
        netMargin: num(d.factors?.netMargin),
        assetTurnover: num(d.factors?.assetTurnover),
        equityMultiplier: num(d.factors?.equityMultiplier),
      },
      basis: d.basis || {},
      interpretation: str(d.interpretation),
    };
  },

  /** 净利环比归因(量/价/本/税四因素) */
  async attribution(): Promise<AttributionVO> {
    const res = await request<any>({ url: '/api/zy/attribution', headers: adminHeaders() });
    const d = res.data || res;
    return {
      period: str(d.period),
      prevPeriod: str(d.prevPeriod),
      comparable: Boolean(d.comparable),
      delta: num(d.delta),
      factors: (d.factors || []).map((f: any) => ({
        factor: str(f.factor), effect: num(f.effect), explain: str(f.explain),
      })),
      note: str(d.note),
    };
  },

  /** 财务健康度五维评分 */
  async health(): Promise<HealthVO> {
    const res = await request<any>({ url: '/api/zy/health', headers: adminHeaders() });
    const d = res.data || res;
    return {
      month: str(d.month),
      totalScore: num(d.totalScore),
      grade: str(d.grade),
      dimensions: (d.dimensions || []).map((x: any) => ({
        dim: str(x.dim), raw: num(x.raw), score: num(x.score), explain: str(x.explain),
      })),
      prevMonth: str(d.prevMonth),
    };
  },

  // ================= P1: 预测与沙盘 =================

  /** 滚动预测(加权移动平均+趋势外推) */
  async forecast(horizon = 6): Promise<ForecastVO> {
    const res = await request<any>({
      url: `/api/zy/forecast?horizon=${horizon}`, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      horizon: num(d.horizon),
      rows: d.rows || [],
      basis: {
        recentAvg: d.basis?.recentAvg || {},
        fullAvg: d.basis?.fullAvg || {},
        weightScheme: str(d.basis?.weightScheme),
        trendApplied: Boolean(d.basis?.trendApplied),
        historyMonths: num(d.basis?.historyMonths),
      },
      determinismNote: str(d.determinismNote),
    };
  },

  /** What-if 情景沙盘(三维假设推演) */
  async sandbox(params: {
    priceDelta: number; volumeDelta: number; costDelta: number;
  }): Promise<SandboxVO> {
    const res = await request<any>({
      url: '/api/zy/sandbox', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      baseline: d.baseline || {},
      assumptions: {
        priceDelta: num(d.assumptions?.priceDelta),
        volumeDelta: num(d.assumptions?.volumeDelta),
        costDelta: num(d.assumptions?.costDelta),
      },
      scenario: d.scenario || {},
      impacts: d.impacts || {},
      mitigations: d.mitigations || [],
      note: str(d.note),
    };
  },

  /** 收入驱动因素(相关性排序) */
  async drivers(): Promise<DriversVO> {
    const res = await request<any>({ url: '/api/zy/drivers', headers: adminHeaders() });
    const d = res.data || res;
    return {
      months: num(d.months),
      drivers: (d.drivers || []).map((x: any) => ({
        factor: str(x.factor),
        correlation: num(x.correlation),
        effectiveCorrelation: num(x.effectiveCorrelation),
        direction: str(x.direction),
        sensitivity: num(x.sensitivity),
      })),
      note: str(d.note),
    };
  },

  // ================= P2: 税务优化 =================

  /** 交易级税负模拟(四结构对比) */
  async taxSimulate(params: {
    amount: number; quantity: number; structures?: string[];
  }): Promise<TaxSimVO> {
    const res = await request<any>({
      url: '/api/zy/tax/simulate', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      amount: num(d.amount),
      quantity: num(d.quantity),
      structures: (d.structures || []).map((s: any) => ({
        structure: str(s.structure),
        structureName: str(s.structureName),
        vat: num(s.vat),
        consumptionTax: num(s.consumptionTax),
        incomeTax: num(s.incomeTax),
        total: num(s.total),
        effectiveRate: num(s.effectiveRate),
        note: str(s.note),
      })),
      recommendation: {
        best: str(d.recommendation?.best),
        bestName: str(d.recommendation?.bestName),
        bestTotal: num(d.recommendation?.bestTotal),
        savingVsWorst: num(d.recommendation?.savingVsWorst),
        note: str(d.recommendation?.note),
      },
    };
  },

  /** 政策库+标签匹配 */
  async taxPolicies(tags?: string[]): Promise<TaxPoliciesVO> {
    const q = tags?.length ? `?tags=${encodeURIComponent(tags.join(','))}` : '';
    const res = await request<any>({
      url: `/api/zy/tax/policies${q}`, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      policies: (d.policies || []).map((p: any) => ({
        policyId: str(p.policyId),
        title: str(p.title),
        category: str(p.category),
        tags: p.tags || [],
        content: str(p.content),
        condition: str(p.condition),
        savingFormula: str(p.savingFormula),
        effectiveFrom: str(p.effectiveFrom),
        effectiveTo: str(p.effectiveTo),
        matchedTags: p.matchedTags || [],
        eligible: Boolean(p.eligible),
        suggestion: str(p.suggestion),
      })),
      matched: num(d.matched),
      note: str(d.note),
    };
  },

  /** 税务风险热力图 */
  async taxHeatmap(): Promise<TaxHeatmapVO> {
    const res = await request<any>({
      url: '/api/zy/tax/risk-heatmap', headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      month: str(d.month),
      risks: (d.risks || []).map((r: any) => ({
        risk: str(r.risk),
        signal: str(r.signal),
        value: num(r.value),
        severity: str(r.severity),
        severityName: str(r.severityName),
        detail: str(r.detail),
      })),
      overall: {
        level: str(d.overall?.level),
        levelName: str(d.overall?.levelName),
        topRisk: str(d.overall?.topRisk),
      },
      note: str(d.note),
    };
  },

  // ================= P3: 自主进化与决策支持 =================

  /** 反馈闭环(采纳/修正/拒绝→参数确定性调优) */
  async feedback(params: {
    targetType: string; verdict: string;
    note?: string; correction?: Record<string, any>;
  }): Promise<FeedbackVO> {
    const res = await request<any>({
      url: '/api/zy/evolution/feedback', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return toFeedback(res.data || res);
  },

  /** 反馈记录列表 */
  async feedbacks(limit = 50): Promise<FeedbackVO[]> {
    const res = await request<any>({
      url: `/api/zy/evolution/feedbacks?limit=${limit}`, headers: adminHeaders(),
    });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toFeedback);
  },

  /** 预测参数现状(trendWeight 进化态) */
  async params(): Promise<{ trendWeight: number; updatedAt: string }> {
    const res = await request<any>({
      url: '/api/zy/evolution/params', headers: adminHeaders(),
    });
    const d = res.data || res;
    return { trendWeight: num(d.trendWeight), updatedAt: str(d.updatedAt) };
  },

  /** 异常自发现(三检测器) */
  async anomalies(): Promise<AnomalyVO[]> {
    const res = await request<any>({
      url: '/api/zy/evolution/anomalies', headers: adminHeaders(),
    });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toAnomaly);
  },

  /** 资金智能调度(逐日缺口推演) */
  async cashSchedule(days = 90): Promise<CashScheduleVO> {
    const res = await request<any>({
      url: `/api/zy/evolution/cash-schedule?days=${days}`, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      days: num(d.days),
      dailyInflow: num(d.dailyInflow),
      dailyOutflow: num(d.dailyOutflow),
      firstGapDay: d.firstGapDay != null ? num(d.firstGapDay) : null,
      maxGap: num(d.maxGap),
      rows: (d.rows || []).map((r: any) => ({
        day: num(r.day), date: str(r.date),
        netFlow: num(r.netFlow), cumulative: num(r.cumulative),
        gap: num(r.gap),
      })),
      suggestions: d.suggestions || [],
      note: str(d.note),
    };
  },

  /** 决策备忘录(投资 DCF/预算调整) */
  async decisionMemo(params: {
    type: string; params: Record<string, any>;
  }): Promise<MemoVO> {
    const res = await request<any>({
      url: '/api/zy/evolution/decision-memo', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return toMemo(res.data || res);
  },

  /** 备忘录列表 */
  async memos(limit = 20): Promise<MemoVO[]> {
    const res = await request<any>({
      url: `/api/zy/evolution/memos?limit=${limit}`, headers: adminHeaders(),
    });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toMemo);
  },

  /** 进化日志(全量留痕) */
  async logs(engine?: string, limit = 50): Promise<EvoLogVO[]> {
    const q = (engine ? `engine=${engine}&` : '') + `limit=${limit}`;
    const res = await request<any>({
      url: `/api/zy/evolution/logs?${q}`, headers: adminHeaders(),
    });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map((l: any) => ({
      logId: num(l.logId),
      engine: str(l.engine),
      action: str(l.action),
      detail: l.detail || {},
      createdAt: str(l.createdAt),
    }));
  },

  /** 智启元总览 */
  async status(): Promise<ZyStatusVO> {
    const res = await request<any>({ url: '/api/zy/status', headers: adminHeaders() });
    const d = res.data || res;
    return {
      feedbacks: {
        total: num(d.feedbacks?.total),
        adopted: num(d.feedbacks?.adopted),
        trendWeight: num(d.feedbacks?.trendWeight),
      },
      anomalies: (d.anomalies || []).map(toAnomaly),
      note: str(d.note),
    };
  },
};
