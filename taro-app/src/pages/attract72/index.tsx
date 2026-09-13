/**
 * 72号·AI智能自动引流大模型 · 前端管理工作台(只读观测+显式动作)
 * 五页签: 画像(P1感知) → 信号(P1) → 洞察(P2因果) → 定律(P2知识) → 预算(P3博弈)
 * 口径: 观测面不受 ATTRACT72_MODE 影响 · 决策面(off 态 409):
 *       洞察结晶/定律发布/预分配生成 · 偏差重博弈=快环不受 MODE 影响
 * 铁律: 配额/偏差/系数数字 100% 来自后端确定性公式(前端只渲染)
 */
import React, { useState } from 'react';
import { View, Text, Input, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  Attract72API, subjectTypeName, personaTypeName,
  effectTypeName, statusName,
} from '@/api/attract72';

type Tab = 'persona' | 'signal' | 'insight' | 'law' | 'budget';

const TABS: { key: Tab; label: string }[] = [
  { key: 'persona', label: '画像' },
  { key: 'signal', label: '信号' },
  { key: 'insight', label: '洞察' },
  { key: 'law', label: '定律' },
  { key: 'budget', label: '预算' },
];

const errMsg = (e: any): string =>
  String(e?.message || e).slice(0, 40);

/** 0-1 比率 → 百分比(纯格式化) */
const pctStr = (v: any): string =>
  v == null ? '—' : `${(Number(v) * 100).toFixed(0)}%`;

