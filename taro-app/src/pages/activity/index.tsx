/**
 * 活动中心 · 活动列表/报名/取消
 * 数据来源: 后端 /api/activity/*
 * 我的报名状态: 本地存储记录(后端暂无用户端报名查询接口)
 */
import React, { useState, useEffect, useMemo } from 'react';
import { View, Text } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import { PromotionAPI, ActivityAPI, ActivityVO } from '@/api/promotion';
import { CURRENT_MEMBER_ID } from '@/config';

// 状态筛选 tab
const STATUS_TABS = [
  { key: 'all', label: '全部' },
  { key: 'registering', label: '报名中' },
  { key: 'ongoing', label: '进行中' },
  { key: 'ended', label: '已结束' },
];

// 状态显示名 + 样式
const STATUS_MAP: Record<string, { label: string; cls: string }> = {
  registering: { label: '报名中', cls: 'registering' },
  ongoing: { label: '进行中', cls: 'ongoing' },
  ended: { label: '已结束', cls: 'ended' },
  cancelled: { label: '已取消', cls: 'ended' },
  draft: { label: '筹备中', cls: 'ended' },
};

// 类型图标
const TYPE_ICON: Record<string, string> = {
  promotion: '🎁',
  lottery: '🎰',
  competition: '🏆',
  arena: '⚔️',
  interactive: '🎲',
  groupbuy: '🛒',
  seckill: '⚡',
  presale: '📅',
};

// 报名状态本地存储 key(按会员隔离)
const REG_STORAGE_KEY = `activity_reg_${CURRENT_MEMBER_ID}`;

const loadRegisteredIds = (): Set<string> => {
  const list = Taro.getStorageSync(REG_STORAGE_KEY) as string[] || [];
  return new Set(list.filter(id => id && !id.startsWith('mock-')));
};

const saveRegisteredIds = (ids: Set<string>) => {
  Taro.setStorageSync(REG_STORAGE_KEY, Array.from(ids));
};

const formatTime = (t?: string): string => {
  if (!t) return '';
  return t.slice(0, 16).replace('T', ' ');
};

