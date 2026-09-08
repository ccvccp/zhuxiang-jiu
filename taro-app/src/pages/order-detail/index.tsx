/**
 * 订单详情 · 商品明细/价格明细/地址/物流轨迹/时间线/操作(支付/取消/确认收货/评价/退货/开票)
 * 数据来源: 后端 /api/order/{id} + /api/logistics/* + /api/invoice/*
 */
import React, { useState, useEffect } from 'react';
import { View, Text, Textarea, ScrollView } from '@tarojs/components';
import Taro, { useRouter } from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import { OrderAPI, OrderVO, ORDER_STATUS_NAME } from '@/api/order';
import { LogisticsAPI, WaybillVO, TrackVO } from '@/api/logistics';
import { InvoiceAPI } from '@/api/invoice';

// 状态 → 头部图标与提示
const STATUS_META: Record<string, { icon: string; tip: string }> = {
  PENDING: { icon: '💰', tip: '订单待支付,请尽快完成付款' },
  PAID: { icon: '🍷', tip: '已支付成功,商家正在备货' },
  SHIPPED: { icon: '🚚', tip: '商品已发出,请注意查收' },
  RECEIVED: { icon: '⭐', tip: '已确认收货,期待您的评价' },
  COMPLETED: { icon: '✅', tip: '订单已完成,感谢您的购买' },
  CANCELLED: { icon: '🚫', tip: '订单已取消' },
  RETURNING: { icon: '↩️', tip: '退货处理中' },
};

const formatTime = (t: string): string => (t ? t.slice(0, 19).replace('T', ' ') : '');

