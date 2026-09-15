/**
 * 顺手赚钱管理工作台(admin) · 对接 /api/pocket/admin/*
 * 两页签: 点位管理(列表/指纹审计/作废) · 参数配置(奖励/防刷阈值)
 * 口径: 打卡图片本体不上传——photoUrl 为 SHA-256 指纹(sha256:hex64)
 */
import React, { useState, useEffect } from 'react';
import { View, Text, ScrollView, Input, Switch } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  PocketAdminAPI, PocketAdminSiteVO, PocketAdminSettingsVO,
} from '@/api/pocket';
import { SCENE_NAME, SCENE_ICON } from '@/api/pocket';

type Tab = 'sites' | 'settings';

const TABS: { key: Tab; label: string }[] = [
  { key: 'sites', label: '点位管理' },
  { key: 'settings', label: '参数配置' },
];

// 状态筛选(换行平铺)
const STATUS_FILTERS = [
  { key: '', label: '全部' },
  { key: 'active', label: '在贴' },
  { key: 'removed', label: '已撤销' },
  { key: 'invalid', label: '已作废' },
];

/** 点位状态显示名 */
const SITE_STATUS: Record<string, { label: string; cls: string }> = {
  active: { label: '在贴', cls: 'pillActive' },
  removed: { label: '已撤销', cls: 'pillRemoved' },
  invalid: { label: '已作废', cls: 'pillInvalid' },
};

const statusName = (s: string): string => SITE_STATUS[s]?.label || s;

const EMPTY_SETTINGS: PocketAdminSettingsVO = {
  enabled: true,
  checkinReward: 2,
  monthRewardPoster: 20,
  monthRewardSticker: 30,
  maxActiveSites: 5,
  aiScoreThreshold: 60,
  durationDays: 30,
  minAddressLen: 5,
  updatedAt: '',
};

const PocketAdminPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('sites');

  // 点位管理
  const [sites, setSites] = useState<PocketAdminSiteVO[]>([]);
  const [statusFilter, setStatusFilter] = useState('');
  const [sitesLoading, setSitesLoading] = useState(true);

  // 参数配置
  const [settings, setSettings] = useState<PocketAdminSettingsVO>(EMPTY_SETTINGS);
  const [saving, setSaving] = useState(false);

  const loadSites = async (status = statusFilter) => {
    setSitesLoading(true);
    try {
      const list = await PocketAdminAPI.listSites(
        status ? { status } : {},
      ).catch(() => [] as PocketAdminSiteVO[]);
      setSites(list);
    } finally {
      setSitesLoading(false);
    }
  };

  const loadSettings = async () => {
    try {
      const s = await PocketAdminAPI.getSettings().catch(() => EMPTY_SETTINGS);
      setSettings(s);
    } catch (_) { /* 403 时保持默认 */ }
  };

  useEffect(() => {
    loadSites();
    loadSettings();
  }, []);

  const switchTab = (key: Tab) => {
    setTab(key);
    if (key === 'sites') loadSites();
    else loadSettings();
  };

  /** 状态筛选切换 */
  const applyStatusFilter = (key: string) => {
    setStatusFilter(key);
    loadSites(key);
  };

  /** 指纹复制(管理端留档——图片本体不上传, photoUrl 为 SHA-256 指纹) */
  const copyHash = (hash: string) => {
    if (!hash) return;
    Taro.setClipboardData({
      data: hash,
      success: () => Taro.showToast({ title: '指纹已复制', icon: 'none' }),
    });
  };

  /** 作废点位(确认弹层+理由) */
  const invalidateSite = (site: PocketAdminSiteVO) => {
    Taro.showModal({
      title: `作废点位 #${site.siteId}`,
      content: `「${site.address}」作废后会员无法再打卡, 确认作废?`,
      editable: true,
      placeholderText: '作废原因(如: 照片造假/点位违规)',
      success: async r => {
        if (!r.confirm) return;
        try {
          await PocketAdminAPI.invalidateSite(
            site.siteId, String(r.content || '').trim() || '管理端作废');
          Taro.showToast({ title: '已作废', icon: 'success' });
          loadSites();
        } catch (e: any) {
          Taro.showToast({
            title: String(e?.message || e || '作废失败').slice(0, 30),
            icon: 'none',
          });
        }
      },
    });
  };

  /** 保存参数(仅提交变更字段) */
  const saveSettings = async () => {
    if (saving) return;
    setSaving(true);
    try {
      const s = await PocketAdminAPI.updateSettings({
        enabled: settings.enabled,
        checkinReward: Number(settings.checkinReward),
        monthRewardPoster: Number(settings.monthRewardPoster),
        monthRewardSticker: Number(settings.monthRewardSticker),
        maxActiveSites: Number(settings.maxActiveSites),
        aiScoreThreshold: Number(settings.aiScoreThreshold),
        durationDays: Number(settings.durationDays),
        minAddressLen: Number(settings.minAddressLen),
      });
      setSettings(s);
      Taro.showToast({ title: '参数已保存', icon: 'success' });
    } catch (e: any) {
      Taro.showToast({
        title: String(e?.message || e || '保存失败').slice(0, 30),
        icon: 'none',
      });
    } finally {
      setSaving(false);
    }
  };

  const numField = (key: keyof PocketAdminSettingsVO, v: string) => {
    setSettings(prev => ({ ...prev, [key]: v === '' ? '' : Number(v) }));
  };

  return (
    <View className={styles.page}>
      <NavBar title="顺手赚钱管理" />

      {/* 页签(换行平铺) */}
      <View className={styles.tabBar}>
        {TABS.map(t => (
          <View
            key={t.key}
            className={`${styles.tab} ${tab === t.key ? styles.tabActive : ''}`}
            onClick={() => switchTab(t.key)}
          >
            {t.label}
          </View>
        ))}
      </View>

      <ScrollView scrollY className={styles.scrollView}>
        {tab === 'sites' && (
          <View className={styles.card}>
            <View className={styles.cardTitle}>
              点位管理({sites.length})
            </View>

            {/* 状态筛选 */}
            <View className={styles.filterRow}>
              {STATUS_FILTERS.map(f => (
                <View
                  key={f.key}
                  className={`${styles.filterItem} ${statusFilter === f.key ? styles.filterActive : ''}`}
                  onClick={() => applyStatusFilter(f.key)}
                >
                  {f.label}
                </View>
              ))}
            </View>

            {sitesLoading && <View className={styles.empty}>加载中...</View>}
            {!sitesLoading && sites.length === 0 && (
              <View className={styles.empty}>暂无点位</View>
            )}

            {sites.map(s => (
              <View key={s.siteId} className={styles.siteRow}>
                <View className={styles.siteHead}>
                  <View className={styles.siteTitle}>
                    {SCENE_ICON[s.scene] || '📍'} {s.address}
                  </View>
                  <View className={`${styles.statusPill} ${styles[SITE_STATUS[s.status]?.cls || 'pillActive']}`}>
                    {statusName(s.status)}
                  </View>
                </View>
                <View className={styles.siteMeta}>
                  <Text>#{s.siteId} · 会员 {s.memberId} · {SCENE_NAME[s.scene] || s.scene} · {s.posterType === 'sticker' ? '车贴' : '海报'}</Text>
                  <Text>打卡 {s.checkinCount} 次 · 连续 {s.consecutiveDays} 天 · AI 评分 {s.aiScoreLatest}{s.monthRewardClaimed ? ' · 存续奖已领' : ''}</Text>
                  <Text>张贴 {s.postedAt.slice(0, 16).replace('T', ' ')} · 最近打卡 {s.lastCheckinAt.slice(0, 16).replace('T', ' ')}</Text>
                </View>
                <View className={styles.siteFoot}>
                  {s.photoUrl ? (
                    <View
                      className={`${styles.hashBadge} ${s.photoUrl.startsWith('sha256:') ? '' : styles.hashLegacy}`}
                      onClick={() => copyHash(s.photoUrl)}
                    >
                      🔒 {s.photoUrl.startsWith('sha256:')
                        ? `${s.photoUrl.slice(0, 21)}…${s.photoUrl.slice(-8)}`
                        : '历史凭证'}
                    </View>
                  ) : (
                    <Text className={styles.noPhoto}>无打卡凭证</Text>
                  )}
                  {s.status === 'active' && (
                    <View className={styles.miniBtnDanger} onClick={() => invalidateSite(s)}>
                      作废
                    </View>
                  )}
                </View>
              </View>
            ))}
          </View>
        )}

        {tab === 'settings' && (
          <View className={styles.card}>
            <View className={styles.cardTitle}>参数配置</View>

            {/* 总开关 */}
            <View className={styles.switchRow}>
              <View>
                <View className={styles.switchLabel}>模块总开关</View>
                <View className={styles.switchDesc}>关闭后禁止新张贴与打卡</View>
              </View>
              <Switch
                checked={settings.enabled}
                color="#355c44"
                onChange={e => setSettings(prev => ({ ...prev, enabled: e.detail.value }))}
              />
            </View>

            <View className={styles.formRow}>
              <Text className={styles.formLabel}>每次打卡奖励</Text>
              <Input
                className={styles.formInput}
                type="digit"
                value={String(settings.checkinReward)}
                onInput={e => numField('checkinReward', e.detail.value)}
              />
              <Text className={styles.formUnit}>元</Text>
            </View>
            <View className={styles.formRow}>
              <Text className={styles.formLabel}>海报存续奖</Text>
              <Input
                className={styles.formInput}
                type="digit"
                value={String(settings.monthRewardPoster)}
                onInput={e => numField('monthRewardPoster', e.detail.value)}
              />
              <Text className={styles.formUnit}>元</Text>
            </View>
            <View className={styles.formRow}>
              <Text className={styles.formLabel}>车贴存续奖</Text>
              <Input
                className={styles.formInput}
                type="digit"
                value={String(settings.monthRewardSticker)}
                onInput={e => numField('monthRewardSticker', e.detail.value)}
              />
              <Text className={styles.formUnit}>元</Text>
            </View>
            <View className={styles.formRow}>
              <Text className={styles.formLabel}>在贴点位上限</Text>
              <Input
                className={styles.formInput}
                type="number"
                value={String(settings.maxActiveSites)}
                onInput={e => numField('maxActiveSites', e.detail.value)}
              />
              <Text className={styles.formUnit}>个</Text>
            </View>
            <View className={styles.formRow}>
              <Text className={styles.formLabel}>AI 评分阈值</Text>
              <Input
                className={styles.formInput}
                type="number"
                value={String(settings.aiScoreThreshold)}
                onInput={e => numField('aiScoreThreshold', e.detail.value)}
              />
              <Text className={styles.formUnit}>分</Text>
            </View>
            <View className={styles.formRow}>
              <Text className={styles.formLabel}>存续奖天数</Text>
              <Input
                className={styles.formInput}
                type="number"
                value={String(settings.durationDays)}
                onInput={e => numField('durationDays', e.detail.value)}
              />
              <Text className={styles.formUnit}>天</Text>
            </View>
            <View className={styles.formRow}>
              <Text className={styles.formLabel}>地址最短字数</Text>
              <Input
                className={styles.formInput}
                type="number"
                value={String(settings.minAddressLen)}
                onInput={e => numField('minAddressLen', e.detail.value)}
              />
              <Text className={styles.formUnit}>字</Text>
            </View>

            {settings.updatedAt && (
              <View className={styles.updatedAt}>
                上次修改: {settings.updatedAt.slice(0, 19).replace('T', ' ')}
              </View>
            )}

            <View className={styles.btnPrimary} onClick={saveSettings}>
              {saving ? '保存中...' : '保存参数(即时生效)'}
            </View>
            <View className={styles.formNote}>
              奖励资金入会员钱包奖励余额, 仅可购物不可提现
            </View>
          </View>
        )}

        <View className={styles.bottomSpacer} />
      </ScrollView>
    </View>
  );
};

export default PocketAdminPage;
