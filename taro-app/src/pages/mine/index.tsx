import React, { useState, useEffect } from 'react';
import { View, Text, Button, ScrollView } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import styles from './index.module.scss';
import CheckoutService from '@/services/checkout-service';
import { MemberAPI } from '@/api/member';
import { OrderAPI, ORDER_STATUS_NAME } from '@/api/order';
import { AuthAPI } from '@/api/auth';
import { clearSession, getMemberId, isLoggedIn } from '@/services/auth-service';
import { PointsAPI } from '@/api/points';
import { statusColor, DANGER_COLOR } from '@/config';

// 会员等级名(与后端 member_service.LEVEL_NAMES 对齐)
const MEMBER_LEVEL_NAME: Record<string, string> = {
  L1: '竹芽会员', L2: '竹叶会员', L3: '竹林会员', L4: '竹海 VIP', L5: '竹海 SVIP',
};
// 升下一级所需成长值(累计消费元, 与后端 LEVEL_THRESHOLDS 对齐)
const NEXT_LEVEL_GROWTH: Record<string, number> = {
  L1: 500, L2: 3000, L3: 6999, L4: 9999, L5: 9999,
};

const MinePage: React.FC = () => {
  const [member, setMember] = useState<any>(null);
  const [orders, setOrders] = useState<any[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);
  // 等级信息(keepLevel 保级进度, L5 SVIP 续费用)
  const [levelInfo, setLevelInfo] = useState<any>(null);
  const [renewing, setRenewing] = useState(false);
  // 订单区默认折叠: 会员卡与工作台等内容优先可见, 点标题展开
  const [ordersCollapsed, setOrdersCollapsed] = useState(true);

  // 退出登录
  const handleLogout = () => {
    Taro.showModal({
      title: '退出登录',
      content: '确定退出当前账号吗?',
      success: async (res) => {
        if (!res.confirm) return;
        await AuthAPI.logout();
        clearSession();
        Taro.showToast({ title: '已退出登录', icon: 'success' });
        setTimeout(() => {
          setRefreshKey(k => k + 1);
          Taro.navigateTo({ url: '/pages/login/index' });
        }, 800);
      },
    });
  };

  // 页面每次显示时刷新(登录页 navigateBack 返回后同步已登录态,
  // 无需手动刷新页面; refreshKey 兼容既有登出/清数据刷新路径)
  useDidShow(() => {
    setRefreshKey(k => k + 1);
  });

  useEffect(() => {
    (async () => {
      // 未登录 → 展示登录引导(不打真实 API)
      if (!isLoggedIn()) {
        setMember({ guest: true, name: '未登录', points: 0, level: 'L1' });
        setOrders([]);
        return;
      }
      // 优先调真实后端 API 获取会员信息
      try {
        const m = await MemberAPI.profile();
        // 积分显示以积分账本为准(member.points 为遗留字段, 签到不回写)
        const accountId = Number(m.id || getMemberId() || 0);
        const account = await PointsAPI.account(accountId).catch(() => null);
        setMember({
          id: m.id,
          name: m.name,
          points: account ? account.totalPoints : m.points,
          level: m.level,
          levelName: m.levelName || '',
          growthValue: Number(m.growthValue ?? 0),
          role: (m as any).role || 'member',
        });
      } catch (e) {
        console.warn('[mine] 会员API失败,降级 mock:', e);
        const db = CheckoutService.getMockDB();
        const m = (db.members || []).find((x: any) => x.id === 2) || db.members?.[0];
        setMember(m);
      }

      // 等级信息(keepLevel 保级进度/到期日/SVIP 续费标记, 失败静默)
      try {
        const lv = await MemberAPI.level();
        setLevelInfo(lv);
      } catch (e) {
        console.warn('[mine] 等级API失败:', e);
        setLevelInfo(null);
      }

      // 优先调真实后端 API 获取订单列表
      try {
        const res = await OrderAPI.myOrders();
        const orderList = (res.orders || res || []).map((o: any) => ({
          order_no: o.orderId || o.order_id || o.order_no || '',
          // 状态中文化: 后端原始码 PENDING/PAID/... → 待付款/待发货/...
          status: o.statusName || ORDER_STATUS_NAME[o.status] || o.status || '待付款',
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
    // 真实后端订单 → 跳转订单详情页; mock 订单 → 弹窗展示
    if (order.isApiOrder && order.order_no) {
      Taro.navigateTo({ url: `/pages/order-detail/index?id=${order.order_no}` });
      return;
    }
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

  // SVIP 付费(¥99/年): L1-L4 直接购买开通 / L5 续费(周期重开 12 个月)
  const handleSvipPay = () => {
    if (renewing) return;
    const kl = levelInfo?.keepLevel || {};
    const isL5 = levelKey === 'L5';
    const content = isL5
      ? `续费 ¥99/年, 续费成功后等级周期重新起算 12 个月(当前周期${
          kl.daysRemaining != null ? `剩余 ${kl.daysRemaining} 天` : '即将到期'
        })。确认续费?`
      : '支付 ¥99/年, 立即升级为竹海 SVIP(8 折购物 / ×3.0 返分 / 城主店资格), 等级周期 12 个月起算。确认购买?';
    Taro.showModal({
      title: isL5 ? 'SVIP 付费续费' : '购买 SVIP',
      content,
      success: async (res) => {
        if (!res.confirm) return;
        setRenewing(true);
        try {
          const r = await MemberAPI.renewSvip();
          Taro.showToast({
            title: (r as any)?.action === 'purchased'
              ? '已升级竹海 SVIP' : '续费成功, 新周期已生效',
            icon: 'success',
          });
          setRefreshKey(k => k + 1);
        } catch (e) {
          console.warn('[mine] SVIP 付费失败:', e);
        } finally {
          setRenewing(false);
        }
      },
    });
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

  const levelKey = String(member.level || 'L1').toUpperCase().startsWith('L')
    ? String(member.level).toUpperCase()
    : `L${member.level}`;
  // 等级名: 优先后端 levelName, 兜底本地字典
  const levelName = member.levelName || MEMBER_LEVEL_NAME[levelKey] || '竹芽会员';
  // 升级进度按成长值(累计消费), 与后端 LEVEL_THRESHOLDS 对齐
  const growth = Number(member.growthValue ?? member.growth ?? 0);
  const nextGrowth = NEXT_LEVEL_GROWTH[levelKey] || 9999;
  const progress = levelKey === 'L5' ? 100 : Math.min(100, Math.round((growth / nextGrowth) * 100));

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
                {member.guest ? (
                  <View
                    className={styles.levelBadge}
                    onClick={() => Taro.navigateTo({ url: '/pages/login/index' })}
                  >
                    点我登录 ›
                  </View>
                ) : (
                  <View className={styles.levelBadge}>{levelName}</View>
                )}
              </View>
            </View>
            {!member.guest ? (
              <View className={styles.logoutBtn} onClick={handleLogout}>退出</View>
            ) : null}
          </View>
          <View className={styles.memberStats}>
            <View className={styles.statItem} onClick={() => Taro.navigateTo({ url: '/pages/points/index' })}>
              <View className={styles.statValue}>{member.points}</View>
              <View className={styles.statLabel}>积分 ›</View>
            </View>
            <View className={styles.statDivider} />
            <View className={styles.statItem} onClick={() => Taro.navigateTo({ url: '/pages/orders/index' })}>
              <View className={styles.statValue}>{orders.length}</View>
              <View className={styles.statLabel}>订单 ›</View>
            </View>
            <View className={styles.statDivider} />
            <View className={styles.statItem}>
              <View className={styles.statValue}>{levelKey}</View>
              <View className={styles.statLabel}>等级</View>
            </View>
          </View>
          {levelKey !== 'L5' && (
            <>
              <View className={styles.progressBox}>
                <View className={styles.progressBar}>
                  <View className={styles.progressFill} style={{ width: `${progress}%` }} />
                </View>
                <View className={styles.progressText}>
                  距{MEMBER_LEVEL_NAME['L' + (Number(levelKey.slice(1)) + 1)] || '下一等级'}还差 ¥{Math.max(0, nextGrowth - growth)} 累计消费
                </View>
              </View>
              {/* SVIP 直接购买入口(L1-L4, ¥99/年立即升级) */}
              <View className={styles.svipBuyLink} onClick={handleSvipPay}>
                {renewing ? '处理中...' : '不想等? 直接购买 SVIP ¥99/年 ›'}
              </View>
            </>
          )}
          {levelKey === 'L5' && (
            <>
              <View className={styles.maxLevelTip}>已达最高等级 · 竹海 SVIP</View>
              {/* SVIP 续费区(周期信息 + ¥99/年付费续费, 仅 L5) */}
              <View className={styles.svipBox}>
                {levelInfo?.keepLevel?.daysRemaining != null && (
                  <View className={styles.svipTip}>
                    当前周期剩余 {levelInfo.keepLevel.daysRemaining} 天
                    (到期 {(levelInfo.keepLevel.expireAt || '').slice(0, 10)})
                  </View>
                )}
                <View className={styles.svipRenewBtn} onClick={handleSvipPay}>
                  {renewing ? '续费中...' : 'SVIP 续费 ¥99/年'}
                </View>
              </View>
            </>
          )}
        </View>

        {/* 我的订单 */}
        <View className={styles.section}>
          <View
            className={styles.sectionHeader}
            onClick={() => orders.length > 0 && setOrdersCollapsed(c => !c)}
          >
            <View className={styles.sectionTitle}>
              我的订单{orders.length > 0 && (
                <Text className={styles.orderToggle}>{ordersCollapsed ? ` (${orders.length}) ▸` : ' ▾'}</Text>
              )}
            </View>
            {orders.length > 0 && (
              <View
                className={styles.orderCount}
                onClick={(e) => {
                  e.stopPropagation();
                  Taro.navigateTo({ url: '/pages/orders/index' });
                }}
              >
                共 {orders.length} 单 · 全部订单 ›
              </View>
            )}
          </View>
          {orders.length === 0 ? (
            <View className={styles.emptyOrders}>
              <View className={styles.emptyIcon}>📦</View>
              <View className={styles.emptyText}>暂无订单</View>
              <Button className={styles.goShoppingBtn} onClick={handleGoShopping}>去购物</Button>
            </View>
          ) : ordersCollapsed ? null : (
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

        {/* 权限中心(登录即可见: 申请权限/审批/责任书) */}
        {member && (
          <View className={styles.section}>
            <View className={styles.sectionTitle}>工作台</View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/perm-center/index' })}>
              <View className={styles.adminEntryIcon}>🔐</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>权限中心</View>
                <View className={styles.adminEntryDesc}>申请审批 · 权责共存 · 限时回收 · 审计留痕</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/trace-punch/index' })}>
              <View className={styles.adminEntryIcon}>🍶</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>产品溯源</View>
                <View className={styles.adminEntryDesc}>工段扫码打卡 · 批次溯源 · AI 异常把关</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/trace-view/index' })}>
              <View className={styles.adminEntryIcon}>🔍</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>溯源验真</View>
                <View className={styles.adminEntryDesc}>免登录 · 批次号/扫瓶码 · 全链时间线 · AI 健康度</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
          </View>
        )}

        {/* 管理员入口(仅 admin 可见) */}
        {member?.role === 'admin' && (
          <View className={styles.section}>
            <View className={styles.sectionTitle}>站点管理</View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/blogger/index' })}>
              <View className={styles.adminEntryIcon}>📡</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>DV博主流量</View>
                <View className={styles.adminEntryDesc}>博主池 · 雷达侦测 · 跟随发布 · 学习进化</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/flashsale-admin/index' })}>
              <View className={styles.adminEntryIcon}>⚡</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>秒杀管理</View>
                <View className={styles.adminEntryDesc}>场次配置 · 商品上架 · 发布风控 · 运营统计</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/pocket-admin/index' })}>
              <View className={styles.adminEntryIcon}>🤲</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>顺手赚钱管理</View>
                <View className={styles.adminEntryDesc}>点位审计 · 照片核验 · 作废处置 · 参数配置</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/attract72/index' })}>
              <View className={styles.adminEntryIcon}>🧲</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>AI智能引流</View>
                <View className={styles.adminEntryDesc}>画像感知 · 因果洞察 · 定律结晶 · 预算博弈</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/cs-workbench/index' })}>
              <View className={styles.adminEntryIcon}>🎧</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>人工客服工作台</View>
                <View className={styles.adminEntryDesc}>排队接入 · 实时收发 · 会话统计</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/kb-model/index' })}>
              <View className={styles.adminEntryIcon}>📚</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>智能知识库训练模型</View>
                <View className={styles.adminEntryDesc}>双师引擎 · 知识治理 · 缺口队列 · 问答测试</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/promo/index' })}>
              <View className={styles.adminEntryIcon}>📈</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>智能推广</View>
                <View className={styles.adminEntryDesc}>热点雷达 · Agent 工厂 · 发布中心 · 归因回流</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/zy/index' })}>
              <View className={styles.adminEntryIcon}>🧠</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>智启元AI财务</View>
                <View className={styles.adminEntryDesc}>智能问答 · 预测沙盘 · 税务优化 · 进化决策</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/zf/index' })}>
              <View className={styles.adminEntryIcon}>⚖️</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>智法AI法务</View>
                <View className={styles.adminEntryDesc}>生产合规 · 供应链金融 · 数据资产 · 电商风控</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/zw/index' })}>
              <View className={styles.adminEntryIcon}>🚚</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>智运AI物流</View>
                <View className={styles.adminEntryDesc}>智能路由 · 轨迹预测 · 四防风控 · 成本分析</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/zd/index' })}>
              <View className={styles.adminEntryIcon}>📦</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>智单AI订单</View>
                <View className={styles.adminEntryDesc}>订单洞察 · 履约预测 · 退款裁决 · 进化闭环</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/zp/index' })}>
              <View className={styles.adminEntryIcon}>💳</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>智付AI支付</View>
                <View className={styles.adminEntryDesc}>智能路由 · 风险熵 · 自进化引擎 · 红队免疫</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/zk/index' })}>
              <View className={styles.adminEntryIcon}>👑</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>智客AI会员</View>
                <View className={styles.adminEntryDesc}>会员洞察 · 流失预警 · 权益运营 · 进化闭环</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/qr/index' })}>
              <View className={styles.adminEntryIcon}>🔷</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>智码AI二维码</View>
                <View className={styles.adminEntryDesc}>六类码中枢 · 愉悦引擎 · 红队免疫</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
            <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/theme-admin/index' })}>
              <View className={styles.adminEntryIcon}>🎨</View>
              <View className={styles.adminEntryInfo}>
                <View className={styles.adminEntryName}>主题智能管理</View>
                <View className={styles.adminEntryDesc}>网站颜色 / 图标 · AI 把关 · 审计回滚</View>
              </View>
              <View className={styles.adminEntryArrow}>›</View>
            </View>
          </View>
        )}

        {/* 资产管理 */}
        <View className={styles.section}>
          <View className={styles.sectionTitle}>资产管理</View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/points/index' })}>
            <View className={styles.adminEntryIcon}>✨</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>我的积分</View>
              <View className={styles.adminEntryDesc}>签到日历 · 积分流水 · 过期提醒</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/trust/index' })}>
            <View className={styles.adminEntryIcon}>🧧</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>信值兑换</View>
              <View className={styles.adminEntryDesc}>积分换信值 · 支付组合 · 1 信值 = 1 元</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/credit/index' })}>
            <View className={styles.adminEntryIcon}>💳</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>信用先享后付</View>
              <View className={styles.adminEntryDesc}>信用额度 · 免息期 · AI 智能授信</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/wallet/index' })}>
            <View className={styles.adminEntryIcon}>💰</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>钱包盈利</View>
              <View className={styles.adminEntryDesc}>活期/定期收益 · 奖品 · 返利提现</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/recycle/index' })}>
            <View className={styles.adminEntryIcon}>🍶</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>老酒回收</View>
              <View className={styles.adminEntryDesc}>AI 估值 · 兑换新酒 · 折现回收</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
        </View>

        {/* 经营活动 */}
        <View className={styles.section}>
          <View className={styles.sectionTitle}>经营活动</View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/citystore/index' })}>
            <View className={styles.adminEntryIcon}>🏙️</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>市级网店</View>
              <View className={styles.adminEntryDesc}>SVIP 开店 · 一城一店 · 月度考核折扣</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/alliance/index' })}>
            <View className={styles.adminEntryIcon}>🤝</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>同盟商城</View>
              <View className={styles.adminEntryDesc}>酒水不分家 · 八大类目 · 下单评价入盟</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/venue/index' })}>
            <View className={styles.adminEntryIcon}>🏨</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>场馆合作联盟</View>
              <View className={styles.adminEntryDesc}>酒店酒吧会所 · 品鉴酒配额 · 等级分润</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/ride/index' })}>
            <View className={styles.adminEntryIcon}>🚗</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>代驾联盟</View>
              <View className={styles.adminEntryDesc}>满额赠券 · 三轨派单 · 券抵扣车费</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/agent-center/index' })}>
            <View className={styles.adminEntryIcon}>🏪</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>代理商中心</View>
              <View className={styles.adminEntryDesc}>五级代理 · 进货折扣 · 返利提现</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/cooperation/index' })}>
            <View className={styles.adminEntryIcon}>💼</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>商务合作</View>
              <View className={styles.adminEntryDesc}>企业/政府/经销 · AI 资质审核 · 签约</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/help/index' })}>
            <View className={styles.adminEntryIcon}>🤝</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>AI智能叫帮</View>
              <View className={styles.adminEntryDesc}>信值互助 · 公益100%/有偿10% · 零佣金</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
        </View>

        {/* 客服中心 */}
        <View className={styles.section}>
          <View className={styles.sectionTitle}>客服中心</View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/chat/index' })}>
            <View className={styles.adminEntryIcon}>🎧</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>在线客服</View>
              <View className={styles.adminEntryDesc}>AI 秒级应答 · 转人工 · 满意度评价</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/tickets/index' })}>
            <View className={styles.adminEntryIcon}>📋</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>我的工单</View>
              <View className={styles.adminEntryDesc}>问题追踪 · 处理记录 · 确认评价</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/messages/index' })}>
            <View className={styles.adminEntryIcon}>📬</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>消息中心</View>
              <View className={styles.adminEntryDesc}>订单/物流/活动通知 · 未读管理</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
        </View>

        {/* 个人信息管理 */}
        <View className={styles.section}>
          <View className={styles.sectionTitle}>个人信息管理</View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/pointsmall/index' })}>
            <View className={styles.adminEntryIcon}>🎁</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>积分商城</View>
              <View className={styles.adminEntryDesc}>好酒好物 · 会员权益 · 兑现金 AI 推荐</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/address/index' })}>
            <View className={styles.adminEntryIcon}>📍</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>收货地址</View>
              <View className={styles.adminEntryDesc}>地址簿管理 · 默认地址 · 新增/编辑</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
          <View className={styles.adminEntry} onClick={() => Taro.navigateTo({ url: '/pages/invoice/index' })}>
            <View className={styles.adminEntryIcon}>🧾</View>
            <View className={styles.adminEntryInfo}>
              <View className={styles.adminEntryName}>发票管理</View>
              <View className={styles.adminEntryDesc}>发票抬头 · 无感开票 · 红冲申诉</View>
            </View>
            <View className={styles.adminEntryArrow}>›</View>
          </View>
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
