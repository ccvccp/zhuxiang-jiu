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
  platformName, domainName, workStatusName, followStatusName,
} from '@/api/blogger';
import { requireLogin } from '@/services/auth-service';

type Tab = 'overview' | 'pool' | 'works' | 'follows' | 'learning';

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: '总览' },
  { key: 'pool', label: '博主池' },
  { key: 'works', label: '侦测' },
  { key: 'follows', label: '跟随' },
  { key: 'learning', label: '学习' },
];

const PLATFORM_KEYS = ['douyin', 'xiaohongshu', 'weibo', 'wechat_channels'];
const DOMAIN_KEYS = ['wine', 'food', 'gift', 'lifestyle'];
const WORK_STATUS_KEYS = ['detected', 'auto_follow', 'manual_queue', 'following', 'passed'];
const FOLLOW_STATUS_KEYS = ['pending', 'approved', 'queued', 'published', 'rejected'];

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

  // 按当前页签刷新数据
  const refresh = useCallback(() => {
    if (!requireLogin()) return;
    if (tab === 'overview' || tab === 'learning') loadOverview();
    else if (tab === 'pool') loadPool();
    else if (tab === 'works') loadWorks(WORK_STATUS_KEYS[workStatusIdx]);
    else if (tab === 'follows') loadFollows(FOLLOW_STATUS_KEYS[followStatusIdx]);
  }, [tab, loadOverview, loadPool, loadWorks, loadFollows, workStatusIdx, followStatusIdx]);

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

        <View className={styles.bottomSpacer} />
      </ScrollView>
    </View>
  );
};

export default BloggerPage;
