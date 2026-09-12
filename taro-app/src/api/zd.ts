/**
 * 智单·AI智能订单大模型 前端 API 客户端
 * 后端: /api/order-ai/*(20 端点, X-Role: admin, 订单数据敏感域)
 * 铁律: 确定性规则引擎(LLM 禁入判定链, 同输入同输出);
 *       裁决/处置一律建议书(永不自动执行);
 *       预测/评分类响应带 formula 推理链留痕
 */
import { request } from './request';
import { getSession } from '@/services/auth-service';

// ============================================================
// 字典(对齐后端 zd_*_service 常量)
// ============================================================

/** 问答五域字典 */
export const QA_DOMAIN_NAME: Record<string, string> = {
  volume: '单量', gmv: 'GMV', refund: '退款',
  fulfillment: '履约', anomaly: '异常',
};

/** 反馈裁决三态字典 */
export const VERDICT_NAME: Record<string, string> = {
  adopted: '采纳', corrected: '修正', rejected: '拒绝',
};

/** 反馈目标七类字典 */
export const FEEDBACK_TARGET_NAME: Record<string, string> = {
  eta_forecast: '履约ETA', volume_forecast: '单量预测',
  whatif: 'What-if推演', refund_score: '退款裁决',
  anomaly_scan: '异常扫描', checkup: '健康体检',
  portrait: '三维画像',
};

/** 退款风险分级字典 */
export const REFUND_LEVEL_NAME: Record<string, string> = {
  low: '低风险', mid: '中风险', high: '高风险',
};

/** 退款裁决建议字典 */
export const REFUND_SUGGESTION_NAME: Record<string, string> = {
  approve: '建议同意退款', manual: '建议人工复核',
  reject: '建议驳回退款',
};

/** 异常三型字典 */
export const ANOMALY_TYPE_NAME: Record<string, string> = {
  high_frequency: '高频下单', bulk_stockpile: '大额囤货',
  instant_refund: '秒退款',
};

/** 备忘录主题字典 */
export const MEMO_TOPIC_NAME: Record<string, string> = {
  promotion_prep: '大促备货', timeout_policy: '超时策略',
};

/** 订单九态字典(对齐 order_service 状态机) */
export const ORDER_STATUS_NAME: Record<string, string> = {
  PENDING: '待付款', PAID: '已支付', SHIPPED: '已发货',
  RECEIVED: '已签收', COMPLETED: '已完成', CANCELLED: '已取消',
  CLOSED: '已关闭', RETURNING: '退款中', REFUNDED: '已退款',
};

export const qaDomainName = (d: string): string => QA_DOMAIN_NAME[d] || d;
export const verdictName = (v: string): string => VERDICT_NAME[v] || v;
export const feedbackTargetName = (t: string): string =>
  FEEDBACK_TARGET_NAME[t] || t;
export const refundLevelName = (l: string): string =>
  REFUND_LEVEL_NAME[l] || l;
export const refundSuggestionName = (s: string): string =>
  REFUND_SUGGESTION_NAME[s] || s;
export const anomalyTypeName = (t: string): string =>
  ANOMALY_TYPE_NAME[t] || t;
export const memoTopicName = (t: string): string => MEMO_TOPIC_NAME[t] || t;
export const orderStatusName = (s: string): string =>
  ORDER_STATUS_NAME[s] || s;

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

