/**
 * 智码·AI智能二维码大模型 前端 API 客户端
 * 后端: /api/qr70/*(71 端点, P0-P8 九期)
 * 铁律: 六类码封闭注册(LLM 禁入判定链);
 *       生成走 55号签名链(业务参数白名单);
 *       愉悦度=观测指标(策略变更走 46号审批);
 *       观测面不受 QR70_MODE 影响(默认 off 零影响)
 */
import { request } from './request';
import { getSession } from '@/services/auth-service';

// ============================================================
// 字典(对齐后端 qr70_registry 常量)
// ============================================================

/** 六类码字典 */
export const CODE_KIND_NAME: Record<string, string> = {
  manage: '管理码', auth: '认证码', trace: '溯源码',
  receiving: '收货码', shipping: '发货码', collect: '收款码',
};

/** 码实例生命周期五态字典 */
export const LIFECYCLE_NAME: Record<string, string> = {
  generated: '已生成', scanned: '已扫', redeemed: '已核销',
  expired: '已过期', voided: '已作废',
};

/** 消费策略三态字典 */
export const POLICY_NAME: Record<string, string> = {
  once: '一次性(nonce 核销即失效)', session: '会话码(TTL 内可重复)', public: '公开码(永不消费)',
};

/** 溯源三画像字典(P1) */
export const PERSONA_NAME: Record<string, string> = {
  quality: '质检党', story: '故事党', value: '实惠党',
};

/** 认证漂移三态字典(P2) */
export const DRIFT_NAME: Record<string, string> = {
  none: '一致', fast: '弱漂移', drifted: '强漂移',
};

/** 认证失败模式六域字典(P2) */
export const FAILURE_MODE_NAME: Record<string, string> = {
  code_wrong: '数字码错误', code_expired: '令牌过期',
  challenge_expired: '挑战过期', coercion: '胁迫降级',
  mismatch: '指纹不匹配', replayed: '重放',
};

/** 收货判定五态字典(P3) */
export const SCAN_VERDICT_NAME: Record<string, string> = {
  matched: '三要素齐备', not_owner: '非订单归属人',
  order_state_violation: '订单非待收货',
  fence_violation: '围栏外扫码', time_violation: '超时间窗',
};

/** 发货版式字典(P4) */
export const LAYOUT_NAME: Record<string, string> = {
  street_static: '夜市静态码', storefront_dynamic: '门店动态码',
  large_challenge: '大额挑战码',
};

/** 假设状态字典(P7) */
export const HYP_STATUS_NAME: Record<string, string> = {
  proposed: '已生成(待提交)', submitted: '已提交46号', rejected: '已驳回',
};

/** 参数版本状态字典(P7) */
export const PARAM_STATUS_NAME: Record<string, string> = {
  draft: '草稿', shadow: '影子(≥7天)', active: '生效', retired: '退役',
};

/** 红队四向量字典(P8) */
export const VECTOR_NAME: Record<string, string> = {
  forged_code: 'RT-01 伪造码', replay_flood: 'RT-02 重放泛洪',
  whitelist_bypass: 'RT-03 白名单绕过', render_poison: 'RT-04 渲染投毒',
};

export const codeKindName = (k: string): string => CODE_KIND_NAME[k] || k;
export const lifecycleName = (s: string): string => LIFECYCLE_NAME[s] || s;
export const policyName = (p: string): string => POLICY_NAME[p] || p;
export const personaName = (p: string): string => PERSONA_NAME[p] || p;
export const driftName = (d: string): string => DRIFT_NAME[d] || d;
export const failureModeName = (f: string): string => FAILURE_MODE_NAME[f] || f;
export const scanVerdictName = (v: string): string => SCAN_VERDICT_NAME[v] || v;
export const layoutName = (l: string): string => LAYOUT_NAME[l] || l;
export const hypStatusName = (s: string): string => HYP_STATUS_NAME[s] || s;
export const paramStatusName = (s: string): string => PARAM_STATUS_NAME[s] || s;
export const vectorName = (v: string): string => VECTOR_NAME[v] || v;

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

