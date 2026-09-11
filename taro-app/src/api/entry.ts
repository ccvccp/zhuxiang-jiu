/**
 * 39号·AI智能网站入口管理模块 API 客户端
 * ============================================================
 * 对接后端 entry_routes.py(24 端点) + 复用 30号 auth 骨架
 * (短信发码/OAuth callback/bind-phone)
 *
 * 包含三大前端专有设施:
 *   1. 设备指纹采集器 collectFingerprint() —— 弱特征拼接
 *      (UA/屏幕/语言/时区, 不采 MAC/IMEI 等持久标识, 设计文档 §6 红线)
 *   2. 纯 JS sha256(同步, 跨端) —— Mock 断言派生与设备摘要
 *      (不用 crypto.subtle: HTTP 局域网非安全上下文不可用)
 *   3. Mock 断言派生 deriveMockAssertion() —— 与后端 bio_verify
 *      完全对齐: sha256(challenge + deviceId).hexdigest()[:32]
 *
 * 生物特征红线(设计文档 §1.2/§6):
 *   原始生物数据永不上送——前端只传摘要哈希;
 *   本地凭证仅存 {credentialId, bioType, deviceId}(无生物明细)。
 */
import Taro from '@tarojs/taro';
import { request } from './request';
import { setSession } from '@/services/auth-service';

// ============================================================
// 纯 JS sha256(同步, H5/weapp 跨端, 无安全上下文依赖)
// ============================================================

const SHA256_K = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
  0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
  0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

function utf8Bytes(input: string): number[] {
  const bytes: number[] = [];
  for (let i = 0; i < input.length; i++) {
    let c = input.charCodeAt(i);
    if (c < 0x80) {
      bytes.push(c);
    } else if (c < 0x800) {
      bytes.push(0xc0 | (c >> 6), 0x80 | (c & 0x3f));
    } else if (c >= 0xd800 && c <= 0xdbff && i + 1 < input.length) {
      // 代理对 → UTF-32
      const lo = input.charCodeAt(i + 1);
      c = 0x10000 + ((c - 0xd800) << 10) + (lo - 0xdc00);
      bytes.push(
        0xf0 | (c >> 18), 0x80 | ((c >> 12) & 0x3f),
        0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f),
      );
      i++;
    } else {
      bytes.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
    }
  }
  return bytes;
}

/** sha256 十六进制摘要(64 位小写十六进制, 对齐 Python hashlib) */
export function sha256hex(input: string): string {
  const msg = utf8Bytes(input);
  const bitLen = msg.length * 8;
  msg.push(0x80);
  while (msg.length % 64 !== 56) msg.push(0);
  // 64 位长度的低 32 位(前 32 位消息长度上限内恒 0)
  for (let i = 7; i >= 0; i--) {
    msg.push(i >= 4 ? 0 : (bitLen >>> (8 * (i - 4))) & 0xff);
  }

  const h = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ];
  const w = new Array<number>(64);

  const rr = (x: number, n: number) => (x >>> n) | (x << (32 - n));
  for (let off = 0; off < msg.length; off += 64) {
    for (let i = 0; i < 16; i++) {
      const j = off + i * 4;
      w[i] = (msg[j] << 24) | (msg[j + 1] << 16) | (msg[j + 2] << 8) | msg[j + 3];
    }
    for (let i = 16; i < 64; i++) {
      const s0 = rr(w[i - 15], 7) ^ rr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rr(w[i - 2], 17) ^ rr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
    }
    let [a, b, c, d, e, f, g, hh] = h;
    for (let i = 0; i < 64; i++) {
      const S1 = rr(e, 6) ^ rr(e, 11) ^ rr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (hh + S1 + ch + SHA256_K[i] + w[i]) | 0;
      const S0 = rr(a, 2) ^ rr(a, 13) ^ rr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) | 0;
      hh = g; g = f; f = e; e = (d + t1) | 0;
      d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    h[0] = (h[0] + a) | 0; h[1] = (h[1] + b) | 0;
    h[2] = (h[2] + c) | 0; h[3] = (h[3] + d) | 0;
    h[4] = (h[4] + e) | 0; h[5] = (h[5] + f) | 0;
    h[6] = (h[6] + g) | 0; h[7] = (h[7] + hh) | 0;
  }
  return h.map((x) => (x >>> 0).toString(16).padStart(8, '0')).join('');
}

// ============================================================
// 设备指纹采集器(设计文档 §1.3: 弱特征, 不含持久标识)
// ============================================================

