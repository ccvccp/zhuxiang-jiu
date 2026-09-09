/**
 * 钱包 · 余额/充值/提现/定期存单/奖品/流水/收益预估
 * 数据来源: 后端 /api/wallet/*
 * 开通条件: 会员等级 ≥ L2(成长值 ≥ 500), 未开通时引导开通
 */
import React, { useState, useEffect, useMemo } from 'react';
import { View, Text, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  WalletAPI, WalletInfoVO, WalletTxVO, WalletDepositVO, WalletRewardVO,
  TX_TYPE_NAME, DEPOSIT_STATUS_NAME, REWARD_STATUS_NAME, DEPOSIT_TIERS,
} from '@/api/wallet';

// 流水类型筛选
const TX_TABS = [
  { key: '', label: '全部' },
  { key: 'deposit', label: '充值' },
  { key: 'withdraw', label: '提现' },
  { key: 'consume', label: '消费' },
  { key: 'interest', label: '收益' },
  { key: 'rebate', label: '返利' },
  { key: 'transfer_regular', label: '定期' },
];

// 快捷充值金额
const QUICK_AMOUNTS = [100, 500, 1000, 5000];

type PageState = 'loading' | 'not-open' | 'ready';

const formatTime = (t: string): string => (t ? t.slice(0, 19).replace('T', ' ') : '');
const formatDate = (t: string): string => (t ? t.slice(0, 10) : '');

