/**
 * 秒杀管理工作台(admin) · 对接 /api/flash/admin/*
 * 三页签: 场次管理(建/加商品/发布/取消) · 风控参数 · 运营统计
 * 时间口径: 前端输入 "YYYY-MM-DD HH:mm"(北京) → 提交转 ISO8601+08:00
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  FlashAdminAPI, FlashSessionVO, FlashItemVO,
  FlashSettingsVO, FlashStatsVO,
} from '@/api/flashsale';
import { getSession } from '@/services/auth-service';

type Tab = 'sessions' | 'settings' | 'stats';

const TABS: { key: Tab; label: string }[] = [
  { key: 'sessions', label: '场次管理' },
  { key: 'settings', label: '风控参数' },
  { key: 'stats', label: '运营统计' },
];

/** 场次状态显示名 */
const SESSION_STATUS: Record<string, { label: string; cls: string }> = {
  DRAFT: { label: '草稿', cls: 'draft' },
  PUBLISHED: { label: '已发布', cls: 'published' },
  CANCELLED: { label: '已取消', cls: 'cancelled' },
};

const statusName = (s: string): string =>
  SESSION_STATUS[s]?.label || s;

/** 状态 pill 类名(草稿/已发布/已取消三色) */
const pillClass = (s: string): string =>
  styles[`pill${SESSION_STATUS[s]?.cls || 'draft'}`] || '';

/** "2026-09-16 20:00" → "2026-09-16T20:00:00+08:00"(容错已带 T/时区) */
const toIsoBJ = (raw: string): string => {
  const s = raw.trim().replace(' ', 'T');
  if (/[Zz]|[+-]\d{2}:\d{2}$/.test(s)) return s;
  return `${s}:00+08:00`;
};

const fmtTime = (iso: string): string =>
  (iso || '').slice(0, 16).replace('T', ' ');

