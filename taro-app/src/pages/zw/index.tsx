/**
 * 智运·AI智能物流大模型 · 前端管理工作台
 * 六页签: 总览 → 路由(P0) → 轨迹(P1) → 风控(P2) → 分析(P3) → 进化(P3)
 * 口径: 智能调度中枢 · 全链确定性 · 建议/切换永不自动执行
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  ZwAPI, ZwStatusVO, RouteDecisionVO, CarrierScoreVO,
  EtaVO, AnomalyVO, RiskAssessVO,
  zwRiskLevelName, zwAnomalyTypeName, zwClaimTypeName,
  zwInspectResultName,
} from '@/api/zw';

type Tab = 'overview' | 'route' | 'track' | 'risk' | 'analysis' | 'evo';

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: '总览' },
  { key: 'route', label: '路由' },
  { key: 'track', label: '轨迹' },
  { key: 'risk', label: '风控' },
  { key: 'analysis', label: '分析' },
  { key: 'evo', label: '进化' },
];

// 路由决策预设
const ROUTE_PRESET = {
  orderType: 'retail', weight: 5.0, pieceCount: 2, insuredValue: 500,
  sender: { city: '济南', address: '济南市历城区仓库' },
  receiver: { province: '山东', city: '济南', address: '济南市历下区' },
};

// 风控评分预设(高风险: 偏远+高货值+urgent)
const RISK_PRESET = {
  orderType: 'retail', weight: 10.0, pieceCount: 2,
  insuredValue: 8000, receiverProvince: '新疆', urgent: true,
};

const ZhiYunPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('overview');
  const [loading, setLoading] = useState(true);

  // 总览
  const [status, setStatus] = useState<ZwStatusVO | null>(null);
  // 路由
  const [decision, setDecision] = useState<RouteDecisionVO | null>(null);
  const [scores, setScores] = useState<Record<string, CarrierScoreVO> | null>(null);
  // 轨迹
  const [eta, setEta] = useState<EtaVO | null>(null);
  const [waybill, setWaybill] = useState('');
  const [anomalies, setAnomalies] = useState<AnomalyVO[]>([]);
  // 风控
  const [risk, setRisk] = useState<RiskAssessVO | null>(null);
  const [claimResult, setClaimResult] = useState<any>(null);
  // 分析
  const [cost, setCost] = useState<any>(null);
  const [forecast, setForecast] = useState<any>(null);
  // 进化
  const [feedbacks, setFeedbacks] = useState<any[]>([]);

  const loadOverview = useCallback(async () => {
    try {
      const st = await ZwAPI.status().catch(() => null);
      setStatus(st);
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

  const onTab = async (key: Tab) => {
    setTab(key);
    if (key === 'track' && anomalies.length === 0) {
      setAnomalies(await ZwAPI.anomalies().catch(() => [] as AnomalyVO[]));
    }
    if (key === 'evo' && feedbacks.length === 0) {
      setFeedbacks(await ZwAPI.feedbacks(10).catch(() => []));
    }
  };

  /** 路由决策 */
  const runDecide = async () => {
    try {
      const d = await ZwAPI.routeDecide(ROUTE_PRESET);
      setDecision(d);
      setScores(await ZwAPI.carrierScores());
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** ETA 查询 */
  const queryEta = async () => {
    if (!waybill.trim()) {
      Taro.showToast({ title: '请输入运单号', icon: 'none' });
      return;
    }
    try {
      setEta(await ZwAPI.eta(waybill.trim()));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 风控评分 */
  const runRisk = async () => {
    try {
      setRisk(await ZwAPI.riskAssess(RISK_PRESET));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 理赔示例 */
  const runClaim = async () => {
    try {
      setClaimResult(await ZwAPI.createClaim({
        waybillNo: 'SF-DEMO-001', orderId: 'O-DEMO-001',
        carrier: 'SF', claimType: 'damage', claimAmount: 268,
        description: '瓶身破损(工作台演示)',
      }));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 分析 */
  const runAnalysis = async () => {
    try {
      const [c, f] = await Promise.all([
        ZwAPI.costAnalysis(), ZwAPI.volumeForecast(3),
      ]);
      setCost(c); setForecast(f);
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 反馈 */
  const sendFeedback = async (targetType: string, verdict: string) => {
    try {
      await ZwAPI.feedback({ targetType, verdict, note: '工作台标记' });
      Taro.showToast({ title: '已标记', icon: 'success' });
      setFeedbacks(await ZwAPI.feedbacks(10).catch(() => []));
      setStatus(await ZwAPI.status().catch(() => null));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  const sevColor = (s: string): string =>
    s === 'high' || s === 'extreme' ? styles.sevHigh
      : s === 'medium' ? styles.sevMid : styles.sevLow;

  const healthColor = (h: string): string =>
    h === 'healthy' ? styles.sevLow : h === 'degraded' ? styles.sevHigh
      : styles.sevMid;

  return (
    <View className={styles.page}>
      <NavBar title="智运·AI物流" />

      <ScrollView scrollY className={styles.body}>
        {/* 页签栏 */}
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
              <View className={styles.heroTitle}>智运 · 智能调度中枢</View>
              <View className={styles.heroSub}>
                四引擎: 路由×轨迹×风控×分析 · 建议永不自动执行
              </View>
              {status && (
                <View className={styles.heroStats}>
                  <View className={styles.heroStat}>
                    <View className={styles.heroNum}>{status.orders}</View>
                    <View className={styles.heroLbl}>物流单</View>
                  </View>
                  <View className={styles.heroStat}>
                    <View className={styles.heroNum}>{(status.signRate * 100).toFixed(0)}%</View>
                    <View className={styles.heroLbl}>签收率</View>
                  </View>
                  <View className={styles.heroStat}>
                    <View className={styles.heroNum}>{status.avgSignHours}</View>
                    <View className={styles.heroLbl}>均时效h</View>
                  </View>
                  <View className={styles.heroStat}>
                    <View className={styles.heroNum}>{status.evolution.etaWeight}</View>
                    <View className={styles.heroLbl}>时效权重</View>
                  </View>
                </View>
              )}
            </View>
            <View className={styles.footNote}>
              叠加既有物流 18 端点零改动 · 智运四引擎为产业深化层
            </View>
          </View>
        )}

        {/* ============ 二、智能路由 P0 ============ */}
        {tab === 'route' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>多维路由决策</View>
            <View className={styles.runBtn} onClick={runDecide}>
              决策预设订单(零售 2 件)
            </View>
            {decision && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    {decision.decision.carrierName}
                  </View>
                  <View className={styles.resScore}>
                    {decision.decision.combinedScore} 分
                  </View>
                </View>
                <View className={styles.resItem}>{decision.decision.ruleReason}</View>
                <View className={styles.resItem}>公式: {decision.formula}</View>
                {decision.candidates.map(c => (
                  <View key={c.carrier} className={styles.candRow}>
                    <Text>{c.carrierName}</Text>
                    <Text className={styles.candScores}>
                      规则{c.ruleScore} 质量{c.qualityScore} → {c.combinedScore}
                    </Text>
                  </View>
                ))}
              </View>
            )}
            {scores && (
              <View className={styles.resultCard}>
                <View className={styles.cardTitle}>物流商质量评分</View>
                {Object.values(scores).map((s: CarrierScoreVO) => (
                  <View key={s.carrier} className={styles.candRow}>
                    <Text>{s.carrierName}</Text>
                    <Text className={styles.candScores}>
                      {s.score} 分{s.coldStart ? '(冷启动)' : ''}
                    </Text>
                  </View>
                ))}
              </View>
            )}
          </View>
        )}

        {/* ============ 三、轨迹智能 P1 ============ */}
        {tab === 'track' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>ETA 预测</View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                value={waybill}
                onInput={e => setWaybill(e.detail.value)}
                placeholder="输入运单号(如 SF1)"
                maxlength={40}
              />
              <View className={styles.qaBtn} onClick={queryEta}>预测</View>
            </View>
            {eta && (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>运单: {eta.waybillNo}({eta.carrier})</View>
                {eta.status === 'signed' ? (
                  <View className={styles.resOk}>已签收</View>
                ) : (
                  <>
                    <View className={styles.resItem}>
                      剩余约 {eta.remainingHours.toFixed(1)}h · ETA {eta.eta?.slice(0, 16)}
                    </View>
                    <View className={styles.resItem}>依据: {eta.basis}</View>
                  </>
                )}
              </View>
            )}

            <View className={styles.cardTitle}>异常四检测器</View>
            {anomalies.length === 0 && !loading && (
              <View className={styles.empty}>在途运单无异常信号</View>
            )}
            {anomalies.map((a, i) => (
              <View key={i} className={styles.alertCard}>
                <View className={styles.resHead}>
                  <View className={`${styles.alertTag} ${sevColor(a.severity)}`}>
                    {zwAnomalyTypeName(a.type)}
                  </View>
                  <View className={styles.resBatch}>{a.waybillNo}</View>
                </View>
                <View className={styles.resItem}>{a.detail}</View>
                <View className={styles.resItem}>{a.action}</View>
              </View>
            ))}
          </View>
        )}

        {/* ============ 四、风控回执 P2 ============ */}
        {tab === 'risk' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>四防风控评分</View>
            <View className={styles.runBtn} onClick={runRisk}>
              评估高风险单(偏远+高货值+时效)
            </View>
            {risk && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={`${styles.alertTag} ${sevColor(risk.riskLevel)}`}>
                    {zwRiskLevelName(risk.riskLevel)}
                  </View>
                  <View className={styles.resScore}>{risk.riskScore} 分</View>
                </View>
                <View className={styles.resItem}>公式: {risk.formula}</View>
                {risk.suggestions.map((s, i) => (
                  <View key={i} className={styles.resItem}>· {s}</View>
                ))}
              </View>
            )}

            <View className={styles.cardTitle}>理赔工单(演示)</View>
            <View className={styles.runBtn} onClick={runClaim}>
              创建破损理赔(¥268)
            </View>
            {claimResult && (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>
                  {claimResult.claimNo} · {claimResult.claimTypeName} ¥{claimResult.claimAmount}
                </View>
                <View className={styles.resItem}>
                  标准: {claimResult.standard}({claimResult.slaDays} 工作日)
                </View>
                <View className={styles.resWarn}>
                  理赔建议书: 赔付审批须人工, 永不自动
                </View>
              </View>
            )}
          </View>
        )}

        {/* ============ 五、分析 P3 ============ */}
        {tab === 'analysis' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>成本分析 + 运量预测</View>
            <View className={styles.runBtn} onClick={runAnalysis}>
              生成分析(物流商对比+3 期预测)
            </View>
            {cost && (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>总运费: ¥{cost.totalFee}</View>
                {cost.byCarrier.map((c: any) => (
                  <View key={c.carrier} className={styles.candRow}>
                    <Text>{c.carrier}</Text>
                    <Text className={styles.candScores}>
                      {c.count} 单 · 均 ¥{c.avgFee}
                    </Text>
                  </View>
                ))}
                {cost.suggestions.map((s: string, i: number) => (
                  <View key={i} className={styles.resItem}>· {s}</View>
                ))}
              </View>
            )}
            {forecast && (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>
                  历史口径: {forecast.weightScheme}(近 3 期 {forecast.recentAvg} / 全期 {forecast.fullAvg})
                </View>
                {forecast.rows.map((r: any) => (
                  <View key={r.step} className={styles.candRow}>
                    <Text>+{r.step} 期</Text>
                    <Text className={styles.candScores}>
                      预测 {r.predictedOrders} 单
                    </Text>
                  </View>
                ))}
              </View>
            )}
          </View>
        )}

        {/* ============ 六、进化 P3 ============ */}
        {tab === 'evo' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>反馈进化(标记引擎有效性)</View>
            <View className={styles.evoPanel}>
              <View className={styles.evoRow}>
                <View className={styles.evoLbl}>路由决策</View>
                <View className={styles.evoBtns}>
                  <View className={styles.evoBtn} onClick={() => sendFeedback('route_decision', 'adopted')}>👍 有效</View>
                  <View className={styles.evoBtn} onClick={() => sendFeedback('route_decision', 'rejected')}>👎 误报</View>
                </View>
              </View>
              <View className={styles.evoRow}>
                <View className={styles.evoLbl}>ETA 预测</View>
                <View className={styles.evoBtns}>
                  <View className={styles.evoBtn} onClick={() => sendFeedback('eta', 'adopted')}>👍 准</View>
                  <View className={styles.evoBtn} onClick={() => sendFeedback('eta', 'corrected')}>✏ 修正</View>
                </View>
              </View>
              <View className={styles.evoHint}>
                有效→时效权重 +0.1 / 误报→-0.1(安全阀 [0.4, 0.8])
              </View>
            </View>

            {feedbacks.length > 0 && (
              <>
                <View className={styles.cardTitle}>进化留痕</View>
                {feedbacks.map(f => (
                  <View key={f.feedbackId} className={styles.fbItem}>
                    <View className={styles.fbRow}>
                      <Text>{f.targetType}</Text>
                      <Text className={
                        f.verdict === 'adopted' ? styles.fbAdopted
                          : f.verdict === 'rejected' ? styles.fbRejected : styles.fbCorrected
                      }>{f.verdict}</Text>
                    </View>
                    <View className={styles.fbWeight}>时效权重 → {f.etaWeightAfter}</View>
                  </View>
                ))}
              </>
            )}
          </View>
        )}

        <View className={styles.bottomSpace} />
      </ScrollView>
    </View>
  );
};

export default ZhiYunPage;
