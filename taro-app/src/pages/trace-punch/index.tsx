/**
 * 产品溯源 · 工段扫码打卡 + 批次溯源查询
 * 权限即责任: 责任人须持对应环节权限(后端联动 33 号模块校验)
 * 工段二维码内容 = 工段码(STG-BREW 等), 扫码即定位工段
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, Input, Textarea, Canvas } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import { qrMatrix, renderQrMatrix } from '@/utils/qrcode';
import {
  TraceProdAPI, TraceStageVO, TraceBatchVO, StagePunchVO, StageQrVO,
} from '@/api/traceProd';
import { getSession } from '@/services/auth-service';

const STATUS_NAME: Record<string, string> = {
  producing: '生产中', released: '已出库', blocked: '质检阻断',
};
const ANOMALY_NAME: Record<string, string> = {
  skip_stage: '跳工段', time_backflow: '时间倒流',
  dwell_overdue: '超时滞留', qc_blocked: '质检阻断强闯',
};

type TabKey = 'punch' | 'codes' | 'batches' | 'trace' | 'admin';

/** 管理端统计 */
interface AdminStatsVO {
  batchTotal: number;
  batchByStatus: Record<string, number>;
  punchTotal: number;
  anomalyTotal: number;
  avgHealthScore: number;
}

interface AdminAnomalyVO {
  punchId: number;
  batchNo: string;
  stageCode: string;
  anomalies: string[];
  memberId: number;
  memberNickname: string;
  punchedAt: string;
}

