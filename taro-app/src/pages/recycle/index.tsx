/**
 * 老酒回收 · 估价 / 我的回收(估价记录+申请+执行)
 * 数据来源: 后端 /api/recycle/*(AI智能估值: 增值率+品质分级+折现)
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input, Picker } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  RecycleAPI, ValuationVO, ApplicationVO,
  CONDITION_GRADES, appStatusName,
} from '@/api/recycle';
import { ProductAPI, ProductVO } from '@/api/product';
import { MemberAPI } from '@/api/member';
import { levelToInt } from '@/api/groupbuy';
import { requireLogin } from '@/services/auth-service';

// 状态 → 徽标样式
const STATUS_CLS: Record<string, string> = {
  pending: 'pending',
  valuing: 'processing',
  valued: 'processing',
  reviewing: 'processing',
  approved: 'confirm',
  rejected: 'closed',
  recycling: 'confirm',
  exchanging: 'confirm',
  completed: 'resolved',
  cancelled: 'closed',
};

const TYPE_NAME: Record<string, string> = {
  exchange: '兑换新酒',
  recycle: '折现回收',
};

const formatTime = (t?: string): string => {
  if (!t) return '';
  return t.slice(0, 10);
};

const RecyclePage: React.FC = () => {
  // 视图: valuation=估价表单, mine=我的回收
  const [view, setView] = useState<'valuation' | 'mine'>('valuation');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  // 估价表单
  const [products, setProducts] = useState<ProductVO[]>([]);
  const [productIdx, setProductIdx] = useState(0);
  const [price, setPrice] = useState('');
  const [dateStr, setDateStr] = useState('');
  const [grade, setGrade] = useState('A');
  const [memberLevel, setMemberLevel] = useState(1);
  // 估价结果
  const [valuation, setValuation] = useState<ValuationVO | null>(null);
  // 我的回收
  const [myValuations, setMyValuations] = useState<ValuationVO[]>([]);
  const [apps, setApps] = useState<ApplicationVO[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  // 申请面板
  const [appPanel, setAppPanel] = useState(false);
  const [appType, setAppType] = useState<'exchange' | 'recycle'>('exchange');
  const [newProductIdx, setNewProductIdx] = useState(0);
  const [payoutAccount, setPayoutAccount] = useState('');

  // 加载产品与会员等级
  useEffect(() => {
    if (!requireLogin()) {
      setLoading(false);
      return;
    }
    (async () => {
      try {
        const [{ products: prods }, member] = await Promise.all([
          ProductAPI.list({ page_size: 50 }).catch(() => ({ products: [] as ProductVO[], total: 0, page: 1, totalPages: 1 })),
          MemberAPI.profile().catch(() => null),
        ]);
        setProducts(prods);
        if (member) setMemberLevel(levelToInt(member.level));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // 我的回收数据
  const loadMine = useCallback(async () => {
    try {
      const [vals, applications] = await Promise.all([
        RecycleAPI.myValuations().catch(() => [] as ValuationVO[]),
        RecycleAPI.myApplications().catch(() => [] as ApplicationVO[]),
      ]);
      setMyValuations(vals);
      setApps(applications);
      // 清除已被申请使用的估价选中
      setSelected(prev => {
        const next = new Set(prev);
        const used = new Set(applications.flatMap(a => a.valuationIds || []));
        next.forEach(id => { if (used.has(id)) next.delete(id); });
        return next;
      });
    } catch (e) {
      console.warn('[recycle] 我的回收加载失败:', e);
    }
  }, []);

  useEffect(() => {
    if (view === 'mine') loadMine();
  }, [view, loadMine]);

  // 提交估价
  const handleValuate = async () => {
    if (submitting) return;
    if (products.length === 0) {
      Taro.showToast({ title: '暂无可选产品', icon: 'none' });
      return;
    }
    const p = Number(price);
    if (!p || p <= 0) {
      Taro.showToast({ title: '请输入购买原价', icon: 'none' });
      return;
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
      Taro.showToast({ title: '请选择购买日期', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const v = await RecycleAPI.submitValuation({
        productId: products[productIdx].id,
        purchasePrice: p,
        purchaseDate: dateStr,
        conditionGrade: grade,
        memberLevel,
        forExchange: true,
      });
      setValuation(v);
      Taro.showToast({ title: '估价完成', icon: 'success' });
    } catch (e) {
      console.warn('[recycle] 估价失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 切换估价选中
  const toggleSelect = (id: number) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // 打开申请面板
  const openAppPanel = () => {
    if (selected.size === 0) {
      Taro.showToast({ title: '请先勾选要回收的估价记录', icon: 'none' });
      return;
    }
    if (appType === 'recycle' && selected.size > 3) {
      Taro.showToast({ title: '折现单次最多 3 瓶', icon: 'none' });
      return;
    }
    if (appType === 'exchange' && selected.size > 5) {
      Taro.showToast({ title: '兑换单次最多 5 瓶', icon: 'none' });
      return;
    }
    setAppPanel(true);
  };

  // 提交申请
  const handleSubmitApp = async () => {
    if (submitting) return;
    if (appType === 'exchange' && products.length === 0) {
      Taro.showToast({ title: '暂无新酒可选', icon: 'none' });
      return;
    }
    if (appType === 'recycle' && !payoutAccount.trim()) {
      Taro.showToast({ title: '请填写收款账户', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const params: Parameters<typeof RecycleAPI.submitApplication>[0] = {
        type: appType,
        valuationIds: Array.from(selected),
      };
      if (appType === 'exchange') {
        params.newProductId = products[newProductIdx].id;
        params.newProductPrice = products[newProductIdx].price;
      } else {
        params.payoutMethod = 'wechat';
        params.payoutAccount = payoutAccount.trim();
      }
      const app = await RecycleAPI.submitApplication(params);
      Taro.showToast({ title: `申请已提交 #${app.id}`, icon: 'success' });
      setAppPanel(false);
      setSelected(new Set());
      await loadMine();
    } catch (e) {
      console.warn('[recycle] 申请失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 审核通过 → 执行兑换/折现
  const handleExecute = async (app: ApplicationVO) => {
    if (submitting) return;
    if (app.type === 'exchange') {
      // 兑换新酒: 重选新酒执行
      const res = await Taro.showModal({
        title: '兑换新酒',
        content: `将使用老酒价值 ¥${app.oldWineTotalValue.toFixed(2)} 抵扣新酒款。执行后由工作人员发货, 确认执行?`,
      });
      if (!res.confirm) return;
      setSubmitting(true);
      try {
        const ex = await RecycleAPI.exchangeNewWine(
          app.id, app.newProductId!, app.newProductPrice!);
        Taro.showToast({ title: `兑换执行成功 #${ex.id}`, icon: 'success' });
        await loadMine();
      } catch (e) {
        console.warn('[recycle] 兑换失败:', e);
      } finally {
        setSubmitting(false);
      }
    } else {
      // 折现回收
      const res = await Taro.showModal({
        title: '折现回收',
        content: `折现 ¥${app.cashValue.toFixed(2)} 将打至 ${app.payoutMethod === 'wechat' ? '微信' : app.payoutMethod}账户, 确认执行?`,
      });
      if (!res.confirm) return;
      setSubmitting(true);
      try {
        const ex = await RecycleAPI.recycleForCash(
          app.id, app.payoutMethod || 'wechat', app.payoutAccount || '');
        Taro.showToast({ title: `回收执行成功 #${ex.id}`, icon: 'success' });
        await loadMine();
      } catch (e) {
        console.warn('[recycle] 折现失败:', e);
      } finally {
        setSubmitting(false);
      }
    }
  };

  // ============ 估价视图 ============
  const renderValuation = () => (
    <View className={styles.page}>
      <NavBar title="老酒回收" />
      <ScrollView scrollY className={styles.scrollView}>
        <View className={styles.heroCard}>
          <View className={styles.heroTitle}>老酒回收 · AI 智能估值</View>
          <View className={styles.heroDesc}>
            酒龄满 3 年可参与 · 基础增值 15% 逐年 +5% 封顶 100% · 折现 80%
          </View>
        </View>

        <View className={styles.card}>
          <View className={styles.cardTitle}>老酒信息</View>
          {/* 产品选择 */}
          {products.length > 0 ? (
            <Picker
              mode="selector"
              range={products.map(p => `${p.name} · ¥${p.price}`)}
              value={productIdx}
              onChange={(e) => setProductIdx(Number(e.detail.value))}
            >
              <View className={styles.pickerBox}>
                <Text className={styles.pickerLabel}>老酒产品</Text>
                <Text className={styles.pickerValue}>{products[productIdx].name} ›</Text>
              </View>
            </Picker>
          ) : (
            <View className={styles.pickerBox}>
              <Text className={styles.pickerLabel}>老酒产品</Text>
              <Text className={styles.pickerValue}>加载中...</Text>
            </View>
          )}

          {/* 购买价格 */}
          <View className={styles.formRow}>
            <Text className={styles.formLabel}>购买原价</Text>
            <Input
              className={styles.formInput}
              type="digit"
              value={price}
              onInput={(e) => setPrice(e.detail.value)}
              placeholder="¥ 0.00"
              placeholderClass={styles.placeholder}
            />
          </View>

          {/* 购买日期 */}
          <Picker
            mode="date"
            value={dateStr}
            onChange={(e) => setDateStr(e.detail.value)}
            end={new Date().toISOString().slice(0, 10)}
          >
            <View className={styles.pickerBox}>
              <Text className={styles.pickerLabel}>购买日期</Text>
              <Text className={styles.pickerValue}>{dateStr || '选择日期(酒龄基准) ›'}</Text>
            </View>
          </Picker>

          {/* 品质分级 */}
          <View className={styles.formLabel2}>品质分级</View>
          <View className={styles.typeGrid}>
            {CONDITION_GRADES.map(g => (
              <View
                key={g.key}
                className={`${styles.typeItem} ${grade === g.key ? styles.typeActive : ''}`}
                onClick={() => setGrade(g.key)}
              >
                <View className={styles.typeLabel}>{g.label}</View>
                <View className={styles.typeDesc}>{g.desc}</View>
              </View>
            ))}
          </View>

          <View className={styles.submitBtn} onClick={handleValuate}>
            {submitting ? '估价中...' : 'AI 智能估价'}
          </View>
        </View>

        {/* 估价结果 */}
        {valuation && (
          <View className={styles.card}>
            <View className={styles.cardTitle}>估价结果 #{valuation.id}</View>
            <View className={styles.calcRow}>
              <Text className={styles.calcLabel}>酒龄</Text>
              <Text className={styles.calcValue}>{valuation.wineAge} 年</Text>
            </View>
            <View className={styles.calcRow}>
              <Text className={styles.calcLabel}>增值率</Text>
              <Text className={styles.calcValue}>{Math.round(valuation.appreciationRate * 100)}%</Text>
            </View>
            <View className={styles.calcRow}>
              <Text className={styles.calcLabel}>老酒价值</Text>
              <Text className={styles.calcTotal}>¥{valuation.oldValue.toFixed(2)}</Text>
            </View>
            <View className={styles.calcRow}>
              <Text className={styles.calcLabel}>折现金额(80%)</Text>
              <Text className={styles.calcSave}>¥{valuation.cashValue.toFixed(2)}</Text>
            </View>
            <View className={styles.suggestion}>
              估价记录已保存, 可在"我的回收"中勾选提交兑换或折现申请
            </View>
            <View className={styles.goMineBtn} onClick={() => { setView('mine'); setValuation(null); }}>
              前往提交回收申请 ›
            </View>
          </View>
        )}
        <View className={styles.bottomSpacer} />
      </ScrollView>

      {/* 底部视图切换 */}
      <View className={styles.tabBar}>
        <View
          className={`${styles.tabBarItem} ${view === 'valuation' ? styles.tabBarActive : ''}`}
          onClick={() => setView('valuation')}
        >
          老酒估价
        </View>
        <View
          className={`${styles.tabBarItem} ${view === 'mine' ? styles.tabBarActive : ''}`}
          onClick={() => setView('mine')}
        >
          我的回收
        </View>
      </View>
    </View>
  );

  // ============ 我的回收视图 ============
  const renderMine = () => (
    <View className={styles.page}>
      <NavBar title="我的回收" />
      <ScrollView scrollY className={styles.scrollView}>
        {/* 我的估价记录 */}
        <View className={styles.card}>
          <View className={styles.cardTitle}>
            我的估价
            {selected.size > 0 && <Text className={styles.selCount}>已选 {selected.size} 瓶</Text>}
          </View>
          {myValuations.length === 0 ? (
            <View className={styles.empty}>
              <View className={styles.emptyIcon}>🍶</View>
              <View>暂无估价记录, 先去估价吧</View>
            </View>
          ) : (
            myValuations.map(v => {
              const used = apps.some(a => (a.valuationIds || []).includes(v.id)
                && !['cancelled', 'rejected'].includes(a.status));
              const checked = selected.has(v.id);
              return (
                <View
                  key={v.id}
                  className={`${styles.valRow} ${used ? styles.valUsed : ''} ${checked ? styles.valChecked : ''}`}
                  onClick={() => !used && toggleSelect(v.id)}
                >
                  <View className={`${styles.checkBox} ${checked ? styles.checked : ''} ${used ? styles.disabled : ''}`}>
                    {checked ? '✓' : ''}
                  </View>
                  <View className={styles.valInfo}>
                    <View className={styles.valProduct}>
                      {v.productId} · {v.conditionGrade} 级 · {v.wineAge} 年
                    </View>
                    <View className={styles.valMeta}>
                      价值 ¥{v.oldValue.toFixed(2)} · 折现 ¥{v.cashValue.toFixed(2)}
                    </View>
                    <View className={styles.valMeta}>{formatTime(v.createdAt)}</View>
                  </View>
                  {used && <View className={styles.usedTag}>已申请</View>}
                </View>
              );
            })
          )}
          {myValuations.length > 0 && (
            <View className={styles.submitBtn} onClick={openAppPanel}>
              提交回收申请({selected.size} 瓶)
            </View>
          )}
        </View>

        {/* 我的申请 */}
        <View className={styles.card}>
          <View className={styles.cardTitle}>回收申请</View>
          {apps.length === 0 ? (
            <View className={styles.empty}>暂无申请记录</View>
          ) : (
            apps.map(a => (
              <View key={a.id} className={styles.appRow}>
                <View className={styles.appLeft}>
                  <View className={styles.appType}>
                    {TYPE_NAME[a.type] || a.type} · {a.oldWineCount} 瓶
                  </View>
                  <View className={styles.valMeta}>
                    老酒价值 ¥{a.oldWineTotalValue.toFixed(2)}
                    {a.type === 'recycle' && ` · 折现 ¥${a.cashValue.toFixed(2)}`}
                  </View>
                  <View className={styles.valMeta}>#{a.id} · {formatTime(a.createdAt)}</View>
                </View>
                <View className={styles.appRight}>
                  <View className={`${styles.badge} ${styles[STATUS_CLS[a.status] || 'closed']}`}>
                    {appStatusName(a.status)}
                  </View>
                  {a.status === 'approved' && (
                    <View className={styles.execBtn} onClick={() => handleExecute(a)}>
                      {a.type === 'exchange' ? '兑换新酒' : '折现回收'}
                    </View>
                  )}
                </View>
              </View>
            ))
          )}
        </View>
        <View className={styles.bottomSpacer} />
      </ScrollView>

      {/* 底部视图切换 */}
      <View className={styles.tabBar}>
        <View
          className={`${styles.tabBarItem} ${view === 'valuation' ? styles.tabBarActive : ''}`}
          onClick={() => setView('valuation')}
        >
          老酒估价
        </View>
        <View
          className={`${styles.tabBarItem} ${view === 'mine' ? styles.tabBarActive : ''}`}
          onClick={() => setView('mine')}
        >
          我的回收
        </View>
      </View>

      {/* 申请类型选择弹层 */}
      {appPanel && (
        <View className={styles.mask} onClick={() => setAppPanel(false)}>
          <View className={styles.panel} onClick={(e) => e.stopPropagation()}>
            <View className={styles.panelTitle}>提交回收申请</View>

            {/* 类型选择 */}
            <View className={styles.typeRow} onClick={() => setAppType('exchange')}>
              <View className={styles.typeRadio}>{appType === 'exchange' ? '●' : '○'}</View>
              <View className={styles.typeInfo}>
                <View className={styles.typeName}>兑换新酒</View>
                <View className={styles.typeDesc2}>老酒价值全额抵扣 · 差额转积分 · 单次 ≤5 瓶</View>
              </View>
            </View>
            <View className={styles.typeRow} onClick={() => setAppType('recycle')}>
              <View className={styles.typeRadio}>{appType === 'recycle' ? '●' : '○'}</View>
              <View className={styles.typeInfo}>
                <View className={styles.typeName}>折现回收</View>
                <View className={styles.typeDesc2}>老酒价值 ×80% · 超 ¥800 部分缴税 · 单次 ≤3 瓶</View>
              </View>
            </View>

            {/* 兑换: 新酒选择 */}
            {appType === 'exchange' && products.length > 0 && (
              <Picker
                mode="selector"
                range={products.map(p => `${p.name} · ¥${p.price}`)}
                value={newProductIdx}
                onChange={(e) => setNewProductIdx(Number(e.detail.value))}
              >
                <View className={styles.pickerBox}>
                  <Text className={styles.pickerLabel}>兑换新酒</Text>
                  <Text className={styles.pickerValue}>{products[newProductIdx].name} ›</Text>
                </View>
              </Picker>
            )}

            {/* 回收: 收款账户 */}
            {appType === 'recycle' && (
              <View className={styles.formRow}>
                <Text className={styles.formLabel}>收款账户</Text>
                <Input
                  className={styles.formInput}
                  value={payoutAccount}
                  onInput={(e) => setPayoutAccount(e.detail.value)}
                  placeholder="微信/支付宝账号"
                  placeholderClass={styles.placeholder}
                />
              </View>
            )}

            <View className={styles.panelBtn} onClick={handleSubmitApp}>
              {submitting ? '提交中...' : '确认提交'}
            </View>
          </View>
        </View>
      )}
    </View>
  );

  return view === 'mine' ? renderMine() : renderValuation();
};

export default RecyclePage;
