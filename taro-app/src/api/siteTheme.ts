/**
 * 网站图标智能管理 API · 对接后端 /api/site-theme/*
 * 管理端需携带 X-Role: admin 头(权限管控)
 */
import { request } from './request';
import { getSession } from '@/services/auth-service';

/** 主题配色组 */
export interface ThemeColorsVO {
  primary: string;
  primaryLight: string;
  navBar: string;
  tabSelected: string;
  tabColor: string;
  tabBg: string;
  textOnPrimary: string;
}

/** 主题方案 */
export interface ThemeVO {
  themeId: number;
  name: string;
  description: string;
  colors: ThemeColorsVO;
  icons: Record<string, any>;    // tabHome/tabProducts/tabMine/quickGrid
  aiScoreLatest: number;
  status: string;                // draft/active/archived
  activatedAt?: string;
}

/** AI 健康度评估结果 */
export interface ThemeAiCheckVO {
  score: number;
  passed: boolean;
  threshold: number;
  factors: Array<{
    name: string;
    label: string;
    score: number;
    maxScore: number;
    detail: string;
  }>;
}

/** 审计日志 */
export interface ThemeLogVO {
  logId: number;
  themeId: number;
  adminId: number;
  action: string;                // create/update/activate/deactivate/rollback
  note: string;
  createdAt: string;
}

/** AI 季节推荐 */
export interface ThemeRecommendVO {
  month: number;
  season: string;
  festival: string | null;
  recommendations: Array<{
    name: string;
    season: string;
    colors: ThemeColorsVO;
    recommendScore: number;
    reasons: string[];
  }>;
}

// 管理端请求头(JWT 强校验: 携带登录令牌, 后端从 Token 验证角色)
const adminHeaders = (): Record<string, string> => {
  const session = getSession();
  const headers: Record<string, string> = {};
  if (session?.accessToken) {
    headers.Authorization = `Bearer ${session.accessToken}`;
  }
  return headers;
};

/** 图标资源库条目(emoji 或上传图片) */
export interface IconItemVO {
  iconId: number;
  name: string;
  emoji: string;   // emoji 图标(与 url 二选一)
  url: string;     // 图片 data URL(与 emoji 二选一)
}

