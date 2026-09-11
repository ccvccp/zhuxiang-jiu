/**
 * 平台流量DV博主模块(40号) · 管理工作台
 * 博主池 → 雷达侦测 → 跟随流水线 → 发布 → 学习进化 全链管理
 * 数据来源: 后端 /api/blogger/*(需 X-Role: admin)
 */
import React, { useState, useEffect, useCallback, useRef } from 'react';
import { View, Text, ScrollView, Input, Picker } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  BloggerAPI, BloggerVO, WorkVO, FollowVO, ReportOverviewVO, LearningStatusVO,
  RadarAPI, RadarEventVO, RadarTaskVO, RadarDashboardVO,
  platformName, domainName, workStatusName, followStatusName,
  radarCategoryName, radarGradeName, radarLifecycleName, radarTaskStatusName,
} from '@/api/blogger';
import { requireLogin } from '@/services/auth-service';

type Tab = 'overview' | 'pool' | 'works' | 'follows' | 'learning' | 'autonomy' | 'radar';

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: '总览' },
  { key: 'pool', label: '博主池' },
  { key: 'works', label: '侦测' },
  { key: 'follows', label: '跟随' },
  { key: 'learning', label: '学习' },
  { key: 'autonomy', label: '自主引擎' },
  { key: 'radar', label: '雷达' },
];

const PLATFORM_KEYS = ['douyin', 'xiaohongshu', 'weibo', 'wechat_channels'];
const DOMAIN_KEYS = ['wine', 'food', 'gift', 'lifestyle'];
const WORK_STATUS_KEYS = ['detected', 'auto_follow', 'manual_queue', 'following', 'passed'];
const FOLLOW_STATUS_KEYS = ['pending', 'approved', 'queued', 'published', 'rejected'];
// P7 雷达分级过滤(全部/L1/L2/L3/L4)
const RADAR_GRADE_KEYS = ['', 'L1', 'L2', 'L3', 'L4'];
const RADAR_GRADE_FILTER_NAME: Record<string, string> = { '': '全部分级' };