const FlashAdminPage: React.FC = () => {
  const isAdmin = getSession()?.role === 'admin';
  const [tab, setTab] = useState<Tab>('sessions');

  // 场次列表态
  const [sessions, setSessions] = useState<FlashSessionVO[]>([]);
  const [loading, setLoading] = useState(true);
  const [active, setActive] = useState<FlashSessionVO | null>(null);
  // 建场次/编辑场次表单(editingId 空=创建模式, 非空=编辑草稿)
  const [form, setForm] = useState({ name: '', start: '', end: '' });
  const [editingId, setEditingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // 加商品表单
  const [itemForm, setItemForm] = useState({
    productId: '', flashPrice: '', flashStock: '', limit: '1',
  });

  // 风控参数
  const [settings, setSettings] = useState<FlashSettingsVO | null>(null);
  const [settingsBusy, setSettingsBusy] = useState(false);

  // 统计
  const [stats, setStats] = useState<FlashStatsVO | null>(null);

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      const list = await FlashAdminAPI.listSessions();
      setSessions(list);
      setActive(prev =>
        prev ? list.find(s => s.sessionId === prev.sessionId) || null : null);
    } catch (e) {
      console.warn('[flash-admin] 场次加载失败:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadSettings = useCallback(async () => {
    try {
      setSettings(await FlashAdminAPI.getSettings());
    } catch (e) {
      console.warn('[flash-admin] 参数加载失败:', e);
    }
  }, []);

  const loadStats = useCallback(async () => {
    try {
      setStats(await FlashAdminAPI.stats());
    } catch (e) {
      console.warn('[flash-admin] 统计加载失败:', e);
    }
  }, []);

  /** 页签切换即自动加载(数据不空窗) */
  const switchTab = (key: Tab) => {
    setTab(key);
    if (key === 'sessions') loadSessions();
    else if (key === 'settings') loadSettings();
    else if (key === 'stats') loadStats();
  };

  useEffect(() => {
    switchTab('sessions');
  }, []);

  const toast = (title: string, icon: 'success' | 'none' = 'success') =>
    Taro.showToast({ title, icon, duration: 2000 });

  // ---- 场次操作 ----
  /** 编辑草稿(表单回填, 切编辑模式) */
  const openEdit = (s: FlashSessionVO) => {
    setEditingId(s.sessionId);
    setForm({
      name: s.name,
      start: fmtTime(s.startTime),
      end: fmtTime(s.endTime),
    });
  };

  /** 退出编辑回创建模式 */
  const resetForm = () => {
    setEditingId(null);
    setForm({ name: '', start: '', end: '' });
  };

  /** 提交(创建/编辑双模式——editingId 分流) */
  const handleSubmit = async () => {
    if (busy) return;
    if (!form.name.trim()) return toast('请输入场次名称', 'none');
    if (!form.start || !form.end) return toast('请填写起止时间', 'none');
    setBusy(true);
    try {
      if (editingId) {
        await FlashAdminAPI.updateSession(editingId, {
          name: form.name.trim(),
          startTime: toIsoBJ(form.start),
          endTime: toIsoBJ(form.end),
        });
        toast('场次已保存');
      } else {
        await FlashAdminAPI.createSession(
          form.name.trim(), toIsoBJ(form.start), toIsoBJ(form.end));
        toast('场次已创建(草稿)');
      }
      resetForm();
      await loadSessions();
    } catch (e: any) {
      toast(String(e?.message || e?.errMsg || '保存失败'), 'none');
    } finally {
      setBusy(false);
    }
  };

  const handleAddItem = async () => {
    if (busy || !active) return;
    const price = Number(itemForm.flashPrice);
    const stock = Number(itemForm.flashStock);
    const limit = Number(itemForm.limit);
    if (!itemForm.productId.trim()) return toast('请输入产品ID', 'none');
    if (!price || price <= 0) return toast('秒杀价须大于 0', 'none');
    if (!stock || stock < 1) return toast('库存须 ≥ 1', 'none');
    if (!limit || limit < 1) return toast('限购须 ≥ 1', 'none');
    setBusy(true);
    try {
      await FlashAdminAPI.addItem(
        active.sessionId, itemForm.productId.trim(),
        price, stock, limit);
      toast('商品已加入场次');
      setItemForm({ productId: '', flashPrice: '', flashStock: '', limit: '1' });
      const detail = await FlashAdminAPI.listSessions();
      setSessions(detail);
      setActive(detail.find(s => s.sessionId === active.sessionId) || null);
    } catch (e: any) {
      toast(String(e?.message || e?.errMsg || '添加失败'), 'none');
    } finally {
      setBusy(false);
    }
  };

  const handlePublish = async () => {
    if (busy || !active) return;
    setBusy(true);
    try {
      await FlashAdminAPI.publishSession(active.sessionId);
      toast('场次已发布, 用户侧可见');
      await loadSessions();
    } catch (e: any) {
      toast(String(e?.message || e?.errMsg || '发布失败'), 'none');
    } finally {
      setBusy(false);
    }
  };

  const handleCancelSession = async () => {
    if (busy || !active) return;
    setBusy(true);
    try {
      await FlashAdminAPI.cancelSession(active.sessionId);
      toast('场次已取消');
      await loadSessions();
    } catch (e: any) {
      toast(String(e?.message || e?.errMsg || '取消失败'), 'none');
    } finally {
      setBusy(false);
    }
  };

  // ---- 参数保存 ----
  const handleSaveSettings = async () => {
    if (settingsBusy || !settings) return;
    setSettingsBusy(true);
    try {
      await FlashAdminAPI.updateSettings({
        enabled: settings.enabled,
        minRegisterHours: Number(settings.minRegisterHours) || 0,
        minMemberLevel: Number(settings.minMemberLevel) || 0,
        orderExpireMinutes: Number(settings.orderExpireMinutes) || 15,
        maxQuantityPerOrder: Number(settings.maxQuantityPerOrder) || 1,
      });
      toast('参数已保存(即时生效)');
      await loadSettings();
    } catch (e: any) {
      toast(String(e?.message || e?.errMsg || '保存失败'), 'none');
    } finally {
      setSettingsBusy(false);
    }
  };

  // ---- 非管理员拦截 ----
  if (!isAdmin) {
    return (
      <View className={styles.page}>
        <NavBar title="秒杀管理" />
        <View className={styles.empty}>
          <View className={styles.emptyIcon}>🔐</View>
          <View className={styles.emptyText}>仅管理员可访问</View>
        </View>
      </View>
    );
  }

  return (
    <View className={styles.page}>
      <NavBar title="秒杀管理" />
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
        {/* ============ 场次管理 ============ */}
        {tab === 'sessions' && (
          <>
            {/* 建场次/编辑场次(双模式表单) */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>
                {editingId ? '编辑草稿场次' : '创建秒杀场次'}
              </View>
              <View className={styles.formRow}>
                <Text className={styles.formLabel}>名称</Text>
                <Input
                  className={styles.formInput}
                  value={form.name}
                  onInput={e => setForm({ ...form, name: e.detail.value })}
                  placeholder="如: 开仓狂欢·竹香酒专场"
                  placeholderClass={styles.placeholder}
                />
              </View>
              <View className={styles.formRow}>
                <Text className={styles.formLabel}>开始</Text>
                <Input
                  className={styles.formInput}
                  value={form.start}
                  onInput={e => setForm({ ...form, start: e.detail.value })}
                  placeholder="2026-09-16 20:00"
                  placeholderClass={styles.placeholder}
                />
              </View>
              <View className={styles.formRow}>
                <Text className={styles.formLabel}>结束</Text>
                <Input
                  className={styles.formInput}
                  value={form.end}
                  onInput={e => setForm({ ...form, end: e.detail.value })}
                  placeholder="2026-09-16 22:00"
                  placeholderClass={styles.placeholder}
                />
              </View>
              {editingId && (
                <View className={styles.sessionMeta}>
                  仅草稿场次可编辑, 保存后仍为草稿态
                </View>
              )}
              <View className={styles.btnRow}>
                <View className={styles.btnPrimary} onClick={handleSubmit}>
                  {busy ? '保存中...'
                    : editingId ? '保存修改' : '创建场次(草稿)'}
                </View>
                {editingId && (
                  <View className={styles.btnGhost} onClick={resetForm}>
                    取消编辑
                  </View>
                )}
              </View>
            </View>

            {/* 场次列表 */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>
                场次列表({sessions.length})
              </View>
              {loading && <View className={styles.empty}>加载中...</View>}
              {!loading && sessions.length === 0 && (
                <View className={styles.empty}>暂无场次</View>
              )}
              {sessions.map(s => (
                <View
                  key={s.sessionId}
                  className={`${styles.sessionRow} ${
                    active?.sessionId === s.sessionId ? styles.sessionRowActive : ''
                  }`}
                  onClick={() => setActive(s)}
                >
                  <View className={styles.sessionRowHead}>
                    <Text className={styles.sessionName}>{s.name}</Text>
                    <View className={styles.headBtns}>
                      {s.status === 'DRAFT' && (
                        <Text
                          className={styles.miniBtn}
                          onClick={e => { e.stopPropagation(); openEdit(s); }}
                        >
                          编辑
                        </Text>
                      )}
                      <Text className={`${styles.statusPill} ${pillClass(s.status)}`}>
                        {statusName(s.status)}
                      </Text>
                    </View>
                  </View>
                  <View className={styles.sessionMeta}>
                    #{s.sessionId} · {fmtTime(s.startTime)} ~ {fmtTime(s.endTime)} · 商品 {s.itemCount}
                  </View>
                  <View className={styles.sessionMeta}>
                    运行态: {s.runtimeStatusName || s.runtimeStatus}
                  </View>
                </View>
              ))}
            </View>

            {/* 选中场次: 商品与操作 */}
            {active && (
              <View className={styles.card}>
                <View className={styles.cardTitle}>
                  {active.name} · {statusName(active.status)}
                </View>
                <View className={styles.sessionMeta}>
                  {fmtTime(active.startTime)} ~ {fmtTime(active.endTime)}
                </View>

                {/* 草稿: 加商品表单 */}
                {active.status === 'DRAFT' && (
                  <>
                    <View className={styles.subTitle}>添加秒杀商品</View>
                    <View className={styles.formRow}>
                      <Text className={styles.formLabel}>产品ID</Text>
                      <Input
                        className={styles.formInput}
                        value={itemForm.productId}
                        onInput={e => setItemForm({ ...itemForm, productId: e.detail.value })}
                        placeholder="如 ZX42-2026L07"
                        placeholderClass={styles.placeholder}
                      />
                    </View>
                    <View className={styles.formRow}>
                      <Text className={styles.formLabel}>秒杀价</Text>
                      <Input
                        className={styles.formInput}
                        type="digit"
                        value={itemForm.flashPrice}
                        onInput={e => setItemForm({ ...itemForm, flashPrice: e.detail.value })}
                        placeholder="须低于原价"
                        placeholderClass={styles.placeholder}
                      />
                    </View>
                    <View className={styles.formRow}>
                      <Text className={styles.formLabel}>库存</Text>
                      <Input
                        className={styles.formInput}
                        type="number"
                        value={itemForm.flashStock}
                        onInput={e => setItemForm({ ...itemForm, flashStock: e.detail.value })}
                        placeholder="秒杀总库存"
                        placeholderClass={styles.placeholder}
                      />
                    </View>
                    <View className={styles.formRow}>
                      <Text className={styles.formLabel}>限购</Text>
                      <Input
                        className={styles.formInput}
                        type="number"
                        value={itemForm.limit}
                        onInput={e => setItemForm({ ...itemForm, limit: e.detail.value })}
                        placeholder="每人限购"
                        placeholderClass={styles.placeholder}
                      />
                    </View>
                    <View className={styles.btnPrimary} onClick={handleAddItem}>
                      {busy ? '添加中...' : '加入场次'}
                    </View>
                  </>
                )}

                {/* 商品列表 */}
                {active.items && active.items.length > 0 && (
                  <>
                    <View className={styles.subTitle}>
                      场次商品({active.items.length})
                    </View>
                    {active.items.map((i: FlashItemVO) => (
                      <View key={i.itemId} className={styles.itemRow}>
                        <View className={styles.itemName}>{i.productName}</View>
                        <View className={styles.itemMeta}>
                          ¥{i.flashPrice}(原 ¥{i.originalPrice}) · 库存 {i.remainingStock}/{i.flashStock}
                          · 限购 {i.limitPerMember} · 已抢 {i.progressPercent}%
                        </View>
                      </View>
                    ))}
                  </>
                )}

                {/* 操作按钮 */}
                <View className={styles.btnRow}>
                  {active.status === 'DRAFT' && (
                    <View className={styles.btnPrimary} onClick={handlePublish}>
                      {busy ? '发布中...' : '发布场次'}
                    </View>
                  )}
                  {active.status === 'PUBLISHED' && (
                    <View className={styles.btnDanger} onClick={handleCancelSession}>
                      {busy ? '取消中...' : '取消场次(回补库存)'}
                    </View>
                  )}
                </View>
              </View>
            )}
          </>
        )}

        {/* ============ 风控参数 ============ */}
        {tab === 'settings' && settings && (
          <View className={styles.card}>
            <View className={styles.cardTitle}>秒杀风控参数</View>
            <View className={styles.switchRow} onClick={() =>
              setSettings({ ...settings, enabled: !settings.enabled })}>
              <Text className={styles.formLabel}>秒杀总开关</Text>
              <Text className={styles.switchVal}>
                {settings.enabled ? '已开启' : '已关闭'}
              </Text>
            </View>
            <View className={styles.formRow}>
              <Text className={styles.formLabel}>注册时长≥</Text>
              <Input
                className={styles.formInput}
                type="number"
                value={String(settings.minRegisterHours)}
                onInput={e => setSettings({
                  ...settings, minRegisterHours: Number(e.detail.value) || 0 })}
              />
              <Text className={styles.formUnit}>小时</Text>
            </View>
            <View className={styles.formRow}>
              <Text className={styles.formLabel}>会员等级≥</Text>
              <Input
                className={styles.formInput}
                type="number"
                value={String(settings.minMemberLevel)}
                onInput={e => setSettings({
                  ...settings, minMemberLevel: Number(e.detail.value) || 0 })}
              />
              <Text className={styles.formUnit}>级</Text>
            </View>
            <View className={styles.formRow}>
              <Text className={styles.formLabel}>订单超时</Text>
              <Input
                className={styles.formInput}
                type="number"
                value={String(settings.orderExpireMinutes)}
                onInput={e => setSettings({
                  ...settings, orderExpireMinutes: Number(e.detail.value) || 15 })}
              />
              <Text className={styles.formUnit}>分钟</Text>
            </View>
            <View className={styles.formRow}>
              <Text className={styles.formLabel}>单笔上限</Text>
              <Input
                className={styles.formInput}
                type="number"
                value={String(settings.maxQuantityPerOrder)}
                onInput={e => setSettings({
                  ...settings, maxQuantityPerOrder: Number(e.detail.value) || 1 })}
              />
              <Text className={styles.formUnit}>件</Text>
            </View>
            <View className={styles.btnPrimary} onClick={handleSaveSettings}>
              {settingsBusy ? '保存中...' : '保存参数(即时生效)'}
            </View>
          </View>
        )}

        {/* ============ 运营统计 ============ */}
        {tab === 'stats' && stats && (
          <View className={styles.card}>
            <View className={styles.cardTitle}>全局统计</View>
            <View className={styles.statRow}>
              <View className={styles.statBox}>
                <View className={styles.statNum}>{stats.sessionCount ?? 0}</View>
                <View className={styles.statLabel}>场次数</View>
              </View>
              <View className={styles.statBox}>
                <View className={styles.statNum}>{stats.orderCount ?? 0}</View>
                <View className={styles.statLabel}>订单数</View>
              </View>
              <View className={styles.statBox}>
                <View className={styles.statNum}>¥{stats.paidAmount ?? 0}</View>
                <View className={styles.statLabel}>已支付金额</View>
              </View>
            </View>

            {(stats.sessions || []).length > 0 && (
              <>
                <View className={styles.subTitle}>按场次</View>
                {(stats.sessions || []).map((s: any) => (
                  <View key={s.sessionId} className={styles.itemRow}>
                    <View className={styles.itemName}>{s.name}</View>
                    <View className={styles.itemMeta}>
                      商品 {s.itemCount} · 售出 {s.soldCount}/{s.totalStock} ·
                      订单 {s.orderCount}(支付 {s.paidCount}/待付 {s.pendingCount}/取消 {s.cancelledCount}) ·
                      ¥{s.paidAmount}
                    </View>
                  </View>
                ))}
              </>
            )}
          </View>
        )}

        <View className={styles.bottomSpacer} />
      </ScrollView>
    </View>
  );
};

export default FlashAdminPage;
