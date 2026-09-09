/**
 * 代理商中心 · S/A/B/C/D 五级代理体系
 * 等级权益(进货折扣) / 返利档位(T0-T3 超额累进) / 申请入驻 / 代理商名录
 * 数据来源: 后端 /api/agent/*
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input, Picker } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  AgentAPI, AgentLevelVO, RebateTierVO, AgentVO, agentLevelName,
} from '@/api/agent';
import { requireLogin } from '@/services/auth-service';

type Tab = 'levels' | 'apply' | 'list';

const TABS: { key: Tab; label: string }[] = [
  { key: 'levels', label: '等级与返利' },
  { key: 'apply', label: '申请入驻' },
  { key: 'list', label: '代理商名录' },
];

// 申请等级选项
const LEVEL_OPTIONS = ['D', 'C', 'B', 'A', 'S'];

const AgentCenterPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('levels');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  // 等级与返利
  const [levels, setLevels] = useState<AgentLevelVO[]>([]);
  const [rebateTiers, setRebateTiers] = useState<RebateTierVO[]>([]);
  // 申请表单
  const [companyName, setCompanyName] = useState('');
  const [contactName, setContactName] = useState('');
  const [contactPhone, setContactPhone] = useState('');
  const [region, setRegion] = useState('');
  const [levelIdx, setLevelIdx] = useState(0);
  // 名录
  const [agents, setAgents] = useState<AgentVO[]>([]);
  // 我的申请单号(本地记录)
  const myApplyId = AgentAPI.myApplyId();

  const loadLevels = useCallback(async () => {
    const [lv, tiers] = await Promise.all([
      AgentAPI.levels().catch(() => [] as AgentLevelVO[]),
      AgentAPI.rebateTiers().catch(() => [] as RebateTierVO[]),
    ]);
    setLevels(lv);
    setRebateTiers(tiers);
  }, []);

  const loadAgents = useCallback(async () => {
    const r = await AgentAPI.list({ pageSize: 30 }).catch(() => ({ agents: [] as AgentVO[], total: 0 }));
    setAgents(r.agents);
  }, []);

  useEffect(() => {
    (async () => {
      await loadLevels();
      setLoading(false);
    })();
  }, [loadLevels]);

  // 提交申请
  const handleApply = async () => {
    if (submitting) return;
    if (!companyName.trim()) {
      Taro.showToast({ title: '请输入公司名', icon: 'none' });
      return;
    }
    if (!contactName.trim()) {
      Taro.showToast({ title: '请输入联系人', icon: 'none' });
      return;
    }
    if (!contactPhone.trim()) {
      Taro.showToast({ title: '请输入联系电话', icon: 'none' });
      return;
    }
    if (!region.trim()) {
      Taro.showToast({ title: '请输入代理区域', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const { applyId } = await AgentAPI.apply({
        companyName: companyName.trim(),
        contactName: contactName.trim(),
        contactPhone: contactPhone.trim(),
        region: region.trim(),
        applyLevel: LEVEL_OPTIONS[levelIdx],
      });
      Taro.showModal({
        title: '申请已提交',
        content: `申请单号 ${applyId}, 等级 ${LEVEL_OPTIONS[levelIdx]}(${agentLevelName(LEVEL_OPTIONS[levelIdx])}), 等待平台审核。`,
        showCancel: false,
      });
      setCompanyName('');
      setContactName('');
      setContactPhone('');
      setRegion('');
      setLevelIdx(0);
    } catch (e) {
      console.warn('[agent] 申请提交失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <View className={styles.page}>
      <NavBar title="代理商中心" />
      {/* 顶部 Tabs */}
      <View className={styles.tabBar}>
        {TABS.map(t => (
          <View
            key={t.key}
            className={`${styles.tabItem} ${tab === t.key ? styles.tabItemActive : ''}`}
            onClick={async () => {
              setTab(t.key);
              if (t.key === 'list') await loadAgents();
            }}
          >
            {t.label}
          </View>
        ))}
      </View>

      <ScrollView scrollY className={styles.scrollView}>
        {loading ? (
          <View className={styles.empty}>加载中...</View>
        ) : tab === 'levels' ? (
          <>
            {/* 等级体系 */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>代理等级体系(S/A/B/C/D)</View>
              {levels.length === 0 ? (
                <View className={styles.empty}>等级体系加载失败</View>
              ) : (
                levels.map(l => (
                  <View key={l.level} className={styles.levelRow}>
                    <View className={styles.levelLeft}>
                      <View className={styles.levelName}>
                        <Text className={styles.levelBadge}>{l.level}</Text>
                        {l.name}
                      </View>
                      <View className={styles.levelRights}>{l.rights}</View>
                    </View>
                    <View className={styles.levelDiscount}>
                      {(l.discountRate * 100).toFixed(0)} 折
                    </View>
                  </View>
                ))
              )}
            </View>

            {/* 返利档位 */}
            {rebateTiers.length > 0 && (
              <View className={styles.card}>
                <View className={styles.cardTitle}>返利档位(超额累进制)</View>
                {rebateTiers.map(t => (
                  <View key={t.tier} className={styles.levelRow}>
                    <View className={styles.levelLeft}>
                      <View className={styles.levelName}>{t.tier} 档</View>
                      <View className={styles.levelRights}>
                        月进货 ¥{(t.minAmount / 10000).toFixed(0)} 万起
                        {t.maxAmount != null ? ` ~ ¥${(t.maxAmount / 10000).toFixed(0)} 万` : '以上'}
                      </View>
                    </View>
                    <View className={styles.levelDiscount}>
                      {(t.rate * 100).toFixed(1)}%
                    </View>
                  </View>
                ))}
              </View>
            )}

            {/* 我的申请提示 */}
            {myApplyId && (
              <View className={styles.noteCard}>
                <View className={styles.noteTitle}>我的申请</View>
                <View className={styles.noteLine}>申请单号 {myApplyId}(审核结果以平台通知为准)</View>
              </View>
            )}
          </>
        ) : tab === 'apply' ? (
          <View className={styles.card}>
            <View className={styles.cardTitle}>申请入驻</View>
            <View className={styles.sheetDesc}>
              五级代理体系 · 进货折扣 + 月度返利 · 区域保护
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={companyName}
                onInput={(e) => setCompanyName((e.detail as any).value)}
                placeholder="公司名称"
                placeholderClass={styles.placeholder}
                maxlength={40}
              />
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={contactName}
                onInput={(e) => setContactName((e.detail as any).value)}
                placeholder="联系人姓名"
                placeholderClass={styles.placeholder}
                maxlength={20}
              />
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                type="number"
                value={contactPhone}
                onInput={(e) => setContactPhone((e.detail as any).value)}
                placeholder="联系电话"
                placeholderClass={styles.placeholder}
                maxlength={15}
              />
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={region}
                onInput={(e) => setRegion((e.detail as any).value)}
                placeholder="代理区域(如 山东省济南市)"
                placeholderClass={styles.placeholder}
                maxlength={30}
              />
            </View>
            <Picker
              mode="selector"
              range={LEVEL_OPTIONS.map(l => `${l} 级(${agentLevelName(l)})`)}
              value={levelIdx}
              onChange={(e) => setLevelIdx(Number((e.detail as any).value))}
            >
              <View className={styles.pickerRow}>
                <Text className={styles.pickerLabel}>申请等级</Text>
                <Text className={styles.pickerValue}>
                  {LEVEL_OPTIONS[levelIdx]} 级({agentLevelName(LEVEL_OPTIONS[levelIdx])})
                </Text>
                <Text className={styles.pickerArrow}>›</Text>
              </View>
            </Picker>
            <View className={styles.sheetBtn} onClick={handleApply}>
              {submitting ? '提交中...' : '提交申请'}
            </View>
          </View>
        ) : (
          <View className={styles.card}>
            <View className={styles.cardTitle}>代理商名录</View>
            {agents.length === 0 ? (
              <View className={styles.empty}>
                <View className={styles.emptyIcon}>🏪</View>
                <View>暂无代理商信息</View>
              </View>
            ) : (
              agents.map(a => (
                <View key={a.agentId} className={styles.agentRow}>
                  <View className={styles.agentLeft}>
                    <View className={styles.agentName}>{a.companyName}</View>
                    <View className={styles.agentMeta}>
                      {a.region}{a.level ? ` · ${a.level} 级` : ''}
                      {a.totalPurchaseAmount > 0 ? ` · 累计进货 ¥${(a.totalPurchaseAmount / 10000).toFixed(1)} 万` : ''}
                    </View>
                  </View>
                  <View className={styles.agentBadge}>{agentLevelName(a.level) || a.level}</View>
                </View>
              ))
            )}
          </View>
        )}

        {/* 规则说明 */}
        <View className={styles.noteCard}>
          <View className={styles.noteTitle}>代理规则</View>
          <View className={styles.noteLine}>· S/A/B/C/D 五级体系, 进货折扣与权益逐级递增</View>
          <View className={styles.noteLine}>· 返利按月度进货额超额累进(T0-T3 档)</View>
          <View className={styles.noteLine}>· 区域保护: 同区域代理名额有限</View>
          <View className={styles.noteLine}>· 审核通过后建档, 钱包充值进货 · 返利可提现</View>
        </View>
        <View className={styles.bottomSpacer} />
      </ScrollView>
    </View>
  );
};

export default AgentCenterPage;
