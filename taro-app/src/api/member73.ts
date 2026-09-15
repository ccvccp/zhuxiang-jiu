/**
 * 73号·AI智能会员体验大模型 API · 用户权利面
 * 信任面板("AI 为我做了什么") + 画像遗忘权
 *
 * 后端: /api/member73/trust/*(X-Member-Id 本人鉴权, 73号 P4)
 * 口径: 观测面不受 MEMBER73_MODE 影响(用户权利永不关停);
 *       遗忘为五表硬删除+留痕, member 账户本体保留, 不可重复。
 */
import { request } from './request';
import { getMemberId } from '@/services/auth-service';

/** 动作流条目(可解释) */
export interface TrustActionVO {
  kind: 'hint' | 'reveal' | 'delegate';
  refId: number;
  summary: string;
  responded: boolean | null;
  responseType: string;
  at: string;
}

/** 可撤回授权条目 */
export interface RevocableVO {
  action: string;
  grantedAt: string;
}

/** 信任面板(四可: 可解释/可撤回/可验证/可遗忘) */
export interface TrustPanelVO {
  memberId: number;
  nickname: string;
  mode: string;
  actions: TrustActionVO[];
  actionTotal: number;
  revocable: RevocableVO[];
  fourPrinciples: Record<string, string>;
}

/** 遗忘留痕(五表硬删除回执) */
export interface ForgetLedgerVO {
  forgetSeq: number;
  memberId: number;
  deletedTables: Record<string, number>;
  note: string;
  at: string;
}

export const Member73API = {
  /** 信任面板(动作流+可撤回授权清单; 本人/admin) */
  async trustPanel(memberId?: number): Promise<TrustPanelVO> {
    const mid = memberId ?? (Number(getMemberId()) || 0);
    const res = await request<any>({
      url: `/api/member73/trust/panel/${mid}`,
    });
    const d = (res && (res.data || res)) || {};
    return {
      memberId: Number(d.memberId ?? mid),
      nickname: String(d.nickname || ''),
      mode: String(d.mode || ''),
      actions: (d.actions || []).map((a: any) => ({
        kind: a.kind,
        refId: Number(a.refId ?? 0),
        summary: String(a.summary || ''),
        responded: a.responded ?? null,
        responseType: String(a.responseType || ''),
        at: String(a.at || ''),
      })),
      actionTotal: Number(d.actionTotal ?? 0),
      revocable: (d.revocable || []).map((g: any) => ({
        action: String(g.action || ''),
        grantedAt: String(g.grantedAt || ''),
      })),
      fourPrinciples: d.fourPrinciples || {},
    };
  },

  /** 画像遗忘(五表硬删除+seq 留痕; 账户本体保留; 重复遗忘 409) */
  async trustForget(): Promise<ForgetLedgerVO> {
    const res = await request<any>({
      url: '/api/member73/trust/forget',
      method: 'POST',
    });
    const d = (res && (res.data || res)) || {};
    return {
      forgetSeq: Number(d.forgetSeq ?? 0),
      memberId: Number(d.memberId ?? 0),
      deletedTables: Object.fromEntries(
        Object.entries(d.deletedTables || {})
          .map(([k, v]) => [k, Number(v)])),
      note: String(d.note || ''),
      at: String(d.at || ''),
    };
  },
};
