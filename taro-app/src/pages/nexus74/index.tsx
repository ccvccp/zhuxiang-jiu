/**
 * 74号·NexusFlow 发布工作台 · 四页签: 总览 / 素材合规 / 适配发布 / 指标复盘
 * 数据来源: 后端 /api/nexus74/*(X-Role: admin)
 * 口径: 观测面常开; 决策面(adapt/publish/retry)受 NEXUSFLOW74_MODE;
 *       B 档人工回执不受 MODE(数据诚实); 合规判定 100% 确定性(LLM 禁入)。
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input, Textarea } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  NexusAPI, SourceVO, AdaptationVO, PublicationVO,
  ComplianceVO, PUBLISH_PLATFORMS, SOURCE_INTENTS,
} from '@/api/nexus74';

type Tab = 'overview' | 'source' | 'publish' | 'metrics';

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: '总览' },
  { key: 'source', label: '素材合规' },
  { key: 'publish', label: '适配发布' },
  { key: 'metrics', label: '指标复盘' },
];

const fmtTime = (s: string): string =>
  s ? new Date(s).toLocaleString('zh-CN') : '';

const Nexus74WorkbenchPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('overview');
  const [loading, setLoading] = useState(true);
  // 总览
  const [modelStatus, setModelStatus] = useState<any>(null);
  const [quota, setQuota] = useState<any>(null);
  const [metrics, setMetrics] = useState<any>(null);
  // 素材
  const [sources, setSources] = useState<SourceVO[]>([]);
  const [checkText, setCheckText] = useState('');
  const [checkResult, setCheckResult] = useState<ComplianceVO | null>(null);
  const [checking, setChecking] = useState(false);
  // 适配发布
  const [selSource, setSelSource] = useState(0);
  const [selPlatform, setSelPlatform] = useState('');
  const [adaptPreview, setAdaptPreview] = useState<AdaptationVO | null>(null);
  const [busy, setBusy] = useState(false);
  const [publications, setPublications] = useState<PublicationVO[]>([]);
  // 指标
  const [retrospects, setRetrospects] = useState<any[]>([]);

  const loadAll = useCallback(async () => {
    try {
      const [ms, qt, mt, ss, pubs, rt] = await Promise.all([
        NexusAPI.modelStatus().catch(() => null),
        NexusAPI.quotaStatus().catch(() => null),
        NexusAPI.metricsSummary().catch(() => null),
        NexusAPI.sources(20).catch(() => [] as SourceVO[]),
        NexusAPI.publications(20).catch(() => [] as PublicationVO[]),
        NexusAPI.retrospects(10).catch(() => []),
      ]);
      setModelStatus(ms);
      setQuota(qt);
      setMetrics(mt);
      setSources(ss);
      setPublications(pubs);
      setRetrospects(rt);
      if (ss.length > 0) setSelSource(ss[0].sourceId);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  /** 合规检查 */
  const handleCheck = async () => {
    if (checking || !checkText.trim()) return;
    setChecking(true);
    try {
      setCheckResult(await NexusAPI.complianceCheck(checkText.trim()));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    } finally {
      setChecking(false);
    }
  };

  /** 警示语注入修复 */
  const handleInject = async () => {
    if (!checkResult || !checkText.trim()) return;
    try {
      const r = await NexusAPI.warningInject(checkText.trim());
      const fixed = String((r as any).text || '');
      if (fixed) {
        setCheckText(fixed);
        Taro.showToast({ title: '已注入警示语', icon: 'success' });
        setCheckResult(await NexusAPI.complianceCheck(fixed));
      }
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 平台适配 */
  const handleAdapt = async () => {
    if (busy || !selSource || !selPlatform) return;
    setBusy(true);
    try {
      const a = await NexusAPI.adapt(selSource, selPlatform);
      if (a) {
        setAdaptPreview(a);
        Taro.showToast({ title: '适配完成', icon: 'success' });
      }
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    } finally {
      setBusy(false);
    }
  };

  /** 发布 */
  const handlePublish = async () => {
    if (busy || !selSource || !selPlatform) return;
    setBusy(true);
    try {
      const r = await NexusAPI.publish(
        selSource, selPlatform, adaptPreview?.adaptationId || 0);
      Taro.showToast({ title: '发布已受理', icon: 'success' });
      setPublications(await NexusAPI.publications(20).catch(() => [] as PublicationVO[]));
    } catch (e: any) {
      const msg = String(e?.message || e);
      Taro.showModal({
        title: '发布未受理',
        content: msg.includes('409') || msg.includes('决策') || msg.includes('MODE')
          || msg.includes('静默') || msg.includes('配额')
          ? (msg || '决策面暂不可用')
          : (msg.slice(0, 60) || '请稍后重试'),
        showCancel: false,
      });
    } finally {
      setBusy(false);
    }
  };

  /** 回执登记(B 档人工——数据诚实) */
  const handleReceipt = async (pubId: number, result: string) => {
    try {
      await NexusAPI.publicationReceipt(pubId, result);
      Taro.showToast({ title: '回执已登记', icon: 'success' });
      setPublications(await NexusAPI.publications(20).catch(() => [] as PublicationVO[]));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  const stateCls = (s: string) =>
    s === 'pass' ? styles.statePass
      : s === 'block' ? styles.stateBlock : styles.stateReview;

  return (
    <View className={styles.page}>
      <NavBar title="NexusFlow 发布工作台" />
      <View className={styles.tabs}>
        {TABS.map(t => (
          <View
            key={t.key}
            className={`${styles.tab} ${tab === t.key ? styles.tabActive : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </View>
        ))}
      </View>
      <ScrollView scrollY className={styles.scrollView}>

        {/* ============ 总览 ============ */}
        {tab === 'overview' && (
          <>
            <View className={styles.modeCard}>
              <View className={styles.modeLabel}>模型运行模式</View>
              <View className={styles.modeValue}>{modelStatus?.mode || '--'}</View>
              <View className={styles.modeMeta}>
                {modelStatus?.platformCount ?? '--'} 个发布平台 · 红线 {modelStatus?.redlines?.length ?? 0} 条
                {modelStatus?.immunity ? ` · 免疫${modelStatus.immunity.status === 'active' ? '正常' : '冻结'}` : ''}
              </View>
            </View>

            {quota && (
              <View className={styles.card}>
                <View className={styles.cardTitle}>今日发布配额</View>
                {quota.silence?.active && (
                  <View className={styles.silenceWarn}>
                    ⏸️ 静默窗生效中（{quota.silence.hours.join('/')} 时）——发布将进入待发队列
                  </View>
                )}
                {(quota.platforms || []).map((p: any) => (
                  <View key={p.platform} style={{ marginTop: '16rpx' }}>
                    <View className={styles.kv}>
                      <View className={styles.kvKey}>
                        {p.platformName}（{p.adapterTier} 档）
                      </View>
                      <View className={styles.kvVal}>{p.todayPublished}/{p.cap}</View>
                    </View>
                    <View className={styles.quotaBar}>
                      <View
                        className={styles.quotaFill}
                        style={{ width: `${Math.min(100, (p.todayPublished / Math.max(1, p.cap)) * 100)}%` }}
                      />
                    </View>
                  </View>
                ))}
              </View>
            )}

            {metrics && (
              <View className={styles.card}>
                <View className={styles.cardTitle}>全局指标</View>
                <View className={styles.metricGrid}>
                  <View className={styles.metricItem}>
                    <View className={styles.metricValue}>{metrics.global.publications}</View>
                    <View className={styles.metricLabel}>发布记录</View>
                  </View>
                  <View className={styles.metricItem}>
                    <View className={styles.metricValue}>{metrics.global.published}</View>
                    <View className={styles.metricLabel}>已发布</View>
                  </View>
                  <View className={styles.metricItem}>
                    <View className={styles.metricValue}>
                      {Math.round(metrics.global.avgEngagement * 100)}%
                    </View>
                    <View className={styles.metricLabel}>平均互动率</View>
                  </View>
                  <View className={styles.metricItem}>
                    <View className={styles.metricValue}>{metrics.global.withMetrics}</View>
                    <View className={styles.metricLabel}>已回流指标</View>
                  </View>
                </View>
              </View>
            )}
          </>
        )}

        {/* ============ 素材合规 ============ */}
        {tab === 'source' && (
          <>
            <View className={styles.card}>
              <View className={styles.cardTitle}>合规检查（确定性判定——LLM 禁入）</View>
              <Textarea
                value={checkText}
                onInput={e => setCheckText(e.detail.value)}
                placeholder="输入待检文本…"
                maxlength={2000}
                style={{
                  width: '100%', minHeight: '120rpx', padding: '16rpx',
                  background: 'rgba(0,0,0,0.02)', borderRadius: '12rpx',
                  fontSize: '24rpx', boxSizing: 'border-box',
                }}
              />
              <View
                className={`${styles.primaryBtn} ${checking || !checkText.trim() ? styles.btnDisabled : ''}`}
                onClick={handleCheck}
              >
                {checking ? '检查中…' : '开始检查'}
              </View>
              {checkResult && (
                <View style={{ marginTop: '24rpx' }}>
                  <View className={styles.stateBadge + ' ' + stateCls(checkResult.state)}>
                    {checkResult.stateLabel}
                  </View>
                  {(checkResult.hits || []).map((h, i) => (
                    <View key={i} className={styles.hitItem}>
                      命中: {String(h.content || h.pattern || '')}
                    </View>
                  ))}
                  {checkResult.fixable && (
                    <View className={styles.fixBtn} onClick={handleInject}>
                      一键注入警示语修复
                    </View>
                  )}
                  <View className={styles.checkNote}>{checkResult.note}</View>
                </View>
              )}
            </View>

            <View className={styles.card}>
              <View className={styles.sectionHead}>
                <View className={styles.cardTitle}>素材源库</View>
                <View className={styles.sectionCount}>{sources.length} 条</View>
              </View>
              {loading ? (
                <View className={styles.empty}>加载中…</View>
              ) : sources.length === 0 ? (
                <View className={styles.empty}>暂无素材源</View>
              ) : sources.map(s => (
                <View key={s.sourceId} className={styles.sourceItem}>
                  <View className={styles.sourceTitle}>{s.title}</View>
                  <View className={styles.sourceMeta}>
                    <Text className={styles.intentBadge}>{s.intentLabel}</Text>
                    #{s.sourceId} · {s.keywords.join(' / ') || '无关键词'} · {fmtTime(s.createdAt)}
                  </View>
                </View>
              ))}
            </View>
          </>
        )}

        {/* ============ 适配发布 ============ */}
        {tab === 'publish' && (
          <>
            <View className={styles.card}>
              <View className={styles.cardTitle}>平台适配</View>
              <View className={styles.selectRow}>
                <View className={styles.selectCol}>
                  <View className={styles.selectLabel}>素材源（{sources.length} 条可选）</View>
                  <View className={styles.pillGroup}>
                    {sources.slice(0, 6).map(s => (
                      <View
                        key={s.sourceId}
                        className={`${styles.pill} ${selSource === s.sourceId ? styles.pillActive : ''}`}
                        onClick={() => setSelSource(s.sourceId)}
                      >
                        #{s.sourceId} {s.title.slice(0, 8)}
                      </View>
                    ))}
                  </View>
                </View>
              </View>
              <View className={styles.selectLabel}>目标平台</View>
              <View className={styles.pillGroup}>
                {PUBLISH_PLATFORMS.map(p => (
                  <View
                    key={p.key}
                    className={`${styles.pill} ${selPlatform === p.key ? styles.pillActive : ''}`}
                    onClick={() => setSelPlatform(p.key)}
                  >
                    {p.label}
                  </View>
                ))}
              </View>
              <View
                className={`${styles.primaryBtn} ${busy || !selSource || !selPlatform ? styles.btnDisabled : ''}`}
                onClick={handleAdapt}
              >
                {busy ? '处理中…' : '生成适配版本'}
              </View>

              {adaptPreview && (
                <View className={styles.adaptPreview}>
                  <View className={styles.adaptTitle}>
                    [{adaptPreview.platformName} · {adaptPreview.personaStateLabel}]
                    {adaptPreview.title}
                  </View>
                  <View className={styles.adaptSummary}>{adaptPreview.summary}</View>
                  <View className={styles.sourceMeta} style={{ marginTop: '12rpx' }}>
                    合规态: {adaptPreview.complianceState} · 适配 #{adaptPreview.adaptationId}
                  </View>
                </View>
              )}
            </View>

            <View
              className={`${styles.primaryBtn} ${busy || !selSource || !selPlatform ? styles.btnDisabled : ''}`}
              onClick={handlePublish}
              style={{ marginBottom: '24rpx' }}
            >
              {busy ? '处理中…' : `发布到${selPlatform ? PUBLISH_PLATFORMS.find(p => p.key === selPlatform)?.label : ''}`}
            </View>

            <View className={styles.card}>
              <View className={styles.sectionHead}>
                <View className={styles.cardTitle}>发布记录</View>
                <View className={styles.sectionCount}>{publications.length} 条</View>
              </View>
              {publications.length === 0 ? (
                <View className={styles.empty}>暂无发布记录</View>
              ) : publications.map(p => (
                <View key={p.publicationId} className={styles.pubItem}>
                  <View className={styles.pubHead}>
                    <View className={styles.tierBadge}>{p.adapterTier} 档</View>
                    <View className={styles.sourceTitle}>
                      {p.package?.title || `发布 #${p.publicationId}`}
                    </View>
                  </View>
                  <View className={styles.sourceMeta}>
                    {p.platformName} · {p.intentLabel} · 合规 {p.complianceState}
                    {p.autoPublished ? ' · 自主' : ' · 人工'} · #{p.publicationId}
                  </View>
                  <View className={styles.sourceMeta}>{fmtTime(p.createdAt || '')}</View>
                  <View style={{ display: 'flex', gap: '16rpx', marginTop: '12rpx' }}>
                    <View
                      className={styles.miniBtn + ' ' + styles.miniPrimary}
                      onClick={() => handleReceipt(p.publicationId, 'published')}
                    >
                      登记已发布
                    </View>
                    <View
                      className={styles.miniBtn + ' ' + styles.miniWarn}
                      onClick={() => handleReceipt(p.publicationId, 'rejected')}
                    >
                      登记被拒
                    </View>
                  </View>
                </View>
              ))}
            </View>
          </>
        )}

        {/* ============ 指标复盘 ============ */}
        {tab === 'metrics' && (
          <>
            {metrics && (
              <View className={styles.card}>
                <View className={styles.cardTitle}>分平台指标</View>
                {(metrics.platforms || []).map((p: any) => (
                  <View key={p.platform} className={styles.kv}>
                    <View className={styles.kvKey}>
                      {p.platformName}（发布 {p.published}）
                    </View>
                    <View className={styles.kvVal}>
                      读{p.totalRead} · 赞{p.totalLike} · 互动{Math.round(p.avgEngagement * 100)}%
                    </View>
                  </View>
                ))}
              </View>
            )}

            <View className={styles.card}>
              <View className={styles.sectionHead}>
                <View className={styles.cardTitle}>发布复盘</View>
                <View className={styles.sectionCount}>{retrospects.length} 条</View>
              </View>
              {retrospects.length === 0 ? (
                <View className={styles.empty}>暂无复盘记录</View>
              ) : retrospects.map(r => (
                <View key={r.retroId} className={styles.retroItem}>
                  <View className={styles.pubHead}>
                    <View className={styles.tierBadge}>{r.platformName}</View>
                    <View className={styles.sourceTitle}>{r.verdictLabel}</View>
                    <View className={styles.sourceMeta}>
                      互动率 {Math.round(r.engagementRate * 100)}%
                    </View>
                  </View>
                  {(r.advices || []).map((a: any, i: number) => (
                    <View key={i} className={styles.adviceItem}>{a.text}</View>
                  ))}
                </View>
              ))}
            </View>
          </>
        )}

        <View className={styles.footerNote}>
          NexusFlow 发布工作台 · 合规判定确定性 · B 档回执数据诚实
        </View>
      </ScrollView>
    </View>
  );
};

export default Nexus74WorkbenchPage;
