/**
 * 认证 API · 对接后端 /api/auth/*
 * 登录/注册返回 JWT 双令牌 + 会员信息
 */
import { request } from './request';
import { setSession } from '@/services/auth-service';

export interface AuthResult {
  memberId: string;
  phone: string;
  nickname: string;
  role?: string;
  accessToken?: string;
  refreshToken?: string;
}

export const AuthAPI = {
  /** 手机号注册(返回令牌并自动保存会话) */
  async register(phone: string, password: string, nickname?: string): Promise<AuthResult> {
    const res = await request<any>({
      url: '/api/auth/register',
      method: 'POST',
      data: { phone, password, nickname: nickname || undefined },
    });
    return saveSession(res);
  },

  /** 手机号密码登录(自动保存会话) */
  async login(phone: string, password: string): Promise<AuthResult> {
    const res = await request<any>({
      url: '/api/auth/login',
      method: 'POST',
      data: { phone, password },
    });
    return saveSession(res);
  },

  /** 登出(吊销令牌, 调用方负责 clearSession) */
  async logout(refreshToken?: string): Promise<void> {
    const session = (await import('@/services/auth-service')).getSession();
    try {
      await request<any>({
        url: '/api/auth/logout',
        method: 'POST',
        data: { refreshToken: refreshToken || session?.refreshToken || null },
        headers: session?.accessToken
          ? { Authorization: `Bearer ${session.accessToken}` }
          : undefined,
      });
    } catch (_) {
      // 登出失败不阻断本地清理
    }
  },
};

// 后端响应 → 会话保存
function saveSession(res: any): AuthResult {
  const result: AuthResult = {
    memberId: String(res.memberId || ''),
    phone: res.phone || '',
    nickname: res.nickname || '会员',
    role: res.role || 'member',
    accessToken: res.accessToken,
    refreshToken: res.refreshToken,
  };
  if (result.memberId) {
    setSession({
      memberId: result.memberId,
      phone: result.phone,
      nickname: result.nickname,
      role: result.role,
      accessToken: result.accessToken,
      refreshToken: result.refreshToken,
    });
  }
  return result;
}
