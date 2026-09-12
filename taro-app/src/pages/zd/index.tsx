/**
 * 智单·AI智能订单大模型 · 前端管理工作台(订单域只读智能中枢)
 * 四页签: 洞察(P0) → 预测(P1) → 风控(P2) → 进化(P3)
 * 口径: 确定性规则引擎 · 建议书模式 · 裁决权永在人工(永不自动执行)
 * 所有业务数字 100% 来自 /api/order-ai/* 查询层(数字不出现在模板层)
 */
import React, { useState } from 'react';
import { View, Text, ScrollView, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  ZdAPI, qaDomainName, verdictName, feedbackTargetName,
  anomalyTypeName, memoTopicName, orderStatusName,
} from '@/api/zd';

type Tab = 'insight' | 'forecast' | 'risk' | 'evo';

const TABS: { key: Tab; label: string }[] = [
  { key: 'insight', label: '洞察' },
  { key: 'forecast', label: '预测' },
  { key: 'risk', label: '风控' },
  { key: 'evo', label: '进化' },
];

/** 反馈目标七类(对齐后端 FEEDBACK_TARGETS) */
const FEEDBACK_TARGETS = [
  'eta_forecast', 'volume_forecast', 'whatif', 'refund_score',
  'anomaly_scan', 'checkup', 'portrait',
];
/** 裁决三态 */
const VERDICTS = ['adopted', 'corrected', 'rejected'];
/** 备忘录主题二选一 */
const MEMO_TOPICS = ['promotion_prep', 'timeout_policy'];

/** 0-1 比率 → 百分比文案(纯格式化, 不引入业务数字) */
const pctStr = (v: any): string =>
  `${(Number(v || 0) * 100).toFixed(2)}%`;

const errMsg = (e: any): string =>
  String(e?.message || e).slice(0, 30);

const ZhiDanPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('insight');

  // ============ 洞察 P0 ============
  const [overview, setOverview] = useState<any>(null);
  const [query, setQuery] = useState('现在有多少订单');
  const [qaReply, setQaReply] = useState<any>(null);
  const [checkupReport, setCheckupReport] = useState<any>(null);
  const [portraitData, setPortraitData] = useState<any>(null);

  const loadOverview = async () => {
    setOverview(await ZdAPI.overview().catch(() => null));
  };
  const runQa = async () => {
    if (!query.trim()) {
      Taro.showToast({ title: '请输入订单问题', icon: 'none' });
      return;
    }
    try {
      setQaReply(await ZdAPI.qa(query.trim()));
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };
  const runCheckup = async () => {
    try {
      setCheckupReport(await ZdAPI.checkup());
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };
  const runPortrait = async () => {
    try {
      setPortraitData(await ZdAPI.portrait());
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };

  // ============ 预测 P1 ============
  const [etaData, setEtaData] = useState<any>(null);
  const [wiDelay, setWiDelay] = useState('1');
  const [wiCancel, setWiCancel] = useState('0');
  const [wiAov, setWiAov] = useState('0');
  const [whatifData, setWhatifData] = useState<any>(null);
  const [forecastData, setForecastData] = useState<any>(null);

  const runEta = async () => {
    setEtaData(await ZdAPI.eta().catch(() => null));
  };
  const runWhatif = async () => {
    try {
      setWhatifData(await ZdAPI.whatif({
        shipDelayDays: Number(wiDelay) || 0,
        cancelRateDelta: Number(wiCancel) || 0,
        aovDelta: Number(wiAov) || 0,
      }));
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };
  const runForecast = async () => {
    setForecastData(await ZdAPI.forecast(12).catch(() => null));
  };

  // ============ 风控 P2 ============
  const [orderId, setOrderId] = useState('');
  const [refundData, setRefundData] = useState<any>(null);
  const [scanData, setScanData] = useState<any>(null);
  const [anomalyList, setAnomalyList] = useState<any[]>([]);
  const [anomalyLoaded, setAnomalyLoaded] = useState(false);

  const runRefundScore = async () => {
    if (!orderId.trim()) {
      Taro.showToast({ title: '请输入订单号', icon: 'none' });
      return;
    }
    try {
      setRefundData(await ZdAPI.refundScore(orderId.trim()));
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };
  const runScan = async () => {
    try {
      const r = await ZdAPI.anomalyScan();
      setScanData(r);
      setAnomalyList(await ZdAPI.anomalies().catch(() => []));
      setAnomalyLoaded(true);
      Taro.showToast({ title: `检出 ${r.anomalyCount} 条异常`, icon: 'success' });
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };
  const loadAnomalies = async () => {
    setAnomalyList(await ZdAPI.anomalies().catch(() => []));
    setAnomalyLoaded(true);
  };

  // ============ 进化 P3 ============
  const [paramsData, setParamsData] = useState<any>(null);
  const [fbTarget, setFbTarget] = useState('eta_forecast');
  const [fbVerdict, setFbVerdict] = useState('adopted');
  const [fbNote, setFbNote] = useState('');
  const [fbResult, setFbResult] = useState<any>(null);
  const [detectData, setDetectData] = useState<any>(null);
  const [memoTopic, setMemoTopic] = useState('promotion_prep');
  const [memoNotes, setMemoNotes] = useState('');
  const [memoData, setMemoData] = useState<any>(null);
  const [memoList, setMemoList] = useState<any[]>([]);
  const [memoLoaded, setMemoLoaded] = useState(false);

  const loadParams = async () => {
    setParamsData(await ZdAPI.params().catch(() => null));
  };
  const submitFeedback = async () => {
    try {
      const r = await ZdAPI.feedback(fbTarget, fbVerdict, fbNote.trim());
      setFbResult(r);
      Taro.showToast({ title: `已${verdictName(fbVerdict)}(留痕)`, icon: 'success' });
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };
  const runDetect = async () => {
    setDetectData(await ZdAPI.detect().catch(() => null));
  };
  const runMemo = async () => {
    try {
      const r = await ZdAPI.memo(memoTopic, memoNotes.trim());
      setMemoData(r);
      setMemoList(await ZdAPI.memos().catch(() => []));
      setMemoLoaded(true);
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };
  const loadMemos = async () => {
    setMemoList(await ZdAPI.memos().catch(() => []));
    setMemoLoaded(true);
  };

  const sevColor = (s: string): string =>
    s === 'high' ? styles.sevHigh : s === 'mid' ? styles.sevMid
      : styles.sevLow;

  return (
    <View className={styles.page}>
      <NavBar title="智单·AI智能订单大模型" />

      <ScrollView scrollY className={styles.body}>
        <View className={styles.section}>
          <View className={styles.heroCard}>
            <View className={styles.heroTitle}>智单·AI智能订单大模型</View>
            <View className={styles.heroSub}>确定性引擎 · 建议书模式</View>
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

        {/* ============ 洞察 P0 ============ */}
        {tab === 'insight' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>织物总览(订单数据底座)</View>
            <View className={styles.runBtn} onClick={loadOverview}>刷新织物总览</View>
            {overview ? (
              <View className={styles.resultCard}>
                <View className={styles.statGrid}>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>{overview.totalOrders}</View>
                    <View className={styles.statLbl}>累计订单</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>¥{overview.gmv}</View>
                    <View className={styles.statLbl}>GMV(支付口径)</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>{pctStr(overview.refundRate)}</View>
                    <View className={styles.statLbl}>退款率</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>¥{overview.avgOrderValue}</View>
                    <View className={styles.statLbl}>平均客单价</View>
                  </View>
                </View>
                {Object.entries(overview.statusDistribution || {})
                  .filter(([, v]) => Number(v) > 0)
                  .map(([k, v]) => (
                    <View key={k} className={styles.candRow}>
                      <Text>{orderStatusName(k)}</Text>
                      <Text className={styles.candScores}>{v} 单</Text>
                    </View>
                  ))}
                <View className={styles.resItem}>
                  平均履约 {overview.avgFulfillmentHours} 小时(样本 {overview.fulfillmentSamples} 单)
                </View>
                <View className={styles.formulaBox}>{overview.note}</View>
              </View>
            ) : (
              <View className={styles.empty}>点击上方按钮加载织物总览</View>
            )}

            <View className={styles.cardTitle}>订单智能问答(五域)</View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                value={query}
                onInput={e => setQuery(e.detail.value)}
                placeholder="如: 现在有多少订单 / GMV 是多少 / 退款率多少"
                maxlength={100}
              />
              <View className={styles.qaBtn} onClick={runQa}>发送</View>
            </View>
            {qaReply && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    {qaReply.domainName || qaDomainName(qaReply.domain)}
                  </View>
                  <View className={styles.resBatch}>
                    快照 {Object.keys(qaReply.dataSnapshot || {}).length} 项
                  </View>
                </View>
                <View className={styles.resItem}>{qaReply.answer}</View>
                <View className={styles.formulaBox}>{qaReply.reasoning}</View>
              </View>
            )}

            <View className={styles.cardTitle}>订单健康体检(四维)</View>
            <View className={styles.runBtn} onClick={runCheckup}>立即体检</View>
            {checkupReport && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>{checkupReport.grade}</View>
                  <View className={styles.resScore}>{checkupReport.totalScore}/100</View>
                </View>
                {(checkupReport.dimensions || []).map((d: any) => (
                  <View key={d.dim} className={styles.candRow}>
                    <Text>{d.dim}</Text>
                    <Text className={styles.candScores}>{d.score}/100 · {d.explain}</Text>
                  </View>
                ))}
                {(checkupReport.suggestions || []).map((s: string, i: number) => (
                  <View key={i} className={styles.resItem}>• {s}</View>
                ))}
                <View className={styles.formulaBox}>{checkupReport.formula}</View>
                <View className={styles.adviceRow}>
                  <Text className={styles.adviceTag}>建议书</Text>
                  <Text className={styles.adviceText}>{checkupReport.disposition}</Text>
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>三维画像(会员×商品×时段)</View>
            <View className={styles.runBtn} onClick={runPortrait}>生成画像</View>
            {portraitData && (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>TOP 会员(按已支付金额)</View>
                {(portraitData.topMembers || []).slice(0, 5).map((m: any) => (
                  <View key={m.memberId} className={styles.candRow}>
                    <Text>会员 {m.memberId}</Text>
                    <Text className={styles.candScores}>
                      {m.orderCount} 单 · ¥{m.amount} · {pctStr(m.share)}
                    </Text>
                  </View>
                ))}
                <View className={styles.resItem}>TOP 商品(按折前商品额)</View>
                {(portraitData.topProducts || []).slice(0, 5).map((p: any) => (
                  <View key={p.productId} className={styles.candRow}>
                    <Text>{p.productName}</Text>
                    <Text className={styles.candScores}>{p.quantity} 件 · ¥{p.amount}</Text>
                  </View>
                ))}
                <View className={styles.resItem}>
                  时段分布(峰值: {portraitData.peakBucket || '—'})
                </View>
                {(portraitData.timeBuckets || []).map((b: any) => (
                  <View key={b.bucket} className={styles.candRow}>
                    <Text>{b.bucket}</Text>
                    <Text className={styles.candScores}>{b.count} 单 · {pctStr(b.share)}</Text>
                  </View>
                ))}
                <View className={styles.formulaBox}>{portraitData.note}</View>
                <View className={styles.adviceRow}>
                  <Text className={styles.adviceTag}>建议书</Text>
                  <Text className={styles.adviceText}>{portraitData.disposition}</Text>
                </View>
              </View>
            )}
          </View>
        )}

        {/* ============ 预测 P1 ============ */}
        {tab === 'forecast' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>履约 ETA 加权预测</View>
            <View className={styles.runBtn} onClick={runEta}>刷新 ETA</View>
            {etaData && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    {etaData.etaHours != null ? `${etaData.etaHours} 小时` : '无样本'}
                  </View>
                  <View className={styles.resBatch}>样本 {etaData.sampleSize} 单</View>
                </View>
                {etaData.etaHours != null && (
                  <View className={styles.resItem}>
                    近3单均值 {etaData.recentAvg}h · 全期均值 {etaData.fullAvg}h ·
                    近期权重 {etaData.recentWeight}
                  </View>
                )}
                <View className={styles.resItem}>{etaData.note}</View>
                <View className={styles.formulaBox}>{etaData.formula}</View>
              </View>
            )}

            <View className={styles.cardTitle}>What-if 三维推演</View>
            <View className={styles.qaInputRow}>
              <Text className={styles.inputLbl}>发货延迟(天)</Text>
              <Input
                className={styles.qaInput} type="digit" value={wiDelay}
                onInput={e => setWiDelay(e.detail.value)}
                placeholder="0-30" maxlength={4}
              />
            </View>
            <View className={styles.qaInputRow}>
              <Text className={styles.inputLbl}>取消率变动</Text>
              <Input
                className={styles.qaInput} type="digit" value={wiCancel}
                onInput={e => setWiCancel(e.detail.value)}
                placeholder="如 0.1 = +10%" maxlength={6}
              />
            </View>
            <View className={styles.qaInputRow}>
              <Text className={styles.inputLbl}>客单价变动</Text>
              <Input
                className={styles.qaInput} type="digit" value={wiAov}
                onInput={e => setWiAov(e.detail.value)}
                placeholder="如 -0.1 = -10%" maxlength={6}
              />
            </View>
            <View className={styles.runBtn} onClick={runWhatif}>开始推演</View>
            {whatifData && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>GMV ¥{whatifData.scenario.gmv}</View>
                  <View className={styles.resScore}>
                    {whatifData.impacts.gmvDelta >= 0 ? '+' : ''}{whatifData.impacts.gmvDelta}
                  </View>
                </View>
                <View className={styles.resItem}>
                  履约' {whatifData.scenario.projectedFulfillmentHours}h(超时风险
                  {pctStr(whatifData.scenario.overtimeRiskRatio)}, 约
                  {whatifData.scenario.estOvertimeOrders} 单)
                </View>
                <View className={styles.resItem}>
                  有效单量 {whatifData.scenario.effectiveOrders} 单 ·
                  客单价 ¥{whatifData.scenario.avgOrderValue}
                </View>
                <View className={styles.resItem}>应对预案:</View>
                {(whatifData.mitigations || []).map((m: string, i: number) => (
                  <View key={i} className={styles.resItem}>• {m}</View>
                ))}
                <View className={styles.formulaBox}>{whatifData.formula}</View>
                <View className={styles.adviceRow}>
                  <Text className={styles.adviceTag}>建议书</Text>
                  <Text className={styles.adviceText}>{whatifData.disposition}</Text>
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>日单量滚动预测</View>
            <View className={styles.runBtn} onClick={runForecast}>预测未来 12 日</View>
            {forecastData && (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>
                  基线 = {forecastData.basis.weightScheme}(近3日均值
                  {forecastData.basis.recentAvg} · 全期 {forecastData.basis.fullAvg} ·
                  趋势斜率 {forecastData.basis.trendSlope})
                </View>
                {(forecastData.rows || []).slice(0, 6).map((r: any) => (
                  <View key={r.step} className={styles.candRow}>
                    <Text>{r.date || `+${r.step}日`}</Text>
                    <Text className={styles.candScores}>预计 {r.forecastVolume} 单</Text>
                  </View>
                ))}
                <View className={styles.formulaBox}>{forecastData.formula}</View>
              </View>
            )}
          </View>
        )}

        {/* ============ 风控 P2 ============ */}
        {tab === 'risk' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>退款裁决评分(四因子)</View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                value={orderId}
                onInput={e => setOrderId(e.detail.value)}
                placeholder="输入订单号 orderId"
                maxlength={40}
              />
              <View className={styles.qaBtn} onClick={runRefundScore}>裁决</View>
            </View>
            {refundData && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={`${styles.alertTag} ${sevColor(refundData.level)}`}>
                    {refundData.levelName}
                  </View>
                  <View className={styles.resScore}>{refundData.score}/100</View>
                </View>
                <View className={styles.resItem}>
                  {refundData.suggestionName} · 订单 {refundData.orderId} ·
                  会员 {refundData.memberId} · ¥{refundData.amount}
                </View>
                {(refundData.factors || []).map((f: any) => (
                  <View key={f.code} className={styles.candRow}>
                    <Text>{f.factor}</Text>
                    <Text className={styles.candScores}>{f.score}/100 · {f.explain}</Text>
                  </View>
                ))}
                <View className={styles.formulaBox}>{refundData.formula}</View>
                <View className={styles.adviceRow}>
                  <Text className={styles.adviceTag}>建议书</Text>
                  <Text className={styles.adviceText}>{refundData.disposition}</Text>
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>异常订单扫描(三类规则)</View>
            <View className={styles.runBtn} onClick={runScan}>全量扫描(高频/囤货/秒退款)</View>
            {scanData && (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>
                  扫描 {scanData.totalOrders} 单 · 检出 {scanData.anomalyCount} 条
                </View>
                {(scanData.rules || []).map((r: string, i: number) => (
                  <View key={i} className={styles.resItem}>规则: {r}</View>
                ))}
                <View className={styles.adviceRow}>
                  <Text className={styles.adviceTag}>建议书</Text>
                  <Text className={styles.adviceText}>{scanData.disposition}</Text>
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>历史异常留痕</View>
            <View className={styles.runBtn} onClick={loadAnomalies}>刷新异常列表</View>
            {anomalyList.slice(0, 10).map(a => (
              <View key={a.anomalyId} className={styles.alertCard}>
                <View className={styles.resHead}>
                  <View className={`${styles.alertTag} ${sevColor(a.level)}`}>
                    {a.typeName || anomalyTypeName(a.type)}
                  </View>
                  <View className={styles.resBatch}>{a.orderId}</View>
                </View>
                <View className={styles.resItem}>{a.detail}</View>
                <View className={styles.resItem}>{a.suggestion}</View>
              </View>
            ))}
            {anomalyLoaded && anomalyList.length === 0 && (
              <View className={styles.empty}>暂无异常记录(扫描后留痕)</View>
            )}
          </View>
        )}

        {/* ============ 进化 P3 ============ */}
        {tab === 'evo' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>进化参数视图(安全阀)</View>
            <View className={styles.runBtn} onClick={loadParams}>查看参数</View>
            {paramsData && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    etaRecentWeight {paramsData.etaRecentWeight}
                  </View>
                  <View className={styles.resBatch}>
                    clamp [{(paramsData.clamp || []).join(', ')}]
                  </View>
                </View>
                <View className={styles.resItem}>
                  默认 {paramsData.default} · 乘子: 采纳×{paramsData.multipliers?.adopted} /
                  修正×{paramsData.multipliers?.corrected} /
                  拒绝×{paramsData.multipliers?.rejected}
                </View>
                <View className={styles.formulaBox}>{paramsData.note}</View>
              </View>
            )}

            <View className={styles.cardTitle}>反馈闭环(三态裁决留痕)</View>
            <View className={styles.evoPanel}>
              <View className={styles.evoRow}>
                <Text className={styles.evoLbl}>目标</Text>
              </View>
              <View className={styles.chipRow}>
                {FEEDBACK_TARGETS.map(t => (
                  <View
                    key={t}
                    className={`${styles.chip} ${fbTarget === t ? styles.chipActive : ''}`}
                    onClick={() => setFbTarget(t)}
                  >
                    {feedbackTargetName(t)}
                  </View>
                ))}
              </View>
              <View className={styles.evoRow}>
                <Text className={styles.evoLbl}>裁决</Text>
              </View>
              <View className={styles.chipRow}>
                {VERDICTS.map(v => (
                  <View
                    key={v}
                    className={`${styles.chip} ${fbVerdict === v ? styles.chipActive : ''}`}
                    onClick={() => setFbVerdict(v)}
                  >
                    {verdictName(v)}
                  </View>
                ))}
              </View>
              <View className={styles.qaInputRow}>
                <Input
                  className={styles.qaInput}
                  value={fbNote}
                  onInput={e => setFbNote(e.detail.value)}
                  placeholder="备注(可选, ≤200字)"
                  maxlength={200}
                />
              </View>
              <View className={styles.runBtn} onClick={submitFeedback}>提交反馈(留痕)</View>
              {fbResult && (
                <View className={styles.resultCard}>
                  <View className={styles.resItem}>
                    #{fbResult.feedbackId} {feedbackTargetName(fbResult.targetType)} ·
                    {fbResult.verdictName || verdictName(fbResult.verdict)}
                  </View>
                  {fbResult.etaRecentWeightAfter != null && (
                    <View className={styles.resItem}>
                      etaRecentWeight → {fbResult.etaRecentWeightAfter}(安全阀内)
                    </View>
                  )}
                </View>
              )}
            </View>

            <View className={styles.cardTitle}>三检测器(单量/PAID/取消)</View>
            <View className={styles.runBtn} onClick={runDetect}>运行检测</View>
            {detectData && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>{detectData.alertCount} 条告警</View>
                  <View className={styles.resBatch}>观测 {detectData.days} 日</View>
                </View>
                {(detectData.alerts || []).map((a: any, i: number) => (
                  <View key={i} className={styles.alertCard}>
                    <View className={styles.resItem}>
                      [{a.type}] {a.metric} · {a.date}
                    </View>
                    <View className={styles.resItem}>{a.detail}</View>
                  </View>
                ))}
                {detectData.alertCount === 0 && (
                  <View className={styles.empty}>无告警(三检测器均未触发)</View>
                )}
                <View className={styles.formulaBox}>{detectData.formula}</View>
                <View className={styles.adviceRow}>
                  <Text className={styles.adviceTag}>建议书</Text>
                  <Text className={styles.adviceText}>{detectData.disposition}</Text>
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>决策备忘录生成</View>
            <View className={styles.chipRow}>
              {MEMO_TOPICS.map(t => (
                <View
                  key={t}
                  className={`${styles.chip} ${memoTopic === t ? styles.chipActive : ''}`}
                  onClick={() => setMemoTopic(t)}
                >
                  {memoTopicName(t)}
                </View>
              ))}
            </View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                value={memoNotes}
                onInput={e => setMemoNotes(e.detail.value)}
                placeholder="决策备注(可选, ≤200字)"
                maxlength={200}
              />
              <View className={styles.qaBtn} onClick={runMemo}>生成</View>
            </View>
            {memoData && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>{memoData.topicName}</View>
                  <View className={styles.resBatch}>#{memoData.memoId}</View>
                </View>
                <View className={styles.resItem}>{memoData.recommendation}</View>
                {(memoData.assumptions || []).map((a: string, i: number) => (
                  <View key={i} className={styles.resItem}>{a}</View>
                ))}
                <View className={styles.adviceRow}>
                  <Text className={styles.adviceTag}>建议书</Text>
                  <Text className={styles.adviceText}>{memoData.disposition}</Text>
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>备忘录历史留痕</View>
            <View className={styles.runBtn} onClick={loadMemos}>刷新备忘录</View>
            {memoList.slice(0, 10).map(m => (
              <View key={m.memoId} className={styles.fbItem}>
                <View className={styles.fbRow}>
                  <Text>#{m.memoId} {m.topicName || memoTopicName(m.topic)}</Text>
                  <Text className={styles.candScores}>
                    {(m.createdAt || '').slice(0, 10)}
                  </Text>
                </View>
                <View className={styles.resItem}>{m.recommendation}</View>
              </View>
            ))}
            {memoLoaded && memoList.length === 0 && (
              <View className={styles.empty}>暂无备忘录(生成后留痕)</View>
            )}
            <View className={styles.footNote}>
              智单: 确定性规则引擎 · 建议书模式 · 裁决权永在人工
            </View>
          </View>
        )}

        <View className={styles.bottomSpace} />
      </ScrollView>
    </View>
  );
};

export default ZhiDanPage;
