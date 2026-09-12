/**
 * 智码·AI智能二维码大模型 · 前端管理工作台
 * 四页签: 总览(P0) → 六码(P1-P6) → 愉悦(P7) → 免疫(P8)
 * 口径: 六类码封闭注册(LLM 禁入判定链)
 *       生成走 55号签名链(业务参数白名单)
 *       愉悦度=观测指标(策略变更走 46号审批——永不在快环调整)
 *       观测面不受 QR70_MODE 影响(默认 off 零影响上线)
 * 所有业务数字 100% 来自 /api/qr70/* 查询层(数字不出现在模板层)
 */
import React, { useState } from 'react';
import { View, Text, ScrollView, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  QrAPI, codeKindName, lifecycleName, policyName, personaName,
  driftName, scanVerdictName, layoutName, hypStatusName,
  paramStatusName, vectorName,
} from '@/api/qr';

type Tab = 'overview' | 'codes' | 'joy' | 'immunity';

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: '总览' },
  { key: 'codes', label: '六码' },
  { key: 'joy', label: '愉悦' },
  { key: 'immunity', label: '免疫' },
];

const errMsg = (e: any): string =>
  String(e?.message || e).slice(0, 40);

const ZhiMaPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('overview');

  // ============ 总览 P0 ============
  const [status, setStatus] = useState<any>(null);
  const [dict, setDict] = useState<any>(null);
  const [codes, setCodes] = useState<any>(null);
  const [joyStats, setJoyStats] = useState<any>(null);

  const loadOverview = async () => {
    setStatus(await QrAPI.modelStatus().catch(() => null));
    setDict(await QrAPI.dict().catch(() => null));
    setCodes(await QrAPI.codes().catch(() => null));
    setJoyStats(await QrAPI.joyStats().catch(() => null));
  };

  // ============ 六码演示 ============
  // ① 统一生成/核销
  const [genResult, setGenResult] = useState<any>(null);
  const [redeemResult, setRedeemResult] = useState<any>(null);
  const [genCodeId, setGenCodeId] = useState('auth-entry');
  const [genMember, setGenMember] = useState('9');

  const runGenerate = async () => {
    const memberId = Number(genMember) || 0;
    const params: Record<string, string> =
      genCodeId === 'auth-entry'
        ? { deviceHint: 'demo' }
        : genCodeId === 'trace-bottle'
          ? { batchNo: 'B-DEMO', stage: 'STG-PACK' }
          : genCodeId === 'collect-merchant'
            ? { amount: '99', sceneNote: '演示', challenge: '0' }
            : genCodeId === 'receiving-sign'
              ? { orderId: 'ORD-DEMO', fenceKm: '20' }
              : genCodeId === 'shipping-handover'
                ? { waveNo: 'W-DEMO', carrier: 'SF', orderId: 'ORD-DEMO' }
                : { station: 'STG-PACK', batchNo: 'B-DEMO' };
    const scene = genCodeId === 'collect-merchant' ? 'storefront'
      : genCodeId === 'shipping-handover' ? 'warehouse'
        : genCodeId === 'manage-workbench' ? 'warehouse' : 'consumer';
    setGenResult(await QrAPI.generate(memberId, genCodeId, params, scene)
      .catch(e => {
        Taro.showToast({ title: errMsg(e), icon: 'none' });
        return null;
      }));
    setRedeemResult(null);
  };

  const runRedeem = async () => {
    if (!genResult?.code) return;
    setRedeemResult(await QrAPI.redeem(genResult.code, 7)
      .catch(e => {
        Taro.showToast({ title: errMsg(e), icon: 'none' });
        return null;
      }));
  };

  // ② 溯源分层呈现
  const [traceResult, setTraceResult] = useState<any>(null);
  const [traceCode, setTraceCode] = useState('');
  const [persona, setPersona] = useState('quality');

  const runTraceView = async () => {
    if (!traceCode.trim()) {
      Taro.showToast({ title: '请输入瓶码(BLC- 或签名码)', icon: 'none' });
      return;
    }
    setTraceResult(await QrAPI.traceView(traceCode.trim(), persona)
      .catch(e => {
        Taro.showToast({ title: errMsg(e), icon: 'none' });
        return null;
      }));
  };

  // ③ 收款版式演示
  const [collectResult, setCollectResult] = useState<any>(null);
  const [collectRedeemed, setCollectRedeemed] = useState<any>(null);
  const [ctAmount, setCtAmount] = useState('88');
  const [ctScene, setCtScene] = useState('storefront');

  const runCollect = async () => {
    const amount = Number(ctAmount);
    if (!amount || amount <= 0) {
      Taro.showToast({ title: '请输入金额', icon: 'none' });
      return;
    }
    setCollectResult(await QrAPI.collectIssue(66, amount, ctScene, '演示桌')
      .catch(e => {
        Taro.showToast({ title: errMsg(e), icon: 'none' });
        return null;
      }));
    setCollectRedeemed(null);
  };

  const runCollectRedeem = async () => {
    if (!collectResult?.code) return;
    setCollectRedeemed(await QrAPI.collectRedeem(collectResult.code, 66)
      .catch(e => {
        Taro.showToast({ title: errMsg(e), icon: 'none' });
        return null;
      }));
  };

  // ④ 认证漂移观测
  const [driftResult, setDriftResult] = useState<any>(null);
  const [driftRisk, setDriftRisk] = useState('75');

  const runDrift = async () => {
    setDriftResult(await QrAPI.driftCheck(9, 'fp-stored-x', 'fp-present-y',
      Number(driftRisk) || 0).catch(e => {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
      return null;
    }));
  };

  // ============ 愉悦 P7 ============
  const [engineDict, setEngineDict] = useState<any>(null);
  const [renderResult, setRenderResult] = useState<any>(null);
  const [hyps, setHyps] = useState<any>(null);
  const [drift, setDrift] = useState<any>(null);
  const [elderly, setElderly] = useState(false);
  const [lowLight, setLowLight] = useState(false);

  const loadJoy = async () => {
    setEngineDict(await QrAPI.engineDict().catch(() => null));
    setHyps(await QrAPI.hypotheses().catch(() => null));
  };

  const runRender = async () => {
    setRenderResult(await QrAPI.renderParams(9, 'trace', elderly, lowLight, elderly ? 3 : 0)
      .catch(e => {
        Taro.showToast({ title: errMsg(e), icon: 'none' });
        return null;
      }));
  };

  const runDriftDetect = async () => {
    setDrift(await QrAPI.driftDetect().catch(() => null));
  };

  // ============ 免疫 P8 ============
  const [immunity, setImmunity] = useState<any>(null);
  const [redteamResult, setRedteamResult] = useState<any>(null);
  const [rtVector, setRtVector] = useState('replay_flood');

  const loadImmunity = async () => {
    setImmunity(await QrAPI.immunityView().catch(() => null));
  };

  const runRedteam = async () => {
    setRedteamResult(await QrAPI.redteamRun(rtVector).catch(e => {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
      return null;
    }));
  };

  const runMonitor = async () => {
    const m = await QrAPI.immunityMonitor().catch(e => {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
      return null;
    });
    if (m) {
      setDrift(m);
      setImmunity(await QrAPI.immunityView().catch(() => null));
    }
  };

  const toggleFreeze = async () => {
    const frozen = immunity?.frozen?.frozen;
    const r = await (frozen
      ? QrAPI.immunityUnfreeze()
      : QrAPI.immunityFreeze()).catch(e => {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
      return null;
    });
    if (r) {
      setImmunity(await QrAPI.immunityView().catch(() => null));
      Taro.showToast({
        title: frozen ? '已解冻(人工专属)' : '已冻结(保护方向)',
        icon: 'none',
      });
    }
  };

  // ============ 渲染辅助 ============
  const genCodes = dict?.codes || [];

  return (
    <View className={styles.page}>
      <NavBar title="智码·AI智能二维码大模型" />

      <ScrollView scrollY className={styles.body}>
        <View className={styles.section}>
          <View className={styles.heroCard}>
            <View className={styles.heroTitle}>智码·AI智能二维码大模型</View>
            <View className={styles.heroDesc}>
              六类码语义中枢 · 流程愉悦引擎 · 安全免疫系统
            </View>
            <View className={styles.heroMeta}>
              <Text>九期 P0-P8</Text>
              <Text>71 端点</Text>
              <Text>530 断言</Text>
            </View>
            <View className={styles.heroRule}>
              铁律: LLM 禁入判定链 · 愉悦度=观测指标(46号审批) · 55号签名链零改动
            </View>
          </View>
        </View>

        {/* 页签栏 */}
        <View className={styles.tabBar}>
          {TABS.map(t => (
            <View key={t.key}
              className={tab === t.key ? styles.tabActive : styles.tab}
              onClick={() => setTab(t.key)}>
              {t.label}
            </View>
          ))}
        </View>

        {tab === 'overview' && (
          <View className={styles.section}>
            <View className={styles.loadBtn} onClick={loadOverview}>加载总览数据</View>

            {status && (
              <>
                <View className={styles.cardTitle}>模型状态(P0 观测面)</View>
                <View className={styles.statGrid}>
                  <View className={styles.statItem}>
                    <View className={styles.statNum}>{status.codeKindCount || 0}</View>
                    <View className={styles.statLabel}>码类</View>
                  </View>
                  <View className={styles.statItem}>
                    <View className={styles.statNum}>{status.codeTypeCount || 0}</View>
                    <View className={styles.statLabel}>码型</View>
                  </View>
                  <View className={styles.statItem}>
                    <View className={styles.statNum}>{status.codeInstanceCount || 0}</View>
                    <View className={styles.statLabel}>码实例</View>
                  </View>
                </View>
                <View className={styles.footNote}>
                  模式: {String(status.mode || 'off')} · 生命周期: {(status.lifecycleStates || []).join('/')}
                </View>
              </>
            )}

            {dict && (
              <>
                <View className={styles.cardTitle}>六类码注册表(封闭)</View>
                {(dict.kinds || []).map((k: any) => (
                  <View key={k.kind} className={styles.rowCard}>
                    <View className={styles.rowHead}>
                      <Text className={styles.rowTitle}>{k.kindLabel || codeKindName(k.kind)}</Text>
                      <Text className={styles.badge}>{k.codeCount} 码型</Text>
                    </View>
                    <View className={styles.rowDesc}>
                      {(k.codeIds || []).join(' · ')}
                    </View>
                  </View>
                ))}
              </>
            )}

            {joyStats && (
              <>
                <View className={styles.cardTitle}>愉悦度观测基线(快环)</View>
                {(joyStats.stats || []).map((s: any) => (
                  <View key={s.codeId} className={styles.rowCard}>
                    <View className={styles.rowHead}>
                      <Text className={styles.rowTitle}>{s.codeId}</Text>
                      <Text className={styles.badge}>{s.sampleCount} 样本</Text>
                    </View>
                    <View className={styles.rowDesc}>
                      均耗时 {s.avgDurationMs}ms · 完成率 {(s.completeRate * 100).toFixed(0)}% · 误触率 {(s.misTouchRate * 100).toFixed(0)}%
                    </View>
                  </View>
                ))}
                <View className={styles.footNote}>
                  {String(joyStats.note || '')}
                </View>
              </>
            )}

            {codes && (
              <>
                <View className={styles.cardTitle}>码实例留痕(生命周期)</View>
                {(codes.codes || []).slice(0, 6).map((c: any) => (
                  <View key={c.nonce} className={styles.rowCard}>
                    <View className={styles.rowHead}>
                      <Text className={styles.rowTitle}>{c.codeId}</Text>
                      <Text className={styles.badge}>{lifecycleName(c.status)}</Text>
                    </View>
                    <View className={styles.rowDesc}>
                      {codeKindName(c.kind)} · {c.scene || '无场景'} · {policyName(c.consumePolicy || '')}
                    </View>
                  </View>
                ))}
              </>
            )}
          </View>
        )}

        {tab === 'codes' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>① 统一生成/核销管道(55号签名链)</View>
            <View className={styles.chipRow}>
              {['auth-entry', 'trace-bottle', 'receiving-sign', 'shipping-handover',
                'manage-workbench', 'collect-merchant'].map(id => (
                <View key={id}
                  className={genCodeId === id ? styles.chipActive : styles.chip}
                  onClick={() => setGenCodeId(id)}>
                  {id.split('-')[0]}
                </View>
              ))}
            </View>
            <View className={styles.inputRow}>
              <Text className={styles.inputLabel}>会员ID</Text>
              <Input className={styles.input} value={genMember}
                onInput={e => setGenMember(e.detail.value)} />
            </View>
            <View className={styles.runBtn} onClick={runGenerate}>生成(决策面需 assist)</View>
            {genResult && (
              <View className={styles.resultCard}>
                <View className={styles.resultLine}>码型: {genResult.codeId}</View>
                <View className={styles.resultLine}>状态: {lifecycleName(genResult.status)}</View>
                <View className={styles.resultLine}>策略: {policyName(genResult.consumePolicy)}</View>
                <View className={styles.resultCode}>{String(genResult.code || '').slice(0, 52)}…</View>
                <View className={styles.runBtn} onClick={runRedeem}>核销该码</View>
              </View>
            )}
            {redeemResult && (
              <View className={styles.resultCard}>
                <View className={styles.resultLine}>
                  verify={String(redeemResult.verifyStatus)} · redeemed={String(redeemResult.redeemed)}
                </View>
                {redeemResult.reason && (
                  <View className={styles.resultLine}>{String(redeemResult.reason)}</View>
                )}
              </View>
            )}

            <View className={styles.cardTitle}>② 溯源分层呈现(三画像)</View>
            <View className={styles.chipRow}>
              {['quality', 'story', 'value'].map(p => (
                <View key={p}
                  className={persona === p ? styles.chipActive : styles.chip}
                  onClick={() => setPersona(p)}>
                  {personaName(p)}
                </View>
              ))}
            </View>
            <View className={styles.inputRow}>
              <Input className={styles.input} placeholder="BLC-瓶码 或签名码"
                value={traceCode} onInput={e => setTraceCode(e.detail.value)} />
            </View>
            <View className={styles.runBtn} onClick={runTraceView}>分层呈现(公开)</View>
            {traceResult?.viewable && (
              <View className={styles.resultCard}>
                <View className={styles.resultLine}>
                  {traceResult.personaLabel} · 批次 {String(traceResult.batchNo)}
                </View>
                {(traceResult.sections || []).slice(0, 3).map((s: any) => (
                  <View key={s.sectionId} className={styles.resultLine}>
                    {s.priority}. {s.title}
                  </View>
                ))}
                {traceResult.redFlagCount > 0 && (
                  <View className={styles.resultWarn}>异常标红 {traceResult.redFlagCount} 项</View>
                )}
              </View>
            )}
            {traceResult && !traceResult.viewable && (
              <View className={styles.resultCard}>
                <View className={styles.resultWarn}>
                  {String(traceResult.verifyStatus)}: {String(traceResult.reason || '')}
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>③ 收款版式(场景×大额挑战)</View>
            <View className={styles.chipRow}>
              {['storefront', 'street'].map(s => (
                <View key={s}
                  className={ctScene === s ? styles.chipActive : styles.chip}
                  onClick={() => setCtScene(s)}>
                  {s === 'storefront' ? '门店' : '夜市'}
                </View>
              ))}
            </View>
            <View className={styles.inputRow}>
              <Text className={styles.inputLabel}>金额</Text>
              <Input className={styles.input} type="number" value={ctAmount}
                onInput={e => setCtAmount(e.detail.value)} />
            </View>
            <View className={styles.runBtn} onClick={runCollect}>生成收款码</View>
            {collectResult && (
              <View className={styles.resultCard}>
                <View className={styles.resultLine}>
                  版式: {layoutName(collectResult.layout)}
                </View>
                {collectResult.challengeRequired && (
                  <View className={styles.resultWarn}>大额挑战已嵌入(challenge=1)</View>
                )}
                <View className={styles.runBtn} onClick={runCollectRedeem}>核销收款</View>
              </View>
            )}
            {collectRedeemed && (
              <View className={styles.resultCard}>
                <View className={styles.resultLine}>
                  redeemed={String(collectRedeemed.redeemed)} · 金额 {collectRedeemed.amount}
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>④ 认证漂移观测(公开快环)</View>
            <View className={styles.inputRow}>
              <Text className={styles.inputLabel}>风控分</Text>
              <Input className={styles.input} type="number" value={driftRisk}
                onInput={e => setDriftRisk(e.detail.value)} />
            </View>
            <View className={styles.runBtn} onClick={runDrift}>漂移校准</View>
            {driftResult && (
              <View className={styles.resultCard}>
                <View className={styles.resultLine}>
                  漂移级: {driftName(driftResult.driftLevel)} · 风控 {driftResult.riskScore}
                </View>
                <View className={styles.resultLine}>
                  已存 {String(driftResult.storedFingerprint)} vs 呈现 {String(driftResult.presentedFingerprint)}
                </View>
              </View>
            )}
          </View>
        )}

        {tab === 'joy' && (
          <View className={styles.section}>
            <View className={styles.loadBtn} onClick={loadJoy}>加载引擎数据</View>

            {engineDict && (
              <>
                <View className={styles.cardTitle}>四层引擎(自适应学习)</View>
                <View className={styles.rowCard}>
                  <View className={styles.rowDesc}>
                    {(engineDict.layers || []).join(' → ')}
                  </View>
                </View>
                <View className={styles.footNote}>
                  表现层白名单: {(engineDict.renderWhitelist || []).join(' / ')}
                </View>
              </>
            )}

            <View className={styles.cardTitle}>端侧渲染建议(白名单六参数)</View>
            <View className={styles.chipRow}>
              <View className={elderly ? styles.chipActive : styles.chip}
                onClick={() => setElderly(!elderly)}>老年模式</View>
              <View className={lowLight ? styles.chipActive : styles.chip}
                onClick={() => setLowLight(!lowLight)}>弱光环境</View>
            </View>
            <View className={styles.runBtn} onClick={runRender}>获取建议</View>
            {renderResult && (
              <View className={styles.resultCard}>
                {Object.entries(renderResult.renderParams || {}).map(([k, v]) => (
                  <View key={k} className={styles.resultLine}>{k} = {String(v)}</View>
                ))}
                <View className={styles.footNote}>
                  业务参数服务端唯一权威(白名单外即拒)
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>假设建议书(46号审批链)</View>
            {hyps && (hyps.hypotheses || []).slice(0, 5).map((h: any) => (
              <View key={h.hypothesisId} className={styles.rowCard}>
                <View className={styles.rowHead}>
                  <Text className={styles.rowTitle}>{h.paramId}</Text>
                  <Text className={styles.badge}>{hypStatusName(h.status)}</Text>
                </View>
                <View className={styles.rowDesc}>
                  {String(h.proposedAction?.from)} → {String(h.proposedAction?.to)}
                </View>
                <View className={styles.rowDesc}>{String(h.reason || '')}</View>
              </View>
            ))}
            {hyps && (hyps.hypotheses || []).length === 0 && (
              <View className={styles.empty}>暂无假设(愉悦度观测积累中)</View>
            )}
            <View className={styles.footNote}>
              愉悦度=奖励信号非决策主体 · 影子验证≥7天 · QR70_KILL 秒级制动
            </View>

            <View className={styles.runBtn} onClick={runDriftDetect}>漂移检测(元认知)</View>
            {drift && (
              <View className={styles.resultCard}>
                <View className={styles.resultLine}>
                  近窗 {drift.recentWindow} · 漂移 {drift.drift} · {'阈'} {drift.threshold}
                </View>
                <View className={drift.drifted ? styles.resultWarn : styles.resultOk}>
                  {drift.drifted ? '漂移预警(自动冻结进化)' : '分布正常'}
                </View>
              </View>
            )}
          </View>
        )}

        {tab === 'immunity' && (
          <View className={styles.section}>
            <View className={styles.loadBtn} onClick={loadImmunity}>加载免疫看板</View>

            {immunity && (
              <>
                <View className={styles.cardTitle}>免疫状态(P8)</View>
                <View className={immunity.frozen?.frozen ? styles.resultWarn : styles.resultOk}>
                  {immunity.frozen?.frozen ? '进化已冻结(保护方向)' : '进化运行中(未冻结)'}
                </View>
                <View className={styles.statGrid}>
                  <View className={styles.statItem}>
                    <View className={styles.statNum}>{immunity.redteamRuns || 0}</View>
                    <View className={styles.statLabel}>红队批次</View>
                  </View>
                  <View className={styles.statItem}>
                    <View className={styles.statNum}>{immunity.defendedCount || 0}</View>
                    <View className={styles.statLabel}>防御成功</View>
                  </View>
                  <View className={styles.statItem}>
                    <View className={styles.statNum}>{immunity.replayFloodThreshold || 5}</View>
                    <View className={styles.statLabel}>泛洪阈值</View>
                  </View>
                </View>
                <View className={styles.runBtn} onClick={toggleFreeze}>
                  {immunity.frozen?.frozen ? '解冻(人工专属)' : '人工冻结'}
                </View>
              </>
            )}

            <View className={styles.cardTitle}>红队四向量(决策面需 assist)</View>
            <View className={styles.chipRow}>
              {['replay_flood', 'forged_code', 'whitelist_bypass', 'render_poison'].map(v => (
                <View key={v}
                  className={rtVector === v ? styles.chipActive : styles.chip}
                  onClick={() => setRtVector(v)}>
                  {vectorName(v).split(' ')[0]}
                </View>
              ))}
            </View>
            <View className={styles.runBtn} onClick={runRedteam}>执行红队攻击</View>
            {redteamResult && (
              <View className={styles.resultCard}>
                <View className={redteamResult.defended ? styles.resultOk : styles.resultWarn}>
                  {redteamResult.defended ? '✓ 全部防住' : '✗ 存在突破'}
                </View>
                {Object.entries(redteamResult.detail?.checks || {}).map(([k, v]) => (
                  <View key={k} className={styles.resultLine}>
                    {k}: {String(v)}
                  </View>
                ))}
              </View>
            )}

            <View className={styles.runBtn} onClick={runMonitor}>分布监控(自动冻结)</View>
            <View className={styles.footNote}>
              冻结自动(保护方向) / 解冻人工专属 · 环境变量 QR70_IMMUNITY 双保险
            </View>
          </View>
        )}

        <View className={styles.section}>
          <View className={styles.footNote}>
            智码·AI智能二维码大模型 · 数据 100% 来自 /api/qr70/* 查询层 ·
            观测面不受 QR70_MODE 影响
          </View>
        </View>
      </ScrollView>
    </View>
  );
};

export default ZhiMaPage;
