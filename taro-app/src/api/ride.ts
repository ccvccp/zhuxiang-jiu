/**
 * 代驾联盟 API · 对接后端 /api/ride/*
 * 券包(FEFO 自动选券) → 叫代驾(三轨派单) → 行程(取消/司机接单流转)
 * 司机端: 注册申请(AI 审查) → 上下线 → 接单/开始/完成
 */
import { request } from './request';

/** 行程状态名 */
export const RIDE_STATUS_NAME: Record<string, string> = {
  requested: '已叫单',
  dispatched: '已派单',
  driver_arriving: '司机赶来',
  trip_started: '行程中',
  trip_completed: '行程结束',
  settling: '结算中',
  settled: '已结算',
  cancelled: '已取消',
  no_driver: '暂无运力',
};

/** 券状态名 */
export const COUPON_STATUS_NAME: Record<string, string> = {
  granted: '可用',
  used: '已使用',
  expired: '已过期',
  revoked: '已作废',
};

export const rideStatusName = (s: string): string => RIDE_STATUS_NAME[s] || s;
export const couponStatusName = (s: string): string => COUPON_STATUS_NAME[s] || s;

/** 可取消的行程状态 */
export const CANCELLABLE_STATUSES = ['requested', 'dispatched', 'driver_arriving'];

export interface RideCouponVO {
  code: string;
  value: number;
  status: string;
  expiresAt: string;
  sourceOrderId?: string;
}

export interface CouponPackageVO {
  holdCount: number;
  totalGranted: number;
  totalUsed: number;
  totalRevoked: number;
  holdCap: number;
  expiringSoon: string[];
  coupons: RideCouponVO[];
}

export interface DriverSnapshotVO {
  driverId: number | null;
  trackName: string;
  platform: string;
  name: string;
  phone: string;
  plateNo: string;
  rating: number | null;
  pickupDistanceKm?: number;
}

export interface RideVO {
  rideId: string;
  memberId: number;
  couponCode: string;
  couponValue: number;
  status: string;
  pickup: { lat: number; lng: number; address: string };
  dropoff: { lat: number; lng: number; address: string };
  distanceKm: number;
  actualKm: number | null;
  driverId: number | null;
  driverSnapshot: DriverSnapshotVO | Record<string, never>;
  dispatchMode: string;
  pricing: Record<string, any>;
  cancelReason: string;
  cancelWindowFree: boolean | null;
  requestedAt: string;
  dispatchedAt: string | null;
  startedAt: string | null;
  completedAt: string | null;
  settledAt: string | null;
}

export interface DriverApplicationVO {
  applicationId: number;
  memberId: number;
  status: string;      // approved / manual_review / rejected
  aiScore?: number;
  note?: string;
}

function toCoupon(c: any): RideCouponVO {
  return {
    code: c.code || '',
    value: Number(c.value ?? 0),
    status: c.status || 'granted',
    expiresAt: c.expiresAt || '',
    sourceOrderId: c.sourceOrderId || c.orderId || '',
  };
}

function toRide(r: any): RideVO {
  const ds = r.driverSnapshot || {};
  return {
    rideId: String(r.rideId ?? r.ride_id ?? ''),
    memberId: Number(r.memberId ?? 0),
    couponCode: r.couponCode || '',
    couponValue: Number(r.couponValue ?? 0),
    status: r.status || '',
    pickup: {
      lat: Number(r.pickup?.lat ?? 0),
      lng: Number(r.pickup?.lng ?? 0),
      address: r.pickup?.address || '',
    },
    dropoff: {
      lat: Number(r.dropoff?.lat ?? 0),
      lng: Number(r.dropoff?.lng ?? 0),
      address: r.dropoff?.address || '',
    },
    distanceKm: Number(r.distanceKm ?? 0),
    actualKm: r.actualKm ?? null,
    driverId: r.driverId ?? null,
    driverSnapshot: {
      driverId: ds.driverId ?? null,
      trackName: ds.trackName || '',
      platform: ds.platform || '',
      name: ds.name || '',
      phone: ds.phone || '',
      plateNo: ds.plateNo || '',
      rating: ds.rating ?? null,
      pickupDistanceKm: ds.pickupDistanceKm,
    },
    dispatchMode: r.dispatchMode || '',
    pricing: r.pricing || {},
    cancelReason: r.cancelReason || '',
    cancelWindowFree: r.cancelWindowFree ?? null,
    requestedAt: r.requestedAt || '',
    dispatchedAt: r.dispatchedAt || null,
    startedAt: r.startedAt || null,
    completedAt: r.completedAt || null,
    settledAt: r.settledAt || null,
  };
}

