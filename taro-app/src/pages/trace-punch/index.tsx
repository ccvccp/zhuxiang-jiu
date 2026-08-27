/**
 * 产品溯源 · 工段扫码打卡 + 批次溯源查询
 * 权限即责任: 责任人须持对应环节权限(后端联动 33 号模块校验)
 * 工段二维码内容 = 工段码(STG-BREW 等), 扫码即定位工段
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, Input, Textarea } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import {
  TraceProdAPI, TraceStageVO, TraceBatchVO, StagePunchVO,
} from '@/api/traceProd';

const STATUS_NAME: Record<string, string> = {
  producing: '生产中', released: '已出库', blocked: '质检阻断',
};
const ANOMALY_NAME: Record<string, string> = {
  skip_stage: '跳工段', time_backflow: '时间倒流',
  dwell_overdue: '超时滞留', qc_blocked: '质检阻断强闯',
};

type TabKey = 'punch' | 'batches' | 'trace';

const TracePunchPage: React.FC = () => {
  const [tab, setTab] = useState<TabKey>('punch');
  const [stages, setStages] = useState<TraceStageVO[]>([]);
  const [batches, setBatches] = useState<TraceBatchVO[]>([]);
  const [activeStage, setActiveStage] = useState<TraceStageVO | null>(null);
  const [batchNo, setBatchNo] = useState('');
  const [qcConclusion, setQcConclusion] = useState('');
  const [paramText, setParamText] = useState('');
  const [myPunches, setMyPunches] = useState<StagePunchVO[]>([]);
  const [traceInput, setTraceInput] = useState('');
  const [traceResult, setTraceResult] = useState<{
    timeline: StagePunchVO[];
    chainValid: boolean;
    currentStageSeq: number;
    health: { score: number; factors: Record<string, number>; anomalyCount: number };
    status: string;
  } | null>(null);
  const [loading, setLoading] = useState(true);

  const loadData = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [s, b] = await Promise.all([
        TraceProdAPI.stages(), TraceProdAPI.batches(),
      ]);
      setStages(s);
      setBatches(b);
      // 我的打卡历史(从最近批次链聚合太重, 仅本地展示最近打卡)
    } catch (e: any) {
      Taro.showToast({ title: e.message || '加载失败', icon: 'none' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, []);

  // ============ 扫码打卡 ============
  const handleScan = () => {
    Taro.scanCode({
      success: (res) => {
        const code = (res.result || '').trim();
        const stage = stages.find(s => s.code === code);
        if (!stage) {
          // 非工段码 → 尝试作为批次号溯源
          if (code.length >= 3) {
            setTraceInput(code);
            setTab('trace');
            handleTrace(code);
          } else {
            Taro.showToast({ title: '非工段二维码', icon: 'none' });
          }
          return;
        }
        setActiveStage(stage);
        setQcConclusion('');
        setParamText('');
        Taro.showToast({ title: `已识别: ${stage.name}`, icon: 'success' });
      },
    });
  };

  const selectStage = (stage: TraceStageVO) => {
    setActiveStage(stage);
    setQcConclusion('');
    setParamText('');
  };

  const handlePunch = async () => {
    if (!activeStage) {
      Taro.showToast({ title: '请先扫码或选择工段', icon: 'none' });
      return;
    }
    if (!batchNo.trim()) {
      Taro.showToast({ title: '请填写批次号', icon: 'none' });
      return;
    }
    if (activeStage.isQcGate && !qcConclusion.trim()) {
      Taro.showToast({
        title: '质检关卡须填质检结论', icon: 'none',
      });
      return;
    }
    let params: Record<string, any> = {};
    if (paramText.trim()) {
      // 简易解析 "键:值, 键2:值2"
      for (const pair of paramText.split(/[,，]/)) {
        const [k, v] = pair.split(/[:：]/);
        if (k?.trim() && v?.trim()) params[k.trim()] = v.trim();
      }
    }
    try {
      const p = await TraceProdAPI.punch(
        activeStage.code, batchNo.trim(), qcConclusion.trim(), params);
      if (p.result === 'block') {
        Taro.showModal({
          title: '质检不合格 · 批次已阻断',
          content: '该批次已被质检阻断, 须管理员解锁后才能流转',
          showCancel: false,
        });
      } else if ((p.anomalies || []).length > 0) {
        Taro.showModal({
          title: '打卡成功(AI 检出异常)',
          content: `异常: ${(p.anomalies || [])
            .map(a => ANOMALY_NAME[a] || a).join('、')}\n已留痕, 将纳入责任考核`,
          showCancel: false,
        });
      } else {
        Taro.showToast({ title: '打卡成功 ✓', icon: 'success' });
      }
      setBatchNo('');
      setQcConclusion('');
      setParamText('');
      setMyPunches([p, ...myPunches].slice(0, 10));
      loadData(true);
    } catch (e: any) {
      Taro.showToast({ title: e.message || '打卡失败', icon: 'none' });
    }
  };

  // ============ 溯源查询 ============
  const handleTrace = async (input?: string) => {
    const no = (input ?? traceInput).trim();
    if (!no) {
      Taro.showToast({ title: '请输入批次号', icon: 'none' });
      return;
    }
    try {
      const pub = await TraceProdAPI.publicTrace(no);
      setTraceResult(pub);
    } catch (e: any) {
      Taro.showToast({ title: e.message || '查询失败', icon: 'none' });
    }
  };

  // ============ 渲染 ============
  const renderTimeline = (timeline: StagePunchVO[],
                          currentSeq: number) => (
    <View className={styles.timeline}>
      {stages.map((s, idx) => {
        const punch = timeline
          .filter(t => t.stageSeq === s.seq)
          .sort((a, b) => b.punchId - a.punchId)[0];
        const done = !!punch && punch.result === 'pass';
        const blocked = punch?.result === 'block';
        const isCurrent = !done && !blocked && s.seq === currentSeq + 1;
        return (
          <View className={styles.tlItem} key={s.code}>
            <View className={`${styles.tlDot} ${
              blocked ? styles.tlDotBlock
                : done ? '' : styles.tlDotPending
            }`}>
              {blocked ? '✕' : done ? '✓' : idx + 1}
            </View>
            <View className={styles.tlBody}>
              <View className={styles.tlHead}>
                <Text className={styles.tlStage}>{s.name}</Text>
                {s.isQcGate ? (
                  <Text className={`${styles.tlState} ${styles.tagQc}`}>质检关卡</Text>
                ) : null}
                {isCurrent ? (
                  <Text className={`${styles.tlState} ${styles.tagWarn}`}>待流转</Text>
                ) : null}
              </View>
              {punch ? (
                <>
                  <View className={styles.tlMeta}>
                    {punch.punchedAt?.slice(0, 19).replace('T', ' ')} ·
                    责任人: {punch.responsibleMasked
                      || punch.responsibleMasked === ''
                      ? (punch.responsibleMasked || '**') : punch.responsible}
                  </View>
                  {punch.qcConclusion ? (
                    <View className={styles.tlMeta}>
                      质检: {punch.qcConclusion}
                    </View>
                  ) : null}
                  {Object.keys(punch.params || {}).length > 0 ? (
                    <View className={styles.tlMeta}>
                      {Object.entries(punch.params)
                        .map(([k, v]) => `${k}:${v}`).join(' · ')}
                    </View>
                  ) : null}
                  {(punch.anomalies || []).map((a, i) => (
                    <Text className={styles.anomalyTag} key={i}>
                      ⚠ {ANOMALY_NAME[a] || a}
                    </Text>
                  ))}
                </>
              ) : (
                <View className={styles.tlMeta}>{s.desc}</View>
              )}
            </View>
          </View>
        );
      })}
    </View>
  );

  return (
    <View className={styles.page}>
      <View className={styles.header}>
        <View className={styles.headerTitle}>🍶 产品溯源</View>
        <View className={styles.headerDesc}>
          工段扫码打卡 · 权限即责任 · 批次全链可溯 · AI 异常把关
        </View>
      </View>

      <View className={styles.tabs}>
        <View className={`${styles.tab} ${tab === 'punch' ? styles.tabActive : ''}`}
          onClick={() => setTab('punch')}>工段打卡</View>
        <View className={`${styles.tab} ${tab === 'batches' ? styles.tabActive : ''}`}
          onClick={() => setTab('batches')}>生产批次</View>
        <View className={`${styles.tab} ${tab === 'trace' ? styles.tabActive : ''}`}
          onClick={() => setTab('trace')}>溯源查询</View>
      </View>

      {loading && <View className={styles.empty}>加载中...</View>}

      {/* ============ 工段打卡 ============ */}
      {tab === 'punch' && !loading && (
        <>
          <View className={styles.scanBtn} onClick={handleScan}>
            <View className={styles.scanIcon}>📷</View>
            <View>扫工段二维码打卡</View>
          </View>

          {activeStage && (
            <View className={styles.section}>
              <View className={styles.sectionTitle}>
                当前工段: {activeStage.name}
              </View>
              <View className={styles.stageMeta}>
                {activeStage.code} · 顺序 {activeStage.seq}/7 ·
                {activeStage.isQcGate ? ' 质检关卡(须结论)' : ' 常规工段'}
              </View>
              <Input
                className={styles.input}
                placeholder='批次号(如 ZX52-2026L08)'
                value={batchNo}
                onInput={e => setBatchNo(e.detail.value)}
              />
              {activeStage.isQcGate && (
                <Input
                  className={styles.input}
                  placeholder='质检结论(必填, 填"不合格"将阻断批次)'
                  value={qcConclusion}
                  onInput={e => setQcConclusion(e.detail.value)}
                />
              )}
              <Textarea
                className={styles.textarea}
                placeholder='工艺参数(选填), 如: 窖池:3号, 酒度:52.2'
                value={paramText}
                onInput={e => setParamText(e.detail.value)}
              />
              <View className={styles.btnRow}>
                <View className={styles.primaryBtn} onClick={handlePunch}>
                  确认打卡(责任人签名)
                </View>
                <View className={styles.ghostBtn}
                  onClick={() => setActiveStage(null)}>取消</View>
              </View>
            </View>
          )}

          <View className={styles.section}>
            <View className={styles.sectionTitle}>
              7 工段(手动选择)
            </View>
            {stages.map(s => (
              <View className={styles.stageCard} key={s.code}>
                <View className={styles.stageHead}>
                  <Text className={styles.stageName}>
                    {s.seq}. {s.name}
                  </Text>
                  {s.isQcGate ? (
                    <Text className={`${styles.tag} ${styles.tagQc}`}>质检关卡</Text>
                  ) : null}
                </View>
                <View className={styles.stageMeta}>
                  {s.code} · 权限环节: {s.permStage} · {s.desc}
                </View>
                <View className={styles.punchBtn}
                  onClick={() => selectStage(s)}>选择此工段打卡</View>
              </View>
            ))}
          </View>

          {myPunches.length > 0 && (
            <View className={styles.section}>
              <View className={styles.sectionTitle}>我最近的打卡</View>
              {myPunches.map(p => (
                <View className={styles.batchCard} key={p.punchId}>
                  <View className={styles.batchHead}>
                    <Text className={styles.batchNo}>{p.stageName}</Text>
                    <Text className={`${styles.tag} ${
                      p.result === 'pass' ? styles.tagActive : styles.tagBlocked
                    }`}>{p.result === 'pass' ? '通过' : '阻断'}</Text>
                  </View>
                  <View className={styles.batchMeta}>
                    批次 {p.batchNo} · {p.punchedAt.slice(0, 19).replace('T', ' ')}
                  </View>
                </View>
              ))}
            </View>
          )}
        </>
      )}

      {/* ============ 生产批次 ============ */}
      {tab === 'batches' && !loading && (
        <View className={styles.section}>
          <View className={styles.sectionTitle}>生产批次({batches.length})</View>
          {batches.length === 0 && (
            <View className={styles.empty}>暂无批次{'\n'}生产环节权限持有者可创建批次</View>
          )}
          {batches.map(b => (
            <View className={styles.batchCard} key={b.batchNo}>
              <View className={styles.batchHead}>
                <Text className={styles.batchNo}>{b.batchNo}</Text>
                <Text className={`${styles.tag} ${
                  b.status === 'released' ? styles.tagReleased
                    : b.status === 'blocked' ? styles.tagBlocked
                      : styles.tagActive
                }`}>{STATUS_NAME[b.status] || b.status}</Text>
              </View>
              <View className={styles.batchMeta}>
                计划 {b.plannedQty} 瓶 · 已流转 {b.currentStageSeq}/7 工段
                {b.lifeCodes.length > 0 ? ` · 已绑 ${b.lifeCodes.length} 瓶码` : ''}
              </View>
              <View className={styles.progressRow}>
                {stages.map(s => (
                  <View key={s.code} className={`${styles.progressCell} ${
                    s.seq <= b.currentStageSeq ? styles.progressDone : ''
                  }`} />
                ))}
              </View>
              {b.status === 'blocked' && b.blockedReason ? (
                <View className={styles.batchMeta}>⚠ {b.blockedReason}</View>
              ) : null}
              <View className={styles.punchBtn} onClick={() => {
                setTraceInput(b.batchNo);
                setTab('trace');
                handleTrace(b.batchNo);
              }}>查看溯源链 ›</View>
            </View>
          ))}
        </View>
      )}

      {/* ============ 溯源查询 ============ */}
      {tab === 'trace' && !loading && (
        <>
          <View className={styles.section}>
            <View className={styles.sectionTitle}>批次溯源查询</View>
            <Input
              className={styles.input}
              placeholder='输入批次号(如 ZX42-2026L09)'
              value={traceInput}
              onInput={e => setTraceInput(e.detail.value)}
            />
            <View className={styles.btnRow}>
              <View className={styles.primaryBtn} onClick={() => handleTrace()}>
                查询生产溯源链
              </View>
            </View>
          </View>

          {traceResult && (
            <>
              <View className={styles.healthCard}>
                <View>
                  <View className={styles.healthScore}>
                    {traceResult.health.score}
                  </View>
                  <View className={styles.healthLabel}>AI 健康度</View>
                </View>
                <View className={styles.healthFactors}>
                  链完整 {traceResult.health.factors.chainCompleteness}/40 ·
                  无异常 {traceResult.health.factors.noAnomaly}/30{'\n'}
                  时效 {traceResult.health.factors.timeliness}/20 ·
                  质检 {traceResult.health.factors.qcComplete}/10
                </View>
                <View className={`${styles.chainValid} ${
                  traceResult.chainValid ? '' : styles.tagBlocked
                }`}>
                  {traceResult.chainValid ? '链校验✓' : '链异常!'}
                </View>
              </View>
              <View className={styles.section}>
                <View className={styles.sectionTitle}>
                  生产时间线({traceResult.currentStageSeq}/7)
                </View>
                {renderTimeline(traceResult.timeline,
                  traceResult.currentStageSeq)}
              </View>
            </>
          )}
        </>
      )}
    </View>
  );
};

export default TracePunchPage;
