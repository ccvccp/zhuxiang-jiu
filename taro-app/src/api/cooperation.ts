/**
 * 商务合作 API · 对接后端 /api/cooperation/*
 * 提交合作申请(企业/个人/政府/经销) → AI 资质审核 → 签约
 */
import { request } from './request';
import { getMemberId } from '@/services/auth-service';
import Taro from '@tarojs/taro';

/** 合作方类型 */
export const PARTNER_TYPE_NAME: Record<string, string> = {
  enterprise: '企业合作',
  personal: '个人合作',
  government: '政府机构',
  dealer: '经销商',
};

/** 申请类型 */
export const APPLY_TYPE_NAME: Record<string, string> = {
  new: '新合作',
  renewal: '续约',
  upgrade: '升级',
};

/** 申请状态 */
export const APPLY_STATUS_NAME: Record<string, string> = {
  pending: '审核中',
  approved: '已通过',
  rejected: '已驳回',
  signed: '已签约',
};

export const partnerTypeName = (t: string): string => PARTNER_TYPE_NAME[t] || t;
export const applyTypeName = (t: string): string => APPLY_TYPE_NAME[t] || t;
export const applyStatusName = (s: string): string => APPLY_STATUS_NAME[s] || s;

export interface CoopApplicationVO {
  applicationId: number;
  partnerId: number;
  partnerName: string;
  partnerType: string;
  type: string;
  businessScope: string;
  estimatedAmount: number;
  contactName: string;
  contactPhone: string;
  status: string;
  reviewNote: string;
  createdAt: string;
}

// 本地存储: 我提交的申请单号(后端无 memberId 关联, 详情端点公开可查)
const MY_COOP_KEY = 'coop_my_application_id';

function toApp(a: any): CoopApplicationVO {
  return {
    applicationId: Number(a.applicationId ?? a.application_id ?? a.id ?? 0),
    partnerId: Number(a.partnerId ?? a.partner_id ?? 0),
    partnerName: a.partnerName || a.partner_name || '',
    partnerType: a.partnerType || a.partner_type || 'enterprise',
    type: a.type || a.app_type || 'new',
    businessScope: a.businessScope || a.business_scope || '',
    estimatedAmount: Number(a.estimatedAmount ?? a.estimated_amount ?? 0),
    contactName: a.contactName || a.contact_name || '',
    contactPhone: a.contactPhone || a.contact_phone || '',
    status: a.status || 'pending',
    reviewNote: a.reviewNote || a.review_note || a.reviewRemark || '',
    createdAt: a.createdAt || a.created_at || '',
  };
}

export const CooperationAPI = {
  /** 提交合作申请(用户端) */
  async apply(params: {
    partnerName: string;
    partnerType: string;
    type: string;
    businessScope: string;
    estimatedAmount: number;
    contactName?: string;
    contactPhone?: string;
    contactEmail?: string;
  }): Promise<CoopApplicationVO> {
    const res = await request<any>({
      url: '/api/cooperation/applications',
      method: 'POST',
      data: {
        partnerName: params.partnerName,
        partnerType: params.partnerType,
        type: params.type,
        businessScope: params.businessScope,
        estimatedAmount: params.estimatedAmount,
        contactName: params.contactName || '',
        contactPhone: params.contactPhone || '',
        contactEmail: params.contactEmail || '',
      },
    });
    const app = toApp((res.data || res || {}));
    if (app.applicationId > 0) {
      Taro.setStorageSync(MY_COOP_KEY, app.applicationId);
    }
    return app;
  },

  /** 我的申请(服务端按 memberId 查询最新一条; 兜底本地记录单号详情) */
  async myApplication(): Promise<CoopApplicationVO | null> {
    // 优先: 服务端 my-applications(新提交均带 memberId 归属)
    try {
      const res = await request<any>({ url: '/api/cooperation/my-applications' });
      const list = res.data || [];
      if (Array.isArray(list) && list.length > 0) {
        return toApp(list[0]);
      }
    } catch (_) { /* 端点不可用(旧版本后端) → 本地兜底 */ }
    // 兜底: 本地记住单号 → 详情端点
    let appId = 0;
    try {
      appId = Number(Taro.getStorageSync(MY_COOP_KEY)) || 0;
    } catch (_) { /* 忽略 */ }
    if (!appId) return null;
    try {
      const res = await request<any>({
        url: `/api/cooperation/applications/${appId}`,
      });
      const d = res.data || res;
      return d ? toApp(d) : null;
    } catch (_) {
      return null;
    }
  },

  /** 校验登录态(申请前) */
  memberId(): string {
    return getMemberId();
  },
};