export const QrAPI = {
  // ================= P0 码语义中枢(观测面) =================

  /** 六类码注册表字典(码型×场景×权限×生命周期) */
  async dict(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/dict', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 模型状态(观测面——off 不受影响) */
  async modelStatus(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/model/status', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 码实例留痕视图(生命周期可审计) */
  async codes(kind = '', limit = 20): Promise<any> {
    const q = kind ? `?kind=${kind}&limit=${limit}` : `?limit=${limit}`;
    const res = await request<any>({
      url: `/api/qr70/codes${q}`, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 全链事件视图(四可审计) */
  async events(limit = 15): Promise<any> {
    const res = await request<any>({
      url: `/api/qr70/events?limit=${limit}`, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 愉悦度统计基线(按码型聚合) */
  async joyStats(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/joy/stats', headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P0 统一生成/核销(决策面演示) =================

  /** 六类码统一生成(55号签名链) */
  async generate(memberId: number, codeId: string,
    params: Record<string, string>, scene = ''): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/codes/generate', method: 'POST',
      data: { memberId, codeId, params, scene },
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 六类码统一核销(verify 四态+消费策略) */
  async redeem(code: string, operatorId = 0): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/codes/redeem', method: 'POST',
      data: { code, operatorId }, headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P1 溯源码(公开分层呈现) =================

  /** 三画像字典(公开) */
  async personas(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/trace/personas',
    });
    return res.data || res;
  },

  /** 扫瓶码分层呈现(公开; 决策面 off 409) */
  async traceView(code: string, persona = 'quality',
    deviceCap = 'none'): Promise<any> {
    const res = await request<any>({
      url: `/api/qr70/trace/view?code=${encodeURIComponent(code)}&persona=${persona}&deviceCap=${deviceCap}`,
    });
    return res.data || res;
  },

  /** 溯源停留/点击聚合观测(admin) */
  async traceStats(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/trace/view/stats', headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P2 认证码统一 =================

  /** 认证字典(通道×漂移×失败模式) */
  async authDict(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/auth/dict', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 统一认证会话发起(决策面) */
  async authBegin(memberId: number, channel: string,
    fingerprint = ''): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/auth/begin', method: 'POST',
      data: { memberId, channel, fingerprint },
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 指纹漂移校准观测(公开快环) */
  async driftCheck(memberId: number, storedFp: string,
    presentedFp: string, riskScore = 0): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/auth/drift/check', method: 'POST',
      data: { memberId, storedFingerprint: storedFp, presentedFingerprint: presentedFp, riskScore },
    });
    return res.data || res;
  },

  /** 失败模式统计基线(admin) */
  async failureStats(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/auth/failure/stats', headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P3 收货码(三要素) =================

  /** 收货码字典(判定域+阈值) */
  async receivingDict(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/receiving/dict', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 签收码签发(决策面; 仅 SHIPPED) */
  async receivingIssue(orderId: string, fenceKm = 20): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/receiving/issue', method: 'POST',
      data: { orderId, fenceKm }, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 三要素校验(公开; 只校验留痕) */
  async receivingScan(code: string, memberId: number,
    city = '', deviceId = ''): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/receiving/scan', method: 'POST',
      data: { code, memberId, city, deviceId },
    });
    return res.data || res;
  },

  /** 显式签收(公开; 升档须 escalatedAck) */
  async receivingConfirm(code: string, operatorId = 0,
    escalatedAck = false): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/receiving/confirm', method: 'POST',
      data: { code, operatorId, escalatedAck },
    });
    return res.data || res;
  },

  // ================= P4 发货码(版式+交接) =================

  /** 发货码字典(版式+色带) */
  async shippingDict(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/shipping/dict', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 交接码签发(版式+易混色带) */
  async shippingIssue(waveNo: string, orderId: string,
    skuNames: string[], province = '', carrier = ''): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/shipping/issue', method: 'POST',
      data: { waveNo, orderId, skuNames, destinationProvince: province, carrier },
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 交接扫码(绑定运单) */
  async shippingScan(code: string, operatorId: number,
    waybillNo = ''): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/shipping/scan', method: 'POST',
      data: { code, operatorId, waybillNo },
    });
    return res.data || res;
  },

  /** 交接确认(once 核销) */
  async shippingConfirm(code: string, operatorId = 0): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/shipping/confirm', method: 'POST',
      data: { code, operatorId },
    });
    return res.data || res;
  },

  /** 错发统计(混淆对×场景) */
  async confusionStats(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/shipping/confusion/stats', headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P5 管理码(角色办事台) =================

  /** 管理码字典(面板域+确认级) */
  async manageDict(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/manage/dict', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 办事台码签发(session 策略) */
  async manageIssue(memberId: number, station = ''): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/manage/issue', method: 'POST',
      data: { memberId, station }, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 扫码开办事台(33号实时权限面板) */
  async manageOpen(code: string, memberId: number): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/manage/open', method: 'POST',
      data: { code, memberId },
    });
    return res.data || res;
  },

  /** 频次统计(会员×权限点) */
  async manageRank(memberId = 0): Promise<any> {
    const q = memberId ? `?memberId=${memberId}` : '';
    const res = await request<any>({
      url: `/api/qr70/manage/rank${q}`, headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P6 收款码(场景版式) =================

  /** 收款码字典(版式规则+大额阈值) */
  async collectDict(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/collect/dict', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 商户收款码生成(商户主动) */
  async collectIssue(merchantId: number, amount: number,
    scene = 'storefront', sceneNote = ''): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/collect/issue', method: 'POST',
      data: { merchantId, amount, scene, sceneNote },
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 收款核销(公开; once 凭证) */
  async collectRedeem(code: string, operatorId = 0): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/collect/redeem', method: 'POST',
      data: { code, operatorId },
    });
    return res.data || res;
  },

  /** 商户对账摘要(只读聚合) */
  async merchantSummary(merchantId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/qr70/collect/merchant/${merchantId}/summary`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P7 愉悦度引擎(四层) =================

  /** 引擎字典(四层+白名单) */
  async engineDict(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/joy/engine/dict', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 端侧表现层参数建议(公开快环) */
  async renderParams(memberId: number, kind = '',
    elderly = false, lowLight = false,
    consecutiveFailures = 0): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/joy/render/params', method: 'POST',
      data: { memberId, kind, elderly, lowLight, consecutiveFailures },
    });
    return res.data || res;
  },

  /** 假设建议书视图 */
  async hypotheses(status = '', limit = 10): Promise<any> {
    const q = status ? `?status=${status}&limit=${limit}` : `?limit=${limit}`;
    const res = await request<any>({
      url: `/api/qr70/joy/hypotheses${q}`, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 参数版本基线视图 */
  async paramVersions(paramId = '', status = ''): Promise<any> {
    const q = `?paramId=${paramId}&status=${status}`;
    const res = await request<any>({
      url: `/api/qr70/joy/params${q}`, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 漂移检测(元认知快环) */
  async driftDetect(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/joy/drift/detect', method: 'POST',
    });
    return res.data || res;
  },

  /** 进化健康报告 */
  async joyHealth(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/joy/health', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 原子知识库(知识迁移层) */
  async knowledge(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/joy/knowledge', headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P8 安全免疫 =================

  /** 免疫字典(向量域+阈值) */
  async immunityDict(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/immunity/dict', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 免疫看板(冻结态+红队历史) */
  async immunityView(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/immunity', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 分布监控+自动冻结(快环) */
  async immunityMonitor(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/immunity/monitor', method: 'POST',
    });
    return res.data || res;
  },

  /** 人工冻结进化(不受开关影响) */
  async immunityFreeze(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/immunity/freeze', method: 'POST',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 解冻(人工专属铁律) */
  async immunityUnfreeze(): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/immunity/unfreeze', method: 'POST',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 红队四向量执行(决策面) */
  async redteamRun(vector: string, code = '',
    params: Record<string, string> = {}): Promise<any> {
    const res = await request<any>({
      url: '/api/qr70/immunity/redteam', method: 'POST',
      data: { vector, code, params }, headers: adminHeaders(),
    });
    return res.data || res;
  },
};
