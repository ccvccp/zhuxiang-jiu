/**
 * 智启元·AI智能财务大模型 · 前端管理工作台
 * 六页签: 总览 → 智能问答 → 财务分析 → 预测沙盘 → 税务优化 → 进化决策
 * 口径: 全链确定性 · 建议永不自动执行 · 人类最终否决权
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input, Textarea } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  ZyAPI, ZyStatusVO, HealthVO, DupontVO, AttributionVO,
  ForecastVO, SandboxVO, DriversVO, TaxSimVO, TaxPoliciesVO,
  TaxHeatmapVO, CashScheduleVO, MemoVO, FeedbackVO, AnomalyVO,
  verdictName, feedbackTargetName, anomalyTypeName, riskLevelName,
} from '@/api/zy';

type Tab = 'overview' | 'qa' | 'analysis' | 'forecast' | 'tax' | 'evo';

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: '总览' },
  { key: 'qa', label: '问答' },
  { key: 'analysis', label: '分析' },
  { key: 'forecast', label: '预测' },
  { key: 'tax', label: '税务' },
  { key: 'evo', label: '进化' },
];

const fmt = (n: number): string => `¥${Number(n || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
const pct = (n: number): string => `${Number(n || 0) > 0 ? '+' : ''}${Number(n || 0).toFixed(1)}%`;

// 沙盘三维度预设
const SCENARIOS = [
  { label: '提价 10%', price: 0.1, volume: 0, cost: 0 },
  { label: '降价 10%', price: -0.1, volume: 0, cost: 0 },
  { label: '销量 +20%', price: 0, volume: 0.2, cost: 0 },
  { label: '成本 +5%', price: 0, volume: 0, cost: 0.05 },
];

// 投资备忘预设
const MEMO_PRESET = {
  initialInvestment: 500000, annualCashFlow: 150000,
  growthRate: 0.05, years: 5, discountRate: 0.08,
};

const ZhiQiYuanPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('overview');
  const [loading, setLoading] = useState(true);

  // 总览
  const [status, setStatus] = useState<ZyStatusVO | null>(null);
  // 问答
  const [question, setQuestion] = useState('');
  const [qaReply, setQaReply] = useState<{ answer: string; reasoning: string; intent: string } | null>(null);
  const [qaBusy, setQaBusy] = useState(false);
  // 分析
  const [health, setHealth] = useState<HealthVO | null>(null);
  const [dupont, setDupont] = useState<DupontVO | null>(null);
  const [attrib, setAttrib] = useState<AttributionVO | null>(null);
  // 预测
  const [forecast, setForecast] = useState<ForecastVO | null>(null);
  const [sandbox, setSandbox] = useState<SandboxVO | null>(null);
  const [drivers, setDrivers] = useState<DriversVO | null>(null);
  // 税务
  const [taxSim, setTaxSim] = useState<TaxSimVO | null>(null);
  const [policies, setPolicies] = useState<TaxPoliciesVO | null>(null);
  const [heatmap, setHeatmap] = useState<TaxHeatmapVO | null>(null);
  // 进化
  const [anomalies, setAnomalies] = useState<AnomalyVO[]>([]);
  const [cash, setCash] = useState<CashScheduleVO | null>(null);
  const [memo, setMemo] = useState<MemoVO | null>(null);
  const [feedbacks, setFeedbacks] = useState<FeedbackVO[]>([]);

  const loadOverview = useCallback(async () => {
    try {
      const [st, fb] = await Promise.all([
        ZyAPI.status().catch(() => null),
        ZyAPI.feedbacks(10).catch(() => [] as FeedbackVO[]),
      ]);
      setStatus(st);
      setFeedbacks(fb);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadOverview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useDidShow(() => {
    loadOverview();
  });

  /** 切页签时惰性加载对应数据 */
  const onTab = async (key: Tab) => {
    setTab(key);
    if (key === 'analysis' && !health) {
      const [h, d, a] = await Promise.all([
        ZyAPI.health().catch(() => null),
        ZyAPI.dupont().catch(() => null),
        ZyAPI.attribution().catch(() => null),
      ]);
      setHealth(h); setDupont(d); setAttrib(a);
    }
    if (key === 'forecast' && !forecast) {
      const [f, dr] = await Promise.all([
        ZyAPI.forecast(6).catch(() => null),
        ZyAPI.drivers().catch(() => null),
      ]);
      setForecast(f); setDrivers(dr);
    }
    if (key === 'tax' && !taxSim) {
      const [sim, pol, hm] = await Promise.all([
        ZyAPI.taxSimulate({ amount: 100000, quantity: 100 }).catch(() => null),
        ZyAPI.taxPolicies(['小微', '白酒']).catch(() => null),
        ZyAPI.taxHeatmap().catch(() => null),
      ]);
      setTaxSim(sim); setPolicies(pol); setHeatmap(hm);
    }
    if (key === 'evo' && !cash) {
      const [an, cs, mm] = await Promise.all([
        ZyAPI.anomalies().catch(() => [] as AnomalyVO[]),
        ZyAPI.cashSchedule(90).catch(() => null),
        ZyAPI.memos(10).catch(() => [] as MemoVO[]),
      ]);
      setAnomalies(an); setCash(cs);
      if (mm?.length) setMemo(mm[0]);
    }
  };

  /** 智能问答 */
  const ask = async () => {
    if (question.trim().length < 2) {
      Taro.showToast({ title: '请输入至少 2 个字的问题', icon: 'none' });
      return;
    }
    setQaBusy(true);
    try {
      const r = await ZyAPI.qa(question.trim());
      setQaReply({ answer: r.answer, reasoning: r.reasoning, intent: r.intent });
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    } finally {
      setQaBusy(false);
    }
  };

  /** 情景沙盘 */
  const runSandbox = async (s: typeof SCENARIOS[0]) => {
    try {
      const r = await ZyAPI.sandbox({
        priceDelta: s.price, volumeDelta: s.volume, costDelta: s.cost,
      });
      setSandbox(r);
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 反馈闭环(采纳/拒绝驱动预测参数进化) */
  const sendFeedback = async (targetType: string, verdict: string) => {
    try {
      await ZyAPI.feedback({ targetType, verdict, note: '工作台标记' });
      Taro.showToast({ title: `已${verdictName(verdict)}`, icon: 'success' });
      setFeedbacks(await ZyAPI.feedbacks(10).catch(() => [] as FeedbackVO[]));
      setStatus(await ZyAPI.status().catch(() => null));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 投资备忘(DCF) */
  const runMemo = async () => {
    try {
      const m = await ZyAPI.decisionMemo({ type: 'investment', params: MEMO_PRESET });
      setMemo(m);
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  const sevColor = (sev: string): string =>
    sev === 'critical' || sev === 'high' ? styles.sevHigh
      : sev === 'medium' ? styles.sevMid : styles.sevLow;

  return (
    <View className={styles.page}>
      <NavBar title="智启元·AI财务" />

      <ScrollView scrollY className={styles.body}>
        {/* ============ 页签栏 ============ */}
        <View className={styles.tabBar}>
          {TABS.map(t => (
            <View
              key={t.key}
              className={`${styles.tab} ${tab === t.key ? styles.tabActive : ''}`}
              onClick={() => onTab(t.key)}
            >
              {t.label}
            </View>
          ))}
        </View>

        {/* ============ 一、总览 ============ */}
        {tab === 'overview' && (
          <View className={styles.section}>
            <View className={styles.heroCard}>
              <View className={styles.heroTitle}>智启元 · 自主进化态</View>
              <View className={styles.heroSub}>
                越用越懂你 · 全链确定性 · 建议永不自动执行
              </View>
              {status && (
                <View className={styles.heroStats}>
                  <View className={styles.heroStat}>
                    <View className={styles.heroNum}>{status.feedbacks.total}</View>
                    <View className={styles.heroLbl}>反馈总数</View>
                  </View>
                  <View className={styles.heroStat}>
                    <View className={styles.heroNum}>{status.feedbacks.adopted}</View>
                    <View className={styles.heroLbl}>已采纳</View>
                  </View>
                  <View className={styles.heroStat}>
                    <View className={styles.heroNum}>{status.feedbacks.trendWeight}</View>
                    <View className={styles.heroLbl}>趋势权重</View>
                  </View>
                  <View className={styles.heroStat}>
                    <View className={styles.heroNum}>{status.anomalies.length}</View>
                    <View className={styles.heroLbl}>异常待察</View>
                  </View>
                </View>
              )}
              {status?.note && <View className={styles.heroNote}>{status.note}</View>}
            </View>

            {/* 异常自发现 */}
            <View className={styles.cardTitle}>异常自发现(三检测器)</View>
            {status && status.anomalies.length === 0 && (
              <View className={styles.empty}>月度财务流平稳, 无异常信号</View>
            )}
            {status?.anomalies.map((a, i) => (
              <View key={i} className={styles.alertCard}>
                <View className={styles.alertHead}>
                  <View className={`${styles.alertTag} ${sevColor(a.severity)}`}>
                    {anomalyTypeName(a.type)}
                  </View>
                  <View className={styles.alertMonth}>{a.month}</View>
                </View>
                <View className={styles.alertDetail}>{a.detail}</View>
                <View className={styles.alertDisp}>{a.disposition}</View>
              </View>
            ))}

            {/* 反馈流 */}
            <View className={styles.cardTitle}>反馈进化留痕</View>
            {feedbacks.length === 0 && !loading && (
              <View className={styles.empty}>暂无反馈记录——在「进化」页签标记 AI 建议</View>
            )}
            {feedbacks.map(f => (
              <View key={f.feedbackId} className={styles.fbItem}>
                <View className={styles.fbRow}>
                  <Text className={styles.fbTarget}>{feedbackTargetName(f.targetType)}</Text>
                  <Text className={
                    f.verdict === 'adopted' ? styles.fbAdopted
                      : f.verdict === 'rejected' ? styles.fbRejected : styles.fbCorrected
                  }>{verdictName(f.verdict)}</Text>
                </View>
                {f.trendWeightAfter != null && (
                  <View className={styles.fbWeight}>趋势权重 → {f.trendWeightAfter}</View>
                )}
              </View>
            ))}
          </View>
        )}

        {/* ============ 二、智能问答 ============ */}
        {tab === 'qa' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>自然语言财务问答</View>
            <View className={styles.qaSub}>收入 · 成本 · 税负 · 现金流 · 异常(五域意图路由)</View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                value={question}
                onInput={e => setQuestion(e.detail.value)}
                placeholder="例如: 本月收入多少? / 税负情况 / 有什么异常"
                maxlength={200}
              />
              <View className={styles.qaBtn} onClick={ask}>
                {qaBusy ? '分析中…' : '提问'}
              </View>
            </View>
            {qaReply && (
              <View className={styles.qaAnswerCard}>
                <View className={styles.qaIntent}>意图: {qaReply.intent}</View>
                <View className={styles.qaAnswer}>{qaReply.answer}</View>
                <View className={styles.qaReason}>{qaReply.reasoning}</View>
              </View>
            )}
            <View className={styles.qaFooter}>数字 100% 来自查询层 · LLM 不参与判定链</View>
          </View>
        )}

        {/* ============ 三、财务分析 ============ */}
        {tab === 'analysis' && (
          <View className={styles.section}>
            {/* 健康度五维 */}
            <View className={styles.cardTitle}>财务健康度(五维 Sigmoid)</View>
            {health ? (
              <View className={styles.healthCard}>
                <View className={styles.healthHead}>
                  <View className={styles.healthGrade}>{health.grade}</View>
                  <View className={styles.healthScore}>{health.totalScore} 分</View>
                </View>
                {health.dimensions.map(d => (
                  <View key={d.dim} className={styles.dimRow}>
                    <View className={styles.dimName}>{d.dim}</View>
                    <View className={styles.dimBar}>
                      <View className={styles.dimBarFill} style={{ width: `${Math.min(100, d.score)}%` }} />
                    </View>
                    <View className={styles.dimScoreVal}>{d.score}</View>
                    <View className={styles.dimExplain}>{d.explain}</View>
                  </View>
                ))}
              </View>
            ) : <View className={styles.empty}>健康度计算中…</View>}

            {/* 杜邦分析 */}
            <View className={styles.cardTitle}>杜邦分析(ROE 分解)</View>
            {dupont ? (
              <View className={styles.dupontCard}>
                <View className={styles.roeRow}>
                  <Text className={styles.roeLabel}>ROE {dupont.period}</Text>
                  <Text className={styles.roeVal}>{(dupont.roe * 100).toFixed(1)}%</Text>
                </View>
                <View className={styles.roeFormula}>
                  净利率 {(dupont.factors.netMargin * 100).toFixed(1)}% × 周转 {dupont.factors.assetTurnover} × 权益乘数 {dupont.factors.equityMultiplier}
                </View>
                <View className={styles.roeInterp}>{dupont.interpretation}</View>
              </View>
            ) : <View className={styles.empty}>杜邦分析计算中…</View>}

            {/* 净利归因 */}
            <View className={styles.cardTitle}>净利环比归因(四因素)</View>
            {attrib ? (
              <View className={styles.attribCard}>
                {attrib.comparable ? (
                  <>
                    <View className={styles.attribDelta}>
                      净利变动 {fmt(attrib.delta)}({attrib.prevPeriod} → {attrib.period})
                    </View>
                    {attrib.factors.map(f => (
                      <View key={f.factor} className={styles.attribRow}>
                        <View className={styles.attribFactor}>{f.factor}</View>
                        <View className={`${styles.attribEffect} ${f.effect >= 0 ? styles.effPos : styles.effNeg}`}>
                          {f.effect >= 0 ? '+' : ''}{fmt(f.effect)}
                        </View>
                      </View>
                    ))}
                  </>
                ) : (
                  <View className={styles.empty}>{attrib.note}</View>
                )}
              </View>
            ) : <View className={styles.empty}>归因分析计算中…</View>}
          </View>
        )}

        {/* ============ 四、预测沙盘 ============ */}
        {tab === 'forecast' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>滚动预测(加权移动平均)</View>
            {forecast ? (
              <View className={styles.fcCard}>
                {forecast.rows.map((r, i) => (
                  <View key={i} className={styles.fcRow}>
                    <View className={styles.fcStep}>+{r.step}月</View>
                    <View className={styles.fcNet}>{fmt(r.netAmount)}</View>
                    <View className={styles.fcProfit}>净利 {fmt(r.netProfit)}</View>
                  </View>
                ))}
                <View className={styles.fcBasis}>
                  {forecast.basis.weightScheme} · 历史 {forecast.basis.historyMonths} 月
                  {forecast.basis.trendApplied ? ' · 含趋势项' : ' · 稳健基线'}
                </View>
              </View>
            ) : <View className={styles.empty}>预测计算中…</View>}

            <View className={styles.cardTitle}>What-if 情景沙盘</View>
            <View className={styles.scenGrid}>
              {SCENARIOS.map(s => (
                <View key={s.label} className={styles.scenBtn} onClick={() => runSandbox(s)}>
                  {s.label}
                </View>
              ))}
            </View>
            {sandbox ? (
              <View className={styles.sbCard}>
                <View className={styles.sbBase}>
                  基线 {String(sandbox.baseline.period || '')}: 收入 {fmt(Number(sandbox.baseline.revenue || 0))} · 净利 {fmt(Number(sandbox.baseline.netProfit || 0))}
                </View>
                <View className={styles.sbImpactRow}>
                  <View className={styles.sbImpact}>
                    <View className={styles.sbNum}>{fmt(sandbox.impacts.netProfit)}</View>
                    <View className={styles.sbLbl}>净利变动</View>
                  </View>
                  <View className={styles.sbImpact}>
                    <View className={styles.sbNum}>{fmt(sandbox.impacts.revenue)}</View>
                    <View className={styles.sbLbl}>收入变动</View>
                  </View>
                  <View className={styles.sbImpact}>
                    <View className={styles.sbNum}>{fmt(sandbox.impacts.tax)}</View>
                    <View className={styles.sbLbl}>税负变动</View>
                  </View>
                </View>
                {sandbox.mitigations?.length > 0 && (
                  <View className={styles.sbMitigation}>
                    {sandbox.mitigations.map((m, i) => (
                      <View key={i} className={styles.sbMitItem}>· {m}</View>
                    ))}
                  </View>
                )}
              </View>
            ) : <View className={styles.empty}>选择一个情景开始推演</View>}

            <View className={styles.cardTitle}>收入驱动因素</View>
            {drivers ? (
              <View className={styles.drvCard}>
                {drivers.drivers.map(d => (
                  <View key={d.factor} className={styles.drvRow}>
                    <View className={styles.drvName}>{d.factor}</View>
                    <View className={styles.drvBar}>
                      <View className={styles.drvBarFill} style={{ width: `${Math.min(100, d.sensitivity * 100)}%` }} />
                    </View>
                    <View className={styles.drvCorr}>
                      {d.direction} {d.effectiveCorrelation.toFixed(2)}
                    </View>
                  </View>
                ))}
              </View>
            ) : <View className={styles.empty}>驱动因素建模中…</View>}
          </View>
        )}

        {/* ============ 五、税务优化 ============ */}
        {tab === 'tax' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>税负模拟器(四结构对比)</View>
            {taxSim ? (
              <View className={styles.taxCard}>
                {taxSim.structures.map(s => (
                  <View key={s.structure} className={`${styles.taxRow} ${s.structure === taxSim.recommendation.best ? styles.taxBest : ''}`}>
                    <View className={styles.taxName}>{s.structureName}</View>
                    <View className={styles.taxTotal}>{fmt(s.total)}</View>
                    <View className={styles.taxRate}>{(s.effectiveRate * 100).toFixed(2)}%</View>
                  </View>
                ))}
                <View className={styles.taxRec}>
                  最优: {taxSim.recommendation.bestName}
                  (较最差省 {fmt(taxSim.recommendation.savingVsWorst)})
                </View>
                <View className={styles.taxNote}>{taxSim.recommendation.note}</View>
              </View>
            ) : <View className={styles.empty}>模拟中…</View>}

            <View className={styles.cardTitle}>风险热力图(五维扫描)</View>
            {heatmap ? (
              <View className={styles.hmCard}>
                {heatmap.risks.map(r => (
                  <View key={r.risk} className={styles.hmRow}>
                    <View className={`${styles.hmTag} ${sevColor(r.severity)}`}>
                      {r.severityName}
                    </View>
                    <View className={styles.hmBody}>
                      <View className={styles.hmRisk}>{r.risk}</View>
                      <View className={styles.hmDetail}>{r.detail}</View>
                    </View>
                  </View>
                ))}
                <View className={styles.hmOverall}>
                  综合: {heatmap.overall.levelName}(首险 {heatmap.overall.topRisk})
                </View>
              </View>
            ) : <View className={styles.empty}>风险扫描中…</View>}

            <View className={styles.cardTitle}>优惠政策匹配</View>
            {policies ? (
              <View className={styles.polCard}>
                {policies.policies.map(p => (
                  <View key={p.policyId} className={`${styles.polItem} ${p.eligible ? styles.polHit : ''}`}>
                    <View className={styles.polTitle}>{p.title}</View>
                    <View className={styles.polContent}>{p.content}</View>
                    <View className={styles.polCond}>条件: {p.condition}</View>
                    {p.eligible && <View className={styles.polSugg}>{p.suggestion}</View>}
                  </View>
                ))}
                <View className={styles.polNote}>{policies.note}</View>
              </View>
            ) : <View className={styles.empty}>政策匹配中…</View>}
          </View>
        )}

        {/* ============ 六、进化决策 ============ */}
        {tab === 'evo' && (
          <View className={styles.section}>
            {/* 反馈进化 */}
            <View className={styles.cardTitle}>反馈进化(标记 AI 建议)</View>
            <View className={styles.evoPanel}>
              <View className={styles.evoRow}>
                <View className={styles.evoLbl}>滚动预测</View>
                <View className={styles.evoBtns}>
                  <View className={styles.evoBtn} onClick={() => sendFeedback('forecast', 'adopted')}>👍 采纳</View>
                  <View className={styles.evoBtn} onClick={() => sendFeedback('forecast', 'rejected')}>👎 拒绝</View>
                </View>
              </View>
              <View className={styles.evoRow}>
                <View className={styles.evoLbl}>税务建议</View>
                <View className={styles.evoBtns}>
                  <View className={styles.evoBtn} onClick={() => sendFeedback('tax_suggestion', 'adopted')}>👍 采纳</View>
                  <View className={styles.evoBtn} onClick={() => sendFeedback('tax_suggestion', 'corrected')}>✏ 修正</View>
                </View>
              </View>
              <View className={styles.evoHint}>
                采纳→趋势权重 +0.1 / 拒绝→-0.1(安全阀 [0, 0.8])
              </View>
            </View>

            {/* 异常 */}
            <View className={styles.cardTitle}>异常信号</View>
            {anomalies.length === 0 && <View className={styles.empty}>无异常信号</View>}
            {anomalies.map((a, i) => (
              <View key={i} className={styles.alertCard}>
                <View className={styles.alertHead}>
                  <View className={`${styles.alertTag} ${sevColor(a.severity)}`}>
                    {anomalyTypeName(a.type)}
                  </View>
                  <View className={styles.alertMonth}>{a.month}</View>
                </View>
                <View className={styles.alertDetail}>{a.detail}</View>
              </View>
            ))}

            {/* 资金调度 */}
            <View className={styles.cardTitle}>资金智能调度(90 日推演)</View>
            {cash ? (
              <View className={styles.cashCard}>
                <View className={styles.cashStats}>
                  <View className={styles.cashStat}>
                    <View className={styles.cashNum}>{fmt(cash.dailyInflow)}</View>
                    <View className={styles.cashLbl}>日均流入</View>
                  </View>
                  <View className={styles.cashStat}>
                    <View className={styles.cashNum}>{fmt(cash.dailyOutflow)}</View>
                    <View className={styles.cashLbl}>日均流出</View>
                  </View>
                  <View className={styles.cashStat}>
                    <View className={styles.cashNum}>
                      {cash.firstGapDay != null ? `第 ${cash.firstGapDay} 天` : '无缺口'}
                    </View>
                    <View className={styles.cashLbl}>首次缺口</View>
                  </View>
                </View>
                {cash.suggestions.map((s, i) => (
                  <View key={i} className={styles.cashSugg}>· {s}</View>
                ))}
                <View className={styles.cashNote}>{cash.note}</View>
              </View>
            ) : <View className={styles.empty}>资金排程推演中…</View>}

            {/* 决策备忘 */}
            <View className={styles.cardTitle}>投资决策备忘(DCF)</View>
            <View className={styles.memoBtn} onClick={runMemo}>
              生成投资备忘(示例参数)
            </View>
            {memo ? (
              <View className={styles.memoCard}>
                <View className={styles.memoNpv}>
                  NPV <Text className={memo.npv >= 0 ? styles.npvPos : styles.npvNeg}>{fmt(memo.npv)}</Text>
                  <Text className={styles.memoPayback}>
                    {memo.paybackYears != null ? ` · 回收 ${memo.paybackYears} 年` : ''}
                  </Text>
                </View>
                <View className={styles.memoConclusion}>{memo.conclusion}</View>
                {memo.sensitivities.map(s => (
                  <View key={s.discountRate} className={styles.memoSens}>
                    r={(s.discountRate * 100).toFixed(0)}% → NPV {fmt(s.npv)}
                  </View>
                ))}
                <View className={styles.memoAssume}>{memo.assumptionNote}</View>
              </View>
            ) : null}

            {status?.note && (
              <View className={styles.evoFooter}>{status.note}</View>
            )}
          </View>
        )}

        <View className={styles.bottomSpace} />
      </ScrollView>
    </View>
  );
};

export default ZhiQiYuanPage;