/** 设备弱特征指纹 → 后端 hash_device_id → deviceId */
export function collectFingerprint(): string {
  try {
    if (process.env.TARO_ENV !== 'h5' && typeof Taro.getSystemInfoSync === 'function') {
      // weapp: 系统信息拼接(无 window)
      const si = Taro.getSystemInfoSync() || ({} as any);
      return [
        `ua=${si.system || ''}/${si.platform || ''}/${si.model || ''}`,
        `scr=${si.screenWidth || 0}x${si.screenHeight || 0}`,
        `lang=${si.language || ''}`,
        'tz=weapp',
      ].join('|');
    }
    // H5: UA/屏幕/语言/时区
    const nav = typeof navigator !== 'undefined' ? navigator : ({} as any);
    let tz = '';
    try {
      tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    } catch (_) { /* 老浏览器无时区 */ }
    return [
      `ua=${nav.userAgent || ''}`,
      `scr=${(typeof screen !== 'undefined' ? screen.width : 0)}x${(typeof screen !== 'undefined' ? screen.height : 0)}`,
      `lang=${nav.language || ''}`,
      `tz=${tz}`,
    ].join('|');
  } catch (_) {
    return 'ua=unknown';
  }
}

/**
 * Mock 断言派生(与后端 entry_service.bio_verify 完全对齐):
 * sha256(challenge + deviceId).hexdigest()[:32]
 */
export function deriveMockAssertion(challenge: string, deviceId: string): string {
  return sha256hex(`${challenge}${deviceId}`).slice(0, 32);
}

// ============================================================
// 本地凭证存储(仅 {credentialId, bioType, deviceId}, 无生物明细)
// ============================================================

const BIO_STORAGE_KEY = 'entry_bio_credential';

export interface LocalBioCredential {
  credentialId: string;
  bioType: 'fingerprint' | 'face';
  deviceId: string;
}

export function getLocalBioCredential(): LocalBioCredential | null {
  try {
    const s = Taro.getStorageSync(BIO_STORAGE_KEY);
    return s && s.credentialId ? (s as LocalBioCredential) : null;
  } catch (_) {
    return null;
  }
}

export function saveLocalBioCredential(cred: LocalBioCredential): void {
  Taro.setStorageSync(BIO_STORAGE_KEY, cred);
}

export function clearLocalBioCredential(): void {
  try { Taro.removeStorageSync(BIO_STORAGE_KEY); } catch (_) { /* 忽略 */ }
}

// ============================================================
// VO 定义
// ============================================================

/** AI 预判结果(recognize) */
export interface EntryRecognizeVO {
  deviceId: string;
  knownDevice: boolean;
  recommendedModes: string[];
  greeting: string;
  fingerprintHint?: string;
}

/** 风控决策快照 */
export interface EntryDecision {
  riskScore: number;
  action: 'allow' | 'step_up' | 'challenge' | 'block';
  factors?: Record<string, number>;
  hardBlocked?: string[];
}

/** 统一登录结果(authenticated | step_up_required) */
export interface EntryLoginResult {
  status: 'authenticated' | 'step_up_required';
  tokens?: EntryTokens;
  memberId?: number;
  decision?: EntryDecision;
  stepUpHint?: string;
}

/** 令牌结构(对齐 30号 auth._login_by_member_id) */
export interface EntryTokens {
  memberId: number | string;
  phone: string;
  nickname: string;
  role?: string;
  accessToken: string;
  refreshToken: string;
}

/** 扫码会话(PC create) */
export interface QrSessionVO {
  qrId: string;
  qrPayload: string;
  expiresIn: number;
  statusUrl: string;
}

/** 扫码状态(轮询) */
export interface QrStatusVO {
  qrId: string;
  status: 'pending' | 'scanned' | 'confirmed' | 'expired' | 'cancelled' | 'used';
  seq?: number;
  loginTicket?: string;
}

/** 生物凭证 */
export interface BioCredentialVO {
  credentialId: string;
  bioType: 'fingerprint' | 'face';
  deviceId: string;
  name: string;
  status: string;
  mode?: string;
  enrolledAt?: string;
}

/** 可信设备 */
export interface EntryDeviceVO {
  deviceId: string;
  deviceName?: string;
  lastLoginAt?: string;
  lastIp?: string;
  riskAvg?: number;
  trusted?: boolean;
  trustedUntil?: string;
}

/** 角色落地页 */
export interface EntryLandingVO {
  role: string;
  streak?: number;
  reward?: { day: number; points: number; hint?: string };
  greeting?: string;
  chips?: { key: string; label: string }[];
}

// ============================================================
// API 客户端
// ============================================================

