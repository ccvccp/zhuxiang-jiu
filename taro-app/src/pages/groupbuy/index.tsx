import React, { useState, useEffect, useRef } from 'react';
import { View, Text, ScrollView, Textarea } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import { GroupBuyAPI, GroupBuyProductVO, CalcResultVO, GroupBuyOrderVO, levelToInt } from '@/api/groupbuy';
import { MemberAPI } from '@/api/member';
import { getMemberId, requireLogin } from '@/services/auth-service';

/** 团购类型 */
const GROUP_TYPES = [
  { key: 'enterprise', label: '企业团购', desc: '商务宴请 · 员工福利' },
  { key: 'wedding', label: '婚庆喜宴', desc: '喜结连理 · 宾客答谢' },
  { key: 'festival', label: '节日送礼', desc: '佳节馈赠 · 礼尚往来' },
  { key: 'custom', label: '定制需求', desc: '个性定制 · 专属方案' },
];

const GROUP_TYPE_NAME: Record<string, string> = {
  enterprise: '企业团购', wedding: '婚庆喜宴', festival: '节日送礼', custom: '定制需求',
};

/**
 * 组团团购页 · 对接 /api/groupbuy/*
 * 类型选择 → 商品选择+数量 → 实时阶梯试算 → 提交申请 → 我的团购
 */
