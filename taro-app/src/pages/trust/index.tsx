/**
 * 信值兑换 · 建档 + 余额 + 积分兑换 + 支付组合试算 + 限额 + 订单·申诉
 * 数据来源: 后端 /api/xx64/*(64号会员面) + /api/trust/*(45号)
 * 注: 决策面端点(兑换/下单)受 XX64_MODE 控制; 档案 ID 建档后本地持久化;
 *     申诉通道不受开关影响(观测与纠错永不关停——宪法口径)
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input, Textarea } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  Xx64API, PointsPreviewVO, PlanVO, QuotaVO,
  Xx64MyOrderVO, Xx64AppealResultVO,
  ORDER_STATUS_NAME, APPEAL_STATUS_NAME,
  getCachedTrustId, myTrustId,
} from '@/api/xx64';
import { PointsAPI } from '@/api/points';
import { requireLogin } from '@/services/auth-service';

type Panel = null | 'exchange' | 'plan' | 'create';

/** 申诉终态(可再次申诉) */
const APPEAL_TERMINAL = ['approved', 'rejected', 'expired'];

/** 订单是否可申诉(非 initiated 且无进行中申诉) */
const appealable = (o: Xx64MyOrderVO): boolean => {
  if (o.status === 'initiated') return false;
  if (o.appeal && !APPEAL_TERMINAL.includes(o.appeal.status)) return false;
  return true;
};

