/**
 * 智客·AI智能会员大模型 前端 API 客户端
 * ============================================================
 * 后端: /api/member-ai/*(20 端点, 全部 X-Role: admin)
 * 模块定位: 会员智能运营中枢——NL 问答/健康度五维/RFM 画像/
 *   三信号流失预警/LTV 预测/等级沙盘/权益匹配/积分运营/
 *   沉睡唤醒建议书/反馈学习/三检测器/决策备忘录。
 *
 * 铁律:
 *   - 全部确定性规则引擎(LLM 禁入), 同输入同输出, 数字全来自织物查询层
 *   - 建议书模式: 运营策略/触达永不自动执行, 决策权永在人工
 *   - 与 68 号信值雷达边界: 智客仅消费/等级/积分运营域,
 *     信值五维(诚信/互助/专业/活跃成长)与 TV 资产不在此域输出
 */
import { request } from './request';
import { getSession } from '@/services/auth-service';

const adminHeaders = (): Record<string, string> => {
  const headers: Record<string, string> = { 'X-Role': 'admin' };
  const session = getSession();
  if (session?.accessToken) {
    headers.Authorization = `Bearer ${session.accessToken}`;
  }
  return headers;
};

// ================= 确定性字典(与后端口径一致) =================

/** 等级五档(成长值门槛 0/500/3000/6999/9999) */
export const LEVEL_NAME: Record<number, string> = {
  1: '竹芽会员', 2: '竹叶会员', 3: '竹林会员',
  4: '竹海 VIP', 5: '竹海 SVIP',
};

/** 问答五域(关键词意图路由) */
export const QA_DOMAIN_NAME: Record<string, string> = {
  member: '会员量', level: '等级分布', consume: '消费',
  points: '积分', churn: '流失预警',
};

/** 反馈裁决三态(驱动 ltvRetainFactor 学习) */
export const VERDICT_NAME: Record<string, string> = {
  adopted: '采纳', corrected: '修正', rejected: '拒绝',
};

/** 流失预警三级(红/黄/绿) */
export const RISK_LEVEL_NAME: Record<string, string> = {
  red: '红色高预警', yellow: '黄色中预警', green: '绿色健康',
};

/** 反馈目标六类 */
export const FEEDBACK_TARGET_NAME: Record<string, string> = {
  ltv: 'LTV 预测', churn_scan: '流失扫描', wakeup: '唤醒建议',
  benefit_match: '权益匹配', sandbox: '等级沙盘', portrait: 'RFM 画像',
};

/** 备忘录两主题 */
export const MEMO_TOPIC_NAME: Record<string, string> = {
  member_day: '会员日活动', level_threshold: '等级门槛调整',
};

/** 唤醒分级三级(渠道×时机×权益) */
export const WAKEUP_TIER_NAME: Record<string, string> = {
  deep: '深度沉睡', medium: '中度沉睡', light: '轻度沉睡',
};

// ================= 字典映射(未知回落原值) =================

export const levelName = (lv: number): string =>
  LEVEL_NAME[lv] || `L${lv}`;
export const qaDomainName = (d: string): string =>
  QA_DOMAIN_NAME[d] || d;
export const verdictName = (v: string): string =>
  VERDICT_NAME[v] || v;
export const riskLevelName = (r: string): string =>
  RISK_LEVEL_NAME[r] || r;
export const feedbackTargetName = (t: string): string =>
  FEEDBACK_TARGET_NAME[t] || t;
export const memoTopicName = (t: string): string =>
  MEMO_TOPIC_NAME[t] || t;
export const wakeupTierName = (t: string): string =>
  WAKEUP_TIER_NAME[t] || t;

