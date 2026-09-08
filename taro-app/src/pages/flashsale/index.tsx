import React, { useState, useEffect, useRef, useCallback } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import { FlashAPI, FlashSessionVO, FlashItemVO, FlashOrderVO } from '@/api/flashsale';
import { countdownText, phaseOf, buyButtonState } from './countdown';
import { requireLogin } from '@/services/auth-service';

/**
 * 限时秒杀页 · 对接 /api/flash/*
 * 场次列表(运行态) → 场次卡(秒杀价/进度/限购) → 倒计时/马上抢 → 我的秒杀订单
 */
const FlashsalePage: React.FC = () => {
  const [sessions, setSessions] = useState<FlashSessionVO[]>([]);
  const [activeSession, setActiveSession] = useState<FlashSessionVO | null>(null);
  const [myOrders, setMyOrders] = useState<FlashOrderVO[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [tick, setTick] = useState(0);   // 倒计时驱动
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const list = await FlashAPI.sessions();
      setSessions(list);
      // 默认选中: 进行中优先, 否则第一个
      const inProgress = list.find(s => phaseOf(s.startTime, s.endTime) === 'inProgress');
      const target = inProgress || list[0] || null;
      if (target) {
        const detail = await FlashAPI.sessionDetail(target.sessionId).catch(() => target);
        setActiveSession(detail);
      } else {
        setActiveSession(null);
      }
    } catch (e) {
      console.warn('[flashsale] 场次加载失败:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadMyOrders = useCallback(async () => {
    try {
      const orders = await FlashAPI.myOrders();
      setMyOrders(orders);
    } catch (e) {
      // 未登录静默
    }
  }, []);

  useEffect(() => {
    loadData();
    loadMyOrders();
    // 每秒 tick 驱动倒计时
    timerRef.current = setInterval(() => setTick(t => t + 1), 1000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  const handleSelectSession = async (s: FlashSessionVO) => {
    setActiveSession(s);
    try {
      const detail = await FlashAPI.sessionDetail(s.sessionId);
      setActiveSession(detail);
    } catch (e) {
      console.warn('[flashsale] 场次详情加载失败:', e);
    }
  };

  // 抢购(防抖 + 提交锁; 409 分支逐条 toast 由 request 层统一弹 detail)
  const handleBuy = async (item: FlashItemVO) => {
    if (submitting) return;
    if (!requireLogin()) return;
    if (!activeSession) return;
    const phase = phaseOf(activeSession.startTime, activeSession.endTime);
    if (phase !== 'inProgress') return;
    if (item.remainingStock <= 0) {
      Taro.showToast({ title: '已售罄', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const order = await FlashAPI.purchase(activeSession.sessionId, item.itemId, 1);
      Taro.showToast({ title: `抢购成功 ¥${order.totalAmount}`, icon: 'success' });
      // 刷新详情(库存/进度)与我的订单
      const detail = await FlashAPI.sessionDetail(activeSession.sessionId).catch(() => null);
      if (detail) setActiveSession(detail);
      loadMyOrders();
    } catch (e: any) {
      // 后端 409 detail 已由 request 层 toast; 此处兜底 console
      console.warn('[flashsale] 抢购失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  const handlePayOrder = async (order: FlashOrderVO) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await FlashAPI.pay(order.orderNo);
      Taro.showToast({ title: '支付成功', icon: 'success' });
      loadMyOrders();
    } catch (e) {
      console.warn('[flashsale] 支付失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancelOrder = async (order: FlashOrderVO) => {
    if (submitting) return;
    const res = await Taro.showModal({ title: '取消订单', content: '确定取消该秒杀订单吗?' });
    if (!res.confirm) return;
    setSubmitting(true);
    try {
      await FlashAPI.cancel(order.orderNo);
      Taro.showToast({ title: '已取消', icon: 'success' });
      loadMyOrders();
      // 库存回补刷新
      if (activeSession) {
        const detail = await FlashAPI.sessionDetail(activeSession.sessionId).catch(() => null);
        if (detail) setActiveSession(detail);
      }
    } catch (e) {
      console.warn('[flashsale] 取消失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  const ORDER_STATUS_NAME: Record<string, string> = {
    PENDING: '待支付', PAID: '已支付', CANCELLED: '已取消', EXPIRED: '已超时',
  };

  return (
    <View className={styles.page}>
      <NavBar title="限时秒杀" />
      <ScrollView scrollY className={styles.scrollView}>
        {/* 场次横滑 */}
        {loading ? (
          <View className={styles.empty}>加载中...</View>
        ) : sessions.length === 0 ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>⚡</View>
            <View>暂无秒杀场次</View>
            <View className={styles.emptySub}>敬请期待精彩活动</View>
          </View>
        ) : (
          <>
            <ScrollView scrollX className={styles.sessionBar}>
              {sessions.map(s => {
                const phase = phaseOf(s.startTime, s.endTime);
                return (
                  <View
                    key={s.sessionId}
                    className={`${styles.sessionTab} ${activeSession?.sessionId === s.sessionId ? styles.sessionTabActive : ''}`}
                    onClick={() => handleSelectSession(s)}
                  >
                    <View className={styles.sessionTabName}>{s.name}</View>
                    <View className={styles.sessionTabPhase}>
                      {phase === 'upcoming' ? `开抢倒计时 ${countdownText(s.startTime)}` : (s.runtimeStatusName || '')}
                    </View>
                  </View>
                );
              })}
            </ScrollView>

            {/* 场次卡 */}
            {activeSession && (
              <View className={styles.sessionCard}>
                <View className={styles.sessionHeader}>
                  <View className={styles.sessionName}>{activeSession.name}</View>
                  <View className={`${styles.phaseBadge} ${styles[`phase_${phaseOf(activeSession.startTime, activeSession.endTime)}`] || ''}`}>
                    {phaseOf(activeSession.startTime, activeSession.endTime) === 'upcoming'
                      ? `倒计时 ${countdownText(activeSession.startTime)}`
                      : (activeSession.runtimeStatusName || '')}
                  </View>
                </View>
                {(activeSession.items || []).length === 0 ? (
                  <View className={styles.itemEmpty}>本场次暂无秒杀商品</View>
                ) : (
                  (activeSession.items || []).map(item => {
                    const phase = phaseOf(activeSession.startTime, activeSession.endTime);
                    const btn = buyButtonState(phase, item.remainingStock);
                    return (
                      <View key={item.itemId} className={styles.itemCard}>
                        <View className={styles.itemIcon}>🍶</View>
                        <View className={styles.itemBody}>
                          <View className={styles.itemName}>{item.productName}</View>
                          <View className={styles.itemPriceRow}>
                            <Text className={styles.flashPrice}>¥{item.flashPrice}</Text>
                            <Text className={styles.originalPrice}>¥{item.originalPrice}</Text>
                          </View>
                          <View className={styles.stockRow}>
                            <View className={styles.progressBar}>
                              <View className={styles.progressFill} style={{ width: `${Math.min(100, item.progressPercent)}%` }} />
                            </View>
                            <Text className={styles.stockText}>已抢{item.progressPercent}% · 剩{item.remainingStock}件</Text>
                          </View>
                          <View className={styles.limitText}>每人限购 {item.limitPerMember} 件</View>
                        </View>
                        <View
                          className={`${styles.buyBtn} ${btn.disabled ? styles.buyBtnDisabled : ''}`}
                          onClick={() => { if (!btn.disabled) handleBuy(item); }}
                        >
                          {btn.label}
                        </View>
                      </View>
                    );
                  })
                )}
              </View>
            )}

            {/* 我的秒杀订单 */}
            <View className={styles.ordersCard}>
              <View className={styles.cardTitle}>我的秒杀订单</View>
              {myOrders.length === 0 ? (
                <View className={styles.orderEmpty}>暂无秒杀订单</View>
              ) : (
                myOrders.map(o => (
                  <View key={o.orderNo} className={styles.orderItem}>
                    <View className={styles.orderLeft}>
                      <View className={styles.orderName}>{o.productName}</View>
                      <View className={styles.orderMeta}>×{o.quantity} · ¥{o.totalAmount}</View>
                      <View className={styles.orderNoText}>{o.orderNo}</View>
                    </View>
                    {o.status === 'PENDING' ? (
                      <View className={styles.orderActions}>
                        <Text className={styles.orderCancel} onClick={() => handleCancelOrder(o)}>取消</Text>
                        <Text className={styles.orderPay} onClick={() => handlePayOrder(o)}>支付</Text>
                      </View>
                    ) : (
                      <Text className={styles.orderStatus}>{ORDER_STATUS_NAME[o.status] || o.status}</Text>
                    )}
                  </View>
                ))
              )}
            </View>
          </>
        )}
        <View className={styles.bottomSpacer} />
      </ScrollView>
    </View>
  );
};

export default FlashsalePage;
