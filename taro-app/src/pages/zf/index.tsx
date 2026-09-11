/**
 * 智法·AI智能法务大模型 · 前端管理工作台
 * 六页签: 总览(孪生) → 生产(P0) → 金融(P1) → 数据(P2) → 电商(P3) → 进化(P3)
 * 口径: 产-销-法一体化 · 全链确定性 · 建议永不自动执行
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  ZfAPI, ProcessCheckVO, CreditVO, TwinVO, ZfStatusVO,
  zfVerdictName, zfFeedbackVerdictName, zfFeedbackTargetName,
} from '@/api/zf';

type Tab = 'overview' | 'production' | 'finance' | 'asset' | 'commerce' | 'evo';

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: '总览' },
  { key: 'production', label: '生产' },
  { key: 'finance', label: '金融' },
  { key: 'asset', label: '数据' },
  { key: 'commerce', label: '电商' },
  { key: 'evo', label: '进化' },
];

// 工艺校验预设(合规样例)
const PROCESS_PRESET = {
  batchId: 'B20260901',
  params: {
    fermentation_days: 35, fermentation_temp: 28,
    storage_months: 6, additive_count: 0, blend_ratio: 0.3,
  },
  operator: '酿酒师张三',
};

// 信用评估预设
const CREDIT_PRESET = {
  entityId: 'SUP001', entityName: '济南粮液供应链',
  monthlyOrders: 50000, inventoryValue: 60000,
  productionCapacity: 50000, repaymentRate: 0.95,
};

const ZhiFaPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('overview');
  const [loading, setLoading] = useState(true);

  // 总览
  const [status, setStatus] = useState<ZfStatusVO | null>(null);
  const [twin, setTwin] = useState<TwinVO | null>(null);
  // 生产
  const [check, setCheck] = useState<ProcessCheckVO | null>(null);
  // 金融
  const [credit, setCredit] = useState<CreditVO | null>(null);
  const [contract, setContract] = useState<any>(null);
  // 数据
  const [catalog, setCatalog] = useState<any>(null);
  const [crossBorder, setCrossBorder] = useState<any>(null);
  // 电商
  const [priceAudit, setPriceAudit] = useState<any>(null);
  const [blackmail, setBlackmail] = useState<any>(null);
  // 进化
  const [precedents, setPrecedents] = useState<any[]>([]);
  const [feedbacks, setFeedbacks] = useState<any[]>([]);

  const loadOverview = useCallback(async () => {
    try {
      const [st, tw] = await Promise.all([
        ZfAPI.status().catch(() => null),
        ZfAPI.twin().catch(() => null),
      ]);
      setStatus(st);
      setTwin(tw);
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

  /** 页签惰性加载 */
  const onTab = async (key: Tab) => {
    setTab(key);
    if (key === 'evo' && precedents.length === 0) {
      const [pc, fb] = await Promise.all([
        ZfAPI.precedents().catch(() => []),
        ZfAPI.feedbacks(10).catch(() => []),
      ]);
      setPrecedents(pc);
      setFeedbacks(fb);
    }
  };

  /** 工艺校验(预设参数) */
  const runProcessCheck = async () => {
    try {
      const r = await ZfAPI.processCheck(PROCESS_PRESET);
      setCheck(r);
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 信用评估+合约(预设) */
  const runCredit = async () => {
    try {
      const c = await ZfAPI.creditAssess(CREDIT_PRESET);
      setCredit(c);
      const ct = await ZfAPI.contractGenerate({
        entityId: c.entityId, loanAmount: 200000,
      });
      setContract(ct);
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 数据分类扫描(预设字段) */
  const runClassify = async () => {
    try {
      setCatalog(await ZfAPI.classify({
        dataSamples: ['member_phone', '收货地址', '发酵温度', '勾调配方',
          '供应商价格', '库存数'],
        source: '工作台扫描',
      }));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 跨境评估(欧盟样例) */
  const runCrossBorder = async () => {
    try {
      setCrossBorder(await ZfAPI.crossBorderAssess({
        region: 'EU', dataLevels: ['L1', 'L2'], businessPurpose: '海外市场分析',
      }));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 价格审计(先涨后降样例) */
  const runPriceAudit = async () => {
    try {
      const history = [100, 100, 100, 130, 130, 130, 130, 130]
        .map((p, d) => ({ day: d, dealPrice: p }));
      setPriceAudit(await ZfAPI.priceAudit({
        productId: 'P001', priceHistory: history,
        current: { original: 130, strikethrough: 100, coupon: 128, member: 130 },
      }));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 打假防御(高风险样例) */
  const runBlackmail = async () => {
    try {
      setBlackmail(await ZfAPI.antiBlackmail({
        memberId: 9001, orderId: 'ORD-9001', complaints90d: 7,
        returnRatio: 0.8, lawsuitCount: 1,
      }));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 反馈 */
  const sendFeedback = async (targetType: string, verdict: string) => {
    try {
      await ZfAPI.feedback({ targetType, verdict, note: '工作台标记' });
      Taro.showToast({ title: `已${zfFeedbackVerdictName(verdict)}`, icon: 'success' });
      const [fb, st, tw] = await Promise.all([
        ZfAPI.feedbacks(10).catch(() => []),
        ZfAPI.status().catch(() => null),
        ZfAPI.twin().catch(() => null),
      ]);
      setFeedbacks(fb); setStatus(st); setTwin(tw);
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  const verdictColor = (v: string): string =>
    v === 'violation' ? styles.sevHigh
      : v === 'deviation' ? styles.sevMid : styles.sevLow;

  return (
    <View className={styles.page}>
      <NavBar title="智法·AI法务" />

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

        {/* ============ 一、总览(数字孪生) ============ */}
        {tab === 'overview' && (
          <View className={styles.section}>
            <View className={styles.heroCard}>
              <View className={styles.heroTitle}>智法 · 合规数字孪生</View>
              <View className={styles.heroSub}>
                产-销-法一体化 · 三重校验 · 建议永不自动执行
              </View>
              {status && (
                <View className={styles.heroStats}>
                  <View className={styles.heroStat}>
                    <View className={styles.heroNum}>{status.production.checks}</View>
                    <View className={styles.heroLbl}>工艺校验</View>
                  </View>
                  <View className={styles.heroStat}>
                    <View className={styles.heroNum}>{status.production.violations}</View>
                    <View className={styles.heroLbl}>违规数</View>
                  </View>
                  <View className={styles.heroStat}>
                    <View className={styles.heroNum}>{status.precedents}</View>
                    <View className={styles.heroLbl}>判例库</View>
                  </View>
                  <View className={styles.heroStat}>
                    <View className={styles.heroNum}>{status.evolution.strictness}</View>
                    <View className={styles.heroLbl}>严格度</View>
                  </View>
                </View>
              )}
            </View>

            {/* 三重校验 */}
            {twin && (
              <View className={styles.twinCard}>
                <View className={styles.cardTitle}>三重校验(孪生健康 {twin.twinHealth})</View>
                {(['physicalDigital', 'digitalLegal', 'physicalLegal'] as const).map(k => {
                  const item = twin.tripleVerification[k];
                  if (!item) return null;
                  return (
                    <View key={k} className={styles.dimRow}>
                      <View className={styles.dimName}>{item.explain?.split(':')[0]}</View>
                      <View className={styles.dimBar}>
                        <View className={styles.dimBarFill} style={{ width: `${Math.min(100, item.score * 100)}%` }} />
                      </View>
                      <View className={styles.dimScoreVal}>{(item.score * 100).toFixed(0)}%</View>
                    </View>
                  );
                })}
              </View>
            )}
          </View>
        )}

        {/* ============ 二、生产合规 P0 ============ */}
        {tab === 'production' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>工艺合规锚定(国标实时校验)</View>
            <View className={styles.runBtn} onClick={runProcessCheck}>
              校验预设批次({PROCESS_PRESET.batchId})
            </View>
            {check && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={`${styles.alertTag} ${verdictColor(check.verdict)}`}>
                    {zfVerdictName(check.verdict)}
                  </View>
                  <View className={styles.resBatch}>{check.batchId}</View>
                </View>
                {(check.violations.length + check.deviations.length) === 0 && (
                  <View className={styles.resOk}>全部工艺参数在国标限值内</View>
                )}
                {[...check.violations, ...check.deviations].map((v, i) => (
                  <View key={i} className={styles.resItem}>· {v.explain}</View>
                ))}
                {check.workOrder && (
                  <View className={styles.woCard}>
                    <View className={styles.woTitle}>{check.workOrder.title}</View>
                    <View className={styles.woSugg}>{check.workOrder.suggestion}</View>
                    <View className={styles.woDisp}>{check.workOrder.disposition}</View>
                  </View>
                )}
              </View>
            )}
            <View className={styles.footNote}>
              依据: GB/T 10781 / GB 2760(蒸馏酒禁添加) / 备案工艺
            </View>
          </View>
        )}

        {/* ============ 三、供应链金融 P1 ============ */}
        {tab === 'finance' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>供应链信用评估+动态合约</View>
            <View className={styles.runBtn} onClick={runCredit}>
              评估预设主体({CREDIT_PRESET.entityId})
            </View>
            {credit && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>{credit.grade}</View>
                  <View className={styles.resScore}>{credit.score} 分</View>
                </View>
                <View className={styles.resItem}>公式: {credit.formula}</View>
                {credit.fraudSuspected && (
                  <View className={styles.resWarn}>
                    ⚠ 数据矛盾: {credit.contradictions[0]}
                  </View>
                )}
                {contract && (
                  <View className={styles.contractCard}>
                    <View className={styles.woTitle}>
                      借款协议 ¥{contract.loanAmount.toLocaleString()}
                    </View>
                    <View className={styles.resItem}>
                      年利率 {(contract.terms.annualRate * 100).toFixed(1)}% · {contract.terms.guarantee}
                      {contract.terms.collateralRatio > 0
                        ? `(${(contract.terms.collateralRatio * 100).toFixed(0)}%)` : ''}
                    </View>
                    {contract.clauses.slice(0, 3).map((c: string, i: number) => (
                      <View key={i} className={styles.resItem}>{c}</View>
                    ))}
                  </View>
                )}
              </View>
            )}
          </View>
        )}

        {/* ============ 四、数据资产 P2 ============ */}
        {tab === 'asset' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>数据分类分级扫描</View>
            <View className={styles.runBtn} onClick={runClassify}>
              扫描预设字段(6 项)
            </View>
            {catalog && (
              <View className={styles.resultCard}>
                {catalog.catalog.map((c: any) => (
                  <View key={c.field} className={styles.lvRow}>
                    <View className={`${styles.lvTag} ${c.level === 'L4' || c.level === 'L3' ? styles.sevHigh : c.level === 'L2' ? styles.sevMid : styles.sevLow}`}>
                      {c.level}
                    </View>
                    <View className={styles.lvField}>{c.field}</View>
                    <View className={styles.lvCat}>{c.category}</View>
                  </View>
                ))}
                <View className={styles.footNote}>
                  L4 个人信息/L3 工艺秘方——禁止出库与许可(红线)
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>跨境传输评估(GDPR)</View>
            <View className={styles.runBtn} onClick={runCrossBorder}>
              评估样例(欧盟 · L1+L2)
            </View>
            {crossBorder && (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>目标法规: {crossBorder.targetLaw}</View>
                <View className={crossBorder.passable ? styles.resOk : styles.resWarn}>
                  {crossBorder.conclusion}
                </View>
              </View>
            )}
          </View>
        )}

        {/* ============ 五、电商深化 P3 ============ */}
        {tab === 'commerce' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>价格合规动态监测</View>
            <View className={styles.runBtn} onClick={runPriceAudit}>
              审计样例(先涨后降)
            </View>
            {priceAudit && (
              <View className={styles.resultCard}>
                {priceAudit.compliant && (
                  <View className={styles.resOk}>价格链路合规</View>
                )}
                {priceAudit.findings?.map((f: any, i: number) => (
                  <View key={i} className={styles.resItem}>
                    ⚠ {f.name}: {f.detail}
                  </View>
                ))}
              </View>
            )}

            <View className={styles.cardTitle}>职业打假防御</View>
            <View className={styles.runBtn} onClick={runBlackmail}>
              画像样例(高风险用户)
            </View>
            {blackmail && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={`${styles.alertTag} ${blackmail.highRisk ? styles.sevHigh : styles.sevLow}`}>
                    {blackmail.highRisk ? '高风险' : '常规'}
                  </View>
                  <View className={styles.resBatch}>{blackmail.orderId}</View>
                </View>
                {blackmail.riskMarkers?.map((m: string, i: number) => (
                  <View key={i} className={styles.resItem}>· {m}</View>
                ))}
                {blackmail.highRisk && (
                  <View className={styles.woCard}>
                    <View className={styles.woTitle}>{blackmail.defensePackage}</View>
                    {blackmail.evidenceChecklist?.map((e: string, i: number) => (
                      <View key={i} className={styles.resItem}>{i + 1}. {e}</View>
                    ))}
                  </View>
                )}
              </View>
            )}
          </View>
        )}

        {/* ============ 六、进化闭环 P3 ============ */}
        {tab === 'evo' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>反馈进化(标记规则有效性)</View>
            <View className={styles.evoPanel}>
              <View className={styles.evoRow}>
                <View className={styles.evoLbl}>工艺校验</View>
                <View className={styles.evoBtns}>
                  <View className={styles.evoBtn} onClick={() => sendFeedback('process_check', 'adopted')}>👍 有效</View>
                  <View className={styles.evoBtn} onClick={() => sendFeedback('process_check', 'rejected')}>👎 误报</View>
                </View>
              </View>
              <View className={styles.evoRow}>
                <View className={styles.evoLbl}>价格审计</View>
                <View className={styles.evoBtns}>
                  <View className={styles.evoBtn} onClick={() => sendFeedback('price_audit', 'adopted')}>👍 有效</View>
                  <View className={styles.evoBtn} onClick={() => sendFeedback('price_audit', 'corrected')}>✏ 修正</View>
                </View>
              </View>
              <View className={styles.evoHint}>
                有效→严格度 +0.1 / 误报→-0.1(安全阀 [0.6, 1.4])
              </View>
            </View>

            <View className={styles.cardTitle}>判例回流(同类酒企败诉案例)</View>
            {precedents.map(p => (
              <View key={p.caseId} className={styles.pcCard}>
                <View className={styles.pcHead}>
                  <View className={styles.pcId}>{p.caseId}</View>
                  <View className={styles.alertTag} style={{ background: '#f53f3f' }}>{p.outcome}</View>
                </View>
                <View className={styles.pcName}>{p.caseName}</View>
                <View className={styles.resItem}>败点: {p.lossPoint}</View>
                <View className={styles.pcSugg}>规则建议: {p.ruleSuggestion}</View>
              </View>
            ))}

            {feedbacks.length > 0 && (
              <>
                <View className={styles.cardTitle}>进化留痕</View>
                {feedbacks.map(f => (
                  <View key={f.feedbackId} className={styles.fbItem}>
                    <View className={styles.fbRow}>
                      <Text>{zfFeedbackTargetName(f.targetType)}</Text>
                      <Text className={
                        f.verdict === 'adopted' ? styles.fbAdopted
                          : f.verdict === 'rejected' ? styles.fbRejected : styles.fbCorrected
                      }>{zfFeedbackVerdictName(f.verdict)}</Text>
                    </View>
                    <View className={styles.fbWeight}>严格度 → {f.strictnessAfter}</View>
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

export default ZhiFaPage;