const formatNumber = (n: number): string => {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}w`;
  return String(n);
};

const BloggerPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('overview');
  const [loading, setLoading] = useState(false);

  // 总览
  const [overview, setOverview] = useState<ReportOverviewVO | null>(null);
  const [learningStat, setLearningStat] = useState<LearningStatusVO | null>(null);

  // 博主池
  const [bloggers, setBloggers] = useState<BloggerVO[]>([]);
  const [showAddForm, setShowAddForm] = useState(false);
  const [form, setForm] = useState({
    account: '', nickname: '', fansWan: '', engagementRate: '5',
  });
  const [formPlatformIdx, setFormPlatformIdx] = useState(0);
  const [formDomainIdx, setFormDomainIdx] = useState(0);
  const [submitting, setSubmitting] = useState(false);

  // 侦测作品
  const [works, setWorks] = useState<WorkVO[]>([]);
  const [workStatusIdx, setWorkStatusIdx] = useState(1); // 默认自动跟随
  const [scanning, setScanning] = useState(false);

  // 跟随内容
  const [follows, setFollows] = useState<FollowVO[]>([]);
  const [followStatusIdx, setFollowStatusIdx] = useState(0);

  // 自主引擎看板(P6g-1: 治理开关/P5 四引擎/P6 音视频/P6f 生态/干预史)
  const [autoEvo, setAutoEvo] = useState<any>(null);
  const [avEvo, setAvEvo] = useState<any>(null);
  const [signalStat, setSignalStat] = useState<any>(null);
  const [strategyList, setStrategyList] = useState<any[]>([]);
  const [ledgerList, setLedgerList] = useState<any[]>([]);
  const [perfList, setPerfList] = useState<any[]>([]);
  const [trustList, setTrustList] = useState<any[]>([]);
  const [pauseReason, setPauseReason] = useState('');
  const [showPauseForm, setShowPauseForm] = useState(false);
  const [operating, setOperating] = useState(false);

  // 雷达2.0 工作台(P7: 看板/事件流/L1 任务确认)
  const [radarDash, setRadarDash] = useState<RadarDashboardVO | null>(null);
  const [radarEvents, setRadarEvents] = useState<RadarEventVO[]>([]);
  const [radarTasks, setRadarTasks] = useState<RadarTaskVO[]>([]);
  const [radarGradeIdx, setRadarGradeIdx] = useState(0);
  const [radarBusy, setRadarBusy] = useState(false);

  // ---------- 数据加载 ----------
  const loadOverview = useCallback(async () => {
    setLoading(true);
    try {
      const [ov, ls] = await Promise.all([
        BloggerAPI.reportOverview(),
        BloggerAPI.learningStatus().catch(() => null),
      ]);
      setOverview(ov);
      setLearningStat(ls);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadPool = useCallback(async () => {
    setLoading(true);
    try {
      setBloggers(await BloggerAPI.listBloggers());
    } finally {
      setLoading(false);
    }
  }, []);

  const loadWorks = useCallback(async (status?: string) => {
    setLoading(true);
    try {
      setWorks(await BloggerAPI.listWorks(status ? { status } : undefined));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadFollows = useCallback(async (status?: string) => {
    setLoading(true);
    try {
      setFollows(await BloggerAPI.listFollows(status ? { status } : undefined));
    } finally {
      setLoading(false);
    }
  }, []);

  // 自主引擎看板加载(五分区并行拉取; 单源失败不阻断——catch null)
  const loadAutonomy = useCallback(async () => {
    setLoading(true);
    try {
      const [evo, av, sig, strategies, ledger, perf, trust] =
        await Promise.all([
          BloggerAPI.autoEvolution().catch(() => null),
          BloggerAPI.avEvolution().catch(() => null),
          BloggerAPI.autoSignalsStatus().catch(() => null),
          BloggerAPI.autoStrategies().catch(() => []),
          BloggerAPI.rentalLedger().catch(() => []),
          BloggerAPI.perfReports().catch(() => []),
          BloggerAPI.trustSubjects().catch(() => []),
        ]);
      setAutoEvo(evo);
      setAvEvo(av);
      setSignalStat(sig);
      setStrategyList(strategies);
      setLedgerList(ledger);
      setPerfList(perf);
      setTrustList(trust);
    } finally {
      setLoading(false);
    }
  }, []);

  // 雷达工作台加载(看板+事件流+任务队列并行; 单源失败不阻断)
  const loadRadar = useCallback(async (grade?: string) => {
    setLoading(true);
    try {
      const [dash, events, tasks] = await Promise.all([
        RadarAPI.dashboard().catch(() => null),
        RadarAPI.listEvents(grade ? { grade } : undefined).catch(() => []),
        RadarAPI.listTasks().catch(() => []),
      ]);
      setRadarDash(dash);
      setRadarEvents(events);
      setRadarTasks(tasks);
    } finally {
      setLoading(false);
    }
  }, []);

  // 按当前页签刷新数据
  const refresh = useCallback(() => {
    if (!requireLogin()) return;
    if (tab === 'overview' || tab === 'learning') loadOverview();
    else if (tab === 'pool') loadPool();
    else if (tab === 'autonomy') loadAutonomy();
    else if (tab === 'radar') loadRadar(RADAR_GRADE_KEYS[radarGradeIdx]);
    else if (tab === 'works') loadWorks(WORK_STATUS_KEYS[workStatusIdx]);
    else if (tab === 'follows') loadFollows(FOLLOW_STATUS_KEYS[followStatusIdx]);
  }, [tab, loadOverview, loadPool, loadWorks, loadFollows, loadAutonomy, loadRadar, workStatusIdx, followStatusIdx, radarGradeIdx]);

  useEffect(() => { refresh(); }, [refresh]);

  // 页面再现时刷新(登录跳转返回后页面不重挂载, useEffect 不重跑; 首次由上面 useEffect 覆盖)
  const mountedRef = useRef(false);
  useDidShow(() => {
    if (!mountedRef.current) { mountedRef.current = true; return; }
    refresh();
  });

  // ---------- 博主池操作 ----------
  const handleAddBlogger = async () => {
    if (submitting) return;
    if (!form.account.trim() || !form.nickname.trim()) {
      Taro.showToast({ title: '请输入账号与昵称', icon: 'none' });
      return;
    }
    const fans = Number(form.fansWan);
    if (!fans || fans <= 0) {
      Taro.showToast({ title: '粉丝量须大于0(万)', icon: 'none' });
      return;
    }
    const er = Number(form.engagementRate);
    if (Number.isNaN(er) || er < 0 || er > 100) {
      Taro.showToast({ title: '互动率 0-100 (%)', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      await BloggerAPI.createBlogger({
        platform: PLATFORM_KEYS[formPlatformIdx],
        account: form.account.trim(),
        nickname: form.nickname.trim(),
        fansWan: fans,
        domain: DOMAIN_KEYS[formDomainIdx],
        engagementRate: er / 100,
      });
      Taro.showToast({ title: '博主已入池', icon: 'success' });
      setForm({ account: '', nickname: '', fansWan: '', engagementRate: '5' });
      setShowAddForm(false);
      await loadPool();
    } catch (_) {
      // request 层已 toast
    } finally {
      setSubmitting(false);
    }
  };

  const handleToggleBlogger = async (b: BloggerVO) => {
    try {
      if (b.status === 'active') {
        await BloggerAPI.pauseBlogger(b.bloggerId);
        Taro.showToast({ title: '已暂停扫描', icon: 'success' });
      } else {
        await BloggerAPI.activateBlogger(b.bloggerId);
        Taro.showToast({ title: '已恢复', icon: 'success' });
      }
      await loadPool();
    } catch (_) { /* request 层已 toast */ }
  };

  // ---------- 自主引擎治理操作(P6g-1 唯一写口) ----------

  /** 人工暂停(理由必填——留痕审计, 后端校验对齐) */
  const handlePauseAutonomy = async () => {
    if (operating) return;
    const reason = pauseReason.trim();
    if (!reason) {
      Taro.showToast({ title: '暂停理由必填(留痕审计)', icon: 'none' });
      return;
    }
    setOperating(true);
    try {
      await BloggerAPI.pauseAutonomy(reason);
      Taro.showToast({ title: '已暂停全部自主行为', icon: 'success' });
      setPauseReason('');
      setShowPauseForm(false);
      await loadAutonomy();
    } catch (_) { /* request 层已 toast */ } finally {
      setOperating(false);
    }
  };

  /** 显式恢复(永不自动恢复) */
  const handleResumeAutonomy = () => {
    Taro.showModal({
      title: '恢复自主行为',
      content: '确认恢复 AI 全部自主行为? 恢复须显式操作(永不自动恢复)。',
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await BloggerAPI.resumeAutonomy();
          Taro.showToast({ title: '已恢复', icon: 'success' });
          await loadAutonomy();
        } catch (_) { /* request 层已 toast */ }
      },
    });
  };

  // ---------- 侦测操作 ----------
  const handleScan = async () => {
    if (scanning) return;
    setScanning(true);
    try {
      const r = await BloggerAPI.radarScan();
      Taro.showModal({
        title: '扫描完成',
        content: `本轮发现 ${r.works.length} 件作品, 决策 ${r.decisions.length} 条(≥70 自动跟随 / 50-70 人工确认 / <50 跳过)。`,
        showCancel: false,
      });
      await loadWorks(WORK_STATUS_KEYS[workStatusIdx]);
    } catch (_) { /* request 层已 toast */ } finally {
      setScanning(false);
    }
  };

  const handleManualDecide = (w: WorkVO, engage: boolean) => {
    Taro.showModal({
      title: engage ? '确认跟随' : '放弃作品',
      content: engage
        ? `确认跟随《${w.title}》? 将生成跟随内容并进入三审。`
        : `放弃《${w.title}》? 放弃后留痕不可恢复。`,
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await BloggerAPI.manualDecide(w.workId, engage);
          Taro.showToast({ title: engage ? '已确认跟随' : '已放弃', icon: 'success' });
          await loadWorks(WORK_STATUS_KEYS[workStatusIdx]);
        } catch (_) { /* request 层已 toast */ }
      },
    });
  };

  const handleGenerateFollow = async (w: WorkVO) => {
    try {
      const f = await BloggerAPI.generateFollow(w.workId);
      Taro.showModal({
        title: '跟随内容已生成',
        content: `标题: ${f.title}\n合规分: ${f.complianceScore}\n状态: ${followStatusName(f.status)}`,
        showCancel: false,
      });
      await loadWorks(WORK_STATUS_KEYS[workStatusIdx]);
    } catch (_) { /* request 层已 toast */ }
  };

  // ---------- 跟随操作 ----------
  const handleReview = (f: FollowVO, approved: boolean) => {
    Taro.showModal({
      title: approved ? '通过三审' : '拒绝发布',
      content: `跟随内容《${f.title}》${approved ? '通过后可入发布队列' : '拒绝后留痕归档'}。`,
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await BloggerAPI.reviewFollow(f.followId, approved);
          Taro.showToast({ title: approved ? '已通过' : '已拒绝', icon: 'success' });
          await loadFollows(FOLLOW_STATUS_KEYS[followStatusIdx]);
        } catch (_) { /* request 层已 toast */ }
      },
    });
  };

  const handlePublish = async (f: FollowVO) => {
    try {
      await BloggerAPI.publishFollow(f.followId);
      Taro.showToast({ title: '已入发布队列', icon: 'success' });
      await loadFollows(FOLLOW_STATUS_KEYS[followStatusIdx]);
    } catch (_) { /* request 层已 toast */ }
  };

  const handleRunPublish = async () => {
    try {
      const r = await BloggerAPI.runPublish();
      Taro.showToast({ title: `已发布 ${r.count} 条`, icon: 'success' });
      await loadFollows(FOLLOW_STATUS_KEYS[followStatusIdx]);
    } catch (_) { /* request 层已 toast */ }
  };

  // ---------- 学习操作 ----------
  const handleCollectLearning = async () => {
    try {
      const r = await BloggerAPI.collectLearning();
      Taro.showToast({ title: `回流 ${r.submitted} 条`, icon: 'success' });
      await loadOverview();
    } catch (_) { /* request 层已 toast */ }
  };

  const handleRunLearning = async () => {
    try {
      await BloggerAPI.runLearning();
      Taro.showToast({ title: '学习轮次完成', icon: 'success' });
      await loadOverview();
    } catch (_) { /* request 层已 toast(409=反馈不足) */ }
  };

  // ---------- 雷达2.0 操作(P7 五引擎管理面) ----------

  /** 流式采集(15min 槽位 · 聚类去重+情绪场域+刷量过滤) */
  const handleRadarCollect = async () => {
    if (radarBusy) return;
    setRadarBusy(true);
    try {
      const r = await RadarAPI.collectEvents();
      Taro.showModal({
        title: '采集完成',
        content: `${r.channels} 频道扫描: 采集 ${r.collected} 条 · 聚合 ${r.aggregated} 条 · 幂等跳过 ${r.duplicates} 条(同槽位) · 刷量过滤 ${r.botFiltered} 条。`,
        showCancel: false,
      });
      await loadRadar(RADAR_GRADE_KEYS[radarGradeIdx]);
    } catch (_) { /* request 层已 toast */ } finally {
      setRadarBusy(false);
    }
  };

  /** 三维评分批次(契合×安全×转化 → L1-L4; 安全<0.6 硬闸 L4) */
  const handleRadarScore = async () => {
    if (radarBusy) return;
    setRadarBusy(true);
    try {
      const r = await RadarAPI.scoreEvents();
      const g = r.grades || {};
      Taro.showModal({
        title: `评分完成(${r.scored} 条)`,
        content: `L1 紧急 ${g.L1 || 0} · L2 常规 ${g.L2 || 0} · L3 观察 ${g.L3 || 0} · L4 屏蔽 ${g.L4 || 0}(政治军事类硬闸拦截, 屏蔽原因留痕备查)。`,
        showCancel: false,
      });
      await loadRadar(RADAR_GRADE_KEYS[radarGradeIdx]);
    } catch (_) { /* request 层已 toast */ } finally {
      setRadarBusy(false);
    }
  };

  /** 事件演化预测(生命周期分段+跨平台关联) */
  const handleRadarPredict = (e: RadarEventVO) => {
    Taro.showLoading({ title: '预测中' });
    RadarAPI.predictEvent(e.eventId)
      .then((p: any) => {
        Taro.hideLoading();
        const cross = p.crossPlatform || {};
        const opp = cross.opportunity || {};
        const hype = cross.coordinatedHype || {};
        Taro.showModal({
          title: `${p.phase || '新侦测'} · ${radarLifecycleName(p.lifecycle || 'new')}`,
          content: [
            (p.signals || []).join('; ') || '无预警信号',
            (p.advice || '').slice(0, 30),
            opp.windows ? `机会窗: ${opp.windows.join('/')}(沉默平台差异化切入)` : '',
            hype.suspected ? '疑似操纵性流量(情绪已降权)' : '',
          ].filter(Boolean).join('\n'),
          showCancel: false,
        });
      })
      .catch(() => Taro.hideLoading());
  };

  /** L1 任务人工确认(46号审批总线轨 · 确认后派发 P6b 创作轨) */
  const handleRadarConfirm = (t: RadarTaskVO, approve: boolean) => {
    const basis = t.decisionBasis || {};
    Taro.showModal({
      title: approve ? '确认 L1 高价值任务' : '否决任务',
      content: approve
        ? `《${(t.plan as any)?.recommendedAngles?.[0]?.slice(0, 24) || '雷达任务'}》\n`
          + `价值分 ${basis.valueScore ?? '-'} · 契合 ${basis.fit ?? '-'} · 安全 ${basis.safety ?? '-'}\n`
          + `确认后将派发 P6b 创作轨生成脚本。`
        : `否决溯源 ${t.traceId}? 决策回流效能周报(误报率)。`,
      success: async (res) => {
        if (!res.confirm) return;
        try {
          const r = await RadarAPI.confirmTask(t.taskId, approve);
          Taro.showModal({
            title: r.dispatched ? '已派发创作轨' : '已确认',
            content: r.dispatched
              ? `脚本 #${r.task?.dispatchScriptId ?? 0} 已生成(P6b 五层合规内生)。`
              : `任务 ${approve ? '已确认(派发失败留痕待重试)' : '已否决(46号留痕)'}。`,
            showCancel: false,
          });
          await loadRadar(RADAR_GRADE_KEYS[radarGradeIdx]);
        } catch (_) { /* request 层已 toast */ }
      },
    });
  };

  /** 效能周报生成(触发/命中率/误报率/漏报案例库) */
  const handleRadarWeekly = async () => {
    if (radarBusy) return;
    setRadarBusy(true);
    try {
      const w: any = await RadarAPI.generateWeeklyReport();
      Taro.showModal({
        title: `周报 #${w.reportId}`,
        content: `触发 ${w.triggered ?? 0} · 派发 ${w.dispatched ?? 0} · 命中率 ${(Number(w.hitRate ?? 0) * 100).toFixed(0)}% · 误报率 ${(Number(w.falsePositiveRate ?? 0) * 100).toFixed(0)}% · 漏报案例 ${(w.missedCases || []).length} 条。`,
        showCancel: false,
      });
      await loadRadar(RADAR_GRADE_KEYS[radarGradeIdx]);
    } catch (_) { /* request 层已 toast */ } finally {
      setRadarBusy(false);
    }
  };

  // ---------- 渲染 ----------
  const renderStat = (label: string, value: string | number, sub?: string) => (
    <View className={styles.statItem}>
      <View className={styles.statValue}>{value}</View>
      <View className={styles.statLabel}>{label}</View>
      {sub ? <View className={styles.statSub}>{sub}</View> : null}
    </View>
  );

  return (
    <View className={styles.page}>
      <NavBar title="DV博主流量" />
      {/* 顶部 Tabs */}
      <View className={styles.tabBar}>
        {TABS.map(t => (
          <View
            key={t.key}
            className={`${styles.tabItem} ${tab === t.key ? styles.tabItemActive : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </View>
        ))}
      </View>

      <ScrollView className={styles.scrollView} scrollY enableFlex>
        {loading ? <View className={styles.empty}>加载中...</View> : null}

        {/* ============ 总览 ============ */}
        {tab === 'overview' && !loading && (
          <>
            <View className={styles.card}>
              <View className={styles.cardTitle}>博主池</View>
              <View className={styles.statGrid}>
                {renderStat('总博主', overview?.pool.total ?? 0)}
                {renderStat('在池', overview?.pool.active ?? 0)}
                {renderStat('暂停', overview?.pool.paused ?? 0)}
                {renderStat('自动止损', overview?.pool.autoPaused ?? 0)}
                {renderStat('已进化', overview?.pool.evolved ?? 0, '权重已调整')}
              </View>
            </View>
            <View className={styles.card}>
              <View className={styles.cardTitle}>侦测与跟随漏斗</View>
              <View className={styles.statGrid}>
                {renderStat('侦测作品', overview?.works.total ?? 0)}
                {renderStat('自动跟随', overview?.works.autoFollow ?? 0)}
                {renderStat('待人工', overview?.works.manualQueue ?? 0)}
                {renderStat('跟随内容', overview?.follows.total ?? 0)}
                {renderStat('已发布', overview?.follows.published ?? 0)}
              </View>
            </View>
            <View className={styles.card}>
              <View className={styles.cardTitle}>引流归因</View>
              <View className={styles.statGrid}>
                {renderStat('引流量', formatNumber(overview?.attribution.clicks ?? 0), '短码点击')}
                {renderStat('注册', overview?.attribution.registered ?? 0)}
                {renderStat('下单', overview?.attribution.ordered ?? 0)}
                {renderStat('GMV', `¥${overview?.attribution.gmv ?? 0}`)}
              </View>
            </View>
            <View className={styles.noteCard}>
              <View className={styles.noteTitle}>发布三限(护栏)</View>
              <View className={styles.noteLine}>
                单日上限 {overview?.limits.dailyCap ?? '-'} 条 · 博主冷却 {overview?.limits.bloggerCooldownHours ?? '-'}h · 全站间隔 {overview?.limits.followGapHours ?? '-'}h
              </View>
            </View>
            {learningStat ? (
              <View className={styles.noteCard}>
                <View className={styles.noteTitle}>学习回流</View>
                <View className={styles.noteLine}>
                  已发布 {learningStat.feedback.published} 条 · 已回流 {learningStat.feedback.fed} 条 · 待沉淀 {learningStat.feedback.pending} 条(窗口 {learningStat.feedback.settleHours}h)
                </View>
              </View>
            ) : null}
          </>
        )}

        {/* ============ 博主池 ============ */}
        {tab === 'pool' && !loading && (
          <>
            <View className={styles.actionRow}>
              <View className={styles.primaryBtn} onClick={() => setShowAddForm(!showAddForm)}>
                {showAddForm ? '收起表单' : '+ 新增博主'}
              </View>
            </View>

            {showAddForm ? (
              <View className={styles.card}>
                <View className={styles.cardTitle}>新增博主(领域准入)</View>
                <View className={styles.pickerRow}>
                  <Text className={styles.pickerLabel}>平台</Text>
                  <Picker
                    mode="selector"
                    range={PLATFORM_KEYS.map(platformName)}
                    value={formPlatformIdx}
                    onChange={e => setFormPlatformIdx(Number(e.detail.value))}
                  >
                    <View className={styles.pickerValue}>
                      {platformName(PLATFORM_KEYS[formPlatformIdx])} ▾
                    </View>
                  </Picker>
                </View>
                <View className={styles.inputRow}>
                  <Text className={styles.inputPrefix}>账号</Text>
                  <Input
                    className={styles.input}
                    placeholder="平台账号 ID"
                    value={form.account}
                    onInput={e => setForm({ ...form, account: e.detail.value })}
                  />
                </View>
                <View className={styles.inputRow}>
                  <Text className={styles.inputPrefix}>昵称</Text>
                  <Input
                    className={styles.input}
                    placeholder="博主昵称"
                    value={form.nickname}
                    onInput={e => setForm({ ...form, nickname: e.detail.value })}
                  />
                </View>
                <View className={styles.pickerRow}>
                  <Text className={styles.pickerLabel}>领域</Text>
                  <Picker
                    mode="selector"
                    range={DOMAIN_KEYS.map(domainName)}
                    value={formDomainIdx}
                    onChange={e => setFormDomainIdx(Number(e.detail.value))}
                  >
                    <View className={styles.pickerValue}>
                      {domainName(DOMAIN_KEYS[formDomainIdx])} ▾
                    </View>
                  </Picker>
                </View>
                <View className={styles.inputRow}>
                  <Text className={styles.inputPrefix}>粉丝</Text>
                  <Input
                    className={styles.input}
                    type="digit"
                    placeholder="粉丝量(万)"
                    value={form.fansWan}
                    onInput={e => setForm({ ...form, fansWan: e.detail.value })}
                  />
                  <Text className={styles.inputSuffix}>万</Text>
                </View>
                <View className={styles.inputRow}>
                  <Text className={styles.inputPrefix}>互动率</Text>
                  <Input
                    className={styles.input}
                    type="digit"
                    placeholder="如 5"
                    value={form.engagementRate}
                    onInput={e => setForm({ ...form, engagementRate: e.detail.value })}
                  />
                  <Text className={styles.inputSuffix}>%</Text>
                </View>
                <View className={styles.sheetBtn} onClick={handleAddBlogger}>
                  {submitting ? '提交中...' : '入池'}
                </View>
                <View className={styles.sheetDesc}>仅收酒/美食/礼品/生活领域博主</View>
              </View>
            ) : null}

            {bloggers.length === 0 ? (
              <View className={styles.empty}>
                <View className={styles.emptyIcon}>📡</View>
                暂无博主, 请先新增
              </View>
            ) : bloggers.map(b => (
              <View className={styles.card} key={b.bloggerId}>
                <View className={styles.itemTop}>
                  <View className={styles.itemName}>{b.nickname}</View>
                  <View
                    className={`${styles.badge} ${b.status === 'active' ? styles.badgeOk : styles.badgeWarn}`}
                  >
                    {b.status === 'active' ? '在池' : b.pausedReason === 'auto_loss_cut' ? '止损' : '暂停'}
                  </View>
                </View>
                <View className={styles.itemMeta}>
                  {platformName(b.platform)} · {domainName(b.domain)} · {b.fansWan}w 粉丝
                </View>
                <View className={styles.itemMeta}>
                  权重 {b.weight.toFixed(1)}(基线 {b.weightBase.toFixed(1)}{b.weightAdjust >= 0 ? '+' : ''}{b.weightAdjust.toFixed(1)} 进化) · 互动率 {(b.engagementRate * 100).toFixed(1)}%
                </View>
                <View className={styles.actionRow}>
                  <View
                    className={`${styles.miniBtn} ${b.status === 'active' ? styles.miniBtnWarn : ''}`}
                    onClick={() => handleToggleBlogger(b)}
                  >
                    {b.status === 'active' ? '暂停扫描' : '恢复扫描'}
                  </View>
                </View>
              </View>
            ))}
          </>
        )}

        {/* ============ 侦测作品 ============ */}
        {tab === 'works' && !loading && (
          <>
            <View className={styles.actionRow}>
              <View className={styles.pickerRow} style={{ flex: 1 }}>
                <Picker
                  mode="selector"
                  range={WORK_STATUS_KEYS.map(workStatusName)}
                  value={workStatusIdx}
                  onChange={e => setWorkStatusIdx(Number(e.detail.value))}
                >
                  <View className={styles.pickerValue}>
                    {workStatusName(WORK_STATUS_KEYS[workStatusIdx])} ▾
                  </View>
                </Picker>
              </View>
              <View className={styles.primaryBtn} onClick={handleScan}>
                {scanning ? '扫描中...' : '雷达扫描'}
              </View>
            </View>

            {works.length === 0 ? (
              <View className={styles.empty}>
                <View className={styles.emptyIcon}>🎯</View>
                暂无{workStatusName(WORK_STATUS_KEYS[workStatusIdx])}作品, 可触发雷达扫描
              </View>
            ) : works.map(w => (
              <View className={styles.card} key={w.workId}>
                <View className={styles.itemTop}>
                  <View className={styles.itemName}>{w.title}</View>
                  <View className={styles.scoreBadge}>{w.score}分</View>
                </View>
                <View className={styles.itemMeta}>{w.summary}</View>
                <View className={styles.itemMeta}>
                  {platformName(w.platform)} · 赞 {formatNumber(w.likes)} · 评 {formatNumber(w.comments)} · 转 {formatNumber(w.shares)}
                </View>
                <View className={styles.actionRow}>
                  {w.status === 'manual_queue' ? (
                    <>
                      <View className={styles.miniBtn} onClick={() => handleManualDecide(w, true)}>确认跟随</View>
                      <View className={`${styles.miniBtn} ${styles.miniBtnWarn}`} onClick={() => handleManualDecide(w, false)}>放弃</View>
                    </>
                  ) : null}
                  {w.status === 'auto_follow' ? (
                    <View className={styles.miniBtn} onClick={() => handleGenerateFollow(w)}>生成跟随内容</View>
                  ) : null}
                </View>
              </View>
            ))}
          </>
        )}

        {/* ============ 跟随内容 ============ */}
        {tab === 'follows' && !loading && (
          <>
            <View className={styles.actionRow}>
              <View className={styles.pickerRow} style={{ flex: 1 }}>
                <Picker
                  mode="selector"
                  range={FOLLOW_STATUS_KEYS.map(followStatusName)}
                  value={followStatusIdx}
                  onChange={e => setFollowStatusIdx(Number(e.detail.value))}
                >
                  <View className={styles.pickerValue}>
                    {followStatusName(FOLLOW_STATUS_KEYS[followStatusIdx])} ▾
                  </View>
                </Picker>
              </View>
              <View className={styles.primaryBtn} onClick={handleRunPublish}>发布出队</View>
            </View>

            {follows.length === 0 ? (
              <View className={styles.empty}>
                <View className={styles.emptyIcon}>📝</View>
                暂无{followStatusName(FOLLOW_STATUS_KEYS[followStatusIdx])}内容
              </View>
            ) : follows.map(f => (
              <View className={styles.card} key={f.followId}>
                <View className={styles.itemTop}>
                  <View className={styles.itemName}>{f.title}</View>
                  <View className={`${styles.badge} ${f.status === 'published' || f.status === 'approved' ? styles.badgeOk : styles.badgeWarn}`}>
                    {followStatusName(f.status)}
                  </View>
                </View>
                <View className={styles.itemMeta}>
                  #{f.followId} · {platformName(f.platform)} · 合规 {f.complianceScore} 分 · 短码 {f.shortCode || '-'}
                </View>
                {f.hardFail?.length ? (
                  <View className={styles.riskLine}>硬拒: {f.hardFail.join(' / ')}</View>
                ) : null}
                {f.learningFed && f.learningMetrics ? (
                  <View className={styles.learnLine}>
                    回流: 引流 {f.learningMetrics.clicks} · 注册 {f.learningMetrics.registrations} · GMV ¥{f.learningMetrics.gmv} · 奖励 {f.learningMetrics.reward}
                  </View>
                ) : null}
                <View className={styles.actionRow}>
                  {f.status === 'pending' ? (
                    <>
                      <View className={styles.miniBtn} onClick={() => handleReview(f, true)}>通过三审</View>
                      <View className={`${styles.miniBtn} ${styles.miniBtnWarn}`} onClick={() => handleReview(f, false)}>拒绝</View>
                    </>
                  ) : null}
                  {f.status === 'approved' ? (
                    <View className={styles.miniBtn} onClick={() => handlePublish(f)}>入发布队列</View>
                  ) : null}
                </View>
              </View>
            ))}
          </>
        )}

        {/* ============ 学习进化 ============ */}
        {tab === 'learning' && !loading && (
          <>
            <View className={styles.actionRow}>
              <View className={styles.primaryBtn} onClick={handleCollectLearning}>批量回流</View>
              <View className={styles.primaryBtn} onClick={handleRunLearning}>触发学习</View>
            </View>
            <View className={styles.card}>
              <View className={styles.cardTitle}>回流状态</View>
              {learningStat ? (
                <View className={styles.statGrid}>
                  {renderStat('已发布', learningStat.feedback.published)}
                  {renderStat('已回流', learningStat.feedback.fed)}
                  {renderStat('待沉淀', learningStat.feedback.pending, `窗口 ${learningStat.feedback.settleHours}h`)}
                </View>
              ) : <View className={styles.empty}>暂无数据</View>}
            </View>
            {learningStat?.weightEvolution?.top?.length ? (
              <View className={styles.card}>
                <View className={styles.cardTitle}>进化榜(权重调整 TOP5)</View>
                {learningStat.weightEvolution.top.map((e, i) => (
                  <View className={styles.rankRow} key={e.bloggerId}>
                    <Text className={styles.rankNo}>{i + 1}</Text>
                    <Text className={styles.rankName}>{e.nickname || `#${e.bloggerId}`}</Text>
                    <Text className={`${styles.rankDelta} ${e.weightAdjust >= 0 ? styles.rankUp : styles.rankDown}`}>
                      {e.weightAdjust >= 0 ? '+' : ''}{e.weightAdjust?.toFixed(1)}
                    </Text>
                  </View>
                ))}
              </View>
            ) : null}
            <View className={styles.noteCard}>
              <View className={styles.noteTitle}>学习机制</View>
              <View className={styles.noteLine}>
                已发布内容过 24h 沉淀窗口后可回流效果(引流量/注册/下单), 反馈驱动第21档案权重(Hedge)与博主权重自进化; 零引流连续多轮自动止损出池。
              </View>
            </View>
          </>
        )}

        {/* ============ 自主引擎看板(P6g-1) ============ */}
        {tab === 'autonomy' && !loading && (
          <>
            {/* 分区1: 治理开关(唯一写口) */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>治理开关</View>
              {autoEvo?.autonomy ? (
                <View className={styles.statGrid}>
                  <View className={styles.statItem}>
                    <Text className={styles.statValue}>
                      {autoEvo.autonomy.paused ? '已暂停' : '运行中'}
                    </Text>
                    <Text className={styles.statLabel}>自主行为</Text>
                  </View>
                </View>
              ) : <View className={styles.empty}>暂无数据</View>}
              {autoEvo?.autonomy?.reason ? (
                <View className={styles.noteLine} style={{ marginTop: '8rpx' }}>
                  暂停理由: {autoEvo.autonomy.reason}
                </View>
              ) : null}
              {!autoEvo?.autonomy?.paused ? (
                <>
                  <View className={styles.actionRow}>
                    <View
                      className={`${styles.miniBtn} ${styles.miniBtnWarn}`}
                      onClick={() => setShowPauseForm(true)}
                    >暂停自主行为</View>
                  </View>
                  {showPauseForm ? (
                    <View className={styles.card} style={{ marginTop: '12rpx' }}>
                      <View className={styles.cardTitle}>暂停理由(必填·留痕审计)</View>
                      <Input
                        className={styles.input}
                        value={pauseReason}
                        onInput={(e) => setPauseReason(e.detail.value)}
                        placeholder="例: 例行巡检 / 舆情处置"
                        maxlength={200}
                      />
                      <View className={styles.actionRow} style={{ marginTop: '12rpx' }}>
                        <View className={styles.primaryBtn} onClick={handlePauseAutonomy}>
                          {operating ? '提交中…' : '确认暂停'}
                        </View>
                      </View>
                    </View>
                  ) : null}
                </>
              ) : (
                <View className={styles.actionRow}>
                  <View className={styles.primaryBtn} onClick={handleResumeAutonomy}>
                    恢复自主行为
                  </View>
                </View>
              )}
            </View>

            {/* 分区2: P5 四引擎(信号/策略) */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>P5 引擎(信号·策略)</View>
              {signalStat ? (
                <View className={styles.statGrid}>
                  {renderStat('信号总数', signalStat.total ?? 0)}
                  {renderStat('未消费', signalStat.unconsumed ?? 0)}
                </View>
              ) : <View className={styles.empty}>暂无数据</View>}
              {strategyList.length ? (
                <View style={{ marginTop: '12rpx' }}>
                  <View className={styles.noteTitle}>策略排行 TOP</View>
                  {strategyList.slice(0, 5).map((s: any) => (
                    <View className={styles.rankRow} key={s.strategyId}>
                      <Text className={styles.rankName}>
                        {s.displayName || s.name}
                      </Text>
                      <Text className={styles.rankDelta}>
                        胜{s.winCount ?? 0} · 均{(s.avgReward ?? 0).toFixed(2)}
                      </Text>
                    </View>
                  ))}
                </View>
              ) : null}
            </View>

            {/* 分区3: P6 音视频(六层漏斗/共鸣) */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>P6 音视频(六层漏斗)</View>
              {avEvo?.funnel ? (
                <View className={styles.statGrid}>
                  {renderStat('已发布', avEvo.funnel.published ?? 0)}
                  {renderStat('有曝光', avEvo.funnel.withExposure ?? 0)}
                  {renderStat('有完播', avEvo.funnel.withCompletion ?? 0)}
                  {renderStat('有点击', avEvo.funnel.withClicks ?? 0)}
                  {renderStat('有注册', avEvo.funnel.withRegistered ?? 0)}
                  {renderStat('有激活', avEvo.funnel.withActivated ?? 0)}
                  {renderStat('有首单', avEvo.funnel.withOrdered ?? 0)}
                </View>
              ) : <View className={styles.empty}>暂无数据</View>}
              {avEvo?.resonance ? (
                <View className={styles.noteLine} style={{ marginTop: '8rpx' }}>
                  平均共鸣 {avEvo.resonance.avgResonance ?? 0} · 低共鸣预警
                  {' '}{(avEvo.resonance.lowResonanceWorks || []).length} 条
                </View>
              ) : null}
            </View>

            {/* 分区4: P6f 生态(租用/绩效/可信度) */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>P6f 生态</View>
              <View className={styles.statGrid}>
                {renderStat('租用计费条目', ledgerList.length)}
                {renderStat('绩效月报', perfList.length)}
                {renderStat('可信度主体', trustList.length)}
              </View>
              {trustList.length ? (
                <View style={{ marginTop: '12rpx' }}>
                  <View className={styles.noteTitle}>可信度主体</View>
                  {trustList.slice(0, 5).map((t: any) => (
                    <View className={styles.rankRow} key={`${t.subjectType}-${t.subjectId}`}>
                      <Text className={styles.rankName}>{t.name}</Text>
                      <Text className={styles.rankDelta}>
                        {t.subjectType === 'persona' ? '人设' : t.subjectType === 'creator' ? '创作者' : '会员'}
                      </Text>
                    </View>
                  ))}
                </View>
              ) : null}
            </View>

            {/* 分区5: 干预史(只读时间线) */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>干预史</View>
              {(autoEvo?.interventions || []).length ? (
                (autoEvo.interventions).slice(0, 10).map((i: any) => (
                  <View className={styles.noteLine} key={i.interventionId}>
                    [{i.kind}] {i.note || ''} — {i.operator || 'admin'}
                    {' '}{(i.createdAt || '').slice(0, 19)}
                  </View>
                ))
              ) : <View className={styles.empty}>暂无干预记录</View>}
            </View>

            <View className={styles.noteCard}>
              <View className={styles.noteTitle}>治理机制</View>
              <View className={styles.noteLine}>
                暂停即时冻结全部自主行为(学习/创作/发布/自愈/租用受控写),
                观测面不中断; 恢复须显式操作(永不自动); 干预留痕审计。
              </View>
            </View>
          </>
        )}

        {/* ============ 雷达2.0 工作台(P7) ============ */}
        {tab === 'radar' && !loading && (
          <>
            {/* 分区1: 中枢看板五区 */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>雷达中枢看板</View>
              <View className={styles.statGrid}>
                {renderStat('事件总数', radarDash?.events.total ?? 0)}
                {renderStat('L1 紧急', radarDash?.events.byGrade.L1 ?? 0, '任务包待确认')}
                {renderStat('L4 屏蔽', radarDash?.events.byGrade.L4 ?? 0, '留痕备查')}
                {renderStat('任务总数', radarDash?.tasks.total ?? 0)}
                {renderStat('违规标记', radarDash?.tasks.violations ?? 0)}
              </View>
            </View>
            <View className={styles.noteCard}>
              <View className={styles.noteTitle}>阈值状态(只紧不松)</View>
              <View className={styles.noteLine}>
                当前 L1 价值线 {radarDash?.threshold.currentL1Line ?? 75} · 封顶 {radarDash?.threshold.cap ?? 95} · 收紧史 {radarDash?.threshold.tightenHistory ?? 0} 次
                (违规率 ×3 且样本≥20 自动 +10; 放宽须 46号建议书)
              </View>
            </View>

            {/* 分区2: 引擎操作 */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>五引擎操作</View>
              <View className={styles.actionRow}>
                <View className={styles.primaryBtn} onClick={handleRadarCollect}>
                  {radarBusy ? '采集中...' : '① 采集事件流'}
                </View>
                <View className={styles.miniBtn} onClick={handleRadarScore}>
                  {radarBusy ? '评分中...' : '② 三维评分'}
                </View>
              </View>
              <View className={styles.actionRow}>
                <View className={styles.miniBtn} onClick={handleRadarWeekly}>
                  生成效能周报
                </View>
              </View>
              <View className={styles.noteLine}>
                采集(15min 槽位·聚类去重·情绪场域) → 评分(契合×安全硬闸×转化→L1-L4)
                → 预测(点击事件查看) → 任务确认(派发 P6b 创作轨) → 周报(归因闭环)
              </View>
            </View>

            {/* 分区3: L1 任务包队列(46号人工确认轨) */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>L1 任务包(人工确认轨)</View>
              {radarTasks.length ? radarTasks.slice(0, 10).map(t => (
                <View className={styles.noteCard} key={t.taskId} style={{ marginBottom: '8px' }}>
                  <View className={styles.noteTitle}>
                    {t.traceId} · {radarTaskStatusName(t.status)}
                    {t.dispatchExecuted ? `(脚本 #${t.dispatchScriptId})` : ''}
                  </View>
                  {t.decisionBasis ? (
                    <View className={styles.noteLine}>
                      价值 {t.decisionBasis.valueScore ?? '-'} · 契合 {t.decisionBasis.fit ?? '-'} ·
                      安全 {t.decisionBasis.safety ?? '-'} · 转化 {t.decisionBasis.conversion ?? '-'} ·
                      {radarLifecycleName(t.decisionBasis.lifecycle || 'new')}
                    </View>
                  ) : null}
                  {t.dispatchError ? (
                    <View className={styles.riskLine}>派发留痕: {t.dispatchError.slice(0, 40)}</View>
                  ) : null}
                  {t.status === 'pending' ? (
                    <View className={styles.actionRow}>
                      <View className={styles.primaryBtn} onClick={() => handleRadarConfirm(t, true)}>
                        确认并派发
                      </View>
                      <View className={styles.miniBtn} onClick={() => handleRadarConfirm(t, false)}>
                        否决
                      </View>
                    </View>
                  ) : null}
                </View>
              )) : <View className={styles.empty}>暂无任务(L1 事件经预演后自动入队)</View>}
            </View>

            {/* 分区4: 事件流(分级过滤) */}
            <View className={styles.card}>
              <View className={styles.pickerRow}>
                <Text className={styles.pickerLabel}>分级</Text>
                <Picker
                  mode="selector"
                  range={RADAR_GRADE_KEYS.map(g => RADAR_GRADE_FILTER_NAME[g] || (g ? `${g} ${radarGradeName(g)}` : '全部分级'))}
                  value={radarGradeIdx}
                  onChange={e => {
                    const idx = Number(e.detail.value);
                    setRadarGradeIdx(idx);
                    loadRadar(RADAR_GRADE_KEYS[idx]);
                  }}
                >
                  <View className={styles.pickerValue}>
                    {RADAR_GRADE_KEYS[radarGradeIdx]
                      ? `${RADAR_GRADE_KEYS[radarGradeIdx]} ${radarGradeName(RADAR_GRADE_KEYS[radarGradeIdx])}`
                      : '全部分级'} ▾
                  </View>
                </Picker>
              </View>
              {radarEvents.length ? radarEvents.map(e => (
                <View
                  className={styles.noteCard}
                  key={e.eventId}
                  style={{ marginBottom: '8px' }}
                  onClick={() => handleRadarPredict(e)}
                >
                  <View className={styles.noteTitle}>
                    {e.title}
                    <Text className={styles.badge} style={{ marginLeft: '6px' }}>
                      {e.grade || '未评分'}
                    </Text>
                  </View>
                  <View className={styles.noteLine}>
                    {radarCategoryName(e.category)} · {platformName(e.platform)} ·
                    热度 {formatNumber(e.heatBase)} · {radarLifecycleName(e.lifecycle)}
                    {e.grade ? ` · 价值分 ${e.valueScore}` : ''}
                    {e.botFiltered ? ' · 刷量过滤' : ''}
                  </View>
                  {e.grade === 'L4' ? (
                    <View className={styles.riskLine}>风险屏蔽(类别硬映射/政策词, 留痕备查)</View>
                  ) : null}
                  <View className={styles.noteLine} style={{ opacity: 0.55 }}>
                    点击查看演化预测(生命周期/机会窗/联动造势)
                  </View>
                </View>
              )) : (
                <View className={styles.empty}>
                  暂无事件(空库或该分级无事件——先执行「① 采集事件流」)
                </View>
              )}
            </View>

            <View className={styles.noteCard}>
              <View className={styles.noteTitle}>雷达红线(宪法域)</View>
              <View className={styles.noteLine}>
                政治军事类→L4 禁区 · 安全系数&lt;0.6 无条件拦截(不进乘法) ·
                L1 须经 46号人工确认 · 阈值只紧不松 · 情绪原文即用即弃 ·
                LLM 禁入判定链
              </View>
            </View>
          </>
        )}

        <View className={styles.bottomSpacer} />
      </ScrollView>
    </View>
  );
};

export default BloggerPage;
