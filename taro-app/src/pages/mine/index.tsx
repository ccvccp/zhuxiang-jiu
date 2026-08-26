import React, { useState, useEffect } from 'react';
import { View, Button, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import CheckoutService from '@/services/checkout-service';
import { MemberAPI } from '@/api/member';
import { OrderAPI } from '@/api/order';
import { LEVEL_NAME, NEXT_LEVEL_POINTS, statusColor, DANGER_COLOR } from '@/config';

const MinePage: React.FC = () => {
  const [member, setMember] = useState<any>(null);
  const [orders, setOrders] = useState<any[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    (async () => {
      // 优先调真实后端 API 获取会员信息
      try {
        const m = await MemberAPI.profile();
        setMember({
          id: m.id,
          name: m.name,
          points: m.points,
          level: m.level,
        });
      } catch (e) {
        console.warn('[mine] 会员API失败,降级 mock:', e);
        const db = CheckoutService.getMockDB();
        const m = (db.members || []).find((x: any) => x.id === 2) || db.members?.[0];
        setMember(m);
      }

      // 优先调真实后端 API 获取订单列表
      try {
        const res = await OrderAPI.myOrders();
        const orderList = (res.orders || res || []).map((o: any) => ({
          order_no: o.orderId || o.order_id || o.order_no || '',
          status: o.status || '待付款',
          items: (o.items || []).map((i: any) => ({
            name: i.productName || i.name || '',
            qty: i.quantity || i.qty || 1,
          })),
          shipper_type: o.shipperType || o.shipper_type || 'manufacturer',
          shipper_agent_name: o.shipperAgentName || o.shipper_agent_name || '',
          final_amount: (o.priceDetail || {}).actualAmount || o.final_amount || 0,
          points_earned: (o.priceDetail || {}).pointsEarned || o.points_earned || 0,
        }));
        setOrders(orderList);
      } catch (e) {
        console.warn('[mine] 订单API失败,降级 mock:', e);
        const db = CheckoutService.getMockDB();
        setOrders(db.orders || []);
      }
    })();
  }, [refreshKey]);

  const handleClearData = () => {
    Taro.showModal({
      title: '删除我的数据',
      content: '根据《个人信息保护法》第47条，您有权删除个人信息。此操作将清除所有订单、积分、会员数据，不可恢复。确认删除？',
      confirmColor: DANGER_COLOR,
      success: (res) => {
        if (res.confirm) {
          CheckoutService.resetMock();
          Taro.showToast({ title: '数据已清除', icon: 'success' });
          setTimeout(() => {
            setRefreshKey(k => k + 1);
            Taro.switchTab({ url: '/pages/index/index' });
          }, 1500);
        }
      }
    });
  };

  const handleViewOrder = (order: any) => {
    Taro.showModal({
      title: `订单 ${order.order_no}`,
      content: `商品: ${(order.items || []).map((i: any) => i.name).join(', ')}\n实付: ¥${order.final_amount}\n状态: ${order.status}\n发货方: ${order.shipper_type === 'agent' ? '代理商:' + (order.shipper_agent_name || '') : '厂家直供'}\n积分入账: +${order.points_earned || 0}`,
      showCancel: false,
      confirmText: '关闭',
    });
  };

  const handleGoShopping = () => {
    Taro.switchTab({ url: '/pages/products/index' });
  };

  if (!member) {
    return (
      <View className={styles.page}>
        <View className={styles.empty}>
          <View className={styles.emptyIcon}>👤</View>
          <View className={styles.emptyText}>加载中...</View>
        </View>
      </View>
    );
  }

  const levelName = LEVEL_NAME[member.level] || '普通会员';
  const nextPoints = NEXT_LEVEL_POINTS[member.level] || 10000;
  const progress = member.level === 'L5' ? 100 : Math.min(100, Math.round((member.points / nextPoints) * 100));

  return (
    <View className={styles.page}>
      <ScrollView scrollY className={styles.scrollView}>
        {/* 会员卡 */}
        <View className={styles.memberCard}>
          <View className={styles.memberHeader}>
            <View className={styles.avatar}>{member.name?.[0] || '用'}</View>
            <View className={styles.memberInfo}>
              <View className={styles.memberName}>{member.name}</View>
              <View className={styles.memberLevel}>
                <View className={styles.levelBadge}>{levelName}</View>
              </View>
            </View>
          </View>
          <View className={styles.memberStats}>
            <View className={styles.statItem}>
              <View className={styles.statValue}>{member.points}</View>
              <View className={styles.statLabel}>积分</View>
            </View>
            <View className={styles.statDivider} />
            <View className={styles.statItem}>
              <View className={styles.statValue}>{orders.length}</View>
              <View className={styles.statLabel}>订单</View>
            </View>
            <View className={styles.statDivider} />
            <View className={styles.statItem}>
              <View className={styles.statValue}>{member.level}</View>
              <View className={styles.statLabel}>等级</View>
            </View>
          </View>
          {member.level !== 'L5' && (
            <View className={styles.progressBox}>
              <View className={styles.progressBar}>
                <View className={styles.progressFill} style={{ width: `${progress}%` }} />
              </View>
              <View className={styles.progressText}>
                距{LEVEL_NAME['L' + (Number(member.level.slice(1)) + 1)] || '下一等级'}还差 {nextPoints - member.points} 积分
              </View>
            </View>
          )}
          {member.level === 'L5' && (
            <View className={styles.maxLevelTip}>已达最高等级</View>
          )}
        </View>

        {/* 我的订单 */}
        <View className={styles.section}>
          <View className={styles.sectionHeader}>
            <View className={styles.sectionTitle}>我的订单</View>
            {orders.length > 0 && (
              <View className={styles.orderCount}>共 {orders.length} 单</View>
            )}
          </View>
          {orders.length === 0 ? (
            <View className={styles.emptyOrders}>
              <View className={styles.emptyIcon}>📦</View>
              <View className={styles.emptyText}>暂无订单</View>
              <Button className={styles.goShoppingBtn} onClick={handleGoShopping}>去购物</Button>
            </View>
          ) : (
            <View className={styles.orderList}>
              {orders.slice().reverse().map((order: any) => (
                <View key={order.order_no} className={styles.orderCard} onClick={() => handleViewOrder(order)}>
                  <View className={styles.orderTop}>
                    <View className={styles.orderNo}>订单号: {order.order_no}</View>
                    <View className={styles.orderStatus} style={{ color: statusColor(order.status) }}>
                      {order.status}
                    </View>
                  </View>
                  <View className={styles.orderItems}>
                    {(order.items || []).map((item: any, idx: number) => (
                      <View key={idx} className={styles.orderItem}>
                        <View className={styles.itemName}>{item.name}</View>
                        <View className={styles.itemQty}>x{item.qty}</View>
                      </View>
                    ))}
                  </View>
                  <View className={styles.orderBottom}>
                    <View className={styles.orderShipper}>
                      {order.shipper_type === 'agent' ? `代理商: ${order.shipper_agent_name || ''}` : '厂家直供'}
                    </View>
                    <View className={styles.orderAmount}>¥{order.final_amount}</View>
                  </View>
                </View>
              ))}
            </View>
          )}
        </View>

        {/* 个人信息管理 */}
        <View className={styles.section}>
          <View className={styles.sectionTitle}>个人信息管理</View>
          <View className={styles.privacyDesc}>
            根据《个人信息保护法》，您有权查看、修改和删除个人信息。
          </View>
          <Button className={styles.dangerButton} onClick={handleClearData}>
            删除我的数据
          </Button>
        </View>
      </ScrollView>
    </View>
  );
};

export default MinePage;
