/**
 * 智法·AI智能法务大模型 API · 对接后端 /api/legal/*(24号合规模块升级更名)
 * 产-销-法一体化智能合规中枢: 生产合规(P0) → 供应链金融(P1)
 * → 数据资产(P2) → 电商深化+进化闭环(P3)
 * 管理端需携带 X-Role: admin 头(法务敏感域权限管控)
 */
import { request } from './request';
import { getSession } from '@/services/auth-service';

// ============================================================
// 字典
// ============================================================

/** 工艺校验判定字典 */
export const VERDICT_NAME: Record<string, string> = {
  pass: '合规', deviation: '偏离', violation: '违规',
};

/** 反馈裁决字典 */
export const FEEDBACK_VERDICT_NAME: Record<string, string> = {
  adopted: '采纳', corrected: '修正', rejected: '拒绝',
};

/** 反馈目标字典(三通道) */
export const FEEDBACK_TARGET_NAME: Record<string, string> = {
  process_check: '工艺校验', credit: '信用评估',
  passport_verify: '护照验证', price_audit: '价格审计',
  presale_guard: '预售护栏',
};

/** 数据等级字典 */
export const DATA_LEVEL_NAME: Record<string, string> = {
  L4: '核心(个人信息)', L3: '重要(商业秘密)',
  L2: '业务(受限流通)', L1: '一般(可流通)',
};

/** 跨境区域字典 */
export const REGION_NAME: Record<string, string> = {
  EU: '欧盟(GDPR)', US: '美国加州(CCPA)', OTHER: '其他国家',
};

/** 风险等级字典 */
export const RISK_LEVEL_NAME: Record<string, string> = {
  low: '低', medium: '中', high: '高',
};

export const zfVerdictName = (v: string): string => VERDICT_NAME[v] || v;
export const zfFeedbackVerdictName = (v: string): string => FEEDBACK_VERDICT_NAME[v] || v;
export const zfFeedbackTargetName = (t: string): string => FEEDBACK_TARGET_NAME[t] || t;
export const zfDataLevelName = (l: string): string => DATA_LEVEL_NAME[l] || l;
export const zfRegionName = (r: string): string => REGION_NAME[r] || r;
export const zfRiskLevelName = (r: string): string => RISK_LEVEL_NAME[r] || r;

// ============================================================
// VO 类型(节选核心字段)
// ============================================================

export interface ProcessCheckVO {
  checkId: number;
  batchId: string;
  verdict: string;
  violations: { param: string; paramName: string; value: number; explain: string }[];
  deviations: { param: string; paramName: string; value: number; explain: string }[];
  workOrder?: {
    workOrderId: string; title: string; suggestion: string;
    evidence: string[]; disposition: string; legalBasis: string[];
  };
  checkedAt: string;
}

export interface PassportVO {
  passportId: number;
  batchId: string;
  fingerprint: string;
  prevHash: string;
  contentHash: string;
  usage: string;
  issuedAt: string;
}

export interface EsgVO {
  reportId: number;
  period: string;
  totalScore: number;
  grade: string;
  declaration: string;
}

export interface QualityRiskVO {
  batches: number;
  abnormal: number;
  deviationRatio: number;
  riskScore: number;
  riskLevel: string;
  prediction: string;
}

export interface CreditVO {
  creditId: number;
  entityId: string;
  entityName: string;
  score: number;
  grade: string;
  fraudSuspected: boolean;
  contradictions: string[];
  formula: string;
}

export interface ContractVO {
  contractId: number;
  entityId: string;
  entityName: string;
  loanAmount: number;
  creditGrade: string;
  terms: {
    annualRate: number; guarantee: string;
    collateralRatio: number; collateralAmount: number;
    reviewCycle: string;
  };
  interestAnnual: number;
  clauses: string[];
}

export interface ClassifyVO {
  catalogId: number;
  totalFields: number;
  summary: Record<string, number>;
  catalog: { field: string; level: string; levelName: string; category: string; boundary: string }[];
}

export interface PrecedentVO {
  caseId: string;
  caseName: string;
  outcome: string;
  lossPoint: string;
  ruleSuggestion: string;
  relatedScene: string;
}

