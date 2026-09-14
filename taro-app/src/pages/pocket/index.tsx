/**
 * 顺手赚钱 · 张贴广告物料打卡获利
 * 数据来源: 后端 /api/pocket/*
 * 玩法: 张贴海报/车贴 → 每日打卡(AI评估¥2/次) → 满30天领存续奖(海报¥20/车贴¥30)
 */
import React, { useState, useEffect } from 'react';
import { View, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  PocketAPI,
  PocketSiteVO,
  PocketStatsVO,
  PocketScene,
  SCENE_NAME,
  SCENE_ICON,
} from '@/api/pocket';

// 场景筛选
const FILTERS = [
  { key: '', label: '全部' },
  { key: 'hotel', label: '酒店' },
  { key: 'supermarket', label: '超市' },
  { key: 'taxi_rear', label: '车贴' },
];

// 空统计兜底(与后端 DEFAULT_SETTINGS 一致)
const EMPTY_STATS: PocketStatsVO = {
  activeSiteCount: 0, totalSiteCount: 0,
  totalCheckinCount: 0, totalCheckinReward: 0,
  monthRewardReadyCount: 0, monthRewardReadyAmount: 0,
  checkinReward: 2, monthRewardPoster: 20, monthRewardSticker: 30,
  maxActiveSites: 5, durationDays: 30,
};