const Attract72Page: React.FC = () => {
  const [tab, setTab] = useState<Tab>('persona');

  // ============ 画像页(P1 感知层) ============
  const [personas, setPersonas] = useState<any[]>([]);
  const [subjectFilter, setSubjectFilter] = useState('');
  const [syncing, setSyncing] = useState(false);
  const loadPersonas = async (st = subjectFilter) => {
    try {
      const res = await Attract72API.personas(
        st ? { subjectType: st } : undefined);
      setPersonas(Array.isArray(res) ? res : (res?.personas || []));
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };
  const handleSync = async () => {
    if (syncing) return;
    setSyncing(true);
    try {
      const res = await Attract72API.syncPersonas();
      Taro.showToast({
        title: `同步完成: 画像 ${res?.synced ?? 0} 条(新建 ${res?.created ?? 0})`,
        icon: 'none',
      });
      await loadPersonas();
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    } finally {
      setSyncing(false);
    }
  };

  // ============ 信号页(P1 外部信号总线) ============
  const [signals, setSignals] = useState<any[]>([]);
  const loadSignals = async () => {
    try {
      const res = await Attract72API.signals();
      setSignals(Array.isArray(res) ? res : (res?.signals || []));
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };

  // ============ 洞察页(P2 因果推理) ============
  const [insights, setInsights] = useState<any[]>([]);
  const [effectFilter, setEffectFilter] = useState('');
  const [running, setRunning] = useState(false);
  const loadInsights = async (ef = effectFilter) => {
    try {
      const res = await Attract72API.insights(
        ef ? { effectType: ef } : undefined);
      setInsights(Array.isArray(res) ? res : (res?.insights || []));
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };
  const handleRunCausal = async () => {
    if (running) return;
    setRunning(true);
    try {
      const res = await Attract72API.runCausal();
      Taro.showToast({
        title: `推理完成: 驱动 ${res?.drivers ?? 0} · 流失 ${res?.losses ?? 0}`,
        icon: 'none',
      });
      await loadInsights();
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    } finally {
      setRunning(false);
    }
  };

  // ============ 定律页(P2 知识结晶) ============
  const [laws, setLaws] = useState<any[]>([]);
  const [kindFilter, setKindFilter] = useState('');
  const loadLaws = async (k = kindFilter) => {
    try {
      const res = await Attract72API.laws(k || undefined as any);
      setLaws(Array.isArray(res) ? res : (res?.laws || []));
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };
  // 自然语言查询(确定性关键词路由)
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState<any>(null);
  const handleQuery = async () => {
    if (!question.trim()) return;
    try {
      setAnswer(await Attract72API.query(question.trim()));
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };

  // ============ 预算页(P3 预判博弈) ============
  const [forecast, setForecast] = useState<any>(null);
  const [exploration, setExploration] = useState<any>(null);
  const [rebalancing, setRebalancing] = useState(false);
  const loadBudget = async () => {
    try {
      const [f, e] = await Promise.all([
        Attract72API.forecast().catch(() => null),
        Attract72API.exploration().catch(() => null),
      ]);
      setForecast(f);
      setExploration(e);
    } catch (e2: any) {
      Taro.showToast({ title: errMsg(e2), icon: 'none' });
    }
  };
  const handleRebalance = async () => {
    if (rebalancing) return;
    setRebalancing(true);
    try {
      const res = await Attract72API.rebalance();
      Taro.showToast({
        title: `已重博弈: 偏差 ${pctStr(res?.deviation)} → 新方案 #${res?.newForecastId ?? '—'}`,
        icon: 'none',
      });
      await loadBudget();
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    } finally {
      setRebalancing(false);
    }
  };

  const effectCls = (t: string): string =>
    t === 'driver' ? styles.effectDriver
      : t === 'loss' ? styles.effectLoss : styles.effectNeutral;

  /** 预算方案(响应为 {mode, active, history}——active 空则无生效方案) */
  const activeForecast = forecast?.active || null;

  return (
    <View className={styles.page}>
      <NavBar title="72号·智能引流大模型" />
      <ScrollView scrollY className={styles.body}>
        <View className={styles.section}>
          <View className={styles.heroCard}>
            <View className={styles.heroTitle}>72号·AI智能自动引流大模型</View>
            <View className={styles.heroSub}>
              感知画像 · 因果洞察 · 定律结晶 · 预算博弈 · 反知识止损
            </View>
          </View>
        </View>

        <View className={styles.tabBar}>
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

        {/* ============ 画像页(P1 渠道人格) ============ */}
        {tab === 'persona' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>渠道人格画像(感知层 · 观测面)</View>
            <View className={styles.runBtn} onClick={handleSync}>
              {syncing ? '同步中...' : '感知面同步'}
            </View>
            <View className={styles.chipRow}>
              {['', 'member', 'influencer'].map(s => (
                <View
                  key={s || 'all'}
                  className={`${styles.chip} ${subjectFilter === s ? styles.chipActive : ''}`}
                  onClick={() => {
                    setSubjectFilter(s);
                    loadPersonas(s);
                  }}
                >
                  {s ? subjectTypeName(s) : '全部主体'}
                </View>
              ))}
            </View>
            <View className={styles.runBtn} onClick={() => loadPersonas()}>刷新列表</View>
            {personas.length === 0 ? (
              <View className={styles.empty}>暂无画像——点击「感知面同步」生成</View>
            ) : (
              <View className={styles.resultCard}>
                {personas.map(p => (
                  <View key={p.personaId} className={styles.row}>
                    <View className={styles.rowHead}>
                      <Text className={styles.rowId}>#{p.personaId}</Text>
                      <Text className={styles.rowLabel}>{p.name || `主体${p.subjectId}`}</Text>
                      <Text className={styles.effectBadge}>{personaTypeName(p.personaType)}</Text>
                    </View>
                    <View className={styles.rowMeta}>
                      {subjectTypeName(p.subjectType)} · 信任分 {p.trustScore ?? '—'} ·
                      置信 {pctStr(p.confidence)} · 转化 {pctStr(p.conversionRate)}
                    </View>
                  </View>
                ))}
              </View>
            )}
          </View>
        )}

        {/* ============ 信号页(P1 信号总线) ============ */}
        {tab === 'signal' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>外部信号流(节日/雷达事件 · 观测面)</View>
            <View className={styles.runBtn} onClick={loadSignals}>刷新信号</View>
            {signals.length === 0 ? (
              <View className={styles.empty}>暂无信号(同步后由信号总线摄取)</View>
            ) : (
              <View className={styles.resultCard}>
                {signals.map(s => (
                  <View key={s.signalId} className={styles.row}>
                    <View className={styles.rowHead}>
                      <Text className={styles.rowId}>#{s.signalId}</Text>
                      <Text className={styles.rowLabel}>{s.type}</Text>
                      <Text className={styles.effectBadge}>权重 {s.weight ?? '—'}</Text>
                      {s.consumed ? (
                        <Text className={styles.statusBadge}>已消费</Text>
                      ) : null}
                    </View>
                    <View className={styles.rowMeta}>
                      {s.ref || ''} · {String(s.createdAt || '').slice(0, 19).replace('T', ' ')}
                      {s.impactChannels ? ` · 影响 ${s.impactChannels}` : ''}
                    </View>
                  </View>
                ))}
              </View>
            )}
          </View>
        )}

        {/* ============ 洞察页(P2 因果推理) ============ */}
        {tab === 'insight' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>因果洞察(反事实对照 · 观测/快环)</View>
            <View className={styles.runBtn} onClick={handleRunCausal}>
              {running ? '推理中...' : '运行反事实推理'}
            </View>
            <View className={styles.chipRow}>
              {['', 'driver', 'loss', 'neutral'].map(f => (
                <View
                  key={f || 'all'}
                  className={`${styles.chip} ${effectFilter === f ? styles.chipActive : ''}`}
                  onClick={() => {
                    setEffectFilter(f);
                    loadInsights(f);
                  }}
                >
                  {f ? effectTypeName(f) : '全部效果'}
                </View>
              ))}
            </View>
            {insights.length === 0 ? (
              <View className={styles.empty}>暂无洞察——点击「运行反事实推理」</View>
            ) : (
              <View className={styles.resultCard}>
                {insights.map(i => (
                  <View key={i.insightId} className={styles.row}>
                    <View className={styles.rowHead}>
                      <Text className={styles.rowId}>#{i.insightId}</Text>
                      <Text className={styles.rowLabel}>{i.dimension}·{i.factor}</Text>
                      <Text className={`${styles.effectBadge} ${effectCls(i.effectType)}`}>
                        {effectTypeName(i.effectType)}
                      </Text>
                      <Text className={styles.statusBadge}>{statusName(i.status)}</Text>
                    </View>
                    <View className={styles.rowMeta}>
                      反事实分 {Number(i.counterfactualScore ?? 0).toFixed(2)} ·
                      出现 {pctStr(i.rateA)} vs 不出现 {pctStr(i.rateB)} ·
                      样本 {i.sampleSize ?? 0}/{i.baseSampleSize ?? 0} ·
                      置信 {pctStr(i.confidence)}
                    </View>
                  </View>
                ))}
              </View>
            )}
          </View>
        )}

        {/* ============ 定律页(P2 知识结晶) ============ */}
        {tab === 'law' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>定律台账(结晶/发布为决策面 · 观测面常开)</View>
            <View className={styles.chipRow}>
              {['', 'law', 'anti'].map(k => (
                <View
                  key={k || 'all'}
                  className={`${styles.chip} ${kindFilter === k ? styles.chipActive : ''}`}
                  onClick={() => {
                    setKindFilter(k);
                    loadLaws(k);
                  }}
                >
                  {k === 'law' ? '定律' : k === 'anti' ? '反知识' : '全部'}
                </View>
              ))}
            </View>
            <View className={styles.runBtn} onClick={() => loadLaws()}>刷新台账</View>
            {laws.length === 0 ? (
              <View className={styles.empty}>暂无定律(洞察 verified 后经 46 号结晶)</View>
            ) : (
              <View className={styles.resultCard}>
                {laws.map(l => (
                  <View key={l.lawId} className={styles.row}>
                    <View className={styles.rowHead}>
                      <Text className={styles.rowId}>#{l.lawId}</Text>
                      <Text className={styles.rowLabel}>{l.dimension}·{l.factor}</Text>
                      <Text className={`${styles.effectBadge} ${effectCls(l.effectType)}`}>
                        {effectTypeName(l.effectType)}
                      </Text>
                      <Text className={styles.statusBadge}>{statusName(l.status)}</Text>
                    </View>
                    <View className={styles.rowMeta}>
                      {l.kind === 'anti' ? '反知识(验证无效, 防重复试错)' : '正定律'}
                      {l.submittedAt ? ` · 提交 ${String(l.submittedAt).slice(0, 10)}` : ''}
                    </View>
                  </View>
                ))}
              </View>
            )}

            <View className={styles.cardTitle}>自然语言查询(确定性关键词路由)</View>
            <View className={styles.queryRow}>
              <Input
                className={styles.queryInput}
                placeholder="如: 哪些渠道是驱动因子"
                value={question}
                onInput={e => setQuestion(e.detail.value)}
              />
              <View className={styles.runBtn} onClick={handleQuery}>查询</View>
            </View>
            {answer ? (
              <View className={styles.answerCard}>
                <View className={styles.answerText}>
                  {typeof answer === 'string' ? answer : JSON.stringify(answer)}
                </View>
              </View>
            ) : null}
          </View>
        )}

        {/* ============ 预算页(P3 预判博弈) ============ */}
        {tab === 'budget' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>72h 预算预分配(方案+偏差 · 观测面)</View>
            <View className={styles.runBtn} onClick={loadBudget}>刷新预算状态</View>
            <View
              className={styles.runBtn}
              onClick={handleRebalance}
            >
              {rebalancing ? '重博弈中...' : '偏差重博弈(>15% 快环)'}
            </View>
            {forecast ? (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>
                  模型 {forecast.modelVersion || '—'} · 档位 {forecast.mode || 'off'}
                  {forecast.kill ? ' · KILL 制动中' : ''}
                </View>
                {activeForecast ? (
                  <>
                    <View className={styles.statGrid}>
                      <View className={styles.statCell}>
                        <View className={styles.statNum}>{activeForecast.forecastId ?? '—'}</View>
                        <View className={styles.statLbl}>方案号</View>
                      </View>
                      <View className={styles.statCell}>
                        <View className={styles.statNum}>¥{activeForecast.poolTotal ?? '—'}</View>
                        <View className={styles.statLbl}>月池切片</View>
                      </View>
                      <View className={styles.statCell}>
                        <View className={styles.statNum}>{pctStr(activeForecast.deviation)}</View>
                        <View className={styles.statLbl}>当前偏差</View>
                      </View>
                    </View>
                    <View className={styles.resHead}>渠道配额</View>
                    {(activeForecast.allocations || []).map((a: any, idx: number) => (
                      <View key={idx} className={styles.rowMeta}>
                        {a.channel ?? a.channelId ?? `渠道${idx + 1}`}:
                        基准 {pctStr(a.baseShare)} · 雷达提升 {pctStr(a.signalLift)} ·
                        定律助推 {pctStr(a.lawBoost)} → 配额 {pctStr(a.finalShare)}
                        {a.exploration ? '(探索)' : ''}
                      </View>
                    ))}
                    <View className={styles.resHead}>窗口</View>
                    <View className={styles.resItem}>
                      {String(activeForecast.windowStart || '').slice(0, 19).replace('T', ' ')}
                      ~{String(activeForecast.windowEnd || '').slice(0, 19).replace('T', ' ')}
                    </View>
                  </>
                ) : (
                  <View className={styles.empty}>暂无生效方案(决策面生成——ATTRACT72_MODE=off 时 409)</View>
                )}
                {(forecast.history || []).length > 0 && (
                  <>
                    <View className={styles.resHead}>历史方案({forecast.history.length})</View>
                    {(forecast.history || []).slice(0, 5).map((h: any) => (
                      <View key={h.forecastId} className={styles.resItem}>
                        #{h.forecastId} · 偏差 {pctStr(h.deviation)}
                        {h.windowStart ? ` · ${String(h.windowStart).slice(0, 10)}` : ''}
                      </View>
                    ))}
                  </>
                )}
              </View>
            ) : (
              <View className={styles.empty}>点击「刷新预算状态」加载</View>
            )}
            {exploration ? (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>探索基金(新渠道样本驱动上浮)</View>
                <View className={styles.resItem}>
                  比率 {pctStr(exploration.rate ?? exploration.explorationRate)} ·
                  状态 {exploration.status ?? '—'}
                  {exploration.candidates != null
                    ? ` · 候选渠道 ${exploration.candidates}` : ''}
                </View>
              </View>
            ) : null}
          </View>
        )}

        <View className={styles.footNote}>
          观测面(画像/信号/洞察/定律/预算状态)不受 ATTRACT72_MODE 影响;
          决策面(结晶/发布/预分配生成)off 态 409。奖励系数变更永远
          经 46 号建议书——LLM 禁入判定链, 数字 100% 确定性公式。
        </View>
        <View className={styles.bottomSpace} />
      </ScrollView>
    </View>
  );
};

export default Attract72Page;
