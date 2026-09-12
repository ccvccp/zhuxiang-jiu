/**
 * 信值·臻选购物平台(68号) · 前端频道页
 * 五区: 雷达(五维+等级) → 臻选货架(L1) → 导购(SOP) → 邻里臻选(品类聚合)
 *       → 邻里求购(LBS) + 碳档案
 * 宪法口径: 观测面永不关停; 决策面 off 时引导提示(灰度放量 shadow→assist)
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input, Textarea } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  XinzhiAPI, XinzhiShopAPI, XinzhiTradeAPI,
  XinzhiRadarVO, PrimeItemVO, PriceDetailVO,
  NeighborShelfVO, GroupbuyVO, CarbonVO, ModeVO, GuideReplyVO,
  ShopVO, ShopRadarVO, ListingVO, ShelfVO, ShelfItemVO,
  CartVO, PreviewVO, OrderVO, SettlementVO,
  XINZHI_GATES, XINZHI_PAY_METHOD,
  xinzhiGradeName, xinzhiDimName,
  xinzhiShopStatusName, xinzhiShopGradeName,
  xinzhiListingStatusName, xinzhiOrderStatusName,
  xinzhiPayMethodName, xinzhiSettleStatusName,
} from '@/api/xinzhi';
import { getMemberId, getSession } from '@/services/auth-service';

type Tab = 'shelf' | 'shop' | 'trade' | 'neighbor';

const TABS: { key: Tab; label: string }[] = [
  { key: 'shelf', label: '臻选货架' },
  { key: 'shop', label: '店铺' },
  { key: 'trade', label: '购物·订单' },
  { key: 'neighbor', label: '邻里社区' },
];

// 店铺类目(P6 三类目)
const SHOP_CATEGORIES = [
  { key: 'wine', label: '好酒' },
  { key: 'vessel', label: '酒具' },
  { key: 'venue', label: '好境' },
];

// 预设地点(与叫帮/代驾页一致)
const PRESETS = [
  { name: '泉城广场(市中心)', lat: 36.6634, lng: 117.0268 },
  { name: '竹韵大酒店(历下区)', lat: 36.6612, lng: 117.1201 },
];
const LOC_IDX = 1;

// 反馈场景标签(四步闭环)
const FB_SCENES = [
  { key: 'product', label: '商品问题', tags: ['价格不合理', '描述不符'] },
  { key: 'radar', label: '信值疑问', tags: ['分数不合理', '维度不理解'] },
  { key: 'guide', label: '导购反馈', tags: ['推荐不合适', '解释不清楚'] },
];

const formatMoney = (n: number): string => `¥${Number(n || 0).toFixed(2)}`;

const shopCategoryName = (c: string): string =>
  SHOP_CATEGORIES.find(x => x.key === c)?.label || c;

const XinzhiPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('shelf');
  const [loading, setLoading] = useState(true);
  const myId = Number(getMemberId() || 0);

  // 雷达
  const [radar, setRadar] = useState<XinzhiRadarVO | null>(null);
  // 货架
  const [prime, setPrime] = useState<PrimeItemVO[]>([]);
  const [priceDetail, setPriceDetail] = useState<PriceDetailVO | null>(null);
  // 导购
  const [guideQuery, setGuideQuery] = useState('');
  const [guideReply, setGuideReply] = useState<GuideReplyVO | null>(null);
  // 邻里
  const [neighbor, setNeighbor] = useState<NeighborShelfVO | null>(null);
  const [groupbuys, setGroupbuys] = useState<GroupbuyVO[]>([]);
  const [gbTitle, setGbTitle] = useState('');
  const [gbUrgent, setGbUrgent] = useState(false);
  const [gbSubmitting, setGbSubmitting] = useState(false);
  // 碳档案
  const [carbon, setCarbon] = useState<CarbonVO | null>(null);
  // 灰度
  const [mode, setMode] = useState<ModeVO | null>(null);
  // 反馈弹层
  const [fbOpen, setFbOpen] = useState(false);
  const [fbScene, setFbScene] = useState('product');
  const [fbContent, setFbContent] = useState('');
  const [fbResult, setFbResult] = useState<string>('');
  // 店铺(P6/P7 平台化)
  const isAdmin = getSession()?.role === 'admin';
  const [myShop, setMyShop] = useState<ShopVO | null>(null);
  const [shopRadar, setShopRadar] = useState<ShopRadarVO | null>(null);
  const [myListings, setMyListings] = useState<ListingVO[]>([]);
  const [shelfData, setShelfData] = useState<ShelfVO | null>(null);
  const [shopLoaded, setShopLoaded] = useState(false);
  const [applyForm, setApplyForm] = useState({ shopName: '', category: 'wine', intro: '' });
  const [applyBusy, setApplyBusy] = useState(false);
  const [advice, setAdvice] = useState<Record<string, any> | null>(null);
  const [pickProduct, setPickProduct] = useState('');
  const [pickPrice, setPickPrice] = useState<PriceDetailVO | null>(null);
  const [listingBusy, setListingBusy] = useState(false);
  const [shopView, setShopView] = useState<{ shop: ShopVO | null; items: ShelfItemVO[] } | null>(null);
  // 购物·订单(P8/P9 平台化)
  const [cart, setCart] = useState<CartVO | null>(null);
  const [preview, setPreview] = useState<PreviewVO | null>(null);
  const [orders, setOrders] = useState<OrderVO[]>([]);
  const [settleRows, setSettleRows] = useState<SettlementVO[]>([]);
  const [tradeLoaded, setTradeLoaded] = useState(false);
  const [addrText, setAddrText] = useState('');
  const [orderRemark, setOrderRemark] = useState('');
  const [ageOk, setAgeOk] = useState(false);
  const [payMethod, setPayMethod] = useState('wallet');
  const [trustIdText, setTrustIdText] = useState('');
  const [payFor, setPayFor] = useState('');
  const [shipForm, setShipForm] = useState({ carrier: '', waybillNo: '' });
  const [settleBusy, setSettleBusy] = useState(false);

  const loadAll = useCallback(async () => {
    try {
      const [m, r] = await Promise.all([
        XinzhiAPI.mode().catch(() => null),
        myId ? XinzhiAPI.radar().catch(() => null) : Promise.resolve(null),
      ]);
      setMode(m);
      setRadar(r);
      const [p, nb, gb, cb] = await Promise.all([
        XinzhiAPI.prime(10).catch(() => [] as PrimeItemVO[]),
        XinzhiAPI.neighbor().catch(() => null),
        XinzhiAPI.groupbuyHall(PRESETS[LOC_IDX].lng, PRESETS[LOC_IDX].lat, 10)
          .catch(() => [] as GroupbuyVO[]),
        myId ? XinzhiAPI.carbon().catch(() => null) : Promise.resolve(null),
      ]);
      setPrime(p);
      setNeighbor(nb);
      setGroupbuys(gb);
      setCarbon(cb);
    } finally {
      setLoading(false);
    }
  }, [myId]);

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useDidShow(() => {
    loadAll();
  });

  // ============ 平台化: 店铺/购物 数据加载(按页签懒加载) ============

  /** 店铺页签数据(我的店铺+我的铺货+公开货架) */
  const loadShopData = useCallback(async () => {
    try {
      const [shop, ls, sh] = await Promise.all([
        XinzhiShopAPI.myShop().catch(() => null),
        XinzhiShopAPI.myListings().catch(() => [] as ListingVO[]),
        XinzhiShopAPI.shelf().catch(() => null),
      ]);
      setMyShop(shop);
      setMyListings(Array.isArray(ls) ? ls : []);
      setShelfData(sh);
    } finally {
      setShopLoaded(true);
    }
  }, [myId]);

  /** 购物车+结算预览(实时重算) */
  const refreshCart = useCallback(async () => {
    const [c, pv] = await Promise.all([
      XinzhiTradeAPI.cartMine().catch(() => null),
      XinzhiTradeAPI.checkoutPreview().catch(() => null),
    ]);
    setCart(c);
    setPreview(pv);
  }, []);

  /** 我的臻选订单 */
  const refreshOrders = useCallback(async () => {
    const os = await XinzhiTradeAPI.myOrders().catch(() => [] as OrderVO[]);
    setOrders(Array.isArray(os) ? os : []);
  }, []);

  /** 结算单(商家 mine / admin 总览) */
  const refreshSettle = useCallback(async () => {
    const rows = await (isAdmin
      ? XinzhiTradeAPI.settlements().catch(() => [] as SettlementVO[])
      : XinzhiTradeAPI.mySettlements().catch(() => [] as SettlementVO[]));
    setSettleRows(Array.isArray(rows) ? rows : []);
  }, [isAdmin]);

  /** 购物·订单页签数据 */
  const loadTradeData = useCallback(async () => {
    try {
      await Promise.all([
        refreshCart(),
        refreshOrders(),
        refreshSettle(),
        XinzhiShopAPI.shelf().catch(() => null).then(setShelfData),
      ]);
    } finally {
      setTradeLoaded(true);
    }
  }, [refreshCart, refreshOrders, refreshSettle]);

  /** 页签切换(店铺/购物·订单 懒加载) */
  const switchTab = (key: Tab) => {
    setTab(key);
    if (key === 'shop') {
      loadShopData();
    } else if (key === 'trade') {
      loadTradeData();
    }
  };

  // ============ 平台化: 店铺动作(P6/P7) ============

  /** 开店申请(门槛: 雷达≥60+L3+一人一铺; 409 拦截信息透出) */
  const submitShopApply = async () => {
    if (!applyForm.shopName.trim()) {
      Taro.showToast({ title: '请填写店铺名称', icon: 'none' });
      return;
    }
    setApplyBusy(true);
    try {
      const shop = await XinzhiShopAPI.applyShop(
        applyForm.shopName.trim(), applyForm.category, applyForm.intro.trim());
      setMyShop(shop);
      setAdvice(shop.disposition || null);
      Taro.showToast({ title: '申请已提交', icon: 'success' });
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e || '').slice(0, 30) || '提交失败', icon: 'none' });
    } finally {
      setApplyBusy(false);
    }
  };

  /** 商家自关店(仅 active) */
  const closeMyShop = async () => {
    if (!myShop?.shopId) return;
    try {
      await XinzhiShopAPI.closeShop(myShop.shopId, '商家自关');
      Taro.showToast({ title: '店铺已关闭', icon: 'none' });
      loadShopData();
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || '').slice(0, 30) || '操作失败', icon: 'none' });
    }
  };

  /** 店铺信值快照(跌破预警→建议书, 永不自动执行) */
  const loadShopRadar = async () => {
    if (!myShop?.shopId) return;
    try {
      const snap = await XinzhiShopAPI.radarSnapshot(myShop.shopId);
      setShopRadar(snap);
      if (snap?.warning) setAdvice(snap.warning);
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || '').slice(0, 30) || '查询失败', icon: 'none' });
    }
  };

  /** 选品(主站商品池只读; 展示信值价) */
  const pickProductFor = async (pid: string) => {
    setPickProduct(pid);
    try {
      setPickPrice(await XinzhiAPI.price(pid));
    } catch (_) {
      setPickPrice(null);
    }
  };

  /** 铺货提交(四门禁: 资质/溯源/合规/信值分) */
  const submitListing = async () => {
    if (!pickProduct) {
      Taro.showToast({ title: '请先选择主站商品', icon: 'none' });
      return;
    }
    setListingBusy(true);
    try {
      const l = await XinzhiShopAPI.submitListing(pickProduct);
      if (l.disposition) setAdvice(l.disposition);
      Taro.showToast({
        title: l.status === 'reviewing' ? '已提交, 待审核' : `门禁未全过(${l.statusLabel})`,
        icon: 'none',
      });
      setMyListings(await XinzhiShopAPI.myListings().catch(() => [] as ListingVO[]));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e || '').slice(0, 30) || '提交失败', icon: 'none' });
    } finally {
      setListingBusy(false);
    }
  };

  /** 上下架(listed⇄delisted) */
  const toggleListing = async (l: ListingVO) => {
    try {
      if (l.status === 'listed') {
        await XinzhiShopAPI.delistListing(l.listingId);
      } else if (l.status === 'delisted') {
        await XinzhiShopAPI.relistListing(l.listingId);
      }
      setMyListings(await XinzhiShopAPI.myListings().catch(() => [] as ListingVO[]));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || '').slice(0, 30) || '操作失败', icon: 'none' });
    }
  };

  /** 店铺主页(公开) */
  const openShopView = async (shopId: number) => {
    try {
      const [sp, sl] = await Promise.all([
        XinzhiShopAPI.shopPage(shopId),
        XinzhiShopAPI.shopListings(shopId),
      ]);
      setShopView({ shop: sp, items: (sl && sl.items) || [] });
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || '').slice(0, 30) || '查询失败', icon: 'none' });
    }
  };

  // ============ 平台化: 购物与订单动作(P8/P9) ============

  /** 加购(货架入口) */
  const addToCart = async (listingId: number) => {
    if (!myId) {
      Taro.showToast({ title: '请先登录', icon: 'none' });
      return;
    }
    try {
      await XinzhiTradeAPI.cartAdd(listingId, 1);
      Taro.showToast({ title: '已加购', icon: 'success' });
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e || '').slice(0, 30) || '加购失败', icon: 'none' });
    }
  };

  /** 改量(减到 0 = 移除) */
  const changeQty = async (listingId: number, delta: number) => {
    const item = (cart?.items || []).find(i => i.listingId === listingId);
    if (!item) return;
    try {
      const next = item.quantity + delta;
      if (next <= 0) {
        await XinzhiTradeAPI.cartRemove(listingId);
      } else {
        await XinzhiTradeAPI.cartUpdate(listingId, Math.min(99, next));
      }
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || '').slice(0, 30) || '操作失败', icon: 'none' });
    } finally {
      refreshCart();
    }
  };

  /** 移除条目 */
  const removeCartItem = async (listingId: number) => {
    try {
      await XinzhiTradeAPI.cartRemove(listingId);
    } catch (_) { /* request 层已提示 */ }
    refreshCart();
  };

  /** 下单(年龄门必勾; 全量购物车) */
  const submitOrder = async () => {
    if (!addrText.trim()) {
      Taro.showToast({ title: '请填写收货地址', icon: 'none' });
      return;
    }
    if (!ageOk) {
      Taro.showToast({ title: '酒类下单须确认年满 18 周岁', icon: 'none' });
      return;
    }
    try {
      const res = await XinzhiTradeAPI.createOrder({
        address: { full: addrText.trim() },
        remark: orderRemark.trim(),
        ageConfirmed: true,
      });
      Taro.showToast({ title: `下单成功 ${res.orderId}`, icon: 'none' });
      setAddrText('');
      setOrderRemark('');
      setAgeOk(false);
      loadTradeData();
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e || '').slice(0, 30) || '下单失败', icon: 'none' });
    }
  };

  /** 支付(TV/组合通道须携带 trustId) */
  const payNow = async (orderId: string) => {
    if (payMethod !== 'wallet' && !trustIdText.trim()) {
      Taro.showToast({ title: 'TV/组合支付须填写信值档案 trustId', icon: 'none' });
      return;
    }
    try {
      const res = await XinzhiTradeAPI.payOrder(
        orderId, payMethod, undefined,
        trustIdText.trim() ? Number(trustIdText.trim()) : undefined);
      Taro.showToast({ title: `支付成功(${res.statusName})`, icon: 'none' });
      setPayFor('');
      setTrustIdText('');
      refreshOrders();
      refreshSettle();
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e || '').slice(0, 30) || '支付失败', icon: 'none' });
    }
  };

  /** 取消订单 */
  const cancelOrderNow = async (orderId: string) => {
    try {
      await XinzhiTradeAPI.cancelOrder(orderId, '用户取消');
      refreshOrders();
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || '').slice(0, 30) || '取消失败', icon: 'none' });
    }
  };

  /** 商家发货(PAID 态) */
  const shipNow = async (orderId: string) => {
    if (!shipForm.carrier.trim() || !shipForm.waybillNo.trim()) {
      Taro.showToast({ title: '请填写承运商与运单号', icon: 'none' });
      return;
    }
    try {
      await XinzhiTradeAPI.shipOrder(orderId, shipForm.carrier.trim(), shipForm.waybillNo.trim());
      Taro.showToast({ title: '已发货', icon: 'success' });
      setShipForm({ carrier: '', waybillNo: '' });
      refreshOrders();
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || '').slice(0, 30) || '发货失败', icon: 'none' });
    }
  };

  /** 确认收货 */
  const confirmOrderNow = async (orderId: string) => {
    try {
      await XinzhiTradeAPI.confirmOrder(orderId);
      Taro.showToast({ title: '已确认收货', icon: 'success' });
      refreshOrders();
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || '').slice(0, 30) || '操作失败', icon: 'none' });
    }
  };

  /** 评价(信值回流留痕) */
  const reviewNow = async (orderId: string, rating: number) => {
    try {
      await XinzhiTradeAPI.reviewOrder(orderId, rating, '');
      Taro.showToast({ title: `已评价 ${rating} 星(信值回流留痕)`, icon: 'none' });
      refreshOrders();
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || '').slice(0, 30) || '评价失败', icon: 'none' });
    }
  };

  /** T+1 分账执行(admin, 幂等) */
  const runSettle = async () => {
    setSettleBusy(true);
    try {
      const res = await XinzhiTradeAPI.runSettlements();
      Taro.showToast({ title: `T+1 执行: 结算 ${res.settledCount} 单`, icon: 'none' });
      refreshSettle();
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e || '').slice(0, 30) || '执行失败', icon: 'none' });
    } finally {
      setSettleBusy(false);
    }
  };

  /** 结算冲正(admin; 不足记负债, 诚实标注) */
  const reverseSettleNow = async (settleId: number) => {
    try {
      await XinzhiTradeAPI.reverseSettlement(settleId, 'admin 人工冲正');
      Taro.showToast({ title: '已冲正(负债诚实标注)', icon: 'none' });
      refreshSettle();
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || '').slice(0, 30) || '冲正失败', icon: 'none' });
    }
  };

  /** 购物车条目名称(货架/我的铺货 → listingId 反查, ES2017 兼容) */
  const cartNameOf = (listingId: number): string => {
    const cats = (shelfData && shelfData.categories) || [];
    for (const c of cats) {
      for (const x of c.items || []) {
        if (x.listingId === listingId) return x.productName;
      }
    }
    const mine = myListings.find(x => x.listingId === listingId);
    return mine ? mine.productName : `臻选铺货 #${listingId}`;
  };

  /** 状态徽章配色 */
  const shopStatusColor = (s: string): string =>
    (({
      pending: styles.stPending, ai_reviewing: styles.stPending,
      manual_reviewing: styles.stWarn, signed: styles.stInfo,
      probation: styles.stWarn, active: styles.stActive,
      suspended: styles.stDanger, terminated: styles.stMuted,
      rejected: styles.stDanger,
    } as Record<string, string>)[s] || styles.stMuted);

  const listingStatusColor = (s: string): string =>
    (({
      draft: styles.stMuted, reviewing: styles.stWarn,
      listed: styles.stActive, delisted: styles.stPending,
      removed: styles.stDanger,
    } as Record<string, string>)[s] || styles.stMuted);

  const orderStatusColor = (s: string): string =>
    (({
      PENDING: styles.stWarn, PAID: styles.stInfo, SHIPPED: styles.stPending,
      RECEIVED: styles.stInfo, COMPLETED: styles.stActive, CANCELLED: styles.stMuted,
      CLOSED: styles.stMuted, RETURNING: styles.stWarn, REFUNDED: styles.stDanger,
    } as Record<string, string>)[s] || styles.stMuted);

  const settleStatusColor = (s: string): string =>
    (({
      pending: styles.stWarn, settled: styles.stActive, reversed: styles.stDanger,
    } as Record<string, string>)[s] || styles.stMuted);

  /** 查看价格构成(透明定价) */
  const showPrice = async (productId: string) => {
    try {
      const d = await XinzhiAPI.price(productId);
      setPriceDetail(d);
    } catch (e: any) {
      Taro.showToast({ title: e?.message || '查询失败', icon: 'none' });
    }
  };

  /** 导购问答(决策面——off 时提示灰度) */
  const askGuide = async () => {
    if (!prime.length) {
      Taro.showToast({ title: '货架暂无商品', icon: 'none' });
      return;
    }
    try {
      const reply = await XinzhiAPI.guide(prime[0].productId, guideQuery || '推荐');
      setGuideReply(reply);
    } catch (e: any) {
      const msg = String(e?.message || e || '');
      if (msg.includes('决策面') || msg.includes('409')) {
        Taro.showModal({
          title: '灰度开放中',
          content: '导购问答处于灰度观察期(XINZHI_MODE=off), 数据积累达标后开放。您的雷达/价格/反馈等观测功能不受影响。',
          showCancel: false,
        });
      } else {
        Taro.showToast({ title: msg.slice(0, 30) || '查询失败', icon: 'none' });
      }
    }
  };

  /** 发布求购(决策面) */
  const submitGroupbuy = async () => {
    if (!gbTitle.trim()) {
      Taro.showToast({ title: '请填写求购内容', icon: 'none' });
      return;
    }
    setGbSubmitting(true);
    try {
      const loc = PRESETS[LOC_IDX];
      await XinzhiAPI.publishGroupbuy({
        title: gbTitle.trim(),
        quantity: 1,
        urgency: gbUrgent ? 'urgent' : 'normal',
        longitude: loc.lng,
        latitude: loc.lat,
        address: loc.name,
      });
      Taro.showToast({ title: '已发布, 等待邻里响应', icon: 'success' });
      setGbTitle('');
      setGroupbuys(await XinzhiAPI.groupbuyHall(loc.lng, loc.lat, 10));
    } catch (e: any) {
      const msg = String(e?.message || e || '');
      if (msg.includes('决策面') || msg.includes('409')) {
        Taro.showModal({
          title: '灰度开放中',
          content: '邻里求购处于灰度观察期, 达标后开放。您可浏览大厅与邻里频道(观测面)。',
          showCancel: false,
        });
      } else {
        Taro.showToast({ title: msg.slice(0, 30), icon: 'none' });
      }
    } finally {
      setGbSubmitting(false);
    }
  };

  /** 响应求购 */
  const respondGb = async (gb: GroupbuyVO) => {
    try {
      await XinzhiAPI.respondGroupbuy(gb.groupbuyId);
      Taro.showToast({ title: '已响应', icon: 'success' });
      const loc = PRESETS[LOC_IDX];
      setGroupbuys(await XinzhiAPI.groupbuyHall(loc.lng, loc.lat, 10));
    } catch (e: any) {
      const msg = String(e?.message || e || '');
      Taro.showToast({
        title: msg.includes('决策面') ? '灰度观察期' : (msg.slice(0, 30) || '失败'),
        icon: 'none',
      });
    }
  };

  /** 提交反馈(永不关停) */
  const submitFeedback = async () => {
    const scene = FB_SCENES.find(s => s.key === fbScene)!;
    try {
      const fb = await XinzhiAPI.submitFeedback(fbScene, scene.tags, fbContent);
      setFbResult(fb.level === 'L1'
        ? `已记录并自动回复: ${fb.autoReply}`
        : `已受理(${fb.sla}), 路由至${fb.routedTo}`);
      setFbContent('');
    } catch (e: any) {
      Taro.showToast({ title: '提交失败', icon: 'none' });
    }
  };

  const gradeColor = (g: string): string =>
    g === 'S' ? styles.gradeS : g === 'A' ? styles.gradeA
      : g === 'B' ? styles.gradeB : styles.gradeD;

  return (
    <View className={styles.page}>
      <NavBar title="信值·臻选" />

      <ScrollView scrollY className={styles.body}>
        {/* ============ 一、信值雷达卡 ============ */}
        <View className={styles.radarCard}>
          <View className={styles.radarHeader}>
            <View>
              <View className={styles.radarTitle}>我的信值雷达</View>
              <View className={styles.radarSub}>
                {radar?.coldStart ? '新用户保护期 · 权重已切换' : '五维信用画像 · 全链可解释'}
              </View>
            </View>
            {radar && (
              <View className={`${styles.gradeBadge} ${gradeColor(radar.grade)}`}>
                {xinzhiGradeName(radar.grade)}
              </View>
            )}
          </View>
          {radar ? (
            <>
              <View className={styles.radarTotal}>
                <Text className={styles.radarTotalNum}>{radar.totalScore}</Text>
                <Text className={styles.radarTotalUnit}>分</Text>
              </View>
              {radar.circuitBroken && (
                <View className={styles.circuitTip}>
                  熔断中: 存在维度低于 40, 总分暂封顶 59
                </View>
              )}
              <View className={styles.dimGrid}>
                {(radar.dimensions || []).map(d => (
                  <View key={d.key} className={styles.dimItem}>
                    <View className={styles.dimScore}>{d.score}</View>
                    <View className={styles.dimLabel}>{xinzhiDimName(d.key)}</View>
                    <View className={styles.dimBar}>
                      <View className={styles.dimBarFill} style={{ width: `${Math.min(100, d.score)}%` }} />
                    </View>
                    {d.factors?.length > 0 && (
                      <View className={styles.dimFactor}>{d.factors[0]}</View>
                    )}
                  </View>
                ))}
              </View>
            </>
          ) : (
            <View className={styles.loginTip}>
              {myId ? '雷达计算中…' : '登录后查看您的五维信值画像'}
            </View>
          )}
          {carbon && (
            <View className={styles.carbonRow}>
              🌱 碳积分 {carbon.carbonGrams >= 1000
                ? `${(carbon.carbonGrams / 1000).toFixed(1)}kg` : `${carbon.carbonGrams.toFixed(0)}g`}
              (互助 {carbon.helpOrders} 单 · 拼单 {carbon.groupbuys} 次 · 只读不可交易)
            </View>
          )}
        </View>

        {/* 标签页 */}
        <View className={styles.tabBar}>
          {TABS.map(t => (
            <View
              key={t.key}
              className={`${styles.tab} ${tab === t.key ? styles.tabActive : ''}`}
              onClick={() => switchTab(t.key)}
            >
              {t.label}
            </View>
          ))}
          <View className={styles.fbBtn} onClick={() => { setFbOpen(!fbOpen); setFbResult(''); }}>
            反馈
          </View>
        </View>

        {/* 反馈弹层(永不关停) */}
        {fbOpen && (
          <View className={styles.fbPanel}>
            <View className={styles.fbScenes}>
              {FB_SCENES.map(s => (
                <View
                  key={s.key}
                  className={`${styles.fbScene} ${fbScene === s.key ? styles.fbSceneActive : ''}`}
                  onClick={() => setFbScene(s.key)}
                >
                  {s.label}
                </View>
              ))}
            </View>
            <Textarea
              className={styles.fbInput}
              value={fbContent}
              onInput={e => setFbContent(e.detail.value)}
              placeholder="说说您遇到的問題(15 分钟紧急通道自动分流)…"
              maxlength={500}
            />
            <View className={styles.fbSubmit} onClick={submitFeedback}>提交反馈</View>
            {fbResult && <View className={styles.fbResult}>{fbResult}</View>}
          </View>
        )}

        {/* ============ 二、臻选货架 ============ */}
        {tab === 'shelf' && (
          <View className={styles.section}>
            <View className={styles.sectionTitle}>
              臻选货架
              <Text className={styles.sectionNote}>三维评分 · 信值加权排序 · 价格全透明</Text>
            </View>
            {prime.length === 0 && !loading && (
              <View className={styles.empty}>货架整理中…</View>
            )}
            {prime.map(item => (
              <View key={item.scoreSeq} className={styles.productCard}>
                <View className={styles.productHead}>
                  <View className={styles.productName}>{item.productName}</View>
                  <View className={`${styles.productGrade} ${item.grade === 'L1' ? styles.gradeS : styles.gradeB}`}>
                    {item.grade}
                  </View>
                </View>
                <View className={styles.scoreRow}>
                  <View className={styles.scoreItem}>
                    <View className={styles.scoreNum}>{item.fit}</View>
                    <View className={styles.scoreLbl}>契合</View>
                  </View>
                  <View className={styles.scoreItem}>
                    <View className={styles.scoreNum}>{item.safety}</View>
                    <View className={styles.scoreLbl}>安全</View>
                  </View>
                  <View className={styles.scoreItem}>
                    <View className={styles.scoreNum}>{item.conversion}</View>
                    <View className={styles.scoreLbl}>转化</View>
                  </View>
                  <View className={styles.scoreItem}>
                    <View className={styles.scoreNum}>{item.valueScore}</View>
                    <View className={styles.scoreLbl}>价值分</View>
                  </View>
                </View>
                <View className={styles.priceBtn} onClick={() => showPrice(item.productId)}>
                  查看价格构成
                </View>
              </View>
            ))}

            {/* 价格构成弹层(透明定价) */}
            {priceDetail && (
              <View className={styles.pricePanel}>
                <View className={styles.pricePanelTitle}>
                  价格构成 · {priceDetail.productName}
                </View>
                <View className={styles.priceRows}>
                  <View className={styles.priceRow}>
                    <Text>原价</Text>
                    <Text>{formatMoney(priceDetail.basePrice)}</Text>
                  </View>
                  <View className={styles.priceRow}>
                    <Text>信值抵扣(等级{priceDetail.grade} α={priceDetail.xinzhiAlpha})</Text>
                    <Text className={styles.priceDeduct}>-{formatMoney(priceDetail.xinzhiCredit)}</Text>
                  </View>
                  <View className={styles.priceRow}>
                    <Text>三因子折扣</Text>
                    <Text className={styles.priceDeduct}>
                      -{formatMoney(priceDetail.basePrice - priceDetail.afterThreeFactor)}
                    </Text>
                  </View>
                  <View className={`${styles.priceRow} ${styles.priceFinal}`}>
                    <Text>实付</Text>
                    <Text>{formatMoney(priceDetail.finalPrice)}</Text>
                  </View>
                </View>
                {priceDetail.floored && (
                  <View className={styles.priceNote}>已触达平台地板保护(基准价 7 折)</View>
                )}
                {priceDetail.auditFlag && (
                  <View className={styles.priceNote}>
                    ⚠ 存在跨会员价差记录, 已进入审计通道(杀熟零容忍)
                  </View>
                )}
                <View className={styles.priceClose} onClick={() => setPriceDetail(null)}>收起</View>
              </View>
            )}

            {/* 导购问答 */}
            <View className={styles.guideSection}>
              <View className={styles.guideTitle}>🧑‍🌾 小竹臻选导购</View>
              <View className={styles.guideSub}>
                数字全部来自实时查询 · 不承诺降价 · 风险如实告知
              </View>
              <View className={styles.guideInputRow}>
                <Input
                  className={styles.guideInput}
                  value={guideQuery}
                  onInput={e => setGuideQuery(e.detail.value)}
                  placeholder={`问问${prime[0]?.productName?.slice(0, 6) || '这款酒'}: 多少钱? 适合我吗?`}
                />
                <View className={styles.guideBtn} onClick={askGuide}>问小竹</View>
              </View>
              {guideReply && (
                <View className={styles.guideReply}>
                  {guideReply.reply}
                  {guideReply.xinzhiMode && (
                    <View className={styles.guideMode}>灰度态: {guideReply.xinzhiMode}</View>
                  )}
                </View>
              )}
            </View>
          </View>
        )}

        {/* ============ 三、店铺(P6/P7 平台化) ============ */}
        {tab === 'shop' && (
          <View className={styles.section}>
            <View className={styles.sectionTitle}>
              我的店铺
              <Text className={styles.sectionNote}>信值门槛: 雷达≥60 · 会员≥L3 · 一人一铺</Text>
            </View>

            {/* 建议书面板(审核/预警类响应——处罚永不自动执行) */}
            {advice && (
              <View className={styles.advicePanel}>
                <View className={styles.adviceTag}>建议书</View>
                <View className={styles.adviceNote}>
                  {String(advice.note || advice.recommendation || '')}
                  {advice.requiresAdmin === false ? '(已自动生效留痕)' : '(须人工确认, 永不自动执行)'}
                </View>
              </View>
            )}

            {myShop && myShop.shopId ? (
              <View className={styles.shopCard}>
                <View className={styles.shopHead}>
                  <View className={styles.shopName}>{myShop.shopName}</View>
                  <View className={`${styles.stBadge} ${shopStatusColor(myShop.status)}`}>
                    {myShop.statusLabel || xinzhiShopStatusName(myShop.status)}
                  </View>
                </View>
                <View className={styles.shopMeta}>
                  {myShop.categoryLabel || shopCategoryName(myShop.category)}
                  {myShop.merchantGrade ? ` · 等级 ${xinzhiShopGradeName(myShop.merchantGrade)}` : ''}
                  {myShop.radarTotal ? ` · 雷达 ${myShop.radarTotal} 分` : ''}
                </View>
                {myShop.intro && (
                  <View className={styles.shopIntro}>{myShop.intro}</View>
                )}
                {/* 信值快照 */}
                <View className={styles.snapRow} onClick={loadShopRadar}>
                  {shopRadar
                    ? `信值快照: ${shopRadar.current.score} 分(等级${shopRadar.current.grade || '-'} · 预警线 ${shopRadar.warnLine})`
                    : '查看店铺信值快照(90 日滚动留痕)'}
                </View>
                {myShop.status === 'active' && (
                  <View className={styles.shopCloseBtn} onClick={closeMyShop}>
                    关闭店铺(商家自关)
                  </View>
                )}
              </View>
            ) : (
              /* 无店 → 开店申请表单 */
              <View className={styles.applyCard}>
                <View className={styles.subTitle}>开店申请</View>
                <Input
                  className={styles.formInput}
                  value={applyForm.shopName}
                  onInput={e => setApplyForm({ ...applyForm, shopName: e.detail.value })}
                  placeholder="店铺名称(≤20字)"
                  maxlength={20}
                />
                <View className={styles.catRow}>
                  {SHOP_CATEGORIES.map(c => (
                    <View
                      key={c.key}
                      className={`${styles.catChip} ${applyForm.category === c.key ? styles.catChipOn : ''}`}
                      onClick={() => setApplyForm({ ...applyForm, category: c.key })}
                    >
                      {c.label}
                    </View>
                  ))}
                </View>
                <Textarea
                  className={styles.formArea}
                  value={applyForm.intro}
                  onInput={e => setApplyForm({ ...applyForm, intro: e.detail.value })}
                  placeholder="店铺简介(≤100字; 空视为签名材料不全, 转 AI 预审人工档)"
                  maxlength={100}
                />
                <View className={styles.formBtn} onClick={submitShopApply}>
                  {applyBusy ? '提交中…' : '提交开店申请'}
                </View>
              </View>
            )}

            {/* 有店 → 我的铺货 + 铺货提交 */}
            {myShop && myShop.shopId && (
              <>
                <View className={styles.sectionTitle} style={{ marginTop: '24rpx' }}>
                  我的铺货
                  <Text className={styles.sectionNote}>四门禁全过→待审核 · 任一失败→草稿</Text>
                </View>
                {myListings.length === 0 && shopLoaded && (
                  <View className={styles.empty}>暂无铺货——从下方选择主站商品提交</View>
                )}
                {myListings.map(l => (
                  <View key={l.listingId} className={styles.listingCard}>
                    <View className={styles.listingHead}>
                      <View className={styles.listingName}>{l.productName}</View>
                      <View className={`${styles.stBadge} ${listingStatusColor(l.status)}`}>
                        {l.statusLabel || xinzhiListingStatusName(l.status)}
                      </View>
                    </View>
                    <View className={styles.listingMeta}>
                      信值价 {formatMoney(l.xinzhiPrice.finalPrice)} · 信值抵扣 -{formatMoney(l.xinzhiPrice.xinzhiCredit)}
                      {l.sourcePrice ? ` · 主站价 ${formatMoney(l.sourcePrice)}` : ''}
                    </View>
                    {(l.status === 'listed' || l.status === 'delisted') && (
                      <View className={styles.listingBtn} onClick={() => toggleListing(l)}>
                        {l.status === 'listed' ? '下架' : '重新上架'}
                      </View>
                    )}
                  </View>
                ))}

                {/* 铺货提交表单(主站商品池只读选品) */}
                <View className={styles.applyCard}>
                  <View className={styles.subTitle}>铺货提交(主站商品池只读选品)</View>
                  <View className={styles.pickRow}>
                    {(prime.length ? prime : []).slice(0, 6).map(p => (
                      <View
                        key={p.productId}
                        className={`${styles.pickChip} ${pickProduct === p.productId ? styles.pickChipOn : ''}`}
                        onClick={() => pickProductFor(p.productId)}
                      >
                        {p.productName.slice(0, 8)}
                      </View>
                    ))}
                    {prime.length === 0 && (
                      <View className={styles.empty}>主站货架暂无商品可选</View>
                    )}
                  </View>
                  {pickPrice && (
                    <View className={styles.pickPrice}>
                      信值价 {formatMoney(pickPrice.finalPrice)} · 信值抵扣 -{formatMoney(pickPrice.xinzhiCredit)} · 原价 {formatMoney(pickPrice.basePrice)}
                    </View>
                  )}
                  <View className={styles.formBtn} onClick={submitListing}>
                    {listingBusy ? '提交中…' : '提交铺货(四门禁)'}
                  </View>
                  <View className={styles.gateNote}>
                    四门禁: {Object.values(XINZHI_GATES).join(' / ')}
                  </View>
                </View>
              </>
            )}

            {/* 公开货架(品类聚合) */}
            <View className={styles.sectionTitle} style={{ marginTop: '24rpx' }}>
              店铺货架
              <Text className={styles.sectionNote}>品类聚合 · 信值价快照 · 点店铺名进店</Text>
            </View>
            {shelfData && (shelfData.categories || []).map(cat => (
              <View key={cat.category} className={styles.shelfGroup}>
                <View className={styles.shelfCat}>{cat.category} · {cat.count} 件在售</View>
                {(cat.items || []).map(it => (
                  <View key={it.listingId} className={styles.shelfItem}>
                    <View className={styles.shelfInfo} onClick={() => openShopView(it.shopId)}>
                      <View className={styles.shelfName}>{it.productName}</View>
                      <View className={styles.shelfShop}>{it.shopName} · 进店</View>
                      <View className={styles.shelfPrice}>
                        信值价 {formatMoney(it.xinzhiFinalPrice)} · 抵扣 -{formatMoney(it.xinzhiCredit)}
                      </View>
                    </View>
                    <View className={styles.addCartBtn} onClick={() => addToCart(it.listingId)}>加购</View>
                  </View>
                ))}
              </View>
            ))}
            {shopLoaded && (!shelfData || (shelfData.categories || []).length === 0) && (
              <View className={styles.empty}>货架整理中(暂无在售铺货)</View>
            )}

            {/* 店铺主页弹层(公开信息) */}
            {shopView && (
              <View className={styles.shopViewPanel}>
                <View className={styles.shopViewTitle}>
                  {shopView.shop?.shopName || '店铺主页'}
                  <Text className={styles.sectionNote}>
                    {shopView.shop ? ` ${shopView.shop.statusLabel || xinzhiShopStatusName(shopView.shop.status)}` : ''}
                  </Text>
                </View>
                {(shopView.items || []).map(it => (
                  <View key={it.listingId} className={styles.shelfItem}>
                    <View className={styles.shelfInfo}>
                      <View className={styles.shelfName}>{it.productName}</View>
                      <View className={styles.shelfPrice}>
                        信值价 {formatMoney(it.xinzhiFinalPrice)} · 抵扣 -{formatMoney(it.xinzhiCredit)}
                      </View>
                    </View>
                    <View className={styles.addCartBtn} onClick={() => addToCart(it.listingId)}>加购</View>
                  </View>
                ))}
                {(shopView.items || []).length === 0 && (
                  <View className={styles.empty}>该店暂无在售商品(removed 已隐藏, 其余透明可见)</View>
                )}
                <View className={styles.priceClose} onClick={() => setShopView(null)}>收起</View>
              </View>
            )}
          </View>
        )}

        {/* ============ 四、购物车·订单(P8/P9 平台化) ============ */}
        {tab === 'trade' && (
          <View className={styles.section}>
            <View className={styles.sectionTitle}>
              购物车
              <Text className={styles.sectionNote}>实时重算 · α抵扣≤30% · 满99免运费</Text>
            </View>
            {tradeLoaded && (!cart || (cart.items || []).length === 0) && (
              <View className={styles.empty}>购物车空空如也——去店铺货架逛逛</View>
            )}
            {(cart?.items || []).map(it => (
              <View key={it.listingId} className={styles.cartItem}>
                <View className={styles.cartHead}>
                  <View className={styles.cartName}>
                    {cartNameOf(it.listingId)}
                  </View>
                  <View className={styles.qtyCtrl}>
                    <View className={styles.qtyBtn} onClick={() => changeQty(it.listingId, -1)}>−</View>
                    <View className={styles.qtyNum}>{it.quantity}</View>
                    <View className={styles.qtyBtn} onClick={() => changeQty(it.listingId, 1)}>＋</View>
                  </View>
                </View>
                <View className={styles.cartPrice}>
                  实时价 {formatMoney(it.snapshot.finalPrice)} × {it.quantity}
                </View>
                <View className={styles.cartDeduct}>
                  信值抵扣 -{formatMoney((it.snapshot.xinzhiCredit || 0) * it.quantity)}
                </View>
                <View className={styles.cartDel} onClick={() => removeCartItem(it.listingId)}>移除</View>
              </View>
            ))}

            {/* 结算预览(合计/抵扣/运费) */}
            {preview && preview.totals && (
              <View className={styles.previewPanel}>
                <View className={styles.previewTitle}>
                  结算预览(实时重算{preview.crossShop ? ' · 跨店' : ''})
                </View>
                <View className={styles.priceRows}>
                  <View className={styles.priceRow}>
                    <Text>商品合计</Text>
                    <Text>{formatMoney(preview.totals.goodsTotal)}</Text>
                  </View>
                  <View className={styles.priceRow}>
                    <Text>信值抵扣</Text>
                    <Text className={styles.priceDeduct}>-{formatMoney(preview.totals.xinzhiCredit)}</Text>
                  </View>
                  <View className={styles.priceRow}>
                    <Text>运费(满{Math.round(preview.shippingRule.freeThreshold)}免)</Text>
                    <Text>{formatMoney(preview.totals.shippingFee)}</Text>
                  </View>
                  <View className={`${styles.priceRow} ${styles.priceFinal}`}>
                    <Text>应付合计</Text>
                    <Text>{formatMoney(preview.totals.actualAmount)}</Text>
                  </View>
                </View>
                <View className={styles.gateNote}>{preview.note}</View>
              </View>
            )}

            {/* 下单表单(地址+年龄门) */}
            <View className={styles.applyCard}>
              <View className={styles.subTitle}>下单(全量购物车 · XZ 前缀订单号)</View>
              <Input
                className={styles.formInput}
                value={addrText}
                onInput={e => setAddrText(e.detail.value)}
                placeholder="收货地址(必填)"
                maxlength={100}
              />
              <Input
                className={styles.formInput}
                value={orderRemark}
                onInput={e => setOrderRemark(e.detail.value)}
                placeholder="订单备注(选填)"
                maxlength={200}
              />
              <View className={styles.ageRow} onClick={() => setAgeOk(!ageOk)}>
                <View className={styles.ageCheck}>{ageOk ? '☑' : '☐'}</View>
                <View className={styles.ageText}>我确认已年满 18 周岁(酒类合规年龄门)</View>
              </View>
              <View className={styles.formBtn} onClick={submitOrder}>提交订单</View>
            </View>

            {/* 我的臻选订单(九态徽章) */}
            <View className={styles.sectionTitle} style={{ marginTop: '24rpx' }}>
              我的臻选订单
              <Text className={styles.sectionNote}>九态流转 · 抵扣明细透明</Text>
            </View>
            {orders.length === 0 && tradeLoaded && (
              <View className={styles.empty}>暂无臻选订单</View>
            )}
            {orders.map(o => (
              <View key={o.orderId} className={styles.orderCard}>
                <View className={styles.orderHead}>
                  <View className={styles.orderId}>{o.orderId}</View>
                  <View className={`${styles.stBadge} ${orderStatusColor(o.status)}`}>
                    {o.statusName || xinzhiOrderStatusName(o.status)}
                  </View>
                </View>
                <View className={styles.orderMeta}>
                  {o.shopName || '臻选店铺'} · {o.items.length} 件 · {formatMoney(o.priceDetail.actualAmount)}
                </View>
                <View className={styles.orderDeduct}>
                  信值抵扣 -{formatMoney(o.priceDetail.xinzhiCredit)} · 运费 {formatMoney(o.priceDetail.shippingFee)}
                </View>
                {o.payment.method && (
                  <View className={styles.orderMeta}>
                    支付: {xinzhiPayMethodName(o.payment.method)}
                    {o.payment.funding.length > 0 && ` · 资金源 ${o.payment.funding.map(f => f.source).join('+')}`}
                  </View>
                )}
                {o.logistics.carrier && (
                  <View className={styles.orderMeta}>物流: {o.logistics.carrier} {o.logistics.waybillNo}</View>
                )}

                {/* PENDING: 支付三通道 + 取消 */}
                {o.status === 'PENDING' && (
                  <>
                    <View className={styles.payRow}>
                      {Object.keys(XINZHI_PAY_METHOD).map(m => (
                        <View
                          key={m}
                          className={`${styles.payChip} ${payFor === o.orderId && payMethod === m ? styles.payChipOn : ''}`}
                          onClick={() => { setPayFor(o.orderId); setPayMethod(m); }}
                        >
                          {xinzhiPayMethodName(m)}
                        </View>
                      ))}
                    </View>
                    {payFor === o.orderId && payMethod !== 'wallet' && (
                      <Input
                        className={styles.formInput}
                        value={trustIdText}
                        onInput={e => setTrustIdText(e.detail.value)}
                        placeholder="信值档案 trustId(TV/组合支付必填)"
                        type="number"
                      />
                    )}
                    {payFor === o.orderId && (
                      <View className={styles.formBtn} onClick={() => payNow(o.orderId)}>确认支付</View>
                    )}
                    <View className={styles.cancelBtn} onClick={() => cancelOrderNow(o.orderId)}>取消订单(库存回补)</View>
                  </>
                )}

                {/* PAID + 店铺归属商家 → 发货 */}
                {o.status === 'PAID' && o.shopMemberId === myId && (
                  <>
                    <Input
                      className={styles.formInput}
                      value={shipForm.carrier}
                      onInput={e => setShipForm({ ...shipForm, carrier: e.detail.value })}
                      placeholder="承运商(商家发货)"
                      maxlength={30}
                    />
                    <Input
                      className={styles.formInput}
                      value={shipForm.waybillNo}
                      onInput={e => setShipForm({ ...shipForm, waybillNo: e.detail.value })}
                      placeholder="运单号"
                      maxlength={40}
                    />
                    <View className={styles.formBtn} onClick={() => shipNow(o.orderId)}>发货</View>
                  </>
                )}
                {o.status === 'PAID' && o.shopMemberId !== myId && (
                  <View className={styles.orderMeta}>等待商家发货…</View>
                )}

                {/* SHIPPED → 确认收货 */}
                {o.status === 'SHIPPED' && (
                  <View className={styles.formBtn} onClick={() => confirmOrderNow(o.orderId)}>确认收货</View>
                )}

                {/* RECEIVED → 评价 */}
                {o.status === 'RECEIVED' && (
                  <View className={styles.rateRow}>
                    {[1, 2, 3, 4, 5].map(n => (
                      <View key={n} className={styles.starBtn} onClick={() => reviewNow(o.orderId, n)}>{n}★</View>
                    ))}
                  </View>
                )}
              </View>
            ))}

            {/* 结算区(商家 mine / admin 总览+T+1+冲正) */}
            <View className={styles.sectionTitle} style={{ marginTop: '24rpx' }}>
              {isAdmin ? '结算管理(admin)' : '我的结算单(商家)'}
              <Text className={styles.sectionNote}>分账明细 · 资金源留痕</Text>
            </View>
            {isAdmin && (
              <View className={styles.adminPanel}>
                <View className={styles.formBtn} onClick={runSettle}>
                  {settleBusy ? '执行中…' : '执行 T+1 分账(幂等)'}
                </View>
                <View className={styles.adminNote}>
                  货款按结算单 merchantProceeds 入商家奖励余额(只可消费不可提现) · 平台费留痕 · 已 settled 自动跳过 · 冲正走人工轨
                </View>
              </View>
            )}
            {settleRows.length === 0 && tradeLoaded && (
              <View className={styles.empty}>暂无结算单</View>
            )}
            {settleRows.map(s => (
              <View key={s.settleId} className={styles.settleCard}>
                <View className={styles.settleHead}>
                  <View className={styles.settleId}>结算 #{s.settleId} · 订单 {s.orderId}</View>
                  <View className={`${styles.stBadge} ${settleStatusColor(s.status)}`}>
                    {s.statusLabel || xinzhiSettleStatusName(s.status)}
                  </View>
                </View>
                <View className={styles.priceRows}>
                  <View className={styles.priceRow}>
                    <Text>订单金额</Text>
                    <Text>{formatMoney(s.orderAmount)}</Text>
                  </View>
                  <View className={styles.priceRow}>
                    <Text>商家货款({Math.round((s.proceedsRate || 0) * 100)}%)</Text>
                    <Text>{formatMoney(s.merchantProceeds)}</Text>
                  </View>
                  <View className={styles.priceRow}>
                    <Text>平台费({Math.round((s.feeRate || 0) * 100)}%)</Text>
                    <Text>-{formatMoney(s.platformFee)}</Text>
                  </View>
                </View>
                {s.walletTxNo && (
                  <View className={styles.orderMeta}>钱包流水: {s.walletTxNo}</View>
                )}
                {s.reversalDebt > 0 && (
                  <View className={styles.orderDeduct}>
                    冲正负债 {formatMoney(s.reversalDebt)}(奖励余额不足, 追缴走人工/建议书轨)
                  </View>
                )}
                {isAdmin && s.status === 'settled' && (
                  <View className={styles.cancelBtn} onClick={() => reverseSettleNow(s.settleId)}>冲正(admin 人工)</View>
                )}
              </View>
            ))}
          </View>
        )}

        {/* ============ 五、邻里社区 ============ */}
        {tab === 'neighbor' && (
          <View className={styles.section}>
            <View className={styles.sectionTitle}>
              邻里臻选
              <Text className={styles.sectionNote}>
                {neighbor ? `品类聚合 · 匿名门槛 K=${neighbor.anonymityK}(零个体数据)` : ''}
              </Text>
            </View>
            {neighbor && neighbor.categories.length > 0 && (
              <View className={styles.nbGrid}>
                {neighbor.categories.map((c, i) => (
                  <View key={`${c.series}-${i}`} className={styles.nbItem}>
                    <View className={styles.nbSeries}>{c.series}</View>
                    <View className={styles.nbCount}>{c.buyerCount} 人在买</View>
                    <View className={styles.nbOrders}>{c.orderCount} 单</View>
                  </View>
                ))}
              </View>
            )}
            {neighbor && neighbor.categories.length === 0 && (
              <View className={styles.empty}>
                同城聚合数据积累中(不足 {neighbor.anonymityK} 人的品类不展示——保护隐私)
              </View>
            )}

            <View className={styles.sectionTitle} style={{ marginTop: '24rpx' }}>
              邻里求购大厅
              <Text className={styles.sectionNote}>紧急优先 · 距离优先 · 响应仅脱敏昵称</Text>
            </View>

            {/* 发布求购 */}
            <View className={styles.gbPublish}>
              <Input
                className={styles.gbInput}
                value={gbTitle}
                onInput={e => setGbTitle(e.detail.value)}
                placeholder="想买什么? 发个求购让邻里搭把手…"
                maxlength={60}
              />
              <View className={styles.gbPublishRow}>
                <View
                  className={`${styles.urgentTag} ${gbUrgent ? styles.urgentTagOn : ''}`}
                  onClick={() => setGbUrgent(!gbUrgent)}
                >
                  {gbUrgent ? '🔴 紧急' : '⚪ 普通'}
                </View>
                <View
                  className={styles.gbBtn}
                  onClick={submitGroupbuy}
                >
                  {gbSubmitting ? '发布中…' : '发布求购'}
                </View>
              </View>
            </View>

            {/* 求购列表 */}
            {groupbuys.length === 0 && !loading && (
              <View className={styles.empty}>附近暂无求购——发第一个, 让邻里看到您</View>
            )}
            {groupbuys.map(gb => (
              <View key={gb.groupbuyId} className={styles.gbCard}>
                <View className={styles.gbHead}>
                  {gb.urgency === 'urgent' && <View className={styles.gbUrgent}>紧急</View>}
                  <View className={styles.gbTitle}>{gb.title}</View>
                  {gb.distanceKm != null && (
                    <View className={styles.gbDist}>{gb.distanceKm}km</View>
                  )}
                </View>
                <View className={styles.gbMeta}>
                  {gb.publisherMasked} · {gb.responderCount} 人响应
                  {gb.series && ` · ${gb.series}`}
                  {gb.closed && ' · 已解决'}
                </View>
                {!gb.closed && (
                  <View className={styles.gbRespondBtn} onClick={() => respondGb(gb)}>
                    我能帮忙
                  </View>
                )}
                {gb.closed && gb.carbonGrams != null && (
                  <View className={styles.gbCarbon}>🌱 本单碳减排 {gb.carbonGrams}g</View>
                )}
              </View>
            ))}
          </View>
        )}

        {/* 灰度说明脚注 */}
        {mode && (
          <View className={styles.modeFooter}>
            {mode.mode === 'off'
              ? '部分功能灰度开放中(导购问答/求购发布), 观测功能全量可用'
              : `当前灰度态: ${mode.mode}${mode.paused ? '(护栏保护暂停)' : ''}`}
            {' · '}信值透明是宪法, 不是功能
          </View>
        )}

        <View className={styles.bottomSpace} />
      </ScrollView>
    </View>
  );
};

export default XinzhiPage;
