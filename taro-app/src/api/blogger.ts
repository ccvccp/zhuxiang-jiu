/**
 * 平台流量DV博主模块 API · 对接后端 /api/blogger/*(40号)
 * 博主池 · 雷达侦测 · 跟随流水线 · 发布调度 · 学习进化
 * 管理端需携带 X-Role: admin 头(权限管控)
 */
import { request } from './request';
import { getSession } from '@/services/auth-service';

/** 平台字典 */
export const PLATFORM_NAME: Record<string, string> = {
  douyin: '抖音', xiaohongshu: '小红书',
  weibo: '微博', wechat_channels: '视频号',
};

/** 领域字典 */
export const DOMAIN_NAME: Record<string, string> = {
  wine: '酒类', food: '美食', gift: '礼品', lifestyle: '生活',
};

/** 作品状态字典 */
export const WORK_STATUS_NAME: Record<string, string> = {
  detected: '待决策', auto_follow: '自动跟随',
  manual_queue: '人工确认', passed: '已跳过',
  discarded: '风险否决', following: '跟随中',
};

/** 跟随内容状态字典 */
export const FOLLOW_STATUS_NAME: Record<string, string> = {
  pending: '待人工审', approved: '已过审',
  rejected: '已拒绝', queued: '发布队列',
  published: '已发布',
};

export const platformName = (p: string): string => PLATFORM_NAME[p] || p;
export const domainName = (d: string): string => DOMAIN_NAME[d] || d;
export const workStatusName = (s: string): string => WORK_STATUS_NAME[s] || s;
export const followStatusName = (s: string): string => FOLLOW_STATUS_NAME[s] || s;

export interface BloggerVO {
  bloggerId: number;
  platform: string;
  account: string;
  nickname: string;
  fansWan: number;
  domain: string;
  engagementRate: number;
  status: string;               // active / paused
  weight: number;
  weightBase: number;
  weightAdjust: number;
  zeroTrafficStreak: number;
  pausedReason: string;        // manual / auto_loss_cut / fraud_suspect / ''
  createdAt: string;
}

export interface WorkVO {
  workId: number;
  bloggerId: number;
  platform: string;
  extWorkId: string;
  title: string;
  summary: string;
  likes: number;
  comments: number;
  shares: number;
  publishedAt: string;
  status: string;
  score: number;
  decision: string;
  riskFlag: string;
}

export interface FollowVO {
  followId: number;
  workId: number;
  bloggerId: number;
  platform: string;
  title: string;
  body: string;
  hashtags: string;
  shortCode: string;
  shortLink: string;
  overlapRatio: number;
  complianceScore: number;
  complianceViolations: string[];
  hardFail: string[];
  evidenceHash: string;
  status: string;
  reviewer: string;
  scheduledAt: string;
  publishedAt: string;
  learningFed: boolean;
  learningMetrics: {
    clicks: number; clickRaw: number; clickQuality: number;
    reward: number; fraudSuspect: boolean;
    registrations: number; orders: number; gmv: number;
  } | null;
  createdAt: string;
}

export interface ReportOverviewVO {
  pool: { total: number; active: number; paused: number; autoPaused: number; evolved: number };
  works: { total: number; autoFollow: number; manualQueue: number; passed: number; discarded: number; following: number };
  follows: { total: number; pending: number; approved: number; rejected: number; queued: number; published: number };
  attribution: { clicks: number; registered: number; ordered: number; gmv: number };
  limits: { dailyCap: number; bloggerCooldownHours: number; followGapHours: number };
}

export interface LearningStatusVO {
  scorerId: string;
  feedback: { published: number; fed: number; pending: number; settleHours: number };
  weightEvolution: {
    top: { bloggerId: number; nickname: string; weight: number; weightAdjust: number }[];
    bottom: { bloggerId: number; nickname: string; weight: number; weightAdjust: number }[];
    autoPaused: { bloggerId: number; nickname: string; zeroTrafficStreak: number }[];
  };
}

// 管理端请求头(X-Role: admin + 登录令牌)
const adminHeaders = (): Record<string, string> => {
  const headers: Record<string, string> = { 'X-Role': 'admin' };
  const session = getSession();
  if (session?.accessToken) {
    headers.Authorization = `Bearer ${session.accessToken}`;
  }
  return headers;
};

function toBlogger(b: any): BloggerVO {
  return {
    bloggerId: Number(b.bloggerId ?? 0),
    platform: b.platform || '',
    account: b.account || '',
    nickname: b.nickname || '',
    fansWan: Number(b.fansWan ?? 0),
    domain: b.domain || '',
    engagementRate: Number(b.engagementRate ?? 0),
    status: b.status || 'active',
    weight: Number(b.weight ?? 0),
    weightBase: Number(b.weightBase ?? 0),
    weightAdjust: Number(b.weightAdjust ?? 0),
    zeroTrafficStreak: Number(b.zeroTrafficStreak ?? 0),
    pausedReason: b.pausedReason || '',
    createdAt: b.createdAt || '',
  };
}

