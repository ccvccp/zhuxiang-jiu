import React, { useState, useEffect } from 'react';
import { View, Text, ScrollView, Image } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import styles from './index.module.scss';
import CheckoutService from '@/services/checkout-service';
import { ProductAPI, ProductVO } from '@/api/product';
import { PromotionAPI, ActivityVO, GroupBuyTier } from '@/api/promotion';
import { MemberAPI } from '@/api/member';
import { WalletAPI } from '@/api/wallet';
import { CreditAPI } from '@/api/credit';
import { LocationAPI, NearbyStoreVO } from '@/api/location';
import { VenueAPI, VenuePartnerVO } from '@/api/venue';
import { applyActiveTheme, getQuickGridIcon } from '@/services/theme-service';
import {
  SIGN_IN_REWARD_POINTS,
  SIGN_IN_STORAGE_KEY,
  NOTICE_INTERVAL_MS,
} from '@/config';
import { PointsAPI } from '@/api/points';
import { getMemberId, isLoggedIn, requireLogin } from '@/services/auth-service';

// 公告轮播文案(API 无公告接口,降级 mock)
const MOCK_NOTICES = [
  '竹香佳酿新品上市 · 限时尝鲜价 ¥88',
  '钻石会员专享 · 全场 9 折优惠',
  '扫码赚钱 · 邀好友购物现金奖励可叠加',
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
    description: '组团团购满 5 万享 8 折',
  },
];

// 活动状态徽标中文映射(与活动中心页 STATUS_MAP 对齐)
const ACTIVITY_STATUS_TEXT: Record<string, string> = {
  draft: '筹备中',
  registering: '报名中',
  ongoing: '进行中',
  ended: '已结束',
  cancelled: '已取消',
};

// 默认定位兜底(济南, 与代驾/场馆页预设一致; H5 定位失败/拒绝时)
const DEFAULT_LNG = 117.1201;
const DEFAULT_LAT = 36.6612;

// 功能金刚区(3 行 × 5 = 15 入口)
// Row1 高频交易 / Row2 生态联盟 / Row3 服务与赚钱
const QUICK_ENTRIES = [
  { key: 'signin', icon: '✅', label: '每日签到' },
  { key: 'flash', icon: '⚡', label: '限时秒杀' },
  { key: 'groupbuy', icon: '🛒', label: '组团团购' },
  { key: 'recycle', icon: '🍶', label: '老酒回收' },
  { key: 'activity', icon: '🎁', label: '活动中心' },
  { key: 'citystore', icon: '🏙️', label: '市级网店' },
  { key: 'alliance', icon: '🤝', label: '同盟商城' },
  { key: 'venue', icon: '🏨', label: '场馆合作' },
  { key: 'ride', icon: '🚗', label: '代驾联盟' },
  { key: 'trace', icon: '🔍', label: '溯源验真' },
  { key: 'promotion', icon: '📱', label: '扫码赚钱' },
  { key: 'pocket', icon: '🤲', label: '顺手赚钱' },
  { key: 'pointsmall', icon: '🎁', label: '积分商城' },
  { key: 'orders', icon: '📦', label: '我的订单' },
  { key: 'service', icon: '🎧', label: '在线客服' },
  { key: 'xinzhi', icon: '🏅', label: '信值臻选' },
  { key: 'map', icon: '🗺️', label: '智图地图' },
];

