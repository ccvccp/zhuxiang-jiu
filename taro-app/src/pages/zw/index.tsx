/**
 * 智运·AI智能物流大模型 · 前端管理工作台
 * 八页签: 总览 → 路由(P0) → 轨迹(P1) → 风控(P2) → 分析(P3) → 进化(P3)
 *        → 调配(P4 语义+熔断) → 协同(P5-P7 绑定/提醒/进化2.0)
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

type Tab = 'overview' | 'route' | 'track' | 'risk' | 'analysis' | 'evo'
  | 'dispatch' | 'nexus';

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: '总览' },
  { key: 'route', label: '路由' },
  { key: 'track', label: '轨迹' },
  { key: 'risk', label: '风控' },
  { key: 'analysis', label: '分析' },
  { key: 'evo', label: '进化' },
  { key: 'dispatch', label: '调配' },
  { key: 'nexus', label: '协同' },
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
  // 调配(P4)
  const [normResult, setNormResult] = useState<any>(null);
  const [carriersList, setCarriersList] = useState<any[]>([]);
  const [fRoute, setFRoute] = useState<any>(null);
  const [circuit, setCircuit] = useState<any>(null);
  // 协同(P5-P7)
  const [capPlan, setCapPlan] = useState<any>(null);
  const [bindForm, setBindForm] = useState({
    waybillNo: '', orderId: '', batchCode: 'BATCH-2026-09', antiFakeCode: '',
  });
  const [verifyCodeVal, setVerifyCodeVal] = useState('');
  const [verifyResult, setVerifyResult] = useState<any>(null);
  const [reverseResult, setReverseResult] = useState<any>(null);
  const [alertList, setAlertList] = useState<any[]>([]);
  const [carbonData, setCarbonData] = useState<any>(null);
  const [suggestions, setSuggestions] = useState<any[]>([]);

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
    if (key === 'dispatch' && !circuit) {
      setCircuit(await ZwAPI.circuitStatus().catch(() => null));
      setCarriersList(await ZwAPI.carriers().catch(() => []));
    }
    if (key === 'nexus' && alertList.length === 0) {
      setAlertList(await ZwAPI.alerts().catch(() => []));
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

  // ============ 智酿运通 P4: 调配 ============

  /** 语义归一化演示(SF 方言回单) */
  const runNormalize = async () => {
    try {
      setNormResult(await ZwAPI.normalize('SF', {
        bill_no: 'SF-DEMO-009', parcel_weight: 5.2,
        state: '已签收', recv_name: '王先生',
        recv_mobile: '13800001234', unknown_extra: 'x',
      }));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 四维画像 + 特征路由(高货值礼盒预设) */
  const runProfileRoute = async () => {
    try {
      const p = await ZwAPI.orderProfile({
        orderType: 'retail', weight: 5, pieceCount: 2,
        insuredValue: 12800,
        receiver: { province: '广东', city: '深圳' },
      });
      setFRoute({ profile: p, route: await ZwAPI.featureRoute(p) });
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 生成运力异常报告 */
  const runCircuitReport = async () => {
    try {
      const r = await ZwAPI.circuitReport();
      setCircuit(await ZwAPI.circuitStatus().catch(() => null));
      Taro.showToast({
        title: `已生成(open=${r.openCount})`, icon: 'success',
      });
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  // ============ 智酿运通 P5-P7: 协同 ============

  /** 舱位预约建议书 */
  const runCapacity = async () => {
    try {
      setCapPlan(await ZwAPI.capacityPlan());
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 三码合一绑定 */
  const runTriCode = async () => {
    const { waybillNo, orderId, batchCode, antiFakeCode } = bindForm;
    if (!waybillNo.trim() || !orderId.trim() || !antiFakeCode.trim()) {
      Taro.showToast({ title: '运单号/订单号/防伪码必填', icon: 'none' });
      return;
    }
    try {
      await ZwAPI.triCodeBind({
        waybillNo: waybillNo.trim(), orderId: orderId.trim(),
        batchCode: batchCode.trim() || 'BATCH-DEFAULT',
        antiFakeCode: antiFakeCode.trim(),
      });
      Taro.showToast({ title: '三码绑定成功', icon: 'success' });
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 扫码验真(公开端点) */
  const runVerify = async () => {
    if (!verifyCodeVal.trim()) {
      Taro.showToast({ title: '请输入任一码(防伪/批次/运单)', icon: 'none' });
      return;
    }
    try {
      setVerifyResult(await ZwAPI.verifyCode(verifyCodeVal.trim()));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 逆向物流路径匹配 */
  const runReverse = async (condition: string) => {
    if (!bindForm.orderId.trim()) {
      Taro.showToast({ title: '请先填写订单号', icon: 'none' });
      return;
    }
    try {
      setReverseResult(await ZwAPI.reverseBind(bindForm.orderId.trim(), condition));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 五角色扫描 */
  const runAlertScan = async () => {
    try {
      const r = await ZwAPI.alertScan();
      setAlertList(await ZwAPI.alerts().catch(() => []));
      Taro.showToast({ title: `出稿 ${r.generated} 条`, icon: 'success' });
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 提醒确认/驳回 */
  const runAlertAck = async (alertId: number, disposition: string) => {
    try {
      await ZwAPI.alertAck(alertId, disposition);
      setAlertList(await ZwAPI.alerts().catch(() => []));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 碳足迹 */
  const runCarbon = async () => {
    try {
      setCarbonData(await ZwAPI.carbon());
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 策略建议 + 裁决 */
  const runSuggest = async () => {
    try {
      const r = await ZwAPI.suggest();
      setSuggestions(r.suggestions || []);
      Taro.showToast({ title: `生成 ${r.generated} 条`, icon: 'success' });
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  const runSuggestionDecide = async (suggestionId: number, verdict: string) => {
    try {
      await ZwAPI.decideSuggestion(suggestionId, verdict);
      setSuggestions(await ZwAPI.suggestions().catch(() => []));
      Taro.showToast({ title: verdict === 'adopted' ? '已采纳' : '已拒绝(负样本)', icon: 'success' });
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 偏好登记演示 */
  const runPreference = async () => {
    try {
      await ZwAPI.savePreference({
        memberId: 3, scope: 'b2b',
        prefs: { weekdayOnly: true, contactPerson: '李采购' },
      });
      Taro.showToast({ title: 'B端偏好已登记', icon: 'success' });
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

        {/* ============ 七、调配 P4(智酿运通) ============ */}
        {tab === 'dispatch' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>统一语义层(渠道方言翻译)</View>
            <View className={styles.runBtn} onClick={runNormalize}>
              归一化顺丰方言回单(bill_no/state)
            </View>
            {normResult && (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>
                  归一化 {normResult.mappedFields} 字段 ·
                  未映射 {normResult.unmappedFields?.length} 项
                </View>
                <View className={styles.resItem}>
                  运单 {normResult.normalized?.waybillNo} ·
                  重量 {normResult.normalized?.weight}kg ·
                  状态 {normResult.normalized?.status}
                </View>
              </View>
            )}
            {carriersList.length > 0 && (
              <View className={styles.resultCard}>
                <View className={styles.cardTitle}>渠道注册表</View>
                {carriersList.map(c => (
                  <View key={c.carrier} className={styles.candRow}>
                    <Text>{c.carrierName}</Text>
                    <Text className={styles.candScores}>
                      {c.builtin ? '内置' : '配置接入'} ·
                      {(c.conditions || []).join('/') || '—'}
                    </Text>
                  </View>
                ))}
              </View>
            )}

            <View className={styles.cardTitle}>四维画像 + 特征路由</View>
            <View className={styles.runBtn} onClick={runProfileRoute}>
              高货值礼盒画像(¥12,800 零售)
            </View>
            {fRoute && (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>
                  品类 {fRoute.profile?.categoryName} ·
                  包装 {fRoute.profile?.packaging} ·
                  风险 {fRoute.profile?.riskLevel}
                </View>
                <View className={styles.resItem}>
                  优先 {fRoute.route?.priority?.carrierName}
                  ({fRoute.route?.priority?.service}) ·
                  备选 {fRoute.route?.backup?.carrierName}
                </View>
                <View className={styles.resItem}>
                  进化信号: {(fRoute.route?.feedbackSignals || []).join('/')}
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>渠道熔断(三指标)</View>
            {circuit && circuit.carriers?.map((c: any) => (
              <View key={c.carrier} className={styles.alertCard}>
                <View className={styles.resHead}>
                  <View className={`${styles.alertTag} ${
                    c.state === 'open' ? styles.sevHigh
                    : c.state === 'half_open' ? styles.sevMid : styles.sevLow
                  }`}>
                    {c.carrierName} {c.stateName}
                  </View>
                </View>
                <View className={styles.resItem}>
                  揽收率 {((c.pickupRate ?? 0) * 100).toFixed(0)}% ·
                  中转 {c.avgTransitHours ?? 0}h ·
                  异常率 {((c.anomalyRate ?? 0) * 100).toFixed(0)}%
                  (样本 {c.sample})
                </View>
                {(c.reasons || []).length > 0 && (
                  <View className={styles.resItem}>
                    {(c.reasons || []).join('; ')}
                  </View>
                )}
              </View>
            ))}
            <View className={styles.runBtn} onClick={runCircuitReport}>
              生成《运力异常报告》(open 渠道切换建议)
            </View>
            <View className={styles.footNote}>
              熔断为观测态 · 流量切换建议书永不自动执行
            </View>
          </View>
        )}

        {/* ============ 八、协同 P5-P7(智酿运通) ============ */}
        {tab === 'nexus' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>运力舱位预约建议书</View>
            <View className={styles.runBtn} onClick={runCapacity}>
              未来 3 天分渠道预约(安全系数 1.2)
            </View>
            {capPlan && (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>
                  日均预测 {capPlan.dailyForecastOrders} 单 ·
                  窗口 {capPlan.windowDays} 天
                </View>
                {(capPlan.allocations || []).map((a: any) => (
                  <View key={a.carrier} className={styles.candRow}>
                    <Text>{a.carrier}</Text>
                    <Text className={styles.candScores}>
                      日均 {a.dailyForecast} → 建议预约 {a.suggestBooking}
                    </Text>
                  </View>
                ))}
                <View className={styles.resWarn}>
                  建议书: 人工确认后向渠道预约, 永不自动执行
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>三码合一 + 扫码验真</View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                value={bindForm.waybillNo}
                onInput={e => setBindForm({ ...bindForm, waybillNo: e.detail.value })}
                placeholder="运单号" maxlength={40}
              />
              <Input
                className={styles.qaInput}
                value={bindForm.orderId}
                onInput={e => setBindForm({ ...bindForm, orderId: e.detail.value })}
                placeholder="订单号" maxlength={40}
              />
            </View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                value={bindForm.antiFakeCode}
                onInput={e => setBindForm({ ...bindForm, antiFakeCode: e.detail.value })}
                placeholder="防伪码" maxlength={40}
              />
              <View className={styles.qaBtn} onClick={runTriCode}>绑定</View>
            </View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                value={verifyCodeVal}
                onInput={e => setVerifyCodeVal(e.detail.value)}
                placeholder="验真: 输入任一码(公开端点)"
                maxlength={60}
              />
              <View className={styles.qaBtn} onClick={runVerify}>验真</View>
            </View>
            {verifyResult && (
              <View className={styles.resultCard}>
                <View className={styles.resOk}>
                  验真通过 · 批次 {verifyResult.product?.batchCode}
                </View>
                <View className={styles.resItem}>
                  {verifyResult.journey?.iotNote}
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>逆向物流(状态→路径)</View>
            <View className={styles.evoBtns}>
              <View className={styles.evoBtn} onClick={() => runReverse('unopened')}>未开封</View>
              <View className={styles.evoBtn} onClick={() => runReverse('opened')}>已开封</View>
              <View className={styles.evoBtn} onClick={() => runReverse('damaged')}>破损</View>
            </View>
            {reverseResult && (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>
                  {reverseResult.conditionName} → {reverseResult.matchedPathName}
                </View>
                <View className={styles.resItem}>
                  {reverseResult.protocol?.body?.slice(0, 50)}…
                </View>
                <View className={styles.resWarn}>
                  处置协议: 人工确认后执行, 永不自动
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>五角色智能提醒</View>
            <View className={styles.runBtn} onClick={runAlertScan}>
              扫描触发面(延误/异常/爆仓/成本/KPI)
            </View>
            {alertList.slice(0, 6).map(a => (
              <View key={a.alertId} className={styles.alertCard}>
                <View className={styles.resHead}>
                  <View className={`${styles.alertTag} ${sevColor(
                    a.status === 'pending' ? 'medium' : 'low'
                  )}`}>
                    {a.roleName}
                  </View>
                  <View className={styles.resBatch}>{a.status}</View>
                </View>
                <View className={styles.resItem}>{a.title}</View>
                <View className={styles.resItem}>
                  {String(a.body || '').slice(0, 40)}…
                </View>
                {a.status === 'pending' && (
                  <View className={styles.evoBtns}>
                    <View className={styles.evoBtn} onClick={() => runAlertAck(a.alertId, 'acked')}>确认</View>
                    <View className={styles.evoBtn} onClick={() => runAlertAck(a.alertId, 'dismissed')}>驳回</View>
                  </View>
                )}
              </View>
            ))}

            <View className={styles.cardTitle}>碳足迹(排放因子法)</View>
            <View className={styles.runBtn} onClick={runCarbon}>
              核算总排(渠道占比+绿色切换)
            </View>
            {carbonData && (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>
                  总排 {carbonData.totalCarbonKg}kg ·
                  单均 {carbonData.avgCarbonKg}kg
                </View>
                {(carbonData.byCarrier || []).map((c: any) => (
                  <View key={c.carrier} className={styles.candRow}>
                    <Text>{c.carrierName}</Text>
                    <Text className={styles.candScores}>
                      {c.totalKg}kg · 占比 {c.sharePct}%
                    </Text>
                  </View>
                ))}
                <View className={styles.resItem}>
                  {carbonData.greenSuggestion?.body?.slice(0, 60)}…
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>人机协同(策略裁决)</View>
            <View className={styles.evoBtns}>
              <View className={styles.evoBtn} onClick={runSuggest}>生成建议</View>
              <View className={styles.evoBtn} onClick={runPreference}>登记B端偏好</View>
            </View>
            {suggestions.map(s => (
              <View key={s.suggestionId} className={styles.alertCard}>
                <View className={styles.resHead}>
                  <View className={`${styles.alertTag} ${styles.sevMid}`}>
                    {s.typeName}
                  </View>
                  <View className={styles.resBatch}>{s.status}</View>
                </View>
                <View className={styles.resItem}>{s.title}</View>
                <View className={styles.resItem}>
                  {String(s.body || '').slice(0, 45)}…
                </View>
                {s.status === 'pending' && (
                  <View className={styles.evoBtns}>
                    <View className={styles.evoBtn} onClick={() => runSuggestionDecide(s.suggestionId, 'adopted')}>采纳</View>
                    <View className={styles.evoBtn} onClick={() => runSuggestionDecide(s.suggestionId, 'rejected')}>拒绝</View>
                  </View>
                )}
              </View>
            ))}
            <View className={styles.footNote}>
              拒绝记负样本回流 · 决策权永在人工
            </View>
          </View>
        )}

        <View className={styles.bottomSpace} />
      </ScrollView>
    </View>
  );
};

export default ZhiYunPage;