const ActivityPage: React.FC = () => {
  const [activities, setActivities] = useState<ActivityVO[]>([]);
  const [filter, setFilter] = useState('all');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState<string>(''); // 正在操作的 activityId
  const [registeredIds, setRegisteredIds] = useState<Set<string>>(new Set());

  const loadData = async () => {
    try {
      const list = await PromotionAPI.activities({ limit: 50 });
      setActivities(list);
    } catch (e) {
      console.warn('[activity] 加载失败:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setRegisteredIds(loadRegisteredIds());
    loadData();
  }, []);

  // 按状态筛选
  const filtered = useMemo(() => {
    if (filter === 'all') return activities;
    return activities.filter(a => a.status === filter);
  }, [activities, filter]);

  // 统计数字
  const counts = useMemo(() => ({
    registering: activities.filter(a => a.status === 'registering').length,
    ongoing: activities.filter(a => a.status === 'ongoing').length,
  }), [activities]);

  // 刷新单个活动的报名数
  const refreshCount = async (activityId: string) => {
    try {
      const s = await ActivityAPI.stats(activityId);
      setActivities(prev => prev.map(a =>
        a.id === activityId ? { ...a, registrationCount: s.registrationCount } : a
      ));
    } catch (_) { /* 静默 */ }
  };

  // 报名
  const handleRegister = async (activityId: string) => {
    if (submitting) return;
    setSubmitting(activityId);
    try {
      await ActivityAPI.register(activityId);
      const next = new Set(registeredIds);
      next.add(activityId);
      setRegisteredIds(next);
      saveRegisteredIds(next);
      Taro.showToast({ title: '报名成功', icon: 'success' });
      refreshCount(activityId);
    } catch (e) {
      console.warn('[activity] 报名失败:', e);
    } finally {
      setSubmitting('');
    }
  };

  // 取消报名
  const handleCancel = async (activityId: string) => {
    if (submitting) return;
    const res = await Taro.showModal({ title: '取消报名', content: '确定取消该活动报名吗?' });
    if (!res.confirm) return;
    setSubmitting(activityId);
    try {
      await ActivityAPI.cancelRegister(activityId);
      const next = new Set(registeredIds);
      next.delete(activityId);
      setRegisteredIds(next);
      saveRegisteredIds(next);
      Taro.showToast({ title: '已取消报名', icon: 'success' });
      refreshCount(activityId);
    } catch (e) {
      console.warn('[activity] 取消失败:', e);
    } finally {
      setSubmitting('');
    }
  };

  if (loading) {
    return (
      <View className={styles.page}>
        <View className={styles.empty}>
          <View className={styles.emptyIcon}>🎁</View>
          <View className={styles.emptyText}>加载中...</View>
        </View>
      </View>
    );
  }

  return (
    <View className={styles.page}>
      {/* 概览头部 */}
      <View className={styles.heroCard}>
        <View className={styles.heroTitle}>活动中心</View>
        <View className={styles.heroDesc}>品鉴会 · 团购专场 · 擂台赛,好活动不错过</View>
        <View className={styles.heroStats}>
          <View className={styles.heroStatItem}>
            <View className={styles.heroStatValue}>{counts.registering}</View>
            <View className={styles.heroStatLabel}>报名中</View>
          </View>
          <View className={styles.heroStatDivider} />
          <View className={styles.heroStatItem}>
            <View className={styles.heroStatValue}>{counts.ongoing}</View>
            <View className={styles.heroStatLabel}>进行中</View>
          </View>
          <View className={styles.heroStatDivider} />
          <View className={styles.heroStatItem}>
            <View className={styles.heroStatValue}>{registeredIds.size}</View>
            <View className={styles.heroStatLabel}>我的报名</View>
          </View>
        </View>
      </View>

      {/* 状态筛选 */}
      <View className={styles.tabs}>
        {STATUS_TABS.map(tab => (
          <View
            key={tab.key}
            className={`${styles.tab} ${filter === tab.key ? styles.tabActive : ''}`}
            onClick={() => setFilter(tab.key)}
          >
            {tab.label}
          </View>
        ))}
      </View>

      {/* 活动列表 */}
      {filtered.length === 0 ? (
        <View className={styles.empty}>
          <View className={styles.emptyIcon}>🎪</View>
          <View className={styles.emptyText}>暂无相关活动,敬请期待</View>
        </View>
      ) : (
        <View className={styles.list}>
          {filtered.map(act => {
            const st = STATUS_MAP[act.status] || { label: act.status, cls: 'ended' };
            const registered = registeredIds.has(act.id);
            const canRegister = act.status === 'registering';
            return (
              <View key={act.id} className={styles.card}>
                <View className={styles.cardHeader}>
                  <View className={styles.cardIcon}>
                    {TYPE_ICON[act.type] || '🎁'}
                  </View>
                  <View className={styles.cardTitleWrap}>
                    <View className={styles.cardTitle}>{act.name}</View>
                    {act.startTime ? (
                      <View className={styles.cardTime}>
                        {formatTime(act.startTime)}
                        {act.endTime ? ` ~ ${formatTime(act.endTime)}` : ''}
                      </View>
                    ) : null}
                  </View>
                  <View className={`${styles.badge} ${styles[st.cls]}`}>{st.label}</View>
                </View>

                {act.description ? (
                  <View className={styles.cardDesc}>{act.description}</View>
                ) : null}

                <View className={styles.cardFooter}>
                  <View className={styles.regCount}>
                    已报名 <Text className={styles.regCountNum}>{act.registrationCount || 0}</Text> 人
                    {registered ? <Text className={styles.regMark}> · 已报名</Text> : null}
                  </View>
                  {canRegister ? (
                    registered ? (
                      <View
                        className={styles.cancelBtn}
                        onClick={() => handleCancel(act.id)}
                      >
                        {submitting === act.id ? '处理中...' : '取消报名'}
                      </View>
                    ) : (
                      <View
                        className={styles.regBtn}
                        onClick={() => handleRegister(act.id)}
                      >
                        {submitting === act.id ? '报名中...' : '立即报名'}
                      </View>
                    )
                  ) : null}
                </View>
              </View>
            );
          })}
        </View>
      )}

      {/* 说明 */}
      <View className={styles.note}>报名后请在活动时间内参与 · 报名中/进行中的活动可取消</View>
    </View>
  );
};

export default ActivityPage;
