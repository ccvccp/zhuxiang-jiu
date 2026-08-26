import React, { useState, useEffect } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import CheckoutService from '@/services/checkout-service';
import { ProductAPI, ProductVO } from '@/api/product';
import { PromotionAPI, ActivityVO, GroupBuyTier } from '@/api/promotion';
import { MemberAPI } from '@/api/member';
import {
  SERVICE_PHONE,
  SIGN_IN_REWARD_POINTS,
  SIGN_IN_STORAGE_KEY,
  NOTICE_INTERVAL_MS,
} from '@/config';

// 公告轮播文案(API 无公告接口,降级 mock)
const MOCK_NOTICES = [
  '竹香佳酿新品上市 · 限时尝鲜价 ¥88',
  '钻石会员专享 · 全场 9 折优惠',
  '推荐有奖 · 邀好友送 200 元奖励酒',
  '过量饮酒有害健康 · 未成年禁止购买',
];

// 兜底活动(API 失败或为空时)
const MOCK_ACTIVITIES: ActivityVO[] = [
  {
    id: 'mock-1',
    name: '竹香品鉴会 · 8月雅集',
    type: 'promotion',
    status: 'registering',
    description: '邀请酒友品鉴竹奕佳酿,赠限量礼盒',
  },
  {
    id: 'mock-2',
    name: '中秋团圆团购专场',
    type: 'groupbuy',
    status: 'ongoing',
    description: '企业团购满 5 万享 8 折',
  },
];

// 功能金刚区配置
const QUICK_ENTRIES = [
  { key: 'signin', icon: '✅', label: '每日签到' },
  { key: 'groupbuy', icon: '🛒', label: '企业团购' },
  { key: 'recharge', icon: '💰', label: '余额充值' },
  { key: 'activity', icon: '🎁', label: '活动中心' },
  { key: 'promotion', icon: '🤝', label: '推广赚钱' },
  { key: 'member', icon: '👑', label: '会员权益' },
  { key: 'orders', icon: '📦', label: '我的订单' },
  { key: 'service', icon: '🎧', label: '在线客服' },
];

