/**
 * 积分商城 · 信用积分兑换(竹信积分 → 商品/权益/现金/组合)
 * 目录浏览 → AI 方案推荐 → 一键兑换 → 兑换记录
 * 数据来源: 后端 /api/credit/exchange/*
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  PointsmallAPI, CatalogItemVO, CatalogVO, ExchangeRecordVO, ExchangePlanVO,
  ITEM_CATEGORY_NAME, exchangeTypeName,
} from '@/api/pointsmall';
import { requireLogin } from '@/services/auth-service';

type Tab = 'mall' | 'records';

const TABS: { key: Tab; label: string }[] = [
  { key: 'mall', label: '兑换商城' },
  { key: 'records', label: '兑换记录' },
];

// 目录分类 tabs
const CAT_TABS = ['goods', 'benefit'];

const formatDate = (t?: string): string => (t ? t.slice(0, 10) : '');

const PointsmallPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('mall');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  // 数据
  const [points, setPoints] = useState(0);
  const [catalog, setCatalog] = useState<CatalogVO | null>(null);
  const [catFilter, setCatFilter] = useState('goods');
  const [records, setRecords] = useState<ExchangeRecordVO[]>([]);
  const [plans, setPlans] = useState<ExchangePlanVO[]>([]);
  // 兑换弹层(现金/组合)
  const [cashPanel, setCashPanel] = useState<'cash' | 'combo' | null>(null);
  const [cashPoints, setCashPoints] = useState('');

  const loadData = useCallback(async () => {
    try {
      const [pts, cat, recs, rec] = await Promise.all([
        PointsmallAPI.creditPoints().catch(() => 0),
        PointsmallAPI.catalog().catch(() => null),
        PointsmallAPI.records().catch(() => [] as ExchangeRecordVO[]),
        PointsmallAPI.recommend().catch(() => null),
      ]);
      setPoints(pts);
      setCatalog(cat);
      setRecords(recs);
      setPlans(rec?.plans || []);
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

  // 目录商品兑换
  const handleExchangeItem = async (item: CatalogItemVO) => {
    if (submitting) return;
    if (item.roles.length > 0) {
      Taro.showToast({ title: '该权益仅限 B 端角色(代理/合作商)兑换', icon: 'none' });
      return;
    }
    if (points < item.points) {
      Taro.showToast({
        title: `积分不足(还差 ${item.points - points} 分)`,
        icon: 'none',
      });
      return;
    }
    const res = await Taro.showModal({
      title: '确认兑换',
      content: `${item.name}: ${item.points} 积分 → 价值 ¥${item.value.toFixed(2)}, 兑换后不可撤销。`,
    });
    if (!res.confirm) return;
    setSubmitting(true);
    try {
      const r = await PointsmallAPI.exchange({
        exchangeType: item.category as 'goods' | 'benefit',
        points: item.points,
        itemId: item.itemId,
      });
      Taro.showToast({ title: `兑换成功: ${r.itemName}`, icon: 'success', duration: 2000 });
      await loadData();
    } catch (e) {
      console.warn('[pointsmall] 商品兑换失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 打开现金/组合兑换面板
  const openCashPanel = (kind: 'cash' | 'combo') => {
    setCashPoints('');
    setCashPanel(kind);
  };

  // 现金/组合兑换提交
  const handleCashExchange = async () => {
    if (!cashPanel || submitting) return;
    const p = Number(cashPoints);
    if (!p || p <= 0) {
      Taro.showToast({ title: '请输入兑换积分', icon: 'none' });
      return;
    }
    if (p > points) {
      Taro.showToast({ title: `积分不足(可用 ${points})`, icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const r = await PointsmallAPI.exchange({
        exchangeType: cashPanel,
        points: p,
      });
      const tip = cashPanel === 'cash'
        ? `到账 ¥${r.netValue.toFixed(2)}${r.tax > 0 ? `(个税 ¥${r.tax.toFixed(2)})` : ''}`
        : `组合价值 ¥${r.netValue.toFixed(2)}`;
      Taro.showToast({ title: `兑换成功, ${tip}`, icon: 'none', duration: 2500 });
      setCashPanel(null);
      await loadData();
    } catch (e) {
      console.warn('[pointsmall] 兑换失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 目录条目(按分类过滤)
  const items = (catalog?.items || []).filter(i => i.category === catFilter);
  // 现金兑换预览(实时)
  const cashPreview = Number(cashPoints) || 0;
  const rate = catalog?.rates?.cash ?? 1.0;
  const cashValue = cashPreview / 100 * rate;
  const tax = Math.max(0, cashValue - (catalog?.cashTaxFreeAmount ?? 800))
    * (catalog?.cashTaxRate ?? 0.2);

  return (
    <View className={styles.page}>
      <NavBar title="积分商城" />
      {/* 积分余额卡 */}
      <View className={styles.heroCard}>
        <View className={styles.heroLabel}>我的竹信积分</View>
        <View className={styles.heroValue}>{points}</View>
        <View className={styles.heroMeta}>
          100 积分 ≈ ¥{(catalog?.rates?.cash ?? 1).toFixed(0)} 现金 · 季度现金上限 ¥{catalog?.quarterCashCap ?? 5000}
        </View>
        <View className={styles.heroBtns}>
          <View className={styles.heroBtn} onClick={() => openCashPanel('cash')}>兑现金</View>
          <View className={styles.heroBtnGhost} onClick={() => openCashPanel('combo')}>组合兑换</View>
        </View>
      </View>

      {/* 顶部 Tabs */}
      <View className={styles.tabBar}>
        {TABS.map(t => (
          <View
            key={t.key}
            className={`${styles.tabItem} ${tab === t.key ? styles.tabItemActive : ''}`}
            onClick={() => setTab(t.key)}
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
            {/* AI 推荐 */}
            {plans.length > 0 && (
              <View className={styles.planCard}>
                <View className={styles.cardTitle}>AI 兑换方案推荐</View>
                {plans.map(p => (
                  <View key={p.planNo} className={styles.planRow}>
                    <View className={styles.planLeft}>
                      <View className={styles.planName}>
                        方案{p.planNo} · {p.itemName}
                      </View>
                      <View className={styles.planReason}>{p.reason}</View>
                    </View>
                    <View className={styles.planRight}>
                      <View className={styles.planValue}>¥{p.netValue.toFixed(2)}</View>
                      <View className={styles.planPoints}>{p.points} 积分</View>
                    </View>
                  </View>
                ))}
              </View>
            )}

            {/* 目录分类 */}
            <View className={styles.card}>
              <View className={styles.catTabs}>
                {CAT_TABS.map(c => (
                  <View
                    key={c}
                    className={`${styles.catTab} ${catFilter === c ? styles.catTabActive : ''}`}
                    onClick={() => setCatFilter(c)}
                  >
                    {ITEM_CATEGORY_NAME[c]}
                  </View>
                ))}
              </View>
              {items.length === 0 ? (
                <View className={styles.empty}>
                  <View className={styles.emptyIcon}>🎁</View>
                  <View>该分类暂无可兑商品</View>
                </View>
              ) : (
                items.map(item => {
                  const canAfford = points >= item.points;
                  const bOnly = item.roles.length > 0;
                  return (
                    <View
                      key={item.itemId}
                      className={`${styles.itemRow} ${!canAfford || bOnly ? styles.itemRowDisabled : ''}`}
                      onClick={() => handleExchangeItem(item)}
                    >
                      <View className={styles.itemLeft}>
                        <View className={styles.itemName}>
                          {item.name}
                          {bOnly && <Text className={styles.bBadge}>B端</Text>}
                        </View>
                        <View className={styles.itemMeta}>
                          价值 ¥{item.value.toFixed(2)} · {item.points} 积分
                        </View>
                        {!canAfford && (
                          <View className={styles.itemGap}>还差 {item.points - points} 积分</View>
                        )}
                      </View>
                      <View className={`${styles.exchangeBtn} ${!canAfford || bOnly ? styles.exchangeBtnOff : ''}`}>
                        兑换
                      </View>
                    </View>
                  );
                })
              )}
            </View>

            {/* 规则说明 */}
            <View className={styles.noteCard}>
              <View className={styles.noteTitle}>兑换规则</View>
              <View className={styles.noteLine}>· 现金: 100 积分 = ¥1, 超 ¥800 部分扣 20% 个税</View>
              <View className={styles.noteLine}>· 商品/权益: 按目录价兑换, 无税收</View>
              <View className={styles.noteLine}>· 组合: 100 积分 = ¥1.3, 现金性质部分(50%)计税</View>
              <View className={styles.noteLine}>· 季度现金兑换上限 ¥{catalog?.quarterCashCap ?? 5000}</View>
              <View className={styles.noteLine}>· 积分来自季度信用结算(行为分 × 权重 × 等级加成)</View>
            </View>
          </>
        ) : (
          <View className={styles.card}>
            <View className={styles.cardTitle}>兑换记录</View>
            {records.length === 0 ? (
              <View className={styles.empty}>
                <View className={styles.emptyIcon}>📒</View>
                <View>暂无兑换记录</View>
              </View>
            ) : (
              records.map(r => (
                <View key={r.exchangeId} className={styles.recRow}>
                  <View className={styles.recLeft}>
                    <View className={styles.recName}>{r.itemName}</View>
                    <View className={styles.recMeta}>
                      {exchangeTypeName(r.exchangeType)} · {r.points} 积分 · {formatDate(r.createdAt)}
                    </View>
                  </View>
                  <View className={styles.recRight}>
                    <View className={styles.recValue}>¥{r.netValue.toFixed(2)}</View>
                    {r.tax > 0 && <View className={styles.recTax}>税 ¥{r.tax.toFixed(2)}</View>}
                  </View>
                </View>
              ))
            )}
          </View>
        )}
        <View className={styles.bottomSpacer} />
      </ScrollView>

      {/* 现金/组合兑换弹层 */}
      {cashPanel && (
        <View className={styles.mask} onClick={() => setCashPanel(null)}>
          <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
            <View className={styles.sheetTitle}>
              {cashPanel === 'cash' ? '兑换现金' : '组合兑换'}
            </View>
            <View className={styles.sheetDesc}>
              {cashPanel === 'cash'
                ? `100 积分 = ¥${(catalog?.rates?.cash ?? 1).toFixed(0)}, 超 ¥${catalog?.cashTaxFreeAmount ?? 800} 部分扣 20% 个税, 钱包即时到账`
                : '100 积分 = ¥1.3 组合权益(现金+商品+权益), 现金性质部分(50%)计税'}
            </View>
            <View className={styles.inputRow}>
              <Text className={styles.inputPrefix}>积分</Text>
              <Input
                className={styles.amountInput}
                type="number"
                value={cashPoints}
                onInput={(e) => setCashPoints((e.detail as any).value)}
                placeholder={`最多 ${points}`}
                placeholderClass={styles.placeholder}
              />
            </View>
            {cashPanel === 'cash' && cashPreview > 0 && (
              <View className={styles.previewRow}>
                <Text>面值 ¥{cashValue.toFixed(2)}</Text>
                {tax > 0 && <Text> · 个税 ¥{tax.toFixed(2)}</Text>}
                <Text> · 到账 ¥{(cashValue - tax).toFixed(2)}</Text>
              </View>
            )}
            <View className={styles.sheetBtnRow}>
              <View className={styles.sheetBtnGhost} onClick={() => setCashPanel(null)}>取消</View>
              <View className={styles.sheetBtn} onClick={handleCashExchange}>
                {submitting ? '兑换中...' : '确认兑换'}
              </View>
            </View>
          </View>
        </View>
      )}
    </View>
  );
};

export default PointsmallPage;