export const RideAPI = {
  /** 我的券包(含即将过期提醒) */
  async coupons(): Promise<CouponPackageVO> {
    const res = await request<any>({ url: '/api/ride/coupons' });
    const d = res || {};
    return {
      holdCount: Number(d.holdCount ?? 0),
      totalGranted: Number(d.totalGranted ?? 0),
      totalUsed: Number(d.totalUsed ?? 0),
      totalRevoked: Number(d.totalRevoked ?? 0),
      holdCap: Number(d.holdCap ?? 6),
      expiringSoon: d.expiringSoon || [],
      coupons: (d.coupons || []).map(toCoupon),
    };
  },

  /** 叫代驾(FEFO 自动选券 → AI 评分 → 三轨派单, 永不拒单) */
  async call(params: {
    pickup: { lat: number; lng: number; address: string };
    dropoff: { lat: number; lng: number; address: string };
    distanceKm?: number;
  }): Promise<RideVO> {
    const res = await request<any>({
      url: '/api/ride/call',
      method: 'POST',
      data: {
        pickup: params.pickup,
        dropoff: params.dropoff,
        distanceKm: params.distanceKm ?? null,
      },
    });
    return toRide(res.data || res);
  },

  /** 我的行程列表 */
  async myRides(status?: string): Promise<RideVO[]> {
    const qs = status ? `?status=${encodeURIComponent(status)}` : '';
    const res = await request<any>({ url: `/api/ride/orders${qs}` });
    const list = (res || {}).rides || [];
    return (Array.isArray(list) ? list : []).map(toRide);
  },

  /** 行程详情 */
  async rideDetail(rideId: string): Promise<RideVO> {
    const res = await request<any>({ url: `/api/ride/orders/${rideId}` });
    return toRide((res || {}).ride || res);
  },

  /** 取消行程(派单后 3 分钟内券退回) */
  async cancelRide(rideId: string, reason = ''): Promise<any> {
    return await request<any>({
      url: `/api/ride/orders/${rideId}/cancel`,
      method: 'POST',
      data: { reason },
    });
  },

  // ============================================================
  // 司机端
  // ============================================================

  /** 司机注册申请(AI 全自动审查, 即时出档) */
  async driverApply(params: {
    idNumber: string;
    licenseNumber: string;
    licenseClass?: string;
    drivingYears: number;
    accidentFreeDecl: boolean;
    drunkFreeDecl: boolean;
    emergencyContact: string;
  }): Promise<DriverApplicationVO> {
    const res = await request<any>({
      url: '/api/ride/driver/apply',
      method: 'POST',
      data: {
        idNumber: params.idNumber,
        licenseNumber: params.licenseNumber,
        licenseClass: params.licenseClass || 'C1',
        drivingYears: params.drivingYears,
        accidentFreeDecl: params.accidentFreeDecl,
        drunkFreeDecl: params.drunkFreeDecl,
        emergencyContact: params.emergencyContact,
      },
    });
    const d = res.data || res || {};
    return {
      applicationId: Number(d.applicationId ?? d.application_id ?? 0),
      memberId: Number(d.memberId ?? 0),
      status: d.status || '',
      aiScore: d.aiReview?.score ?? d.aiScore,
      note: d.note || d.reviewNote || '',
    };
  },

  /** 我的司机审查进度 */
  async driverApplication(): Promise<DriverApplicationVO | null> {
    const res = await request<any>({ url: '/api/ride/driver/application' });
    const d = (res || {}).application;
    if (!d) return null;
    return {
      applicationId: Number(d.applicationId ?? 0),
      memberId: Number(d.memberId ?? 0),
      status: d.status || '',
      aiScore: d.aiReview?.score ?? d.aiScore,
      note: d.note || d.reviewNote || '',
    };
  },

  /** 司机上下线(online/offline) */
  async setDriverStatus(status: 'online' | 'offline'): Promise<any> {
    return await request<any>({
      url: '/api/ride/driver/status',
      method: 'POST',
      data: { status },
    });
  },

  /** 司机接单(dispatched → driver_arriving) */
  async driverAccept(rideId: string): Promise<any> {
    return await request<any>({
      url: `/api/ride/driver/orders/${rideId}/accept`,
      method: 'POST',
    });
  },

  /** 司机开始行程(乘客上车) */
  async driverStart(rideId: string): Promise<any> {
    return await request<any>({
      url: `/api/ride/driver/orders/${rideId}/start`,
      method: 'POST',
    });
  },

  /** 司机完成行程(AI 自动结算) */
  async driverComplete(rideId: string): Promise<any> {
    return await request<any>({
      url: `/api/ride/driver/orders/${rideId}/complete`,
      method: 'POST',
    });
  },

  /** 司机我的行程 */
  async driverRides(status?: string): Promise<RideVO[]> {
    const qs = status ? `?status=${encodeURIComponent(status)}` : '';
    const res = await request<any>({ url: `/api/ride/driver/orders${qs}` });
    const list = (res || {}).rides || res?.data || [];
    return (Array.isArray(list) ? list : []).map(toRide);
  },
};
