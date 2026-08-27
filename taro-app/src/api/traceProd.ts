/**
 * 产品溯源管理 API · 对接后端 /api/trace-prod/*
 * 权限联动: 打卡/建批次等由后端校验 33 号模块环节权限
 */
import { request } from './request';
import { getSession } from '@/services/auth-service';

/** 工段定义 */
export interface TraceStageVO {
  stageId: number;
  code: string;            // STG-BREW 等(工段二维码内容)
  name: string;
  seq: number;             // 流转顺序 1-7
  permStage: string;
  permLevel: string;
  isQcGate: boolean;       // 质检关卡
  maxDwellHours: number;
  desc: string;
  responsibleCandidates: Array<{ memberId: number; nickname: string }>;
}

/** 生产批次 */
export interface TraceBatchVO {
  batchId: number;
  batchNo: string;
  productId: number;
  plannedQty: number;
  currentStageSeq: number;
  status: string;          // producing/released/blocked
  lifeCodes: string[];
  blockedReason?: string;
  createdAt: string;
}

/** 工段打卡记录 */
export interface StagePunchVO {
  punchId: number;
  batchNo: string;
  stageCode: string;
  stageName: string;
  stageSeq: number;
  memberId: number;
  result: string;          // pass/block
  qcConclusion: string;
  params: Record<string, any>;
  anomalies: string[];
  punchedAt: string;
  responsible?: string;
  responsibleMasked?: string;
}

/** AI 溯源健康度 */
export interface TraceHealthVO {
  score: number;
  factors: {
    chainCompleteness: number;
    noAnomaly: number;
    timeliness: number;
    qcComplete: number;
  };
  anomalyCount: number;
}

/** 公开溯源结果 */
export interface PublicTraceVO {
  batchNo: string;
  productId: number;
  plannedQty: number;
  status: string;
  currentStageSeq: number;
  timeline: StagePunchVO[];
  chainValid: boolean;
  health: TraceHealthVO;
}

const authHeaders = (): Record<string, string> => {
  const session = getSession();
  const headers: Record<string, string> = {};
  if (session?.accessToken) {
    headers.Authorization = `Bearer ${session.accessToken}`;
  }
  return headers;
};

export const TraceProdAPI = {
  /** 工段定义列表(附责任人候选) */
  async stages(): Promise<TraceStageVO[]> {
    const res = await request<any>({
      url: '/api/trace-prod/stages', headers: authHeaders(),
    });
    return (res.stages || []).map((s: any): TraceStageVO => ({
      stageId: Number(s.stageId || 0), code: s.code || '',
      name: s.name || '', seq: Number(s.seq || 0),
      permStage: s.permStage || '', permLevel: s.permLevel || '',
      isQcGate: !!s.isQcGate,
      maxDwellHours: Number(s.maxDwellHours || 0),
      desc: s.desc || '',
      responsibleCandidates: s.responsibleCandidates || [],
    }));
  },

  /** 批次列表 */
  async batches(status?: string): Promise<TraceBatchVO[]> {
    const url = status
      ? `/api/trace-prod/batches?status=${status}`
      : '/api/trace-prod/batches';
    const res = await request<any>({ url, headers: authHeaders() });
    return (res.batches || []).map((b: any): TraceBatchVO => ({
      batchId: Number(b.batchId || 0), batchNo: b.batchNo || '',
      productId: Number(b.productId || 0),
      plannedQty: Number(b.plannedQty || 0),
      currentStageSeq: Number(b.currentStageSeq || 0),
      status: b.status || '', lifeCodes: b.lifeCodes || [],
      blockedReason: b.blockedReason || '',
      createdAt: b.createdAt || '',
    }));
  },

  /** 创建生产批次 */
  async createBatch(batchNo: string, plannedQty: number,
                    productId = 1): Promise<TraceBatchVO> {
    return await request<any>({
      url: '/api/trace-prod/batches', method: 'POST',
      headers: authHeaders(),
      data: { batchNo, productId, plannedQty },
    });
  },

  /** 工段扫码打卡(权限即责任+AI 异常检测+链式哈希) */
  async punch(stageCode: string, batchNo: string,
              qcConclusion = '',
              params?: Record<string, any>): Promise<StagePunchVO> {
    return await request<any>({
      url: '/api/trace-prod/punch', method: 'POST',
      headers: authHeaders(),
      data: { stageCode, batchNo, qcConclusion, params: params || {} },
    });
  },

  /** 批次完整溯源链(生产端) */
  async chain(batchNo: string): Promise<{
    batch: TraceBatchVO; timeline: StagePunchVO[]; chainValid: boolean;
  }> {
    const res = await request<any>({
      url: `/api/trace-prod/batches/${batchNo}/chain`,
      headers: authHeaders(),
    });
    return {
      batch: res.batch, timeline: res.timeline || [],
      chainValid: !!res.chainValid,
    };
  },

  /** 绑定瓶码 */
  async bindCodes(batchNo: string,
                  lifeCodes: string[]): Promise<{ lifeCodes: string[] }> {
    return await request<any>({
      url: `/api/trace-prod/batches/${batchNo}/bind-codes`,
      method: 'POST', headers: authHeaders(),
      data: { lifeCodes },
    });
  },

  /** 出库放行 */
  async release(batchNo: string): Promise<TraceBatchVO> {
    return await request<any>({
      url: `/api/trace-prod/batches/${batchNo}/release`,
      method: 'POST', headers: authHeaders(),
    });
  },

  /** 公开溯源(消费者, 无需登录) */
  async publicTrace(batchNo: string): Promise<PublicTraceVO> {
    const res = await request<any>({
      url: `/api/trace-prod/public/${encodeURIComponent(batchNo)}`,
    });
    return {
      batchNo: res.batchNo || '', productId: Number(res.productId || 0),
      plannedQty: Number(res.plannedQty || 0),
      status: res.status || '',
      currentStageSeq: Number(res.currentStageSeq || 0),
      timeline: res.timeline || [], chainValid: !!res.chainValid,
      health: res.health || {
        score: 0,
        factors: {
          chainCompleteness: 0, noAnomaly: 0,
          timeliness: 0, qcComplete: 0,
        },
        anomalyCount: 0,
      },
    };
  },

  /** 管理端: AI 异常事件 */
  async adminAnomalies(): Promise<Array<{
    punchId: number; batchNo: string; stageCode: string;
    anomalies: string[]; memberId: number;
    memberNickname: string; punchedAt: string;
  }>> {
    const res = await request<any>({
      url: '/api/trace-prod/admin/anomalies',
      headers: authHeaders(),
    });
    return res.anomalies || [];
  },

  /** 管理端: 解除质检阻断 */
  async adminUnblock(batchNo: string,
                     reason: string): Promise<TraceBatchVO> {
    return await request<any>({
      url: `/api/trace-prod/admin/batches/${batchNo}/unblock`,
      method: 'POST', headers: authHeaders(),
      data: { reason },
    });
  },

  /** 管理端: 统计 */
  async adminStats(): Promise<{
    batchTotal: number;
    batchByStatus: Record<string, number>;
    punchTotal: number; anomalyTotal: number; avgHealthScore: number;
  }> {
    return await request<any>({
      url: '/api/trace-prod/admin/stats', headers: authHeaders(),
    });
  },
};
