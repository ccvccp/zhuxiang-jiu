/**
 * 智图·AI智能地图大模型 前端 API 客户端
 * 后端: /api/map-ai/*(22 端点, X-Role: admin)
 * 铁律: 意图解析为确定性本体(无 LLM); 全部建议书模式
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

/** 百度地图 AK(Taro 构建期注入 TARO_APP_ 前缀; 未配置→前端降级列表视图,
 *  不伪造地图。注意: 必须直接静态引用 process.env.X, 防御式
 *  typeof process 守卫会破坏 DefinePlugin 整表达式替换) */
export const BAIDU_MAP_AK: string =
  process.env.TARO_APP_BAIDU_MAP_AK || '';

export const ZtAPI = {
  // ================= P0: 意图引擎 =================

  /** 自然语言→复合意图(确定性本体) */
  async parse(text: string): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/intent/parse', method: 'POST',
      data: { text }, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 复合意图时空搜索(能力 AND+四因子排序) */
  async search(params: {
    text: string; longitude: number; latitude: number;
    role?: string; radiusKm?: number; limit?: number;
  }): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/intent/search', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 业务本体表 */
  async ontology(): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/intent/ontology', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 六角色画像 */
  async roles(): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/intent/roles', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 角色主动服务提示 */
  async behaviorHints(role: string): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/intent/behavior-hints', method: 'POST',
      data: { role }, headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P1: 资源聚合 =================

  /** 附近 POI */
  async nearby(params: {
    longitude: number; latitude: number; capability?: string;
    radiusKm?: number; limit?: number;
  }): Promise<any> {
    const q = Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== '')
      .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
      .join('&');
    const res = await request<any>({
      url: `/api/map-ai/poi/nearby?${q}`, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 全量 POI */
  async pois(poiType?: string): Promise<any[]> {
    const url = poiType
      ? `/api/map-ai/pois?poiType=${poiType}`
      : '/api/map-ai/pois';
    const res = await request<any>({ url, headers: adminHeaders() });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** POI 注册(建议书制) */
  async registerPoi(params: Record<string, any>): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/poi/register', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 月台预约(最短排队分配建议) */
  async dockBooking(params: {
    supplierName: string; slot: string; goodsType?: string;
    truckCount?: number;
  }): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/supplier/dock-booking', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 月台实时状态 */
  async dockStatus(): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/supplier/dock-status',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 多仓货源匹配 */
  async warehouseMatch(params: {
    longitude: number; latitude: number; quantity: number;
  }): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/b2b/warehouse-match', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** B 端履约看板 */
  async fulfillmentBoard(): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/b2b/fulfillment-board',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P2: 全域调度 =================

  /** 态势一张图 */
  async situation(): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/command/situation', headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 异常扫描→工单草稿 */
  async scan(): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/command/scan', method: 'POST',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 工单列表 */
  async tickets(status?: string): Promise<any[]> {
    const url = status
      ? `/api/map-ai/command/tickets?status=${status}`
      : '/api/map-ai/command/tickets';
    const res = await request<any>({ url, headers: adminHeaders() });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** 工单确认/驳回 */
  async ackTicket(ticketId: number,
                  disposition: string): Promise<any> {
    const res = await request<any>({
      url: `/api/map-ai/command/tickets/${ticketId}/ack`,
      method: 'POST', data: { disposition },
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 代理商驾驶舱 */
  async agentCockpit(region: string): Promise<any> {
    const res = await request<any>({
      url: `/api/map-ai/agent/cockpit?region=${encodeURIComponent(
        region)}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 管理层风险雷达 */
  async riskRadar(): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/management/radar', headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= P3: 进化闭环 =================

  /** 行为留痕 */
  async saveBehavior(params: Record<string, any>): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/evolution/behavior', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 行为列表 */
  async behaviors(memberId?: number): Promise<any[]> {
    const url = memberId
      ? `/api/map-ai/evolution/behaviors?memberId=${memberId}`
      : '/api/map-ai/evolution/behaviors';
    const res = await request<any>({ url, headers: adminHeaders() });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** 选址沙盘 */
  async sandbox(params: {
    name: string; longitude: number; latitude: number;
    poiType?: string; monthlyCost?: number;
  }): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/evolution/sandbox', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 时空绩效画像 */
  async performance(): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/evolution/performance',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 反馈闭环 */
  async feedback(targetType: string, verdict: string,
                  note?: string): Promise<any> {
    const res = await request<any>({
      url: '/api/map-ai/evolution/feedback', method: 'POST',
      data: { targetType, verdict, note: note || '' },
      headers: adminHeaders(),
    });
    return res.data || res;
  },
};