const IndexPage: React.FC = () => {
  const [products, setProducts] = useState<ProductVO[]>([]);
  const [hotProducts, setHotProducts] = useState<ProductVO[]>([]);
  const [activities, setActivities] = useState<ActivityVO[]>([]);
  const [tiers, setTiers] = useState<GroupBuyTier[]>([]);
  const [noticeIdx, setNoticeIdx] = useState(0);
  const [signedInToday, setSignedInToday] = useState(false);
  // 资产概览(登录态加载; 游客显示登录引导)
  const [points, setPoints] = useState<number>(0);
  const [balance, setBalance] = useState<number | null>(null);
  const [creditQuota, setCreditQuota] = useState<number | null>(null);
  // 附近推荐 / 好店推荐
  const [nearbyStores, setNearbyStores] = useState<NearbyStoreVO[]>([]);
  const [goodShops, setGoodShops] = useState<VenuePartnerVO[]>([]);
  // 主题图标覆盖就绪标记(拉取到主题后触发重渲染)
  const [, setThemeTick] = useState(0);
  // 页面级初始化只跑一次(商品/活动等公开数据)
  const [initialized, setInitialized] = useState(false);

  // 会员数据加载(登录态才拉——游客跳过, 避免 401 强制跳登录)
  const loadMemberData = async () => {
    if (!isLoggedIn()) {
      setPoints(0);
      setBalance(null);
      setCreditQuota(null);
      setSignedInToday(false);
      return;
    }
    const memberId = Number(getMemberId());
    try {
      const [member, signinRecords, wallet, quota] = await Promise.all([
        MemberAPI.profile(),
        // 签到今日状态以后端记录为准(本地存储仅离线兜底)
        PointsAPI.signinRecords(memberId, 7).catch(() => null),
        // 资产概览: 钱包余额 + 信用可用额度(失败不阻塞)
        WalletAPI.info().catch(() => null),
        CreditAPI.quota().catch(() => null),
      ]);
      setPoints(member.points || 0);
      setBalance(wallet ? (wallet as any).currentBalance ?? null : null);
      setCreditQuota(quota ? quota.availableQuota : null);
      const today = new Date().toISOString().slice(0, 10);
      const signedToday = (signinRecords || []).some(r => r.signDate === today);
      if (signedToday) {
        setSignedInToday(true);
        Taro.setStorageSync(SIGN_IN_STORAGE_KEY, today);
      } else {
        // 后端记录未签 → 以本地记录兜底(后端不可达时不误判)
        const lastSign = Taro.getStorageSync(SIGN_IN_STORAGE_KEY) as string;
        setSignedInToday(lastSign === today);
      }
    } catch (e) {
      console.warn('[index] 会员信息加载失败:', e);
      // 会员态接口失败时本地兜底
      const today = new Date().toISOString().slice(0, 10);
      const lastSign = Taro.getStorageSync(SIGN_IN_STORAGE_KEY) as string;
      setSignedInToday(lastSign === today);
    }
  };

  // 页面每次显示时刷新会员数据(登录返回/切 tab 回来资产即时同步)
  useDidShow(() => {
    loadMemberData();
  });

  // 附近推荐: 定位(3s 超时兜底 + 默认坐标) → nearby 门店
  // 独立异步流(不阻塞首屏主数据 —— H5 定位权限挂起时热销/活动仍正常渲染)
  const loadNearbyStores = async () => {
    let lng = DEFAULT_LNG;
    let lat = DEFAULT_LAT;
    try {
      const loc = await Promise.race([
        Taro.getLocation({ type: 'wgs84' }),
        new Promise<null>(resolve => setTimeout(() => resolve(null), 3000)),
      ]);
      if (loc?.longitude && loc?.latitude) {
        lng = loc.longitude;
        lat = loc.latitude;
      }
    } catch (_) { /* 定位拒绝/失败/超时 → 默认坐标 */ }
    try {
      const stores = await LocationAPI.nearbyStores(lng, lat, 10, 10);
      setNearbyStores(stores);
    } catch (e) {
      console.warn('[index] 附近门店加载失败:', e);
      setNearbyStores([]);
    }
  };

  useEffect(() => {
    (async () => {
      // 初始化 mock DB
      CheckoutService.resetMock();
      const db = CheckoutService.getMockDB();
      setProducts(db.products || []);

      // 并行加载: 热销推荐 / 活动 / 团购阶梯 / 好店推荐(均公开接口)
      const [hot, acts, groupTiers, shops] = await Promise.all([
        ProductAPI.hot(4).catch(() => [] as ProductVO[]),
        PromotionAPI.activities({ limit: 3 }).catch(() => MOCK_ACTIVITIES),
        PromotionAPI.groupBuyTiers().catch(() => ({ tiers: [], rules: null })),
        VenueAPI.partners().catch(() => [] as VenuePartnerVO[]),
      ]);
      setHotProducts(hot);
      setActivities(acts.length > 0 ? acts : MOCK_ACTIVITIES);
      setTiers(groupTiers.tiers);
      setGoodShops(shops.slice(0, 10));
      setInitialized(true);

      // 主题图标覆盖: 主题拉取完成后强制刷新金刚区图标
      applyActiveTheme().then(() => setThemeTick(t => t + 1));

      // 附近推荐独立加载(定位可能挂起, 不阻塞首屏)
      loadNearbyStores();
    })();
  }, []);

  // 公告轮播
  useEffect(() => {
    const timer = setInterval(() => {
      setNoticeIdx(i => (i + 1) % MOCK_NOTICES.length);
    }, NOTICE_INTERVAL_MS);
    return () => clearInterval(timer);
  }, []);

  // 初始化完成后加载一次会员数据(与 useDidShow 首次触发互补)
  useEffect(() => {
    if (initialized) loadMemberData();
  }, [initialized]);

  // 跳转商品详情
  const handleBuy = (product: { id: string }) => {
    Taro.navigateTo({
      url: `/pages/product-detail/index?id=${product.id}`,
    });
  };

  // 签到
  const handleSignIn = async () => {
    if (signedInToday) {
      Taro.showToast({ title: '今日已签到', icon: 'none' });
      return;
    }
    try {
      const result = await PointsAPI.signIn();
      const bonusTip = result?.bonusPoints
        ? `, 连续签到奖励 +${result.bonusPoints}` : '';
      Taro.showToast({
        title: `签到成功 +${result.pointsEarned} 积分${bonusTip}`,
        icon: 'none',
      });
      setSignedInToday(true);
      Taro.setStorageSync(SIGN_IN_STORAGE_KEY, new Date().toISOString().slice(0, 10));
      await loadMemberData();
    } catch (e) {
      console.warn('[index] 签到失败:', e);
      const today = new Date().toISOString().slice(0, 10);
      const lastSign = Taro.getStorageSync(SIGN_IN_STORAGE_KEY) as string;
      if (lastSign === today) {
        setSignedInToday(true);
        Taro.showToast({ title: '今日已签到', icon: 'none' });
      }
    }
  };

  // 功能金刚区点击
  const handleQuickEntry = (key: string) => {
    switch (key) {
      case 'signin':
        handleSignIn();
        break;
      case 'flash':
        Taro.navigateTo({ url: '/pages/flashsale/index' });
        break;
      case 'groupbuy':
        Taro.navigateTo({ url: '/pages/groupbuy/index' });
        break;
      case 'recycle':
        Taro.navigateTo({ url: '/pages/recycle/index' });
        break;
      case 'activity':
        Taro.navigateTo({ url: '/pages/activity/index' });
        break;
      case 'promotion':
        Taro.navigateTo({ url: '/pages/promotion/index' });
        break;
      case 'pocket':
        Taro.navigateTo({ url: '/pages/pocket/index' });
        break;
      case 'pointsmall':
        Taro.navigateTo({ url: '/pages/pointsmall/index' });
        break;
      case 'citystore':
        Taro.navigateTo({ url: '/pages/citystore/index' });
        break;
      case 'alliance':
        Taro.navigateTo({ url: '/pages/alliance/index' });
        break;
      case 'venue':
        Taro.navigateTo({ url: '/pages/venue/index' });
        break;
      case 'ride':
        Taro.navigateTo({ url: '/pages/ride/index' });
        break;
      case 'trace':
        Taro.navigateTo({ url: '/pages/trace-view/index' });
        break;
      case 'orders':
        Taro.navigateTo({ url: '/pages/orders/index' });
        break;
      case 'service':
        Taro.navigateTo({ url: '/pages/chat/index' });
        break;
      case 'xinzhi':
        Taro.navigateTo({ url: '/pages/xinzhi/index' });
        break;
      case 'map':
        Taro.navigateTo({ url: '/pages/zt/index' });
        break;
    }
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

        {/* 功能金刚区(3 行 15 入口) */}
        <View className={styles.quickGrid}>
          {QUICK_ENTRIES.map(item => {
            const iconVal = getQuickGridIcon(item.key, item.icon);
            const isImg = typeof iconVal === 'string'
              && (iconVal.startsWith('data:image/') || iconVal.startsWith('http'));
            return (
              <View
                key={item.key}
                className={styles.quickItem}
                onClick={() => handleQuickEntry(item.key)}
              >
                <View className={styles.quickIcon}>
                  {isImg
                    ? <Image src={iconVal} className={styles.quickIconImg} mode='aspectFit' />
                    : iconVal}
                </View>
                <View className={styles.quickLabel}>{item.label}</View>
              </View>
            );
          })}
        </View>

        {/* 资产概览卡(登录态展示; 游客引导登录) */}
        <View className={styles.assetCard}>
          <View className={styles.assetHeader}>
            <Text className={styles.assetTitle}>我的资产</Text>
            {isLoggedIn() && (
              <Text
                className={styles.assetMore}
                onClick={() => Taro.navigateTo({ url: '/pages/wallet/index' })}
              >
                钱包 ›
              </Text>
            )}
          </View>
          {isLoggedIn() ? (
            <View className={styles.assetGrid}>
              <View
                className={styles.assetItem}
                onClick={() => Taro.navigateTo({ url: '/pages/pointsmall/index' })}
              >
                <View className={styles.assetValue}>{points}</View>
                <View className={styles.assetLabel}>积分</View>
              </View>
              <View
                className={styles.assetItem}
                onClick={() => Taro.navigateTo({ url: '/pages/trust/index' })}
              >
                <View className={styles.assetValue}>开通</View>
                <View className={styles.assetLabel}>信值</View>
              </View>
              <View
                className={styles.assetItem}
                onClick={() => Taro.navigateTo({ url: '/pages/wallet/index' })}
              >
                <View className={styles.assetValue}>
                  {balance != null ? balance.toFixed(2) : '--'}
                </View>
                <View className={styles.assetLabel}>余额(元)</View>
              </View>
              <View
                className={styles.assetItem}
                onClick={() => Taro.navigateTo({ url: '/pages/credit/index' })}
              >
                <View className={styles.assetValue}>
                  {creditQuota != null ? creditQuota.toFixed(0) : '--'}
                </View>
                <View className={styles.assetLabel}>信用额度</View>
              </View>
            </View>
          ) : (
            <View
              className={styles.assetLoginBtn}
              onClick={() => Taro.navigateTo({ url: '/pages/login/index' })}
            >
              登录查看积分 · 信值 · 余额 · 信用额度
            </View>
          )}
        </View>

        {/* 附近推荐(定位 → 附近门店, 距离排序) */}
        <View className={styles.section}>
          <View className={styles.sectionHeader}>
            <Text className={styles.sectionTitle}>📍 附近推荐</Text>
          </View>
          {nearbyStores.length === 0 ? (
            <View className={styles.nearbyEmpty}>
              门店网络建设中 · 更多竹香门店即将开业
            </View>
          ) : (
            <ScrollView scrollX className={styles.nearbyScroll}>
              {nearbyStores.map(s => (
                <View key={s.id} className={styles.nearbyCard}>
                  <View className={styles.nearbyName}>{s.storeName}</View>
                  <View className={styles.nearbyMeta}>
                    {s.storeType}{s.city ? ` · ${s.city}` : ''}
                  </View>
                  <View className={styles.nearbyAddr}>{s.address}</View>
                  <View className={styles.nearbyDist}>{s.distance.toFixed(1)} km</View>
                </View>
              ))}
            </ScrollView>
          )}
        </View>

        {/* 好店推荐(合作场馆联盟) */}
        <View className={styles.section}>
          <View className={styles.sectionHeader}>
            <Text className={styles.sectionTitle}>🏆 好店推荐</Text>
            <Text
              className={styles.sectionMore}
              onClick={() => Taro.navigateTo({ url: '/pages/venue/index' })}
            >
              全部 ›
            </Text>
          </View>
          {goodShops.length === 0 ? (
            <View className={styles.nearbyEmpty}>
              好店入驻中 · 酒店酒吧会所合作申请开放
            </View>
          ) : (
            <ScrollView scrollX className={styles.nearbyScroll}>
              {goodShops.map(p => (
                <View
                  key={p.id}
                  className={styles.nearbyCard}
                  onClick={() => Taro.navigateTo({ url: '/pages/venue/index' })}
                >
                  <View className={styles.nearbyName}>{p.partnerName}</View>
                  <View className={styles.nearbyMeta}>
                    {p.partnerLevel} 级合作 · 品鉴酒 {(p.tastingRate * 100).toFixed(0)}%
                  </View>
                  <View className={styles.nearbyAddr}>{p.contactAddress}</View>
                </View>
              ))}
            </ScrollView>
          )}
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
                <View className={styles.activityBadge}>{ACTIVITY_STATUS_TEXT[act.status] || act.status}</View>
              </View>
            ))}
          </ScrollView>
        </View>

        {/* 团购阶梯 */}
        {tiers.length > 0 && (
          <View className={styles.section}>
            <View className={styles.sectionHeader}>
              <Text className={styles.sectionTitle}>组团团购 · 阶梯折扣</Text>
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
