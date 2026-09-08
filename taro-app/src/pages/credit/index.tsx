/**
 * 信用先享后付 · 额度总览 + 先享后付订单(下单/还款) + 信用流水
 * 数据来源: 后端 /api/credit/*(AI 智能授信审批, L3 起可用)
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  CreditAPI, PaylaterQuotaVO, PaylaterOrderVO, CreditLogVO,
  paylaterStatusName, logTypeName, LEVEL_GATE, LEVEL_MIN_SCORE,
} from '@/api/credit';
import { requireLogin } from '@/services/auth-service';

// 状态 → 徽标样式
const STATUS_CLS: Record<string, string> = {
  review: 'review',
  active: 'active',
  repaid: 'repaid',
  rejected: 'rejected',
  overdue: 'overdue',
};

const formatTime = (t?: string): string => {
  if (!t) return '';
  return t.slice(0, 16).replace('T', ' ');
};

const formatDate = (t?: string): string => {
  if (!t) return '';
  return t.slice(0, 10);
};

const CreditPage: React.FC = () => {
  const [quota, setQuota] = useState<PaylaterQuotaVO | null>(null);
  const [orders, setOrders] = useState<PaylaterOrderVO[]>([]);
  const [logs, setLogs] = useState<CreditLogVO[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  // 下单面板
  const [orderPanel, setOrderPanel] = useState(false);
  const [amountInput, setAmountInput] = useState('');

  const loadData = useCallback(async () => {
    try {
      const [q, os, ls] = await Promise.all([
        CreditAPI.quota().catch(() => null),
        CreditAPI.paylaterOrders().catch(() => [] as PaylaterOrderVO[]),
        CreditAPI.logs(20).catch(() => [] as CreditLogVO[]),
      ]);
      setQuota(q);
      setOrders(os);
      setLogs(ls);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (requireLogin()) {
      loadData();
    } else {
      setLoading(false);
    }
  }, [loadData]);

  // 等级是否达到先享后付门槛(L3+)
  const levelNum = quota ? Number(quota.creditLevel.replace('L', '')) || 1 : 1;
  const gatePassed = levelNum >= LEVEL_GATE;
  // 距下一等级还差多少分
  const nextLevel = `L${Math.min(5, levelNum + 1)}`;
  const scoreGap = quota ? LEVEL_MIN_SCORE[nextLevel] - quota.bambooScore : 0;

  // 打开下单面板
  const openOrderPanel = () => {
    if (!gatePassed) {
      Taro.showModal({
        title: '暂不可用',
        content: `先享后付需信用等级 L3+(信用分 ≥550)。当前 ${quota?.creditLevel}(竹信分 ${quota?.bambooScore})。良好购物与按时还款行为可提升信用分。`,
        showCancel: false,
      });
      return;
    }
    setAmountInput('');
    setOrderPanel(true);
  };

  // 创建先享后付订单
  const handleCreateOrder = async () => {
    if (submitting) return;
    const amount = Number(amountInput);
    if (!amount || amount <= 0) {
      Taro.showToast({ title: '请输入订单金额', icon: 'none' });
      return;
    }
    if (quota && amount > quota.availableQuota) {
      Taro.showToast({ title: `超出可用额度 ¥${quota.availableQuota}`, icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const order = await CreditAPI.createPaylaterOrder({ amount });
      const statusText = order.status === 'active'
        ? '已自动通过, 额度已占用'
        : order.status === 'review' ? '已提交, 等待人工审批' : '已提交';
      Taro.showToast({ title: statusText, icon: 'none', duration: 2500 });
      setOrderPanel(false);
      await loadData();
    } catch (e) {
      console.warn('[credit] 下单失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 还款
  const handleRepay = async (order: PaylaterOrderVO) => {
    if (submitting) return;
    const res = await Taro.showModal({
      title: '确认还款',
      content: `订单 ${order.orderNo} 金额 ¥${order.amount.toFixed(2)}, 将从钱包余额还款并恢复额度。`,
    });
    if (!res.confirm) return;
    setSubmitting(true);
    try {
      const r = await CreditAPI.repay(order.id, 'wallet');
      const overdueText = r.overdueDays > 0
        ? `(逾期 ${r.overdueDays} 天, 费用 ¥${(r.overdueFees + r.penaltyFees).toFixed(2)})`
        : '(免息期内)';
      Taro.showToast({ title: `还款 ¥${r.repayTotal.toFixed(2)} 成功${overdueText}`, icon: 'none', duration: 2500 });
      await loadData();
    } catch (e) {
      console.warn('[credit] 还款失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 额度进度百分比
  const usedPercent = quota && quota.totalQuota > 0
    ? Math.min(100, Math.round((quota.usedQuota / quota.totalQuota) * 100))
    : 0;

  return (
    <View className={styles.page}>
      <NavBar title="信用先享后付" />
      <ScrollView scrollY className={styles.scrollView}>
        {/* 额度总览 */}
        {loading ? (
          <View className={styles.empty}>加载中...</View>
        ) : quota ? (
          <View className={styles.heroCard}>
            <View className={styles.heroTop}>
              <View className={styles.heroLevel}>{quota.creditLevel}</View>
              <View className={styles.heroInfo}>
                <View className={styles.heroLabel}>竹信分</View>
                <View className={styles.heroScore}>{quota.bambooScore}</View>
              </View>
              <View className={styles.heroInfo}>
                <View className={styles.heroLabel}>免息期</View>
                <View className={styles.heroScore}>{quota.interestFreeDays} 天</View>
              </View>
            </View>
            <View className={styles.heroAvailable}>
              <View className={styles.heroAvailLabel}>可用额度(元)</View>
              <View className={styles.heroAvailValue}>{quota.availableQuota.toFixed(2)}</View>
            </View>
            <View className={styles.quotaBar}>
              <View className={styles.quotaFill} style={{ width: `${usedPercent}%` }} />
            </View>
            <View className={styles.quotaMeta}>
              <Text>总额度 ¥{quota.totalQuota.toFixed(2)}</Text>
              <Text>已用 ¥{quota.usedQuota.toFixed(2)}</Text>
            </View>
            {!gatePassed && (
              <View className={styles.gateTip}>
                {scoreGap > 0
                  ? `先享后付需 L3(≥550 分), 再获得 ${scoreGap} 分即可解锁`
                  : '先享后付需 L3(≥550 分), 保持良好信用行为即可解锁'}
              </View>
            )}
            {gatePassed && (
              <View className={styles.heroBtn} onClick={openOrderPanel}>
                ＋ 先享后付下单
              </View>
            )}
          </View>
        ) : (
          <View className={styles.empty}>信用账户加载失败</View>
        )}

        {/* 先享后付订单 */}
        <View className={styles.card}>
          <View className={styles.cardTitle}>先享后付订单</View>
          {orders.length === 0 ? (
            <View className={styles.empty}>
              <View className={styles.emptyIcon}>💳</View>
              <View>暂无先享后付订单</View>
            </View>
          ) : (
            orders.map(o => (
              <View key={o.id} className={styles.orderRow}>
                <View className={styles.orderLeft}>
                  <View className={styles.orderNo}>{o.orderNo}</View>
                  <View className={styles.orderMeta}>
                    ¥{o.amount.toFixed(2)} · {formatDate(o.createdAt)}
                    {o.dueDate && o.status === 'active' && ` · 到期 ${formatDate(o.dueDate)}`}
                    {o.status === 'overdue' && o.overdueDays != null && ` · 逾期 ${o.overdueDays} 天`}
                  </View>
                  {o.riskFlags.length > 0 && (
                    <View className={styles.riskFlag}>{o.riskFlags.join(' · ')}</View>
                  )}
                </View>
                <View className={styles.orderRight}>
                  <View className={`${styles.badge} ${styles[STATUS_CLS[o.status] || 'rejected']}`}>
                    {paylaterStatusName(o.status)}
                  </View>
                  {(o.status === 'active' || o.status === 'overdue') && (
                    <View className={styles.repayBtn} onClick={() => handleRepay(o)}>
                      还款
                    </View>
                  )}
                </View>
              </View>
            ))
          )}
        </View>

        {/* 信用流水 */}
        <View className={styles.card}>
          <View className={styles.cardTitle}>信用流水</View>
          {logs.length === 0 ? (
            <View className={styles.empty}>暂无信用记录</View>
          ) : (
            logs.map(l => (
              <View key={l.id} className={styles.logRow}>
                <View className={styles.logLeft}>
                  <View className={styles.logType}>{logTypeName(l.type)}</View>
                  <View className={styles.logReason}>
                    {l.reason || (l.levelBefore !== l.levelAfter ? `${l.levelBefore} → ${l.levelAfter}` : '')}
                  </View>
                  <View className={styles.logTime}>{formatTime(l.createdAt)}</View>
                </View>
                <View className={`${styles.logDelta} ${l.delta >= 0 ? styles.logPlus : styles.logMinus}`}>
                  {l.delta >= 0 ? `+${l.delta}` : l.delta}
                </View>
              </View>
            ))
          )}
        </View>

        {/* 规则说明 */}
        <View className={styles.noteCard}>
          <View className={styles.noteTitle}>先享后付规则</View>
          <View className={styles.noteLine}>· 信用等级 L3 起可用, 免息期 15-45 天(按等级)</View>
          <View className={styles.noteLine}>· 下单经 AI 智能授信: 自动通过 / 转人工审批 / 自动拒绝</View>
          <View className={styles.noteLine}>· 逾期费用日率 0.035%, 逾期 7 天起罚息 0.1%/日</View>
          <View className={styles.noteLine}>· 逾期将扣除信用分 20 分/次, 影响等级</View>
        </View>
        <View className={styles.bottomSpacer} />
      </ScrollView>

      {/* 下单弹层 */}
      {orderPanel && quota && (
        <View className={styles.mask} onClick={() => setOrderPanel(false)}>
          <View className={styles.panel} onClick={(e) => e.stopPropagation()}>
            <View className={styles.panelTitle}>先享后付下单</View>
            <View className={styles.panelQuota}>
              可用额度 ¥{quota.availableQuota.toFixed(2)} · 免息期 {quota.interestFreeDays} 天
            </View>
            <View className={styles.amountRow}>
              <Text className={styles.amountSymbol}>¥</Text>
              <Input
                className={styles.amountInput}
                type="digit"
                value={amountInput}
                onInput={(e) => setAmountInput(e.detail.value)}
                placeholder="订单金额"
                placeholderClass={styles.placeholder}
                focus
              />
            </View>
            <View className={styles.panelNote}>
              下单后由 AI 智能授信审批: 小额低风险自动通过, 大额转人工审批
            </View>
            <View className={styles.panelBtn} onClick={handleCreateOrder}>
              {submitting ? '提交中...' : '提交订单'}
            </View>
          </View>
        </View>
      )}
    </View>
  );
};

export default CreditPage;