const GroupbuyPage: React.FC = () => {
  const [products, setProducts] = useState<GroupBuyProductVO[]>([]);
  const [groupType, setGroupType] = useState('enterprise');
  const [purpose, setPurpose] = useState('');
  // 已选数量表 productId → quantity
  const [selection, setSelection] = useState<Record<string, number>>({});
  const [calc, setCalc] = useState<CalcResultVO | null>(null);
  const [myOrders, setMyOrders] = useState<GroupBuyOrderVO[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [memberLevel, setMemberLevel] = useState('L1');
  const calcTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadMyOrders = async () => {
    try {
      const orders = await GroupBuyAPI.myOrders();
      setMyOrders(orders);
    } catch (e) { /* 未登录静默 */ }
  };

  useEffect(() => {
    if (!requireLogin()) { setLoading(false); return; }
    (async () => {
      try {
        const [prods, member] = await Promise.all([
          GroupBuyAPI.products().catch(() => [] as GroupBuyProductVO[]),
          MemberAPI.profile().catch(() => null),
        ]);
        setProducts(prods);
        if (member) setMemberLevel(member.level);
      } finally {
        setLoading(false);
      }
    })();
    loadMyOrders();
  }, []);

  // 选中项数组
  const selectedItems = Object.entries(selection)
    .filter(([, qty]) => qty > 0)
    .map(([productId, quantity]) => ({ productId, quantity }));

  // 数量变更(防抖 600ms 自动试算)
  const changeQty = (productId: string, delta: number) => {
    setSelection(prev => {
      const next = { ...prev };
      const cur = next[productId] || 0;
      const val = Math.max(0, cur + delta);
      if (val === 0) delete next[productId];
      else next[productId] = val;
      return next;
    });
    if (calcTimer.current) clearTimeout(calcTimer.current);
    calcTimer.current = setTimeout(runCalculate, 600);
  };

  const runCalculate = async () => {
    const items = Object.entries(selection)
      .filter(([, qty]) => qty > 0)
      .map(([productId, quantity]) => ({ productId, quantity }));
    if (items.length === 0) {
      setCalc(null);
      return;
    }
    try {
      const result = await GroupBuyAPI.calculate(items);
      setCalc(result);
    } catch (e) {
      console.warn('[groupbuy] 试算失败:', e);
    }
  };

  // 提交团购申请
  const handleSubmit = async () => {
    if (submitting) return;
    if (selectedItems.length === 0) {
      Taro.showToast({ title: '请先选择商品与数量', icon: 'none' });
      return;
    }
    const lv = levelToInt(memberLevel);
    if (lv < 5) {
      Taro.showModal({
        title: '需要 SVIP 会员',
        content: `团购申请需 SVIP(L5) 会员, 当前 ${memberLevel}。消费成长值可提升等级, 是否继续浏览?`,
        showCancel: false,
      });
      return;
    }
    if (calc && !calc.meetsThreshold) {
      Taro.showToast({ title: '未达团购门槛, 请增加数量', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const result = await GroupBuyAPI.applyWithLevel({
        userLevel: lv,
        groupType,
        items: selectedItems,
        purpose,
      });
      Taro.showToast({ title: `申请已提交 ${result.orderNo}`, icon: 'success' });
      setSelection({});
      setCalc(null);
      setPurpose('');
      loadMyOrders();
    } catch (e) {
      console.warn('[groupbuy] 申请失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancelOrder = async (order: GroupBuyOrderVO) => {
    if (submitting) return;
    const res = await Taro.showModal({ title: '取消团购', content: `确定取消团购单 ${order.orderNo} 吗?` });
    if (!res.confirm) return;
    setSubmitting(true);
    try {
      await GroupBuyAPI.cancel(order.orderNo, '用户取消');
      Taro.showToast({ title: '已取消', icon: 'success' });
      loadMyOrders();
    } catch (e) {
      console.warn('[groupbuy] 取消失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <View className={styles.page}>
      <NavBar title="组团团购" />
      <ScrollView scrollY className={styles.scrollView}>
        {/* 团购类型 */}
        <View className={styles.card}>
          <View className={styles.cardTitle}>团购类型</View>
          <View className={styles.typeGrid}>
            {GROUP_TYPES.map(t => (
              <View
                key={t.key}
                className={`${styles.typeItem} ${groupType === t.key ? styles.typeActive : ''}`}
                onClick={() => setGroupType(t.key)}
              >
                <View className={styles.typeLabel}>{t.label}</View>
                <View className={styles.typeDesc}>{t.desc}</View>
              </View>
            ))}
          </View>
        </View>

        {/* 商品选择 */}
        <View className={styles.card}>
          <View className={styles.cardTitle}>
            选择商品
            <Text className={styles.levelTag}>{memberLevel}</Text>
          </View>
          {loading ? (
            <View className={styles.empty}>加载中...</View>
          ) : products.length === 0 ? (
            <View className={styles.empty}>暂无可团购商品</View>
          ) : (
            products.map(p => (
              <View key={p.productId} className={styles.productRow}>
                <View className={styles.productInfo}>
                  <View className={styles.productName}>{p.productName}</View>
                  <View className={styles.productMeta}>{p.spec} · ¥{p.price}/瓶</View>
                </View>
                <View className={styles.qtyBox}>
                  <View className={styles.qtyBtn} onClick={() => changeQty(p.productId, -10)}>-10</View>
                  <View className={styles.qtyValue}>{selection[p.productId] || 0}</View>
                  <View className={styles.qtyBtn} onClick={() => changeQty(p.productId, +10)}>+10</View>
                </View>
              </View>
            ))
          )}
          <View className={styles.qtyTip}>数量以 10 瓶步进 · 团购为大批量采购</View>
        </View>

        {/* 实时试算 */}
        {calc && (
          <View className={styles.card}>
            <View className={styles.cardTitle}>阶梯试算 {calc.tier && <Text className={styles.tierTag}>{calc.tier}</Text>}</View>
            <View className={styles.calcRow}>
              <Text className={styles.calcLabel}>原价合计</Text>
              <Text className={styles.calcValue}>¥{calc.originalTotal.toFixed(2)}</Text>
            </View>
            <View className={styles.calcRow}>
              <Text className={styles.calcLabel}>团购折扣</Text>
              <Text className={styles.calcDiscount}>{Math.round((1 - calc.discount) * 100)}% off</Text>
            </View>
            <View className={styles.calcRow}>
              <Text className={styles.calcLabel}>团购总价</Text>
              <Text className={styles.calcTotal}>¥{calc.groupPrice.toFixed(2)}</Text>
            </View>
            <View className={styles.calcRow}>
              <Text className={styles.calcLabel}>预计节省</Text>
              <Text className={styles.calcSave}>¥{calc.savedAmount.toFixed(2)}</Text>
            </View>
            {!calc.meetsThreshold && calc.suggestions.length > 0 && (
              <View className={styles.suggestion}>
                {calc.suggestions.map((s, i) => <View key={i} className={styles.sugLine}>· {s}</View>)}
              </View>
            )}
          </View>
        )}

        {/* 用途说明 */}
        <View className={styles.card}>
          <View className={styles.cardTitle}>用途说明(选填)</View>
          <Textarea
            value={purpose}
            onInput={(e) => setPurpose(e.detail.value)}
            maxlength={200}
            placeholder='如: 公司年会用酒 60 桌 / 婚宴 30 桌'
            className={styles.textarea}
          />
        </View>

        {/* 提交 */}
        <View className={styles.submitBtn} onClick={handleSubmit}>
          {submitting ? '提交中...' : '提交团购申请'}
        </View>

        {/* 我的团购 */}
        <View className={styles.card}>
          <View className={styles.cardTitle}>我的团购</View>
          {myOrders.length === 0 ? (
            <View className={styles.empty}>暂无团购记录</View>
          ) : (
            myOrders.map(o => (
              <View key={o.orderNo} className={styles.orderRow}>
                <View className={styles.orderLeft}>
                  <View className={styles.orderType}>
                    {GROUP_TYPE_NAME[o.groupType] || o.groupType} · ¥{o.groupPrice}
                  </View>
                  <View className={styles.orderNoText}>{o.orderNo}</View>
                </View>
                <View className={styles.orderRight}>
                  <View className={styles.orderStatus}>{o.statusName || o.status}</View>
                  {['pending', 'approved', 'paying'].includes(o.status) && (
                    <Text className={styles.orderCancel} onClick={() => handleCancelOrder(o)}>取消</Text>
                  )}
                </View>
              </View>
            ))
          )}
        </View>
        <View className={styles.bottomSpacer} />
      </ScrollView>
    </View>
  );
};

export default GroupbuyPage;
