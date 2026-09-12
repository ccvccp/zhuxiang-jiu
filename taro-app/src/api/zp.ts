/**
 * 智付·AI智能支付大模型 前端 API 客户端
 * 后端: /api/pay69/*(66 端点, X-Role: admin)
 * 铁律: 确定性规则引擎(LLM 禁入判定链, 同输入同输出);
 *       资金域永不自动(调额建议书→admin 终审; 确认仅建议包);
 *       观测面不受 PAY69_MODE 影响(默认 off 零影响上线)
 */
import { request } from './request';
import { getSession } from '@/services/auth-service';

// ============================================================
// 字典(对齐后端 pay69_registry 常量)
// ============================================================

/** 通道七域字典 */
export const CHANNEL_NAME: Record<string, string> = {
  qr: '智能二维码', wechat: '微信支付', alipay: '支付宝支付',
  bank: '银行卡支付', unionpay: '云闪付',
  credit_tv: '闪银信用(信值抵扣)', biometric: '生物特征',
};

/** 健康度四态字典 */
export const HEALTH_STATE_NAME: Record<string, string> = {
  healthy: '健康', degraded: '降级', critical: '危急', frozen: '冻结',
};

/** 意图标签八类字典 */
export const INTENT_TAG_NAME: Record<string, string> = {
  fast_needed: '求快', large_amount: '大额', privacy_needed: '求隐私',
  social_sharing: '社交', promo_hunting: '捡漏', credit_preference: '信用偏好',
  biometric_habit: '生物习惯', default: '默认',
};

/** 步进梯度四档字典(对齐 60号 riskTier) */
export const STEP_NAME: Record<string, string> = {
  free: '免密支付', otp: '短信验证码', biometric: '生物强验证', dual: '双人复核',
};

export const RISK_TIER_NAME: Record<string, string> = {
  light: '轻量', standard: '标准', strong: '强验证', enhanced: '增强',
};

/** 生物结果四态字典 */
export const BIO_RESULT_NAME: Record<string, string> = {
  verified: '通过', degraded: '降级(胁迫线索)',
  failed: '特征不匹配', challenge_expired: '挑战过期',
};

/** 授信五档字典 */
export const CREDIT_GRADE_NAME: Record<string, string> = {
  excellent: '优秀', good: '良好', fair: '一般',
  cautious: '谨慎', rejected: '拒绝',
};

/** 调额建议书三态字典 */
export const ADJ_STATE_NAME: Record<string, string> = {
  proposed: '已建议(待终审)', approved: '已批准', rejected: '已驳回',
};

/** 假设建议书状态字典(P7) */
export const HYP_STATUS_NAME: Record<string, string> = {
  proposed: '已生成', submitted: '已提交46号',
  published: '已发布', rejected: '已驳回',
};

/** 参数版本状态字典(P7) */
export const PARAM_STATUS_NAME: Record<string, string> = {
  draft: '草稿', shadow: '影子', active: '生效', retired: '退役',
};

/** 治理分级字典(P7) */
export const EVO_LEVEL_NAME: Record<string, string> = {
  L0: '观察学习', L1: '受限进化', L2: '协同进化',
};

export const channelName = (c: string): string => CHANNEL_NAME[c] || c;
export const healthStateName = (s: string): string => HEALTH_STATE_NAME[s] || s;
export const intentTagName = (t: string): string => INTENT_TAG_NAME[t] || t;
export const stepName = (s: string): string => STEP_NAME[s] || s;
export const riskTierName = (t: string): string => RISK_TIER_NAME[t] || t;
export const bioResultName = (r: string): string => BIO_RESULT_NAME[r] || r;
export const creditGradeName = (g: string): string => CREDIT_GRADE_NAME[g] || g;
export const adjStateName = (s: string): string => ADJ_STATE_NAME[s] || s;
export const hypStatusName = (s: string): string => HYP_STATUS_NAME[s] || s;
export const paramStatusName = (s: string): string => PARAM_STATUS_NAME[s] || s;
export const evoLevelName = (l: string): string => EVO_LEVEL_NAME[l] || l;

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

