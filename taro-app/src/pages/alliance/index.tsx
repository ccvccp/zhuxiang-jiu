/**
 * 同盟商城 · 酒水不分家(水茶酒菜肉鱼器境)
 * 商城浏览(类目筛选) → 下单 → 我的订单(评价) → 场景(酒友小聚/核销/定制) → 我的商铺(入盟)
 * 数据来源: 后端 /api/alliance/*
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input, Textarea, Picker } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  AllianceAPI, AllianceProductVO, AllianceOrderVO, AllianceMerchantVO,
  SceneVO, CustomDemandVO,
  categoryName, merchantStatusName, orderStatusName, CATEGORY_NAME,
  sceneStatusName, demandStatusName, demandTypeName,
} from '@/api/alliance';
import { MemberAPI } from '@/api/member';
import { requireLogin } from '@/services/auth-service';

type Tab = 'mall' | 'orders' | 'scene' | 'merchant';

const TABS: { key: Tab; label: string }[] = [
  { key: 'mall', label: '商城' },
  { key: 'orders', label: '我的订单' },
  { key: 'scene', label: '场景' },
  { key: 'merchant', label: '我的商铺' },
];

// 类目 tabs(全部 + 8 类)
const CATEGORY_TABS = ['', ...Object.keys(CATEGORY_NAME)];

// 场景子菜单
type SceneTab = 'gathering' | 'demands';
const SCENE_TABS: { key: SceneTab; label: string }[] = [
  { key: 'gathering', label: '酒友小聚' },
  { key: 'demands', label: '定制需求' },
];

// 定制类型选项
const DEMAND_TYPES = ['engraving', 'private_feast', 'sealing'];

const formatDate = (t?: string): string => (t ? t.slice(0, 10) : '');

const AlliancePage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('mall');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  // 商城
  const [category, setCategory] = useState('');
  const [products, setProducts] = useState<AllianceProductVO[]>([]);
  // 我的订单
  const [orders, setOrders] = useState<AllianceOrderVO[]>([]);
  const [productMap, setProductMap] = useState<Record<number, AllianceProductVO>>({});
  // 我的商铺
  const [merchant, setMerchant] = useState<AllianceMerchantVO | null>(null);
  const [memberLevel, setMemberLevel] = useState(0);
  // 购买弹层
  const [buyProduct, setBuyProduct] = useState<AllianceProductVO | null>(null);
  const [buyQty, setBuyQty] = useState('1');
  // 评价弹层
  const [reviewOrder, setReviewOrder] = useState<AllianceOrderVO | null>(null);
  const [reviewScore, setReviewScore] = useState(5);
  const [reviewContent, setReviewContent] = useState('');
  // 入盟弹层
  const [showJoin, setShowJoin] = useState(false);
  const [joinCategory, setJoinCategory] = useState('');
  const [joinShopName, setJoinShopName] = useState('');
  // 场景 tab
  const [sceneTab, setSceneTab] = useState<SceneTab>('gathering');
  const [scenes, setScenes] = useState<SceneVO[]>([]);
  const [demands, setDemands] = useState<CustomDemandVO[]>([]);
  // 小聚表单
  const [gPartySize, setGPartySize] = useState('4');
  const [gTime, setGTime] = useState('');
  const [wineProducts, setWineProducts] = useState<AllianceProductVO[]>([]);
  const [dishProducts, setDishProducts] = useState<AllianceProductVO[]>([]);
  const [venueProducts, setVenueProducts] = useState<AllianceProductVO[]>([]);
  const [gWineIdx, setGWineIdx] = useState(-1);
  const [gDishIdx, setGDishIdx] = useState(-1);
  const [gVenueIdx, setGVenueIdx] = useState(-1);
  // 核销输入
  const [redeemCode, setRedeemCode] = useState('');
  // 定制表单
  const [demandTypeIdx, setDemandTypeIdx] = useState(0);
  const [demandDesc, setDemandDesc] = useState('');
  const [demandBudget, setDemandBudget] = useState('');

  const loadMall = useCallback(async (cat: string) => {
    try {
      const list = await AllianceAPI.products(cat || undefined);
      setProducts(list);
    } catch (e) {
      console.warn('[alliance] 商品加载失败:', e);
      setProducts([]);
    }
  }, []);

  const loadOrders = useCallback(async () => {
    try {
      const list = await AllianceAPI.myOrders();
      setOrders(list);
      // 拉商品名映射(去重)
      const ids = Array.from(new Set(list.map(o => o.productId)));
      const map: Record<number, AllianceProductVO> = { ...productMap };
      await Promise.all(ids.filter(id => !map[id]).map(async (id) => {
        try {
          map[id] = await AllianceAPI.productDetail(id);
        } catch (_) { /* 商品可能已下架, 忽略 */ }
      }));
      setProductMap({ ...map });
    } catch (e) {
      console.warn('[alliance] 订单加载失败:', e);
      setOrders([]);
    }
  }, [productMap]);

  const loadMerchant = useCallback(async () => {
    try {
      const m = await AllianceAPI.myMerchant();
      setMerchant(m);
    } catch (e) {
      console.warn('[alliance] 商铺档案加载失败:', e);
      setMerchant(null);
    }
  }, []);

  /** 场景 tab 数据: 我的场景单 + 我的定制 + 三类目商品(选酒/配菜/订境) */
  const loadSceneData = useCallback(async () => {
    const [sc, dm, wines, dishes, venues] = await Promise.all([
      AllianceAPI.myScenes().catch(() => [] as SceneVO[]),
      AllianceAPI.myDemands().catch(() => [] as CustomDemandVO[]),
      AllianceAPI.products('wine').catch(() => [] as AllianceProductVO[]),
      AllianceAPI.products('dish').catch(() => [] as AllianceProductVO[]),
      AllianceAPI.products('venue').catch(() => [] as AllianceProductVO[]),
    ]);
    setScenes(sc);
    setDemands(dm);
    setWineProducts(wines);
    setDishProducts(dishes);
    setVenueProducts(venues);
  }, []);

  useEffect(() => {
    if (!requireLogin()) {
      setLoading(false);
      return;
    }
    (async () => {
      try {
        const [profile] = await Promise.all([
          MemberAPI.profile().catch(() => null),
        ]);
        setMemberLevel(profile ? Number(String(profile.level).replace('L', '')) || 0 : 0);
        await Promise.all([loadMall(category), loadOrders(), loadMerchant(), loadSceneData()]);
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 切换类目
  const handleCategory = async (cat: string) => {
    setCategory(cat);
    await loadMall(cat);
  };

  // 打开购买面板
  const openBuy = (p: AllianceProductVO) => {
    if (p.stock <= 0) {
      Taro.showToast({ title: '库存不足', icon: 'none' });
      return;
    }
    setBuyProduct(p);
    setBuyQty('1');
  };

  // 下单
  const handleBuy = async () => {
    if (!buyProduct || submitting) return;
    const qty = Number(buyQty);
    if (!qty || qty < 1) {
      Taro.showToast({ title: '请输入购买数量', icon: 'none' });
      return;
    }
    if (qty > buyProduct.stock) {
      Taro.showToast({ title: `库存不足(剩余 ${buyProduct.stock})`, icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const order = await AllianceAPI.placeOrder(buyProduct.productId, qty);
      Taro.showToast({ title: `下单成功 ¥${order.amount.toFixed(2)}`, icon: 'success', duration: 2000 });
      setBuyProduct(null);
      await loadMall(category);
      await loadOrders();
    } catch (e) {
      console.warn('[alliance] 下单失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 打开评价面板
  const openReview = (o: AllianceOrderVO) => {
    setReviewOrder(o);
    setReviewScore(5);
    setReviewContent('');
  };

  // 提交评价
  const handleReview = async () => {
    if (!reviewOrder || submitting) return;
    setSubmitting(true);
    try {
      await AllianceAPI.submitReview({
        orderId: reviewOrder.orderId,
        score: reviewScore,
        content: reviewContent.trim(),
      });
      Taro.showToast({ title: '评价已提交', icon: 'success' });
      setReviewOrder(null);
    } catch (e) {
      console.warn('[alliance] 评价失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 打开入盟面板
  const openJoin = () => {
    if (memberLevel < 4) {
      Taro.showModal({
        title: '暂不可入盟',
        content: `入盟须超级会员(Lv4+), 当前等级 Lv${memberLevel}。升级会员后即可申请。`,
        showCancel: false,
      });
      return;
    }
    setJoinCategory('');
    setJoinShopName('');
    setShowJoin(true);
  };

  // 提交入盟申请
  const handleJoin = async () => {
    if (submitting) return;
    if (!joinCategory) {
      Taro.showToast({ title: '请选择经营类目', icon: 'none' });
      return;
    }
    if (!joinShopName.trim()) {
      Taro.showToast({ title: '请输入店铺名称', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const res = await AllianceAPI.apply({
        category: joinCategory,
        shopName: joinShopName.trim(),
        credentials: [],
      });
      const app = (res && res.data) || {};
      const aiScore = app.aiReview?.score;
      Taro.showModal({
        title: '申请已提交',
        content: aiScore != null
          ? `AI 预审评分 ${aiScore} 分, ${aiScore >= 80 ? '进入快车道' : '转人工重点审核'}。`
          : '等待平台审核。',
        showCancel: false,
      });
      setShowJoin(false);
      await loadMerchant();
    } catch (e) {
      console.warn('[alliance] 入盟申请失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 酒友小聚编排出单
  const handleGathering = async () => {
    if (submitting) return;
    const partySize = Number(gPartySize);
    if (!partySize || partySize < 1 || partySize > 50) {
      Taro.showToast({ title: '人数须为 1-50', icon: 'none' });
      return;
    }
    const wine = gWineIdx >= 0 ? wineProducts[gWineIdx] : null;
    if (!wine) {
      Taro.showToast({ title: '请选择好酒(在售 wine 类目)', icon: 'none' });
      return;
    }
    const dish = gDishIdx >= 0 ? dishProducts[gDishIdx] : null;
    if (!dish) {
      Taro.showToast({ title: '请选择配菜商户(在售 dish 类目)', icon: 'none' });
      return;
    }
    const venue = gVenueIdx >= 0 ? venueProducts[gVenueIdx] : null;
    if (!venue) {
      Taro.showToast({ title: '请选择订境商户(在售 venue 类目)', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const scene = await AllianceAPI.createGathering({
        partySize,
        wineProductId: wine.productId,
        dishMerchantId: dish.merchantId,
        venueMerchantId: venue.merchantId,
        gatheringTime: gTime.trim(),
      });
      Taro.showModal({
        title: '小聚单已生成',
        content: `合计 ¥${scene.totalAmount.toFixed(2)}, 核销码 ${scene.redeemCode}(72 小时内到店核销有效)。`,
        showCancel: false,
      });
      setGPartySize('4');
      setGTime('');
      setGWineIdx(-1);
      setGDishIdx(-1);
      setGVenueIdx(-1);
      await loadSceneData();
    } catch (e) {
      console.warn('[alliance] 小聚下单失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 复制核销码
  const handleCopyCode = (code: string) => {
    Taro.setClipboardData({ data: code });
  };

  // 线下核销
  const handleRedeem = async (code: string) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await AllianceAPI.redeem(code);
      Taro.showToast({ title: '核销成功, 分润已起算', icon: 'success', duration: 2000 });
      await loadSceneData();
    } catch (e) {
      console.warn('[alliance] 核销失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 提交定制需求
  const handleDemand = async () => {
    if (submitting) return;
    const merchantId = dishesMerchantIdForDemand();
    if (!merchantId) {
      Taro.showToast({ title: '暂无在营商户可接定制', icon: 'none' });
      return;
    }
    if (!demandDesc.trim()) {
      Taro.showToast({ title: '请描述定制需求', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const d = await AllianceAPI.createDemand({
        merchantId,
        demandType: DEMAND_TYPES[demandTypeIdx],
        description: demandDesc.trim(),
        budget: Number(demandBudget) || 0,
      });
      Taro.showToast({
        title: `需求已提交(${demandTypeName(d.demandType)}), 等待商户报价`,
        icon: 'none',
        duration: 2500,
      });
      setDemandDesc('');
      setDemandBudget('');
      await loadSceneData();
    } catch (e) {
      console.warn('[alliance] 定制需求提交失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 定制需求目标商户: 优先当前登录会员的商铺, 否则取 dish 类目在售商品所属商户
  const dishesMerchantIdForDemand = (): number => {
    if (merchant && (merchant.status === 'active' || merchant.status === 'probation')) {
      return merchant.merchantId;
    }
    return dishProducts[0]?.merchantId ?? 0;
  };

  // 确认定制报价
  const handleConfirmDemand = async (d: CustomDemandVO) => {
    if (submitting) return;
    const res = await Taro.showModal({
      title: '确认报价',
      content: `${demandTypeName(d.demandType)}报价 ¥${d.quotedPrice.toFixed(2)}, 确认后进入制作。`,
    });
    if (!res.confirm) return;
    setSubmitting(true);
    try {
      await AllianceAPI.confirmDemand(d.demandId);
      Taro.showToast({ title: '已确认, 进入制作', icon: 'success' });
      await loadSceneData();
    } catch (e) {
      console.warn('[alliance] 确认报价失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <View className={styles.page}>
      <NavBar title="同盟商城" />
      {/* 顶部 Tabs */}
      <View className={styles.tabBar}>
        {TABS.map(t => (
          <View
            key={t.key}
            className={`${styles.tabItem} ${tab === t.key ? styles.tabItemActive : ''}`}
            onClick={async () => {
              setTab(t.key);
              if (t.key === 'mall') await loadMall(category);
              if (t.key === 'orders') await loadOrders();
              if (t.key === 'scene') await loadSceneData();
              if (t.key === 'merchant') await loadMerchant();
            }}
          >
            {t.label}
          </View>
        ))}
      </View>

      <ScrollView scrollY className={styles.scrollView}>
        {loading ? (
          <View className={styles.empty}>加载中...</View>
        ) : tab === 'mall' ? (
          <>
            {/* 类目筛选 */}
            <ScrollView scrollX className={styles.catBar}>
              {CATEGORY_TABS.map(c => (
                <View
                  key={c || 'all'}
                  className={`${styles.catItem} ${category === c ? styles.catItemActive : ''}`}
                  onClick={() => handleCategory(c)}
                >
                  {c ? categoryName(c) : '全部'}
                </View>
              ))}
            </ScrollView>

            {/* 商品列表 */}
            {products.length === 0 ? (
              <View className={styles.empty}>
                <View className={styles.emptyIcon}>🏪</View>
                <View>该类目暂无在售商品</View>
              </View>
            ) : (
              products.map(p => (
                <View key={p.productId} className={styles.prodCard}>
                  <View className={styles.prodTop}>
                    <View className={styles.prodCat}>{categoryName(p.category)}</View>
                    {p.trace.traceVerified && (
                      <View className={styles.traceBadge}>溯源已验</View>
                    )}
                  </View>
                  <View className={styles.prodName}>{p.name}</View>
                  <View className={styles.prodDesc}>{p.description}</View>
                  <View className={styles.prodBottom}>
                    <View className={styles.prodPriceWrap}>
                      <Text className={styles.prodPrice}>¥{p.price.toFixed(2)}</Text>
                      <Text className={styles.prodStock}>库存 {p.stock}</Text>
                    </View>
                    <View className={styles.buyBtn} onClick={() => openBuy(p)}>购买</View>
                  </View>
                </View>
              ))
            )}
          </>
        ) : tab === 'orders' ? (
          <View className={styles.card}>
            <View className={styles.cardTitle}>同盟订单</View>
            {orders.length === 0 ? (
              <View className={styles.empty}>
                <View className={styles.emptyIcon}>🧾</View>
                <View>暂无订单, 去商城看看吧</View>
              </View>
            ) : (
              orders.map(o => (
                <View key={o.orderId} className={styles.orderRow}>
                  <View className={styles.orderLeft}>
                    <View className={styles.orderName}>
                      {productMap[o.productId]?.name || `商品 ${o.productId}`}
                    </View>
                    <View className={styles.orderMeta}>
                      {o.orderId} · ×{o.quantity} · {formatDate(o.createdAt)}
                    </View>
                    <View className={styles.orderMeta}>
                      {orderStatusName(o.status)}{o.settled ? ' · 已结算' : ' · 待结算'}
                    </View>
                  </View>
                  <View className={styles.orderRight}>
                    <View className={styles.orderAmount}>¥{o.amount.toFixed(2)}</View>
                    {o.settled && (
                      <View className={styles.reviewBtn} onClick={() => openReview(o)}>评价</View>
                    )}
                  </View>
                </View>
              ))
            )}
          </View>
        ) : tab === 'scene' ? (
          <>
            {/* 场景子菜单 */}
            <View className={styles.sceneTabs}>
              {SCENE_TABS.map(st => (
                <View
                  key={st.key}
                  className={`${styles.sceneTab} ${sceneTab === st.key ? styles.sceneTabActive : ''}`}
                  onClick={() => setSceneTab(st.key)}
                >
                  {st.label}
                </View>
              ))}
            </View>

            {sceneTab === 'gathering' ? (
              <>
                {/* 酒友小聚表单 */}
                <View className={styles.card}>
                  <View className={styles.cardTitle}>酒友小聚 · 好酒配好菜配好境</View>
                  <View className={styles.sheetDesc}>
                    一单三子单(酒+菜+境)合并结算, 到店出示核销码完成履约
                  </View>
                  <Picker
                    mode="selector"
                    range={wineProducts.length ? wineProducts.map(p => `${p.name} ¥${p.price.toFixed(2)}`) : ['暂无在售好酒']}
                    value={gWineIdx >= 0 ? gWineIdx : 0}
                    onChange={(e) => setGWineIdx(Number((e.detail as any).value))}
                  >
                    <View className={styles.pickerRow}>
                      <Text className={styles.pickerLabel}>选好酒</Text>
                      <Text className={gWineIdx >= 0 ? styles.pickerValue : styles.pickerPlaceholder}>
                        {gWineIdx >= 0 && wineProducts[gWineIdx]
                          ? `${wineProducts[gWineIdx].name} ¥${wineProducts[gWineIdx].price.toFixed(2)}`
                          : '选择 wine 类目在售商品'}
                      </Text>
                      <Text className={styles.pickerArrow}>›</Text>
                    </View>
                  </Picker>
                  <Picker
                    mode="selector"
                    range={dishProducts.length ? dishProducts.map(p => `商户#${p.merchantId} · ${p.name}`) : ['暂无在售配菜']}
                    value={gDishIdx >= 0 ? gDishIdx : 0}
                    onChange={(e) => setGDishIdx(Number((e.detail as any).value))}
                  >
                    <View className={styles.pickerRow}>
                      <Text className={styles.pickerLabel}>配菜商户</Text>
                      <Text className={gDishIdx >= 0 ? styles.pickerValue : styles.pickerPlaceholder}>
                        {gDishIdx >= 0 && dishProducts[gDishIdx]
                          ? `商户#${dishProducts[gDishIdx].merchantId} · ${dishProducts[gDishIdx].name}`
                          : '选择 dish 类目商户(按人数配菜)'}
                      </Text>
                      <Text className={styles.pickerArrow}>›</Text>
                    </View>
                  </Picker>
                  <Picker
                    mode="selector"
                    range={venueProducts.length ? venueProducts.map(p => `商户#${p.merchantId} · ${p.name}`) : ['暂无在售订境']}
                    value={gVenueIdx >= 0 ? gVenueIdx : 0}
                    onChange={(e) => setGVenueIdx(Number((e.detail as any).value))}
                  >
                    <View className={styles.pickerRow}>
                      <Text className={styles.pickerLabel}>订境商户</Text>
                      <Text className={gVenueIdx >= 0 ? styles.pickerValue : styles.pickerPlaceholder}>
                        {gVenueIdx >= 0 && venueProducts[gVenueIdx]
                          ? `商户#${venueProducts[gVenueIdx].merchantId} · ${venueProducts[gVenueIdx].name}`
                          : '选择 venue 类目商户(时段包间)'}
                      </Text>
                      <Text className={styles.pickerArrow}>›</Text>
                    </View>
                  </Picker>
                  <View className={styles.gatherRow}>
                    <View className={styles.gatherField}>
                      <Text className={styles.pickerLabel}>人数</Text>
                      <Input
                        className={styles.gatherInput}
                        type="number"
                        value={gPartySize}
                        onInput={(e) => setGPartySize((e.detail as any).value)}
                        placeholder="1-50"
                        placeholderClass={styles.placeholder}
                      />
                    </View>
                    <View className={styles.gatherField}>
                      <Text className={styles.pickerLabel}>时间</Text>
                      <Input
                        className={styles.gatherInput}
                        value={gTime}
                        onInput={(e) => setGTime((e.detail as any).value)}
                        placeholder="如 周六晚 7 点(选填)"
                        placeholderClass={styles.placeholder}
                        maxlength={30}
                      />
                    </View>
                  </View>
                  <View className={styles.sheetBtn} onClick={handleGathering}>
                    {submitting ? '出单中...' : '一键出单(三子单+核销码)'}
                  </View>
                </View>

                {/* 我的场景单 */}
                <View className={styles.card}>
                  <View className={styles.cardTitle}>我的小聚单</View>
                  {scenes.length === 0 ? (
                    <View className={styles.empty}>
                      <View className={styles.emptyIcon}>🍻</View>
                      <View>暂无小聚单, 上方一键编排</View>
                    </View>
                  ) : (
                    scenes.map(s => (
                      <View key={s.sceneId} className={styles.sceneRow}>
                        <View className={styles.sceneLeft}>
                          <View className={styles.sceneTitle}>
                            小聚单 #{s.sceneId} · {s.partySize} 人
                            {s.gatheringTime ? ` · ${s.gatheringTime}` : ''}
                          </View>
                          <View className={styles.sceneMeta}>
                            {formatDate(s.createdAt)} · 合计 ¥{s.totalAmount.toFixed(2)} · 三子单(
                            {s.items.map(i => i.type).join('+')})
                          </View>
                          <View className={styles.sceneCode} onClick={() => handleCopyCode(s.redeemCode)}>
                            核销码 {s.redeemCode}(点击复制)
                          </View>
                        </View>
                        <View className={styles.sceneRight}>
                          <View className={`${styles.sceneBadge} ${s.status === 'redeemed' ? styles.sceneBadgeDone : ''}`}>
                            {sceneStatusName(s.status)}
                          </View>
                          {s.status === 'created' && (
                            <View className={styles.redeemBtn} onClick={() => handleRedeem(s.redeemCode)}>
                              核销
                            </View>
                          )}
                        </View>
                      </View>
                    ))
                  )}
                </View>
              </>
            ) : (
              <>
                {/* 定制需求表单 */}
                <View className={styles.card}>
                  <View className={styles.cardTitle}>定制需求</View>
                  <Picker
                    mode="selector"
                    range={DEMAND_TYPES.map(demandTypeName)}
                    value={demandTypeIdx}
                    onChange={(e) => setDemandTypeIdx(Number((e.detail as any).value))}
                  >
                    <View className={styles.pickerRow}>
                      <Text className={styles.pickerLabel}>定制类型</Text>
                      <Text className={styles.pickerValue}>{demandTypeName(DEMAND_TYPES[demandTypeIdx])}</Text>
                      <Text className={styles.pickerArrow}>›</Text>
                    </View>
                  </Picker>
                  <View className={styles.textareaRow}>
                    <Textarea
                      className={styles.textarea}
                      value={demandDesc}
                      onInput={(e) => setDemandDesc((e.detail as any).value)}
                      placeholder="描述定制需求(酒具刻字内容/私宴人数菜单/封坛规格年份等)"
                      placeholderClass={styles.placeholder}
                      maxlength={200}
                    />
                  </View>
                  <View className={styles.inputRow}>
                    <Text className={styles.pickerLabel}>预算</Text>
                    <Input
                      className={styles.gatherInput}
                      type="digit"
                      value={demandBudget}
                      onInput={(e) => setDemandBudget((e.detail as any).value)}
                      placeholder="¥ 选填"
                      placeholderClass={styles.placeholder}
                    />
                  </View>
                  <View className={styles.sheetBtn} onClick={handleDemand}>
                    {submitting ? '提交中...' : '提交定制需求'}
                  </View>
                </View>

                {/* 我的定制列表 */}
                <View className={styles.card}>
                  <View className={styles.cardTitle}>我的定制</View>
                  {demands.length === 0 ? (
                    <View className={styles.empty}>
                      <View className={styles.emptyIcon}>🎯</View>
                      <View>暂无定制需求</View>
                    </View>
                  ) : (
                    demands.map(d => (
                      <View key={d.demandId} className={styles.sceneRow}>
                        <View className={styles.sceneLeft}>
                          <View className={styles.sceneTitle}>
                            #{d.demandId} · {demandTypeName(d.demandType)}
                            {d.budget > 0 ? ` · 预算 ¥${d.budget.toFixed(0)}` : ''}
                          </View>
                          <View className={styles.sceneMeta}>
                            {formatDate(d.createdAt)} · 商户#{d.merchantId}
                          </View>
                          <View className={styles.sceneMeta}>{d.description}</View>
                          {d.quotedPrice > 0 && (
                            <View className={styles.sceneCode}>
                              报价 ¥{d.quotedPrice.toFixed(2)}
                            </View>
                          )}
                        </View>
                        <View className={styles.sceneRight}>
                          <View className={`${styles.sceneBadge} ${d.status === 'delivered' ? styles.sceneBadgeDone : ''}`}>
                            {demandStatusName(d.status)}
                          </View>
                          {d.status === 'quoted' && (
                            <View className={styles.redeemBtn} onClick={() => handleConfirmDemand(d)}>
                              确认报价
                            </View>
                          )}
                        </View>
                      </View>
                    ))
                  )}
                </View>
              </>
            )}
          </>
        ) : (
          <View className={styles.card}>
            <View className={styles.cardTitle}>我的商铺</View>
            {merchant ? (
              <>
                <View className={styles.merchantTop}>
                  <View className={styles.merchantName}>{merchant.shopName}</View>
                  <View className={`${styles.merchantBadge} ${merchant.status === 'active' ? styles.badgeActive : ''}`}>
                    {merchantStatusName(merchant.status)}
                  </View>
                </View>
                <View className={styles.merchantMeta}>
                  类目 {categoryName(merchant.category)} · 等级 {merchant.grade} · 信用分 {merchant.creditScore}
                </View>
                <View className={styles.merchantMeta}>
                  商户编号 {merchant.merchantId}
                  {merchant.ratingCount > 0 ? ` · 评分 ${merchant.ratingAvg.toFixed(1)}(${merchant.ratingCount} 条)` : ''}
                </View>
              </>
            ) : (
              <>
                <View className={styles.empty}>
                  <View className={styles.emptyIcon}>🤝</View>
                  <View className={styles.emptyText}>加入同盟生态</View>
                  <View className={styles.emptySub}>
                    超级会员(Lv4+)可申请 · AI 预审 + 人工终审<br />
                    八大类目: 水茶酒菜肉鱼器境 · 15% 分润五方拆账
                  </View>
                </View>
                <View className={styles.applyBtn} onClick={openJoin}>申请入盟</View>
              </>
            )}
          </View>
        )}

        {/* 规则说明 */}
        <View className={styles.noteCard}>
          <View className={styles.noteTitle}>同盟规则</View>
          <View className={styles.noteLine}>· 酒水不分家: 好水/好茶/好酒/好菜/肉类/鱼类/酒具/好境</View>
          <View className={styles.noteLine}>· 酒类商品全量溯源(7 工段批次), 其余类目简化凭证</View>
          <View className={styles.noteLine}>· 下单即付, T+1 结算: 15% 抽佣五方分润拆账</View>
          <View className={styles.noteLine}>· 订单结算后方可评价(一单一评), 违规评价自动折叠</View>
        </View>
        <View className={styles.bottomSpacer} />
      </ScrollView>

      {/* 购买弹层 */}
      {buyProduct && (
        <View className={styles.mask} onClick={() => setBuyProduct(null)}>
          <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
            <View className={styles.sheetTitle}>确认购买</View>
            <View className={styles.sheetProd}>
              <View className={styles.sheetProdName}>{buyProduct.name}</View>
              <View className={styles.sheetProdMeta}>
                {categoryName(buyProduct.category)} · 库存 {buyProduct.stock}
                {buyProduct.trace.traceVerified ? ' · 溯源已验' : ''}
              </View>
            </View>
            <View className={styles.inputRow}>
              <Text className={styles.inputLabel}>数量</Text>
              <Input
                className={styles.input}
                type="number"
                value={buyQty}
                onInput={(e) => setBuyQty((e.detail as any).value)}
                placeholder="1"
                placeholderClass={styles.placeholder}
              />
            </View>
            <View className={styles.sheetTotal}>
              合计: ¥{(buyProduct.price * (Number(buyQty) || 0)).toFixed(2)}
            </View>
            <View className={styles.sheetBtn} onClick={handleBuy}>
              {submitting ? '下单中...' : '立即下单'}
            </View>
          </View>
        </View>
      )}

      {/* 评价弹层 */}
      {reviewOrder && (
        <View className={styles.mask} onClick={() => setReviewOrder(null)}>
          <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
            <View className={styles.sheetTitle}>评价本次消费</View>
            <View className={styles.sheetProdMeta}>
              {productMap[reviewOrder.productId]?.name || `订单 ${reviewOrder.orderId}`}
            </View>
            <View className={styles.scoreRow}>
              {[1, 2, 3, 4, 5].map(s => (
                <Text
                  key={s}
                  className={`${styles.star} ${s <= reviewScore ? styles.starActive : ''}`}
                  onClick={() => setReviewScore(s)}
                >
                  ★
                </Text>
              ))}
              <Text className={styles.scoreText}>{reviewScore} 星</Text>
            </View>
            <View className={styles.textareaRow}>
              <Textarea
                className={styles.textarea}
                value={reviewContent}
                onInput={(e) => setReviewContent((e.detail as any).value)}
                placeholder="说说消费体验(可选)"
                placeholderClass={styles.placeholder}
                maxlength={200}
              />
            </View>
            <View className={styles.sheetBtn} onClick={handleReview}>
              {submitting ? '提交中...' : '提交评价'}
            </View>
          </View>
        </View>
      )}

      {/* 入盟弹层 */}
      {showJoin && (
        <View className={styles.mask} onClick={() => setShowJoin(false)}>
          <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
            <View className={styles.sheetTitle}>申请入盟</View>
            <View className={styles.sheetDesc}>超级会员专享 · AI 预审 + 人工终审</View>
            <View className={styles.catGrid}>
              {Object.keys(CATEGORY_NAME).map(c => (
                <View
                  key={c}
                  className={`${styles.catGridItem} ${joinCategory === c ? styles.catGridItemActive : ''}`}
                  onClick={() => setJoinCategory(c)}
                >
                  {categoryName(c)}
                </View>
              ))}
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={joinShopName}
                onInput={(e) => setJoinShopName((e.detail as any).value)}
                placeholder="店铺名称"
                placeholderClass={styles.placeholder}
                maxlength={30}
              />
            </View>
            <View className={styles.sheetBtn} onClick={handleJoin}>
              {submitting ? '提交中...' : '提交申请'}
            </View>
          </View>
        </View>
      )}
    </View>
  );
};

export default AlliancePage;
