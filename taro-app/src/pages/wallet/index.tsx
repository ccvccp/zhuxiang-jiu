/**
 * 钱包 · 余额/充值/提现/流水/补贴预估
 * 数据来源: 后端 /api/wallet/*
 * 开通条件: 会员等级 ≥ L2(成长值 ≥ 500), 未开通时引导开通
 */
import React, { useState, useEffect, useMemo } from 'react';
import { View, Text, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import { WalletAPI, WalletInfoVO, WalletTxVO, TX_TYPE_NAME } from '@/api/wallet';

// 流水类型筛选
const TX_TABS = [
  { key: '', label: '全部' },
  { key: 'deposit', label: '充值' },
  { key: 'withdraw', label: '提现' },
  { key: 'consume', label: '消费' },
  { key: 'interest', label: '补贴' },
  { key: 'rebate', label: '返利' },
];

// 快捷充值金额
const QUICK_AMOUNTS = [100, 500, 1000, 5000];

type PageState = 'loading' | 'not-open' | 'ready';

const formatTime = (t: string): string => (t ? t.slice(0, 19).replace('T', ' ') : '');

const WalletPage: React.FC = () => {
  const [state, setState] = useState<PageState>('loading');
  const [info, setInfo] = useState<WalletInfoVO | null>(null);
  const [txs, setTxs] = useState<WalletTxVO[]>([]);
  const [txFilter, setTxFilter] = useState('');
  const [opening, setOpening] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // 充值弹层状态
  const [showDeposit, setShowDeposit] = useState(false);
  const [depAmount, setDepAmount] = useState('');
  // 提现弹层状态
  const [showWithdraw, setShowWithdraw] = useState(false);
  const [wdAmount, setWdAmount] = useState('');

  const loadAll = async () => {
    try {
      const [w, t] = await Promise.all([
        WalletAPI.info(),
        WalletAPI.transactions().catch((): WalletTxVO[] => []),
      ]);
      setInfo(w);
      setTxs(t);
      setState('ready');
    } catch (e: any) {
      // 404 = 钱包未开通
      const notOpen = String(e?.message || '').includes('未开通');
      setState(notOpen ? 'not-open' : 'ready');
      if (!notOpen) console.warn('[wallet] 加载失败:', e);
    }
  };

  useEffect(() => { loadAll(); }, []);

  // 筛选流水
  const filteredTxs = useMemo(() => {
    if (!txFilter) return txs;
    return txs.filter(t => t.type === txFilter);
  }, [txs, txFilter]);

  const handleTab = (key: string) => {
    setTxFilter(key);
  };

  // 开通钱包
  const handleOpen = async () => {
    if (opening) return;
    setOpening(true);
    try {
      await WalletAPI.open();
      Taro.showToast({ title: '开通成功', icon: 'success' });
      setState('loading');
      loadAll();
    } catch (e) {
      console.warn('[wallet] 开通失败:', e);
    } finally {
      setOpening(false);
    }
  };

  // 充值
  const handleDeposit = async () => {
    if (submitting) return;
    const amount = Number(depAmount);
    if (!amount || amount < 100) {
      Taro.showToast({ title: '充值最低 ¥100', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      await WalletAPI.deposit(amount);
      Taro.showToast({ title: '充值成功', icon: 'success' });
      setShowDeposit(false);
      setDepAmount('');
      loadAll();
    } catch (e) {
      console.warn('[wallet] 充值失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 提现
  const handleWithdraw = async () => {
    if (submitting) return;
    const amount = Number(wdAmount);
    if (!amount || amount <= 0) {
      Taro.showToast({ title: '请输入提现金额', icon: 'none' });
      return;
    }
    if (info && amount > info.currentBalance) {
      Taro.showToast({ title: '超出可提余额', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const res = await WalletAPI.withdraw(amount);
      const tip = res.status === 'auto_approved'
        ? '提现成功' : `提现申请已提交(${res.statusName || '待审核'})`;
      Taro.showToast({ title: tip, icon: 'success' });
      setShowWithdraw(false);
      setWdAmount('');
      loadAll();
    } catch (e) {
      console.warn('[wallet] 提现失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // ============================================
  // 加载中
  // ============================================
  if (state === 'loading') {
    return (
      <View className={styles.page}>
        <View className={styles.empty}>
          <View className={styles.emptyIcon}>💰</View>
          <View className={styles.emptyText}>加载中...</View>
        </View>
      </View>
    );
  }

  // ============================================
  // 未开通
  // ============================================
  if (state === 'not-open') {
    return (
      <View className={styles.page}>
        <View className={styles.openCard}>
          <View className={styles.openIcon}>💰</View>
          <View className={styles.openTitle}>开通竹香钱包</View>
          <View className={styles.openDesc}>
            存入余额享活期营销补贴(年化 3%),消费自动返利 1%
          </View>
          <View className={styles.openRule}>开通条件: 会员等级 ≥ L2(成长值 ≥ 500)</View>
          <View className={styles.openBtn} onClick={handleOpen}>
            {opening ? '开通中...' : '立即开通'}
          </View>
        </View>
      </View>
    );
  }

  // ============================================
  // 钱包主页
  // ============================================
  return (
    <View className={styles.page}>
      {/* 余额卡片 */}
      <View className={styles.heroCard}>
        <View className={styles.heroTop}>
          <View>
            <View className={styles.heroLabel}>总资产(元)</View>
            <View className={styles.heroAmount}>{(info?.totalAssets ?? 0).toFixed(2)}</View>
          </View>
          <View className={styles.heroStatus}>{info?.statusName}</View>
        </View>
        <View className={styles.heroStats}>
          <View className={styles.heroStatItem}>
            <View className={styles.heroStatValue}>{(info?.currentBalance ?? 0).toFixed(2)}</View>
            <View className={styles.heroStatLabel}>活期余额</View>
          </View>
          <View className={styles.heroStatDivider} />
          <View className={styles.heroStatItem}>
            <View className={styles.heroStatValue}>{(info?.regularTotal ?? 0).toFixed(2)}</View>
            <View className={styles.heroStatLabel}>定期</View>
          </View>
          <View className={styles.heroStatDivider} />
          <View className={styles.heroStatItem}>
            <View className={styles.heroStatValue}>{(info?.pendingInterest ?? 0).toFixed(2)}</View>
            <View className={styles.heroStatLabel}>待结补贴</View>
          </View>
        </View>
        <View className={styles.heroActions}>
          <View className={styles.heroBtn} onClick={() => setShowDeposit(true)}>充值</View>
          <View className={styles.heroBtnGhost} onClick={() => setShowWithdraw(true)}>提现</View>
        </View>
      </View>

      {/* 收益概览 */}
      <View className={styles.section}>
        <View className={styles.sectionTitle}>累计收益</View>
        <View className={styles.statsCard}>
          <View className={styles.statsItem}>
            <View className={styles.statsValue}>{(info?.totalInterest ?? 0).toFixed(2)}</View>
            <View className={styles.statsLabel}>营销补贴</View>
          </View>
          <View className={styles.statsItem}>
            <View className={styles.statsValue}>{(info?.totalRebate ?? 0).toFixed(2)}</View>
            <View className={styles.statsLabel}>消费返利</View>
          </View>
          <View className={styles.statsItem}>
            <View className={styles.statsValue}>{(info?.totalDeposit ?? 0).toFixed(2)}</View>
            <View className={styles.statsLabel}>累计充值</View>
          </View>
          <View className={styles.statsItem}>
            <View className={styles.statsValue}>{(info?.totalWithdraw ?? 0).toFixed(2)}</View>
            <View className={styles.statsLabel}>累计提现</View>
          </View>
        </View>
      </View>

      {/* 交易流水 */}
      <View className={styles.section}>
        <View className={styles.sectionTitle}>交易流水</View>
        <View className={styles.txTabs}>
          {TX_TABS.map(tab => (
            <View
              key={tab.key}
              className={`${styles.txTab} ${txFilter === tab.key ? styles.txTabActive : ''}`}
              onClick={() => handleTab(tab.key)}
            >
              {tab.label}
            </View>
          ))}
        </View>
        {filteredTxs.length === 0 ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>🧾</View>
            <View className={styles.emptyText}>暂无交易记录</View>
          </View>
        ) : (
          <View className={styles.txList}>
            {filteredTxs.map(tx => {
              const isIn = tx.direction === 'IN';
              return (
                <View key={tx.txNo} className={styles.txItem}>
                  <View className={`${styles.txIcon} ${isIn ? styles.txIn : styles.txOut}`}>
                    {isIn ? '↓' : '↑'}
                  </View>
                  <View className={styles.txInfo}>
                    <View className={styles.txName}>
                      {TX_TYPE_NAME[tx.type] || tx.type} · {tx.payChannel || '—'}
                    </View>
                    <View className={styles.txDesc}>{tx.description}</View>
                    <View className={styles.txTime}>{formatTime(tx.createdAt)}</View>
                  </View>
                  <View className={styles.txRight}>
                    <View className={`${styles.txAmount} ${isIn ? styles.txAmountIn : styles.txAmountOut}`}>
                      {isIn ? '+' : '-'}¥{tx.amount.toFixed(2)}
                    </View>
                    <View className={styles.txBalance}>余额 ¥{tx.balanceAfter.toFixed(2)}</View>
                  </View>
                </View>
              );
            })}
          </View>
        )}
      </View>

      {/* 规则说明 */}
      <View className={styles.note}>
        活期补贴年化 3% 按月入账 · 消费返利 1%(单笔上限 ¥100) · 提现 ≥ ¥5000 需人工审核
      </View>

      {/* 充值弹层 */}
      {showDeposit ? (
        <View className={styles.mask} onClick={() => setShowDeposit(false)}>
          <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
            <View className={styles.sheetTitle}>钱包充值</View>
            <View className={styles.sheetDesc}>充值进入活期余额,可消费/可提现,享年化 3% 补贴</View>
            <View className={styles.quickRow}>
              {QUICK_AMOUNTS.map(a => (
                <View
                  key={a}
                  className={`${styles.quickItem} ${Number(depAmount) === a ? styles.quickItemActive : ''}`}
                  onClick={() => setDepAmount(String(a))}
                >
                  ¥{a}
                </View>
              ))}
            </View>
            <View className={styles.inputRow}>
              <Text className={styles.inputPrefix}>¥</Text>
              <Input
                className={styles.amountInput}
                type='digit'
                value={depAmount}
                placeholder='最低 100 元'
                onInput={(e) => setDepAmount(e.detail.value)}
              />
            </View>
            <View className={styles.sheetBtn} onClick={handleDeposit}>
              {submitting ? '充值中...' : '确认充值'}
            </View>
          </View>
        </View>
      ) : null}

      {/* 提现弹层 */}
      {showWithdraw ? (
        <View className={styles.mask} onClick={() => setShowWithdraw(false)}>
          <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
            <View className={styles.sheetTitle}>提现</View>
            <View className={styles.sheetDesc}>
              可提余额 ¥{(info?.currentBalance ?? 0).toFixed(2)} · ¥5000 以上需人工审核
            </View>
            <View className={styles.inputRow}>
              <Text className={styles.inputPrefix}>¥</Text>
              <Input
                className={styles.amountInput}
                type='digit'
                value={wdAmount}
                placeholder='输入提现金额'
                onInput={(e) => setWdAmount(e.detail.value)}
              />
            </View>
            <View className={styles.sheetBtn} onClick={handleWithdraw}>
              {submitting ? '提交中...' : '确认提现'}
            </View>
          </View>
        </View>
      ) : null}
    </View>
  );
};

export default WalletPage;