const TrustPage: React.FC = () => {
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  // 档案态
  const [trustId, setTrustId] = useState<number | null>(null);
  const [balance, setBalance] = useState<{ available: number; frozen: number; dailyCap: number } | null>(null);
  const [preview, setPreview] = useState<PointsPreviewVO | null>(null);
  const [quota, setQuota] = useState<QuotaVO | null>(null);
  const [points, setPoints] = useState(0);
  // 订单·申诉
  const [myOrders, setMyOrders] = useState<Xx64MyOrderVO[]>([]);
  // 面板
  const [panel, setPanel] = useState<Panel>(null);
  const [exPoints, setExPoints] = useState('');
  const [planPrice, setPlanPrice] = useState('');
  const [plan, setPlan] = useState<PlanVO | null>(null);
  // 建档表单
  const [crName, setCrName] = useState('');
  const [crIdNumber, setCrIdNumber] = useState('');
  // 申诉弹层
  const [appealOrder, setAppealOrder] = useState<Xx64MyOrderVO | null>(null);
  const [appealReason, setAppealReason] = useState('');
  const [appealBusy, setAppealBusy] = useState(false);
  const [appealResult, setAppealResult] = useState<Xx64AppealResultVO | null>(null);

  const loadOrders = useCallback(async () => {
    const os = await Xx64API.myOrders(20).catch(() => [] as Xx64MyOrderVO[]);
    setMyOrders(os);
  }, []);

  const loadData = useCallback(async (tid?: number | null) => {
    const id = tid ?? getCachedTrustId();
    try {
      const pts = await PointsAPI.account(Number(myTrustId()) || 0).catch(() => null);
      if (pts) setPoints(pts.totalPoints ?? 0);
      loadOrders();
      if (id) {
        const [bal, prev, qt] = await Promise.all([
          Xx64API.trustBalance(id).catch(() => null),
          Xx64API.pointsPreview(id).catch(() => null),
          Xx64API.quota(id).catch(() => null),
        ]);
        // 档案不存在: 清缓存进建档态
        if (!bal) {
          setTrustId(null);
          Taro.removeStorageSync('trust_id_cache');
        } else {
          setTrustId(id);
          setBalance(bal);
          setPreview(prev);
          setQuota(qt);
        }
      }
    } finally {
      setLoading(false);
    }
  }, [loadOrders]);

  useEffect(() => {
    if (requireLogin()) {
      loadData();
    } else {
      setLoading(false);
    }
  }, [loadData]);

  // 建档
  const handleCreate = async () => {
    if (submitting) return;
    if (!crName.trim()) {
      Taro.showToast({ title: '请输入姓名/机构名', icon: 'none' });
      return;
    }
    if (!/^\d{15}(\d{2}[0-9Xx])?$/.test(crIdNumber.trim())) {
      Taro.showToast({ title: '身份证号格式不正确', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const r = await Xx64API.createTrustRole({
        role: 'person', name: crName.trim(), idNumber: crIdNumber.trim(),
      });
      Taro.showToast({ title: '建档成功', icon: 'success' });
      setPanel(null);
      setCrName('');
      setCrIdNumber('');
      setLoading(true);
      await loadData(r.trustId);
    } catch (e) {
      console.warn('[trust] 建档失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 积分兑换
  const handleExchange = async () => {
    if (submitting || !trustId) return;
    const p = Number(exPoints);
    if (!p || p < 100) {
      Taro.showToast({ title: '最低兑换 100 积分', icon: 'none' });
      return;
    }
    if (p % 100 !== 0) {
      Taro.showToast({ title: '须为 100 的整数倍', icon: 'none' });
      return;
    }
    if (p > points) {
      Taro.showToast({ title: `积分不足(当前 ${points})`, icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const r = await Xx64API.exchangePoints(p);
      const got = r?.data?.trustGained ?? r?.data?.trust_gained ?? p / 100;
      Taro.showToast({ title: `兑换成功, 获得信值 ${got}`, icon: 'success', duration: 2500 });
      setPanel(null);
      setExPoints('');
      loadData(trustId);
    } catch (e: any) {
      const msg = String(e?.message || e?.errMsg || '');
      Taro.showModal({
        title: '兑换未成功',
        content: msg.includes('409') || msg.includes('决策') || msg.includes('off')
          ? '信值兑换功能暂未开放(决策面关闭), 敬请期待。'
          : (msg || '请稍后重试'),
        showCancel: false,
      });
    } finally {
      setSubmitting(false);
    }
  };

  // 支付组合试算
  const handlePlan = async () => {
    if (submitting || !trustId) return;
    const price = Number(planPrice);
    if (!price || price <= 0) {
      Taro.showToast({ title: '请输入商品价格', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const p = await Xx64API.paymentPlan(trustId, price);
      setPlan(p);
    } catch (e) {
      console.warn('[trust] 试算失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // ============ 订单申诉(不受开关影响) ============
  const openAppeal = (o: Xx64MyOrderVO) => {
    setAppealOrder(o);
    setAppealReason('');
    setAppealResult(null);
  };

  const closeAppeal = () => {
    setAppealOrder(null);
    setAppealReason('');
    setAppealResult(null);
    loadOrders();
  };

  const handleAppeal = async () => {
    if (appealBusy || !appealOrder) return;
    const reason = appealReason.trim();
    if (!reason) {
      Taro.showToast({ title: '请填写申诉理由', icon: 'none' });
      return;
    }
    if (reason.length > 500) {
      Taro.showToast({ title: '申诉理由须 1-500 字', icon: 'none' });
      return;
    }
    setAppealBusy(true);
    try {
      const r = await Xx64API.appeal(appealOrder.orderId, reason);
      const d = r?.data ?? r;
      setAppealResult({
        appealId: Number(d?.appealId ?? 0),
        orderId: Number(d?.orderId ?? appealOrder.orderId),
        status: String(d?.status || 'recalculated'),
        expiresAt: String(d?.expiresAt || ''),
        note: String(d?.note || ''),
      });
    } catch (e: any) {
      const msg = String(e?.message || e?.errMsg || '');
      Taro.showModal({
        title: '申诉未提交',
        content: msg || '请稍后重试',
        showCancel: false,
      });
    } finally {
      setAppealBusy(false);
    }
  };

  // ============ 建档面板 ============
  const renderCreatePanel = () => (
    <View className={styles.mask} onClick={() => setPanel(null)}>
      <View className={styles.panel} onClick={(e) => e.stopPropagation()}>
        <View className={styles.panelTitle}>开通信值档案</View>
        <View className={styles.panelRate}>个人档案 · 证件号仅本次校验, 存储只留摘要</View>
        <View className={styles.formRow}>
          <Text className={styles.amountLabel}>姓名</Text>
          <Input
            className={styles.amountInput2}
            value={crName}
            onInput={(e) => setCrName(e.detail.value)}
            placeholder="真实姓名"
            placeholderClass={styles.placeholder}
          />
        </View>
        <View className={styles.formRow}>
          <Text className={styles.amountLabel}>身份证</Text>
          <Input
            className={styles.amountInput2}
            value={crIdNumber}
            onInput={(e) => setCrIdNumber(e.detail.value)}
            placeholder="18 位身份证号"
            placeholderClass={styles.placeholder}
          />
        </View>
        <View className={styles.panelBtn} onClick={handleCreate}>
          {submitting ? '建档中...' : '确认建档'}
        </View>
      </View>
    </View>
  );

  // ============ 兑换面板 ============
  const renderExchangePanel = () => (
    <View className={styles.mask} onClick={() => setPanel(null)}>
      <View className={styles.panel} onClick={(e) => e.stopPropagation()}>
        <View className={styles.panelTitle}>积分兑换信值</View>
        <View className={styles.panelRate}>100 积分 = 1 信值 · T+1 冻结观察后入账</View>
        <View className={styles.amountRow}>
          <Text className={styles.amountLabel}>积分</Text>
          <Input
            className={styles.amountInput}
            type="number"
            value={exPoints}
            onInput={(e) => setExPoints(e.detail.value)}
            placeholder="100 的整数倍"
            placeholderClass={styles.placeholder}
          />
        </View>
        <View className={styles.panelNote}>
          当前积分 {points} · 将获得信值 {Math.floor((Number(exPoints) || 0) / 100)}
        </View>
        <View className={styles.panelBtn} onClick={handleExchange}>
          {submitting ? '兑换中...' : '确认兑换'}
        </View>
      </View>
    </View>
  );

  // ============ 试算面板 ============
  const renderPlanPanel = () => (
    <View className={styles.mask} onClick={() => { setPanel(null); setPlan(null); }}>
      <View className={styles.panel} onClick={(e) => e.stopPropagation()}>
        <View className={styles.panelTitle}>最优支付组合试算</View>
        <View className={styles.panelRate}>刚性结构: 信值最多抵 30%, 现金至少 70%</View>
        <View className={styles.amountRow}>
          <Text className={styles.amountLabel}>¥</Text>
          <Input
            className={styles.amountInput}
            type="digit"
            value={planPrice}
            onInput={(e) => setPlanPrice(e.detail.value)}
            placeholder="商品价格"
            placeholderClass={styles.placeholder}
          />
        </View>
        <View className={styles.panelBtn} onClick={handlePlan}>
          {submitting ? '试算中...' : '开始试算'}
        </View>
        {plan && (
          <View className={styles.planResult}>
            <View className={styles.planRow}>
              <Text className={styles.planLabel}>信值余额</Text>
              <Text className={styles.planValue}>{plan.balance}</Text>
            </View>
            <View className={`${styles.planRow} ${plan.planA.feasible ? '' : styles.planDim}`}>
              <Text className={styles.planLabel}>{plan.planA.label}</Text>
              <Text className={styles.planValue}>
                {plan.planA.feasible
                  ? `信值 ${plan.planA.trustValue} + 现金 ¥${plan.planA.cash} · 省 ¥${plan.planA.saving}`
                  : `需信值 ${plan.planA.trustValue} + 现金 ¥${plan.planA.cash} · 缺额 ${plan.planA.gap}(需积分 ${plan.planA.gapPoints})`}
              </Text>
            </View>
            <View className={styles.planRow}>
              <Text className={styles.planLabel}>{plan.planB.label}</Text>
              <Text className={styles.planValue}>现金 ¥{plan.planB.cash}</Text>
            </View>
          </View>
        )}
      </View>
    </View>
  );

  // ============ 申诉面板 ============
  const renderAppealPanel = () => appealOrder && (
    <View className={styles.mask} onClick={() => { if (!appealBusy) closeAppeal(); }}>
      <View className={styles.panel} onClick={e => e.stopPropagation()}>
        <View className={styles.panelTitle}>订单申诉</View>
        {appealResult ? (
          <>
            <View className={styles.planResult}>
              <View className={styles.planRow}>
                <Text className={styles.planLabel}>申诉编号</Text>
                <Text className={styles.planValue}>#{appealResult.appealId}</Text>
              </View>
              <View className={styles.planRow}>
                <Text className={styles.planLabel}>状态</Text>
                <Text className={styles.planValue}>
                  {APPEAL_STATUS_NAME[appealResult.status] || appealResult.status}
                </Text>
              </View>
              <View className={styles.planRow}>
                <Text className={styles.planLabel}>终审截止</Text>
                <Text className={styles.planValue}>
                  {(appealResult.expiresAt || '').slice(0, 16).replace('T', ' ')}
                </Text>
              </View>
            </View>
            <View className={styles.panelNote}>
              {appealResult.note || '重算结果仅展示, 终审为人工决定, 终审前订单现状不变'}
            </View>
            <View className={styles.panelBtn} onClick={closeAppeal}>知道了</View>
          </>
        ) : (
          <>
            <View className={styles.panelRate}>
              订单 #{appealOrder.orderId} · {appealOrder.product || '信值订单'} · ¥{appealOrder.price}
            </View>
            <View className={styles.panelRate}>
              提交后触发确定性重算(预校验四查+规则解释+五防检测)——重算仅展示, 终审由人工完成, 终审前订单现状不变
            </View>
            <Textarea
              className={styles.appealTextarea}
              value={appealReason}
              onInput={e => setAppealReason(e.detail.value)}
              maxlength={500}
              placeholder="请填写申诉理由(1-500 字)"
              placeholderClass={styles.placeholder}
            />
            <View className={styles.panelNote}>{appealReason.length}/500</View>
            <View className={styles.panelBtn} onClick={handleAppeal}>
              {appealBusy ? '提交中...' : '提交申诉'}
            </View>
          </>
        )}
      </View>
    </View>
  );

  return (
    <View className={styles.page}>
      <NavBar title="信值兑换" />
      <ScrollView scrollY className={styles.scrollView}>
        {loading ? (
          <View className={styles.empty}>加载中...</View>
        ) : trustId && balance ? (
          <>
            {/* 信值余额卡 */}
            <View className={styles.heroCard}>
              <View className={styles.heroTitle}>我的信值</View>
              <View className={styles.heroValue}>{balance.available.toFixed(2)}</View>
              <View className={styles.heroMeta}>
                冻结中 {balance.frozen.toFixed(2)} · 1 信值 = 1 元购买力 · 日兑换上限 ¥{balance.dailyCap}
              </View>
              <View className={styles.heroBtns}>
                <View className={styles.heroBtn} onClick={() => setPanel('exchange')}>积分兑换</View>
                <View className={styles.heroBtnGhost} onClick={() => setPanel('plan')}>支付试算</View>
              </View>
            </View>

            {/* 积分信息卡 */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>积分余额</View>
              <View className={styles.bigPoints}>{points}</View>
              {preview && (
                <>
                  <View className={styles.calcRow}>
                    <Text className={styles.calcLabel}>兑换比例</Text>
                    <Text className={styles.calcValue}>{preview.rate}</Text>
                  </View>
                  <View className={styles.calcRow}>
                    <Text className={styles.calcLabel}>冻结观察中</Text>
                    <Text className={styles.calcValue}>{preview.pendingValue}</Text>
                  </View>
                  <View className={styles.calcRow}>
                    <Text className={styles.calcLabel}>已入账信值</Text>
                    <Text className={styles.calcValue}>{preview.creditedValue}</Text>
                  </View>
                </>
              )}
            </View>

            {/* 限额卡 */}
            {quota && (
              <View className={styles.card}>
                <View className={styles.cardTitle}>兑换限额(观测)</View>
                <View className={styles.calcRow}>
                  <Text className={styles.calcLabel}>信值余额基准</Text>
                  <Text className={styles.calcValue}>{quota.balance}</Text>
                </View>
                <View className={styles.calcRow}>
                  <Text className={styles.calcLabel}>单次限额(20%)</Text>
                  <Text className={styles.calcValue}>{quota.singleQuota}</Text>
                </View>
                <View className={styles.calcRow}>
                  <Text className={styles.calcLabel}>窗口剩余(40%/{quota.windowDays}天)</Text>
                  <Text className={styles.calcValue}>{quota.windowRemaining}</Text>
                </View>
              </View>
            )}

            {/* 订单·申诉卡 */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>订单·申诉</View>
              {myOrders.length === 0 && (
                <View className={styles.empty}>暂无兑换订单</View>
              )}
              {myOrders.map(o => (
                <View key={o.orderId} className={styles.orderCard}>
                  <View className={styles.orderHead}>
                    <Text className={styles.orderIdText}>#{o.orderId} {o.product || '信值订单'}</Text>
                    <Text className={styles.orderStatus}>
                      {ORDER_STATUS_NAME[o.status] || o.status}
                    </Text>
                  </View>
                  <View className={styles.calcRow}>
                    <Text className={styles.calcLabel}>金额</Text>
                    <Text className={styles.calcValue}>¥{o.price}</Text>
                  </View>
                  <View className={styles.appealMeta}>
                    {(o.createdAt || '').slice(0, 16).replace('T', ' ')}
                  </View>
                  {o.appeal ? (
                    <View className={styles.appealRow}>
                      <Text
                        className={`${styles.appealStatusText} ${
                          APPEAL_TERMINAL.includes(o.appeal.status) ? styles.appealStatusDone : ''
                        }`}
                      >
                        申诉#{o.appeal.appealId} · {APPEAL_STATUS_NAME[o.appeal.status] || o.appeal.status}
                      </Text>
                      {appealable(o) && (
                        <Text className={styles.appealBtn} onClick={() => openAppeal(o)}>再次申诉</Text>
                      )}
                    </View>
                  ) : appealable(o) ? (
                    <View className={styles.appealRow}>
                      <Text className={styles.appealMeta}>对订单有异议? 可提交申诉</Text>
                      <Text className={styles.appealBtn} onClick={() => openAppeal(o)}>申诉</Text>
                    </View>
                  ) : null}
                </View>
              ))}
            </View>
          </>
        ) : (
          // 未建档
          <View className={styles.card}>
            <View className={styles.empty}>
              <View className={styles.emptyIcon}>🧧</View>
              <View className={styles.emptyText}>开通信值档案后即可使用</View>
              <View className={styles.emptySub}>
                信值 = 个人信用资产 · 积分可兑换 · 支付抵扣 30%
              </View>
            </View>
            <View className={styles.panelBtn} onClick={() => setPanel('create')}>
              开通信值档案
            </View>
          </View>
        )}

        {/* 规则说明 */}
        <View className={styles.noteCard}>
          <View className={styles.noteTitle}>信值规则</View>
          <View className={styles.noteLine}>· 积分: 信值 = 100:1, 兑换后 T+1 冻结观察入账</View>
          <View className={styles.noteLine}>· 支付刚性结构: 信值最多抵扣 30%, 现金至少 70%</View>
          <View className={styles.noteLine}>· 1 信值 = 1 元购买力, 可兑换商品/服务(不可兑现)</View>
          <View className={styles.noteLine}>· 兑换限额: 单次余额 20% / 30 日窗口余额 40%</View>
          <View className={styles.noteLine}>· 申诉通道永不关闭: 确定性重算仅展示, 终审人工(48h 内), 翻转可获补偿</View>
        </View>
        <View className={styles.bottomSpacer} />
      </ScrollView>
      {panel === 'create' && renderCreatePanel()}
      {panel === 'exchange' && renderExchangePanel()}
      {panel === 'plan' && renderPlanPanel()}
      {appealOrder && renderAppealPanel()}
    </View>
  );
};

export default TrustPage;
