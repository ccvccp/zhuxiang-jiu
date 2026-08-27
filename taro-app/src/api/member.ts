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
  role?: string;
}

export const MemberAPI = {
  /** 获取个人信息 */
  async profile(): Promise<MemberVO> {
    const res = await request<any>({ url: '/api/member/profile' });
    const m = res.profile || res.member || res;
    // 后端 level 为数字(1-5), 统一归一化为 'L1'-'L5' 字符串
    const rawLevel = m.level;
    const level = typeof rawLevel === 'number'
      ? `L${rawLevel}`
      : (rawLevel || 'L1');
    return {
      id: String(m.id || m.member_id || ''),
      name: m.nickname || m.name || '会员',
      phone: m.phone,
      level,
      points: m.points || 0,
      growth: m.growth,
      avatar: m.avatar,
      role: m.role || 'member',
    };
  },

  /** 查询等级 */
  async level(): Promise<any> {
    return await request<any>({ url: '/api/member/level' });
  },

  /** 查询积分 */
  async points(): Promise<any> {
    return await request<any>({ url: '/api/member/points' });
  },
};
