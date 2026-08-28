/**
 * 认证会话服务 · 登录态统一管理
 * ============================================================
 * 会员身份从硬编码改为本地存储动态读取:
 *   - 登录/注册成功 → setSession() 保存会话
 *   - 所有 API 请求经 request.ts 自动注入 X-Member-Id
 *   - 退出登录 → clearSession() 清空
 */
import Taro from '@tarojs/taro';
import { API_BASE } from '@/config';

// 会话存储 key
const AUTH_STORAGE_KEY = 'auth_session';

// 会话结构
export interface AuthSession {
  memberId: string;
  phone: string;
  nickname: string;
  role?: string;
  accessToken?: string;
  refreshToken?: string;
  loggedAt?: string;
}

/** 读取当前会话(未登录返回 null) */
export function getSession(): AuthSession | null {
  try {
    const s = Taro.getStorageSync(AUTH_STORAGE_KEY);
    return s && s.memberId ? (s as AuthSession) : null;
  } catch (_) {
    return null;
  }
}

/** 当前会员 ID(未登录返回空串) */
export function getMemberId(): string {
  return getSession()?.memberId || '';
}

/** 保存会话 */
export function setSession(session: AuthSession): void {
  Taro.setStorageSync(AUTH_STORAGE_KEY, {
    ...session,
    loggedAt: new Date().toISOString(),
  });
}

/** 清空会话(退出登录) */
export function clearSession(): void {
  try {
    Taro.removeStorageSync(AUTH_STORAGE_KEY);
  } catch (_) { /* 忽略 */ }
}

/** 是否已登录 */
export function isLoggedIn(): boolean {
  return !!getSession();
}

/** 未登录时跳转登录页(防重复跳转) */
let redirecting = false;
export function requireLogin(): boolean {
  if (isLoggedIn()) return true;
  if (!redirecting) {
    redirecting = true;
    Taro.navigateTo({
      url: '/pages/login/index',
      complete: () => { redirecting = false; },
    });
  }
  return false;
}

// ---------- 令牌自动刷新(401 时调用, 并发去重) ----------
let refreshing: Promise<AuthSession | null> | null = null;

/**
 * 用 refreshToken 换新令牌(后端轮换机制: 旧 refresh 立即吊销)
 * 成功 → 更新会话并返回; 失败/无 refreshToken → 返回 null
 */
export async function refreshSession(): Promise<AuthSession | null> {
  const session = getSession();
  if (!session?.refreshToken) return null;
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await Taro.request({
          url: `${API_BASE}/api/auth/refresh`,
          method: 'POST',
          data: { refreshToken: session.refreshToken },
          header: { 'Content-Type': 'application/json' },
          timeout: 10000,
        });
        if (res.statusCode >= 200 && res.statusCode < 300
          && res.data?.accessToken) {
          const next: AuthSession = {
            ...session,
            accessToken: res.data.accessToken,
            refreshToken: res.data.refreshToken || session.refreshToken,
          };
          setSession(next);
          return next;
        }
        return null;
      } catch (_) {
        return null;
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}
