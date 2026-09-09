/**
 * 代驾联盟 · 买竹香酒满 ¥500 赠代驾券
 * 券包 → 叫代驾(FEFO 选券+三轨派单) → 行程管理(取消/详情)
 * 司机端: 注册申请(AI 审查) → 上下线 → 接单流转
 * 数据来源: 后端 /api/ride/*
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  RideAPI, RideVO, CouponPackageVO, DriverApplicationVO,
  rideStatusName, couponStatusName, CANCELLABLE_STATUSES,
} from '@/api/ride';
import { requireLogin } from '@/services/auth-service';

type Tab = 'call' | 'rides' | 'coupons' | 'driver';

const TABS: { key: Tab; label: string }[] = [
  { key: 'call', label: '叫代驾' },
  { key: 'rides', label: '我的行程' },
  { key: 'coupons', label: '券包' },
  { key: 'driver', label: '司机端' },
];

// 预设地点(济南, 演示用坐标)
const PRESETS = [
  { name: '竹韵大酒店(历下区)', lat: 36.6612, lng: 117.1201 },
  { name: '泉城广场(市中心)', lat: 36.6634, lng: 117.0268 },
  { name: '济南西站(槐荫区)', lat: 36.6689, lng: 116.9009 },
];

const formatTime = (t?: string | null): string => (t ? t.slice(0, 16).replace('T', ' ') : '');

const RidePage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('call');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  // 叫代驾表单
  const [puIdx, setPuIdx] = useState(0);
  const [doIdx, setDoIdx] = useState(2);
  const [pickupAddr, setPickupAddr] = useState('竹韵大酒店门口');
  const [dropoffAddr, setDropoffAddr] = useState('济南西站南广场');
  // 数据
  const [rides, setRides] = useState<RideVO[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [pkg, setPkg] = useState<CouponPackageVO | null>(null);
  const [lastRide, setLastRide] = useState<RideVO | null>(null);
  // 司机端
  const [driverApp, setDriverApp] = useState<DriverApplicationVO | null>(null);
  const [driverRides, setDriverRides] = useState<RideVO[]>([]);
  const [showDriverApply, setShowDriverApply] = useState(false);
  const [daIdNumber, setDaIdNumber] = useState('');
  const [daLicense, setDaLicense] = useState('');
  const [daYears, setDaYears] = useState('5');
  const [daContact, setDaContact] = useState('');

  const loadData = useCallback(async () => {
    const [rs, cp, app, drs] = await Promise.all([
      RideAPI.myRides().catch(() => [] as RideVO[]),
      RideAPI.coupons().catch(() => null),
      RideAPI.driverApplication().catch(() => null),
      RideAPI.driverRides().catch(() => [] as RideVO[]),
    ]);
    setRides(rs);
    setPkg(cp);
    setDriverApp(app);
    setDriverRides(drs);
  }, []);

  useEffect(() => {
    if (requireLogin()) {
      loadData().finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, [loadData]);

  // 叫代驾
  const handleCall = async () => {
    if (submitting) return;
    const pickup = PRESETS[puIdx];
    const dropoff = PRESETS[doIdx];
    if (puIdx === doIdx) {
      Taro.showToast({ title: '起终点相同, 无需代驾', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const ride = await RideAPI.call({
        pickup: { lat: pickup.lat, lng: pickup.lng, address: pickupAddr.trim() || pickup.name },
        dropoff: { lat: dropoff.lat, lng: dropoff.lng, address: dropoffAddr.trim() || dropoff.name },
      });
      setLastRide(ride);
      const ds: any = ride.driverSnapshot || {};
      Taro.showModal({
        title: ride.status === 'dispatched' ? '派单成功' : '已叫单',
        content: ride.status === 'dispatched'
          ? `司机 ${ds.name || '平台司机'}(${ds.trackName || '平台直发'})已接单, ${ride.distanceKm.toFixed(1)}km, 券抵扣 ¥${ride.couponValue.toFixed(0)}。`
          : `行程已创建(${rideStatusName(ride.status)}), 券 ${ride.couponCode} 已占用。`,
        showCancel: false,
      });
      await loadData();
    } catch (e) {
      console.warn('[ride] 叫单失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 取消行程
  const handleCancel = async (ride: RideVO) => {
    if (submitting) return;
    const res = await Taro.showModal({
      title: '确认取消',
      content: '派单 3 分钟内取消券退回, 超时取消券将作废。',
    });
    if (!res.confirm) return;
    setSubmitting(true);
    try {
      await RideAPI.cancelRide(ride.rideId, '用户主动取消');
      Taro.showToast({ title: '行程已取消', icon: 'success' });
      await loadData();
    } catch (e) {
      console.warn('[ride] 取消失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 司机申请
  const handleDriverApply = async () => {
    if (submitting) return;
    if (!/^\d{18}$/.test(daIdNumber.trim())) {
      Taro.showToast({ title: '身份证号须为 18 位', icon: 'none' });
      return;
    }
    if (!daLicense.trim() || daLicense.trim().length < 10) {
      Taro.showToast({ title: '请输入有效驾照号', icon: 'none' });
      return;
    }
    if (!daContact.trim()) {
      Taro.showToast({ title: '请填写紧急联系人', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const app = await RideAPI.driverApply({
        idNumber: daIdNumber.trim(),
        licenseNumber: daLicense.trim(),
        drivingYears: Number(daYears) || 0,
        accidentFreeDecl: true,
        drunkFreeDecl: true,
        emergencyContact: daContact.trim(),
      });
      Taro.showModal({
        title: '申请已提交',
        content: `AI 审查评分 ${app.aiScore ?? '-'} 分, 结果: ${app.status === 'approved' ? '已通过入池' : app.status === 'manual_review' ? '转人工复核' : '未通过'}。`,
        showCancel: false,
      });
      setShowDriverApply(false);
      await loadData();
    } catch (e) {
      console.warn('[ride] 司机申请失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 司机上下线
  const handleDriverStatus = async (status: 'online' | 'offline') => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await RideAPI.setDriverStatus(status);
      Taro.showToast({ title: status === 'online' ? '已上线接单' : '已下线', icon: 'success' });
      await loadData();
    } catch (e) {
      console.warn('[ride] 上下线失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 司机流转(接单/开始/完成)
  const handleDriverFlow = async (rideId: string, action: 'accept' | 'start' | 'complete') => {
    if (submitting) return;
    setSubmitting(true);
    try {
      if (action === 'accept') await RideAPI.driverAccept(rideId);
      if (action === 'start') await RideAPI.driverStart(rideId);
      if (action === 'complete') await RideAPI.driverComplete(rideId);
      Taro.showToast({
        title: action === 'accept' ? '已接单' : action === 'start' ? '行程开始' : '行程完成, 已结算',
        icon: 'success',
      });
      await loadData();
    } catch (e) {
      console.warn('[ride] 流转失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 可用券数
  const usableCoupons = (pkg?.coupons || []).filter(c => c.status === 'granted');

  return (
    <View className={styles.page}>
      <NavBar title="代驾联盟" />
      {/* 顶部 Tabs */}
      <View className={styles.tabBar}>
        {TABS.map(t => (
          <View
            key={t.key}
            className={`${styles.tabItem} ${tab === t.key ? styles.tabItemActive : ''}`}
            onClick={async () => {
              setTab(t.key);
              await loadData();
            }}
          >
            {t.label}
          </View>
        ))}
      </View>

      <ScrollView scrollY className={styles.scrollView}>
        {loading ? (
          <View className={styles.empty}>加载中...</View>
        ) : tab === 'call' ? (
          <>
            {/* 券提示卡 */}
            <View className={styles.heroCard}>
              <View className={styles.heroLabel}>可用代驾券</View>
              <View className={styles.heroValue}>{usableCoupons.length} 张</View>
              <View className={styles.heroMeta}>
                {usableCoupons.length > 0
                  ? `共 ¥${usableCoupons.reduce((s, c) => s + c.value, 0).toFixed(0)} · 叫单自动选最早过期券(FEFO)`
                  : '买竹香酒满 ¥500 支付后自动赠券 ¥60/张'}
              </View>
            </View>

            {/* 叫代驾表单 */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>一键叫代驾</View>
              <View className={styles.presetRow}>
                <Text className={styles.presetLabel}>上车点</Text>
                <Text className={styles.presetValue}>{PRESETS[puIdx].name}</Text>
                <Text className={styles.presetArrow} onClick={() => {
                  setPuIdx((puIdx + 1) % PRESETS.length);
                  setPickupAddr(PRESETS[(puIdx + 1) % PRESETS.length].name);
                }}>切换</Text>
              </View>
              <View className={styles.inputRow}>
                <Input
                  className={styles.input}
                  value={pickupAddr}
                  onInput={(e) => setPickupAddr((e.detail as any).value)}
                  placeholder="上车点详细地址"
                  placeholderClass={styles.placeholder}
                  maxlength={40}
                />
              </View>
              <View className={styles.presetRow}>
                <Text className={styles.presetLabel}>下车点</Text>
                <Text className={styles.presetValue}>{PRESETS[doIdx].name}</Text>
                <Text className={styles.presetArrow} onClick={() => {
                  setDoIdx((doIdx + 1) % PRESETS.length);
                  setDropoffAddr(PRESETS[(doIdx + 1) % PRESETS.length].name);
                }}>切换</Text>
              </View>
              <View className={styles.inputRow}>
                <Input
                  className={styles.input}
                  value={dropoffAddr}
                  onInput={(e) => setDropoffAddr((e.detail as any).value)}
                  placeholder="下车点详细地址"
                  placeholderClass={styles.placeholder}
                  maxlength={40}
                />
              </View>
              <View className={styles.callBtn} onClick={handleCall}>
                {submitting ? '派单中...' : '呼叫代驾'}
              </View>
            </View>

            {/* 最近一单 */}
            {lastRide && (
              <View className={styles.card}>
                <View className={styles.cardTitle}>最近一单</View>
                <View className={styles.rideRow}>
                  <View className={styles.rideLeft}>
                    <View className={styles.rideTitle}>{lastRide.rideId}</View>
                    <View className={styles.rideMeta}>
                      {lastRide.pickup.address} → {lastRide.dropoff.address}
                    </View>
                    <View className={styles.rideMeta}>
                      {lastRide.distanceKm.toFixed(1)}km · {rideStatusName(lastRide.status)}
                      {lastRide.driverSnapshot.name ? ` · 司机 ${lastRide.driverSnapshot.name}` : ''}
                    </View>
                  </View>
                </View>
              </View>
            )}
          </>
        ) : tab === 'rides' ? (
          <View className={styles.card}>
            <View className={styles.cardTitle}>我的行程</View>
            {rides.length === 0 ? (
              <View className={styles.empty}>
                <View className={styles.emptyIcon}>🚗</View>
                <View>暂无行程, 买酒满 ¥500 自动获赠代驾券</View>
              </View>
            ) : (
              rides.map(r => {
                const open = expanded === r.rideId;
                const ds = r.driverSnapshot;
                return (
                  <View key={r.rideId} className={styles.rideBlock}>
                    <View className={styles.rideRow} onClick={() => setExpanded(open ? null : r.rideId)}>
                      <View className={styles.rideLeft}>
                        <View className={styles.rideTitle}>{r.rideId}</View>
                        <View className={styles.rideMeta}>
                          {r.pickup.address} → {r.dropoff.address}
                        </View>
                        <View className={styles.rideMeta}>
                          {r.distanceKm.toFixed(1)}km · 券 ¥{r.couponValue.toFixed(0)} · {formatTime(r.requestedAt)}
                        </View>
                      </View>
                      <View className={styles.rideRight}>
                        <View className={`${styles.statusBadge} ${r.status === 'settled' ? styles.badgeSettled : r.status === 'cancelled' || r.status === 'no_driver' ? styles.badgeCancelled : ''}`}>
                          {rideStatusName(r.status)}
                        </View>
                        <View className={styles.expandArrow}>{open ? '▾' : '›'}</View>
                      </View>
                    </View>
                    {open && (
                      <View className={styles.rideDetail}>
                        {ds && (ds as any).name ? (
                          <>
                            <View className={styles.detailLine}>
                              司机: {(ds as any).name}({(ds as any).trackName || '平台直发'})
                              {(ds as any).plateNo ? ` · ${(ds as any).plateNo}` : ''}
                              {(ds as any).rating != null ? ` · 评分 ${(ds as any).rating}` : ''}
                            </View>
                            <View className={styles.detailLine}>派单模式: {r.dispatchMode || 'ai'}</View>
                          </>
                        ) : (
                          <View className={styles.detailLine}>暂无司机信息(平台直发或未派单)</View>
                        )}
                        {r.pricing && Object.keys(r.pricing).length > 0 && (
                          <>
                            <View className={styles.detailLine}>
                              计价: 起步 ¥{Number(r.pricing.baseFare ?? 0).toFixed(0)}
                              {Number(r.pricing.extraKmFee ?? 0) > 0 ? ` + 里程 ¥${Number(r.pricing.extraKmFee).toFixed(2)}` : ''}
                              {Number(r.pricing.extraMinFee ?? 0) > 0 ? ` + 超时 ¥${Number(r.pricing.extraMinFee).toFixed(2)}` : ''}
                              {Number(r.pricing.nightSurge ?? 0) > 0 ? ` + 夜间 ¥${Number(r.pricing.nightSurge).toFixed(2)}` : ''}
                              = ¥{Number(r.pricing.totalAmount ?? 0).toFixed(2)}
                            </View>
                            <View className={styles.detailLine}>
                              券抵扣 ¥{Number(r.pricing.couponDeduction ?? 0).toFixed(2)} · 乘客补差 ¥{Number(r.pricing.extraCharge ?? 0).toFixed(2)}
                            </View>
                          </>
                        )}
                        {r.cancelReason && (
                          <View className={styles.detailLine}>取消原因: {r.cancelReason}</View>
                        )}
                        {CANCELLABLE_STATUSES.includes(r.status) && (
                          <View className={styles.cancelBtn} onClick={() => handleCancel(r)}>取消行程</View>
                        )}
                      </View>
                    )}
                  </View>
                );
              })
            )}
          </View>
        ) : tab === 'coupons' ? (
          <View className={styles.card}>
            <View className={styles.cardTitle}>代驾券包</View>
            {!pkg || pkg.coupons.length === 0 ? (
              <View className={styles.empty}>
                <View className={styles.emptyIcon}>🎫</View>
                <View>暂无代驾券, 买竹香酒满 ¥500 自动赠券</View>
              </View>
            ) : (
              <>
                <View className={styles.pkgStats}>
                  持有 {pkg.holdCount}/{pkg.holdCap} · 累计发放 {pkg.totalGranted} · 已用 {pkg.totalUsed}
                </View>
                {pkg.expiringSoon.length > 0 && (
                  <View className={styles.expiringTip}>
                    {pkg.expiringSoon.length} 张券 7 天内过期, 尽快使用
                  </View>
                )}
                {pkg.coupons.map(c => (
                  <View key={c.code} className={styles.couponRow}>
                    <View className={styles.couponLeft}>
                      <View className={styles.couponValue}>¥{c.value.toFixed(0)}</View>
                      <View className={styles.couponCode}>{c.code}</View>
                    </View>
                    <View className={styles.couponRight}>
                      <View className={`${styles.couponBadge} ${c.status === 'granted' ? styles.couponOk : ''}`}>
                        {couponStatusName(c.status)}
                      </View>
                      <View className={styles.couponExp}>有效期至 {formatTime(c.expiresAt).slice(0, 10)}</View>
                    </View>
                  </View>
                ))}
              </>
            )}
          </View>
        ) : (
          <View className={styles.card}>
            <View className={styles.cardTitle}>司机端</View>
            {driverApp ? (
              <>
                <View className={styles.driverStatusRow}>
                  <View>
                    <View className={styles.driverTitle}>申请 #{driverApp.applicationId}</View>
                    <View className={styles.rideMeta}>
                      AI 评分 {driverApp.aiScore ?? '-'} · 状态 {driverApp.status}
                    </View>
                  </View>
                  {driverApp.status === 'approved' && (
                    <View className={styles.onlineBtns}>
                      <View className={styles.miniBtn} onClick={() => handleDriverStatus('online')}>上线</View>
                      <View className={styles.miniBtnGhost} onClick={() => handleDriverStatus('offline')}>下线</View>
                    </View>
                  )}
                </View>

                {/* 司机行程 */}
                <View className={styles.venueSection}>
                  <View className={styles.sectionTitle}>我的接单</View>
                  {driverRides.length === 0 ? (
                    <View className={styles.venueEmpty}>暂无派给我的行程</View>
                  ) : (
                    driverRides.map(r => (
                      <View key={r.rideId} className={styles.driverRideRow}>
                        <View className={styles.rideLeft}>
                          <View className={styles.rideTitle}>{r.rideId}</View>
                          <View className={styles.rideMeta}>
                            {r.pickup.address} → {r.dropoff.address} · {r.distanceKm.toFixed(1)}km
                          </View>
                        </View>
                        <View className={styles.driverActions}>
                          {r.status === 'dispatched' && (
                            <View className={styles.miniBtn} onClick={() => handleDriverFlow(r.rideId, 'accept')}>接单</View>
                          )}
                          {r.status === 'driver_arriving' && (
                            <View className={styles.miniBtn} onClick={() => handleDriverFlow(r.rideId, 'start')}>乘客上车</View>
                          )}
                          {r.status === 'trip_started' && (
                            <View className={styles.miniBtn} onClick={() => handleDriverFlow(r.rideId, 'complete')}>完成行程</View>
                          )}
                          <View className={styles.statusBadge}>{rideStatusName(r.status)}</View>
                        </View>
                      </View>
                    ))
                  )}
                </View>
              </>
            ) : (
              <>
                <View className={styles.empty}>
                  <View className={styles.emptyIcon}>🧑‍✈️</View>
                  <View className={styles.emptyText}>成为竹香酒代驾员</View>
                  <View className={styles.emptySub}>
                    超级会员专享 · AI 全自动审查即时出档<br />
                    买竹香酒满 ¥500 赠 ¥60 代驾券 · 三轨派单永不拒单
                  </View>
                </View>
                <View
                  className={styles.applyBtn}
                  onClick={() => {
                    if (!requireLogin()) return;
                    setDaIdNumber('');
                    setDaLicense('');
                    setDaYears('5');
                    setDaContact('');
                    setShowDriverApply(true);
                  }}
                >
                  申请成为代驾员
                </View>
              </>
            )}
          </View>
        )}

        {/* 规则说明 */}
        <View className={styles.noteCard}>
          <View className={styles.noteTitle}>代驾联盟规则</View>
          <View className={styles.noteLine}>· 买竹香酒订单满 ¥500, 支付后自动赠 ¥60 代驾券(最多持有 6 张)</View>
          <View className={styles.noteLine}>· 叫单自动选最早过期券(FEFO), 市内 40km 范围内有效</View>
          <View className={styles.noteLine}>· 计价: 起步 ¥35(含 5km) + ¥5/km + 超时 ¥1/min, 夜间加成 20%</View>
          <View className={styles.noteLine}>· 券抵扣本站支付部分, 超出部分乘客补差价</View>
          <View className={styles.noteLine}>· 派单 3 分钟内取消券退回, 超时取消券作废</View>
        </View>
        <View className={styles.bottomSpacer} />
      </ScrollView>

      {/* 司机申请弹层 */}
      {showDriverApply && (
        <View className={styles.mask} onClick={() => setShowDriverApply(false)}>
          <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
            <View className={styles.sheetTitle}>申请成为代驾员</View>
            <View className={styles.sheetDesc}>超级会员专享 · AI 全自动审查即时出档</View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={daIdNumber}
                onInput={(e) => setDaIdNumber((e.detail as any).value)}
                placeholder="身份证号(18 位)"
                placeholderClass={styles.placeholder}
                maxlength={18}
              />
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={daLicense}
                onInput={(e) => setDaLicense((e.detail as any).value)}
                placeholder="驾照号"
                placeholderClass={styles.placeholder}
                maxlength={20}
              />
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                type="digit"
                value={daYears}
                onInput={(e) => setDaYears((e.detail as any).value)}
                placeholder="驾龄(年)"
                placeholderClass={styles.placeholder}
                maxlength={3}
              />
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={daContact}
                onInput={(e) => setDaContact((e.detail as any).value)}
                placeholder="紧急联系人(姓名/电话)"
                placeholderClass={styles.placeholder}
                maxlength={30}
              />
            </View>
            <View className={styles.declRow}>
              申请即声明: 无重大交通事故 · 无酒驾记录
            </View>
            <View className={styles.sheetBtn} onClick={handleDriverApply}>
              {submitting ? '提交中...' : '提交申请'}
            </View>
          </View>
        </View>
      )}
    </View>
  );
};

export default RidePage;
