import React, { useState, useEffect } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import { PointsAPI, PointsAccountVO, PointsLogVO, SigninResultVO } from '@/api/points';
import { getMemberId, isLoggedIn, requireLogin } from '@/services/auth-service';

/**
 * 积分中心页 · 对接 GET /api/points/account + logs + signin 记录
 * 账户概览 + 签到日历(近7日) + 积分流水
 */
const PointsPage: React.FC = () => {
  const [account, setAccount] = useState<PointsAccountVO | null>(null);
  const [logs, setLogs] = useState<PointsLogVO[]>([]);
  const [signRecords, setSignRecords] = useState<SigninResultVO[]>([]);
  const [loading, setLoading] = useState(true);

  const loadData = async () => {
    if (!isLoggedIn()) {
      setLoading(false);
      return;
    }
    const uid = Number(getMemberId());
    setLoading(true);
    try {
      const [acc, logList, records] = await Promise.all([
        PointsAPI.account(uid).catch(() => null),
        PointsAPI.logs(uid, 50).catch(() => [] as PointsLogVO[]),
        PointsAPI.signinRecords(uid, 7).catch(() => [] as SigninResultVO[]),
      ]);
      setAccount(acc);
      setLogs(logList);
      setSignRecords(records);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (requireLogin()) loadData();
    else setLoading(false);
  }, []);

  useDidShow(() => {
    if (isLoggedIn()) loadData();
  });

  // 近 7 日签到日历(周一→周日 = 今天往前 6 天)
  const last7Days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - (6 - i));
    const dateStr = d.toISOString().slice(0, 10);
    const rec = signRecords.find(r => r.signDate === dateStr);
    return {
      dateStr,
      label: `${d.getMonth() + 1}/${d.getDate()}`,
      signed: Boolean(rec),
      bonus: Boolean(rec?.isBonus),
    };
  });
  const todayStr = new Date().toISOString().slice(0, 10);
  const signedToday = signRecords.some(r => r.signDate === todayStr);

  const handleSignIn = () => {
    // 跳回首页签到(复用首页签到逻辑, 避免双入口逻辑分叉)
    Taro.switchTab({ url: '/pages/index/index' });
  };

  return (
    <View className={styles.page}>
      <NavBar title="我的积分" />
      <ScrollView scrollY className={styles.scrollView}>
        {/* 账户概览 */}
        <View className={styles.accountCard}>
          <View className={styles.accountTop}>
            <View>
              <View className={styles.pointsValue}>{account?.totalPoints ?? '—'}</View>
              <View className={styles.pointsLabel}>当前积分</View>
            </View>
            <View
              className={`${styles.signinBtn} ${signedToday ? styles.signed : ''}`}
              onClick={signedToday ? undefined : handleSignIn}
            >
              {signedToday ? '今日已签' : '去签到'}
            </View>
          </View>
          <View className={styles.accountStats}>
            <View className={styles.statItem}>
              <View className={styles.statValue}>{account?.totalEarned ?? 0}</View>
              <View className={styles.statLabel}>累计获得</View>
            </View>
            <View className={styles.statItem}>
              <View className={styles.statValue}>{account?.totalSpent ?? 0}</View>
              <View className={styles.statLabel}>累计消耗</View>
            </View>
            <View className={styles.statItem}>
              <View className={styles.statValue}>{account?.frozenPoints ?? 0}</View>
              <View className={styles.statLabel}>冻结中</View>
            </View>
          </View>
          {(account?.expiringPoints ?? 0) > 0 && (
            <View className={styles.expiringTip}>
              ⏰ {account?.expiringPoints} 积分将于 30 日内过期, 请尽快使用
            </View>
          )}
        </View>

        {/* 近 7 日签到日历 */}
        <View className={styles.calendarCard}>
          <View className={styles.cardTitle}>近 7 日签到</View>
          <View className={styles.calendarRow}>
            {last7Days.map(d => (
              <View key={d.dateStr} className={styles.calendarItem}>
                <View className={`${styles.calendarDot} ${d.signed ? (d.bonus ? styles.bonusDot : styles.signedDot) : ''}`}>
                  {d.bonus ? '🎁' : d.signed ? '✓' : '·'}
                </View>
                <View className={`${styles.calendarDate} ${d.dateStr === todayStr ? styles.today : ''}`}>
                  {d.label}
                </View>
              </View>
            ))}
          </View>
          <View className={styles.calendarRule}>
            连续签到第 7/14/21 天开启宝箱, 积分加倍 · 连续中断重新计
          </View>
        </View>

        {/* 积分流水 */}
        <View className={styles.logsCard}>
          <View className={styles.cardTitle}>积分明细</View>
          {loading ? (
            <View className={styles.empty}>加载中...</View>
          ) : logs.length === 0 ? (
            <View className={styles.empty}>
              暂无积分记录{'\n'}每日签到、下单购物均可获得积分
            </View>
          ) : (
            logs.map(l => (
              <View key={l.id} className={styles.logItem}>
                <View className={styles.logLeft}>
                  <View className={styles.logDesc}>{l.refDesc || (l.type === 'earn' ? '积分收入' : '积分支出')}</View>
                  <View className={styles.logDate}>{(l.createdAt || '').slice(0, 16).replace('T', ' ')}</View>
                </View>
                <View className={`${styles.logPoints} ${l.type === 'earn' ? styles.earn : styles.spend}`}>
                  {l.type === 'earn' ? '+' : ''}{l.points}
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

export default PointsPage;
