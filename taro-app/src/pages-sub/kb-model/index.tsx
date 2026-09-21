/**
 * 智能知识库训练模型 · 管理工作台
 * 四页签: 双师引擎(灰度/统计/样本) → 知识库(条目治理) → 缺口队列 → 问答测试
 * 口径: 观测面永不关停 · 样本仅为建议(流转必经人工) · 宪法域不可校准
 */
import React, { useEffect, useState } from 'react';
import { View, Text, ScrollView, Input, Textarea } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  KbModelAPI, entryStatusName, gapStatusName, sampleKindName,
} from '@/api/kb-model';

type Tab = 'dual' | 'entries' | 'gaps' | 'ask';

const TABS: { key: Tab; label: string }[] = [
  { key: 'dual', label: '双师引擎' },
  { key: 'entries', label: '知识库' },
  { key: 'gaps', label: '缺口队列' },
  { key: 'ask', label: '问答测试' },
];

// 条目状态筛选
const STATUS_FILTERS = ['', 'published', 'pending', 'approved', 'rejected', 'retired'];

const formatTime = (t?: string): string => (t || '').slice(0, 16).replace('T', ' ');
const pctStr = (v: any): string =>
  v == null ? '—' : `${(Number(v) * 100).toFixed(1)}%`;

const errMsg = (e: any): string => String(e?.message || e).slice(0, 40);

const KbModelPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('dual');

  // ---- 双师页 ----
  const [dualMode, setDualMode] = useState<any>(null);
  const [dualStats, setDualStats] = useState<any>(null);
  const [samples, setSamples] = useState<any[]>([]);
  const [sampleKind, setSampleKind] = useState('golden');
  const [dualLoading, setDualLoading] = useState(false);
  const loadDual = async () => {
    if (dualLoading) return;
    setDualLoading(true);
    try {
      const [m, s, list] = await Promise.all([
        KbModelAPI.dualMode(),
        KbModelAPI.dualStats(),
        KbModelAPI.dualSamples(sampleKind),
      ]);
      setDualMode(m); setDualStats(s); setSamples(list);
      Taro.showToast({ title: '已刷新', icon: 'success' });
    } catch (e) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    } finally {
      setDualLoading(false);
    }
  };
  const pickSampleKind = async (k: string) => {
    setSampleKind(k);
    try {
      setSamples(await KbModelAPI.dualSamples(k));
    } catch (e) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };

  // ---- 知识库页 ----
  const [statusFilter, setStatusFilter] = useState('');
  const [entries, setEntries] = useState<any[]>([]);
  const [entriesLoading, setEntriesLoading] = useState(false);
  const [stats, setStats] = useState<any>(null);
  const loadEntries = async (status = statusFilter) => {
    if (entriesLoading) return;
    setEntriesLoading(true);
    try {
      const [list, s] = await Promise.all([
        KbModelAPI.entries(status || undefined),
        KbModelAPI.stats(),
      ]);
      setEntries(list); setStats(s);
      Taro.showToast({ title: '已刷新', icon: 'success' });
    } catch (e) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    } finally {
      setEntriesLoading(false);
    }
  };
  const handleEntryAction = async (e2: any, action: 'review' | 'publish' | 'retire') => {
    const names = { review: '审核通过', publish: '发布', retire: '退役' };
    const res = await Taro.showModal({
      title: names[action], content: `「${(e2.question || '').slice(0, 20)}」确定执行?`,
    });
    if (!res.confirm) return;
    try {
      if (action === 'review') await KbModelAPI.reviewEntry(e2.id, true);
      if (action === 'publish') await KbModelAPI.publishEntry(e2.id);
      if (action === 'retire') await KbModelAPI.retireEntry(e2.id);
      Taro.showToast({ title: '已执行', icon: 'success' });
      loadEntries();
    } catch (err) {
      Taro.showToast({ title: errMsg(err), icon: 'none' });
    }
  };

  // ---- 缺口页 ----
  const [gaps, setGaps] = useState<any[]>([]);
  const [gapsLoading, setGapsLoading] = useState(false);
  const loadGaps = async () => {
    if (gapsLoading) return;
    setGapsLoading(true);
    try {
      setGaps(await KbModelAPI.gaps());
      Taro.showToast({ title: '已刷新', icon: 'success' });
    } catch (e) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    } finally {
      setGapsLoading(false);
    }
  };
  const handleGap = async (g: any, action: 'ignore') => {
    try {
      await KbModelAPI.resolveGap(g.id, action);
      Taro.showToast({ title: '已忽略', icon: 'success' });
      loadGaps();
    } catch (e) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };

  // ---- 问答页 ----
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState<any>(null);
  const [asking, setAsking] = useState(false);
  const handleAsk = async () => {
    const q = question.trim();
    if (!q || asking) return;
    setAsking(true);
    try {
      setAnswer(await KbModelAPI.askDual(q));
    } catch (e) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    } finally {
      setAsking(false);
    }
  };

  /** 页签切换(自动加载对应数据, 无须手动点刷新) */
  const switchTab = (key: Tab) => {
    setTab(key);
    if (key === 'dual') loadDual();
    else if (key === 'entries') loadEntries();
    else if (key === 'gaps') loadGaps();
  };

  useEffect(() => { switchTab('dual'); }, []);

  return (
    <View className={styles.page}>
      <NavBar title="智能知识库训练模型" />

      <ScrollView scrollY className={styles.body}>
        <View className={styles.section}>
          <View className={styles.heroCard}>
            <View className={styles.heroTitle}>智能知识库训练模型</View>
            <View className={styles.heroSub}>
              双师对抗-协同 · 治理即奖励 · 缺口驱动 · 样本人工流转
            </View>
          </View>
        </View>

        <View className={styles.tabBar}>
          {TABS.map(t => (
            <View
              key={t.key}
              className={`${styles.tab} ${tab === t.key ? styles.tabActive : ''}`}
              onClick={() => switchTab(t.key)}
            >
              {t.label}
            </View>
          ))}
        </View>

        {/* ============ 双师引擎页 ============ */}
        {tab === 'dual' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>双师对抗-协同引擎</View>
            <View className={styles.runBtn} onClick={loadDual}>
              {dualLoading ? '刷新中…' : '刷新引擎状态'}
            </View>
            {dualMode && (
              <View className={styles.resultCard}>
                <View className={styles.statGrid}>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>{dualMode.mode || '—'}</View>
                    <View className={styles.statLbl}>当前档位</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>{dualStats?.goldenCount ?? '—'}</View>
                    <View className={styles.statLbl}>黄金标准</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>{dualStats?.negativeCount ?? '—'}</View>
                    <View className={styles.statLbl}>负例数</View>
                  </View>
                </View>
                <View className={styles.resItem}>
                  否决率: {pctStr(dualStats?.rejectionRate)}
                  (健康带 {dualStats?.healthyBand?.[0] ?? 0.4}~{dualStats?.healthyBand?.[1] ?? 0.6})
                </View>
                <View className={styles.resItem}>
                  评分直方图: {(dualStats?.scoreHistogram || []).join(' · ')}
                </View>
              </View>
            )}
            <View className={styles.cardTitle}>样本库(仅为建议数据, 流转须人工)</View>
            <View className={styles.chipRow}>
              {['golden', 'negative'].map(k => (
                <View
                  key={k}
                  className={`${styles.chip} ${sampleKind === k ? styles.chipActive : ''}`}
                  onClick={() => pickSampleKind(k)}
                >
                  {sampleKindName(k)}
                </View>
              ))}
            </View>
            {samples.length === 0 ? (
              <View className={styles.empty}>暂无{sampleKindName(sampleKind)}样本</View>
            ) : samples.map(s => (
              <View key={s.id} className={styles.rowCard}>
                <View className={styles.rowHead}>
                  <Text className={styles.rowBadge}>{sampleKindName(s.kind)}</Text>
                  <Text className={styles.rowScore}>{s.scores?.totalScore ?? '—'} 分</Text>
                </View>
                <View className={styles.rowQ}>{s.question}</View>
                <View className={styles.rowA}>{(s.answer || '').slice(0, 80)}</View>
              </View>
            ))}
          </View>
        )}

        {/* ============ 知识库页 ============ */}
        {tab === 'entries' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>知识条目治理</View>
            <View className={styles.runBtn} onClick={() => loadEntries()}>
              {entriesLoading ? '刷新中…' : '刷新列表'}
            </View>
            {stats && (
              <View className={styles.resultCard}>
                <View className={styles.statGrid}>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>{stats.totalEntries ?? '—'}</View>
                    <View className={styles.statLbl}>总条目</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>{pctStr(stats.hitRate)}</View>
                    <View className={styles.statLbl}>命中率</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>{stats.openGaps ?? '—'}</View>
                    <View className={styles.statLbl}>待处理缺口</View>
                  </View>
                </View>
              </View>
            )}
            <View className={styles.chipRow}>
              {STATUS_FILTERS.map(f => (
                <View
                  key={f}
                  className={`${styles.chip} ${statusFilter === f ? styles.chipActive : ''}`}
                  onClick={() => { setStatusFilter(f); loadEntries(f); }}
                >
                  {f ? entryStatusName(f) : '全部'}
                </View>
              ))}
            </View>
            {entries.length === 0 ? (
              <View className={styles.empty}>
                {entriesLoading ? '加载中...' : '暂无条目'}
              </View>
            ) : entries.map(e => (
              <View key={e.id} className={styles.rowCard}>
                <View className={styles.rowHead}>
                  <Text className={styles.rowBadge}>{entryStatusName(e.status)}</Text>
                  <Text className={styles.rowScore}>×{e.hitCount ?? 0} 命中</Text>
                </View>
                <View className={styles.rowQ}>{e.question}</View>
                <View className={styles.rowA}>{(e.answer || '').slice(0, 60)}</View>
                <View className={styles.actionRow}>
                  {e.status === 'pending' && (
                    <View className={styles.actionBtn} onClick={() => handleEntryAction(e, 'review')}>审核通过</View>
                  )}
                  {e.status === 'approved' && (
                    <View className={styles.actionBtn} onClick={() => handleEntryAction(e, 'publish')}>发布</View>
                  )}
                  {e.status === 'published' && (
                    <View className={styles.actionWarn} onClick={() => handleEntryAction(e, 'retire')}>退役</View>
                  )}
                </View>
              </View>
            ))}
          </View>
        )}

        {/* ============ 缺口页 ============ */}
        {tab === 'gaps' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>知识缺口队列(高频优先)</View>
            <View className={styles.runBtn} onClick={loadGaps}>
              {gapsLoading ? '刷新中…' : '刷新缺口'}
            </View>
            {gapsLoading && gaps.length === 0 ? (
              <View className={styles.empty}>缺口加载中…</View>
            ) : gaps.length === 0 ? (
              <View className={styles.empty}>暂无待处理缺口</View>
            ) : gaps.map(g => (
              <View key={g.id} className={styles.rowCard}>
                <View className={styles.rowHead}>
                  <Text className={styles.rowBadge}>{gapStatusName(g.status)}</Text>
                  <Text className={styles.rowScore}>×{g.askCount ?? 1} 次提问</Text>
                </View>
                <View className={styles.rowQ}>{g.question}</View>
                <View className={styles.rowA}>最近: {formatTime(g.lastAskedAt)}</View>
                {g.status === 'open' && (
                  <View className={styles.actionRow}>
                    <View className={styles.actionWarn} onClick={() => handleGap(g, 'ignore')}>忽略</View>
                  </View>
                )}
              </View>
            ))}
          </View>
        )}

        {/* ============ 问答测试页 ============ */}
        {tab === 'ask' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>双师问答测试(provider=dual)</View>
            <Textarea
              className={styles.askInput}
              value={question}
              onInput={e => setQuestion(e.detail.value)}
              placeholder="输入测试问题(如: 送礼送什么有面子)"
              maxlength={200}
            />
            <View
              className={`${styles.runBtn} ${asking ? styles.runDisabled : ''}`}
              onClick={handleAsk}
            >
              {asking ? '双师生成中...' : '发起双师问答'}
            </View>
            {answer && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  mode={answer.mode} · 置信度 {answer.confidence ?? '—'}
                  {answer.dual && ` · 胜出: ${answer.dual.winnerLabel}`}
                </View>
                <View className={styles.resItem}>{answer.answer || '(无答案)'}</View>
                {answer.dual?.scores?.map((s: any, i: number) => (
                  <View key={i} className={styles.resItem}>
                    [{s.label}] {s.totalScore}分 {s.verdict === 'pass' ? '✓' : '✗'}
                    (事实{s.dims?.factual} 结构{s.dims?.structure} 引用{s.dims?.citation})
                  </View>
                ))}
              </View>
            )}
          </View>
        )}

        <View className={styles.footNote}>
          观测面永不关停 · 样本仅为建议数据(黄金/负例流转必经人工审核) ·
          宪法域(合规分≥70)不可校准
        </View>
        <View className={styles.bottomSpace} />
      </ScrollView>
    </View>
  );
};

export default KbModelPage;
