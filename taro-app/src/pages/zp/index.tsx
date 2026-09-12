/**
 * 智付·AI智能支付大模型 · 前端管理工作台
 * 四页签: 总览(P0) → 路由(P1) → 风控(P2/P3/P4) → 进化(P7/P8/P9)
 * 口径: 确定性规则引擎 · 资金域永不自动(调额建议书/确认仅建议包)
 *       观测面不受 PAY69_MODE 影响(默认 off 零影响)
 * 所有业务数字 100% 来自 /api/pay69/* 查询层(数字不出现在模板层)
 */
import React, { useState } from 'react';
import { View, Text, ScrollView, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  ZpAPI, channelName, healthStateName, stepName, riskTierName,
  bioResultName, creditGradeName, hypStatusName, evoLevelName,
} from '@/api/zp';

type Tab = 'overview' | 'route' | 'risk' | 'evo';

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: '总览' },
  { key: 'route', label: '路由' },
  { key: 'risk', label: '风控' },
  { key: 'evo', label: '进化' },
];

/** 0-1 比率 → 百分比(纯格式化) */
const pctStr = (v: any): string =>
  `${(Number(v || 0) * 100).toFixed(0)}%`;

const errMsg = (e: any): string =>
  String(e?.message || e).slice(0, 40);

const ZhiFuPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('overview');

  // ============ 总览 P0 ============
  const [status, setStatus] = useState<any>(null);
  const [channels, setChannels] = useState<any>(null);
  const [health, setHealth] = useState<any>(null);

  const loadOverview = async () => {
    setStatus(await ZpAPI.modelStatus().catch(() => null));
    setChannels(await ZpAPI.channelDict().catch(() => null));
    setHealth(await ZpAPI.healthView().catch(() => null));
  };

  // ============ 路由 P1 ============
  const [routeDict, setRouteDict] = useState<any>(null);
  const [routeResult, setRouteResult] = useState<any>(null);
  const [windowStats, setWindowStats] = useState<any>(null);
  const [rtAmount, setRtAmount] = useState('500');
  const [rtTag, setRtTag] = useState('default');

  const loadRouteDict = async () => {
    setRouteDict(await ZpAPI.routeDict().catch(() => null));
    setWindowStats(await ZpAPI.routeWindow().catch(() => null));
  };

  const runCompute = async () => {
    const amount = Number(rtAmount);
    if (!amount || amount <= 0) {
      Taro.showToast({ title: '请输入金额', icon: 'none' });
      return;
    }
    setRouteResult(await ZpAPI.routeCompute(1, amount, [rtTag], true)
      .catch(e => {
        Taro.showToast({ title: errMsg(e), icon: 'none' });
        return null;
      }));
  };

  // ============ 风控 P2/P3/P4 ============
  const [entropyResult, setEntropyResult] = useState<any>(null);
  const [enAmount, setEnAmount] = useState('2000');
  const [enTier, setEnTier] = useState('B');
  const [enChannel, setEnChannel] = useState('wechat');
  const [bioResult, setBioResult] = useState<any>(null);
  const [creditResult, setCreditResult] = useState<any>(null);
  const [crAmount, setCrAmount] = useState('5000');
  const [crTier, setCrTier] = useState('A');

  const runEntropy = async () => {
    const amount = Number(enAmount);
    if (!amount || amount <= 0) {
      Taro.showToast({ title: '请输入金额', icon: 'none' });
      return;
    }
    setEntropyResult(await ZpAPI.entropyCompute(1, amount, enTier, enChannel)
      .catch(e => {
        Taro.showToast({ title: errMsg(e), icon: 'none' });
        return null;
      }));
  };

  const runBiometric = async () => {
    const ch = await ZpAPI.bioChallenge(9980, 'face').catch(e => {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
      return null;
    });
    if (!ch) return;
    // 双线索胁迫演示(0.45+0.40=0.85 ≥ 0.60 → degraded)
    setBioResult(await ZpAPI.bioVerify(
      9980, ch.challenge, true,
      ['facial_stiffness', 'voice_tremor'],
    ).catch(e => {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
      return null;
    }));
  };

  const runCredit = async () => {
    const amount = Number(crAmount);
    if (!amount || amount <= 0) {
      Taro.showToast({ title: '请输入金额', icon: 'none' });
      return;
    }
    setCreditResult(await ZpAPI.creditEvaluate(1, amount, crTier, 0.9)
      .catch(e => {
        Taro.showToast({ title: errMsg(e), icon: 'none' });
        return null;
      }));
  };

  // ============ 进化 P7/P8/P9 ============
  const [governance, setGovernance] = useState<any>(null);
  const [drift, setDrift] = useState<any>(null);
  const [immunity, setImmunity] = useState<any>(null);
  const [redteam, setRedteam] = useState<any>(null);
  const [cbResult, setCbResult] = useState<any>(null);
  const [cbAmount, setCbAmount] = useState('5000');

  const loadEvolution = async () => {
    setGovernance(await ZpAPI.governance().catch(() => null));
    setImmunity(await ZpAPI.immunityView().catch(() => null));
  };

  const runDrift = async () => {
    setDrift(await ZpAPI.driftDetect().catch(() => null));
  };

  const runMonitor = async () => {
    const m = await ZpAPI.immunityMonitor().catch(e => {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
      return null;
    });
    if (m) {
      setDrift(m);
      setImmunity(await ZpAPI.immunityView().catch(() => null));
    }
  };

  const runRedteam = async () => {
    setRedteam(await ZpAPI.redteamRun().catch(e => {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
      return null;
    }));
  };

  const runCrossborder = async () => {
    const amount = Number(cbAmount);
    if (!amount || amount <= 0) {
      Taro.showToast({ title: '请输入金额', icon: 'none' });
      return;
    }
    setCbResult(await ZpAPI.crossborderPreview('USD', 'US', amount)
      .catch(e => {
        Taro.showToast({ title: errMsg(e), icon: 'none' });
        return null;
      }));
  };

  return (
    <View className={styles.page}>
      <NavBar title="智付·AI智能支付大模型" />

      <ScrollView scrollY className={styles.body}>
        <View className={styles.section}>
          <View className={styles.heroCard}>
            <View className={styles.heroTitle}>智付·AI智能支付大模型</View>
            <View className={styles.heroSub}>四层认知支付栈 · 双环自进化 · 宪法约束</View>
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

        {/* ============ 总览 P0 ============ */}
        {tab === 'overview' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>认知中枢(七通道×健康度)</View>
            <View className={styles.runBtn} onClick={loadOverview}>刷新总览</View>
            {status ? (
              <View className={styles.resultCard}>
                <View className={styles.statGrid}>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>{status.channelCount}</View>
                    <View className={styles.statLbl}>通道注册</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>{status.mode}</View>
                    <View className={styles.statLbl}>模块模式</View>
                  </View>
                </View>
              </View>
            ) : (
              <View className={styles.empty}>点击上方按钮加载(观测面 off 可用)</View>
            )}

            {health && (
              <View className={styles.resultCard}>
                <View className={styles.cardTitle}>健康度观测(快环统计)</View>
                {(health.channels || []).map((c: any) => (
                  <View key={c.channelId} className={styles.candRow}>
                    <Text>{channelName(c.channelId)}</Text>
                    <Text className={styles.candScores}>
                      {healthStateName(c.state)}{c.frozen ? ' · 冻结' : ''}
                    </Text>
                  </View>
                ))}
                <View className={styles.footNote}>
                  frozen 人工专属铁律——成功率永不自动冻结通道
                </View>
              </View>
            )}

            {channels && (
              <View className={styles.resultCard}>
                <View className={styles.cardTitle}>通道字典(封闭注册表)</View>
                {(channels.channels || []).map((c: any) => (
                  <View key={c.channelId} className={styles.candRow}>
                    <Text>{channelName(c.channelId)}</Text>
                    <Text className={styles.candScores}>
                      费率 {pctStr(c.feeRate)} · 限额 ¥{c.singleLimit}
                    </Text>
                  </View>
                ))}
                <View className={styles.formulaBox}>
                  健康度阈值: healthy ≥ {channels.healthThresholds?.healthy},
                  degraded ≥ {channels.healthThresholds?.degraded}
                </View>
              </View>
            )}
          </View>
        )}

        {/* ============ 路由 P1 ============ */}
        {tab === 'route' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>智能路由评分(四因子确定性)</View>
            <View className={styles.runBtn} onClick={loadRouteDict}>加载路由字典+窗口</View>

            <View className={styles.chipRow}>
              {['default', 'fast_needed', 'large_amount', 'privacy_needed',
                'social_sharing', 'promo_hunting', 'credit_preference',
                'biometric_habit'].map(t => (
                <View
                  key={t}
                  className={`${styles.chip} ${rtTag === t ? styles.chipActive : ''}`}
                  onClick={() => setRtTag(t)}
                >
                  {t === 'default' ? '默认' : t === 'fast_needed' ? '求快' : t === 'large_amount' ? '大额'
                    : t === 'privacy_needed' ? '求隐私' : t === 'social_sharing' ? '社交'
                      : t === 'promo_hunting' ? '捡漏' : t === 'credit_preference' ? '信用' : '生物'}
                </View>
              ))}
            </View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                type="digit"
                value={rtAmount}
                onInput={e => setRtAmount(e.detail.value)}
                placeholder="金额(元)"
                maxlength={10}
              />
              <View className={styles.qaBtn} onClick={runCompute}>评分</View>
            </View>

            {routeResult && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    {routeResult.ranking?.[0]
                      ? channelName(routeResult.ranking[0].channelId) : '无通道'}
                  </View>
                  <View className={styles.resBatch}>
                    候选 {routeResult.candidateCount} 通道
                  </View>
                </View>
                {(routeResult.ranking || []).slice(0, 5).map((r: any) => (
                  <View key={r.channelId} className={styles.candRow}>
                    <Text>{channelName(r.channelId)}</Text>
                    <Text className={styles.candScores}>
                      {r.routeScore} (费{r.factors.fee} 健{r.factors.health}
                      亲{r.factors.affinity} 习{r.factors.habit})
                    </Text>
                  </View>
                ))}
                <View className={styles.formulaBox}>
                  LLM 禁入判定链——四因子全数值留痕可复现
                </View>
              </View>
            )}

            {routeDict && (
              <View className={styles.resultCard}>
                <View className={styles.cardTitle}>路由权重(封闭注册)</View>
                {Object.entries(routeDict.weights || {}).map(([k, v]) => (
                  <View key={k} className={styles.candRow}>
                    <Text>{k === 'fee' ? '费率' : k === 'health' ? '健康度'
                      : k === 'affinity' ? '意图亲和' : '会员习惯'}</Text>
                    <Text className={styles.candScores}>{v}</Text>
                  </View>
                ))}
                <View className={styles.formulaBox}>
                  硬过滤: {String(routeDict.hardFilters || [])}
                </View>
              </View>
            )}

            {windowStats && (
              <View className={styles.resultCard}>
                <View className={styles.cardTitle}>滚动窗口(快环基线, N={windowStats.windowSize})</View>
                {(windowStats.channels || []).map((c: any) => (
                  <View key={c.channelId} className={styles.candRow}>
                    <Text>{channelName(c.channelId)}</Text>
                    <Text className={styles.candScores}>
                      {c.attemptCount} 次 · {c.successRate ?? '未观测'}
                    </Text>
                  </View>
                ))}
              </View>
            )}
          </View>
        )}

        {/* ============ 风控 P2/P3/P4 ============ */}
        {tab === 'risk' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>风险熵引擎(六轴确定性)</View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                type="digit"
                value={enAmount}
                onInput={e => setEnAmount(e.detail.value)}
                placeholder="金额(元)"
                maxlength={10}
              />
              <View className={styles.qaBtn} onClick={runEntropy}>评估</View>
            </View>
            <View className={styles.chipRow}>
              {['S', 'A', 'B', 'C', 'D'].map(t => (
                <View
                  key={t}
                  className={`${styles.chip} ${enTier === t ? styles.chipActive : ''}`}
                  onClick={() => setEnTier(t)}
                >
                  信值{t}
                </View>
              ))}
              {['wechat', 'qr', 'credit_tv', 'biometric'].map(c => (
                <View
                  key={c}
                  className={`${styles.chip} ${enChannel === c ? styles.chipActive : ''}`}
                  onClick={() => setEnChannel(c)}
                >
                  {channelName(c).slice(0, 2)}
                </View>
              ))}
            </View>

            {entropyResult && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    {stepName(entropyResult.step)}
                  </View>
                  <View className={styles.resScore}>
                    熵 {entropyResult.entropy ?? 'fail-soft'}
                  </View>
                </View>
                {entropyResult.axes && (
                  <View className={styles.formulaBox}>
                    金额{entropyResult.axes.amount} 信值{entropyResult.axes.trust}
                    行为{entropyResult.axes.behavior} 环境{entropyResult.axes.environment}
                    通道{entropyResult.axes.channel} 历史{entropyResult.axes.history}
                    → 对齐 60号 {riskTierName(entropyResult.riskTier)} 档
                  </View>
                )}
                {entropyResult.failSoft && (
                  <View className={styles.resWarn}>
                    fail-soft 引擎故障→light 档+留痕(不阻断业务)
                  </View>
                )}
              </View>
            )}

            <View className={styles.cardTitle}>交易级授信(P3)</View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                type="digit"
                value={crAmount}
                onInput={e => setCrAmount(e.detail.value)}
                placeholder="金额(元)"
                maxlength={10}
              />
              <View className={styles.qaBtn} onClick={runCredit}>评估</View>
            </View>
            <View className={styles.chipRow}>
              {['S', 'A', 'B', 'C'].map(t => (
                <View
                  key={t}
                  className={`${styles.chip} ${crTier === t ? styles.chipActive : ''}`}
                  onClick={() => setCrTier(t)}
                >
                  信值{t}
                </View>
              ))}
            </View>

            {creditResult && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    {creditGradeName(creditResult.grade)}
                  </View>
                  <View className={styles.resBatch}>
                    {creditResult.creditApproved ? '可授信' : '不建议'}
                  </View>
                </View>
                <View className={styles.resItem}>
                  综合分 {creditResult.score} · 基础额度 ¥{creditResult.baseLimit}
                </View>
                {(creditResult.installmentPlans || []).map((p: any) => (
                  <View key={p.periods} className={styles.candRow}>
                    <Text>{p.periods} 期</Text>
                    <Text className={styles.candScores}>
                      利率 {pctStr(p.annualRate)} · 每期 ¥{p.perInstallment}
                    </Text>
                  </View>
                ))}
                <View className={styles.footNote}>
                  利率规则表查表——LLM 禁定价; 调额走建议书 admin 终审
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>活体意图验证(P4 胁迫演示)</View>
            <View className={styles.runBtn} onClick={runBiometric}>
              发起挑战+双线索胁迫验证
            </View>
            {bioResult && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View
                    className={`${styles.gradeBadge} ${bioResult.result === 'degraded' ? styles.sevMid : ''}`}
                  >
                    {bioResultName(bioResult.result)}
                  </View>
                  <View className={styles.resBatch}>
                    胁迫分 {bioResult.coerceScore}
                  </View>
                </View>
                <View className={styles.resItem}>
                  {bioResult.result === 'degraded'
                    ? '静默降级密码验证+人工介入留痕(保护用户方向)'
                    : '特征匹配通过'}
                </View>
                <View className={styles.formulaBox}>
                  面部僵硬 0.45 + 语音颤抖 0.40 = 0.85 ≥ 0.60 阈值 → 降级
                </View>
              </View>
            )}
          </View>
        )}

        {/* ============ 进化 P7/P8/P9 ============ */}
        {tab === 'evo' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>自进化治理(P7 双环学习)</View>
            <View className={styles.runBtn} onClick={loadEvolution}>刷新治理+免疫</View>

            {governance && (
              <View className={styles.resultCard}>
                <View className={styles.statGrid}>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      {evoLevelName(governance.level || 'L0')}
                    </View>
                    <View className={styles.statLbl}>治理分级</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      {governance.killActive ? 'KILL' : '正常'}
                    </View>
                    <View className={styles.statLbl}>紧急制动</View>
                  </View>
                </View>
                <View className={styles.formulaBox}>
                  进化永不自动生效——假设→46号审批→显式发布三级
                </View>
              </View>
            )}

            {immunity && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View
                    className={`${styles.gradeBadge} ${immunity.status === 'frozen' ? styles.sevHigh : ''}`}
                  >
                    {immunity.status === 'frozen' ? '进化冻结' : '免疫正常'}
                  </View>
                  <View className={styles.resBatch}>
                    红队 {immunity.redteamRuns} 批
                  </View>
                </View>
                {immunity.status === 'frozen' && (
                  <View className={styles.resWarn}>
                    {immunity.frozenReason}(解冻人工专属)
                  </View>
                )}
              </View>
            )}

            <View className={styles.cardTitle}>漂移检测(快环) + 分布监控(P8)</View>
            <View className={styles.evoBtns}>
              <View className={styles.evoBtn} onClick={runDrift}>漂移检测</View>
              <View className={styles.evoBtn} onClick={runMonitor}>分布监控</View>
              <View className={styles.evoBtn} onClick={runRedteam}>红队四向量</View>
            </View>

            {drift && !drift.rules && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    {drift.signalCount} 信号
                  </View>
                  <View className={styles.resBatch}>{drift.level}</View>
                </View>
                {(drift.signals || []).map((s: any, i: number) => (
                  <View key={i} className={styles.alertCard}>
                    <View className={styles.fbRow}>
                      <Text>{s.subject}</Text>
                      <Text className={styles.candScores}>{s.severity}</Text>
                    </View>
                    <View className={styles.resItem}>{s.detail}</View>
                  </View>
                ))}
                {(!drift.signals || !drift.signals.length) && (
                  <View className={styles.empty}>三域无漂移信号(确定性统计)</View>
                )}
              </View>
            )}

            {drift && drift.rules && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    {drift.criticalChannels} critical
                  </View>
                  <View className={styles.resBatch}>{drift.signalCount} 信号</View>
                </View>
                <View className={styles.resItem}>
                  {drift.shouldFreeze
                    ? `触发自动冻结: ${drift.action}(安全方向+告警留痕)`
                    : '未达冻结阈值(critical<2 且信号<3)'}
                </View>
              </View>
            )}

            {redteam && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View
                    className={`${styles.gradeBadge} ${redteam.allDefended ? '' : styles.sevHigh}`}
                  >
                    {redteam.allDefended ? '全防御' : '发现漏洞'}
                  </View>
                  <View className={styles.resBatch}>{redteam.summary}</View>
                </View>
                {(redteam.vectors || []).map((v: any) => (
                  <View key={v.vector} className={styles.candRow}>
                    <Text>{v.vector} {v.name}</Text>
                    <Text className={styles.candScores}>
                      {v.defended ? '已防御 ✓' : '未防御 ✗'}
                    </Text>
                  </View>
                ))}
                {!redteam.allDefended && (
                  <View className={styles.resWarn}>
                    未防御向量→自动冻结进化(安全方向)
                  </View>
                )}
              </View>
            )}

            <View className={styles.cardTitle}>跨境沙盘预演(P9 全合成)</View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                type="digit"
                value={cbAmount}
                onInput={e => setCbAmount(e.detail.value)}
                placeholder="金额(人民币本位, 元)"
                maxlength={10}
              />
              <View className={styles.qaBtn} onClick={runCrossborder}>USD 预演</View>
            </View>

            {cbResult && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>USD/{cbResult.jurisdiction}</View>
                  <View className={styles.resBatch}>
                    ¥{cbResult.amountCny} → ${cbResult.convertedAmount}
                  </View>
                </View>
                <View className={styles.resItem}>
                  汇率 {cbResult.sandboxRate}(沙盘合成) ·
                  对冲 {cbResult.hedge?.action}
                </View>
                <View className={styles.resItem}>
                  单证: {String(cbResult.requiredDocs)}
                </View>
                <View className={styles.formulaBox}>
                  {cbResult.disclaimer}
                </View>
              </View>
            )}

            <View className={styles.bottomSpace} />
          </View>
        )}
      </ScrollView>
    </View>
  );
};

export default ZhiFuPage;
