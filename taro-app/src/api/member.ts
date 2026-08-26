/**
 * 会员 API · 对接后端 /api/member/*
 */
import { request } from './request';

export interface MemberVO {
  id: string;
  name: string;
  phone?: string;
  level: string;
  points: number;
  growth?: number;
  avatar?: string;
}

export const MemberAPI = {
  /** 获取个人信息 */
  async profile(): Promise<MemberVO> {
    const res = await request<any>('/api/member/profile');
    const m = res.member || res;
    return {
      id: String(m.id || m.member_id || ''),
      name: m.nickname || m.name || '会员',
      phone: m.phone,
      level: m.level || 'L1',
      points: m.points || 0,
      growth: m.growth,
      avatar: m.avatar,
    };
  },

  /** 查询等级 */
  async level(): Promise<any> {
    return await request<any>('/api/member/level');
  },

  /** 查询积分 */
  async points(): Promise<any> {
    return await request<any>({ url: '/api/member/points' });
  },
};