const TracePunchPage: React.FC = () => {
  const [tab, setTab] = useState<TabKey>('punch');
  const [stages, setStages] = useState<TraceStageVO[]>([]);
  const [stageQrs, setStageQrs] = useState<StageQrVO[]>([]);
  const [batches, setBatches] = useState<TraceBatchVO[]>([]);
  const [activeStage, setActiveStage] = useState<TraceStageVO | null>(null);
  const [batchNo, setBatchNo] = useState('');
  const [qcConclusion, setQcConclusion] = useState('');
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
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

  // ============ P3: 管理驾驶舱 ============
  const isAdmin = getSession()?.role === 'admin';
  const [adminStats, setAdminStats] = useState<AdminStatsVO | null>(null);
  const [adminAnoms, setAdminAnoms] = useState<AdminAnomalyVO[]>([]);

  const loadData = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [s, b, q] = await Promise.all([
        TraceProdAPI.stages(), TraceProdAPI.batches(),
        TraceProdAPI.stagesQr(),
      ]);
      setStages(s);
      setBatches(b);
      setStageQrs(q);
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
        // P2: 支持印刷载荷格式 ZXBJ-TRACE:{code}:v1
        let stageCode = code;
        const parts = code.split(':');
        if (parts[0] === 'ZXBJ-TRACE' && parts.length >= 2) {
          stageCode = parts[1];
        }
        const stage = stages.find(s => s.code === stageCode);
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
        selectStage(stage);
        Taro.showToast({ title: `已识别: ${stage.name}`, icon: 'success' });
      },
    });
  };

  const selectStage = (stage: TraceStageVO) => {
    setActiveStage(stage);
    setQcConclusion('');
    setParamValues({});
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
      Taro.showToast({ title: '质检关卡须填质检结论', icon: 'none' });
      return;
    }
    // 必填参数本地预检(后端亦强校验)
    const missing = (activeStage.paramsTemplate || [])
      .filter(t => t.required && !paramValues[t.key]?.trim());
    if (missing.length > 0) {
      Taro.showToast({
        title: `缺必填参数: ${missing.map(m => m.label).join('、')}`,
        icon: 'none',
      });
      return;
    }
    // 过滤空值
    const params: Record<string, string> = {};
    for (const [k, v] of Object.entries(paramValues)) {
      if (v.trim()) params[k] = v.trim();
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
      } else if (p.aiQcReview && p.aiQcReview.score < 100) {
        Taro.showModal({
          title: `打卡成功(AI 审核 ${p.aiQcReview.score}分)`,
          content: (p.aiQcReview.flags || []).join('\n')
            || '质检结论已通过审核',
          showCancel: false,
        });
      } else {
        Taro.showToast({ title: '打卡成功 ✓', icon: 'success' });
      }
      setBatchNo('');
      setQcConclusion('');
      setParamValues({});
      setMyPunches([p, ...myPunches].slice(0, 10));
      loadData(true);
    } catch (e: any) {
      Taro.showToast({ title: e.message || '打卡失败', icon: 'none' });
    }
  };

  // ============ P2: 工段码绘制与保存 ============
  const drawStageQrs = useCallback((qrs: StageQrVO[]) => {
    Taro.nextTick(() => {
      setTimeout(() => {
        try {
          const sys = Taro.getSystemInfoSync();
          const sizePx = Math.round((240 / 750) * sys.windowWidth);
          for (const q of qrs) {
            const ctx = Taro.createCanvasContext(`stageQr${q.seq}`);
            renderQrMatrix(ctx, qrMatrix(q.payload), sizePx);
            ctx.draw();
          }
        } catch (e) {
          console.warn('[trace] 工段码绘制失败:', e);
        }
      }, 150);
    });
  }, []);

  useEffect(() => {
    if (tab === 'codes' && stageQrs.length > 0) {
      drawStageQrs(stageQrs);
    }
  }, [tab, stageQrs, drawStageQrs]);

  const handleSaveStageQr = (seq: number, name: string) => {
    Taro.canvasToTempFilePath({
      canvasId: `stageQr${seq}`,
      success: r => {
        Taro.saveImageToPhotosAlbum({
          filePath: r.tempFilePath,
          success: () => Taro.showToast({
            title: `${name}工段码已保存`, icon: 'success',
          }),
          fail: () => Taro.showToast({
            title: '保存失败,请检查相册权限', icon: 'none',
          }),
        });
      },
      fail: () => Taro.showToast({ title: '导出失败', icon: 'none' }),
    });
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

  // ============ P3: 管理驾驶舱 ============
  const loadAdmin = useCallback(async () => {
    try {
      const [stats, anoms] = await Promise.all([
        TraceProdAPI.adminStats(),
        TraceProdAPI.adminAnomalies(),
      ]);
      setAdminStats(stats);
      setAdminAnoms(anoms);
    } catch (e: any) {
      Taro.showToast({ title: e.message || '管理数据加载失败', icon: 'none' });
    }
  }, []);

  useEffect(() => {
    if (tab === 'admin' && isAdmin) loadAdmin();
  }, [tab, isAdmin, loadAdmin]);

  const handleUnblock = (batchNo: string) => {
    Taro.showModal({
      title: `解除阻断 ${batchNo}`,
      editable: true,
      placeholderText: '请输入解除原因(如: 复检合格)',
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await TraceProdAPI.adminUnblock(batchNo, res.content || '');
          Taro.showToast({ title: '已解除阻断', icon: 'success' });
          loadAdmin();
          loadData(true);
        } catch (e: any) {
          Taro.showToast({ title: e.message || '解除失败', icon: 'none' });
        }
      },
    });
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
                      {punch.aiQcReview ? (
                        <Text className={styles.aiReviewScore}>
                          AI 审核 {punch.aiQcReview.score}分
                        </Text>
                      ) : null}
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
        <View className={`${styles.tab} ${tab === 'codes' ? styles.tabActive : ''}`}
          onClick={() => setTab('codes')}>工段码</View>
        <View className={`${styles.tab} ${tab === 'batches' ? styles.tabActive : ''}`}
          onClick={() => setTab('batches')}>生产批次</View>
        <View className={`${styles.tab} ${tab === 'trace' ? styles.tabActive : ''}`}
          onClick={() => setTab('trace')}>溯源查询</View>
        {isAdmin && (
          <View className={`${styles.tab} ${tab === 'admin' ? styles.tabActive : ''}`}
            onClick={() => setTab('admin')}>管理</View>
        )}
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
                {activeStage.isQcGate ? ' 质检关卡(须结论+AI审核)' : ' 常规工段'}
              </View>
              <Input
                className={styles.input}
                placeholder='批次号(如 ZX52-2026L08)'
                value={batchNo}
                onInput={e => setBatchNo(e.detail.value)}
              />
              {(activeStage.paramsTemplate || []).map(t => (
                <Input
                  key={t.key}
                  className={styles.input}
                  placeholder={`${t.label}${t.unit ? `(${t.unit})` : ''}${t.required ? ' *必填' : ''}`}
                  value={paramValues[t.key] || ''}
                  onInput={e => setParamValues({
                    ...paramValues, [t.key]: e.detail.value,
                  })}
                />
              ))}
              {activeStage.isQcGate && (
                <Textarea
                  className={styles.textarea}
                  placeholder='质检结论(必填, AI 语义审核: 须含指标实测值与合格判定, 禁用"大概/差不多"等模糊词)'
                  value={qcConclusion}
                  onInput={e => setQcConclusion(e.detail.value)}
                />
              )}
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

      {/* ============ 工段码(P2 印刷) ============ */}
      {tab === 'codes' && !loading && (
        <>
          <View className={styles.section}>
            <View className={styles.sectionTitle}>工段二维码印刷</View>
            <View className={styles.stageMeta}>
              每工段一张打卡码, 塑封后张贴于工段现场{'\n'}
              责任人扫码 → 权限自动校验 → 打卡即签名
            </View>
          </View>
          {stageQrs.map(q => (
            <View className={styles.section} key={q.stageCode}>
              <View className={styles.stageHead}>
                <Text className={styles.stageName}>
                  {q.seq}. {q.printTitle}
                </Text>
              </View>
              <View className={styles.qrCard}>
                <Canvas canvasId={`stageQr${q.seq}`}
                  className={styles.qrCanvas} />
                <View className={styles.qrPayload}>{q.payload}</View>
                <View className={styles.stageMeta}>
                  {q.desc}
                </View>
                {(q.paramsTemplate || []).filter(t => t.required)
                  .map(t => t.label).length > 0 && (
                  <View className={styles.stageMeta}>
                    打卡必填: {
                      (q.paramsTemplate || [])
                        .filter(t => t.required)
                        .map(t => t.label).join('、')}
                  </View>
                )}
              </View>
              <View className={styles.btnRow}>
                <View className={styles.primaryBtn}
                  onClick={() => handleSaveStageQr(q.seq, q.printTitle)}>
                  保存到相册(送印)
                </View>
              </View>
            </View>
          ))}
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

      {/* ============ 管理驾驶舱(P3, 仅管理员) ============ */}
      {tab === 'admin' && isAdmin && (
        <>
          {adminStats && (
            <View className={styles.section}>
              <View className={styles.sectionTitle}>溯源统计驾驶舱</View>
              <View className={styles.statGrid}>
                <View className={styles.statCell}>
                  <View className={styles.statNum}>{adminStats.batchTotal}</View>
                  <View className={styles.statLabel}>批次总数</View>
                </View>
                <View className={styles.statCell}>
                  <View className={styles.statNum}>{adminStats.punchTotal}</View>
                  <View className={styles.statLabel}>打卡总数</View>
                </View>
                <View className={styles.statCell}>
                  <View className={`${styles.statNum} ${
                    adminStats.anomalyTotal > 0 ? styles.statDanger : ''
                  }`}>{adminStats.anomalyTotal}</View>
                  <View className={styles.statLabel}>AI 异常</View>
                </View>
                <View className={styles.statCell}>
                  <View className={styles.statNum}>
                    {adminStats.avgHealthScore}
                  </View>
                  <View className={styles.statLabel}>平均健康度</View>
                </View>
              </View>
              <View className={styles.stageMeta}>
                生产中 {adminStats.batchByStatus?.producing || 0} ·
                已出库 {adminStats.batchByStatus?.released || 0} ·
                阻断中 {adminStats.batchByStatus?.blocked || 0}
              </View>
            </View>
          )}

          {batches.filter(b => b.status === 'blocked').length > 0 && (
            <View className={styles.section}>
              <View className={styles.sectionTitle}>
                质检阻断批次 · 待处置
              </View>
              {batches.filter(b => b.status === 'blocked').map(b => (
                <View className={styles.batchCard} key={b.batchNo}>
                  <View className={styles.batchHead}>
                    <Text className={styles.batchNo}>{b.batchNo}</Text>
                    <Text className={`${styles.tag} ${styles.tagBlocked}`}>
                      阻断
                    </Text>
                  </View>
                  {b.blockedReason ? (
                    <View className={styles.batchMeta}>⚠ {b.blockedReason}</View>
                  ) : null}
                  <View className={styles.punchBtn}
                    onClick={() => handleUnblock(b.batchNo)}>
                解除阻断(须复检确认)
                  </View>
                </View>
              ))}
            </View>
          )}

          <View className={styles.section}>
            <View className={styles.sectionTitle}>
              AI 异常事件流({adminAnoms.length})
            </View>
            {adminAnoms.length === 0 && (
              <View className={styles.empty}>暂无异常打卡 ✓</View>
            )}
            {adminAnoms.map(a => (
              <View className={styles.batchCard} key={a.punchId}>
                <View className={styles.batchHead}>
                  <Text className={styles.batchNo}>{a.batchNo}</Text>
                  <Text className={`${styles.tag} ${styles.tagBlocked}`}>
                    {a.anomalies.map(x => ANOMALY_NAME[x] || x).join('、')}
                  </Text>
                </View>
                <View className={styles.batchMeta}>
                  {stages.find(s => s.code === a.stageCode)?.name
                    || a.stageCode} ·
                  {a.memberNickname}(#{a.memberId}) ·
                  {a.punchedAt?.slice(0, 19).replace('T', ' ')}
                </View>
              </View>
            ))}
            <View className={styles.stageMeta}>
              异常打卡已回流 33 号权限模块, 纳入责任人信用分考核
            </View>
          </View>
        </>
      )}
    </View>
  );
};

export default TracePunchPage;
