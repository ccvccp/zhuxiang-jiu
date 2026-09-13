/**
 * 62号·AI智能无形资产估值模型 · 前端管理工作台(资产估值只读观测)
 * 四页签: 状态(第37档案) → 看板(四区) → 资产(九域) → 评估(版本链)
 * 口径: 观测面不受 AV62_MODE 影响 · 决策面操作经管理端 API(off 态 409)
 * 所有估值数字 100% 来自 /api/av62/* 确定性引擎(前端只渲染)
 */
import React, { useState } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import { Av62API, roleName, tierName } from '@/api/av62';

type Tab = 'status' | 'board' | 'assets' | 'assess';

const TABS: { key: Tab; label: string }[] = [
  { key: 'status', label: '状态' },
  { key: 'board', label: '看板' },
  { key: 'assets', label: '资产' },
  { key: 'assess', label: '评估' },
];

/** 0-1 比率 → 百分比文案(纯格式化) */
const pctStr = (v: any): string =>
  v == null ? '—' : `${(Number(v) * 100).toFixed(1)}%`;

const errMsg = (e: any): string =>
  String(e?.message || e).slice(0, 30);

/** 分布对象 → "k:v · k:v" 行文案 */
const distStr = (d: any): string =>
  !d || !Object.keys(d).length
    ? '—'
    : Object.entries(d).map(([k, v]) => `${k}:${v}`).join(' · ');

const AssetPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('status');

  // ============ 状态页 ============
  const [status, setStatus] = useState<any>(null);
  const loadStatus = async () => {
    try {
      setStatus(await Av62API.status());
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };

  // ============ 看板页 ============
  const [board, setBoard] = useState<any>(null);
  const loadBoard = async () => {
    try {
      setBoard(await Av62API.dashboard());
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };

  // ============ 资产页 ============
  const [assetData, setAssetData] = useState<any>(null);
  const [roleFilter, setRoleFilter] = useState('');
  const loadAssets = async (role = roleFilter) => {
    try {
      setAssetData(await Av62API.assets(
        role ? { role } : undefined));
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };
  const pickRole = (r: string) => {
    setRoleFilter(r);
    loadAssets(r);
  };

  // ============ 评估页 ============
  const [assessData, setAssessData] = useState<any>(null);
  const loadAssess = async () => {
    try {
      setAssessData(await Av62API.assessments(50));
    } catch (e: any) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };

  const s = (status?.status) || {};
  const zones = board?.zones || {};
  const assets = assetData?.assets || [];
  const assesses = assessData?.assessments || [];

  return (
    <View className={styles.page}>
      <NavBar title="62号·无形资产估值" />

      <ScrollView scrollY className={styles.body}>
        <View className={styles.section}>
          <View className={styles.heroCard}>
            <View className={styles.heroTitle}>62号·AI智能无形资产估值模型</View>
            <View className={styles.heroSub}>
              信任要素登记 · 因果估值 · 版本链 · 红队七向量
            </View>
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

        {/* ============ 状态页(第37档案) ============ */}
        {tab === 'status' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>模型状态(第37档案 · 八因子)</View>
            <View className={styles.runBtn} onClick={loadStatus}>刷新模型状态</View>
            {status ? (
              <View className={styles.resultCard}>
                <View className={styles.statGrid}>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>{s.mode || 'off'}</View>
                    <View className={styles.statLbl}>当前档位</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>{s.activeVersion || '—'}</View>
                    <View className={styles.statLbl}>生效权重版本</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>{s.scorerId || '—'}</View>
                    <View className={styles.statLbl}>治理档案</View>
                  </View>
                </View>
                <View className={styles.resItem}>
                  决策域: {(s.decisions || []).join(' / ') || '—'}
                </View>
                <View className={styles.factorGrid}>
                  {Object.entries(s.factorsMeta || {}).map(([k, v]) => (
                    <View key={k} className={styles.factorCell}>
                      <View className={styles.factorName}>{String(v)}</View>
                      <View className={styles.factorKey}>{k}</View>
                    </View>
                  ))}
                </View>
              </View>
            ) : <View className={styles.empty}>点击刷新加载</View>}
          </View>
        )}

        {/* ============ 看板页(四区) ============ */}
        {tab === 'board' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>四区看板(度量/资产/评估/防御)</View>
            <View className={styles.runBtn} onClick={loadBoard}>刷新四区看板</View>
            {board ? (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  度量区 · mode={board.mode}
                </View>
                <View className={styles.statGrid}>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      {pctStr(zones.metrics?.valuationAccuracy)}
                    </View>
                    <View className={styles.statLbl}>估值准确率</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      {pctStr(zones.metrics?.attributionGrounded)}
                    </View>
                    <View className={styles.statLbl}>归因锚定率</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      {zones.metrics?.fairness?.compliant ? '达标' : '观察'}
                    </View>
                    <View className={styles.statLbl}>公平态势</View>
                  </View>
                  <View className={styles.statCell}>
                    <View className={styles.statNum}>
                      {zones.metrics?.scorer?.trustScore != null
                        ? Number(zones.metrics.scorer.trustScore).toFixed(2)
                        : '—'}
                    </View>
                    <View className={styles.statLbl}>档案信任分</View>
                  </View>
                </View>

                <View className={styles.resHead}>资产区(三角色 × 九域)</View>
                <View className={styles.resItem}>
                  资产总数 {zones.assets?.total || 0} · 负资产{' '}
                  {zones.assets?.negativeCount || 0} 项
                </View>
                <View className={styles.resItem}>
                  角色分布: {distStr(zones.assets?.byRole)}
                </View>
                <View className={styles.resItem}>
                  域分布: {distStr(zones.assets?.byDomain)}
                </View>
                <View className={styles.resItem}>
                  流动性档: {distStr(zones.assets?.byLiquidity)}
                </View>

                <View className={styles.resHead}>评估区(版本链 · 置信度)</View>
                <View className={styles.resItem}>
                  评估总数 {zones.assessments?.total || 0} · 最大版本链 v
                  {zones.assessments?.maxVersionChain || 0}
                </View>
                <View className={styles.resItem}>
                  置信档: {distStr(zones.assessments?.byConfidence)}
                </View>

                <View className={styles.resHead}>防御区(红队七向量)</View>
                <View className={styles.resItem}>
                  红队 {zones.defense?.redteamRuns || 0} 轮 · 最近一轮{' '}
                  {zones.defense?.redteamLatest?.allDefended
                    ? '全防御' : '未执行/有失守'}
                </View>
              </View>
            ) : <View className={styles.empty}>点击刷新加载</View>}
          </View>
        )}

        {/* ============ 资产页 ============ */}
        {tab === 'assets' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>资产列表(三角色 × 九域 + 负资产)</View>
            <View className={styles.chipRow}>
              {['', 'enterprise', 'organization', 'personal'].map(r => (
                <View
                  key={r || 'all'}
                  className={`${styles.chip} ${roleFilter === r ? styles.chipActive : ''}`}
                  onClick={() => pickRole(r)}
                >
                  {r ? roleName(r) : '全部'}
                </View>
              ))}
            </View>
            <View className={styles.runBtn} onClick={() => loadAssets()}>刷新资产列表</View>
            {assetData ? (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>
                  共 {assetData.total || 0} 项 · 负资产 {assetData.negative || 0} 项
                </View>
                {assets.map(a => (
                  <View key={a.assetId} className={styles.assetRow}>
                    <View className={styles.assetHead}>
                      <Text className={styles.assetId}>#{a.assetId}</Text>
                      <Text className={styles.assetLabel}>
                        {a.label || a.domain}
                      </Text>
                      {a.negative
                        ? <Text className={styles.negBadge}>负</Text>
                        : null}
                    </View>
                    <View className={styles.assetMeta}>
                      主体 {a.subjectId} · {roleName(a.role)} · {a.domain}
                      · {a.status}
                    </View>
                  </View>
                ))}
                {!assets.length && <View className={styles.empty}>暂无资产</View>}
              </View>
            ) : <View className={styles.empty}>点击刷新加载</View>}
          </View>
        )}

        {/* ============ 评估页 ============ */}
        {tab === 'assess' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>评估流水(因果估值 · 净贡献)</View>
            <View className={styles.runBtn} onClick={loadAssess}>刷新评估流水</View>
            {assessData ? (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>
                  共 {assessData.total || 0} 条(版本链倒序)
                </View>
                {assesses.map(a => (
                  <View key={a.assessId} className={styles.assetRow}>
                    <View className={styles.assetHead}>
                      <Text className={styles.assetId}>评估#{a.assessId}</Text>
                      <Text className={styles.assetLabel}>
                        {a.domain}
                      </Text>
                      <Text
                        className={`${styles.tierBadge} ${
                          a.confidenceTier === 'high' ? styles.tierHigh
                            : a.confidenceTier === 'medium' ? styles.tierMid
                              : styles.tierLow}`}
                      >
                        {tierName(a.confidenceTier)}
                      </Text>
                    </View>
                    <View className={styles.assetMeta}>
                      资产#{a.assetId} · v{a.version} · 要素{' '}
                      {a.elementScore} · 净贡献 {a.netContribution}
                    </View>
                    <View className={styles.assetMeta}>
                      基准值 {a.baseValue} · 归因{' '}
                      {a.ruleId || '—'}
                    </View>
                  </View>
                ))}
                {!assesses.length && <View className={styles.empty}>暂无评估</View>}
              </View>
            ) : <View className={styles.empty}>点击刷新加载</View>}
          </View>
        )}

        <View className={styles.footNote}>
          观测面不受 AV62_MODE 影响 · 决策面(登记/估值/压力/校准)经
          管理 API 调用(off 态 409) · 红队七向量: POST /api/av62/redteam
        </View>
        <View className={styles.bottomSpace} />
      </ScrollView>
    </View>
  );
};

export default AssetPage;
