/**
 * 智能推广工作台 · 36号运营端(设计文档 §2 六大子系统的管理面)
 * ============================================================
 * 「全网热点侦探 → 蹭点决策 → Agent 内容工厂 → 发布 → 归因回流」
 *
 * 五页签:
 *   总览: 统计卡(热点/内容/发布/归因/日限) + 待裁决提醒
 *   雷达: 手动扫描 + 热点列表(评分进度条) + 待裁决跟进/放弃
 *   内容: 一源多态生成(平台多选) + 列表 + 审核(三审 HITL) + 入队
 *   发布: 队列(黄金时段窗口) + 出队回执
 *   通道: 五平台三态徽章 + SEO 推送记录 + 受众画像
 */
import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  PromoAPI, HotspotVO, DecisionVO, PromoContentVO,
  PublishQueueVO, PromoOverviewVO, ChannelVO, SeoPushVO,
  AudienceProfileVO, EvolutionStatusVO, RiskCandidateVO,
  hotspotStatusName, publishPlatformName, contentStatusName,
  channelModeName,
} from '@/api/promo';

type Tab = 'overview' | 'radar' | 'studio' | 'publish' | 'channels'
  | 'evolution';

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: '总览' },
  { key: 'radar', label: '热点雷达' },
  { key: 'studio', label: '内容工厂' },
  { key: 'publish', label: '发布中心' },
  { key: 'channels', label: '通道画像' },
  { key: 'evolution', label: '进化中枢' },
];

/** 发布平台多选项 */
const PLATFORMS = [
  { key: 'douyin', label: '抖音' },
  { key: 'xiaohongshu', label: '小红书' },
  { key: 'wechat_moments', label: '视频号' },
  { key: 'weibo', label: '微博' },
  { key: 'wechat_channels', label: '百家号' },
];

const PromoPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('overview');
  const [loading, setLoading] = useState(true);

  // 总览
  const [overview, setOverview] = useState<PromoOverviewVO | null>(null);
  // 雷达
  const [hotspots, setHotspots] = useState<HotspotVO[]>([]);
  const [pending, setPending] = useState<DecisionVO[]>([]);
  const [scanning, setScanning] = useState(false);
  // 内容工厂
  const [contents, setContents] = useState<PromoContentVO[]>([]);
  const [genHotspotId, setGenHotspotId] = useState<number>(0);
  const [genPlatforms, setGenPlatforms] = useState<string[]>(['douyin']);
  const [generating, setGenerating] = useState(false);
  const [showDetail, setShowDetail] = useState<PromoContentVO | null>(null);
  // 发布
  const [queue, setQueue] = useState<PublishQueueVO[]>([]);
  const [processing, setProcessing] = useState(false);
  // 通道
  const [channels, setChannels] = useState<ChannelVO[]>([]);
  const [seoPushes, setSeoPushes] = useState<SeoPushVO[]>([]);
  const [profiles, setProfiles] = useState<AudienceProfileVO[]>([]);
  // 进化中枢(P3)
  const [evoStatus, setEvoStatus] = useState<EvolutionStatusVO | null>(null);
  const [riskCandidates, setRiskCandidates] = useState<RiskCandidateVO[]>([]);
  const [activeWords, setActiveWords] = useState<{ word: string; approvedBy?: string; approvedAt?: string }[]>([]);
  const [runningRegress, setRunningRegress] = useState(false);

  // ---------- 数据加载 ----------
  const loadAll = useCallback(async () => {
    try {
      const [ov, hs, pd, cs, q, ch, sp, ap, evo, rc, aw] = await Promise.all([
        PromoAPI.reportOverview().catch(() => null),
        PromoAPI.hotspots().catch(() => [] as HotspotVO[]),
        PromoAPI.decisions({ pendingOnly: true }).catch(() => [] as DecisionVO[]),
        PromoAPI.contents().catch(() => [] as PromoContentVO[]),
        PromoAPI.publishQueue().catch(() => [] as PublishQueueVO[]),
        PromoAPI.channelsStatus().catch(() => [] as ChannelVO[]),
        PromoAPI.seoPushes().catch(() => [] as SeoPushVO[]),
        PromoAPI.audienceProfiles().catch(() => [] as AudienceProfileVO[]),
        PromoAPI.evolutionStatus().catch(() => null),
        PromoAPI.evolutionRiskCandidates('pending').catch(() => [] as RiskCandidateVO[]),
        PromoAPI.evolutionActiveWords().catch(() => []),
      ]);
      setOverview(ov);
      setHotspots(hs);
      setPending(pd);
      setContents(cs);
      setQueue(q);
      setChannels(ch);
      setSeoPushes(sp);
      setProfiles(ap);
      setEvoStatus(evo);
      setRiskCandidates(rc);
      setActiveWords(aw);
      // 默认生成热点: 第一个已跟进
      if (!genHotspotId) {
        const engaged = hs.find((h) => h.status === 'engaged');
        if (engaged) setGenHotspotId(engaged.hotspotId);
      }
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [genHotspotId]);

  useEffect(() => { loadAll(); }, [loadAll]);

  // ---------- 雷达: 扫描 ----------
  const handleScan = async () => {
    if (scanning) return;
    setScanning(true);
    try {
      const r = await PromoAPI.radarScan();
      Taro.showToast({
        title: `扫描${r.scanned}条 · 新${r.new} · 否决${r.discarded}`,
        icon: 'none', duration: 2000,
      });
      await loadAll();
    } catch (e) {
      console.warn('[promo] 扫描失败:', e);
    } finally {
      setScanning(false);
    }
  };

  // ---------- 雷达: 人工裁决 ----------
  const handleDecide = async (hotspotId: number, engage: boolean) => {
    try {
      await PromoAPI.decide(hotspotId, engage, engage ? '工作台裁决跟进' : '工作台裁决放弃');
      Taro.showToast({ title: engage ? '已跟进' : '已放弃', icon: 'none' });
      await loadAll();
    } catch (e) {
      console.warn('[promo] 裁决失败:', e);
    }
  };

  // ---------- 内容工厂: 平台多选切换 ----------
  const togglePlatform = (key: string) => {
    setGenPlatforms((prev) => prev.includes(key)
      ? prev.filter((p) => p !== key)
      : [...prev, key]);
  };

  // ---------- 内容工厂: 生成 ----------
  const handleGenerate = async () => {
    if (generating) return;
    if (!genHotspotId) {
      Taro.showToast({ title: '请选择已跟进热点', icon: 'none' });
      return;
    }
    if (genPlatforms.length === 0) {
      Taro.showToast({ title: '请至少选择一个平台', icon: 'none' });
      return;
    }
    setGenerating(true);
    try {
      const items = await PromoAPI.generate({
        hotspotId: genHotspotId, platforms: genPlatforms,
      });
      Taro.showToast({
        title: `生成${items.length}个平台版本`,
        icon: 'success', duration: 1800,
      });
      await loadAll();
      setTab('studio');
    } catch (e) {
      console.warn('[promo] 生成失败(冷却期/日限):', e);
    } finally {
      setGenerating(false);
    }
  };

  // ---------- 内容工厂: 审核 ----------
  const handleReview = async (contentId: number, approved: boolean) => {
    try {
      await PromoAPI.review(contentId, approved);
      Taro.showToast({ title: approved ? '已通过' : '已拒绝', icon: 'none' });
      await loadAll();
    } catch (e) {
      console.warn('[promo] 审核失败:', e);
    }
  };

  // ---------- 内容工厂: 入队发布 ----------
  const handleQueue = async (contentId: number) => {
    try {
      const q = await PromoAPI.publish(contentId);
      Taro.showToast({
        title: `已入队 · ${q.windowHint || '黄金时段'}`,
        icon: 'none', duration: 1800,
      });
      await loadAll();
      setTab('publish');
    } catch (e) {
      console.warn('[promo] 入队失败(日限/状态):', e);
    }
  };

  // ---------- 发布中心: 出队 ----------
  const handleProcess = async () => {
    if (processing) return;
    setProcessing(true);
    try {
      const receipts = await PromoAPI.processPublish();
      const ok = receipts.filter((r) => r.receipt?.mode !== 'mock_fallback').length;
      Taro.showToast({
        title: receipts.length
          ? `发布${receipts.length}条(${ok}成功)` : '暂无到期内容',
        icon: 'none', duration: 1800,
      });
      await loadAll();
    } catch (e) {
      console.warn('[promo] 出队失败:', e);
    } finally {
      setProcessing(false);
    }
  };

  // ---------- 通道模式颜色 ----------
  const modeClass = (m: string): string =>
    m === 'real' ? styles.modeReal
      : m === 'mock_fallback' ? styles.modeFallback : styles.modeMock;

  const engagedHotspots = hotspots.filter((h) => h.status === 'engaged');

  return (
    <View className={styles.page}>
      <NavBar title="智能推广" />
      <ScrollView scrollY className={styles.body}>
        {/* 页签栏 */}
        <View className={styles.tabBar}>
          {TABS.map((t) => (
            <View
              key={t.key}
              className={`${styles.tabItem} ${tab === t.key ? styles.tabItemActive : ''}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
              {t.key === 'radar' && pending.length > 0 && (
                <Text className={styles.badge}>{pending.length}</Text>
              )}
            </View>
          ))}
        </View>

        {loading && <View className={styles.empty}>加载中...</View>}

        {/* ============ 总览 ============ */}
        {tab === 'overview' && !loading && overview && (
          <View className={styles.section}>
            <View className={styles.statGrid}>
              <View className={styles.statCard}>
                <View className={styles.statNum}>{overview.hotspots.total}</View>
                <View className={styles.statLabel}>热点总数</View>
              </View>
              <View className={styles.statCard}>
                <View className={styles.statNum}>{overview.hotspots.pendingManual}</View>
                <View className={styles.statLabel}>待人工裁决</View>
              </View>
              <View className={styles.statCard}>
                <View className={styles.statNum}>{overview.contents.total}</View>
                <View className={styles.statLabel}>生成内容</View>
              </View>
              <View className={styles.statCard}>
                <View className={styles.statNum}>{overview.contents.published}</View>
                <View className={styles.statLabel}>已发布</View>
              </View>
              <View className={styles.statCard}>
                <View className={styles.statNum}>{overview.attribution.clicks}</View>
                <View className={styles.statLabel}>引流点击</View>
              </View>
              <View className={styles.statCard}>
                <View className={styles.statNum}>¥{overview.attribution.gmv}</View>
                <View className={styles.statLabel}>归因GMV</View>
              </View>
            </View>
            <View className={styles.cardRow}>
              <View className={styles.cardLabel}>单日发布限额</View>
              <View className={styles.cardValue}>
                {overview.dailyCap.used} / {overview.dailyCap.limit}
              </View>
            </View>
            {overview.hotspots.pendingManual > 0 && (
              <View
                className={styles.alertCard}
                onClick={() => setTab('radar')}
              >
                ⚠️ {overview.hotspots.pendingManual} 条热点待人工裁决(50-70 分区间),
                点击前往处理
              </View>
            )}
            <View className={styles.hint}>
              全链: 热点侦探 → 蹭点决策 → Agent 工厂 → 三审合规 → 发布 → 归因回流
            </View>
          </View>
        )}

        {/* ============ 热点雷达 ============ */}
        {tab === 'radar' && !loading && (
          <View className={styles.section}>
            <View
              className={`${styles.actionBtn} ${scanning ? styles.actionBtnDisabled : ''}`}
              onClick={handleScan}
            >
              {scanning ? '扫描中...' : '🔍 立即扫描五平台热榜'}
            </View>

            {/* 待人工裁决 */}
            {pending.length > 0 && (
              <View className={styles.subCard}>
                <View className={styles.subTitle}>待人工裁决({pending.length})</View>
                {pending.map((d) => (
                  <View key={d.decisionId} className={styles.decideRow}>
                    <View className={styles.decideInfo}>
                      <View className={styles.decideTitle}>
                        {d.hotspotTitle || `热点#${d.hotspotId}`}
                      </View>
                      <View className={styles.decideReason}>{d.reason}</View>
                    </View>
                    <View className={styles.decideBtns}>
                      <View
                        className={styles.btnEngage}
                        onClick={() => handleDecide(d.hotspotId, true)}
                      >跟进</View>
                      <View
                        className={styles.btnPass}
                        onClick={() => handleDecide(d.hotspotId, false)}
                      >放弃</View>
                    </View>
                  </View>
                ))}
              </View>
            )}

            {/* 热点列表 */}
            <View className={styles.subTitle}>热点池({hotspots.length})</View>
            {hotspots.map((h) => (
              <View key={h.hotspotId} className={styles.hotspotCard}>
                <View className={styles.hotspotHead}>
                  <View className={styles.hotspotTitle}>{h.title}</View>
                  <View
                    className={`${styles.statusPill} ${h.status === 'engaged' ? styles.pillEngaged : h.status === 'discarded' ? styles.pillDiscarded : ''}`}
                  >
                    {hotspotStatusName(h.status)}
                  </View>
                </View>
                <View className={styles.scoreRow}>
                  <View className={styles.scoreBar}>
                    <View
                      className={styles.scoreFill}
                      style={{ width: `${Math.min(100, Math.round(h.score))}%` }}
                    />
                  </View>
                  <Text className={styles.scoreNum}>{h.score.toFixed(0)}</Text>
                </View>
                {h.summary && <View className={styles.hotspotSummary}>{h.summary}</View>}
              </View>
            ))}
            {hotspots.length === 0 && (
              <View className={styles.empty}>暂无热点, 点击上方扫描(五平台模拟源)</View>
            )}
          </View>
        )}

        {/* ============ 内容工厂 ============ */}
        {tab === 'studio' && !loading && (
          <View className={styles.section}>
            {/* 生成表单 */}
            <View className={styles.subCard}>
              <View className={styles.subTitle}>Agent 一源多态生成</View>
              <View className={styles.formRow}>
                <Text className={styles.formLabel}>热点</Text>
                <View className={styles.hotspotPicker}>
                  {engagedHotspots.length === 0 ? (
                    <Text className={styles.pickerEmpty}>先在雷达页跟进热点</Text>
                  ) : engagedHotspots.map((h) => (
                    <View
                      key={h.hotspotId}
                      className={`${styles.pickerItem} ${genHotspotId === h.hotspotId ? styles.pickerItemActive : ''}`}
                      onClick={() => setGenHotspotId(h.hotspotId)}
                    >
                      {h.title.slice(0, 12)}
                    </View>
                  ))}
                </View>
              </View>
              <View className={styles.formRow}>
                <Text className={styles.formLabel}>平台</Text>
                <View className={styles.platformPicker}>
                  {PLATFORMS.map((p) => (
                    <View
                      key={p.key}
                      className={`${styles.pickerItem} ${genPlatforms.includes(p.key) ? styles.pickerItemActive : ''}`}
                      onClick={() => togglePlatform(p.key)}
                    >
                      {p.label}
                    </View>
                  ))}
                </View>
              </View>
              <View
                className={`${styles.actionBtn} ${generating ? styles.actionBtnDisabled : ''}`}
                onClick={handleGenerate}
              >
                {generating ? 'Agent 生成中(四步链)...' : '✨ 生成内容(分析→匹配→生成→自查)'}
              </View>
              <View className={styles.hint}>
                GLM-5.3 四步链三级降级 · 合规预审 · 权威信源 RAG · 短码自动挂接
              </View>
            </View>

            {/* 内容列表 */}
            <View className={styles.subTitle}>内容列表({contents.length})</View>
            {contents.map((c) => (
              <View key={c.contentId} className={styles.contentCard}>
                <View className={styles.hotspotHead}>
                  <View className={styles.hotspotTitle}>{c.title}</View>
                  <View
                    className={`${styles.statusPill} ${c.status === 'published' ? styles.pillEngaged : c.status === 'rejected' ? styles.pillDiscarded : ''}`}
                  >
                    {contentStatusName(c.status)}
                  </View>
                </View>
                <View className={styles.contentMeta}>
                  <Text>{publishPlatformName(c.platform)}</Text>
                  <Text>合规 {c.complianceScore ?? '-'}</Text>
                  <Text>组 #{c.contentGroupId}</Text>
                  {c.shortCode && <Text>{c.shortCode}</Text>}
                </View>
                <View className={styles.traceRow}>
                  {(c.agentTrace || []).map((t, i) => (
                    <Text key={i} className={styles.traceBadge}>{t}</Text>
                  ))}
                </View>
                {c.status === 'pending' && (
                  <View className={styles.contentActions}>
                    <View
                      className={styles.btnEngage}
                      onClick={() => handleReview(c.contentId, true)}
                    >通过</View>
                    <View
                      className={styles.btnPass}
                      onClick={() => handleReview(c.contentId, false)}
                    >拒绝</View>
                  </View>
                )}
                {c.status === 'approved' && (
                  <View className={styles.contentActions}>
                    <View
                      className={styles.btnQueue}
                      onClick={() => handleQueue(c.contentId)}
                    >入队发布</View>
                  </View>
                )}
                <View
                  className={styles.detailLink}
                  onClick={async () => {
                    try {
                      const d = await PromoAPI.contentDetail(c.contentId);
                      setShowDetail(d);
                    } catch (e) { /* 详情失败忽略 */ }
                  }}
                >查看详情 ›</View>
              </View>
            ))}
            {contents.length === 0 && (
              <View className={styles.empty}>暂无内容, 上方选择热点与平台生成</View>
            )}
          </View>
        )}

        {/* ============ 发布中心 ============ */}
        {tab === 'publish' && !loading && (
          <View className={styles.section}>
            <View
              className={`${styles.actionBtn} ${processing ? styles.actionBtnDisabled : ''}`}
              onClick={handleProcess}
            >
              {processing ? '发布中...' : '🚀 处理到期发布(出队+回执)'}
            </View>
            <View className={styles.subTitle}>发布队列({queue.length})</View>
            {queue.map((q, i) => (
              <View key={`${q.contentId}-${i}`} className={styles.queueCard}>
                <View className={styles.hotspotHead}>
                  <View className={styles.hotspotTitle}>
                    #{q.contentId} {q.title || ''}
                  </View>
                  <View className={styles.statusPill}>{q.platform}</View>
                </View>
                <View className={styles.queueMeta}>
                  <Text>计划 {q.scheduledAt}</Text>
                  <Text className={q.inWindow ? styles.windowOk : styles.windowWait}>
                    {q.inWindow ? '黄金时段内' : (q.windowHint || '等待窗口')}
                  </Text>
                </View>
              </View>
            ))}
            {queue.length === 0 && (
              <View className={styles.empty}>队列为空, 内容工厂审核通过后入队</View>
            )}
            <View className={styles.hint}>
              黄金时段调度 · 单日上限防刷屏 · mock 轨回执含曝光预估
            </View>
          </View>
        )}

        {/* ============ 通道画像 ============ */}
        {tab === 'channels' && !loading && (
          <View className={styles.section}>
            <View className={styles.subTitle}>发布通道(五平台)</View>
            {channels.map((c) => (
              <View key={c.platform} className={styles.cardRow}>
                <View className={styles.cardLabel}>{publishPlatformName(c.platform)}</View>
                <View className={styles.cardValueWrap}>
                  <Text className={`${styles.modeBadge} ${modeClass(c.effectiveMode)}`}>
                    {channelModeName(c.effectiveMode)}
                  </Text>
                  {c.keyConfigured && <Text className={styles.keyOk}>KEY✓</Text>}
                </View>
              </View>
            ))}
            <View className={styles.subTitle}>百度 SEO 推送</View>
            {seoPushes.length === 0 ? (
              <View className={styles.empty}>暂无推送记录(发布后自动触发)</View>
            ) : seoPushes.map((s, i) => (
              <View key={i} className={styles.cardRow}>
                <View className={styles.cardLabel}>
                  {s.pushedAt || `记录${i + 1}`} · {s.urls?.length || 0} URL
                </View>
                <View className={styles.cardValue}>{s.status}</View>
              </View>
            ))}
            <View className={styles.subTitle}>平台受众画像</View>
            {profiles.map((p) => (
              <View key={p.platform} className={styles.profileCard}>
                <View className={styles.hotspotTitle}>
                  {publishPlatformName(p.platform)}
                </View>
                <View className={styles.profileRow}>人群: {p.audience}</View>
                <View className={styles.profileRow}>基调: {p.tone}</View>
                <View className={styles.profileRow}>格式: {p.format}</View>
              </View>
            ))}
            <View className={styles.hint}>
              真实通道资质就绪后配 PROMO_CHANNEL_KEY, 未配平台自动 mock_fallback 可观测降级
            </View>
          </View>
        )}

        {/* ============ 进化中枢(P3) ============ */}
        {tab === 'evolution' && !loading && evoStatus && (
          <View className={styles.section}>
            <View className={styles.hint}>
              反馈驱动进化: 数据回流 → 策略参数自调 → 全量留痕可审计
            </View>

            {/* 引擎1 品类权重 */}
            <View className={styles.subCard}>
              <View className={styles.subTitle}>引擎1 · 热点价值评估进化</View>
              {evoStatus.engines.hotspotWeights.categories.map((c) => (
                <View key={c.category} className={styles.evoRow}>
                  <View className={styles.cardLabel}>{c.categoryName}</View>
                  <View className={styles.evoValueWrap}>
                    <Text className={styles.evoWeight}>{c.weight.toFixed(2)}</Text>
                    <Text className={styles.evoBase}>(基线{c.base})</Text>
                    <Text className={styles.evoSamples}>{c.samples}样本</Text>
                  </View>
                </View>
              ))}
              <View
                className={`${styles.actionBtn} ${runningRegress ? styles.actionBtnDisabled : ''}`}
                onClick={async () => {
                  if (runningRegress) return;
                  setRunningRegress(true);
                  try {
                    const r = await PromoAPI.evolutionWeightsRun();
                    Taro.showToast({
                      title: r.adjustments.length
                        ? `回归完成: ${r.adjustments.length} 品类调整`
                        : '回归完成(样本不足品类跳过)',
                      icon: 'none', duration: 1800,
                    });
                    await loadAll();
                  } catch (e) {
                    console.warn('[evo] 回归失败:', e);
                  } finally {
                    setRunningRegress(false);
                  }
                }}
              >
                {runningRegress ? '回归中...' : '运行品类 ROI 回归'}
              </View>
            </View>

            {/* 引擎2 风格 A/B */}
            <View className={styles.subCard}>
              <View className={styles.subTitle}>引擎2 · 内容风格自适应进化</View>
              {evoStatus.engines.styleChampion.styles.map((s) => (
                <View key={s.styleKey} className={styles.evoRow}>
                  <View className={styles.cardLabel}>
                    {s.name}
                    {s.champion && <Text className={styles.championTag}>冠军</Text>}
                  </View>
                  <View className={styles.evoValueWrap}>
                    <Text>{s.variants}变体</Text>
                    <Text>CTR {s.ctr.toFixed(3)}</Text>
                  </View>
                </View>
              ))}
            </View>

            {/* 引擎3 老虎机 */}
            <View className={styles.subCard}>
              <View className={styles.subTitle}>引擎3 · 承接页智能路由(UCB1)</View>
              {evoStatus.engines.landingBandit.arms.map((a) => (
                <View key={a.arm} className={styles.evoRow}>
                  <View className={styles.cardLabel}>
                    {a.name}
                    <Text className={styles.evoIntent}>{a.intent}</Text>
                  </View>
                  <View className={styles.evoValueWrap}>
                    <Text>{a.pulls}次</Text>
                    <Text>均值 {a.meanReward.toFixed(3)}</Text>
                  </View>
                </View>
              ))}
            </View>

            {/* 引擎4 风险词候选 */}
            <View className={styles.subCard}>
              <View className={styles.subTitle}>
                引擎4 · 合规进化(候选 {riskCandidates.length})
              </View>
              {riskCandidates.length === 0 ? (
                <View className={styles.empty}>暂无候选(审核反馈录入后提取)</View>
              ) : riskCandidates.map((c) => (
                <View key={c.word} className={styles.decideRow}>
                  <View className={styles.decideInfo}>
                    <View className={styles.decideTitle}>{c.word}</View>
                    <View className={styles.decideReason}>
                      拒绝文档 {c.rejectDocs} 条 · 拒绝率 {c.rejectRate}
                    </View>
                  </View>
                  <View className={styles.decideBtns}>
                    <View
                      className={styles.btnEngage}
                      onClick={async () => {
                        try {
                          await PromoAPI.evolutionApproveWord(c.word);
                          Taro.showToast({ title: '已批准生效', icon: 'none' });
                          await loadAll();
                        } catch (e) { console.warn('[evo] 批准失败:', e); }
                      }}
                    >批准</View>
                    <View
                      className={styles.btnPass}
                      onClick={async () => {
                        try {
                          await PromoAPI.evolutionRejectWord(c.word);
                          Taro.showToast({ title: '已拒绝(误报)', icon: 'none' });
                          await loadAll();
                        } catch (e) { console.warn('[evo] 拒绝失败:', e); }
                      }}
                    >拒绝</View>
                  </View>
                </View>
              ))}
              <View className={styles.hint}>
                候选词仅观察永不自动阻断; 人工批准后注入雷达与内容双闸门
              </View>
              {activeWords.length > 0 && (
                <View>
                  <View className={styles.subTitle}>
                    已生效词({activeWords.length}) · 可撤销
                  </View>
                  {activeWords.map((w) => (
                    <View key={w.word} className={styles.decideRow}>
                      <View className={styles.decideInfo}>
                        <View className={styles.decideTitle}>{w.word}</View>
                        <View className={styles.decideReason}>
                          {w.approvedBy || 'admin'} 批准 · {(w.approvedAt || '').slice(0, 10)}
                        </View>
                      </View>
                      <View className={styles.decideBtns}>
                        <View
                          className={styles.btnPass}
                          onClick={async () => {
                            try {
                              await PromoAPI.evolutionRevokeWord(w.word);
                              Taro.showToast({
                                title: '已撤销, 下轮生成起不再拦截',
                                icon: 'none', duration: 1800,
                              });
                              await loadAll();
                            } catch (e) { console.warn('[evo] 撤销失败:', e); }
                          }}
                        >撤销</View>
                      </View>
                    </View>
                  ))}
                </View>
              )}
            </View>

            <View className={styles.cardRow}>
              <View className={styles.cardLabel}>商品拓展</View>
              <View className={styles.cardValue}>
                {evoStatus.productExpansion.library}
              </View>
            </View>
          </View>
        )}
        {tab === 'evolution' && !loading && !evoStatus && (
          <View className={styles.empty}>进化引擎数据加载失败, 请重试</View>
        )}

        <View className={styles.footer}>36号·AI智能推广 · 热点雷达×Agent工厂×三审合规×归因回流×进化引擎</View>
      </ScrollView>

      {/* 内容详情弹层 */}
      {showDetail && (
        <View className={styles.mask} onClick={() => setShowDetail(null)}>
          <View className={styles.detailPanel} onClick={(e) => e.stopPropagation()}>
            <View className={styles.detailTitle}>{showDetail.title}</View>
            <View className={styles.detailMeta}>
              {publishPlatformName(showDetail.platform)} · 合规
              {showDetail.complianceScore} · 组#{showDetail.contentGroupId}
            </View>
            <ScrollView scrollY className={styles.detailBody}>
              <View className={styles.detailText}>{showDetail.body}</View>
              {showDetail.hashtags?.length ? (
                <View className={styles.traceRow}>
                  {showDetail.hashtags.map((t, i) => (
                    <Text key={i} className={styles.traceBadge}>{t}</Text>
                  ))}
                </View>
              ) : null}
              {showDetail.agentTrace?.length ? (
                <View className={styles.detailSection}>
                  Agent 轨迹: {showDetail.agentTrace.join(' → ')}
                </View>
              ) : null}
              {showDetail.authorityRefs?.length ? (
                <View className={styles.detailSection}>
                  权威信源引用: {showDetail.authorityRefs.join('; ')}
                </View>
              ) : null}
              <View className={styles.detailSection}>
                溯源校验: {showDetail.provenanceViolations?.length
                  ? `违规${showDetail.provenanceViolations.length}处` : '通过(无数字违规)'}
              </View>
            </ScrollView>
            <View className={styles.detailClose} onClick={() => setShowDetail(null)}>关闭</View>
          </View>
        </View>
      )}
    </View>
  );
};

export default PromoPage;