export const ZkAPI = {
  // ================= P0: 洞察中枢 =================

  /** 服务状态与能力声明 */
  async status(): Promise<any> {
    const res = await request<any>({
      url: '/api/member-ai/status', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 会员总览(量/等级分布/消费/积分/注册时序) */
  async overview(): Promise<any> {
    const res = await request<any>({
      url: '/api/member-ai/overview', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 自然语言会员问答(五域关键词路由, 数字全插值) */
  async qa(text: string): Promise<any> {
    const res = await request<any>({
      url: '/api/member-ai/qa', method: 'POST',
      data: { text }, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 会员健康度五维评分(分段 Sigmoid, 带 formula) */
  async health(memberId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/member-ai/health/${memberId}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 单会员 RFM 确定性分层 */
  async portrait(memberId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/member-ai/portrait/${memberId}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 全量会员 RFM 分层列表(数组防御) */
  async portraits(): Promise<any[]> {
    const res = await request<any>({
      url: '/api/member-ai/portraits', headers: adminHeaders(),
    });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  // ================= P1: 留存引擎 =================

  /** 三信号流失预警全量扫描(红/黄/绿分级) */
  async churnScan(): Promise<any> {
    const res = await request<any>({
      url: '/api/member-ai/churn-scan', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 流失预警留痕列表(按风险分降序) */
  async churns(limit?: number): Promise<any[]> {
    const url = limit
      ? `/api/member-ai/churns?limit=${limit}`
      : '/api/member-ai/churns';
    const res = await request<any>({ url, headers: adminHeaders() });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** 会员 LTV 预测(确定性公式 + 假设标注) */
  async ltv(memberId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/member-ai/ltv/${memberId}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 等级生命周期 What-if 推演(建议书) */
  async sandbox(params: {
    memberId: number; consumeDelta: number; growthDelta: number;
  }): Promise<any> {
    const res = await request<any>({
      url: '/api/member-ai/sandbox', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P2: 权益运营 =================

  /** L1-L5 差异化权益建议(等级×RFM, 建议书) */
  async benefitMatch(memberId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/member-ai/benefit-match/${memberId}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 积分运营分析(单会员/全量, 确定性统计) */
  async pointsAnalysis(memberId?: number): Promise<any> {
    const url = memberId
      ? `/api/member-ai/points-analysis?memberId=${memberId}`
      : '/api/member-ai/points-analysis';
    const res = await request<any>({ url, headers: adminHeaders() });
    return res.data || res;
  },

  /** 沉睡会员分级触达建议书(永不自动发送) */
  async wakeupSuggest(level?: number): Promise<any> {
    const url = level
      ? `/api/member-ai/wakeup-suggest?level=${level}`
      : '/api/member-ai/wakeup-suggest';
    const res = await request<any>({ url, headers: adminHeaders() });
    return res.data || res;
  },

  /** 唤醒建议书留痕列表 */
  async wakeups(limit?: number): Promise<any[]> {
    const url = limit
      ? `/api/member-ai/wakeups?limit=${limit}`
      : '/api/member-ai/wakeups';
    const res = await request<any>({ url, headers: adminHeaders() });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  // ================= P3: 进化闭环 =================

  /** 反馈闭环(参数权重学习, clamp 安全阀) */
  async feedback(targetType: string, verdict: string,
                 note?: string): Promise<any> {
    const res = await request<any>({
      url: '/api/member-ai/feedback', method: 'POST',
      data: { targetType, verdict, note: note || '' },
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 反馈留痕列表(最近优先) */
  async feedbacks(limit?: number): Promise<any[]> {
    const url = limit
      ? `/api/member-ai/feedbacks?limit=${limit}`
      : '/api/member-ai/feedbacks';
    const res = await request<any>({ url, headers: adminHeaders() });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** 当前可学习参数视图(clamp 安全阀) */
  async params(): Promise<any> {
    const res = await request<any>({
      url: '/api/member-ai/params', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 三检测器扫描(消费 spike/积分 drop/注册 surge) */
  async detect(): Promise<any> {
    const res = await request<any>({
      url: '/api/member-ai/detect', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 决策备忘录(模板+数据插值+假设标注) */
  async memo(topic: string, notes?: string): Promise<any> {
    const res = await request<any>({
      url: '/api/member-ai/memo', method: 'POST',
      data: { topic, notes: notes || '' },
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 决策备忘录列表(最近优先) */
  async memos(limit?: number): Promise<any[]> {
    const url = limit
      ? `/api/member-ai/memos?limit=${limit}`
      : '/api/member-ai/memos';
    const res = await request<any>({ url, headers: adminHeaders() });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },
};