const WalletPage: React.FC = () => {
  const [state, setState] = useState<PageState>('loading');
  const [info, setInfo] = useState<WalletInfoVO | null>(null);
  const [txs, setTxs] = useState<WalletTxVO[]>([]);
  const [txFilter, setTxFilter] = useState('');
  const [opening, setOpening] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // 定期与奖品
  const [deposits, setDeposits] = useState<WalletDepositVO[]>([]);
  const [rewards, setRewards] = useState<WalletRewardVO[]>([]);

  // 充值弹层状态
  const [showDeposit, setShowDeposit] = useState(false);
  const [depAmount, setDepAmount] = useState('');
  // 提现弹层状态
  const [showWithdraw, setShowWithdraw] = useState(false);
  const [wdAmount, setWdAmount] = useState('');
  // 转定期弹层状态
  const [showRegular, setShowRegular] = useState(false);
  const [regAmount, setRegAmount] = useState('');
  const [regPeriod, setRegPeriod] = useState(3);

  const loadAll = async () => {
    try {
      const [w, t, ds, rs] = await Promise.all([
        WalletAPI.info(),
        WalletAPI.transactions().catch((): WalletTxVO[] => []),
        WalletAPI.deposits().catch((): WalletDepositVO[] => []),
        WalletAPI.rewards().catch((): WalletRewardVO[] => []),
      ]);
      setInfo(w);
      setTxs(t);
      setDeposits(ds);
      setRewards(rs);
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

  // 转定期
  const handleTransferRegular = async () => {
    if (submitting) return;
    const amount = Number(regAmount);
    const tier = DEPOSIT_TIERS.find(t => t.period === regPeriod);
    if (!amount || amount <= 0) {
      Taro.showToast({ title: '请输入存入金额', icon: 'none' });
      return;
    }
    if (tier && amount < tier.min) {
      Taro.showToast({ title: `${regPeriod} 月档最低 ¥${tier.min}`, icon: 'none' });
      return;
    }
    if (info && amount > info.currentBalance) {
      Taro.showToast({ title: '超出活期余额', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      await WalletAPI.transferRegular(amount, regPeriod);
      Taro.showToast({ title: '转定期成功', icon: 'success' });
      setShowRegular(false);
      setRegAmount('');
      loadAll();
    } catch (e) {
      console.warn('[wallet] 转定期失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 定期到期取出
  const handleSettle = async (d: WalletDepositVO) => {
    if (submitting) return;
    const res = await Taro.showModal({
      title: '到期取出',
      content: `本金 ¥${d.amount.toFixed(2)} + 收益 ¥${d.expectedInterest.toFixed(2)} 将入活期, 奖品转为可领取。确认取出?`,
    });
    if (!res.confirm) return;
    setSubmitting(true);
    try {
      await WalletAPI.settleDeposit(d.depositNo);
      Taro.showToast({ title: '已取出', icon: 'success' });
      loadAll();
    } catch (e) {
      console.warn('[wallet] 取出失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 定期提前取出
  const handleEarlySettle = async (d: WalletDepositVO) => {
    if (submitting) return;
    const res = await Taro.showModal({
      title: '提前取出确认',
      content: `将收取 1% 手续费(¥${(d.amount * 0.01).toFixed(2)}), 并损失全部余额收益与奖品。确认提前取出?`,
    });
    if (!res.confirm) return;
    setSubmitting(true);
    try {
      await WalletAPI.earlySettleDeposit(d.depositNo);
      Taro.showToast({ title: '已提前取出', icon: 'success' });
      loadAll();
    } catch (e) {
      console.warn('[wallet] 提前取出失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 领取奖品
  const handleClaim = async (r: WalletRewardVO) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await WalletAPI.claimReward(r.rewardNo);
      Taro.showToast({ title: '已领取, 等待发货', icon: 'success' });
      loadAll();
    } catch (e) {
      console.warn('[wallet] 领取失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 奖品签收
  const handleSign = async (r: WalletRewardVO) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await WalletAPI.signReward(r.rewardNo);
      Taro.showToast({ title: '已签收', icon: 'success' });
      loadAll();
    } catch (e) {
      console.warn('[wallet] 签收失败:', e);
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
        <NavBar title="钱包" />
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
        <NavBar title="钱包" />
        <View className={styles.openCard}>
          <View className={styles.openIcon}>💰</View>
          <View className={styles.openTitle}>开通竹香钱包</View>
          <View className={styles.openDesc}>
            存入余额享年化 3% 活期收益,消费自动返利 1%
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
        <NavBar title="钱包" />
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
            <View className={styles.heroStatLabel}>待结收益</View>
          </View>
        </View>
        <View className={styles.heroActions}>
          <View className={styles.heroBtn} onClick={() => setShowDeposit(true)}>充值</View>
          <View className={styles.heroBtnGhost} onClick={() => setShowWithdraw(true)}>提现</View>
          <View className={styles.heroBtnGhost} onClick={() => setShowRegular(true)}>转定期</View>
        </View>
      </View>

      {/* 收益概览 */}
      <View className={styles.section}>
        <View className={styles.sectionTitle}>累计收益</View>
        <View className={styles.statsCard}>
          <View className={styles.statsItem}>
            <View className={styles.statsValue}>{(info?.totalInterest ?? 0).toFixed(2)}</View>
            <View className={styles.statsLabel}>余额收益</View>
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

      {/* 定期存单 */}
      <View className={styles.section}>
        <View className={styles.sectionTitle}>
          定期存单
          {deposits.length > 0 && <Text className={styles.sectionCount}>{deposits.length} 笔</Text>}
        </View>
        {deposits.length === 0 ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>🏦</View>
            <View className={styles.emptyText}>暂无定期, 转定期享年化 3%-5%+奖品</View>
          </View>
        ) : (
          deposits.map(d => (
            <View key={d.depositNo} className={styles.depItem}>
              <View className={styles.depLeft}>
                <View className={styles.depTitle}>
                  {d.period} 个月定期 · 年化 {(d.annualRate * 100).toFixed(1)}%
                </View>
                <View className={styles.depMeta}>
                  ¥{d.amount.toFixed(2)} · {formatDate(d.startDate)} ~ {formatDate(d.endDate)}
                </View>
                <View className={styles.depMeta}>
                  预计收益 ¥{d.expectedInterest.toFixed(2)}
                  {d.rewardType ? ` · 奖品: ${d.rewardType}` : ''}
                </View>
              </View>
              <View className={styles.depRight}>
                <View className={`${styles.depBadge} ${d.matured || d.status === 'matured' ? styles.depBadgeMatured : ''}`}>
                  {d.matured ? '已到期' : DEPOSIT_STATUS_NAME[d.status] || d.status}
                </View>
                {d.status === 'active' && (d.matured ? (
                  <View className={styles.depAction} onClick={() => handleSettle(d)}>取出</View>
                ) : (
                  <View className={styles.depActionGhost} onClick={() => handleEarlySettle(d)}>提前取出</View>
                ))}
              </View>
            </View>
          ))
        )}
      </View>

      {/* 我的奖品 */}
      <View className={styles.section}>
        <View className={styles.sectionTitle}>
          我的奖品
          {rewards.length > 0 && <Text className={styles.sectionCount}>{rewards.length} 件</Text>}
        </View>
        {rewards.length === 0 ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>🎁</View>
            <View className={styles.emptyText}>暂无奖品, 定期到期可获赠品</View>
          </View>
        ) : (
          rewards.map(r => (
            <View key={r.rewardNo} className={styles.depItem}>
              <View className={styles.depLeft}>
                <View className={styles.depTitle}>{r.rewardType}</View>
                <View className={styles.depMeta}>
                  价值 ¥{r.rewardValue.toFixed(2)} · {formatDate(r.createdAt)}
                </View>
              </View>
              <View className={styles.depRight}>
                <View className={styles.depBadge}>{REWARD_STATUS_NAME[r.status] || r.status}</View>
                {r.status === 'claimable' && (
                  <View className={styles.depAction} onClick={() => handleClaim(r)}>领取</View>
                )}
                {r.status === 'shipped' && (
                  <View className={styles.depAction} onClick={() => handleSign(r)}>签收</View>
                )}
              </View>
            </View>
          ))
        )}
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
        活期收益年化 3% 按月入账 · 消费返利 1%(单笔上限 ¥100) · 提现 ≥ ¥5000 需人工审核
      </View>

      {/* 充值弹层 */}
      {showDeposit ? (
        <View className={styles.mask} onClick={() => setShowDeposit(false)}>
          <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
            <View className={styles.sheetTitle}>钱包充值</View>
            <View className={styles.sheetDesc}>充值进入活期余额,可消费/可提现,享年化 3% 收益</View>
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

      {/* 转定期弹层 */}
      {showRegular ? (
        <View className={styles.mask} onClick={() => setShowRegular(false)}>
          <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
            <View className={styles.sheetTitle}>活期转定期</View>
            <View className={styles.sheetDesc}>
              活期余额 ¥{(info?.currentBalance ?? 0).toFixed(2)} · 到期返本金+收益, 另赠奖品
            </View>
            {/* 存期档位 */}
            <View className={styles.quickRow}>
              {DEPOSIT_TIERS.map(t => (
                <View
                  key={t.period}
                  className={`${styles.quickItem} ${regPeriod === t.period ? styles.quickItemActive : ''}`}
                  onClick={() => setRegPeriod(t.period)}
                >
                  <View>{t.period} 个月</View>
                  <View className={styles.quickSub}>{t.rate} 年化</View>
                </View>
              ))}
            </View>
            <View className={styles.inputRow}>
              <Text className={styles.inputPrefix}>¥</Text>
              <Input
                className={styles.amountInput}
                type='digit'
                value={regAmount}
                placeholder={`最低 ${DEPOSIT_TIERS.find(t => t.period === regPeriod)?.min ?? 0} 元`}
                onInput={(e) => setRegAmount(e.detail.value)}
              />
            </View>
            <View className={styles.sheetBtn} onClick={handleTransferRegular}>
              {submitting ? '转入中...' : '确认转入'}
            </View>
          </View>
        </View>
      ) : null}
    </View>
  );
};

export default WalletPage;
