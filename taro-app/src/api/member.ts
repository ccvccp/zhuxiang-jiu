/**
 * 会员 API · 对接后端 /api/member/*
 */
import { request } from './request';

export interface MemberVO {
  id: string;
  name: string;
  phone?: string;
  level: string;
  levelName?: string;
  points: number;
  growthValue?: number;
  avatar?: string;
  role?: string;
}

/** 等级保级进度(后端 get_level.keepLevel 直映射) */
export interface KeepLevelVO {
  periodConsume: number;       // 本周期消费
  requirement: number;         // 保级要求
  remainingAmount: number;    // 距保级还差
  progressPercent: number;    // 保级进度 %
  levelUpdatedAt: string;     // 周期起算日
  expireAt: string;           // 到期日
  daysRemaining: number | null; // 剩余天数
  renewable: boolean;         // SVIP 可付费续费
  renewFee: number;           // 续费费(¥99/年)
}

/** 等级信息 VO(后端 get_level 直映射) */
export interface LevelInfoVO {
  memberId: string;
  level: number;
  levelName: string;
  growthValue: number;
  nextLevelGrowth: number | null;
  thresholds: Record<string, number>;
  keepLevel: KeepLevelVO;
}

// 后端 level → 前端 VO
function toLevelInfo(d: any): LevelInfoVO {
  const k = d.keepLevel || {};
  return {
    memberId: String(d.memberId ?? ''),
    level: Number(d.level ?? 1),
    levelName: d.levelName || '',
    growthValue: Number(d.growthValue ?? 0),
    nextLevelGrowth: d.nextLevelGrowth ?? null,
    thresholds: d.thresholds || {},
    keepLevel: {
      periodConsume: Number(k.periodConsume ?? 0),
      requirement: Number(k.requirement ?? 0),
      remainingAmount: Number(k.remainingAmount ?? 0),
      progressPercent: Number(k.progressPercent ?? 0),
      levelUpdatedAt: k.levelUpdatedAt || '',
      expireAt: k.expireAt || '',
      daysRemaining: k.daysRemaining ?? null,
      renewable: !!k.renewable,
      renewFee: Number(k.renewFee ?? 0),
    },
  };
}

/** 收货地址 VO(后端 address 存储结构直映射) */
export interface AddressVO {
  id: string;
  name: string;
  phone: string;
  province: string;
  city: string;
  district: string;
  detail: string;
  isDefault: boolean;
}

// 后端 address 字段 → 前端 VO
function mapAddress(a: any): AddressVO {
  return {
    id: String(a.address_id ?? a.id ?? ''),
    name: a.name || '',
    phone: a.phone || '',
    province: a.province || '',
    city: a.city || '',
    district: a.district || '',
    detail: a.detail || '',
    isDefault: Number(a.is_default) === 1,
  };
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
      levelName: m.levelName || '',
      points: m.points || 0,
      growthValue: Number(m.growthValue ?? m.growth_value ?? m.growth ?? 0),
      avatar: m.avatar,
      role: m.role || 'member',
    };
  },

  /** 查询等级(含 keepLevel 保级进度: 周期消费/到期日/剩余天数/renewable) */
  async level(): Promise<LevelInfoVO> {
    const res = await request<any>({ url: '/api/member/level' });
    return toLevelInfo(res);
  },

  /** L5 SVIP 付费续费保级(¥99/年, 续费即开新 12 个月周期; 仅 L5 可调) */
  async renewSvip(): Promise<any> {
    return await request<any>({
      url: '/api/member/level/renew-svip',
      method: 'POST',
      data: {},
    });
  },

  /** 查询积分 */
  async points(): Promise<any> {
    return await request<any>({ url: '/api/member/points' });
  },

  /** 收货地址簿(列表/新增/修改/删除) */
  addresses: {
    /** 地址列表 */
    async list(): Promise<AddressVO[]> {
      const res = await request<any>({ url: '/api/member/addresses' });
      return (res.addresses || []).map(mapAddress);
    },

    /** 新增地址(isDefault=true 时后端自动清除其他默认) */
    async create(data: {
      name: string; phone: string; province: string; city: string;
      district: string; detail: string; isDefault?: boolean;
    }): Promise<AddressVO> {
      const res = await request<any>({
        url: '/api/member/addresses',
        method: 'POST',
        data: {
          name: data.name,
          phone: data.phone,
          province: data.province,
          city: data.city,
          district: data.district,
          detail: data.detail,
          is_default: data.isDefault ? 1 : 0,
        },
      });
      return mapAddress(res.address || res);
    },

    /** 修改地址(传差量字段) */
    async update(addressId: string, data: Partial<{
      name: string; phone: string; province: string; city: string;
      district: string; detail: string; isDefault: boolean;
    }>): Promise<AddressVO> {
      const body: Record<string, any> = {};
      if (data.name !== undefined) body.name = data.name;
      if (data.phone !== undefined) body.phone = data.phone;
      if (data.province !== undefined) body.province = data.province;
      if (data.city !== undefined) body.city = data.city;
      if (data.district !== undefined) body.district = data.district;
      if (data.detail !== undefined) body.detail = data.detail;
      if (data.isDefault !== undefined) body.is_default = data.isDefault ? 1 : 0;
      const res = await request<any>({
        url: `/api/member/addresses/${addressId}`,
        method: 'PUT',
        data: body,
      });
      return mapAddress(res.address || res);
    },

    /** 删除地址 */
    async remove(addressId: string): Promise<void> {
      await request<any>({
        url: `/api/member/addresses/${addressId}`,
        method: 'DELETE',
      });
    },
  },
};
