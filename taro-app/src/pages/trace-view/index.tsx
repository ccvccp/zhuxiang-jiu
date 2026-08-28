/**
 * 产品溯源 · 消费者公开溯源页(P3)
 * 免登录: 输入批次号/扫瓶码 → 7 工段进度 + 脱敏时间线 + AI 健康度卡
 * 数据来源: 后端公开端 /api/trace-prod/public/*
 */
import React, { useState } from 'react';
import { View, Text, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import { TraceProdAPI, PublicTraceVO } from '@/api/traceProd';

// 7 工段静态元数据(与后端种子一致, 公开页免登录展示进度条)
const STAGE_META = [
  { seq: 1, name: '工艺酿酒', isQcGate: false },
  { seq: 2, name: '原酒储藏', isQcGate: false },
  { seq: 3, name: '产品(调配/检测)', isQcGate: true },
  { seq: 4, name: '灌装', isQcGate: false },
  { seq: 5, name: '包装', isQcGate: true },
  { seq: 6, name: '仓库', isQcGate: false },
  { seq: 7, name: '出库', isQcGate: false },
];

const STATUS_NAME: Record<string, string> = {
  producing: '生产中', released: '已出库', blocked: '质检阻断',
};
const ANOMALY_NAME: Record<string, string> = {
  skip_stage: '跳工段', time_backflow: '时间倒流',
  dwell_overdue: '超时滞留', qc_blocked: '质检阻断强闯',
};
// P4: 流通码前缀(瓶码 BLC / 箱顶码 TBC / 箱底码 BBC)
const CIRCULATION_PREFIXES = ['BLC-', 'TBC-', 'BBC-'];
const LIFE_STATUS_NAME: Record<string, string> = {
  pending: '待激活', active: '已激活', transferred: '已转让',
  recycled: '已回收', frozen: '已冻结',
};
const BOX_STATUS_NAME: Record<string, string> = {
  pending: '待绑定', bound: '已绑定', opened: '已开箱',
  recycled: '已回收',
};

const isCirculationCode = (s: string) =>
  CIRCULATION_PREFIXES.some(p => s.startsWith(p));

const TraceViewPage: React.FC = () => {
  const [batchNo, setBatchNo] = useState('');
  const [result, setResult] = useState<PublicTraceVO | null>(null);
  const [loading, setLoading] = useState(false);

  const doQuery = async (no: string) => {
    const target = no.trim();
    if (!target) {
      Taro.showToast({ title: '请输入批次号或瓶身码', icon: 'none' });
      return;
    }
    setLoading(true);
    try {
      // P4: 瓶码/箱码 → 流通贯通查询; 其余按批次号查询
      const res = isCirculationCode(target)
        ? await TraceProdAPI.publicTraceByCode(target)
        : await TraceProdAPI.publicTrace(target);
      setResult(res);
    } catch (e: any) {
      setResult(null);
      Taro.showToast({ title: e.message || '查询失败', icon: 'none' });
    } finally {
      setLoading(false);
    }
  };

  // 扫瓶码/批次码 → 溯源查询
  const handleScan = () => {
    Taro.scanCode({
      success: (res) => {
        const code = (res.result || '').trim();
        if (code.startsWith('ZXBJ-TRACE')) {
          Taro.showToast({ title: '这是工段打卡码, 请扫瓶身溯源码', icon: 'none' });
          return;
        }
        setBatchNo(code);
        doQuery(code);
      },
    });
  };

  const healthScore = result?.health.score ?? 0;
  const healthLevel = healthScore >= 90 ? '优' : healthScore >= 75 ? '良'
    : healthScore >= 60 ? '中' : '待改善';

  return (
    <View className={styles.page}>
      <View className={styles.header}>
        <View className={styles.headerTitle}>🔍 竹香酒溯源</View>
        <View className={styles.headerDesc}>
          一根鲜竹(笋) · 泰山姊妹山徂徕山{'\n'}
          国家森林公园一滴富硒水 · 鲜竹酝香成美酒{'\n'}
          链式哈希防篡改 · 责任人实名背书
        </View>
      </View>

      <View className={styles.searchCard}>
        <Input
          className={styles.input}
          placeholder='扫码/输入瓶身码(BLC)或批次号(如 ZX52-2026L08)'
          value={batchNo}
          onInput={e => setBatchNo(e.detail.value)}
        />
        <View className={styles.btnRow}>
          <View className={styles.primaryBtn} onClick={() => doQuery(batchNo)}>
            {loading ? '查询中...' : '查询溯源'}
          </View>
          <View className={styles.ghostBtn} onClick={handleScan}>扫码查询</View>
        </View>
      </View>

      {result && (
        <>
          {/* P4: 流通信息卡(扫瓶码/箱码时展示) */}
          {result.codeType && (
            <View className={styles.section}>
              <View className={styles.sectionTitle}>
                {result.codeType === 'life' ? '🍾 瓶身码验真' : '📦 箱码验真'}
              </View>
              <View className={styles.circCode}>{result.code}</View>
              {result.codeType === 'life' ? (
                <View className={styles.circMeta}>
                  流通状态: {LIFE_STATUS_NAME[result.lifeStatus || '']
                    || result.lifeStatus || '-'}
                  {result.firstActivationDate
                    ? ` · 首次激活: ${result.firstActivationDate}` : ''}
                  {'\n'}
                  {result.prodReleased
                    ? '✓ 该瓶出自已放行生产批次' : '⚠ 生产批次尚未出库放行'}
                </View>
              ) : (
                <View className={styles.circMeta}>
                  箱状态: {BOX_STATUS_NAME[result.boxStatus || '']
                    || result.boxStatus || '-'}
                  {result.agentRegion ? ` · 代理区域: ${result.agentRegion}` : ''}
                </View>
              )}
            </View>
          )}

          {/* 批次概要 */}
          <View className={styles.section}>
            <View className={styles.batchHead}>
              <Text className={styles.batchNo}>{result.batchNo}</Text>
              <Text className={`${styles.tag} ${
                result.status === 'released' ? styles.tagReleased
                  : result.status === 'blocked' ? styles.tagBlocked
                    : styles.tagActive
              }`}>{STATUS_NAME[result.status] || result.status}</Text>
            </View>
            <View className={styles.batchMeta}>
              计划产量 {result.plannedQty} 瓶 · 已流转 {
                STAGE_META.filter(s => s.seq <= result.currentStageSeq).length
              }/7 工段
            </View>
            {/* 7 工段进度条 */}
            <View className={styles.progressRow}>
              {STAGE_META.map(s => (
                <View key={s.seq}
                  className={`${styles.progressCell} ${
                    s.seq <= result.currentStageSeq ? styles.progressDone : ''
                  } ${s.isQcGate ? styles.progressGate : ''}`} />
              ))}
            </View>
            <View className={styles.progressLegend}>
              <Text>■ 已完成</Text>
              <Text>□ 未流转</Text>
              <Text className={styles.gateMark}>◆ 质检关卡</Text>
            </View>
          </View>

          {/* AI 健康度卡 */}
          <View className={styles.healthCard}>
            <View>
              <View className={styles.healthScore}>{healthScore}</View>
              <View className={styles.healthLabel}>AI 健康度 · {healthLevel}</View>
            </View>
            <View className={styles.healthFactors}>
              链完整 {result.health.factors.chainCompleteness}/40{'\n'}
              无异常 {result.health.factors.noAnomaly}/30{'\n'}
              时效 {result.health.factors.timeliness}/20{'\n'}
              质检 {result.health.factors.qcComplete}/10
              {result.health.anomalyCount > 0
                ? `\n检出异常 ${result.health.anomalyCount} 项` : ''}
            </View>
            <View className={`${styles.chainValid} ${
              result.chainValid ? '' : styles.tagBlocked
            }`}>
              {result.chainValid ? '链校验✓' : '链异常!'}
            </View>
          </View>

          {/* 生产时间线(脱敏) */}
          <View className={styles.section}>
            <View className={styles.sectionTitle}>
              生产时间线 · 责任人已实名背书
            </View>
            <View className={styles.timeline}>
              {STAGE_META.map((s, idx) => {
                const punch = result.timeline
                  .filter(t => t.stageSeq === s.seq)
                  .sort((a, b) => b.punchId - a.punchId)[0];
                const done = !!punch && punch.result === 'pass';
                const blocked = punch?.result === 'block';
                return (
                  <View className={styles.tlItem} key={s.seq}>
                    <View className={`${styles.tlDot} ${
                      blocked ? styles.tlDotBlock : done ? '' : styles.tlDotPending
                    }`}>
                      {blocked ? '✕' : done ? '✓' : idx + 1}
                    </View>
                    <View className={styles.tlBody}>
                      <View className={styles.tlHead}>
                        <Text className={styles.tlStage}>{s.name}</Text>
                        {s.isQcGate ? (
                          <Text className={`${styles.tlState} ${styles.tagQc}`}>
                            质检关卡
                          </Text>
                        ) : null}
                      </View>
                      {punch ? (
                        <>
                          <View className={styles.tlMeta}>
                            {punch.punchedAt?.slice(0, 19).replace('T', ' ')} ·
                            责任人: {punch.responsibleMasked || '**'}
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
                        <View className={styles.tlMeta}>待流转</View>
                      )}
                    </View>
                  </View>
                );
              })}
            </View>
          </View>

          <View className={styles.footerNote}>
            本溯源链由工段责任人逐段扫码签名生成,{'\n'}
            链式哈希校验通过即不可篡改, 可放心购买
          </View>
        </>
      )}
    </View>
  );
};

export default TraceViewPage;