function toWork(w: any): WorkVO {
  return {
    workId: Number(w.workId ?? 0),
    bloggerId: Number(w.bloggerId ?? 0),
    platform: w.platform || '',
    extWorkId: w.extWorkId || '',
    title: w.title || '',
    summary: w.summary || '',
    likes: Number(w.likes ?? 0),
    comments: Number(w.comments ?? 0),
    shares: Number(w.shares ?? 0),
    publishedAt: w.publishedAt || '',
    status: w.status || 'detected',
    score: Number(w.score ?? 0),
    decision: w.decision || '',
    riskFlag: w.riskFlag || '',
  };
}

function toFollow(f: any): FollowVO {
  return {
    followId: Number(f.followId ?? 0),
    workId: Number(f.workId ?? 0),
    bloggerId: Number(f.bloggerId ?? 0),
    platform: f.platform || '',
    title: f.title || '',
    body: f.body || '',
    hashtags: f.hashtags || '',
    shortCode: f.shortCode || '',
    shortLink: f.shortLink || '',
    overlapRatio: Number(f.overlapRatio ?? 0),
    complianceScore: Number(f.complianceScore ?? 0),
    complianceViolations: f.complianceViolations || [],
    hardFail: f.hardFail || [],
    evidenceHash: f.evidenceHash || '',
    status: f.status || 'pending',
    reviewer: f.reviewer || '',
    scheduledAt: f.scheduledAt || '',
    publishedAt: f.publishedAt || '',
    learningFed: Boolean(f.learningFed),
    learningMetrics: f.learningMetrics
      ? {
        clicks: Number(f.learningMetrics.clicks ?? 0),
        clickRaw: Number(f.learningMetrics.clickRaw ?? 0),
        clickQuality: Number(f.learningMetrics.clickQuality ?? 0),
        reward: Number(f.learningMetrics.reward ?? 0),
        fraudSuspect: Boolean(f.learningMetrics.fraudSuspect),
        registrations: Number(f.learningMetrics.registrations ?? 0),
        orders: Number(f.learningMetrics.orders ?? 0),
        gmv: Number(f.learningMetrics.gmv ?? 0),
      }
      : null,
    createdAt: f.createdAt || '',
  };
}

