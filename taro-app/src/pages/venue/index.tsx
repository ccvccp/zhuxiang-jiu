/**
 * 场馆合作联盟 · 酒店/酒吧/会所合作网络
 * 浏览合作场馆(公开) → 我要合作(申请入驻) → 我的合作商档案 + 场地管理
 * 数据来源: 后端 /api/venue/*
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input, Picker } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  VenueAPI, VenuePartnerVO, VenueVO,
  partnerTypeName, partnerStatusName,
} from '@/api/venue';
import { requireLogin } from '@/services/auth-service';

type Tab = 'browse' | 'mine';

const TABS: { key: Tab; label: string }[] = [
  { key: 'browse', label: '合作场馆' },
  { key: 'mine', label: '我的合作' },
];

// 类型筛选(全部 + 酒店/酒吧/会所)
const TYPE_TABS = ['', 'hotel', 'bar', 'club'];

// 申请弹层的类型选项
const TYPE_OPTIONS = [
  { type: 'hotel', label: '酒店' },
  { type: 'bar', label: '酒吧' },
  { type: 'club', label: '会所' },
];

const VenuePage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('browse');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  // 浏览
  const [typeFilter, setTypeFilter] = useState('');
  const [partners, setPartners] = useState<VenuePartnerVO[]>([]);
  const [venueMap, setVenueMap] = useState<Record<number, VenueVO[]>>({});
  const [expanded, setExpanded] = useState<number | null>(null);
  // 我的合作
  const [myPartner, setMyPartner] = useState<VenuePartnerVO | null>(null);
  const [myVenues, setMyVenues] = useState<VenueVO[]>([]);
  // 申请弹层
  const [showApply, setShowApply] = useState(false);
  const [apTypeIdx, setApTypeIdx] = useState(0);
  const [apName, setApName] = useState('');
  const [apCredit, setApCredit] = useState('');
  const [apPhone, setApPhone] = useState('');
  const [apAddress, setApAddress] = useState('');
  // 建场地弹层
  const [showVenue, setShowVenue] = useState(false);
  const [vnName, setVnName] = useState('');
  const [vnType, setVnType] = useState('');
  const [vnCapacity, setVnCapacity] = useState('');

  const loadBrowse = useCallback(async (type: string) => {
    try {
      const list = await VenueAPI.partners(type || undefined);
      setPartners(list);
      // 拉取各合作商的场地(公开)
      const map: Record<number, VenueVO[]> = {};
      await Promise.all(list.map(async (p) => {
        try {
          map[p.id] = await VenueAPI.venues(p.id);
        } catch (_) {
          map[p.id] = [];
        }
      }));
      setVenueMap(map);
    } catch (e) {
      console.warn('[venue] 合作商列表加载失败:', e);
      setPartners([]);
    }
  }, []);

  const loadMine = useCallback(async () => {
    try {
      const mine = await VenueAPI.myPartner();
      setMyPartner(mine);
      if (mine) {
        setMyVenues(await VenueAPI.venues(mine.id).catch(() => [] as VenueVO[]));
      } else {
        setMyVenues([]);
      }
    } catch (e) {
      console.warn('[venue] 我的信息加载失败:', e);
      setMyPartner(null);
    }
  }, []);

  useEffect(() => {
    (async () => {
      await Promise.all([loadBrowse(typeFilter), loadMine()]);
      setLoading(false);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 类型筛选
  const handleTypeFilter = async (t: string) => {
    setTypeFilter(t);
    setExpanded(null);
    await loadBrowse(t);
  };

  // 展开合作商看场地
  const handleExpand = (pid: number) => {
    setExpanded(prev => (prev === pid ? null : pid));
  };

  // 提交合作申请
  const handleApply = async () => {
    if (submitting) return;
    const type = TYPE_OPTIONS[apTypeIdx]?.type;
    if (!type) {
      Taro.showToast({ title: '请选择合作商类型', icon: 'none' });
      return;
    }
    if (!apName.trim()) {
      Taro.showToast({ title: '请输入合作商名称', icon: 'none' });
      return;
    }
    if (!apCredit.trim()) {
      Taro.showToast({ title: '请输入统一社会信用代码', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const p = await VenueAPI.applyPartner({
        partnerType: type,
        partnerName: apName.trim(),
        creditCode: apCredit.trim(),
        contactPhone: apPhone.trim(),
        contactAddress: apAddress.trim(),
      });
      Taro.showToast({
        title: `申请已提交(编号 ${p.id}), 等待平台审核`,
        icon: 'none',
        duration: 2500,
      });
      setShowApply(false);
      setTab('mine');
      await loadMine();
    } catch (e) {
      console.warn('[venue] 合作申请失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 提交创建场地
  const handleCreateVenue = async () => {
    if (submitting || !myPartner) return;
    if (!vnName.trim()) {
      Taro.showToast({ title: '请输入场地名称', icon: 'none' });
      return;
    }
    if (!vnType.trim()) {
      Taro.showToast({ title: '请输入场地类型', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      await VenueAPI.createVenue(myPartner.id, {
        venueName: vnName.trim(),
        venueType: vnType.trim(),
        capacity: Number(vnCapacity) || 0,
      });
      Taro.showToast({ title: '场地已创建', icon: 'success' });
      setShowVenue(false);
      setMyVenues(await VenueAPI.venues(myPartner.id).catch(() => [] as VenueVO[]));
    } catch (e) {
      console.warn('[venue] 场地创建失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  const openCreateVenue = () => {
    if (myPartner && myPartner.status !== 'active' && myPartner.status !== 'signed') {
      Taro.showToast({ title: '合作商审核通过后方可管理场地', icon: 'none' });
      return;
    }
    setVnName('');
    setVnType('');
    setVnCapacity('');
    setShowVenue(true);
  };

  return (
    <View className={styles.page}>
      <NavBar title="场馆合作联盟" />
      {/* 顶部 Tabs */}
      <View className={styles.tabBar}>
        {TABS.map(t => (
          <View
            key={t.key}
            className={`${styles.tabItem} ${tab === t.key ? styles.tabItemActive : ''}`}
            onClick={async () => {
              setTab(t.key);
              if (t.key === 'browse') await loadBrowse(typeFilter);
              else await loadMine();
            }}
          >
            {t.label}
          </View>
        ))}
      </View>

      <ScrollView scrollY className={styles.scrollView}>
        {loading ? (
          <View className={styles.empty}>加载中...</View>
        ) : tab === 'browse' ? (
          <>
            {/* 类型筛选 */}
            <ScrollView scrollX className={styles.typeBar}>
              {TYPE_TABS.map(t => (
                <View
                  key={t || 'all'}
                  className={`${styles.typeItem} ${typeFilter === t ? styles.typeItemActive : ''}`}
                  onClick={() => handleTypeFilter(t)}
                >
                  {t ? partnerTypeName(t) : '全部'}
                </View>
              ))}
            </ScrollView>

            {/* 合作商卡片 */}
            {partners.length === 0 ? (
              <View className={styles.empty}>
                <View className={styles.emptyIcon}>🏨</View>
                <View>暂无该类型合作商</View>
              </View>
            ) : (
              partners.map(p => {
                const venues = venueMap[p.id] || [];
                const open = expanded === p.id;
                return (
                  <View key={p.id} className={styles.partnerCard}>
                    <View className={styles.partnerTop} onClick={() => handleExpand(p.id)}>
                      <View className={styles.partnerLeft}>
                        <View className={styles.partnerName}>
                          <Text className={styles.typeBadge}>{partnerTypeName(p.partnerType)}</Text>
                          {p.partnerName}
                        </View>
                        <View className={styles.partnerMeta}>
                          {p.partnerLevel} 级合作 · 品鉴酒比例 {(p.tastingRate * 100).toFixed(0)}%
                          {p.contactAddress ? ` · ${p.contactAddress}` : ''}
                        </View>
                      </View>
                      <View className={styles.partnerArrow}>{open ? '▾' : '›'}</View>
                    </View>
                    {open && (
                      <View className={styles.venueList}>
                        {venues.length === 0 ? (
                          <View className={styles.venueEmpty}>暂无公开场地信息</View>
                        ) : (
                          venues.map(v => (
                            <View key={v.id} className={styles.venueRow}>
                              <View className={styles.venueLeft}>
                                <View className={styles.venueName}>{v.venueName}</View>
                                <View className={styles.venueMeta}>
                                  {v.venueType}{v.capacity > 0 ? ` · 可容纳 ${v.capacity} 人` : ''}
                                  {v.address ? ` · ${v.address}` : ''}
                                </View>
                              </View>
                            </View>
                          ))
                        )}
                      </View>
                    )}
                  </View>
                );
              })
            )}
          </>
        ) : (
          <View className={styles.card}>
            {myPartner ? (
              <>
                <View className={styles.mineTop}>
                  <View className={styles.mineName}>
                    <Text className={styles.typeBadge}>{partnerTypeName(myPartner.partnerType)}</Text>
                    {myPartner.partnerName}
                  </View>
                  <View className={`${styles.mineBadge} ${myPartner.status === 'active' ? styles.badgeActive : ''}`}>
                    {partnerStatusName(myPartner.status)}
                  </View>
                </View>
                <View className={styles.mineMeta}>
                  合作等级 {myPartner.partnerLevel} · 品鉴酒比例 {(myPartner.tastingRate * 100).toFixed(0)}%
                  {myPartner.contractStart ? ` · 合同期 ${myPartner.contractStart.slice(0, 10)} ~ ${myPartner.contractEnd.slice(0, 10)}` : ''}
                </View>

                {/* 场地管理 */}
                <View className={styles.venueSection}>
                  <View className={styles.venueSectionTitle}>
                    我的场地
                    <Text className={styles.venueAdd} onClick={openCreateVenue}>＋ 新增场地</Text>
                  </View>
                  {myVenues.length === 0 ? (
                    <View className={styles.venueEmpty}>暂无场地, 点击「新增场地」创建</View>
                  ) : (
                    myVenues.map(v => (
                      <View key={v.id} className={styles.venueRow}>
                        <View className={styles.venueLeft}>
                          <View className={styles.venueName}>{v.venueName}</View>
                          <View className={styles.venueMeta}>
                            {v.venueType}{v.capacity > 0 ? ` · 可容纳 ${v.capacity} 人` : ''}
                            {v.address ? ` · ${v.address}` : ''}
                          </View>
                        </View>
                        <View className={styles.venueStatus}>{v.status === 'active' ? '营业中' : v.status}</View>
                      </View>
                    ))
                  )}
                </View>
              </>
            ) : (
              <>
                <View className={styles.empty}>
                  <View className={styles.emptyIcon}>🤝</View>
                  <View className={styles.emptyText}>成为竹香酒合作商</View>
                  <View className={styles.emptySub}>
                    酒店 · 酒吧 · 会所入驻合作<br />
                    SVIP 进货价铺货 · 品鉴酒免费配额 · 等级分润
                  </View>
                </View>
                <View
                  className={styles.applyBtn}
                  onClick={() => {
                    if (!requireLogin()) return;
                    setApTypeIdx(0);
                    setApName('');
                    setApCredit('');
                    setApPhone('');
                    setApAddress('');
                    setShowApply(true);
                  }}
                >
                  申请合作入驻
                </View>
              </>
            )}
          </View>
        )}

        {/* 规则说明 */}
        <View className={styles.noteCard}>
          <View className={styles.noteTitle}>合作联盟规则</View>
          <View className={styles.noteLine}>· 酒水不分家: 酒店/酒吧/会所均可申请入驻合作</View>
          <View className={styles.noteLine}>· 审核签约后按 SVIP 进货价铺货竹香酒全系产品</View>
          <View className={styles.noteLine}>· 按合作等级(D→S)享品鉴酒免费配额与差价分润</View>
          <View className={styles.noteLine}>· 月销 5/20/50/100 瓶逐级晋升 C/B/A/S 等级</View>
        </View>
        <View className={styles.bottomSpacer} />
      </ScrollView>

      {/* 合作申请弹层 */}
      {showApply && (
        <View className={styles.mask} onClick={() => setShowApply(false)}>
          <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
            <View className={styles.sheetTitle}>申请合作入驻</View>
            <Picker
              mode="selector"
              range={TYPE_OPTIONS.map(t => t.label)}
              value={apTypeIdx}
              onChange={(e) => setApTypeIdx(Number((e.detail as any).value))}
            >
              <View className={styles.pickerRow}>
                <Text className={styles.pickerLabel}>合作商类型</Text>
                <Text className={styles.pickerValue}>{TYPE_OPTIONS[apTypeIdx]?.label}</Text>
                <Text className={styles.pickerArrow}>›</Text>
              </View>
            </Picker>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={apName}
                onInput={(e) => setApName((e.detail as any).value)}
                placeholder="合作商名称(如 竹韵大酒店)"
                placeholderClass={styles.placeholder}
                maxlength={40}
              />
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={apCredit}
                onInput={(e) => setApCredit((e.detail as any).value)}
                placeholder="统一社会信用代码"
                placeholderClass={styles.placeholder}
                maxlength={32}
              />
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                type="number"
                value={apPhone}
                onInput={(e) => setApPhone((e.detail as any).value)}
                placeholder="联系电话(选填)"
                placeholderClass={styles.placeholder}
                maxlength={15}
              />
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={apAddress}
                onInput={(e) => setApAddress((e.detail as any).value)}
                placeholder="联系地址(选填)"
                placeholderClass={styles.placeholder}
                maxlength={60}
              />
            </View>
            <View className={styles.sheetBtn} onClick={handleApply}>
              {submitting ? '提交中...' : '提交申请'}
            </View>
          </View>
        </View>
      )}

      {/* 新增场地弹层 */}
      {showVenue && myPartner && (
        <View className={styles.mask} onClick={() => setShowVenue(false)}>
          <View className={styles.sheet} onClick={(e) => e.stopPropagation()}>
            <View className={styles.sheetTitle}>新增场地</View>
            <View className={styles.sheetDesc}>场地用于合作商铺货与品鉴场景管理</View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={vnName}
                onInput={(e) => setVnName((e.detail as any).value)}
                placeholder="场地名称(如 竹韵宴会厅)"
                placeholderClass={styles.placeholder}
                maxlength={30}
              />
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={vnType}
                onInput={(e) => setVnType((e.detail as any).value)}
                placeholder="场地类型(宴会厅/包间/吧台等)"
                placeholderClass={styles.placeholder}
                maxlength={20}
              />
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                type="number"
                value={vnCapacity}
                onInput={(e) => setVnCapacity((e.detail as any).value)}
                placeholder="可容纳人数(选填)"
                placeholderClass={styles.placeholder}
                maxlength={5}
              />
            </View>
            <View className={styles.sheetBtn} onClick={handleCreateVenue}>
              {submitting ? '创建中...' : '确认创建'}
            </View>
          </View>
        </View>
      )}
    </View>
  );
};

export default VenuePage;
