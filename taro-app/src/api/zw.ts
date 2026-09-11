/**
 * 智运·AI智能物流大模型 API · 对接后端 /api/logistics-ai/*
 * (物流接口管理模块升级更名·智能调度中枢)
 * 四引擎: P0 智能路由 → P1 轨迹智能 → P2 风控回执 → P3 分析进化
 * 管理端需携带 X-Role: admin 头
 */
import { request } from './request';
import { getSession } from '@/services/auth-service';

// ============================================================
// 字典
// ============================================================

/** 风险等级字典 */
export const RISK_LEVEL_NAME: Record<string, string> = {
  low: '低', medium: '中', high: '高', extreme: '极高',
};

/** 异常类型字典 */
export const ANOMALY_TYPE_NAME: Record<string, string> = {
  pickup_timeout: '揽收超时', stagnation: '运输停滞',
  deliver_failed: '派送失败', sign_timeout: '签收超时',
};

/** 理赔类型字典 */
export const CLAIM_TYPE_NAME: Record<string, string> = {
  damage: '破损', loss: '丢失', delay: '延误', stain: '污损',
};

/** 验货结果字典 */
export const INSPECT_RESULT_NAME: Record<string, string> = {
  pass: '通过', shortage: '少收', surplus: '多收',
};

/** 反馈裁决字典 */
export const FEEDBACK_VERDICT_NAME: Record<string, string> = {
  adopted: '采纳', corrected: '修正', rejected: '拒绝',
};

export const zwRiskLevelName = (r: string): string => RISK_LEVEL_NAME[r] || r;
export const zwAnomalyTypeName = (t: string): string => ANOMALY_TYPE_NAME[t] || t;
export const zwClaimTypeName = (t: string): string => CLAIM_TYPE_NAME[t] || t;
export const zwInspectResultName = (r: string): string => INSPECT_RESULT_NAME[r] || r;

// ============================================================
// VO 类型
// ============================================================

export interface CarrierScoreVO {
  carrier: string;
  carrierName: string;
  score: number;
  sample: number;
  signRate: number;
  avgSignHours: number;
  avgFee: number;
  coldStart: boolean;
  explain: string;
}

export interface RouteDecisionVO {
  decisionId: number;
  decision: {
    carrier: string; carrierName: string; serviceType: string;
    ruleReason: string; ruleScore: number;
    qualityScore: number; combinedScore: number;
  };
  candidates: RouteDecisionVO['decision'][];
  formula: string;
}

export interface CarrierHealthVO {
  carriers: {
    carrier: string; carrierName: string; sample: number;
    signRate: number | null; failRate: number | null;
    health: string; explain: string;
    switchSuggestion?: { title: string; body: string; disposition: string };
  }[];
}

export interface EtaVO {
  waybillNo: string;
  carrier: string;
  status: string;
  remainingHours: number;
  eta: string;
  basis?: string;
  avgHours?: number;
  elapsedHours?: number;
  explain?: string;
}

export interface AnomalyVO {
  waybillNo: string;
  carrier: string;
  type: string;
  severity: string;
  detail: string;
  action: string;
}

export interface RiskAssessVO {
  riskId: number;
  damageScore: number;
  lossScore: number;
  delayScore: number;
  riskScore: number;
  riskLevel: string;
  factors: string[];
  suggestions: string[];
  formula: string;
}

export interface ClaimVO {
  claimId: number;
  claimNo: string;
  waybillNo: string;
  claimTypeName: string;
  claimAmount: number;
  standard: string;
  slaDays: number;
  status: string;
}

export interface CostAnalysisVO {
  byCarrier: { carrier: string; count: number; totalFee: number; avgFee: number }[];
  byMonth: { month: string; totalFee: number }[];
  totalFee: number;
  suggestions: string[];
}

export interface VolumeForecastVO {
  horizon: number;
  historyMonths: number;
  recentAvg: number;
  fullAvg: number;
  trendSlope: number;
  rows: { step: number; predictedOrders: number }[];
  weightScheme: string;
}

export interface ZwStatusVO {
  module: string;
  orders: number;
  signRate: number;
  avgSignHours: number;
  evolution: { feedbacks: number; etaWeight: number };
}

// ============================================================
// 管理端请求头
// ============================================================

const adminHeaders = (): Record<string, string> => {
  const headers: Record<string, string> = { 'X-Role': 'admin' };
  const session = getSession();
  if (session?.accessToken) {
    headers.Authorization = `Bearer ${session.accessToken}`;
  }
  return headers;
};

const num = (v: any): number => Number(v ?? 0);
const str = (v: any): string => String(v ?? '');

