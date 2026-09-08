import React, { useState, useEffect } from 'react';
import { View, Text, Input, ScrollView } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import { InvoiceAPI, InvoiceTitleVO, InvoiceVO } from '@/api/invoice';
import { requireLogin } from '@/services/auth-service';

/**
 * 发票管理页 · 抬头簿 CRUD + 我的发票列表
 * 对接 /api/invoice/titles + /api/invoice/mine
 */
const InvoicePage: React.FC = () => {
  const [titles, setTitles] = useState<InvoiceTitleVO[]>([]);
  const [invoices, setInvoices] = useState<InvoiceVO[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<'titles' | 'invoices'>('titles');
  // 新增抬头表单
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    titleType: 'personal' as 'personal' | 'company',
    title: '',
    taxNo: '',
  });
  const [submitting, setSubmitting] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const [ts, invs] = await Promise.all([
        InvoiceAPI.titles().catch(() => [] as InvoiceTitleVO[]),
        InvoiceAPI.mine().catch(() => [] as InvoiceVO[]),
      ]);
      setTitles(ts);
      setInvoices(invs);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (requireLogin()) loadData();
    else setLoading(false);
  }, []);

  useDidShow(() => {
    loadData();
  });

  const handleAddTitle = async () => {
    if (submitting) return;
    if (!form.title.trim()) {
      Taro.showToast({ title: '请填写发票抬头', icon: 'none' });
      return;
    }
    if (form.titleType === 'company' && !form.taxNo.trim()) {
      Taro.showToast({ title: '企业抬头需填写税号', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      // 首个抬头后端自动设为默认
      await InvoiceAPI.addTitle({
        titleType: form.titleType,
        title: form.title.trim(),
        taxNo: form.taxNo.trim(),
      });
      Taro.showToast({ title: '抬头已添加', icon: 'success' });
      setShowForm(false);
      setForm({ titleType: 'personal', title: '', taxNo: '' });
      loadData();
    } catch (e) {
      console.warn('[invoice] 抬头保存失败:', e);
      Taro.showToast({ title: '保存失败', icon: 'none' });
    } finally {
      setSubmitting(false);
    }
  };

  const handleSetDefault = async (t: InvoiceTitleVO) => {
    if (t.isDefault) return;
    try {
      await InvoiceAPI.setDefault(t.id);
      Taro.showToast({ title: '已设为默认', icon: 'success' });
      loadData();
    } catch (e) {
      console.warn('[invoice] 设默认失败:', e);
    }
  };

  const handleRemoveTitle = (t: InvoiceTitleVO) => {
    Taro.showModal({
      title: '删除抬头',
      content: `确定删除抬头「${t.title}」吗?`,
      confirmColor: '#e64340',
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await InvoiceAPI.removeTitle(t.id);
          Taro.showToast({ title: '已删除', icon: 'success' });
          loadData();
        } catch (e) {
          console.warn('[invoice] 删除失败:', e);
        }
      },
    });
  };

  return (
    <View className={styles.page}>
      <NavBar title="发票管理" />
      <ScrollView scrollY className={styles.scrollView}>
        {/* Tab 切换 */}
        <View className={styles.tabBar}>
          <View
            className={`${styles.tabItem} ${tab === 'titles' ? styles.tabActive : ''}`}
            onClick={() => setTab('titles')}
          >
            发票抬头({titles.length})
          </View>
          <View
            className={`${styles.tabItem} ${tab === 'invoices' ? styles.tabActive : ''}`}
            onClick={() => setTab('invoices')}
          >
            我的发票({invoices.length})
          </View>
        </View>

        {loading ? (
          <View className={styles.empty}>加载中...</View>
        ) : tab === 'titles' ? (
          <>
            {/* 新增抬头表单 */}
            {showForm ? (
              <View className={styles.formCard}>
                <View className={styles.typeRow}>
                  <View
                    className={`${styles.typeBtn} ${form.titleType === 'personal' ? styles.typeActive : ''}`}
                    onClick={() => setForm(f => ({ ...f, titleType: 'personal' }))}
                  >
                    个人
                  </View>
                  <View
                    className={`${styles.typeBtn} ${form.titleType === 'company' ? styles.typeActive : ''}`}
                    onClick={() => setForm(f => ({ ...f, titleType: 'company' }))}
                  >
                    企业
                  </View>
                </View>
                <View className={styles.fieldRow}>
                  <Text className={styles.fieldLabel}>抬头名称</Text>
                  <Input
                    className={styles.fieldInput}
                    placeholder={form.titleType === 'personal' ? '个人姓名' : '企业全称'}
                    value={form.title}
                    onInput={(e) => setForm(f => ({ ...f, title: e.detail.value }))}
                  />
                </View>
                {form.titleType === 'company' ? (
                  <View className={styles.fieldRow}>
                    <Text className={styles.fieldLabel}>税号</Text>
                    <Input
                      className={styles.fieldInput}
                      placeholder="纳税人识别号"
                      value={form.taxNo}
                      onInput={(e) => setForm(f => ({ ...f, taxNo: e.detail.value }))}
                    />
                  </View>
                ) : null}
                <View className={styles.formActions}>
                  <View className={styles.cancelBtn} onClick={() => setShowForm(false)}>取消</View>
                  <View className={styles.confirmBtn} onClick={handleAddTitle}>
                    {submitting ? '保存中' : '保存'}
                  </View>
                </View>
              </View>
            ) : null}

            {/* 抬头列表 */}
            {titles.length === 0 && !showForm ? (
              <View className={styles.empty}>
                <View className={styles.emptyIcon}>🧾</View>
                <View>暂无发票抬头</View>
                <View className={styles.emptySub}>添加后下单可自动无感开票</View>
                <View className={styles.addBtn} onClick={() => setShowForm(true)}>+ 新增抬头</View>
              </View>
            ) : (
              titles.map(t => (
                <View key={t.id} className={styles.titleCard}>
                  <View className={styles.titleTop}>
                    <View className={styles.titleName}>{t.title}</View>
                    {t.isDefault ? <Text className={styles.defaultTag}>默认</Text> : null}
                  </View>
                  <View className={styles.titleMeta}>
                    {t.titleType === 'company' ? `企业 · 税号 ${t.taxNo || '—'}` : '个人抬头'}
                  </View>
                  <View className={styles.titleActions}>
                    {!t.isDefault ? (
                      <Text className={styles.actionBtn} onClick={() => handleSetDefault(t)}>设为默认</Text>
                    ) : null}
                    <Text className={`${styles.actionBtn} ${styles.danger}`} onClick={() => handleRemoveTitle(t)}>删除</Text>
                  </View>
                </View>
              ))
            )}
            {titles.length > 0 && !showForm ? (
              <View className={styles.addBtn} onClick={() => setShowForm(true)}>+ 新增抬头</View>
            ) : null}
          </>
        ) : (
          /* 我的发票列表 */
          invoices.length === 0 ? (
            <View className={styles.empty}>
              <View className={styles.emptyIcon}>📄</View>
              <View>暂无发票记录</View>
              <View className={styles.emptySub}>订单完成后可申请开票</View>
            </View>
          ) : (
            invoices.map((inv, idx) => (
              <View key={inv.invoiceNo || idx} className={styles.invoiceCard}>
                <View className={styles.invoiceTop}>
                  <View className={styles.invoiceNo}>{inv.invoiceNo || '(开票中)'}</View>
                  <Text className={styles.invoiceStatus}>{inv.status}</Text>
                </View>
                <View className={styles.invoiceMeta}>
                  <View>订单: {inv.orderId}</View>
                  <View>金额: ¥{inv.amount?.toFixed?.(2) ?? inv.amount}</View>
                  <View>{inv.issueType === 'manual' ? '手动开票' : '无感自动开票'} · {inv.title}</View>
                </View>
                <View className={styles.invoiceDate}>{(inv.issuedAt || '').slice(0, 10)}</View>
              </View>
            ))
          )
        )}
        <View className={styles.bottomSpacer} />
      </ScrollView>
    </View>
  );
};

export default InvoicePage;