export const ZdAPI = {
  // ================= 织物底座 =================

  /** 模块状态(五域服务视图 + 宪法铁律声明) */
  async status(): Promise<any> {
    const res = await request<any>({
      url: '/api/order-ai/status', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 织物总览(九态/GMV/退款率/客单价/履约/三维聚合/日时序) */
  async overview(): Promise<any> {
    const res = await request<any>({
      url: '/api/order-ai/overview', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 订单列表(createdAt 倒序, 只读; 空数据诚实返回 []) */
  async orders(limit = 500): Promise<any[]> {
    const res = await request<any>({
      url: `/api/order-ai/orders?limit=${limit}`, headers: adminHeaders(),
    });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  // ================= P0: 洞察中枢 =================

  /** 自然语言订单问答(五域关键词路由→确定性查询→模板拼接) */
  async qa(text: string): Promise<any> {
    const res = await request<any>({
      url: '/api/order-ai/qa', method: 'POST',
      data: { text }, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 订单健康体检(四维确定性打分, 建议书) */
  async checkup(): Promise<any> {
    const res = await request<any>({
      url: '/api/order-ai/checkup', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 体检报告历史留痕 */
  async checkups(limit = 50): Promise<any[]> {
    const res = await request<any>({
      url: `/api/order-ai/checkups?limit=${limit}`, headers: adminHeaders(),
    });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** 会员×商品×时段三维画像(top 榜 + 时段分布) */
  async portrait(): Promise<any> {
    const res = await request<any>({
      url: '/api/order-ai/portrait', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 画像快照历史留痕 */
  async portraits(limit = 50): Promise<any[]> {
    const res = await request<any>({
      url: `/api/order-ai/portraits?limit=${limit}`, headers: adminHeaders(),
    });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  // ================= P1: 预测沙盘 =================

  /** 履约 ETA 加权预测(近3单×W + 全期×(1−W); 样本0诚实 None) */
  async eta(): Promise<any> {
    const res = await request<any>({
      url: '/api/order-ai/eta', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 三维 What-if 推演(发货延迟/取消率/客单价, 建议书) */
  async whatif(params: {
    shipDelayDays: number; cancelRateDelta: number; aovDelta: number;
  }): Promise<any> {
    const res = await request<any>({
      url: '/api/order-ai/whatif', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 日单量滚动预测(加权移动平均 + 趋势外推, 确定性) */
  async forecast(periods = 12): Promise<any> {
    const res = await request<any>({
      url: `/api/order-ai/forecast?periods=${periods}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P2: 退款裁决与风险 =================

  /** 退款裁决评分(四因子加权, 建议书永不自动执行) */
  async refundScore(orderId: string): Promise<any> {
    const res = await request<any>({
      url: `/api/order-ai/refund-score/${encodeURIComponent(orderId)}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 三类异常订单扫描(高频/囤货/秒退款, 建议书不拦截) */
  async anomalyScan(): Promise<any> {
    const res = await request<any>({
      url: '/api/order-ai/anomaly-scan', method: 'POST',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 历史异常记录列表(留痕) */
  async anomalies(limit = 50): Promise<any[]> {
    const res = await request<any>({
      url: `/api/order-ai/anomalies?limit=${limit}`, headers: adminHeaders(),
    });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  // ================= P3: 进化闭环 =================

  /** 反馈闭环(adopted/corrected/rejected → 参数权重学习) */
  async feedback(targetType: string, verdict: string,
                 note = ''): Promise<any> {
    const res = await request<any>({
      url: '/api/order-ai/feedback', method: 'POST',
      data: { targetType, verdict, note }, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 反馈留痕列表 */
  async feedbacks(limit = 50): Promise<any[]> {
    const res = await request<any>({
      url: `/api/order-ai/feedbacks?limit=${limit}`, headers: adminHeaders(),
    });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** 进化参数视图(etaRecentWeight + 安全阀说明) */
  async params(): Promise<any> {
    const res = await request<any>({
      url: '/api/order-ai/params', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 三检测器(单量 spike/PAID 停滞 drop/取消 surge, 确定性) */
  async detect(): Promise<any> {
    const res = await request<any>({
      url: '/api/order-ai/detect', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 决策备忘录(大促备货/超时策略, 数据插值+假设标注) */
  async memo(topic: string, notes = ''): Promise<any> {
    const res = await request<any>({
      url: '/api/order-ai/memo', method: 'POST',
      data: { topic, notes }, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 备忘录列表(留痕) */
  async memos(limit = 50): Promise<any[]> {
    const res = await request<any>({
      url: `/api/order-ai/memos?limit=${limit}`, headers: adminHeaders(),
    });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },
};