export const ZwAPI = {
  // ================= P0: 智能路由 =================

  /** 多维路由决策(规则×0.6+质量×0.4) */
  async routeDecide(params: {
    orderType: string; weight: number; pieceCount: number;
    insuredValue?: number; sender?: Record<string, any>;
    receiver?: Record<string, any>;
  }): Promise<RouteDecisionVO> {
    const res = await request<any>({
      url: '/api/logistics-ai/route/decide', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      decisionId: num(d.decisionId), decision: d.decision,
      candidates: d.candidates || [], formula: str(d.formula),
    };
  },

  /** 物流商质量评分 */
  async carrierScores(): Promise<Record<string, CarrierScoreVO>> {
    const res = await request<any>({
      url: '/api/logistics-ai/route/carrier-scores', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 物流商健康度(降级→切换建议书) */
  async carrierHealth(): Promise<CarrierHealthVO> {
    const res = await request<any>({
      url: '/api/logistics-ai/route/health', headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P1: 轨迹智能 =================

  /** ETA 预测 */
  async eta(waybillNo: string): Promise<EtaVO> {
    const res = await request<any>({
      url: `/api/logistics-ai/track/eta/${waybillNo}`, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      waybillNo: str(d.waybillNo), carrier: str(d.carrier),
      status: str(d.status), remainingHours: num(d.remainingHours),
      eta: str(d.eta), basis: str(d.basis),
      avgHours: num(d.avgHours), elapsedHours: num(d.elapsedHours),
      explain: str(d.explain),
    };
  },

  /** 异常四检测器 */
  async anomalies(): Promise<AnomalyVO[]> {
    const res = await request<any>({
      url: '/api/logistics-ai/track/anomalies?limit=100', headers: adminHeaders(),
    });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** 延误预警总览 */
  async delays(): Promise<any> {
    const res = await request<any>({
      url: '/api/logistics-ai/track/delays', headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P2: 风控回执 =================

  /** 四防风控评分 */
  async riskAssess(params: {
    orderType?: string; weight: number; pieceCount: number;
    insuredValue?: number; receiverProvince?: string; urgent?: boolean;
  }): Promise<RiskAssessVO> {
    const res = await request<any>({
      url: '/api/logistics-ai/risk/assess', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      riskId: num(d.riskId), damageScore: num(d.damageScore),
      lossScore: num(d.lossScore), delayScore: num(d.delayScore),
      riskScore: num(d.riskScore), riskLevel: str(d.riskLevel),
      factors: d.factors || [], suggestions: d.suggestions || [],
      formula: str(d.formula),
    };
  },

  /** 团购验货回执 */
  async inspectReceipt(params: {
    orderId: string; expectedCount: number; actualCount: number;
    inspector: string; remark?: string;
  }): Promise<any> {
    const res = await request<any>({
      url: '/api/logistics-ai/risk/inspect-receipt', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 创建理赔工单 */
  async createClaim(params: {
    waybillNo: string; orderId: string; carrier: string;
    claimType: string; claimAmount: number; description?: string;
  }): Promise<ClaimVO> {
    const res = await request<any>({
      url: '/api/logistics-ai/risk/claims', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      claimId: num(d.claimId), claimNo: str(d.claimNo),
      waybillNo: str(d.waybillNo),
      claimTypeName: str(d.claimTypeName),
      claimAmount: num(d.claimAmount), standard: str(d.standard),
      slaDays: num(d.slaDays), status: str(d.status),
    };
  },

  /** 理赔工单列表 */
  async claims(): Promise<ClaimVO[]> {
    const res = await request<any>({
      url: '/api/logistics-ai/risk/claims', headers: adminHeaders(),
    });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  // ================= P3: 分析进化 =================

  /** 成本分析 */
  async costAnalysis(): Promise<CostAnalysisVO> {
    const res = await request<any>({
      url: '/api/logistics-ai/analysis/cost', headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      byCarrier: d.byCarrier || [], byMonth: d.byMonth || [],
      totalFee: num(d.totalFee), suggestions: d.suggestions || [],
    };
  },

  /** 运量预测 */
  async volumeForecast(horizon = 3): Promise<VolumeForecastVO> {
    const res = await request<any>({
      url: `/api/logistics-ai/analysis/volume-forecast?horizon=${horizon}`,
      headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      horizon: num(d.horizon), historyMonths: num(d.historyMonths),
      recentAvg: num(d.recentAvg), fullAvg: num(d.fullAvg),
      trendSlope: num(d.trendSlope), rows: d.rows || [],
      weightScheme: str(d.weightScheme),
    };
  },

  /** 反馈闭环(etaWeight ±0.1 安全阀) */
  async feedback(params: {
    targetType: string; verdict: string; note?: string;
  }): Promise<any> {
    const res = await request<any>({
      url: '/api/logistics-ai/evolution/feedback', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 反馈列表 */
  async feedbacks(limit = 20): Promise<any[]> {
    const res = await request<any>({
      url: `/api/logistics-ai/evolution/feedbacks?limit=${limit}`,
      headers: adminHeaders(),
    });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** 大模型总览 */
  async status(): Promise<ZwStatusVO> {
    const res = await request<any>({
      url: '/api/logistics-ai/status', headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      module: str(d.module), orders: num(d.orders),
      signRate: num(d.signRate), avgSignHours: num(d.avgSignHours),
      evolution: {
        feedbacks: num(d.evolution?.feedbacks),
        etaWeight: num(d.evolution?.etaWeight),
      },
    };
  },
};
