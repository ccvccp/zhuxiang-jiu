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

  // ================= P6g-1 自主引擎看板(第 6 页签) =================
  // 治理开关 / P5 四引擎 / P6 音视频 / P6f 生态 / 干预史
  // 全只读拉取 + 唯一写操作 pause/resume(理由必填——后端校验对齐)

  /** P5 进化透明度看板(治理开关/漏斗/策略/干预史/自愈流水) */
  async autoEvolution(): Promise<any> {
    const res = await request<any>({ url: '/api/blogger/auto/health/evolution', headers: adminHeaders() });
    return res.data || res;
  },

  /** P5 三通道信号统计 */
  async autoSignalsStatus(): Promise<any> {
    const res = await request<any>({ url: '/api/blogger/auto/signals/status', headers: adminHeaders() });
    return res.data || res;
  },

  /** P5 策略库排行(TOP) */
  async autoStrategies(): Promise<any[]> {
    const res = await request<any>({ url: '/api/blogger/auto/strategies', headers: adminHeaders() });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** P6 AV 进化透明度看板(六层漏斗/共鸣度/自愈) */
  async avEvolution(): Promise<any> {
    const res = await request<any>({ url: '/api/blogger/av/health/evolution', headers: adminHeaders() });
    return res.data || res;
  },

  /** P6f 租用账本(计费明细) */
  async rentalLedger(memberId?: number): Promise<any[]> {
    const q = memberId ? `?memberId=${memberId}` : '';
    const res = await request<any>({ url: `/api/blogger/admin/av/rental/ledger${q}`, headers: adminHeaders() });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** P6f 绩效月报列表 */
  async perfReports(): Promise<any[]> {
    const res = await request<any>({ url: '/api/blogger/admin/av/performance/reports', headers: adminHeaders() });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** P6f 可信度主体清单(五因子观测面) */
  async trustSubjects(): Promise<any[]> {
    const res = await request<any>({ url: '/api/blogger/av/trust/subjects', headers: adminHeaders() });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** 治理干预: 人工暂停(理由必填——留痕审计) */
  async pauseAutonomy(reason: string): Promise<any> {
    const res = await request<any>({
      url: '/api/blogger/auto/intervention/pause', method: 'POST',
      data: { reason, operator: 'admin' }, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 治理干预: 显式恢复(永不自动恢复) */
  async resumeAutonomy(): Promise<any> {
    const res = await request<any>({
      url: '/api/blogger/auto/intervention/resume', method: 'POST',
      data: { operator: 'admin' }, headers: adminHeaders(),
    });
    return res.data || res;
  },
};

// ============================================================
// P7 雷达2.0 全网实时价值侦测中枢(40号·第 7 页签)
// /api/radar/* 16 端点 · 五引擎: 感知→评估→预测→响应→自治理
// ============================================================

/** 雷达频道类别字典 */
export const RADAR_CATEGORY_NAME: Record<string, string> = {
  politics: '时政', military: '军事', finance: '财经',
  history: '历史', current: '时事',
};

/** 事件分级字典(L1-L4) */
export const RADAR_GRADE_NAME: Record<string, string> = {
  L1: '紧急高价值', L2: '常规机会', L3: '观察储备', L4: '风险屏蔽',
};

/** 事件生命周期字典 */
export const RADAR_LIFECYCLE_NAME: Record<string, string> = {
  new: '新侦测', rising: '爆发/发酵', peak: '峰值', decay: '衰退',
};

/** L1 任务状态字典 */
export const RADAR_TASK_STATUS_NAME: Record<string, string> = {
  pending: '待人工确认', confirmed: '已确认', rejected: '已否决',
  dispatched: '已派发',
};

export const radarCategoryName = (c: string): string => RADAR_CATEGORY_NAME[c] || c;
export const radarGradeName = (g: string): string => RADAR_GRADE_NAME[g] || g;
export const radarLifecycleName = (l: string): string => RADAR_LIFECYCLE_NAME[l] || l;
export const radarTaskStatusName = (s: string): string => RADAR_TASK_STATUS_NAME[s] || s;

export interface RadarChannelVO {
  channelId: number;
  platform: string;
  name: string;
  displayName: string;
  category: string;
  status: string;
}

export interface RadarEventVO {
  eventId: number;
  title: string;
  channelName: string;
  platform: string;
  category: string;
  heatBase: number;
  crowdEmotion: string;
  emotionDensity: number;
  botFiltered: boolean;
  lifecycle: string;
  grade: string;
  valueScore: number;
  totalSlots: number;
}

export interface RadarScoreVO {
  scoreId: number;
  eventId: number;
  title: string;
  category: string;
  fit: number;
  fitModules: string[];
  safety: number;
  safetyReasons: string[];
  conversion: number;
  valueScore: number;
  grade: string;
  blockedReasons: string[];
  clicks: number;
  registered: number;
  activated: number;
}

export interface RadarTaskVO {
  taskId: number;
  traceId: string;
  eventId: number;
  changeId: number;
  status: string;
  plan: {
    recommendedAngles?: string[];
    bannedPhrasings?: string[];
    materialSuggestions?: Record<string, string[]>;
    hookDirection?: string;
  } | null;
  decisionBasis: {
    grade?: string; valueScore?: number; fit?: number;
    safety?: number; conversion?: number; lifecycle?: string;
    phase?: string; heatBase?: number; crowdEmotion?: string;
    riskNotes?: string[];
  } | null;
  dispatchScriptId: number;
  dispatchExecuted: boolean;
  dispatchError: string;
  confirmedBy: string;
  confirmNote: string;
}

export interface RadarDashboardVO {
  events: { total: number; byGrade: Record<string, number>; byLifecycle: Record<string, number> };
  tasks: { total: number; byStatus: Record<string, number>; violations: number };
  efficiency: any;
  threshold: { currentL1Line: number; cap: number; tightenHistory: number };
}

export const RadarAPI = {
  // ---------- P7a 感知与聚合 ----------

  /** 频道池列表(12 种子频道惰性灌入) */
  async listChannels(): Promise<RadarChannelVO[]> {
    const res = await request<any>({ url: '/api/radar/channels', headers: adminHeaders() });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map((c: any) => ({
      channelId: Number(c.channelId ?? 0),
      platform: c.platform || '',
      name: c.name || '',
      displayName: c.displayName || '',
      category: c.category || '',
      status: c.status || 'active',
    }));
  },

  /** 频道增量入库(类别驱动预期合规分级) */
  async registerChannel(params: {
    platform: string; name: string; displayName: string; category: string;
  }): Promise<RadarChannelVO> {
    const res = await request<any>({
      url: '/api/radar/channels', method: 'POST',
      data: params, headers: adminHeaders(),
    });
    const c = res.data || res;
    return {
      channelId: Number(c.channelId ?? 0),
      platform: c.platform || '',
      name: c.name || '',
      displayName: c.displayName || '',
      category: c.category || '',
      status: c.status || 'active',
    };
  },

  /** 流式采集触发(15min 槽位 mock · 聚类去重+情绪场域) */
  async collectEvents(): Promise<{
    channels: number; collected: number; aggregated: number;
    duplicates: number; botFiltered: number;
  }> {
    const res = await request<any>({
      url: '/api/radar/events/collect', method: 'POST',
      data: {}, headers: adminHeaders(),
    });
    const d = res.data || res;
    return {
      channels: Number(d.channels ?? 0),
      collected: Number(d.collected ?? 0),
      aggregated: Number(d.aggregated ?? 0),
      duplicates: Number(d.duplicates ?? 0),
      botFiltered: Number(d.botFiltered ?? 0),
    };
  },

  /** 事件流查询(热度降序 · 分级/类别过滤) */
  async listEvents(params?: { grade?: string; category?: string }): Promise<RadarEventVO[]> {
    const p: string[] = [];
    if (params?.grade) p.push(`grade=${params.grade}`);
    if (params?.category) p.push(`category=${params.category}`);
    p.push('limit=50');
    const q = p.length ? `?${p.join('&')}` : '';
    const res = await request<any>({ url: `/api/radar/events${q}`, headers: adminHeaders() });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map((e: any) => ({
      eventId: Number(e.eventId ?? 0),
      title: e.title || '',
      channelName: e.channelName || '',
      platform: e.platform || '',
      category: e.category || '',
      heatBase: Number(e.heatBase ?? 0),
      crowdEmotion: e.crowdEmotion || '',
      emotionDensity: Number(e.emotionDensity ?? 0),
      botFiltered: Boolean(e.botFiltered),
      lifecycle: e.lifecycle || 'new',
      grade: e.grade || '',
      valueScore: Number(e.valueScore ?? 0),
      totalSlots: Number(e.totalSlots ?? 0),
    }));
  },

  // ---------- P7b 三维价值评估 ----------

  /** 三维评分批次执行(契合×安全×转化 → L1-L4) */
  async scoreEvents(eventIds?: number[]): Promise<{
    scored: number; grades: Record<string, number>;
  }> {
    const res = await request<any>({
      url: '/api/radar/events/score', method: 'POST',
      data: eventIds?.length ? { eventIds } : {},
      headers: adminHeaders(),
    });
    const d = res.data || res;
    return { scored: Number(d.scored ?? 0), grades: d.grades || {} };
  },

  /** 评分快照查询(三维分明细) */
  async listScores(params?: { grade?: string }): Promise<RadarScoreVO[]> {
    const q = params?.grade ? `?grade=${params.grade}&limit=30` : '?limit=30';
    const res = await request<any>({ url: `/api/radar/scores${q}`, headers: adminHeaders() });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map((s: any) => ({
      scoreId: Number(s.scoreId ?? 0),
      eventId: Number(s.eventId ?? 0),
      title: s.title || '',
      category: s.category || '',
      fit: Number(s.fit ?? 0),
      fitModules: s.fitModules || [],
      safety: Number(s.safety ?? 0),
      safetyReasons: s.safetyReasons || [],
      conversion: Number(s.conversion ?? 0),
      valueScore: Number(s.valueScore ?? 0),
      grade: s.grade || '',
      blockedReasons: s.blockedReasons || [],
      clicks: Number(s.clicks ?? 0),
      registered: Number(s.registered ?? 0),
      activated: Number(s.activated ?? 0),
    }));
  },

  // ---------- P7c 演化预测 ----------

  /** 事件演化预测(生命周期+跨平台关联) */
  async predictEvent(eventId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/radar/events/${eventId}/predict`, method: 'POST',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 合规预演沙盘(L1 专用 · 预案四件套) */
  async rehearseEvent(eventId: number): Promise<any> {
    const res = await request<any>({
      url: `/api/radar/events/${eventId}/rehearse`, method: 'POST',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  // ---------- P7d 自主响应触发 ----------

  /** 任务包补建(延迟创建轨) */
  async ensureTask(eventId: number): Promise<RadarTaskVO> {
    const res = await request<any>({
      url: '/api/radar/tasks', method: 'POST',
      data: { eventId }, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** L1 任务包队列 */
  async listTasks(params?: { status?: string }): Promise<RadarTaskVO[]> {
    const q = params?.status ? `?status=${params.status}` : '?limit=50';
    const res = await request<any>({ url: `/api/radar/tasks${q}`, headers: adminHeaders() });
    const list = res.data || [];
    return (Array.isArray(list) ? list : []).map((t: any) => ({
      taskId: Number(t.taskId ?? 0),
      traceId: t.traceId || '',
      eventId: Number(t.eventId ?? 0),
      changeId: Number(t.changeId ?? 0),
      status: t.status || 'pending',
      plan: t.plan || null,
      decisionBasis: t.decisionBasis || null,
      dispatchScriptId: Number(t.dispatchScriptId ?? 0),
      dispatchExecuted: Boolean(t.dispatchExecuted),
      dispatchError: t.dispatchError || '',
      confirmedBy: t.confirmedBy || '',
      confirmNote: t.confirmNote || '',
    }));
  },

  /** L1 人工确认(46号审批总线轨 · 确认后派发 P6b 创作轨) */
  async confirmTask(taskId: number, approve: boolean, note?: string): Promise<{
    task: RadarTaskVO; dispatched: boolean;
  }> {
    const res = await request<any>({
      url: `/api/radar/tasks/${taskId}/confirm`, method: 'POST',
      data: { approve, reviewer: 'admin', note: note || '' },
      headers: adminHeaders(),
    });
    const d = res.data || res;
    return { task: d.task || d, dispatched: Boolean(d.dispatched) };
  },

  // ---------- P7e 自治理与进化 ----------

  /** 效能周报生成(触发/命中率/误报率/漏报案例库) */
  async generateWeeklyReport(): Promise<any> {
    const res = await request<any>({
      url: '/api/radar/efficiency/weekly', method: 'POST',
      headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 效能记录查询(weekly 周报/threshold 阈值留痕) */
  async listEfficiency(kind?: string): Promise<any[]> {
    const q = kind ? `?kind=${kind}` : '';
    const res = await request<any>({ url: `/api/radar/efficiency${q}`, headers: adminHeaders() });
    const list = res.data || [];
    return Array.isArray(list) ? list : [];
  },

  /** 阈值收紧(只紧不松 · +10 封顶 95) */
  async tightenThreshold(reason?: string): Promise<any> {
    const res = await request<any>({
      url: '/api/radar/threshold/tighten', method: 'POST',
      data: { reason: reason || '' }, headers: adminHeaders(),
    });
    return res.data || res;
  },

  /** 雷达中枢看板(事件/分级/任务/效能/阈值五区) */
  async dashboard(): Promise<RadarDashboardVO> {
    const res = await request<any>({ url: '/api/radar/dashboard', headers: adminHeaders() });
    const d = res.data || res;
    return {
      events: { total: d.events?.total ?? 0, byGrade: d.events?.byGrade || {}, byLifecycle: d.events?.byLifecycle || {} },
      tasks: { total: d.tasks?.total ?? 0, byStatus: d.tasks?.byStatus || {}, violations: d.tasks?.violations ?? 0 },
      efficiency: d.efficiency || null,
      threshold: {
        currentL1Line: d.threshold?.currentL1Line ?? 75,
        cap: d.threshold?.cap ?? 95,
        tightenHistory: d.threshold?.tightenHistory ?? 0,
      },
    };
  },
};
