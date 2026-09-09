/**
 * 市级网店 · 我的市店
 * SVIP 开店(城市独占) → 审核 → 运营(月度考核/折扣/关联订单)
 * 数据来源: 后端 /api/citystore/*
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input, Picker } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  CityStoreAPI, CityStoreVO, CityVO, StoreAssessmentVO, StoreOrderVO,
  storeStatusName, qualStatusName, SALES_CHANNEL_NAME,
} from '@/api/citystore';
import { MemberAPI } from '@/api/member';
import { requireLogin } from '@/services/auth-service';

const formatDate = (t?: string | null): string => (t ? t.slice(0, 10) : '');

// 状态 → 徽标样式
const STATUS_CLS: Record<number, string> = {
  0: 'pending',
  1: 'operating',
  2: 'warning',
  3: 'suspended',
  4: 'cancelled',
};

const CityStorePage: React.FC = () => {
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [memberLevel, setMemberLevel] = useState(0);
  // 我的网店
  const [store, setStore] = useState<CityStoreVO | null>(null);
  const [assessments, setAssessments] = useState<StoreAssessmentVO[]>([]);
  const [orders, setOrders] = useState<StoreOrderVO[]>([]);
  // 开店面板
  const [showApply, setShowApply] = useState(false);
  const [cities, setCities] = useState<CityVO[]>([]);
  const [cityIdx, setCityIdx] = useState(-1);
  const [storeName, setStoreName] = useState('');
  const [license, setLicense] = useState('');
  const [foodLicense, setFoodLicense] = useState('');

  const loadData = useCallback(async () => {
    try {
      const [profile, stores] = await Promise.all([
        MemberAPI.profile().catch(() => null),
        CityStoreAPI.myStores().catch(() => [] as CityStoreVO[]),
      ]);
      const lv = profile ? Number(String(profile.level).replace('L', '')) || 0 : 0;
      setMemberLevel(lv);
      const mine = stores.find(s => s.status !== 4) || stores[0] || null;
      setStore(mine);
      if (mine) {
        const [as, os] = await Promise.all([
          CityStoreAPI.assessments(mine.storeCode).catch(() => [] as StoreAssessmentVO[]),
          CityStoreAPI.orders(mine.storeCode).catch(() => [] as StoreOrderVO[]),
        ]);
        setAssessments(as);
        setOrders(os);
      } else {
        setAssessments([]);
        setOrders([]);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (requireLogin()) {
      loadData();
    } else {
      setLoading(false);
    }
  }, [loadData]);

  // 打开开店面板(拉取可用城市)
  const openApply = async () => {
    if (memberLevel < 5) {
      Taro.showModal({
        title: '暂不可申请',
        content: `市级网店为 SVIP 专属权益, 当前等级 L${memberLevel}。升级 SVIP 会员后即可开店。`,
        showCancel: false,
      });
      return;
    }
    setStoreName('');
    setLicense('');
    setFoodLicense('');
    setCityIdx(-1);
    setShowApply(true);
    try {
      const r = await CityStoreAPI.availableCities();
      setCities(r.cities);
    } catch (e) {
      console.warn('[citystore] 可用城市加载失败:', e);
    }
  };

  // 提交开店申请
  const handleApply = async () => {
    if (submitting) return;
    if (cityIdx < 0 || !cities[cityIdx]) {
      Taro.showToast({ title: '请选择开店城市', icon: 'none' });
      return;
    }
    if (!storeName.trim()) {
      Taro.showToast({ title: '请输入网店名称', icon: 'none' });
      return;
    }
    if (!license.trim()) {
      Taro.showToast({ title: '请输入营业执照号', icon: 'none' });
      return;
    }
    if (!foodLicense.trim()) {
      Taro.showToast({ title: '请输入食品经营许可证号', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      await CityStoreAPI.apply({
        memberLevel,
        storeName: storeName.trim(),
        city: cities[cityIdx],
        businessLicense: license.trim(),
        foodLicense: foodLicense.trim(),
      });
      Taro.showToast({ title: '申请已提交, 等待审核', icon: 'none', duration: 2500 });
      setShowApply(false);
      await loadData();
    } catch (e) {
      console.warn('[citystore] 开店申请失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 折扣显示(后端 70/80/90 百分制 → 7折/8折/9折)
  const discountText = (d: number): string => `${d % 10 === 0 ? d / 10 : d.toFixed(1)} 折`;
  // 本月销售额(订单汇总)
  const nowMonth = new Date().toISOString().slice(0, 7);
  const monthSales = orders
    .filter(o => (o.createdAt || '').startsWith(nowMonth))
    .reduce((sum, o) => sum + o.totalAmount, 0);

  return (
    <View className={styles.page}>
      <NavBar title="市级网店" />
      <ScrollView scrollY className={styles.scrollView}>
        {loading ? (
          <View className={styles.empty}>加载中...</View>
        ) : store ? (
          <>
            {/* 网店卡片 */}
            <View className={styles.heroCard}>
              <View className={styles.heroTop}>
                <View className={styles.heroName}>{store.storeName}</View>
                <View className={`${styles.heroBadge} ${styles[STATUS_CLS[store.status] || 'pending']}`}>
                  {storeStatusName(store.status)}
                </View>
              </View>
              <View className={styles.heroMeta}>
                {store.provinceName} {store.cityName} · 一城一店(城市独占)
              </View>
              <View className={styles.heroStats}>
                <View className={styles.heroStatItem}>
                  <View className={styles.heroStatValue}>{discountText(store.currentDiscount)}</View>
                  <View className={styles.heroStatLabel}>当前折扣</View>
                </View>
                <View className={styles.heroStatDivider} />
                <View className={styles.heroStatItem}>
                  <View className={styles.heroStatValue}>{monthSales.toFixed(0)}</View>
                  <View className={styles.heroStatLabel}>本月销售(元)</View>
                </View>
                <View className={styles.heroStatDivider} />
                <View className={styles.heroStatItem}>
                  <View className={styles.heroStatValue}>{orders.length}</View>
                  <View className={styles.heroStatLabel}>关联订单</View>
                </View>
              </View>
              <View className={styles.heroCode}>网店编号 {store.storeCode}</View>
            </View>

            {/* 考核记录 */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>月度考核</View>
              {assessments.length === 0 ? (
                <View className={styles.empty}>
                  <View className={styles.emptyIcon}>📊</View>
                  <View>暂无考核记录, 每月系统自动考核</View>
                </View>
              ) : (
                assessments.map(a => (
                  <View key={a.assessmentMonth} className={styles.assessRow}>
                    <View className={styles.assessLeft}>
                      <View className={styles.assessMonth}>{a.assessmentMonth}</View>
                      <View className={styles.assessMeta}>
                        进货 ¥{a.monthlyPurchaseAmount.toFixed(0)}/{a.purchaseTarget}
                        <Text className={a.purchaseQualified ? styles.ok : styles.bad}>
                          {a.purchaseQualified ? ' 达标' : ' 未达'}
                        </Text>
                        {' · '}销售 ¥{a.monthlySalesAmount.toFixed(0)}/{a.salesTarget}
                        <Text className={a.salesQualified ? styles.ok : styles.bad}>
                          {a.salesQualified ? ' 达标' : ' 未达'}
                        </Text>
                      </View>
                      <View className={styles.assessMeta}>
                        次月折扣 {discountText(a.nextMonthDiscount)} · 累计未达 进货{a.consecutiveBelowPurchase}月/销售{a.consecutiveBelowSales}月
                      </View>
                    </View>
                    <View className={`${styles.qualBadge} ${styles[a.qualificationStatus === 1 ? 'qualNormal' : 'qualWarn']}`}>
                      {qualStatusName(a.qualificationStatus)}
                    </View>
                  </View>
                ))
              )}
            </View>

            {/* 关联订单 */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>关联订单</View>
              {orders.length === 0 ? (
                <View className={styles.empty}>
                  <View className={styles.emptyIcon}>📦</View>
                  <View>暂无关联订单</View>
                </View>
              ) : (
                orders.slice(0, 20).map(o => (
                  <View key={o.orderNo} className={styles.orderRow}>
                    <View className={styles.orderLeft}>
                      <View className={styles.orderName}>{o.productName || `商品 ${o.productId}`}</View>
                      <View className={styles.orderMeta}>
                        {o.orderNo} · {SALES_CHANNEL_NAME[o.salesChannel] || '其他渠道'} · {formatDate(o.createdAt)}
                      </View>
                    </View>
                    <View className={styles.orderRight}>
                      <View className={styles.orderAmount}>¥{o.totalAmount.toFixed(2)}</View>
                      <View className={styles.orderQty}>×{o.quantity}</View>
                    </View>
                  </View>
                ))
              )}
            </View>
          </>
        ) : (
          // 未开店引导
          <View className={styles.card}>
            <View className={styles.empty}>
              <View className={styles.emptyIcon}>🏙️</View>
              <View className={styles.emptyText}>市级网店 · 一城一店</View>
              <View className={styles.emptySub}>
                SVIP 专属权益 · 城市独占经营 · 月度考核享折扣
              </View>
            </View>
            <View className={styles.applyBtn} onClick={openApply}>申请开店</View>
          </View>
        )}

        {/* 规则说明 */}
        <View className={styles.noteCard}>
          <View className={styles.noteTitle}>市店规则</View>
          <View className={styles.noteLine}>· SVIP(L5) 专属, 一个城市仅一家网店(城市独占)</View>
          <View className={styles.noteLine}>· 凭营业执照 + 食品经营许可证申请, 平台审核后开业</View>
          <View className={styles.noteLine}>· 月度考核进货/销售双达标, 达标享次月更低折扣</View>
          <View className={styles.noteLine}>· 连续 1 月未达标预警 / 2 月暂停 / 3 月取消资格</View>
          <View className={styles.noteLine}>· 资格取消后 90 天冷静期内不可重新申请</View>
        </View>
        <View className={styles.bottomSpacer} />
      </ScrollView>

      {/* 开店申请弹层 */}
      {showApply && (
        <View className={styles.mask} onClick={() => setShowApply(false)}>
          <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
            <View className={styles.sheetTitle}>申请开店</View>
            <View className={styles.sheetDesc}>
              {cities.length > 0 ? `${cities.length} 个城市可开(未被独占)` : '可用城市加载中...'}
            </View>
            <Picker
              mode="selector"
              range={cities.map(c => `${c.provinceName} · ${c.cityName}`)}
              value={cityIdx >= 0 ? cityIdx : 0}
              onChange={(e) => setCityIdx(Number((e.detail as any).value))}
            >
              <View className={styles.pickerRow}>
                <Text className={styles.pickerLabel}>开店城市</Text>
                <Text className={cityIdx >= 0 ? styles.pickerValue : styles.pickerPlaceholder}>
                  {cityIdx >= 0 && cities[cityIdx]
                    ? `${cities[cityIdx].provinceName} ${cities[cityIdx].cityName}`
                    : '选择城市(一城一店, 先到先得)'}
                </Text>
                <Text className={styles.pickerArrow}>›</Text>
              </View>
            </Picker>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={storeName}
                onInput={(e) => setStoreName((e.detail as any).value)}
                placeholder="网店名称"
                placeholderClass={styles.placeholder}
                maxlength={30}
              />
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={license}
                onInput={(e) => setLicense((e.detail as any).value)}
                placeholder="营业执照号"
                placeholderClass={styles.placeholder}
                maxlength={30}
              />
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={foodLicense}
                onInput={(e) => setFoodLicense((e.detail as any).value)}
                placeholder="食品经营许可证号"
                placeholderClass={styles.placeholder}
                maxlength={30}
              />
            </View>
            <View className={styles.sheetBtn} onClick={handleApply}>
              {submitting ? '提交中...' : '提交申请'}
            </View>
          </View>
        </View>
      )}
    </View>
  );
};

export default CityStorePage;
