/**
 * 订单中心 · 订单列表(状态筛选) + 快捷操作
 * 数据来源: 后端 /api/order/my
 */
import React, { useState, useEffect, useMemo } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import styles from './index.module.scss';
import { OrderAPI, OrderVO } from '@/api/order';

// 状态筛选 tab(与后端状态码对齐)
const STATUS_TABS = [
  { key: '', label: '全部' },
  { key: 'PENDING', label: '待付款' },
  { key: 'PAID', label: '待发货' },
  { key: 'SHIPPED', label: '待收货' },
  { key: 'RECEIVED', label: '待评价' },
  { key: 'COMPLETED', label: '已完成' },
];

// 状态 → 徽标样式
const STATUS_CLS: Record<string, string> = {
  PENDING: 'pending',
  PAID: 'paid',
  SHIPPED: 'shipped',
  RECEIVED: 'received',
  COMPLETED: 'completed',
  CANCELLED: 'cancelled',
  RETURNING: 'cancelled',
};

const formatTime = (t: string): string => (t ? t.slice(0, 19).replace('T', ' ') : '');

const OrdersPage: React.FC = () => {
  const [orders, setOrders] = useState<OrderVO[]>([]);
  const [filter, setFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState<string>(''); // 正在操作的 orderId

  const loadData = async (status: string = filter) => {
    try {
      const list = await OrderAPI.myList(status || undefined);
      setOrders(list);
    } catch (e) {
      console.warn('[orders] 加载失败:', e);
    } finally {
      setLoading(false);
    }
  };

  // 首次加载 + 每次回到页面刷新(从详情页操作后返回能同步状态)
  useEffect(() => { loadData(); }, []);
  useDidShow(() => { loadData(); });

  // 切换状态筛选
  const handleTab = (key: string) => {
    if (filter === key) return;
    setFilter(key);
    setLoading(true);
    loadData(key);
  };

  // 刷新单条订单(操作后就地更新)
  const refreshOne = async (orderId: string) => {
    try {
      const updated = await OrderAPI.detailVO(orderId);
      setOrders(prev => prev.map(o => (o.orderId === orderId ? updated : o)));
    } catch (_) { /* 静默 */ }
  };

  // 去支付
  const handlePay = async (e, orderId: string) => {
    e.stopPropagation();
    if (submitting) return;
    setSubmitting(orderId);
    try {
      await OrderAPI.pay(orderId);
      Taro.showToast({ title: '支付成功', icon: 'success' });
      refreshOne(orderId);
    } catch (err) {
      console.warn('[orders] 支付失败:', err);
    } finally {
      setSubmitting('');
    }
  };

  // 取消订单
  const handleCancel = async (e, orderId: string) => {
    e.stopPropagation();
    if (submitting) return;
    const res = await Taro.showModal({ title: '取消订单', content: '确定取消该订单吗?已扣积分将退还' });
    if (!res.confirm) return;
    setSubmitting(orderId);
    try {
      await OrderAPI.cancel(orderId);
      Taro.showToast({ title: '已取消', icon: 'success' });
      refreshOne(orderId);
    } catch (err) {
      console.warn('[orders] 取消失败:', err);
    } finally {
      setSubmitting('');
    }
  };

  // 确认收货
  const handleConfirm = async (e, orderId: string) => {
    e.stopPropagation();
    if (submitting) return;
    const res = await Taro.showModal({ title: '确认收货', content: '请确认已收到商品' });
    if (!res.confirm) return;
    setSubmitting(orderId);
    try {
      await OrderAPI.confirm(orderId);
      Taro.showToast({ title: '确认收货成功', icon: 'success' });
      refreshOne(orderId);
    } catch (err) {
      console.warn('[orders] 确认收货失败:', err);
    } finally {
      setSubmitting('');
    }
  };

  // 跳转详情
  const goDetail = (orderId: string) => {
    Taro.navigateTo({ url: `/pages/order-detail/index?id=${orderId}` });
  };

  // 汇总(全部 tab 时展示)
  const summary = useMemo(() => ({
    pending: orders.filter(o => o.status === 'PENDING').length,
  }), [orders]);

  if (loading && orders.length === 0) {
    return (
      <View className={styles.page}>
        <View className={styles.empty}>
          <View className={styles.emptyIcon}>📦</View>
          <View className={styles.emptyText}>加载中...</View>
        </View>
      </View>
    );
  }

  return (
    <View className={styles.page}>
      {/* 状态筛选(横向滚动) */}
      <ScrollView scrollX className={styles.tabs} scrollWithAnimation>
        {STATUS_TABS.map(tab => (
          <View
            key={tab.key}
            className={`${styles.tab} ${filter === tab.key ? styles.tabActive : ''}`}
            onClick={() => handleTab(tab.key)}
          >
            {tab.label}
          </View>
        ))}
      </ScrollView>

      {/* 订单列表 */}
      {orders.length === 0 ? (
        <View className={styles.empty}>
          <View className={styles.emptyIcon}>📦</View>
          <View className={styles.emptyText}>
            {filter ? `暂无${STATUS_TABS.find(t => t.key === filter)?.label || ''}订单` : '暂无订单,去逛逛吧'}
          </View>
        </View>
      ) : (
        <View className={styles.list}>
          {orders.map(order => {
            const cls = STATUS_CLS[order.status] || 'completed';
            const isPending = order.status === 'PENDING';
            const isShipped = order.status === 'SHIPPED';
            const isReceived = order.status === 'RECEIVED';
            return (
              <View key={order.orderId} className={styles.card} onClick={() => goDetail(order.orderId)}>
                {/* 卡片头: 订单号 + 状态 */}
                <View className={styles.cardHeader}>
                  <View className={styles.orderNo}>{order.orderId}</View>
                  <View className={`${styles.badge} ${styles[cls]}`}>{order.statusName}</View>
                </View>

                {/* 商品列表 */}
                <View className={styles.items}>
                  {order.items.map((it, idx) => (
                    <View key={idx} className={styles.itemRow}>
                      <View className={styles.itemName}>{it.productName}</View>
                      <View className={styles.itemRight}>
                        <View className={styles.itemPrice}>¥{it.unitPrice}</View>
                        <View className={styles.itemQty}>×{it.quantity}</View>
                      </View>
                    </View>
                  ))}
                </View>

                {/* 卡片底: 金额 + 操作 */}
                <View className={styles.cardFooter}>
                  <View className={styles.amount}>
                    实付 <Text className={styles.amountNum}>¥{order.priceDetail.actualAmount.toFixed(2)}</Text>
                  </View>
                  <View className={styles.actions}>
                    {isPending ? (
                      <>
                        <View
                          className={styles.ghostBtn}
                          onClick={(e) => handleCancel(e, order.orderId)}
                        >
                          {submitting === order.orderId ? '处理中' : '取消订单'}
                        </View>
                        <View
                          className={styles.primaryBtn}
                          onClick={(e) => handlePay(e, order.orderId)}
                        >
                          {submitting === order.orderId ? '支付中' : '去支付'}
                        </View>
                      </>
                    ) : null}
                    {isShipped ? (
                      <View
                        className={styles.primaryBtn}
                        onClick={(e) => handleConfirm(e, order.orderId)}
                      >
                        {submitting === order.orderId ? '处理中' : '确认收货'}
                      </View>
                    ) : null}
                    {isReceived ? (
                      <View className={styles.primaryBtn} onClick={() => goDetail(order.orderId)}>
                        去评价
                      </View>
                    ) : null}
                  </View>
                </View>

                {/* 时间 */}
                <View className={styles.cardTime}>下单时间: {formatTime(order.createdAt)}</View>
              </View>
            );
          })}
        </View>
      )}

      {filter === '' && orders.length > 0 && summary.pending > 0 ? (
        <View className={styles.note}>有 {summary.pending} 笔待付款订单,请及时支付</View>
      ) : (
        <View className={styles.note}>订单状态: 待付款 → 待发货 → 待收货 → 待评价 → 已完成</View>
      )}
    </View>
  );
};

export default OrdersPage;
