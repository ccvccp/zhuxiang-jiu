/**
 * AI智能叫帮 · 信值互助网络(67号)
 * 互助大厅(LBS+个性化) → 发布求助(AI 解析推荐) → 我的互助(履约流转+评价+信值档案)
 * 公益 100% / 有偿 10% 信值双轨 · 平台零佣金
 * P1 智能调度: 偏好推荐 · 信值捐赠 · 互助故事卡 · 荣誉徽章
 * P2 生态深化: 互助接力 · 信值传承(数字功德碑)
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input, Textarea, Picker } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  HelpAPI, HelpOrderVO, TrustProfileVO, StoryCardVO, HeritageVO,
  helpCategoryName, helpStatusName, HELP_CATEGORY_NAME,
} from '@/api/help';
import { requireLogin } from '@/services/auth-service';
import { getMemberId } from '@/services/auth-service';

type Tab = 'hall' | 'publish' | 'mine';

const TABS: { key: Tab; label: string }[] = [
  { key: 'hall', label: '互助大厅' },
  { key: 'publish', label: '发布求助' },
  { key: 'mine', label: '我的互助' },
];

// 类目筛选(全部 + 六类)
const CAT_TABS = ['', ...Object.keys(HELP_CATEGORY_NAME)];
// 模式筛选
const MODE_TABS = [
  { key: '', label: '全部' },
  { key: 'public', label: '公益' },
  { key: 'paid', label: '有偿' },
];
// 预设地点(与代驾页一致)
const PRESETS = [
  { name: '泉城广场(市中心)', lat: 36.6634, lng: 117.0268 },
  { name: '竹韵大酒店(历下区)', lat: 36.6612, lng: 117.1201 },
];
const LOC_IDX = 1;

const formatDate = (t?: string): string => (t ? t.slice(0, 16).replace('T', ' ') : '');

const HelpPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('hall');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  // 大厅
  const [orders, setOrders] = useState<HelpOrderVO[]>([]);
  const [catFilter, setCatFilter] = useState('');
  const [modeFilter, setModeFilter] = useState('');
  // 发布表单
  const [title, setTitle] = useState('');
  const [desc, setDesc] = useState('');
  const [parseHint, setParseHint] = useState('');
  const [pubMode, setPubMode] = useState('public');
  const [pubCat, setPubCat] = useState('other');
  const [duration, setDuration] = useState('60');
  const [price, setPrice] = useState('');
  const [address, setAddress] = useState('');
  const [urgency, setUrgency] = useState(false);
  // 我的
  const [myPub, setMyPub] = useState<HelpOrderVO[]>([]);
  const [myHelped, setMyHelped] = useState<HelpOrderVO[]>([]);
  const [trust, setTrust] = useState<TrustProfileVO | null>(null);
  // 评价弹层
  const [reviewOrder, setReviewOrder] = useState<HelpOrderVO | null>(null);
  const [reviewScore, setReviewScore] = useState(5);
  const [reviewContent, setReviewContent] = useState('');
  // P1: 故事卡弹层 + 捐赠弹层
  const [storyCard, setStoryCard] = useState<StoryCardVO | null>(null);
  const [donateOrder, setDonateOrder] = useState<HelpOrderVO | null>(null);
  const [donateAmount, setDonateAmount] = useState(5);
  // P2: 接力发布 + 信值传承弹层
  const [pubRelay, setPubRelay] = useState(false);
  const [pubLegs, setPubLegs] = useState('3');
  const [heritageOpen, setHeritageOpen] = useState(false);
  const [heritageOut, setHeritageOut] = useState<HeritageVO[]>([]);
  const [heritageIn, setHeritageIn] = useState<HeritageVO[]>([]);
  const [heritageHeirId, setHeritageHeirId] = useState('');
  const [heritageAmount, setHeritageAmount] = useState('');

  const myId = Number(getMemberId() || 0);

  const loadHall = useCallback(async (cat: string, mode: string) => {
    const loc = PRESETS[LOC_IDX];
    try {
      const list = await HelpAPI.hall({
        longitude: loc.lng, latitude: loc.lat,
        category: cat || undefined, mode: mode || undefined,
      });
      setOrders(list);
    } catch (e) {
      console.warn('[help] 大厅加载失败:', e);
      setOrders([]);
    }
  }, []);

  const loadMine = useCallback(async () => {
    const [pub, helped, t] = await Promise.all([
      HelpAPI.myPublished().catch(() => [] as HelpOrderVO[]),
      HelpAPI.myHelped().catch(() => [] as HelpOrderVO[]),
      HelpAPI.trustProfile().catch(() => null),
    ]);
    setMyPub(pub);
    setMyHelped(helped);
    setTrust(t);
  }, []);

  useEffect(() => {
    if (!requireLogin()) {
      setLoading(false);
      return;
    }
    (async () => {
      await Promise.all([loadHall('', ''), loadMine()]);
      setLoading(false);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 大厅筛选
  const handleFilter = async (cat: string, mode: string) => {
    setCatFilter(cat);
    setModeFilter(mode);
    await loadHall(cat, mode);
  };

  // 标题/描述变化 → AI 解析(防抖 600ms)
  let parseTimer: any = null;
  const handleParseInput = (val: string, isTitle: boolean) => {
    if (isTitle) setTitle(val); else setDesc(val);
    if (parseTimer) clearTimeout(parseTimer);
    parseTimer = setTimeout(async () => {
      const text = isTitle ? `${val} ${desc}` : `${title} ${val}`;
      if (!text.trim()) { setParseHint(''); return; }
      try {
        const r = await HelpAPI.parse(text.trim(), '');
        if (r.banned) {
          setParseHint(`⚠ 含违禁内容(${r.banned}), 不可发布`);
          return;
        }
        setPubCat(r.category);
        setPubMode(r.suggestedMode);
        setParseHint(r.hint);
      } catch (_) { /* 解析失败不打扰 */ }
    }, 600);
  };

  // 发布
  const handlePublish = async () => {
    if (submitting) return;
    if (!title.trim()) {
      Taro.showToast({ title: '请输入求助标题', icon: 'none' });
      return;
    }
    const dur = Number(duration);
    if (!dur || dur < 10) {
      Taro.showToast({ title: '预计时长至少 10 分钟', icon: 'none' });
      return;
    }
    if (pubMode === 'paid' && !(Number(price) > 0)) {
      Taro.showToast({ title: '有偿模式请填写协商价格', icon: 'none' });
      return;
    }
    const loc = PRESETS[LOC_IDX];
    setSubmitting(true);
    try {
      const order = await HelpAPI.publish({
        mode: pubMode, category: pubCat,
        title: title.trim(), description: desc.trim(),
        longitude: loc.lng, latitude: loc.lat,
        address: address.trim() || loc.name,
        durationMinutes: dur,
        price: pubMode === 'paid' ? Number(price) : 0,
        urgency: urgency ? 'urgent' : 'normal',
        relay: pubRelay,
        legs: pubRelay ? Number(pubLegs) : 0,
      });
      const modeText = pubMode === 'public'
        ? `公益互助, 完成后帮助者获得 ${order.trustValueReward} 信值(100% 记录)`
        : `有偿互助 ¥${order.price}, 完成后帮助者获得 ${order.trustValueReward} 信值(公益标准 10%)`;
      const relayText = pubRelay
        ? ` 接力模式 ${order.relayLegs?.length ?? 0} 段分段完成。` : '';
      Taro.showModal({
        title: '求助已发布',
        content: `单号 ${order.orderId}, ${modeText}。${relayText}`,
        showCancel: false,
      });
      setTitle(''); setDesc(''); setParseHint('');
      setPrice(''); setAddress('');
      setPubRelay(false); setPubLegs('3');
      setTab('hall');
      await loadHall(catFilter, modeFilter);
      await loadMine();
    } catch (e) {
      console.warn('[help] 发布失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 接单
  const handleAccept = async (o: HelpOrderVO) => {
    if (submitting) return;
    const res = await Taro.showModal({
      title: '确认接单',
      content: `${o.title}\n${o.mode === 'public' ? `公益互助, 完成获得 ${o.trustValueReward} 信值` : `有偿 ¥${o.price}, 完成获得 ${o.trustValueReward} 信值`}`,
    });
    if (!res.confirm) return;
    setSubmitting(true);
    try {
      await HelpAPI.accept(o.orderId);
      Taro.showToast({ title: '接单成功, 请与求助者联系', icon: 'success', duration: 2000 });
      await loadHall(catFilter, modeFilter);
      await loadMine();
      setTab('mine');
    } catch (e) {
      console.warn('[help] 接单失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 流转
  const handleFlow = async (o: HelpOrderVO, action: 'start' | 'complete') => {
    if (submitting) return;
    setSubmitting(true);
    try {
      if (action === 'start') {
        await HelpAPI.start(o.orderId);
        Taro.showToast({ title: '服务开始', icon: 'success' });
      } else {
        const r = await HelpAPI.complete(o.orderId);
        const settlement = (r.data || {}).settlement || {};
        Taro.showModal({
          title: '互助完成',
          content: `帮助者 +${settlement.helperTrustDelta ?? o.trustValueReward} 信值`
            + (settlement.publisherThanks ? `, 发布者 +${settlement.publisherThanks} 感谢信值` : '')
            + '。别忘了评价这次互助!',
          showCancel: false,
        });
      }
      await loadMine();
    } catch (e) {
      console.warn('[help] 流转失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 取消
  const handleCancel = async (o: HelpOrderVO) => {
    if (submitting) return;
    const isHelper = o.helperId === myId;
    const res = await Taro.showModal({
      title: '确认取消',
      content: isHelper && o.status === 'matched'
        ? '接单后取消将扣减 2 信值, 确认取消?'
        : '确认取消该求助?',
    });
    if (!res.confirm) return;
    setSubmitting(true);
    try {
      await HelpAPI.cancel(o.orderId, '用户主动取消');
      Taro.showToast({ title: '已取消', icon: 'none' });
      await loadMine();
      await loadHall(catFilter, modeFilter);
    } catch (e) {
      console.warn('[help] 取消失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 提交评价
  const handleReview = async () => {
    if (!reviewOrder || submitting) return;
    setSubmitting(true);
    try {
      await HelpAPI.review(reviewOrder.orderId, reviewScore, reviewContent.trim());
      Taro.showToast({ title: '评价已提交', icon: 'success' });
      setReviewOrder(null);
      setReviewContent('');
      await loadMine();
    } catch (e) {
      console.warn('[help] 评价失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // P1: 查看互助故事卡
  const handleStory = async (o: HelpOrderVO) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      const card = await HelpAPI.storyCard(o.orderId);
      setStoryCard(card);
    } catch (e) {
      console.warn('[help] 故事卡加载失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // P1: 信值捐赠
  const handleDonate = async () => {
    if (!donateOrder || submitting) return;
    setSubmitting(true);
    try {
      const r = await HelpAPI.donate(donateOrder.orderId, donateAmount);
      Taro.showModal({
        title: '捐赠成功',
        content: `已捐赠 ${donateAmount} 信值, 该求助完成时帮助者将额外获得社区捐赠。您剩余 ${r.donorTrustLeft} 信值。`,
        showCancel: false,
      });
      setDonateOrder(null);
      await loadHall(catFilter, modeFilter);
      await loadMine();
    } catch (e) {
      console.warn('[help] 捐赠失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // P2: 打开信值传承弹层(拉取两向记录)
  const openHeritage = async () => {
    setHeritageOpen(true);
    try {
      const h = await HelpAPI.heritageMy();
      setHeritageOut(h.outgoing);
      setHeritageIn(h.incoming);
    } catch (e) {
      console.warn('[help] 传承记录加载失败:', e);
    }
  };

  // P2: 发起传承
  const handleHeritageApply = async () => {
    if (submitting) return;
    const heir = Number(heritageHeirId);
    if (!heir || heir <= 0) {
      Taro.showToast({ title: '请输入受让人成员ID', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const amount = heritageAmount ? Number(heritageAmount) : undefined;
      await HelpAPI.heritageApply(heir, amount);
      Taro.showToast({ title: '传承已发起, 待受让人确认', icon: 'success' });
      setHeritageHeirId('');
      setHeritageAmount('');
      const h = await HelpAPI.heritageMy();
      setHeritageOut(h.outgoing);
      setHeritageIn(h.incoming);
    } catch (e) {
      console.warn('[help] 传承发起失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // P2: 确认受让传承
  const handleHeritageAccept = async (h: HeritageVO) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      const r = await HelpAPI.heritageAccept(h.heritageId);
      Taro.showModal({
        title: '传承确认成功',
        content: `已承继 ${r.transferred} 公益信值, 善行延续。`,
        showCancel: false,
      });
      const hh = await HelpAPI.heritageMy();
      setHeritageOut(hh.outgoing);
      setHeritageIn(hh.incoming);
      await loadMine();
    } catch (e) {
      console.warn('[help] 传承确认失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 渲染订单卡片(大厅)
  const renderHallCard = (o: HelpOrderVO) => {
    // P1: 捐赠入口(公益单 + 非发布者 + 高信值用户)
    const canDonate = o.mode === 'public'
      && o.publisherId !== myId
      && trust != null
      && (trust.donateGate ?? 50) <= trust.totalTrust;
    return (
      <View key={o.orderId} className={styles.orderCard}>
        <View className={styles.orderTop}>
          <View className={styles.orderTitleRow}>
            {o.urgency === 'urgent' && <Text className={styles.urgentBadge}>紧急</Text>}
            {o.isRelay && o.relayMeta && (
              <Text className={styles.relayBadge}>接力 {o.relayMeta.completedLegs}/{o.relayMeta.totalLegs}段</Text>
            )}
            {o.fitScore === 1 && o.fitReason && (
              <Text className={styles.fitBadge}>✨ {o.fitReason}</Text>
            )}
            <Text className={styles.orderMode}>{o.mode === 'public' ? '公益' : `有偿 ¥${o.price}`}</Text>
            <Text className={styles.orderCat}>{helpCategoryName(o.category)}</Text>
          </View>
          {o.publisherId !== myId && (
            <View className={styles.acceptBtn} onClick={() => handleAccept(o)}>接单</View>
          )}
        </View>
        <View className={styles.orderTitle}>{o.title}</View>
        {o.description && <View className={styles.orderDesc}>{o.description}</View>}
        <View className={styles.orderMeta}>
          {helpCategoryName(o.category)} · {o.durationMinutes} 分钟 · 信值 +{o.trustValueReward}
          {o.distanceKm != null ? ` · 距离 ${o.distanceKm}km` : ''}
          {(o.donatedTrust ?? 0) > 0 ? ` · 💛 社区已捐 ${o.donatedTrust} 信值` : ''}
        </View>
        {o.isRelay && o.relayMeta && (
          <View className={styles.relayAddr}>
            🤝 当前第 {o.relayMeta.currentLegNo} 段: {o.relayMeta.currentLegAddress}
          </View>
        )}
        {o.address && !o.isRelay && <View className={styles.orderAddr}>📍 {o.address}</View>}
        {canDonate && (
          <View
            className={styles.donateLink}
            onClick={() => { setDonateOrder(o); setDonateAmount(5); }}
          >
            💛 为这份公益捐赠信值
          </View>
        )}
      </View>
    );
  };

  // 渲染我的互助卡片
  const renderMineCard = (o: HelpOrderVO, isHelper: boolean) => {
    const canStart = isHelper && o.status === 'matched';
    const canComplete = o.status === 'in_progress'
      && (isHelper || o.publisherId === myId);
    const canCancel = o.status === 'published'
      || (o.status === 'matched' && (isHelper || o.publisherId === myId));
    const canReview = o.status === 'completed';
    const canStory = o.status === 'completed';   // P1: 故事卡入口
    return (
      <View key={o.orderId} className={styles.orderCard}>
        <View className={styles.orderTop}>
          <View className={styles.orderTitleRow}>
            <Text className={styles.orderMode}>{o.mode === 'public' ? '公益' : `有偿 ¥${o.price}`}</Text>
            <Text className={`${styles.statusBadge} ${o.status === 'completed' ? styles.statusDone : ''}`}>
              {helpStatusName(o.status)}
            </Text>
          </View>
        </View>
        <View className={styles.orderTitle}>{o.title}</View>
        <View className={styles.orderMeta}>
          {helpCategoryName(o.category)} · {o.durationMinutes} 分钟 · 信值 {o.trustValueReward}
          {isHelper ? '(我帮助)' : '(我发布)'}
        </View>
        {o.isRelay && o.relayLegs && (
          <View className={styles.relayProgress}>
            {o.relayLegs.map(l => (
              <Text key={l.legNo} className={`${styles.relayDot} ${l.status === 'completed' ? styles.relayDotDone : ''}`}>
                {l.status === 'completed' ? '●' : '○'}
              </Text>
            ))}
            <Text className={styles.relayText}>
              接力 {o.relayLegs.filter(l => l.status === 'completed').length}/{o.relayLegs.length} 段
            </Text>
          </View>
        )}
        {(canStart || canComplete || canCancel || canReview || canStory) && (
          <View className={styles.actionRow}>
            {canStart && (
              <View className={styles.flowBtn} onClick={() => handleFlow(o, 'start')}>开始服务</View>
            )}
            {canComplete && (
              <View className={styles.flowBtn} onClick={() => handleFlow(o, 'complete')}>确认完成</View>
            )}
            {canReview && (
              <View className={styles.flowBtnGhost} onClick={() => {
                setReviewOrder(o);
                setReviewScore(5);
                setReviewContent('');
              }}>评价</View>
            )}
            {canStory && (
              <View className={styles.flowBtnGhost} onClick={() => handleStory(o)}>故事卡</View>
            )}
            {canCancel && (
              <View className={styles.cancelLink} onClick={() => handleCancel(o)}>取消</View>
            )}
          </View>
        )}
      </View>
    );
  };

  return (
    <View className={styles.page}>
      <NavBar title="AI智能叫帮" />
      {/* 信值概要卡 */}
      {trust && (
        <View className={styles.trustCard}>
          <View className={styles.trustHeader}>
            <Text className={styles.trustTitle}>我的互助信值</Text>
            <Text className={styles.trustTotal}>{trust.totalTrust}</Text>
          </View>
          {trust.honor && (
            <View className={styles.honorRow}>
              <Text className={styles.honorIcon}>{trust.honor.icon}</Text>
              <View className={styles.honorInfo}>
                <Text className={styles.honorName}>{trust.honor.name}</Text>
                {trust.honor.nextAt != null && (
                  <View className={styles.honorProgress}>
                    <View className={styles.honorProgressFill} style={{ width: `${Math.min(100, Math.round(trust.honor.progress * 100))}%` }} />
                  </View>
                )}
                <Text className={styles.honorNext}>
                  {trust.honor.nextAt != null
                    ? `公益信值 ${trust.honor.nextAt} 晋升下一级`
                    : '已达最高荣誉 · 数字功德碑'}
                </Text>
              </View>
            </View>
          )}
          <View className={styles.trustGrid}>
            <View className={styles.trustItem}>
              <View className={styles.trustValue}>{trust.publicTrust}</View>
              <View className={styles.trustLabel}>公益信值</View>
            </View>
            <View className={styles.trustItem}>
              <View className={styles.trustValue}>{trust.paidTrust}</View>
              <View className={styles.trustLabel}>有偿信值(10%)</View>
            </View>
            <View className={styles.trustItem}>
              <View className={styles.trustValue}>
                {trust.ratingAvg != null ? trust.ratingAvg : '--'}
              </View>
              <View className={styles.trustLabel}>服务评分({trust.ratingCount})</View>
            </View>
          </View>
          {(trust.carbonGrams ?? 0) > 0 && (
            <View className={styles.carbonRow}>
              🌱 互助碳减排 {(trust.carbonGrams ?? 0) >= 1000
                ? `${((trust.carbonGrams ?? 0) / 1000).toFixed(1)}kg`
                : `${trust.carbonGrams}g`} — 您的善行也在守护地球
            </View>
          )}
          {trust.totalTrust < trust.gates.paid && (
            <View className={styles.trustGate}>
              公益互助积累 {trust.gates.paid} 信值后解锁有偿接单
            </View>
          )}
        </View>
      )}

      {/* Tabs */}
      <View className={styles.tabBar}>
        {TABS.map(t => (
          <View
            key={t.key}
            className={`${styles.tabItem} ${tab === t.key ? styles.tabItemActive : ''}`}
            onClick={async () => {
              setTab(t.key);
              if (t.key === 'hall') await loadHall(catFilter, modeFilter);
              if (t.key === 'mine') await loadMine();
            }}
          >
            {t.label}
          </View>
        ))}
      </View>

      <ScrollView scrollY className={styles.scrollView}>
        {loading ? (
          <View className={styles.empty}>加载中...</View>
        ) : tab === 'hall' ? (
          <>
            {/* 模式筛选 */}
            <View className={styles.filterRow}>
              {MODE_TABS.map(m => (
                <View
                  key={m.key}
                  className={`${styles.filterItem} ${modeFilter === m.key ? styles.filterActive : ''}`}
                  onClick={() => handleFilter(catFilter, m.key)}
                >
                  {m.label}
                </View>
              ))}
            </View>
            {/* 类目筛选 */}
            <ScrollView scrollX className={styles.catBar}>
              {CAT_TABS.map(c => (
                <View
                  key={c || 'all'}
                  className={`${styles.catItem} ${catFilter === c ? styles.catActive : ''}`}
                  onClick={() => handleFilter(c, modeFilter)}
                >
                  {c ? helpCategoryName(c) : '全部'}
                </View>
              ))}
            </ScrollView>
            {orders.length === 0 ? (
              <View className={styles.empty}>
                <View className={styles.emptyIcon}>🤝</View>
                <View>附近暂无求助, 发布一个或切换筛选</View>
              </View>
            ) : (
              orders.map(renderHallCard)
            )}
          </>
        ) : tab === 'publish' ? (
          <View className={styles.card}>
            {parseHint && <View className={styles.parseHint}>💡 {parseHint}</View>}
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={title}
                onInput={(e) => handleParseInput((e.detail as any).value, true)}
                placeholder="求助标题(如 家里灯坏了, 老人腿脚不便)"
                placeholderClass={styles.placeholder}
                maxlength={60}
              />
            </View>
            <View className={styles.textareaRow}>
              <Textarea
                className={styles.textarea}
                value={desc}
                onInput={(e) => handleParseInput((e.detail as any).value, false)}
                placeholder="详细描述(时间地点/特殊情况; AI 将自动识别类型并推荐模式)"
                placeholderClass={styles.placeholder}
                maxlength={500}
              />
            </View>
            {/* 模式切换 */}
            <View className={styles.modeRow}>
              <View
                className={`${styles.modeItem} ${pubMode === 'public' ? styles.modeActive : ''}`}
                onClick={() => setPubMode('public')}
              >
                <View className={styles.modeName}>公益互助</View>
                <View className={styles.modeDesc}>信值 100% 记录</View>
              </View>
              <View
                className={`${styles.modeItem} ${pubMode === 'paid' ? styles.modeActive : ''}`}
                onClick={() => setPubMode('paid')}
              >
                <View className={styles.modeName}>有偿互助</View>
                <View className={styles.modeDesc}>信值 10% · 零佣金</View>
              </View>
            </View>
            <Picker
              mode="selector"
              range={Object.keys(HELP_CATEGORY_NAME).map(helpCategoryName)}
              value={Object.keys(HELP_CATEGORY_NAME).indexOf(pubCat) >= 0
                ? Object.keys(HELP_CATEGORY_NAME).indexOf(pubCat) : 5}
              onChange={(e) => setPubCat(Object.keys(HELP_CATEGORY_NAME)[Number((e.detail as any).value)])}
            >
              <View className={styles.pickerRow}>
                <Text className={styles.pickerLabel}>求助类型</Text>
                <Text className={styles.pickerValue}>{helpCategoryName(pubCat)}</Text>
                <Text className={styles.pickerArrow}>›</Text>
              </View>
            </Picker>
            <View className={styles.inputRow}>
              <Text className={styles.pickerLabel}>时长(分)</Text>
              <Input
                className={styles.input}
                type="number"
                value={duration}
                onInput={(e) => setDuration((e.detail as any).value)}
                placeholder="预计时长(分钟)"
                placeholderClass={styles.placeholder}
              />
            </View>
            {pubMode === 'paid' && (
              <View className={styles.inputRow}>
                <Text className={styles.pickerLabel}>¥ 协商价</Text>
                <Input
                  className={styles.input}
                  type="digit"
                  value={price}
                  onInput={(e) => setPrice((e.detail as any).value)}
                  placeholder="双方协商价格(平台零佣金)"
                  placeholderClass={styles.placeholder}
                />
              </View>
            )}
            <View className={styles.inputRow}>
              <Text className={styles.pickerLabel}>地址</Text>
              <Input
                className={styles.input}
                value={address}
                onInput={(e) => setAddress((e.detail as any).value)}
                placeholder="地址描述(选填, 默认市中心)"
                placeholderClass={styles.placeholder}
                maxlength={60}
              />
            </View>
            <View className={styles.urgencyRow} onClick={() => setUrgency(!urgency)}>
              <Text className={styles.pickerLabel}>紧急求助</Text>
              <Text className={styles.switchText}>{urgency ? '已开启(优先展示)' : '关闭'}</Text>
            </View>
            {/* P2: 互助接力 */}
            <View className={styles.urgencyRow} onClick={() => setPubRelay(!pubRelay)}>
              <Text className={styles.pickerLabel}>互助接力</Text>
              <Text className={styles.switchText}>{pubRelay ? '已开启(多人分段)' : '关闭'}</Text>
            </View>
            {pubRelay && (
              <Picker
                mode="selector"
                range={['2 段', '3 段', '4 段', '5 段']}
                value={Math.max(0, Number(pubLegs) - 2)}
                onChange={(e) => setPubLegs(String(Number((e.detail as any).value) + 2))}
              >
                <View className={styles.pickerRow}>
                  <Text className={styles.pickerLabel}>接力段数</Text>
                  <Text className={styles.pickerValue}>{pubLegs} 段</Text>
                  <Text className={styles.pickerArrow}>›</Text>
                </View>
              </Picker>
            )}
            <View className={styles.publishBtn} onClick={handlePublish}>
              {submitting ? '发布中...' : '发布求助'}
            </View>
          </View>
        ) : (
          <>
            {/* 我帮助的 */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>我接的互助</View>
              {myHelped.length === 0 ? (
                <View className={styles.empty}>
                  <View className={styles.emptyIcon}>💪</View>
                  <View>还没有接单, 去大厅看看谁需要帮助</View>
                </View>
              ) : myHelped.map(o => renderMineCard(o, true))}
            </View>
            {/* 我发布的 */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>我发布的求助</View>
              {myPub.length === 0 ? (
                <View className={styles.empty}>暂无发布记录</View>
              ) : myPub.map(o => renderMineCard(o, false))}
            </View>
            {/* P2: 信值传承入口(数字功德碑) */}
            <View className={styles.heritageEntry} onClick={openHeritage}>
              <Text className={styles.heritageIcon}>🌳</Text>
              <View className={styles.heritageInfo}>
                <View className={styles.heritageName}>信值传承 · 数字功德碑</View>
                <View className={styles.heritageDesc}>公益信值可传承给亲属, 善行代代相传</View>
              </View>
              <View className={styles.heritageArrow}>›</View>
            </View>
            {/* 信值流水 */}
            {trust && trust.ledger.length > 0 && (
              <View className={styles.card}>
                <View className={styles.cardTitle}>信值流水</View>
                {trust.ledger.map(l => (
                  <View key={l.ledgerId} className={styles.ledgerRow}>
                    <View className={styles.ledgerLeft}>
                      <View className={styles.ledgerReason}>{l.reason}</View>
                      <View className={styles.ledgerTime}>{formatDate(l.createdAt)}</View>
                    </View>
                    <View className={`${styles.ledgerDelta} ${l.delta >= 0 ? styles.deltaPlus : styles.deltaMinus}`}>
                      {l.delta >= 0 ? `+${l.delta}` : l.delta}
                    </View>
                  </View>
                ))}
              </View>
            )}
          </>
        )}

        {/* 规则说明 */}
        <View className={styles.noteCard}>
          <View className={styles.noteTitle}>互助信值规则</View>
          <View className={styles.noteLine}>· 公益互助: 信值 100% 记录(时长 × 类型基准), 零门槛人人可助人</View>
          <View className={styles.noteLine}>· 有偿互助: 双方协商价格, 平台零佣金; 信值按公益标准 10% 记录</View>
          <View className={styles.noteLine}>· 累计信值 ≥20 解锁有偿接单资格; 公益单发布者另获感谢信值</View>
          <View className={styles.noteLine}>· 接单后无故取消扣 2 信值; 服务差评(≤2星)扣 5 信值</View>
          <View className={styles.noteLine}>· 互助接力多段完成; 公益信值可传承(数字功德碑); 高信值可捐赠</View>
          <View className={styles.noteLine}>· 违禁需求(代考/涉黄/赌博等)禁止发布, 全程规则可解释</View>
        </View>
        <View className={styles.bottomSpacer} />
      </ScrollView>

      {/* 评价弹层 */}
      {reviewOrder && (
        <View className={styles.mask} onClick={() => setReviewOrder(null)}>
          <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
            <View className={styles.sheetTitle}>评价这次互助</View>
            <View className={styles.sheetDesc}>{reviewOrder.title}</View>
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
                placeholder="说说这次互助的体验(选填)"
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

      {/* P1: 互助故事卡弹层 */}
      {storyCard && (
        <View className={styles.mask} onClick={() => setStoryCard(null)}>
          <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
            <View className={styles.sheetTitle}>互助故事卡</View>
            <View className={styles.storyCard}>
              <View className={styles.storyTheme}>{storyCard.theme}</View>
              <View className={styles.storyTitle}>{storyCard.title}</View>
              <View className={styles.storyMeta}>
                {storyCard.categoryName} · {storyCard.date} · 帮助者获得 {storyCard.trustValueReward} 信值
                {storyCard.donatedTrust > 0 ? `(含社区捐赠 ${storyCard.donatedTrust})` : ''}
              </View>
              {storyCard.publisherReview && (
                <View className={styles.storyReview}>「{storyCard.publisherReview}」— 受助者</View>
              )}
              {storyCard.helperReview && (
                <View className={styles.storyReview}>「{storyCard.helperReview}」— 帮助者</View>
              )}
            </View>
            <View
              className={styles.sheetBtn}
              onClick={() => {
                Taro.setClipboardData({ data: storyCard.shareText });
                setStoryCard(null);
              }}
            >
              复制分享文案
            </View>
          </View>
        </View>
      )}

      {/* P1: 信值捐赠弹层 */}
      {donateOrder && (
        <View className={styles.mask} onClick={() => setDonateOrder(null)}>
          <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
            <View className={styles.sheetTitle}>为公益捐赠信值</View>
            <View className={styles.sheetDesc}>{donateOrder.title}</View>
            <View className={styles.donateNote}>
              您的捐赠将在求助完成时额外奖励帮助者, 让善意流动。当前剩余 {trust?.totalTrust ?? 0} 信值。
            </View>
            <View className={styles.amountRow}>
              {[1, 3, 5, 10].map(a => (
                <View
                  key={a}
                  className={`${styles.amountItem} ${donateAmount === a ? styles.amountActive : ''}`}
                  onClick={() => setDonateAmount(a)}
                >
                  {a} 信值
                </View>
              ))}
            </View>
            <View className={styles.sheetBtn} onClick={handleDonate}>
              {submitting ? '捐赠中...' : `捐赠 ${donateAmount} 信值`}
            </View>
          </View>
        </View>
      )}

      {/* P2: 信值传承弹层(数字功德碑) */}
      {heritageOpen && (
        <View className={styles.mask} onClick={() => setHeritageOpen(false)}>
          <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
            <View className={styles.sheetTitle}>信值传承 · 数字功德碑</View>
            <View className={styles.sheetDesc}>仅公益信值可传承(≥10), 受让人确认后即时划转, 双边留痕</View>

            {/* 待我确认的传承 */}
            {heritageIn.filter(h => h.status === 'pending').length > 0 && (
              <View className={styles.heritageSection}>
                <View className={styles.heritageSectionTitle}>待我确认的传承</View>
                {heritageIn.filter(h => h.status === 'pending').map(h => (
                  <View key={h.heritageId} className={styles.heritageRow}>
                    <View className={styles.heritageRowInfo}>
                      <View className={styles.heritageRowMain}>
                        成员 {h.ownerId} 传承给您 {h.declaredAmount} 公益信值
                      </View>
                      <View className={styles.ledgerTime}>{formatDate(h.createdAt)}</View>
                    </View>
                    <View className={styles.flowBtn} onClick={() => handleHeritageAccept(h)}>确认承继</View>
                  </View>
                ))}
              </View>
            )}

            {/* 发起传承 */}
            <View className={styles.heritageSection}>
              <View className={styles.heritageSectionTitle}>发起传承</View>
              <View className={styles.inputRow}>
                <Text className={styles.pickerLabel}>受让人ID</Text>
                <Input
                  className={styles.input}
                  type="number"
                  value={heritageHeirId}
                  onInput={(e) => setHeritageHeirId((e.detail as any).value)}
                  placeholder="受让人的成员编号"
                  placeholderClass={styles.placeholder}
                />
              </View>
              <View className={styles.inputRow}>
                <Text className={styles.pickerLabel}>额度</Text>
                <Input
                  className={styles.input}
                  type="digit"
                  value={heritageAmount}
                  onInput={(e) => setHeritageAmount((e.detail as any).value)}
                  placeholder={`留空 = 全部公益信值(${trust?.publicTrust ?? 0})`}
                  placeholderClass={styles.placeholder}
                />
              </View>
              <View className={styles.sheetBtn} onClick={handleHeritageApply}>
                {submitting ? '发起中...' : '发起传承'}
              </View>
            </View>

            {/* 我的传承记录 */}
            {heritageOut.length > 0 && (
              <View className={styles.heritageSection}>
                <View className={styles.heritageSectionTitle}>我发起的传承</View>
                {heritageOut.map(h => (
                  <View key={h.heritageId} className={styles.heritageRow}>
                    <View className={styles.heritageRowInfo}>
                      <View className={styles.heritageRowMain}>
                        → 成员 {h.heirId} · {h.declaredAmount} 信值
                      </View>
                      <View className={styles.ledgerTime}>
                        {h.status === 'done'
                          ? `已划转 ${h.transferredAmount} (${formatDate(h.confirmedAt)})`
                          : '待对方确认'}
                      </View>
                    </View>
                    <Text className={h.status === 'done' ? styles.deltaPlus : styles.relayText}>
                      {h.status === 'done' ? '已完成' : '待确认'}
                    </Text>
                  </View>
                ))}
              </View>
            )}
            {heritageIn.filter(h => h.status === 'done').length > 0 && (
              <View className={styles.heritageSection}>
                <View className={styles.heritageSectionTitle}>我承继的记录</View>
                {heritageIn.filter(h => h.status === 'done').map(h => (
                  <View key={h.heritageId} className={styles.heritageRow}>
                    <View className={styles.heritageRowInfo}>
                      <View className={styles.heritageRowMain}>
                        ← 成员 {h.ownerId} · {h.transferredAmount} 信值
                      </View>
                      <View className={styles.ledgerTime}>{formatDate(h.confirmedAt)}</View>
                    </View>
                    <Text className={styles.deltaPlus}>已入账</Text>
                  </View>
                ))}
              </View>
            )}
          </View>
        </View>
      )}
    </View>
  );
};

export default HelpPage;