const IndexPage: React.FC = () => {
  const [products, setProducts] = useState<ProductVO[]>([]);
  const [hotProducts, setHotProducts] = useState<ProductVO[]>([]);
  const [activities, setActivities] = useState<ActivityVO[]>([]);
  const [tiers, setTiers] = useState<GroupBuyTier[]>([]);
  const [noticeIdx, setNoticeIdx] = useState(0);
  const [points, setPoints] = useState<number>(0);
  const [signedInToday, setSignedInToday] = useState(false);

  useEffect(() => {
    (async () => {
      // 初始化 mock DB
      CheckoutService.resetMock();
      const db = CheckoutService.getMockDB();
      setProducts(db.products || []);

      // 并行加载: 热销推荐 / 活动 / 团购阶梯 / 会员积分
      const [hot, acts, groupTiers, member] = await Promise.all([
        ProductAPI.hot(4).catch(() => [] as ProductVO[]),
        PromotionAPI.activities({ limit: 3 }).catch(() => MOCK_ACTIVITIES),
        PromotionAPI.groupBuyTiers().catch(() => ({ tiers: [], rules: null })),
        MemberAPI.profile().catch(() => null),
      ]);
      setHotProducts(hot);
      setActivities(acts.length > 0 ? acts : MOCK_ACTIVITIES);
      setTiers(groupTiers.tiers);
      if (member) setPoints(member.points || 0);

      // 签到状态(本地存储)
      const today = new Date().toISOString().slice(0, 10);
      const lastSign = Taro.getStorageSync(SIGN_IN_STORAGE_KEY) as string;
      if (lastSign === today) setSignedInToday(true);
    })();

    // 公告轮播
    const timer = setInterval(() => {
      setNoticeIdx(i => (i + 1) % MOCK_NOTICES.length);
    }, NOTICE_INTERVAL_MS);
    return () => clearInterval(timer);
  }, []);

  // 跳转商品详情
  const handleBuy = (product: { id: string }) => {
    Taro.navigateTo({
      url: `/pages/product-detail/index?id=${product.id}`,
    });
  };

  // 功能金刚区点击
  const handleQuickEntry = (key: string) => {
    switch (key) {
      case 'signin':
        handleSignIn();
        break;
      case 'groupbuy':
        Taro.showToast({ title: `团购咨询客服: ${SERVICE_PHONE}`, icon: 'none' });
        break;
      case 'recharge':
        Taro.showToast({ title: '充值功能开发中', icon: 'none' });
        break;
      case 'activity':
        Taro.navigateTo({ url: '/pages/activity/index' });
        break;
      case 'promotion':
        Taro.navigateTo({ url: '/pages/promotion/index' });
        break;
      case 'member':
        Taro.switchTab({ url: '/pages/mine/index' });
        break;
      case 'orders':
        Taro.navigateTo({ url: '/pages/orders/index' });
        break;
      case 'service':
        Taro.showToast({ title: `客服热线: ${SERVICE_PHONE}`, icon: 'none' });
        break;
    }
  };

  // 签到: 奖励积分(本地存储记录)
  const handleSignIn = () => {
    if (signedInToday) {
      Taro.showToast({ title: '今日已签到', icon: 'none' });
      return;
    }
    const today = new Date().toISOString().slice(0, 10);
    Taro.setStorageSync(SIGN_IN_STORAGE_KEY, today);
    setSignedInToday(true);
    setPoints(p => p + SIGN_IN_REWARD_POINTS);
    Taro.showToast({ title: `签到成功 +${SIGN_IN_REWARD_POINTS} 积分`, icon: 'success' });
  };

  // 跳转商品列表
  const goProducts = () => {
    Taro.switchTab({ url: '/pages/products/index' });
  };

  return (
    <View className={styles.page}>
      {/* 品牌头部 */}
      <View className={styles.header}>
        <View className={styles.brandTitle}>竹香酒</View>
        <View className={styles.brandDesc}>竹韵佳酿 · 雅致生活</View>
        <View className={styles.warning}>过量饮酒有害健康</View>
      </View>

      <View className={styles.body}>
        {/* 公告条 */}
        <View className={styles.noticeBar}>
          <Text className={styles.noticeIcon}>📢</Text>
          <Text className={styles.noticeText}>{MOCK_NOTICES[noticeIdx]}</Text>
        </View>

        {/* 功能金刚区 */}
        <View className={styles.quickGrid}>
          {QUICK_ENTRIES.map(item => (
            <View
              key={item.key}
              className={styles.quickItem}
              onClick={() => handleQuickEntry(item.key)}
            >
              <View className={styles.quickIcon}>{item.icon}</View>
              <View className={styles.quickLabel}>{item.label}</View>
            </View>
          ))}
        </View>

        {/* 签到卡片 */}
        <View className={styles.signinCard}>
          <View className={styles.signinLeft}>
            <View className={styles.signinTitle}>每日签到</View>
            <View className={styles.signinDesc}>
              {signedInToday ? '今日已签到,明天再来' : '签到领积分 · 兑好礼'}
            </View>
            <View className={styles.signinPoints}>我的积分: {points}</View>
          </View>
          <View
            className={`${styles.signinBtn} ${signedInToday ? styles.signed : ''}`}
            onClick={handleSignIn}
          >
            {signedInToday ? '已签到' : '签到'}
          </View>
        </View>

        {/* 活动横幅 */}
        <View className={styles.section}>
          <View className={styles.sectionHeader}>
            <Text className={styles.sectionTitle}>活动专区</Text>
            <Text className={styles.sectionMore}>查看更多 ›</Text>
          </View>
          <ScrollView scrollX className={styles.activityScroll}>
            {activities.map(act => (
              <View key={act.id} className={styles.activityCard}>
                <View className={styles.activityName}>{act.name}</View>
                <View className={styles.activityDesc}>
                  {act.description || '精彩活动进行中'}
                </View>
                <View className={styles.activityBadge}>{act.status}</View>
              </View>
            ))}
          </ScrollView>
        </View>

        {/* 团购阶梯 */}
        {tiers.length > 0 && (
          <View className={styles.section}>
            <View className={styles.sectionHeader}>
              <Text className={styles.sectionTitle}>企业团购 · 阶梯折扣</Text>
              <Text className={styles.sectionMore} onClick={goProducts}>选品 ›</Text>
            </View>
            <View className={styles.tierRow}>
              {tiers.map(t => (
                <View key={t.tier} className={styles.tierCard}>
                  <View className={styles.tierName}>{t.tier}</View>
                  <View className={styles.tierDiscount}>{t.discountRate}</View>
                  <View className={styles.tierAmount}>
                    ¥{(t.minAmount / 10000).toFixed(0)}万起
                  </View>
                </View>
              ))}
            </View>
          </View>
        )}

        {/* 热销推荐位 */}
        {hotProducts.length > 0 && (
          <View className={styles.section}>
            <View className={styles.sectionHeader}>
              <Text className={styles.sectionTitle}>热销推荐</Text>
              <Text className={styles.sectionMore} onClick={goProducts}>全部 ›</Text>
            </View>
            <ScrollView scrollX className={styles.hotScroll}>
              {hotProducts.map(p => (
                <View
                  key={p.id}
                  className={styles.hotCard}
                  onClick={() => handleBuy(p)}
                >
                  <View className={styles.hotThumb}>🍶</View>
                  <View className={styles.hotName}>{p.name}</View>
                  <View className={styles.hotSpec}>{p.spec}</View>
                  <View className={styles.hotPrice}>
                    <Text className={styles.hotSymbol}>¥</Text>
                    <Text className={styles.hotValue}>{p.price}</Text>
                  </View>
                </View>
              ))}
            </ScrollView>
          </View>
        )}

        {/* 全部商品 */}
        <View className={styles.section}>
          <View className={styles.sectionHeader}>
            <Text className={styles.sectionTitle}>全部商品</Text>
            <Text className={styles.sectionMore} onClick={goProducts}>更多 ›</Text>
          </View>
          <View className={styles.productList}>
            {products.map(p => (
              <View
                key={p.id}
                className={styles.productCard}
                onClick={() => handleBuy(p)}
              >
                <View className={styles.productInfo}>
                  <View className={styles.productName}>{p.name}</View>
                  <View className={styles.productPrice}>¥{p.price}</View>
                  <View className={styles.productStock}>库存 {p.stock} 瓶</View>
                </View>
                <View
                  className={styles.buyButton}
                  onClick={(e) => { e.stopPropagation(); handleBuy(p); }}
                >
                  立即购买
                </View>
              </View>
            ))}
          </View>
        </View>
      </View>
    </View>
  );
};

export default IndexPage;
