/**
 * 智客·AI智能会员大模型 · 前端运营工作台(会员智能运营中枢)
 * 四页签: 洞察(P0) → 预警(P1) → 运营(P2) → 进化(P3)
 * 口径: 确定性规则引擎 · 建议书模式 · 永不自动执行 · 不涉信值域
 * 边界: 只做消费/等级/积分运营; 信值五维与资产评分归 68 号雷达域
 */
import React, { useState, useEffect } from 'react';
import { View, Text, ScrollView, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  ZkAPI, levelName, qaDomainName, verdictName, riskLevelName,
  feedbackTargetName, memoTopicName, wakeupTierName,
} from '@/api/zk';

type Tab = 'insight' | 'alert' | 'ops' | 'evo';

const TABS: { key: Tab; label: string }[] = [
  { key: 'insight', label: '洞察' },
  { key: 'alert', label: '预警' },
  { key: 'ops', label: '运营' },
  { key: 'evo', label: '进化' },
];

const FB_TARGETS = ['ltv', 'churn_scan', 'wakeup',
  'benefit_match', 'sandbox', 'portrait'];
const VERDICTS = ['adopted', 'corrected', 'rejected'];
const MEMO_TOPICS = ['member_day', 'level_threshold'];

const ZhiKePage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('insight');
  const [busy, setBusy] = useState('');
  const [qaText, setQaText] = useState('现在有多少会员');
  const [qaResult, setQaResult] = useState<any>(null);
  const [overview, setOverview] = useState<any>(null);
  const [ovError, setOvError] = useState(false);
  const [mid, setMid] = useState('');
  const [health, setHealth] = useState<any>(null);
  const [portrait, setPortrait] = useState<any>(null);
  const [churn, setChurn] = useState<any>(null);
  const [ltvRes, setLtvRes] = useState<any>(null);
  const [sbRes, setSbRes] = useState<any>(null);
  const [consumeDelta, setConsumeDelta] = useState('0');
  const [growthDelta, setGrowthDelta] = useState('0');
  const [benefit, setBenefit] = useState<any>(null);
  const [points, setPoints] = useState<any>(null);
  const [wakeup, setWakeup] = useState<any>(null);
  const [paramsView, setParamsView] = useState<any>(null);
  const [fbTarget, setFbTarget] = useState('ltv');
  const [fbVerdict, setFbVerdict] = useState('adopted');
  const [fbNote, setFbNote] = useState('');
  const [fbResult, setFbResult] = useState<any>(null);
  const [fbHistory, setFbHistory] = useState<any[] | null>(null);
  const [detectRes, setDetectRes] = useState<any>(null);
  const [memoTopic, setMemoTopic] = useState('member_day');
  const [memoNotes, setMemoNotes] = useState('');
  const [memoRes, setMemoRes] = useState<any>(null);
  const [memoHistory, setMemoHistory] = useState<any[] | null>(null);

  const memberId = parseInt(mid, 10) || 0;

  // ============ 总览加载(挂载一次; 失败诚实错误态+重试) ============
  const loadOverview = async () => {
    try {
      setOvError(false);
      setOverview(await ZkAPI.overview());
    } catch (_) {
      setOvError(true);
    }
  };

  useEffect(() => {
    if (!overview && !ovError) {
      loadOverview();
    }
  }, []);

  const toastErr = (e: any) => {
    Taro.showToast({
      title: String(e?.message || e).slice(0, 30), icon: 'none',
    });
  };
  const needMid = (): boolean => {
    if (!memberId) {
      Taro.showToast({ title: '请输入会员 ID', icon: 'none' });
      return false;
    }
    return true;
  };

  // ============ 洞察(P0) ============
  const runQa = async () => {
    if (!qaText.trim()) {
      Taro.showToast({ title: '请输入问题', icon: 'none' });
      return;
    }
    try {
      setBusy('qa');
      setQaResult(await ZkAPI.qa(qaText.trim()));
    } catch (e: any) { toastErr(e); } finally { setBusy(''); }
  };
  const runMember = async () => {
    if (!needMid()) return;
    try {
      setBusy('member');
      const [h, p] = await Promise.all([
        ZkAPI.health(memberId), ZkAPI.portrait(memberId)]);
      setHealth(h);
      setPortrait(p);
    } catch (e: any) { toastErr(e); } finally { setBusy(''); }
  };

  // ============ 预警(P1) ============
  const runChurn = async () => {
    try {
      setBusy('churn');
      setChurn(await ZkAPI.churnScan());
    } catch (e: any) { toastErr(e); } finally { setBusy(''); }
  };
  const runLtv = async () => {
    if (!needMid()) return;
    try {
      setBusy('ltv');
      setLtvRes(await ZkAPI.ltv(memberId));
    } catch (e: any) { toastErr(e); } finally { setBusy(''); }
  };
  const runSandbox = async () => {
    if (!needMid()) return;
    try {
      setBusy('sandbox');
      setSbRes(await ZkAPI.sandbox({
        memberId,
        consumeDelta: (parseFloat(consumeDelta) || 0) / 100,
        growthDelta: parseInt(growthDelta, 10) || 0,
      }));
    } catch (e: any) { toastErr(e); } finally { setBusy(''); }
  };

  // ============ 运营(P2) ============
  const runBenefit = async () => {
    if (!needMid()) return;
    try {
      setBusy('benefit');
      setBenefit(await ZkAPI.benefitMatch(memberId));
    } catch (e: any) { toastErr(e); } finally { setBusy(''); }
  };
  const runPoints = async () => {
    try {
      setBusy('points');
      setPoints(await ZkAPI.pointsAnalysis(memberId || undefined));
    } catch (e: any) { toastErr(e); } finally { setBusy(''); }
  };
  const runWakeup = async () => {
    try {
      setBusy('wakeup');
      setWakeup(await ZkAPI.wakeupSuggest());
    } catch (e: any) { toastErr(e); } finally { setBusy(''); }
  };

  // ============ 进化(P3) ============
  const runParams = async () => {
    try {
      setBusy('params');
      setParamsView(await ZkAPI.params());
    } catch (e: any) { toastErr(e); } finally { setBusy(''); }
  };
  const runFeedback = async () => {
    try {
      setBusy('feedback');
      setFbResult(await ZkAPI.feedback(fbTarget, fbVerdict, fbNote));
      setFbHistory(await ZkAPI.feedbacks(50).catch(() => []));
      setParamsView(await ZkAPI.params().catch(() => null));
    } catch (e: any) { toastErr(e); } finally { setBusy(''); }
  };
  const runDetect = async () => {
    try {
      setBusy('detect');
      setDetectRes(await ZkAPI.detect());
    } catch (e: any) { toastErr(e); } finally { setBusy(''); }
  };
  const runMemo = async () => {
    try {
      setBusy('memo');
      setMemoRes(await ZkAPI.memo(memoTopic, memoNotes));
      setMemoHistory(await ZkAPI.memos(50).catch(() => []));
    } catch (e: any) { toastErr(e); } finally { setBusy(''); }
  };

  const riskColor = (r: string): string =>
    r === 'red' ? styles.sevHigh : r === 'yellow' ? styles.sevMid
      : styles.sevLow;
  const tierColor = (t: string): string =>
    t === 'deep' ? styles.sevHigh : t === 'medium' ? styles.sevMid
      : styles.sevLow;
  const pct = (v: number): string =>
    `${Math.round((v || 0) * 100)}%`;

  return (
    <View className={styles.page}>
      <NavBar title="智客·AI会员" />

      <ScrollView scrollY className={styles.body}>
        <View className={styles.heroCard}>
          <View className={styles.heroTitle}>智客·AI智能会员大模型</View>
          <View className={styles.heroSub}>
            确定性引擎 · 建议书模式 · 不涉信值域
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

        {busy ? (
          <View className={styles.busyBar}>处理中({busy})…</View>
        ) : null}

        {/* ============ 洞察 P0 ============ */}
        {tab === 'insight' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>智能问答(五域路由)</View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                value={qaText}
                onInput={e => setQaText(e.detail.value)}
                placeholder="如: 现在有多少会员 / 等级分布 / 积分总量"
                maxlength={100}
              />
              <View className={styles.qaBtn} onClick={runQa}>问答</View>
            </View>
            {qaResult ? (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    {qaDomainName(qaResult.domain)}·{qaResult.intent}
                  </View>
                </View>
                <View className={styles.resItem}>{qaResult.answer}</View>
                <View className={styles.formulaBox}>{qaResult.reasoning}</View>
              </View>
            ) : null}

            <View className={styles.cardTitle}>会员总览</View>
            {overview ? (
              <View className={styles.resultCard}>
                <View className={styles.statGrid}>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      {overview.memberTotal}
                    </View>
                    <View className={styles.statLbl}>会员总量</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      ¥{overview.totalConsume}
                    </View>
                    <View className={styles.statLbl}>有效总消费</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      ¥{overview.avgConsume}
                    </View>
                    <View className={styles.statLbl}>平均客单</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      {overview.pointsTotal}
                    </View>
                    <View className={styles.statLbl}>积分总量</View>
                  </View>
                </View>
                <View className={styles.resItem}>
                  有效订单 {overview.validOrderTotal} 单 · 正常
                  {' '}{(overview.statusDistribution || {}).active} /
                  禁用 {(overview.statusDistribution || {}).disabled}
                </View>
                {Object.entries(overview.levelDistribution || {})
                  .map(([lv, n]) => (
                    <View key={lv} className={styles.candRow}>
                      <Text>L{lv} {levelName(Number(lv))}</Text>
                      <Text className={styles.candScores}>{n} 名</Text>
                    </View>
                  ))}
                <View className={styles.formulaBox}>
                  {overview.consumeScope}
                </View>
              </View>
            ) : ovError ? (
              <View className={styles.resultCard}>
                <View className={styles.resWarn}>
                  总览加载失败(后端 /api/member-ai 未启动或无权限)
                </View>
                <View className={styles.runBtn} onClick={loadOverview}>
                  重试
                </View>
              </View>
            ) : (
              <View className={styles.empty}>总览加载中…</View>
            )}

            <View className={styles.cardTitle}>
              单会员洞察(健康度五维 + RFM 画像)
            </View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                type="number"
                value={mid}
                onInput={e => setMid(e.detail.value)}
                placeholder="会员 ID"
                maxlength={9}
              />
              <View className={styles.qaBtn} onClick={runMember}>查询</View>
            </View>
            {health ? (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>{health.grade}</View>
                  <View className={styles.resScore}>
                    {health.totalScore}/100
                  </View>
                </View>
                <View className={styles.resItem}>
                  {health.nickname} · L{health.level}
                  {' '}{health.levelName || levelName(health.level)}
                  · 健康度总分
                </View>
                {(health.dimensions || []).map((d: any) => (
                  <View key={d.dimKey} className={styles.candRow}>
                    <Text>{d.dim}</Text>
                    <Text className={styles.candScores}>
                      {d.score}/100 · {d.explain}
                    </Text>
                  </View>
                ))}
                <View className={styles.formulaBox}>{health.formula}</View>
              </View>
            ) : null}
            {portrait ? (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    {portrait.segmentLabel}
                  </View>
                  <View className={styles.resBatch}>
                    RFM {portrait.rfm.recencyScore}/
                    {portrait.rfm.frequencyScore}/
                    {portrait.rfm.monetaryScore}
                  </View>
                </View>
                <View className={styles.candRow}>
                  <Text>最近消费 R</Text>
                  <Text className={styles.candScores}>
                    {portrait.rfm.recencyDays} 天 · {portrait.rfm.recencyScore} 档
                  </Text>
                </View>
                <View className={styles.candRow}>
                  <Text>近30天频次 F</Text>
                  <Text className={styles.candScores}>
                    {portrait.rfm.frequency30d} 单 · {portrait.rfm.frequencyScore} 档
                  </Text>
                </View>
                <View className={styles.candRow}>
                  <Text>月均消费 M</Text>
                  <Text className={styles.candScores}>
                    ¥{portrait.rfm.monthlyConsume} · {portrait.rfm.monetaryScore} 档
                  </Text>
                </View>
                <View className={styles.resItem}>
                  建议: {portrait.suggestedAction}
                </View>
                <View className={styles.formulaBox}>{portrait.formula}</View>
              </View>
            ) : null}
          </View>
        )}

        {/* ============ 预警 P1 ============ */}
        {tab === 'alert' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>三信号流失扫描</View>
            <View className={styles.runBtn} onClick={runChurn}>
              全量扫描(登录拉长/消费衰减/等级下滑)
            </View>
            {busy === 'churn' ? (
              <View className={styles.empty}>扫描中…</View>
            ) : null}
            {churn ? (
              <View className={styles.resultCard}>
                <View className={styles.statGrid}>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      {churn.scanned}
                    </View>
                    <View className={styles.statLbl}>扫描</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      {(churn.riskCounts || {}).red}
                    </View>
                    <View className={styles.statLbl}>红</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      {(churn.riskCounts || {}).yellow}
                    </View>
                    <View className={styles.statLbl}>黄</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      {(churn.riskCounts || {}).green}
                    </View>
                    <View className={styles.statLbl}>绿</View>
                  </View>
                </View>
                {(churn.items || []).slice(0, 20).map((c: any) => (
                  <View key={c.memberId} className={styles.alertCard}>
                    <View className={styles.resHead}>
                      <View
                        className={`${styles.alertTag} ${riskColor(c.riskLevel)}`}
                      >
                        {riskLevelName(c.riskLevel)}
                      </View>
                      <View className={styles.resScore}>
                        {pct(c.churnScore)}
                      </View>
                    </View>
                    <View className={styles.resItem}>
                      {c.nickname} · 会员{c.memberId} ·
                      L{c.level} {levelName(c.level)}
                    </View>
                    <View className={styles.resItem}>
                      三信号: 登录拉长 {c.signals.loginGap.value} /
                      消费衰减 {c.signals.consumeDecay.value} /
                      等级下滑 {c.signals.levelSlide.value}
                    </View>
                  </View>
                ))}
                {(churn.items || []).length === 0 ? (
                  <View className={styles.empty}>
                    扫描完成, 暂无会员数据(仅显示前 20 条, 按风险分降序)
                  </View>
                ) : (
                  <View className={styles.footNote}>
                    仅显示前 20 条(按风险分降序)
                  </View>
                )}
                <View className={styles.formulaBox}>{churn.formula}</View>
              </View>
            ) : null}

            <View className={styles.cardTitle}>LTV 预测(确定性公式)</View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                type="number"
                value={mid}
                onInput={e => setMid(e.detail.value)}
                placeholder="会员 ID"
                maxlength={9}
              />
              <View className={styles.qaBtn} onClick={runLtv}>预测</View>
            </View>
            {ltvRes ? (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    LTV ¥{ltvRes.ltv}
                  </View>
                  <View className={styles.resBatch}>
                    {ltvRes.nickname} · L{ltvRes.level}
                  </View>
                </View>
                <View className={styles.candRow}>
                  <Text>历史月均消费</Text>
                  <Text className={styles.candScores}>
                    ¥{ltvRes.basis.monthlyAvgConsume}
                    ({ltvRes.basis.historyMonths} 个月 ·
                    近90天 {ltvRes.basis.ordersIn90d} 单)
                  </Text>
                </View>
                <View className={styles.candRow}>
                  <Text>因子链</Text>
                  <Text className={styles.candScores}>
                    等级 {ltvRes.factors.levelWeight} ×
                    活跃 {ltvRes.factors.activeWeight} ×
                    留存 {ltvRes.factors.retainFactor}(可学习) ×
                    {ltvRes.factors.horizonMonths} 月
                  </Text>
                </View>
                {(ltvRes.assumptions || []).map((a: string, i: number) => (
                  <View key={i} className={styles.assumpItem}>· {a}</View>
                ))}
                <View className={styles.formulaBox}>{ltvRes.formula}</View>
              </View>
            ) : null}

            <View className={styles.cardTitle}>
              等级生命周期沙盘(What-if 推演)
            </View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                type="number"
                value={mid}
                onInput={e => setMid(e.detail.value)}
                placeholder="会员 ID"
                maxlength={9}
              />
            </View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                type="digit"
                value={consumeDelta}
                onInput={e => setConsumeDelta(e.detail.value)}
                placeholder="月消费变动%(10=+10%)"
                maxlength={8}
              />
              <Input
                className={styles.qaInput}
                type="number"
                value={growthDelta}
                onInput={e => setGrowthDelta(e.detail.value)}
                placeholder="成长值加成"
                maxlength={6}
              />
              <View className={styles.qaBtn} onClick={runSandbox}>推演</View>
            </View>
            {sbRes ? (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    {sbRes.projected.direction}
                    {sbRes.projected.newLevelName
                      ? `→ ${sbRes.projected.newLevelName}` : ''}
                  </View>
                  <View className={styles.resBatch}>{sbRes.nickname}</View>
                </View>
                <View className={styles.candRow}>
                  <Text>当前</Text>
                  <Text className={styles.candScores}>
                    L{sbRes.current.level} ·
                    成长值 {sbRes.current.growth} ·
                    月均 ¥{sbRes.current.monthlyAvgConsume}
                  </Text>
                </View>
                <View className={styles.candRow}>
                  <Text>推演后</Text>
                  <Text className={styles.candScores}>
                    L{sbRes.projected.newLevel} ·
                    成长值 {sbRes.projected.newGrowth} ·
                    未来月消费 ¥{sbRes.projected.futureMonthlyConsume}
                  </Text>
                </View>
                <View className={styles.candRow}>
                  <Text>保级判定</Text>
                  <Text className={styles.candScores}>
                    {sbRes.projected.keepVerdict}(12 月累计
                    ¥{sbRes.projected.future12mConsume} vs
                    保级额 ¥{sbRes.projected.keepRequirement})
                  </Text>
                </View>
                {sbRes.projected.nextLevel ? (
                  <View className={styles.candRow}>
                    <Text>距下一级</Text>
                    <Text className={styles.candScores}>
                      还差 {sbRes.projected.gapToNextLevel} 成长值
                      (L{sbRes.projected.nextLevel})
                    </Text>
                  </View>
                ) : null}
                <View className={styles.formulaBox}>{sbRes.formula}</View>
                <View className={styles.dispTag}>{sbRes.disposition}</View>
              </View>
            ) : null}
          </View>
        )}

        {/* ============ 运营 P2 ============ */}
        {tab === 'ops' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>权益匹配(等级 × RFM)</View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                type="number"
                value={mid}
                onInput={e => setMid(e.detail.value)}
                placeholder="会员 ID"
                maxlength={9}
              />
              <View className={styles.qaBtn} onClick={runBenefit}>匹配</View>
            </View>
            {benefit ? (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    L{benefit.level}
                    {' '}{benefit.levelName || levelName(benefit.level)}
                  </View>
                  <View className={styles.resBatch}>{benefit.rfmLabel}</View>
                </View>
                {(benefit.benefits || []).map((b: string, i: number) => (
                  <View key={i} className={styles.resItem}>· {b}</View>
                ))}
                <View className={styles.formulaBox}>{benefit.matchRule}</View>
                <View className={styles.dispTag}>{benefit.disposition}</View>
              </View>
            ) : null}

            <View className={styles.cardTitle}>积分运营分析</View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                type="number"
                value={mid}
                onInput={e => setMid(e.detail.value)}
                placeholder="会员 ID(留空查全量)"
                maxlength={9}
              />
              <View className={styles.qaBtn} onClick={runPoints}>分析</View>
            </View>
            {points ? (
              <View className={styles.resultCard}>
                {points.scope === 'member' ? (
                  <View>
                    <View className={styles.resHead}>
                      <View className={styles.gradeBadge}>
                        余额 {points.depositBalance} 竹叶
                      </View>
                      <View className={styles.resBatch}>
                        {points.nickname}
                      </View>
                    </View>
                    <View className={styles.candRow}>
                      <Text>获取速率</Text>
                      <Text className={styles.candScores}>
                        {points.earnRatePerMonth} 竹叶/月
                        (累计 {points.totalEarned})
                      </Text>
                    </View>
                    <View className={styles.candRow}>
                      <Text>兑换倾向</Text>
                      <Text className={styles.candScores}>
                        {pct(points.redeemTendency)}(抵扣
                        {' '}{points.orderUsedPoints} /
                        获得 {points.orderConsumedPoints})
                      </Text>
                    </View>
                    <View className={styles.candRow}>
                      <Text>过期风险</Text>
                      <Text className={styles.candScores}>
                        {points.expiringSoon} 竹叶 30 天内到期
                      </Text>
                    </View>
                    <View className={styles.resItem}>
                      {points.expiryRiskNote}
                    </View>
                  </View>
                ) : (
                  <View>
                    <View className={styles.resHead}>
                      <View className={styles.gradeBadge}>
                        全量 {points.memberTotal} 名会员
                      </View>
                    </View>
                    <View className={styles.candRow}>
                      <Text>档案积分总量</Text>
                      <Text className={styles.candScores}>
                        {points.legacyPointsTotal} 竹叶
                      </Text>
                    </View>
                    <View className={styles.candRow}>
                      <Text>兑换倾向</Text>
                      <Text className={styles.candScores}>
                        {pct(points.redeemTendency)}
                      </Text>
                    </View>
                    <View className={styles.candRow}>
                      <Text>过期风险</Text>
                      <Text className={styles.candScores}>
                        {points.membersWithExpiringRisk}
                        名会员 30 天内到期
                      </Text>
                    </View>
                  </View>
                )}
                <View className={styles.resItem}>{points.suggestion}</View>
                <View className={styles.dispTag}>{points.disposition}</View>
              </View>
            ) : null}

            <View className={styles.cardTitle}>沉睡唤醒建议书</View>
            <View className={styles.neverSend}>
              建议书模式: 触达永不自动发送, 须管理员确认后由消息模块执行
            </View>
            <View className={styles.runBtn} onClick={runWakeup}>
              生成唤醒建议书(分级/渠道/时机/权益)
            </View>
            {busy === 'wakeup' ? (
              <View className={styles.empty}>生成中…</View>
            ) : null}
            {wakeup ? (
              <View className={styles.resultCard}>
                <View className={styles.statGrid}>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      {wakeup.sleepingTotal}
                    </View>
                    <View className={styles.statLbl}>沉睡</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      {(wakeup.tierCounts || {}).deep}
                    </View>
                    <View className={styles.statLbl}>深度</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      {(wakeup.tierCounts || {}).medium}
                    </View>
                    <View className={styles.statLbl}>中度</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      {(wakeup.tierCounts || {}).light}
                    </View>
                    <View className={styles.statLbl}>轻度</View>
                  </View>
                </View>
                {(wakeup.suggestions || []).slice(0, 20).map((s: any) => (
                  <View key={s.suggestionId} className={styles.alertCard}>
                    <View className={styles.resHead}>
                      <View
                        className={`${styles.alertTag} ${tierColor(s.tier)}`}
                      >
                        {wakeupTierName(s.tier)}
                      </View>
                      <View className={styles.resBatch}>
                        {s.nickname} · L{s.level}
                      </View>
                    </View>
                    <View className={styles.resItem}>
                      三要素: {s.channel} · {s.timing} · {s.benefit}
                    </View>
                    <View className={styles.resItem}>
                      活跃分 {s.activityScore}/100 ·
                      距上次消费 {s.sleepDays} 天
                    </View>
                    <View className={styles.resItem}>{s.reason}</View>
                  </View>
                ))}
                {(wakeup.suggestions || []).length === 0 ? (
                  <View className={styles.empty}>
                    当前无沉睡会员(活跃分≥40 且 60 天内有消费)
                  </View>
                ) : null}
                <View className={styles.formulaBox}>{wakeup.formula}</View>
                <View className={styles.dispTag}>{wakeup.disposition}</View>
              </View>
            ) : null}
          </View>
        )}

        {/* ============ 进化 P3 ============ */}
        {tab === 'evo' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>可学习参数(安全阀)</View>
            <View className={styles.runBtn} onClick={runParams}>
              查看当前参数
            </View>
            {paramsView ? (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    ltvRetainFactor {paramsView.ltvRetainFactor}
                  </View>
                </View>
                <View className={styles.candRow}>
                  <Text>安全阀 clamp</Text>
                  <Text className={styles.candScores}>
                    [{(paramsView.clampRange || [])[0]},
                    {' '}{(paramsView.clampRange || [])[1]}]
                  </Text>
                </View>
                <View className={styles.candRow}>
                  <Text>默认值</Text>
                  <Text className={styles.candScores}>
                    {paramsView.defaultLtvRetainFactor}
                  </Text>
                </View>
                <View className={styles.formulaBox}>{paramsView.note}</View>
              </View>
            ) : null}

            <View className={styles.cardTitle}>
              反馈闭环(三裁决驱动参数学习)
            </View>
            <View className={styles.evoPanel}>
              <View className={styles.evoLbl}>反馈对象</View>
              <View className={styles.evoBtns}>
                {FB_TARGETS.map(t => (
                  <View
                    key={t}
                    className={`${styles.evoBtn} ${fbTarget === t ? styles.evoBtnActive : ''}`}
                    onClick={() => setFbTarget(t)}
                  >
                    {feedbackTargetName(t)}
                  </View>
                ))}
              </View>
              <View className={styles.evoLbl}>裁决</View>
              <View className={styles.evoBtns}>
                {VERDICTS.map(v => (
                  <View
                    key={v}
                    className={`${styles.evoBtn} ${fbVerdict === v ? styles.evoBtnActive : ''}`}
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
                  placeholder="备注(可选)"
                  maxlength={100}
                />
                <View className={styles.qaBtn} onClick={runFeedback}>
                  提交
                </View>
              </View>
              {fbResult ? (
                <View className={styles.resOk}>{fbResult.learningNote}</View>
              ) : null}
            </View>
            {fbHistory !== null ? (
              <View className={styles.resultCard}>
                <View className={styles.cardTitle}>
                  反馈留痕(最近 {fbHistory.length} 条)
                </View>
                {fbHistory.length > 0 ? fbHistory.map((f: any) => (
                  <View key={f.feedbackId} className={styles.fbItem}>
                    <View className={styles.fbRow}>
                      <Text>
                        {feedbackTargetName(f.targetType)} ·
                        {verdictName(f.verdict)}
                      </Text>
                      <Text
                        className={
                          f.verdict === 'adopted' ? styles.fbAdopted
                            : f.verdict === 'rejected' ? styles.fbRejected
                              : styles.fbCorrected}
                      >
                        {f.paramBefore} → {f.paramAfter}
                      </Text>
                    </View>
                    <View className={styles.fbWeight}>{f.feedbackAt}</View>
                  </View>
                )) : (
                  <View className={styles.empty}>暂无反馈留痕</View>
                )}
              </View>
            ) : null}

            <View className={styles.cardTitle}>
              三检测器(消费尖峰/积分骤降/注册激增)
            </View>
            <View className={styles.runBtn} onClick={runDetect}>
              运行检测扫描
            </View>
            {detectRes ? (
              <View className={styles.resultCard}>
                {(detectRes.alerts || []).map((a: any, i: number) => (
                  <View key={i} className={styles.alertCard}>
                    <View className={styles.resHead}>
                      <View
                        className={`${styles.alertTag} ${styles.sevHigh}`}
                      >
                        {a.detector}
                      </View>
                      <View className={styles.resBatch}>{a.date}</View>
                    </View>
                    <View className={styles.resItem}>
                      当前 {a.current}{a.unit} vs 历史均值
                      {' '}{a.historyAvg}{a.unit} · {a.rule}
                    </View>
                  </View>
                ))}
                {(detectRes.alerts || []).length === 0 ? (
                  <View className={styles.empty}>
                    三序列无命中(冷启动保护或无异常)
                  </View>
                ) : null}
                <View className={styles.formulaBox}>
                  {detectRes.formula}
                </View>
                <View className={styles.dispTag}>
                  {detectRes.disposition}
                </View>
              </View>
            ) : null}

            <View className={styles.cardTitle}>决策备忘录</View>
            <View className={styles.evoPanel}>
              <View className={styles.evoBtns}>
                {MEMO_TOPICS.map(t => (
                  <View
                    key={t}
                    className={`${styles.evoBtn} ${memoTopic === t ? styles.evoBtnActive : ''}`}
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
                  placeholder="决策备注(可选)"
                  maxlength={100}
                />
                <View className={styles.qaBtn} onClick={runMemo}>生成</View>
              </View>
            </View>
            {memoRes ? (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    {memoRes.topicName}
                  </View>
                  <View className={styles.resBatch}>{memoRes.createdAt}</View>
                </View>
                <View className={styles.resItem}>{memoRes.title}</View>
                <View className={styles.resItem}>{memoRes.body}</View>
                {(memoRes.assumptions || []).map((a: string, i: number) => (
                  <View key={i} className={styles.assumpItem}>· {a}</View>
                ))}
                <View className={styles.formulaBox}>{memoRes.formula}</View>
                <View className={styles.dispTag}>{memoRes.disposition}</View>
              </View>
            ) : null}
            {memoHistory !== null ? (
              <View className={styles.resultCard}>
                <View className={styles.cardTitle}>
                  备忘录留痕(最近 {memoHistory.length} 份)
                </View>
                {memoHistory.length > 0 ? memoHistory.map((m: any) => (
                  <View key={m.memoId} className={styles.fbItem}>
                    <View className={styles.fbRow}>
                      <Text>#{m.memoId} {m.topicName}</Text>
                      <Text className={styles.candScores}>
                        {m.createdAt}
                      </Text>
                    </View>
                    <View className={styles.fbWeight}>{m.title}</View>
                  </View>
                )) : (
                  <View className={styles.empty}>暂无备忘录留痕</View>
                )}
              </View>
            ) : null}

            <View className={styles.footNote}>
              智客: 确定性规则引擎 · 建议书模式 · 决策权永在人工 · 不涉信值域
            </View>
          </View>
        )}

        <View className={styles.bottomSpace} />
      </ScrollView>
    </View>
  );
};

export default ZhiKePage;