const OrderDetailPage: React.FC = () => {
  const { params } = useRouter();
  const orderId = params.id || '';

  const [order, setOrder] = useState<OrderVO | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  // 评价表单
  const [rating, setRating] = useState(5);
  const [reviewContent, setReviewContent] = useState('');
  const [showReview, setShowReview] = useState(false);
  // 物流(发货后展示)
  const [waybill, setWaybill] = useState<WaybillVO | null>(null);
  const [tracks, setTracks] = useState<TrackVO[]>([]);
  // 退货理由
  const [returnReason, setReturnReason] = useState('');

  // 发货及之后状态才拉物流
  const NEEDS_LOGISTICS = ['SHIPPED', 'RECEIVED', 'COMPLETED', 'RETURNING'];

  const loadOrder = async () => {
    try {
      const o = await OrderAPI.detailVO(orderId);
      setOrder(o);
      setShowReview(o.status === 'RECEIVED');
      // 物流加载(独立, 失败静默)
      if (NEEDS_LOGISTICS.includes(o.status)) {
        LogisticsAPI.orderByOrder(orderId)
          .then(wb => {
            setWaybill(wb);
            if (wb) {
              LogisticsAPI.tracks(wb.waybillNo, 20)
                .then(setTracks)
                .catch(() => setTracks([]));
            }
          })
          .catch(() => setWaybill(null));
      }
    } catch (e) {
      console.warn('[order-detail] 加载失败:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadOrder(); }, []);

  const reload = async () => {
    try {
      const o = await OrderAPI.detailVO(orderId);
      setOrder(o);
    } catch (_) { /* 静默 */ }
  };

  // 去支付
  const handlePay = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await OrderAPI.pay(orderId);
      Taro.showToast({ title: '支付成功', icon: 'success' });
      reload();
    } catch (e) {
      console.warn('[order-detail] 支付失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 取消订单
  const handleCancel = async () => {
    if (submitting) return;
    const res = await Taro.showModal({ title: '取消订单', content: '确定取消该订单吗?已扣积分将退还' });
    if (!res.confirm) return;
    setSubmitting(true);
    try {
      await OrderAPI.cancel(orderId);
      Taro.showToast({ title: '已取消', icon: 'success' });
      reload();
    } catch (e) {
      console.warn('[order-detail] 取消失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 确认收货
  const handleConfirm = async () => {
    if (submitting) return;
    const res = await Taro.showModal({ title: '确认收货', content: '请确认已收到商品' });
    if (!res.confirm) return;
    setSubmitting(true);
    try {
      await OrderAPI.confirm(orderId);
      Taro.showToast({ title: '确认收货成功', icon: 'success' });
      reload();
    } catch (e) {
      console.warn('[order-detail] 确认收货失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 提交评价
  const handleReview = async () => {
    if (submitting) return;
    if (rating < 1) {
      Taro.showToast({ title: '请先选择星级', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      await OrderAPI.review(orderId, rating, reviewContent);
      Taro.showToast({ title: '评价成功', icon: 'success' });
      setShowReview(false);
      reload();
    } catch (e) {
      console.warn('[order-detail] 评价失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 申请退货(COMPLETED → RETURNING, reason 必填)
  const handleReturn = async () => {
    if (submitting) return;
    const res = await Taro.showModal({
      title: '申请退货',
      content: '确认对该订单发起退货申请吗? 审核通过后退款原路退回',
      editable: true,             // 小程序/Taro 支持可编辑弹窗(H5 降级为普通确认)
      placeholderText: '请填写退货原因',
    });
    if (!res.confirm) return;
    const reason = (res.content || '').trim() || '用户申请退货';
    setSubmitting(true);
    try {
      await OrderAPI.return(orderId, reason);
      Taro.showToast({ title: '退货申请已提交', icon: 'success' });
      reload();
    } catch (e) {
      console.warn('[order-detail] 退货失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 申请开票(用默认抬头; 无抬头引导去发票页维护)
  const handleInvoice = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      const titles = await InvoiceAPI.titles();
      const def = titles.find(t => t.isDefault) || titles[0];
      if (!def) {
        Taro.showModal({
          title: '尚未添加发票抬头',
          content: '请先在「发票管理」中添加抬头后再申请开票',
          confirmText: '去添加',
          success: (r) => {
            if (r.confirm) Taro.navigateTo({ url: '/pages/invoice/index' });
          },
        });
        return;
      }
      await InvoiceAPI.requestInvoice(orderId, def.id);
      Taro.showToast({ title: '开票申请已提交', icon: 'success' });
    } catch (e) {
      console.warn('[order-detail] 开票失败:', e);
      Taro.showToast({ title: '开票失败,请稍后再试', icon: 'none' });
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <View className={styles.page}>
        <NavBar title="订单详情" />
        <View className={styles.empty}>
          <View className={styles.emptyIcon}>📦</View>
          <View className={styles.emptyText}>加载中...</View>
        </View>
      </View>
    );
  }

  if (!order) {
    return (
      <View className={styles.page}>
        <NavBar title="订单详情" />
        <View className={styles.empty}>
          <View className={styles.emptyIcon}>🔍</View>
          <View className={styles.emptyText}>订单不存在</View>
        </View>
      </View>
    );
  }

  const meta = STATUS_META[order.status] || { icon: '📦', tip: '' };
  const pd = order.priceDetail;
  const addr = order.address || {};

  return (
    <View className={styles.page}>
        <NavBar title="订单详情" />
      {/* 状态头部 */}
      <View className={styles.statusCard}>
        <View className={styles.statusIcon}>{meta.icon}</View>
        <View className={styles.statusInfo}>
          <View className={styles.statusName}>{order.statusName || ORDER_STATUS_NAME[order.status]}</View>
          <View className={styles.statusTip}>{meta.tip}</View>
        </View>
      </View>

      {/* 收货地址 */}
      {addr.name ? (
        <View className={styles.card}>
          <View className={styles.cardTitle}>收货信息</View>
          <View className={styles.addrRow}>
            <Text className={styles.addrName}>{addr.name}</Text>
            <Text className={styles.addrPhone}>{addr.phone}</Text>
          </View>
          <View className={styles.addrDetail}>
            {[addr.province, addr.city, addr.district, addr.detail].filter(Boolean).join(' ')}
          </View>
          {order.remark ? <View className={styles.remark}>备注: {order.remark}</View> : null}
        </View>
      ) : null}

      {/* 商品明细 */}
      <View className={styles.card}>
        <View className={styles.cardTitle}>商品清单</View>
        {order.items.map((it, idx) => (
          <View key={idx} className={styles.itemRow}>
            <View className={styles.itemIcon}>🍶</View>
            <View className={styles.itemInfo}>
              <View className={styles.itemName}>{it.productName}</View>
              <View className={styles.itemMeta}>¥{it.unitPrice} / 瓶</View>
            </View>
            <View className={styles.itemQty}>×{it.quantity}</View>
          </View>
        ))}
      </View>

      {/* 价格明细 */}
      <View className={styles.card}>
        <View className={styles.cardTitle}>价格明细</View>
        <View className={styles.priceRow}>
          <Text className={styles.priceLabel}>商品总价</Text>
          <Text className={styles.priceValue}>¥{pd.goodsTotal.toFixed(2)}</Text>
        </View>
        {pd.memberDiscount !== 0 ? (
          <View className={styles.priceRow}>
            <Text className={styles.priceLabel}>会员折扣</Text>
            <Text className={styles.priceDiscount}>¥{pd.memberDiscount.toFixed(2)}</Text>
          </View>
        ) : null}
        {pd.couponDiscount !== 0 ? (
          <View className={styles.priceRow}>
            <Text className={styles.priceLabel}>优惠券</Text>
            <Text className={styles.priceDiscount}>¥{pd.couponDiscount.toFixed(2)}</Text>
          </View>
        ) : null}
        {pd.pointsDiscount !== 0 ? (
          <View className={styles.priceRow}>
            <Text className={styles.priceLabel}>积分抵扣</Text>
            <Text className={styles.priceDiscount}>¥{pd.pointsDiscount.toFixed(2)}</Text>
          </View>
        ) : null}
        <View className={styles.priceRow}>
          <Text className={styles.priceLabel}>运费</Text>
          <Text className={styles.priceValue}>{pd.shippingFee === 0 ? '免运费' : `¥${pd.shippingFee.toFixed(2)}`}</Text>
        </View>
        <View className={`${styles.priceRow} ${styles.priceTotal}`}>
          <Text className={styles.priceLabel}>实付金额</Text>
          <Text className={styles.priceTotalValue}>¥{pd.actualAmount.toFixed(2)}</Text>
        </View>
      </View>

      {/* 物流轨迹(发货后展示) */}
      {NEEDS_LOGISTICS.includes(order.status) ? (
        <View className={styles.card}>
          <View className={styles.cardTitle}>
            物流信息
            {waybill ? <Text className={styles.carrierTag}>{waybill.carrier || '快递'}</Text> : null}
          </View>
          {waybill ? (
            <>
              <View className={styles.waybillNo}>运单号: {waybill.waybillNo}</View>
              {tracks.length > 0 ? (
                <View className={styles.trackList}>
                  {tracks.slice(0, 5).map((t, idx) => (
                    <View key={t.id ?? idx} className={styles.trackItem}>
                      <View className={`${styles.trackDot} ${idx === 0 ? styles.trackDotActive : ''}`} />
                      <View className={styles.trackBody}>
                        <View className={styles.trackStatus}>{t.status}{t.location ? ` · ${t.location}` : ''}</View>
                        {t.detail ? <View className={styles.trackDetail}>{t.detail}</View> : null}
                        <View className={styles.trackTime}>{formatTime(t.happenedAt)}</View>
                      </View>
                    </View>
                  ))}
                </View>
              ) : (
                <View className={styles.trackEmpty}>暂无轨迹更新</View>
              )}
            </>
          ) : (
            <View className={styles.trackEmpty}>暂无物流单,商家备货中</View>
          )}
        </View>
      ) : null}

      {/* 订单时间线(倒序, 最新在前) */}
      {order.timeline.length > 0 ? (
        <View className={styles.card}>
          <View className={styles.cardTitle}>订单动态</View>
          <View className={styles.timeline}>
            {order.timeline.slice().reverse().map((t, idx) => (
              <View key={idx} className={styles.timelineItem}>
                <View className={`${styles.timelineDot} ${idx === 0 ? styles.timelineDotActive : ''}`} />
                {idx < order.timeline.length - 1 ? <View className={styles.timelineLine} /> : null}
                <View className={styles.timelineBody}>
                  <View className={styles.timelineAction}>{t.action || ORDER_STATUS_NAME[t.status] || t.status}</View>
                  <View className={styles.timelineTime}>{formatTime(t.time)}</View>
                </View>
              </View>
            ))}
          </View>
        </View>
      ) : null}

      {/* 评价表单(待评价状态) */}
      {showReview ? (
        <View className={styles.card}>
          <View className={styles.cardTitle}>评价本单</View>
          <View className={styles.stars}>
            {[1, 2, 3, 4, 5].map(n => (
              <View
                key={n}
                className={`${styles.star} ${n <= rating ? styles.starActive : ''}`}
                onClick={() => setRating(n)}
              >
                ★
              </View>
            ))}
            <View className={styles.ratingText}>{rating} 星</View>
          </View>
          <View className={styles.reviewInput}>
            <Textarea
              value={reviewContent}
              onInput={(e) => setReviewContent(e.detail.value)}
              maxlength={200}
              placeholder='说说这酒怎么样(选填)'
              className={styles.textarea}
            />
          </View>
          <View className={styles.reviewSubmit} onClick={handleReview}>
            {submitting ? '提交中...' : '提交评价'}
          </View>
        </View>
      ) : null}

      {/* 订单信息 */}
      <View className={styles.card}>
        <View className={styles.cardTitle}>订单信息</View>
        <View className={styles.infoRow}>
          <Text className={styles.infoLabel}>订单编号</Text>
          <Text className={styles.infoValue}>{order.orderId}</Text>
        </View>
        <View className={styles.infoRow}>
          <Text className={styles.infoLabel}>下单时间</Text>
          <Text className={styles.infoValue}>{formatTime(order.createdAt)}</Text>
        </View>
      </View>

      {/* 底部操作栏 */}
      {order.status === 'PENDING' || order.status === 'SHIPPED' ? (
        <View className={styles.footer}>
          {order.status === 'PENDING' ? (
            <>
              <View className={styles.ghostBtn} onClick={handleCancel}>
                {submitting ? '处理中' : '取消订单'}
              </View>
              <View className={styles.primaryBtn} onClick={handlePay}>
                {submitting ? '支付中' : `去支付 ¥${pd.actualAmount.toFixed(2)}`}
              </View>
            </>
          ) : (
            <View className={styles.primaryBtn} onClick={handleConfirm}>
              {submitting ? '处理中' : '确认收货'}
            </View>
          )}
        </View>
      ) : null}

      {/* 完成态操作行(退货/开票) */}
      {order.status === 'COMPLETED' ? (
        <View className={styles.footer}>
          <View className={styles.ghostBtn} onClick={handleReturn}>
            {submitting ? '处理中' : '申请退货'}
          </View>
          <View className={styles.ghostBtn} onClick={handleInvoice}>
            {submitting ? '处理中' : '申请开票'}
          </View>
        </View>
      ) : null}
    </View>
  );
};

export default OrderDetailPage;
