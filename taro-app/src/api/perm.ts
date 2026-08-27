/**
 * 权限AI智能管理 API · 对接后端 /api/perm/*
 * 全员端携带 JWT 登录令牌; 超管端后端校验 role=admin
 */
import { request } from './request';
import { getSession } from '@/services/auth-service';

/** 权限树节点(生产流程 7 环节 × 4 操作级) */
export interface PermNodeVO {
  nodeId: number;
  code: string;          // 如 production.operate
  name: string;          // 酿造生产·操作
  stage: string;
  stageName: string;
  level: string;         // view/operate/approve/manage
  levelName: string;
  sensitivity: string;   // normal/important/core
  sensitivityName: string;
  duties: string[];      // 责任清单(权责共存)
  conflictWith: string[]; // SoD 互斥权限码
  defaultDays: number;
}

/** 授权实例 */
export interface PermGrantVO {
  grantId: number;
  memberId: number;
  nodeCode: string;
  nodeName: string;
  source: string;        // assign(超管直授)/apply(申请审批)
  status: string;        // active/expired/revoked/frozen
  dutySigned: boolean;
  expiresAt: string;
  daysLeft: number;
  duties: string[];
  sensitivity: string;
  memberNickname?: string;
}

/** 审批链节点 */
export interface ApprovalStepVO {
  role: string;          // 直属主管/环节主管/超级管理员
  approverIds: number[];
  approvedBy: number | null;
  opinion: string;
  decidedAt: string;
  auto: boolean;
  autoNote?: string;
  rejected?: boolean;
}

/** 权限申请单 */
export interface PermRequestVO {
  requestId: number;
  applicantId: number;
  nodeCode: string;
  nodeName: string;
  reason: string;
  durationDays: number;
  sensitivity: string;
  sensitivityName: string;
  status: string;        // pending/approved/rejected/cancelled
  approvals: ApprovalStepVO[];
  currentStep: number;
  grantId?: number;
  createdAt: string;
}

/** 角色模板 */
export interface PermRoleVO {
  roleId: number;
  name: string;
  stage: string;
  stageName: string;
  nodeCodes: string[];
}

/** 审计日志 */
export interface PermLogVO {
  logId: number;
  memberId: number;
  action: string;
  nodeCode: string;
  riskLevel: string;
  detail: Record<string, any>;
  createdAt: string;
}

const authHeaders = (): Record<string, string> => {
  const session = getSession();
  const headers: Record<string, string> = {};
  if (session?.accessToken) {
    headers.Authorization = `Bearer ${session.accessToken}`;
  }
  return headers;
};