const PocketPage: React.FC = () => {
  const [stats, setStats] = useState<PocketStatsVO>(EMPTY_STATS);
  const [sites, setSites] = useState<PocketSiteVO[]>([]);
  const [filter, setFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  // 张贴弹层
  const [showReport, setShowReport] = useState(false);
  const [scene, setScene] = useState<PocketScene>('hotel');
  const [address, setAddress] = useState('');
  const [photoUrl, setPhotoUrl] = useState('');

  const loadData = async () => {
    try {
      const [s, list] = await Promise.all([
        PocketAPI.stats().catch(() => EMPTY_STATS),
        PocketAPI.mySites().catch((): PocketSiteVO[] => []),
      ]);
      setStats(s);
      setSites(list);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadData(); }, []);

  // 选择打卡照片 → 读 base64 → 上传服务器(落库可审计, 修复本地临时路径缺陷)
  const choosePhoto = () => {
    if (busy) return;
    Taro.chooseImage({
      count: 1,
      sizeType: ['compressed'],
      sourceType: ['album', 'camera'],
      success: res => {
        const path = res.tempFilePaths?.[0];
        if (!path) return;
        setBusy(true);
        Taro.showToast({ title: '照片上传中...', icon: 'none' });
        const fsm = Taro.getFileSystemManager();
        fsm.readFile({
          filePath: path,
          encoding: 'base64',
          success: async readRes => {
            try {
              const ext = (path.split('.').pop() || 'jpg').toLowerCase();
              const mime = ext === 'jpg' ? 'jpeg' : ext;
              const url = await PocketAPI.uploadPhoto(
                `data:image/${mime};base64,${readRes.data}`);
              setPhotoUrl(url);
              Taro.showToast({ title: '照片已上传', icon: 'success' });
            } catch (e) {
              console.warn('[pocket] 照片上传失败:', e);
              Taro.showToast({ title: '照片上传失败, 请重试', icon: 'none' });
            } finally {
              setBusy(false);
            }
          },
          fail: () => {
            setBusy(false);
            Taro.showToast({ title: '读取照片失败', icon: 'none' });
          },
        });
      },
    });
  };

  // 张贴打卡(创建点位+首打卡)
  const handleReport = async () => {
    if (busy) return;
    if (!address || address.trim().length < 5) {
      Taro.showToast({ title: '请填写至少5个字的张贴地址', icon: 'none' });
      return;
    }
    if (!photoUrl) {
      Taro.showToast({ title: '请先拍摄打卡照片', icon: 'none' });
      return;
    }
    setBusy(true);
    try {
      await PocketAPI.reportSite(scene, address.trim(), photoUrl);
      Taro.showToast({ title: `张贴成功 +¥${stats.checkinReward}`, icon: 'success' });
      setShowReport(false);
      setAddress('');
      setPhotoUrl('');
      setScene('hotel');
      loadData();
    } catch (e) {
      console.warn('[pocket] 张贴失败:', e);
    } finally {
      setBusy(false);
    }
  };

  // 每日打卡(拍照 → 上传 → 打卡)
  const handleCheckin = (site: PocketSiteVO) => {
    if (busy) return;
    setBusy(true);
    Taro.chooseImage({
      count: 1,
      sizeType: ['compressed'],
      sourceType: ['album', 'camera'],
      success: res => {
        const path = res.tempFilePaths?.[0];
        if (!path) { setBusy(false); return; }
        Taro.showToast({ title: '照片上传中...', icon: 'none' });
        const fsm = Taro.getFileSystemManager();
        fsm.readFile({
          filePath: path,
          encoding: 'base64',
          success: async readRes => {
            try {
              const ext = (path.split('.').pop() || 'jpg').toLowerCase();
              const mime = ext === 'jpg' ? 'jpeg' : ext;
              const url = await PocketAPI.uploadPhoto(
                `data:image/${mime};base64,${readRes.data}`);
              await PocketAPI.checkin(site.siteId, url);
              Taro.showToast({ title: `打卡成功 +¥${stats.checkinReward}`, icon: 'success' });
              loadData();
            } catch (err) {
              console.warn('[pocket] 打卡失败:', err);
            } finally {
              setBusy(false);
            }
          },
          fail: () => {
            setBusy(false);
            Taro.showToast({ title: '读取照片失败', icon: 'none' });
          },
        });
      },
      fail: () => setBusy(false),
    });
  };

  // 领取满月存续奖
  const handleClaimMonth = async (site: PocketSiteVO) => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await PocketAPI.claimMonthReward(site.siteId);
      Taro.showToast({ title: `存续奖 +¥${res.amount}`, icon: 'success' });
      loadData();
    } catch (e) {
      console.warn('[pocket] 领取存续奖失败:', e);
    } finally {
      setBusy(false);
    }
  };

  // 撤销张贴
  const handleRemove = (site: PocketSiteVO) => {
    Taro.showModal({
      title: '撤销张贴',
      content: `「${site.address}」撤销后未领的存续奖将放弃,确认撤销?`,
      success: async r => {
        if (!r.confirm) return;
        try {
          await PocketAPI.removeSite(site.siteId);
          Taro.showToast({ title: '已撤销', icon: 'success' });
          loadData();
        } catch (e) {
          console.warn('[pocket] 撤销失败:', e);
        }
      },
    });
  };

  const visibleSites = filter
    ? sites.filter(s => s.scene === filter)
    : sites;

  if (loading) {
    return (
      <View className={styles.page}>
        <NavBar title="口袋分红" />
        <View className={styles.loading}>加载中...</View>
      </View>
    );
  }

  return (
    <View className={styles.page}>
        <NavBar title="口袋分红" />
      {/* 收益概览 */}
      <View className={styles.heroCard}>
        <View className={styles.heroTop}>
          <View>
            <View className={styles.heroLabel}>累计打卡奖励(购物金)</View>
            <View className={styles.heroAmount}>¥{stats.totalCheckinReward.toFixed(2)}</View>
          </View>
          <View className={styles.heroBadge}>
            在贴 {stats.activeSiteCount}/{stats.maxActiveSites}
          </View>
        </View>
        <View className={styles.heroStats}>
          <View className={styles.heroStatItem}>
            <View className={styles.heroStatValue}>{stats.totalCheckinCount}</View>
            <View className={styles.heroStatLabel}>累计打卡</View>
          </View>
          <View className={styles.heroStatDivider} />
          <View className={styles.heroStatItem}>
            <View className={styles.heroStatValue}>{stats.totalSiteCount}</View>
            <View className={styles.heroStatLabel}>累计点位</View>
          </View>
          <View className={styles.heroStatDivider} />
          <View className={styles.heroStatItem}>
            <View className={styles.heroStatValue}>{stats.monthRewardReadyCount}</View>
            <View className={styles.heroStatLabel}>可领存续奖</View>
          </View>
        </View>
      </View>

      {/* 张贴打卡入口 */}
      <View className={styles.reportBtn} onClick={() => setShowReport(true)}>
        📌 张贴打卡 · 每次赚 ¥{stats.checkinReward}
      </View>

      {/* 场景筛选 */}
      <View className={styles.filterRow}>
        {FILTERS.map(f => (
          <View
            key={f.key}
            className={`${styles.filterItem} ${filter === f.key ? styles.filterActive : ''}`}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </View>
        ))}
      </View>

      {/* 点位列表 */}
      <View className={styles.section}>
        <View className={styles.sectionTitle}>我的张贴点位</View>
        {visibleSites.length === 0 && (
          <View className={styles.empty}>
            暂无张贴点位{'\n'}点击上方「张贴打卡」开始赚钱
          </View>
        )}
        {visibleSites.map(s => (
          <View key={s.siteId} className={styles.siteCard}>
            <View className={styles.siteTop}>
              <View className={styles.siteIcon}>{SCENE_ICON[s.scene] || '📍'}</View>
              <View className={styles.siteInfo}>
                <View className={styles.siteAddress}>{s.address}</View>
                <View className={styles.siteMeta}>
                  {SCENE_NAME[s.scene] || s.scene} ·
                  {s.posterType === 'sticker' ? '车贴' : '海报'} ·
                  打卡 {s.checkinCount} 次 · 连续 {s.consecutiveDays} 天
                </View>
              </View>
              {s.status !== 'active' && (
                <View className={styles.siteStatusOff}>
                  {s.status === 'removed' ? '已撤销' : '已作废'}
                </View>
              )}
            </View>
            {/* 在贴进度条 */}
            <View className={styles.progressRow}>
              <View className={styles.progressBar}>
                <View
                  className={styles.progressFill}
                  style={{ width: `${Math.min((s.activeDays / s.durationDays) * 100, 100)}%` }}
                />
              </View>
              <View className={styles.progressText}>
                在贴 {s.activeDays}/{s.durationDays} 天
              </View>
            </View>
            <View className={styles.siteActions}>
              {s.status === 'active' && (
                <View className={styles.checkinBtn} onClick={() => handleCheckin(s)}>
                  每日打卡 +¥{stats.checkinReward}
                </View>
              )}
              {s.monthRewardReady && (
                <View className={styles.monthBtn} onClick={() => handleClaimMonth(s)}>
                  领存续奖 ¥{s.posterType === 'sticker' ? stats.monthRewardSticker : stats.monthRewardPoster}
                </View>
              )}
              {s.monthRewardClaimed && (
                <View className={styles.claimedTag}>存续奖已领</View>
              )}
              {s.status === 'active' && !s.monthRewardClaimed && (
                <View className={styles.removeBtn} onClick={() => handleRemove(s)}>撤销</View>
              )}
            </View>
          </View>
        ))}
      </View>

      {/* 玩法说明 */}
      <View className={styles.section}>
        <View className={styles.sectionTitle}>玩法说明</View>
        <View className={styles.rulesCard}>
          <View className={styles.ruleItem}>1. 在酒店/超市等显眼位置张贴海报,出租车后窗贴车贴</View>
          <View className={styles.ruleItem}>2. 每日拍照打卡, AI 评估通过每次得 ¥{stats.checkinReward}(购物金)</View>
          <View className={styles.ruleItem}>
            3. 海报在贴满 {stats.durationDays} 天领 ¥{stats.monthRewardPoster},车贴满 {stats.durationDays} 天领 ¥{stats.monthRewardSticker}
          </View>
          <View className={styles.ruleItem}>4. 物料印你的推广码,新人扫码注册奖励同「扫码赚钱」</View>
          <View className={styles.ruleItem}>5. 每人同时在贴点位上限 {stats.maxActiveSites} 个,奖励仅可购买本站商品</View>
        </View>
      </View>

      {/* 张贴打卡弹层 */}
      {showReport && (
        <View className={styles.mask} onClick={() => setShowReport(false)}>
          <View className={styles.sheet} onClick={e => e.stopPropagation()}>
            <View className={styles.sheetTitle}>张贴打卡</View>
            <View className={styles.sheetLabel}>选择张贴场景</View>
            <View className={styles.sceneRow}>
              {(['hotel', 'supermarket', 'taxi_rear', 'restaurant', 'community'] as PocketScene[]).map(k => (
                <View
                  key={k}
                  className={`${styles.sceneItem} ${scene === k ? styles.sceneActive : ''}`}
                  onClick={() => setScene(k)}
                >
                  {SCENE_ICON[k]} {SCENE_NAME[k]}
                </View>
              ))}
            </View>
            <View className={styles.sheetLabel}>张贴地址</View>
            <Input
              className={styles.input}
              placeholder="如: XX市XX路XX酒店大堂"
              value={address}
              onInput={e => setAddress(e.detail.value)}
              maxlength={60}
            />
            <View className={styles.sheetLabel}>打卡照片</View>
            <View className={styles.photoBtn} onClick={choosePhoto}>
              {photoUrl ? '✅ 已拍摄,点击重拍' : '📷 拍摄打卡照片'}
            </View>
            <View className={styles.submitBtn} onClick={handleReport}>
              {busy ? '提交中...' : `张贴打卡 +¥${stats.checkinReward}`}
            </View>
          </View>
        </View>
      )}
    </View>
  );
};

export default PocketPage;