export interface TwinVO {
  tripleVerification: {
    physicalDigital: { score: number; explain: string };
    digitalLegal: { score: number; explain: string };
    physicalLegal: { score: number; explain: string };
  };
  twinHealth: number;
  evolution: { strictness: number; clamp: number[] };
}

export interface ZfStatusVO {
  module: string;
  production: { checks: number; violations: number };
  evolution: { feedbacks: number; adopted: number; strictness: number };
  precedents: number;
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

export const ZfAPI = {
  // ================= P0: 生产合规 =================

  /** 工艺合规实时校验(国标限值比对) */
  async processCheck(params: { batchId: string; params: Record<string, number>; operator?: string }): Promise<ProcessCheckVO> {
    const res = await request<any>({
      url: '/api/legal/production/process-check', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      checkId: num(d.checkId), batchId: str(d.batchId),
      verdict: str(d.verdict),
      violations: d.violations || [], deviations: d.deviations || [],
      workOrder: d.workOrder || undefined,
      checkedAt: str(d.checkedAt),
    };
  },

  /** 工艺校验记录列表 */
  async processChecks(limit = 50): Promise<ProcessCheckVO[]> {
    const res = await request<any>({
      url: `/api/legal/production/process-checks?limit=${limit}`, headers: adminHeaders(),
    });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** 数字产品护照签发(指纹链) */
  async passportIssue(params: {
    batchId: string; processParams: Record<string, number>;
    labResults: Record<string, number>;
    operator: string; qualityInsp?: string;
  }): Promise<PassportVO> {
    const res = await request<any>({
      url: '/api/legal/production/passport', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      passportId: num(d.passportId), batchId: str(d.batchId),
      fingerprint: str(d.fingerprint), prevHash: str(d.prevHash),
      contentHash: str(d.contentHash), usage: str(d.usage),
      issuedAt: str(d.issuedAt),
    };
  },

  /** 护照列表 */
  async passports(limit = 50): Promise<PassportVO[]> {
    const res = await request<any>({
      url: `/api/legal/production/passports?limit=${limit}`, headers: adminHeaders(),
    });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** 护照验证(防篡改) */
  async passportVerify(batchId: string): Promise<{ valid: boolean; fingerprint: string; recomputed: string }> {
    const res = await request<any>({
      url: `/api/legal/production/passport/${batchId}`, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      valid: Boolean(d.valid), fingerprint: str(d.fingerprint),
      recomputed: str(d.recomputed),
    };
  },

  /** ESG 报告 */
  async esgReport(params: {
    period: string; energyKwh: number;
    wastewaterTons: number; recycledRatio?: number;
  }): Promise<EsgVO> {
    const res = await request<any>({
      url: '/api/legal/production/esg', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      reportId: num(d.reportId), period: str(d.period),
      totalScore: num(d.totalScore), grade: str(d.grade),
      declaration: str(d.declaration),
    };
  },

  /** 质量风险预测 */
  async qualityRisk(): Promise<QualityRiskVO> {
    const res = await request<any>({
      url: '/api/legal/production/quality-risk', headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      batches: num(d.batches), abnormal: num(d.abnormal),
      deviationRatio: num(d.deviationRatio),
      riskScore: num(d.riskScore), riskLevel: str(d.riskLevel),
      prediction: str(d.prediction),
    };
  },

  // ================= P1: 供应链金融 =================

  /** 信用评估(交叉验证+欺诈检测) */
  async creditAssess(params: {
    entityId: string; entityName: string; monthlyOrders: number;
    inventoryValue: number; productionCapacity: number;
    repaymentRate: number;
  }): Promise<CreditVO> {
    const res = await request<any>({
      url: '/api/legal/finance/credit-assess', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      creditId: num(d.creditId), entityId: str(d.entityId),
      entityName: str(d.entityName), score: num(d.score),
      grade: str(d.grade), fraudSuspected: Boolean(d.fraudSuspected),
      contradictions: d.contradictions || [], formula: str(d.formula),
    };
  },

  /** 动态合约生成(评级差异化) */
  async contractGenerate(params: { entityId: string; loanAmount: number }): Promise<ContractVO> {
    const res = await request<any>({
      url: '/api/legal/finance/contract-generate', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      contractId: num(d.contractId), entityId: str(d.entityId),
      entityName: str(d.entityName), loanAmount: num(d.loanAmount),
      creditGrade: str(d.creditGrade),
      terms: {
        annualRate: num(d.terms?.annualRate),
        guarantee: str(d.terms?.guarantee),
        collateralRatio: num(d.terms?.collateralRatio),
        collateralAmount: num(d.terms?.collateralAmount),
        reviewCycle: str(d.terms?.reviewCycle),
      },
      interestAnnual: num(d.interestAnnual),
      clauses: d.clauses || [],
    };
  },

  /** 合约列表 */
  async contracts(limit = 50): Promise<ContractVO[]> {
    const res = await request<any>({
      url: `/api/legal/finance/contracts?limit=${limit}`, headers: adminHeaders(),
    });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** 资金流向监控 */
  async fundMonitor(params: {
    entityId: string; contractId: number;
    flows: { amount: number; use: string }[];
  }): Promise<any> {
    const res = await request<any>({
      url: '/api/legal/finance/fund-monitor', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P2: 数据资产 =================

  /** 数据分类分级 */
  async classify(params: { dataSamples: string[]; source?: string }): Promise<ClassifyVO> {
    const res = await request<any>({
      url: '/api/legal/asset/classify', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      catalogId: num(d.catalogId), totalFields: num(d.totalFields),
      summary: d.summary || {}, catalog: d.catalog || [],
    };
  },

  /** 数据许可协议(三要素) */
  async licenseGenerate(params: {
    assetDesc: string; dataLevel: string; licensee: string;
    revenueShare?: number; termMonths?: number;
  }): Promise<any> {
    const res = await request<any>({
      url: '/api/legal/asset/license-generate', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 跨境传输评估 */
  async crossBorderAssess(params: {
    region: string; dataLevels: string[]; businessPurpose?: string;
  }): Promise<any> {
    const res = await request<any>({
      url: '/api/legal/asset/cross-border-assess', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P3: 电商深化 =================

  /** 价格合规审计 */
  async priceAudit(params: {
    productId: string; priceHistory: { day: number; dealPrice: number }[];
    current: Record<string, number>;
  }): Promise<any> {
    const res = await request<any>({
      url: '/api/legal/commerce/price-audit', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 预售合规护栏 */
  async presaleGuard(params: {
    productId: string; termDays: number; deposit: number;
    totalPrice: number; presaleType?: string;
  }): Promise<any> {
    const res = await request<any>({
      url: '/api/legal/commerce/presale-guard', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 职业打假防御 */
  async antiBlackmail(params: {
    memberId: number; orderId: string; complaints90d: number;
    returnRatio: number; lawsuitCount?: number;
  }): Promise<any> {
    const res = await request<any>({
      url: '/api/legal/commerce/anti-blackmail', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P3: 进化闭环 =================

  /** 反馈闭环(严格度 ±0.1 安全阀) */
  async feedback(params: { targetType: string; verdict: string; note?: string }): Promise<any> {
    const res = await request<any>({
      url: '/api/legal/evolution/feedback', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 反馈列表 */
  async feedbacks(limit = 50): Promise<any[]> {
    const res = await request<any>({
      url: `/api/legal/evolution/feedbacks?limit=${limit}`, headers: adminHeaders(),
    });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** 判例回流 */
  async precedents(): Promise<PrecedentVO[]> {
    const res = await request<any>({
      url: '/api/legal/evolution/precedents', headers: adminHeaders(),
    });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** 合规数字孪生 */
  async twin(): Promise<TwinVO> {
    const res = await request<any>({
      url: '/api/legal/evolution/twin', headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      tripleVerification: d.tripleVerification || {},
      twinHealth: num(d.twinHealth),
      evolution: {
        strictness: num(d.evolution?.strictness),
        clamp: d.evolution?.clamp || [0.6, 1.4],
      },
    };
  },

  /** 大模型总览 */
  async status(): Promise<ZfStatusVO> {
    const res = await request<any>({
      url: '/api/legal/status', headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      module: str(d.module),
      production: {
        checks: num(d.production?.checks),
        violations: num(d.production?.violations),
      },
      evolution: {
        feedbacks: num(d.evolution?.feedbacks),
        adopted: num(d.evolution?.adopted),
        strictness: num(d.evolution?.strictness),
      },
      precedents: num(d.precedents),
    };
  },
};