export const BloggerAPI = {
  // ================= 博主池 =================

  /** 博主池列表(按权重降序) */
  async listBloggers(params?: { status?: string; platform?: string }): Promise<BloggerVO[]> {
    const p: string[] = [];
    if (params?.status) p.push(`status=${params.status}`);
    if (params?.platform) p.push(`platform=${params.platform}`);
    const q = p.length ? `?${p.join('&')}` : '';
    const res = await request<any>({ url: `/api/blogger/pool${q}`, headers: adminHeaders() });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toBlogger);
  },

  /** 新增博主(领域准入: 酒/美食/礼品/生活) */
  async createBlogger(params: {
    platform: string; account: string; nickname: string;
    fansWan: number; domain: string; engagementRate: number;
  }): Promise<BloggerVO> {
    const res = await request<any>({
      url: '/api/blogger/pool', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    return toBlogger(res.data || res);
  },

  /** 暂停博主(不再进入雷达扫描) */
  async pauseBlogger(bloggerId: number): Promise<BloggerVO> {
    const res = await request<any>({
      url: `/api/blogger/pool/${bloggerId}/pause`, method: 'POST',
      headers: adminHeaders(),
    });
    return toBlogger(res.data || res);
  },

  /** 恢复博主(清零止损计数) */
  async activateBlogger(bloggerId: number): Promise<BloggerVO> {
    const res = await request<any>({
      url: `/api/blogger/pool/${bloggerId}/activate`, method: 'POST',
      headers: adminHeaders(),
    });
    return toBlogger(res.data || res);
  },

  // ================= 雷达侦测 =================

  /** 手动触发全池扫描(Mock 增量源+指纹去重+风险否决+自动决策) */
  async radarScan(): Promise<{ scanned: number; works: WorkVO[]; decisions: any[] }> {
    const res = await request<any>({
      url: '/api/blogger/radar/scan', method: 'POST',
      headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      scanned: Number(d.scanned ?? 0),
      works: (d.works || []).map(toWork),
      decisions: d.decisions || [],
    };
  },

  /** 侦测作品列表 */
  async listWorks(params?: { bloggerId?: number; status?: string }): Promise<WorkVO[]> {
    const p: string[] = [];
    if (params?.bloggerId) p.push(`bloggerId=${params.bloggerId}`);
    if (params?.status) p.push(`status=${params.status}`);
    const q = p.length ? `?${p.join('&')}` : '';
    const res = await request<any>({ url: `/api/blogger/works${q}`, headers: adminHeaders() });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toWork);
  },

  /** 手动重决策(detected 状态作品) */
  async decideWork(workId: number): Promise<WorkVO> {
    const res = await request<any>({
      url: `/api/blogger/works/${workId}/decide`, method: 'POST',
      headers: adminHeaders(),
    });
    return toWork((res.data || res).work);
  },

  /** 人工裁决(50-70 区间: 确认跟随/放弃留痕) */
  async manualDecide(workId: number, engage: boolean, note = ''): Promise<WorkVO> {
    const res = await request<any>({
      url: `/api/blogger/works/${workId}/manual-decide`,
      method: 'POST',
      data: { engage, note },
      headers: adminHeaders(),
    });
    return toWork(res.data || res);
  },

  // ================= 跟随流水线 =================

  /** 生成跟随内容(auto_follow 作品 → 三段式文案+三审+存证) */
  async generateFollow(workId: number): Promise<FollowVO> {
    const res = await request<any>({
      url: `/api/blogger/works/${workId}/follow`, method: 'POST',
      headers: adminHeaders(),
    });
    return toFollow(res.data || res);
  },

  /** 跟随内容列表 */
  async listFollows(params?: { bloggerId?: number; status?: string }): Promise<FollowVO[]> {
    const p: string[] = [];
    if (params?.bloggerId) p.push(`bloggerId=${params.bloggerId}`);
    if (params?.status) p.push(`status=${params.status}`);
    const q = p.length ? `?${p.join('&')}` : '';
    const res = await request<any>({ url: `/api/blogger/follows${q}`, headers: adminHeaders() });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map(toFollow);
  },

  /** 三审人工审核(pending → approved/rejected) */
  async reviewFollow(followId: number, approved: boolean): Promise<FollowVO> {
    const res = await request<any>({
      url: `/api/blogger/follows/${followId}/review`,
      method: 'POST',
      data: { approved, reviewer: 'admin' },
      headers: adminHeaders(),
    });
    return toFollow(res.data || res);
  },

  /** 跟随内容入发布队列(approved → queued, 三限校验) */
  async publishFollow(followId: number): Promise<FollowVO> {
    const res = await request<any>({
      url: `/api/blogger/follows/${followId}/publish`,
      method: 'POST',
      data: { publishAt: '' },
      headers: adminHeaders(),
    });
    return toFollow(res.data || res);
  },

  /** 手动触发发布出队(到期 queued → 通道发布+回执) */
  async runPublish(): Promise<{ count: number }> {
    const res = await request<any>({
      url: '/api/blogger/publish/run', method: 'POST',
      headers: adminHeaders(),
    });
    const d = res.data || res;
    return { count: Number(d.count ?? 0) };
  },

  // ================= 报表 =================

  /** 全景报表(池/侦测/跟随/发布/归因/三限) */
  async reportOverview(): Promise<ReportOverviewVO> {
    const res = await request<any>({ url: '/api/blogger/report/overview', headers: adminHeaders() });
    const d = res.data || res;
    return {
      pool: d.pool || { total: 0, active: 0, paused: 0, autoPaused: 0, evolved: 0 },
      works: d.works || { total: 0, autoFollow: 0, manualQueue: 0, passed: 0, discarded: 0, following: 0 },
      follows: d.follows || { total: 0, pending: 0, approved: 0, rejected: 0, queued: 0, published: 0 },
      attribution: d.attribution || { clicks: 0, registered: 0, ordered: 0, gmv: 0 },
      limits: d.limits || { dailyCap: 0, bloggerCooldownHours: 0, followGapHours: 0 },
    };
  },

  /** 单博主归因 */
  async bloggerAttribution(bloggerId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/blogger/report/blogger/${bloggerId}`,
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ================= 学习进化 =================

  /** 批量回流(已发布未回流且过沉淀窗口) */
  async collectLearning(): Promise<{ submitted: number; skipped: number }> {
    const res = await request<any>({
      url: '/api/blogger/learning/collect', method: 'POST',
      headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      submitted: Number(d.submitted ?? 0),
      skipped: Number(d.skipped ?? 0),
    };
  },

  /** 触发一轮 Hedge 学习(反馈不足时 409) */
  async runLearning(): Promise<any> {
    const res = await request<any>({
      url: '/api/blogger/learning/run', method: 'POST',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 回流与学习状态(权重档案/进化榜) */
  async learningStatus(): Promise<LearningStatusVO> {
    const res = await request<any>({ url: '/api/blogger/learning/status', headers: adminHeaders() });
    const d = res.data || res;
    return {
      scorerId: d.scorerId || '',
      feedback: d.feedback || { published: 0, fed: 0, pending: 0, settleHours: 0 },
      weightEvolution: d.weightEvolution || { top: [], bottom: [], autoPaused: [] },
    };
  },
};