export const SiteThemeAPI = {
  /** 当前激活主题(公开, C 端运行时换肤) */
  async active(): Promise<{ themeId: number; name: string; colors: ThemeColorsVO; icons: Record<string, any> }> {
    const res = await request<any>({ url: '/api/site-theme/active' });
    return {
      themeId: Number(res.themeId || 0),
      name: res.name || '',
      colors: res.colors || {} as ThemeColorsVO,
      icons: res.icons || {},
    };
  },

  /** 图标资源库(公开只读, 编辑器选择用) */
  async icons(): Promise<IconItemVO[]> {
    const res = await request<any>({ url: '/api/site-theme/icons' });
    return (res.icons || [])
      .filter((i: any) => i.category === 'grid' && (i.emoji || i.url))
      .map((i: any): IconItemVO => ({
        iconId: Number(i.iconId || 0),
        name: i.name || '',
        emoji: i.emoji || '',
        url: i.url || '',
      }));
  },

  /** 上传图片图标(data URL, 入库后自动选用) */
  async uploadIcon(dataUrl: string, name?: string): Promise<IconItemVO> {
    const res = await request<any>({
      url: '/api/site-theme/admin/icons',
      method: 'POST',
      headers: adminHeaders(),
      data: { image: dataUrl, name: name || '' },
    });
    return {
      iconId: Number(res.iconId || 0),
      name: res.name || '',
      emoji: res.emoji || '',
      url: res.url || '',
    };
  },

  /** 新增 emoji 图标到资源库 */
  async addEmojiIcon(emoji: string, name?: string): Promise<IconItemVO> {
    const res = await request<any>({
      url: '/api/site-theme/admin/icons',
      method: 'POST',
      headers: adminHeaders(),
      data: { emoji, name: name || '' },
    });
    return {
      iconId: Number(res.iconId || 0),
      name: res.name || '',
      emoji: res.emoji || '',
      url: res.url || '',
    };
  },

  /** 主题方案列表(管理端) */
  async list(): Promise<ThemeVO[]> {
    const res = await request<any>({
      url: '/api/site-theme/themes',
      headers: adminHeaders(),
    });
    return (res.themes || []).map((t: any): ThemeVO => ({
      themeId: Number(t.themeId || 0),
      name: t.name || '',
      description: t.description || '',
      colors: t.colors || {} as ThemeColorsVO,
      icons: t.icons || {},
      aiScoreLatest: Number(t.aiScoreLatest || 0),
      status: t.status || 'draft',
      activatedAt: t.activatedAt || '',
    }));
  },

  /** AI 健康度评估(管理端) */
  async aiCheck(themeId: number): Promise<ThemeAiCheckVO> {
    const res = await request<any>({
      url: `/api/site-theme/themes/${themeId}/ai-check`,
      method: 'POST',
      headers: adminHeaders(),
    });
    return {
      score: Number(res.score || 0),
      passed: !!res.passed,
      threshold: Number(res.threshold || 60),
      factors: (res.factors || []).map((f: any) => ({
        name: f.name, label: f.label,
        score: Number(f.score || 0), maxScore: Number(f.maxScore || 0),
        detail: f.detail || '',
      })),
    };
  },

  /** 创建主题(管理端, 初始 draft) */
  async create(name: string, colors: ThemeColorsVO,
               icons?: Record<string, any>, description?: string): Promise<ThemeVO> {
    const res = await request<any>({
      url: '/api/site-theme/themes',
      method: 'POST',
      headers: adminHeaders(),
      data: { name, colors, icons: icons || {}, description: description || '' },
    });
    return {
      themeId: Number(res.themeId || 0), name: res.name || '',
      description: res.description || '', colors: res.colors || {} as ThemeColorsVO,
      icons: res.icons || {}, aiScoreLatest: Number(res.aiScoreLatest || 0),
      status: res.status || 'draft', activatedAt: res.activatedAt || '',
    };
  },

  /** 编辑主题(管理端, 仅 draft 可编辑) */
  async update(themeId: number, data: {
    name?: string; colors?: Partial<ThemeColorsVO>;
    icons?: Record<string, any>; description?: string;
  }): Promise<ThemeVO> {
    const res = await request<any>({
      url: `/api/site-theme/themes/${themeId}`,
      method: 'PUT',
      headers: adminHeaders(),
      data,
    });
    return {
      themeId: Number(res.themeId || 0), name: res.name || '',
      description: res.description || '', colors: res.colors || {} as ThemeColorsVO,
      icons: res.icons || {}, aiScoreLatest: Number(res.aiScoreLatest || 0),
      status: res.status || 'draft', activatedAt: res.activatedAt || '',
    };
  },

  /** 激活主题(管理端, AI<60 拒绝) */
  async activate(themeId: number): Promise<{ success: boolean }> {
    const res = await request<any>({
      url: `/api/site-theme/themes/${themeId}/activate`,
      method: 'POST',
      headers: adminHeaders(),
    });
    return { success: !!res.success };
  },

  /** 审计日志列表(管理端) */
  async logs(limit = 20): Promise<ThemeLogVO[]> {
    const res = await request<any>({
      url: `/api/site-theme/admin/logs?limit=${limit}`,
      headers: adminHeaders(),
    });
    return (res.logs || []).map((l: any): ThemeLogVO => ({
      logId: Number(l.logId || 0),
      themeId: Number(l.themeId || 0),
      adminId: Number(l.adminId || 0),
      action: l.action || '',
      note: l.note || '',
      createdAt: l.createdAt || '',
    }));
  },

  /** 一键回滚(管理端) */
  async rollback(logId: number): Promise<{ success: boolean }> {
    const res = await request<any>({
      url: `/api/site-theme/admin/logs/${logId}/rollback`,
      method: 'POST',
      headers: adminHeaders(),
    });
    return { success: !!res.success };
  },

  /** AI 季节智能推荐(管理端) */
  async recommend(): Promise<ThemeRecommendVO> {
    const res = await request<any>({
      url: '/api/site-theme/admin/recommend',
      headers: adminHeaders(),
    });
    return {
      month: Number(res.month || 0),
      season: res.season || '',
      festival: res.festival || null,
      recommendations: res.recommendations || [],
    };
  },
};