export const PermAPI = {
  /** 权限树(按生产环节分组) */
  async nodes(): Promise<Record<string, PermNodeVO[]>> {
    const res = await request<any>({
      url: '/api/perm/nodes', headers: authHeaders(),
    });
    return (res.stages || {}) as Record<string, PermNodeVO[]>;
  },

  /** 角色模板列表 */
  async roles(): Promise<PermRoleVO[]> {
    const res = await request<any>({
      url: '/api/perm/roles', headers: authHeaders(),
    });
    return (res.roles || []).map((r: any): PermRoleVO => ({
      roleId: Number(r.roleId || 0), name: r.name || '',
      stage: r.stage || '', stageName: r.stageName || '',
      nodeCodes: r.nodeCodes || [],
    }));
  },

  /** 我的权限(含到期倒计时/责任书状态) */
  async myGrants(): Promise<PermGrantVO[]> {
    const res = await request<any>({
      url: '/api/perm/my/grants', headers: authHeaders(),
    });
    return (res.grants || []).map((g: any): PermGrantVO => ({
      grantId: Number(g.grantId || 0), memberId: Number(g.memberId || 0),
      nodeCode: g.nodeCode || '', nodeName: g.nodeName || '',
      source: g.source || '', status: g.status || '',
      dutySigned: !!g.dutySigned, expiresAt: g.expiresAt || '',
      daysLeft: Number(g.daysLeft || 0), duties: g.duties || [],
      sensitivity: g.sensitivity || '',
      memberNickname: g.memberNickname || '',
    }));
  },

  /** 签署责任书(权责共存) */
  async signDuty(grantId: number): Promise<PermGrantVO> {
    return await request<any>({
      url: `/api/perm/grants/${grantId}/duty-sign`,
      method: 'POST', headers: authHeaders(),
    });
  },

  /** 提交权限申请(AI 预检) */
  async submitRequest(nodeCode: string, reason: string,
                      durationDays?: number): Promise<PermRequestVO> {
    return await request<any>({
      url: '/api/perm/requests', method: 'POST', headers: authHeaders(),
      data: { nodeCode, reason, durationDays: durationDays || undefined },
    });
  },

  /** 我的申请 + 待我审批 */
  async requests(): Promise<{ mine: PermRequestVO[]; toApprove: PermRequestVO[] }> {
    const res = await request<any>({
      url: '/api/perm/requests', headers: authHeaders(),
    });
    return { mine: res.mine || [], toApprove: res.toApprove || [] };
  },

  /** 逐级审批(同意/驳回) */
  async approve(requestId: number, action: 'approve' | 'reject',
                opinion = ''): Promise<PermRequestVO> {
    return await request<any>({
      url: `/api/perm/requests/${requestId}/approve`,
      method: 'POST', headers: authHeaders(),
      data: { action, opinion },
    });
  },

  /** 撤回申请 */
  async cancel(requestId: number): Promise<PermRequestVO> {
    return await request<any>({
      url: `/api/perm/requests/${requestId}/cancel`,
      method: 'POST', headers: authHeaders(),
    });
  },

  /** 权限校验(联调演示) */
  async check(nodeCode: string): Promise<{ allowed: boolean; via: string }> {
    return await request<any>({
      url: '/api/perm/check', method: 'POST', headers: authHeaders(),
      data: { nodeCode },
    });
  },

  // ================= 超管端 =================

  /** 超管直授主要权限 */
  async assign(memberId: number, nodeCode: string,
               durationDays?: number): Promise<PermGrantVO> {
    return await request<any>({
      url: '/api/perm/grants', method: 'POST', headers: authHeaders(),
      data: { memberId, nodeCode, durationDays: durationDays || undefined },
    });
  },

  /** 吊销授权 */
  async revoke(grantId: number): Promise<PermGrantVO> {
    return await request<any>({
      url: `/api/perm/grants/${grantId}`,
      method: 'DELETE', headers: authHeaders(),
    });
  },

  /** 全部授权视图(超管) */
  async adminGrants(status?: string): Promise<PermGrantVO[]> {
    const url = status
      ? `/api/perm/admin/grants?status=${status}`
      : '/api/perm/admin/grants';
    const res = await request<any>({ url, headers: authHeaders() });
    return (res.grants || []).map((g: any): PermGrantVO => ({
      grantId: Number(g.grantId || 0), memberId: Number(g.memberId || 0),
      nodeCode: g.nodeCode || '', nodeName: g.nodeName || '',
      source: g.source || '', status: g.status || '',
      dutySigned: !!g.dutySigned, expiresAt: g.expiresAt || '',
      daysLeft: 0, duties: [], sensitivity: '',
      memberNickname: g.memberNickname || '',
    }));
  },

  /** 审计日志(超管) */
  async adminLogs(limit = 50): Promise<PermLogVO[]> {
    const res = await request<any>({
      url: `/api/perm/admin/logs?limit=${limit}`,
      headers: authHeaders(),
    });
    return (res.logs || []).map((l: any): PermLogVO => ({
      logId: Number(l.logId || 0), memberId: Number(l.memberId || 0),
      action: l.action || '', nodeCode: l.nodeCode || '',
      riskLevel: l.riskLevel || '', detail: l.detail || {},
      createdAt: l.createdAt || '',
    }));
  },

  /** 触发到期回收清扫(超管) */
  async expireSweep(): Promise<{ swept: number }> {
    return await request<any>({
      url: '/api/perm/admin/expire-sweep',
      method: 'POST', headers: authHeaders(),
    });
  },
};
