/**
 * 推广中心 · 推广码矩阵获利
 * 数据来源: 后端 /api/promotion/*
 */
import React, { useState, useEffect } from 'react';
import { View, Text } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import {
  PromoAPI,
  PromotionStatsVO,
  PromoCodeVO,
  TeamMemberVO,
  RewardVO,
} from '@/api/promotion';

// 渠道显示名
const CHANNEL_NAME: Record<string, string> = {
  wechat_miniprogram: '微信小程序',
  douyin: '抖音',
  kuaishou: '快手',
  xiaohongshu: '小红书',
  bilibili: 'B站',
  taobao: '淘宝',
  direct: '直接推荐',
};

// 空统计兜底
const EMPTY_STATS: PromotionStatsVO = {
  directCount: 0, qualifiedSubCount: 0,
  level1Threshold: 100, level1RewardAmount: 50,
  level2SubPromoterCount: 50, level2SubThreshold: 100,
  wineMinPrice: 200,
  rewardBalance: 0, wineQualifyAvailable: 0, walletRewardCycles: 0,
};

const PromotionPage: React.FC = () => {
  const [stats, setStats] = useState<PromotionStatsVO>(EMPTY_STATS);
  const [promoCode, setPromoCode] = useState<string>('');
  const [shareTip, setShareTip] = useState<string>('');
  const [team, setTeam] = useState<TeamMemberVO[]>([]);
  const [rewards, setRewards] = useState<RewardVO[]>([]);
  const [loading, setLoading] = useState(true);
  const [claiming, setClaiming] = useState(false);

  const loadData = async () => {
    try {
      const [s, codes, t, r] = await Promise.all([
        PromoAPI.stats().catch(() => EMPTY_STATS),
        PromoAPI.myCodes().catch((): PromoCodeVO[] => []),
        PromoAPI.myTeam().catch((): TeamMemberVO[] => []),
        PromoAPI.myRewards().catch((): RewardVO[] => []),
      ]);
      setStats(s);
      setTeam(t);
      setRewards(r);
      // 已有微信小程序渠道码则直接展示
      const wxCode = codes.find(c => c.channel === 'wechat_miniprogram');
      if (wxCode) setPromoCode(wxCode.code);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadData(); }, []);

  // 领取推广码
  const handleClaimCode = async () => {
    if (claiming) return;
    setClaiming(true);
    try {
      const res = await PromoAPI.claimCode('wechat_miniprogram');
      setPromoCode(res.code);
      setShareTip(res.shareTip);
      Taro.showToast({ title: res.reclaimed ? '已恢复原推广码' : '领取成功', icon: 'success' });
    } catch (e) {
      console.warn('[promotion] 领码失败:', e);
    } finally {
      setClaiming(false);
    }
  };

  // 复制推广码
  const handleCopyCode = () => {
    if (!promoCode) return;
    Taro.setClipboardData({
      data: promoCode,
      success: () => Taro.showToast({ title: '推广码已复制', icon: 'success' }),
    });
  };

  // 复制分享文案
  const handleCopyShare = () => {
    if (!shareTip) return;
    Taro.setClipboardData({
      data: shareTip,
      success: () => Taro.showToast({ title: '分享文案已复制', icon: 'success' }),
    });
  };

  // 一级奖励进度
  const l1Progress = Math.min(100, Math.round((stats.directCount / stats.level1Threshold) * 100));
  // 二级奖励进度(达标下线数/所需下线数)
  const l2Progress = Math.min(100, Math.round((stats.qualifiedSubCount / stats.level2SubPromoterCount) * 100));

  if (loading) {
    return (
      <View className={styles.page}>
        <View className={styles.empty}>
          <View className={styles.emptyIcon}>🤝</View>
          <View className={styles.emptyText}>加载中...</View>
        </View>
      </View>
    );
  }

  return (
    <View className={styles.page}>
      {/* 收益概览 */}
      <View className={styles.heroCard}>
        <View className={styles.heroTitle}>推广收益</View>
        <View className={styles.balanceRow}>
          <View className={styles.balance}>¥{stats.rewardBalance.toFixed(2)}</View>
          <View className={styles.balanceNote}>奖励余额(仅可购买本站产品,不可提现)</View>
        </View>
        <View className={styles.heroStats}>
          <View className={styles.heroStatItem}>
            <View className={styles.heroStatValue}>{stats.directCount}</View>
            <View className={styles.heroStatLabel}>直推人数</View>
          </View>
          <View className={styles.heroStatDivider} />
          <View className={styles.heroStatItem}>
            <View className={styles.heroStatValue}>{stats.qualifiedSubCount}</View>
            <View className={styles.heroStatLabel}>达标下线</View>
          </View>
          <View className={styles.heroStatDivider} />
          <View className={styles.heroStatItem}>
            <View className={styles.heroStatValue}>{stats.wineQualifyAvailable}</View>
            <View className={styles.heroStatLabel}>领酒资格</View>
          </View>
        </View>
      </View>

      {/* 推广码区块 */}
      <View className={styles.section}>
        <View className={styles.sectionTitle}>我的推广码</View>
        {promoCode ? (
          <View className={styles.codeCard}>
            <View className={styles.codeValue}>{promoCode}</View>
            <View className={styles.codeDesc}>好友注册时填入此码,即绑定为你下线</View>
            <View className={styles.codeActions}>
              <View className={styles.codeBtn} onClick={handleCopyCode}>复制推广码</View>
              {shareTip ? (
                <View className={styles.codeBtnGhost} onClick={handleCopyShare}>复制分享文案</View>
              ) : null}
            </View>
          </View>
        ) : (
          <View className={styles.claimCard}>
            <View className={styles.claimDesc}>
              领取专属推广码(ZXBJ 前缀),分享给好友注册绑定,即建团队赚奖励
            </View>
            <View className={styles.claimBtn} onClick={handleClaimCode}>
              {claiming ? '领取中...' : '领取推广码'}
            </View>
          </View>
        )}
      </View>

      {/* 奖励进度 */}
      <View className={styles.section}>
        <View className={styles.sectionTitle}>奖励进度</View>
        <View className={styles.progressCard}>
          <View className={styles.progressItem}>
            <View className={styles.progressHeader}>
              <Text className={styles.progressName}>一级奖励(钱包)</Text>
              <Text className={styles.progressValue}>
                {stats.directCount}/{stats.level1Threshold} 人
              </Text>
            </View>
            <View className={styles.progressBar}>
              <View className={styles.progressFill} style={{ width: `${l1Progress}%` }} />
            </View>
            <View className={styles.progressDesc}>
              直推满 {stats.level1Threshold} 人,每轮奖励 ¥{stats.level1RewardAmount}
            </View>
          </View>
          <View className={styles.progressItem}>
            <View className={styles.progressHeader}>
              <Text className={styles.progressName}>二级奖励(领酒资格)</Text>
              <Text className={styles.progressValue}>
                {stats.qualifiedSubCount}/{stats.level2SubPromoterCount} 人
              </Text>
            </View>
            <View className={styles.progressBar}>
              <View className={styles.progressFill} style={{ width: `${l2Progress}%` }} />
            </View>
            <View className={styles.progressDesc}>
              {stats.level2SubPromoterCount} 个下线各自推广满 {stats.level2SubThreshold} 人,可领 ¥{stats.wineMinPrice}+ 奖励酒
            </View>
          </View>
        </View>
      </View>

      {/* 我的团队 */}
      <View className={styles.section}>
        <View className={styles.sectionTitle}>我的团队({team.length})</View>
        {team.length === 0 ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>👥</View>
            <View className={styles.emptyText}>还没有下线,快去分享推广码吧</View>
          </View>
        ) : (
          <View className={styles.teamList}>
            {team.map(m => (
              <View key={m.inviteeMemberId} className={styles.teamItem}>
                <View className={styles.teamAvatar}>{(m.nickname || '友')[0]}</View>
                <View className={styles.teamInfo}>
                  <View className={styles.teamName}>{m.nickname}</View>
                  <View className={styles.teamMeta}>
                    {CHANNEL_NAME[m.channel] || m.channel} · 已推 {m.subCount} 人
                  </View>
                </View>
                <View className={styles.teamStatus}>
                  {m.subCount >= stats.level2SubThreshold ? '已达标' : '推广中'}
                </View>
              </View>
            ))}
          </View>
        )}
      </View>

      {/* 奖励记录 */}
      <View className={styles.section}>
        <View className={styles.sectionTitle}>奖励记录({rewards.length})</View>
        {rewards.length === 0 ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>🎁</View>
            <View className={styles.emptyText}>暂无奖励,达成条件自动发放</View>
          </View>
        ) : (
          <View className={styles.rewardList}>
            {rewards.map((r, idx) => (
              <View key={idx} className={styles.rewardItem}>
                <View className={styles.rewardIcon}>
                  {r.rewardType === 'wine_qualify' ? '🍶' : '💰'}
                </View>
                <View className={styles.rewardInfo}>
                  <View className={styles.rewardName}>
                    {r.rewardType === 'wine_qualify' ? '领酒资格' : '钱包奖励'}
                  </View>
                  <View className={styles.rewardMeta}>{r.detail}</View>
                </View>
                <View className={styles.rewardRight}>
                  <View className={styles.rewardAmount}>
                    {r.rewardType === 'wine_qualify' ? '×1' : `¥${r.amount}`}
                  </View>
                  <View className={styles.rewardStatus}>{r.status === 'issued' ? '已发放' : '已使用'}</View>
                </View>
              </View>
            ))}
          </View>
        )}
      </View>

      {/* 玩法说明 */}
      <View className={styles.section}>
        <View className={styles.sectionTitle}>玩法说明</View>
        <View className={styles.rulesCard}>
          <View className={styles.ruleItem}>1. 领取专属推广码,分享至微信/抖音等平台</View>
          <View className={styles.ruleItem}>2. 好友注册时绑定你的推广码,成为你的下线</View>
          <View className={styles.ruleItem}>
            3. 直推满 {stats.level1Threshold} 人:每轮获得 ¥{stats.level1RewardAmount} 钱包奖励(可购本站任意产品)
          </View>
          <View className={styles.ruleItem}>
            4. 下线中 {stats.level2SubPromoterCount} 人各自推广满 {stats.level2SubThreshold} 人:获得领酒资格,可领 ¥{stats.wineMinPrice}+ 竹奕酒
          </View>
          <View className={styles.ruleItem}>5. 奖励余额仅可购买本站产品,不可提现</View>
        </View>
      </View>
    </View>
  );
};

export default PromotionPage;