export const EntryAPI = {

  // ---------- 入口识别与统一登录(公开) ----------

  /** AI 预判: 设备识别 → 推荐登录方式排序 + 问候 */
  async recognize(fingerprint?: string): Promise<EntryRecognizeVO> {
    const fp = fingerprint !== undefined ? fingerprint : collectFingerprint();
    const res = await request<any>({
      url: `/api/entry/recognize?fingerprint=${encodeURIComponent(fp)}`,
    });
    return res.data;
  },

  /** 统一登录(密码/短信) → allow 直发 / step_up 待二次 */
  async login(params: {
    mode: 'password' | 'sms';
    phone: string;
    password?: string;
    smsCode?: string;
    fingerprint?: string;
  }): Promise<EntryLoginResult> {
    const res = await request<any>({
      url: '/api/entry/login',
      method: 'POST',
      data: {
        mode: params.mode,
        phone: params.phone,
        password: params.password || '',
        smsCode: params.smsCode || '',
        fingerprint: params.fingerprint !== undefined
          ? params.fingerprint : collectFingerprint(),
      },
    });
    const data = res.data;
    if (data.status === 'authenticated' && data.tokens) {
      saveEntryTokens(data.tokens);
    }
    return data;
  },

  /** step_up 二次验证(短信)完成 → 签发令牌 */
  async stepUpVerify(params: {
    memberId: number;
    phone: string;
    smsCode: string;
    fingerprint?: string;
  }): Promise<EntryLoginResult> {
    const res = await request<any>({
      url: '/api/entry/step-up/verify',
      method: 'POST',
      data: {
        memberId: params.memberId,
        phone: params.phone,
        smsCode: params.smsCode,
        fingerprint: params.fingerprint !== undefined
          ? params.fingerprint : collectFingerprint(),
      },
    });
    const data = res.data;
    if (data.status === 'authenticated' && data.tokens) {
      saveEntryTokens(data.tokens);
    }
    return data;
  },

  // ---------- 扫码登录(手机端确认方视角) ----------

  /** PC 创建扫码会话(180s) */
  async qrCreate(fingerprint?: string): Promise<QrSessionVO> {
    const res = await request<any>({
      url: '/api/entry/qr/create',
      method: 'POST',
      data: { fingerprint: fingerprint || '' },
    });
    return res.data;
  },

  /** 轮询扫码状态(2s 间隔) */
  async qrStatus(qrId: string): Promise<QrStatusVO> {
    const res = await request<any>({ url: `/api/entry/qr/${qrId}/status` });
    return res.data;
  },

  /** 扫码动作(pending → scanned) */
  async qrScan(qrId: string): Promise<QrStatusVO> {
    const res = await request<any>({
      url: `/api/entry/qr/${qrId}/scan`,
      method: 'POST',
      data: {},
    });
    return res.data;
  },

  /** 手机端(已登录态)扫码确认 → 一次性 loginTicket */
  async qrConfirm(qrId: string): Promise<QrStatusVO & { loginTicket?: string }> {
    const res = await request<any>({
      url: `/api/entry/qr/${qrId}/confirm`,
      method: 'POST',
      data: {},
    });
    return res.data;
  },

  /** 取消扫码会话(幂等) */
  async qrCancel(qrId: string): Promise<QrStatusVO> {
    const res = await request<any>({
      url: `/api/entry/qr/${qrId}/cancel`,
      method: 'POST',
      data: {},
    });
    return res.data;
  },

  // ---------- 生物凭证中心(绑定须登录态, 挑战/验证公开) ----------

  /** 发起绑定(设备端本地生成凭证对, 原始数据不上送) */
  async bioEnroll(bioType: 'fingerprint' | 'face', deviceId: string): Promise<{
    memberId: number; bioType: string; deviceId: string;
    enrollChallenge: string; challengeTtl: number; hint: string;
  }> {
    const res = await request<any>({
      url: '/api/entry/bio/enroll',
      method: 'POST',
      data: { bioType, deviceId },
    });
    return res.data;
  },

  /** 完成绑定(publicKeyHash=Mock 派生摘要, 不落原始生物数据) */
  async bioBind(params: {
    bioType: 'fingerprint' | 'face';
    deviceId: string;
    enrollChallenge: string;
    name?: string;
  }): Promise<BioCredentialVO> {
    const publicKeyHash = deriveMockAssertion(
      params.enrollChallenge, params.deviceId);
    const res = await request<any>({
      url: '/api/entry/bio/bind',
      method: 'POST',
      data: {
        bioType: params.bioType,
        deviceId: params.deviceId,
        enrollChallenge: params.enrollChallenge,
        publicKeyHash,
        name: params.name || '',
      },
    });
    return res.data;
  },

  /** 发起生物登录挑战(60s 一次性) */
  async bioChallenge(credentialId: string): Promise<{
    credentialId: string; assertionChallenge: string;
    challengeTtl: number; bioType: string;
  }> {
    const res = await request<any>({
      url: '/api/entry/bio/challenge',
      method: 'POST',
      data: { credentialId },
    });
    return res.data;
  },

  /** 验证断言(本地 Mock 派生) → allow 直发令牌 */
  async bioVerify(credentialId: string, assertionHash: string): Promise<EntryLoginResult> {
    const res = await request<any>({
      url: '/api/entry/bio/verify',
      method: 'POST',
      data: { credentialId, assertionHash },
    });
    const data = res.data;
    if (data.status === 'authenticated' && data.tokens) {
      saveEntryTokens(data.tokens);
    }
    return data;
  },

  /** 我的生物凭证清单 */
  async bioList(): Promise<BioCredentialVO[]> {
    const res = await request<any>({ url: '/api/entry/bio/credentials' });
    return res.data || [];
  },

  /** 吊销生物凭证(幂等) */
  async bioRevoke(credentialId: string): Promise<void> {
    await request<any>({
      url: `/api/entry/bio/credentials/${credentialId}`,
      method: 'DELETE',
    });
  },

  // ---------- 设备管理(登录态) ----------

  /** 我的设备清单 */
  async listDevices(): Promise<EntryDeviceVO[]> {
    const res = await request<any>({ url: '/api/entry/devices' });
    return res.data || [];
  },

  /** 开启设备可信免登录(默认 30 天) */
  async trustDevice(deviceId: string, days = 30): Promise<EntryDeviceVO> {
    const res = await request<any>({
      url: `/api/entry/devices/${deviceId}/trust`,
      method: 'POST',
      data: { days },
    });
    return res.data;
  },

  /** 删除设备(吊销信任, 幂等) */
  async removeDevice(deviceId: string): Promise<void> {
    await request<any>({
      url: `/api/entry/devices/${deviceId}`,
      method: 'DELETE',
    });
  },

  // ---------- 角色落地页 ----------

  /** 角色落地页(hub chips + 连登激励 + 问候) */
  async landing(role: string, memberId?: number): Promise<EntryLandingVO> {
    const mid = memberId ? `&memberId=${memberId}` : '';
    const res = await request<any>({
      url: `/api/entry/landing?role=${role}${mid}`,
    });
    return res.data;
  },

  // ---------- 复用 30号 auth 骨架 ----------

  /** 发送短信验证码(60s 频控 + 日 10 次上限) */
  async sendSmsCode(phone: string): Promise<void> {
    await request<any>({
      url: '/api/sms/send',
      method: 'POST',
      data: { phone },
    });
  },

  /**
   * 三方快捷登录(Mock 轨: 平台未接入, openid 由 code 确定性派生)
   * 已绑定 → 直接登录; 未绑定 → bindRequired(需 bind-phone)
   */
  async oauthLogin(platform: 'wechat' | 'alipay' | 'qq'): Promise<{
    status: 'loggedIn' | 'bindRequired';
    tokens?: EntryTokens;
    ticket?: string;
    expireSeconds?: number;
  }> {
    // Mock 轨: 本地生成随机 code(真实化为平台回调带回)
    const code = `mock_${sha256hex(`${platform}${Date.now()}${Math.random()}`).slice(0, 24)}`;
    const res = await request<any>({
      url: `/api/auth/oauth/${platform}/callback`,
      method: 'POST',
      data: { code },
    });
    if (res.status === 'loggedIn') {
      saveEntryTokens(res as any);
      return { status: 'loggedIn', tokens: res as any };
    }
    return { status: 'bindRequired', ticket: res.ticket, expireSeconds: res.expireSeconds };
  },

  /** 三方登录绑定手机号(手机号+短信码; 已注册绑定/未注册创建) */
  async oauthBindPhone(ticket: string, phone: string, smsCode: string): Promise<EntryTokens> {
    const res = await request<any>({
      url: '/api/auth/oauth/bind-phone',
      method: 'POST',
      data: { ticket, phone, smsCode },
    });
    const tokens = res as any;
    saveEntryTokens(tokens);
    return tokens;
  },
};

// ============================================================
// 令牌 → 会话保存(对齐 AuthAPI.saveSession 口径)
// ============================================================

export function saveEntryTokens(tokens: EntryTokens): void {
  const memberId = String(tokens.memberId || '');
  if (!memberId) return;
  setSession({
    memberId,
    phone: tokens.phone || '',
    nickname: tokens.nickname || '会员',
    role: tokens.role || 'member',
    accessToken: tokens.accessToken,
    refreshToken: tokens.refreshToken,
  });
}