export const ZpAPI = {
  // ================= P0 认知中枢(观测面) =================

  /** 七通道字典(费率/限额/时效/特征) */
  async channelDict(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/channels', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 健康度观测面(未观测默认健康; frozen 人工专属) */
  async healthView(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/health', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 模型状态(观测面——off 不受影响) */
  async modelStatus(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/model/status', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 意图留痕视图(观测面) */
  async intents(limit = 20): Promise<any> {
    const res = await request<any>({
      url: `/api/pay69/intents?limit=${limit}`, headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P1 智能路由 =================

  /** 路由字典(权重/因子口径/窗口) */
  async routeDict(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/route/dict', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 多因子评分选道(决策面; 决策演示) */
  async routeCompute(memberId: number, amount: number,
    tags: string[], tvEligible = false): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/route/compute', method: 'POST',
      data: { memberId, amount, tags, tvEligible },
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 通道滚动窗口统计(快环基线) */
  async routeWindow(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/route/window', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 路由执行留痕(观测面) */
  async routeFlows(limit = 10): Promise<any> {
    const res = await request<any>({
      url: `/api/pay69/route/flows?limit=${limit}`, headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P2 认证步进 =================

  /** 熵引擎字典(六轴+权重+梯度) */
  async entropyDict(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/entropy/dict', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 六轴熵计算+步进梯度(决策面; 演示) */
  async entropyCompute(memberId: number, amount: number,
    trustTier: string, channelId: string): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/entropy/compute', method: 'POST',
      data: { memberId, amount, trustTier, channelId },
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 熵评估留痕(观测面) */
  async entropyRecords(memberId?: number, limit = 10): Promise<any> {
    const q = memberId ? `?memberId=${memberId}&limit=${limit}` : `?limit=${limit}`;
    const res = await request<any>({
      url: `/api/pay69/entropy/records${q}`, headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P3 交易级授信 =================

  /** 授信规则表(四轴+评级+利率表) */
  async creditDict(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/credit/dict', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 交易级授信评估(决策面; 演示) */
  async creditEvaluate(memberId: number, amount: number,
    trustTier: string, cashflowIndex = 0.5): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/credit/evaluate', method: 'POST',
      data: { memberId, amount, trustTier, cashflowIndex },
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 调额建议书视图(观测面) */
  async adjustments(status?: string, limit = 10): Promise<any> {
    const q = status ? `?status=${status}&limit=${limit}` : `?limit=${limit}`;
    const res = await request<any>({
      url: `/api/pay69/credit/adjustments${q}`, headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P4 生物特征 =================

  /** 生物字典(胁迫线索表+阈值) */
  async bioDict(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/biometric/dict', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** FIDO 挑战发起(决策面; 演示) */
  async bioChallenge(memberId: number, method = 'face'): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/biometric/challenge', method: 'POST',
      data: { memberId, method }, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 生物验证(决策面; 演示——含胁迫线索) */
  async bioVerify(memberId: number, challenge: string,
    match: boolean, signs: string[]): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/biometric/verify', method: 'POST',
      data: { memberId, challenge, match, signs }, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 生物事件视图(观测面) */
  async bioEvents(memberId?: number, limit = 10): Promise<any> {
    const q = memberId ? `?memberId=${memberId}&limit=${limit}` : `?limit=${limit}`;
    const res = await request<any>({
      url: `/api/pay69/biometric/events${q}`, headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P5 情境智能码 =================

  /** 情境码字典(风险因素+阈值) */
  async smartcodeDict(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/smartcode/dict', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 情境码生成(决策面; 演示) */
  async smartcodeGenerate(memberId: number, merchantId: number,
    amount: number, hour: number, newDevice = false,
    remoteLocation = false): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/smartcode/generate', method: 'POST',
      data: { memberId, merchantId, amount, hour, newDevice, remoteLocation },
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 情境风险统计(快环) */
  async smartcodeStats(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/smartcode/stats', headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P6 多模态普惠 =================

  /** 多模态字典(模态/群体/三态) */
  async modalityDict(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/modality/dict', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 多模态意图解析(决策面; 演示) */
  async modalityParse(memberId: number, text: string,
    modality = 'voice', accessGroup = 'standard'): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/modality/parse', method: 'POST',
      data: { memberId, text, modality, accessGroup },
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 群体×模态统计(快环) */
  async modalityStats(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/modality/stats', headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P7 自进化引擎 =================

  /** 进化引擎字典(分级/参数白名单/版本状态机) */
  async evolutionDict(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/evolution/dict', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 快环漂移检测(不受开关影响) */
  async driftDetect(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/evolution/drift/detect', method: 'POST',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 假设建议书视图(观测面) */
  async hypotheses(status?: string, limit = 10): Promise<any> {
    const q = status ? `?status=${status}&limit=${limit}` : `?limit=${limit}`;
    const res = await request<any>({
      url: `/api/pay69/evolution/hypotheses${q}`, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** L0-L2 治理观测(当前级/统计) */
  async governance(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/evolution/governance', headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P8 安全免疫 =================

  /** 免疫看板(冻结状态+红队史) */
  async immunityView(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/immunity', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 分布监控+自动冻结(快环) */
  async immunityMonitor(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/immunity/monitor', method: 'POST',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 红队四向量执行(决策面; 需 shadow+) */
  async redteamRun(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/immunity/redteam', method: 'POST',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 红队批次历史(观测面) */
  async redteamRuns(limit = 5): Promise<any> {
    const res = await request<any>({
      url: `/api/pay69/immunity/redteam/runs?limit=${limit}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P9 跨境沙盘 =================

  /** 跨境规则库(币种/管辖区/汇率) */
  async crossborderDict(): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/crossborder/dict', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 跨境合规预演(沙盘——全合成) */
  async crossborderPreview(currency: string, jurisdiction: string,
    amountCny: number): Promise<any> {
    const res = await request<any>({
      url: '/api/pay69/crossborder/preview', method: 'POST',
      data: { currency, jurisdiction, amountCny },
      headers: adminHeaders(),
    });
    return res.data || res;
  },
};
