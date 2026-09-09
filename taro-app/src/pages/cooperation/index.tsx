/**
 * 商务合作 · 企业/个人/政府/经销 合作申请
 * 提交申请(预估金额/业务范围) → AI 资质审核 → 签约 → 我的申请状态
 * 数据来源: 后端 /api/cooperation/*
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input, Picker, Textarea } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  CooperationAPI, CoopApplicationVO,
  partnerTypeName, applyTypeName, applyStatusName,
} from '@/api/cooperation';
import { requireLogin } from '@/services/auth-service';

type Tab = 'apply' | 'mine';

const TABS: { key: Tab; label: string }[] = [
  { key: 'apply', label: '提交申请' },
  { key: 'mine', label: '我的申请' },
];

// 合作方类型选项
const PARTNER_TYPES = ['enterprise', 'personal', 'government', 'dealer'];
// 申请类型选项
const APPLY_TYPES = ['new', 'renewal', 'upgrade'];

const formatDate = (t?: string): string => (t ? t.slice(0, 10) : '');

const CooperationPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('apply');
  const [submitting, setSubmitting] = useState(false);
  // 我的申请
  const [myApp, setMyApp] = useState<CoopApplicationVO | null>(null);
  const [mineLoading, setMineLoading] = useState(false);
  // 表单
  const [partnerName, setPartnerName] = useState('');
  const [typeIdx, setTypeIdx] = useState(0);
  const [ptTypeIdx, setPtTypeIdx] = useState(0);
  const [scope, setScope] = useState('');
  const [amount, setAmount] = useState('');
  const [contactName, setContactName] = useState('');
  const [contactPhone, setContactPhone] = useState('');
  const [contactEmail, setContactEmail] = useState('');

  const loadMine = useCallback(async () => {
    setMineLoading(true);
    try {
      const app = await CooperationAPI.myApplication();
      setMyApp(app);
    } finally {
      setMineLoading(false);
    }
  }, []);

  useEffect(() => {
    if (requireLogin()) {
      loadMine();
    }
  }, [loadMine]);

  // 提交合作申请
  const handleApply = async () => {
    if (submitting) return;
    if (!partnerName.trim()) {
      Taro.showToast({ title: '请输入合作方名称', icon: 'none' });
      return;
    }
    if (!scope.trim()) {
      Taro.showToast({ title: '请描述业务范围', icon: 'none' });
      return;
    }
    const amt = Number(amount);
    if (!amt || amt <= 0) {
      Taro.showToast({ title: '请输入预估合作金额', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const app = await CooperationAPI.apply({
        partnerName: partnerName.trim(),
        partnerType: PARTNER_TYPES[ptTypeIdx],
        type: APPLY_TYPES[typeIdx],
        businessScope: scope.trim(),
        estimatedAmount: amt,
        contactName: contactName.trim(),
        contactPhone: contactPhone.trim(),
        contactEmail: contactEmail.trim(),
      });
      Taro.showModal({
        title: '申请已提交',
        content: `申请单号 ${app.applicationId}, AI 资质审核中, 结果可在「我的申请」查看。`,
        showCancel: false,
      });
      setPartnerName('');
      setScope('');
      setAmount('');
      setContactName('');
      setContactPhone('');
      setContactEmail('');
      setTab('mine');
      await loadMine();
    } catch (e) {
      console.warn('[cooperation] 合作申请提交失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <View className={styles.page}>
      <NavBar title="商务合作" />
      {/* 顶部 Tabs */}
      <View className={styles.tabBar}>
        {TABS.map(t => (
          <View
            key={t.key}
            className={`${styles.tabItem} ${tab === t.key ? styles.tabItemActive : ''}`}
            onClick={async () => {
              setTab(t.key);
              if (t.key === 'mine') await loadMine();
            }}
          >
            {t.label}
          </View>
        ))}
      </View>

      <ScrollView scrollY className={styles.scrollView}>
        {tab === 'apply' ? (
          <View className={styles.card}>
            <View className={styles.cardTitle}>合作申请</View>
            <View className={styles.sheetDesc}>
              企业/个人/政府/经销 · AI 资质审核 · 签约合作
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={partnerName}
                onInput={(e) => setPartnerName((e.detail as any).value)}
                placeholder="合作方名称(公司/机构名)"
                placeholderClass={styles.placeholder}
                maxlength={40}
              />
            </View>
            <Picker
              mode="selector"
              range={PARTNER_TYPES.map(partnerTypeName)}
              value={ptTypeIdx}
              onChange={(e) => setPtTypeIdx(Number((e.detail as any).value))}
            >
              <View className={styles.pickerRow}>
                <Text className={styles.pickerLabel}>合作方类型</Text>
                <Text className={styles.pickerValue}>{partnerTypeName(PARTNER_TYPES[ptTypeIdx])}</Text>
                <Text className={styles.pickerArrow}>›</Text>
              </View>
            </Picker>
            <Picker
              mode="selector"
              range={APPLY_TYPES.map(applyTypeName)}
              value={typeIdx}
              onChange={(e) => setTypeIdx(Number((e.detail as any).value))}
            >
              <View className={styles.pickerRow}>
                <Text className={styles.pickerLabel}>申请类型</Text>
                <Text className={styles.pickerValue}>{applyTypeName(APPLY_TYPES[typeIdx])}</Text>
                <Text className={styles.pickerArrow}>›</Text>
              </View>
            </Picker>
            <View className={styles.textareaRow}>
              <Textarea
                className={styles.textarea}
                value={scope}
                onInput={(e) => setScope((e.detail as any).value)}
                placeholder="业务范围(如 竹香酒系列区域经销/餐饮渠道供货/礼盒定制等)"
                placeholderClass={styles.placeholder}
                maxlength={200}
              />
            </View>
            <View className={styles.inputRow}>
              <Text className={styles.inputPrefix}>¥</Text>
              <Input
                className={styles.input}
                type="digit"
                value={amount}
                onInput={(e) => setAmount((e.detail as any).value)}
                placeholder="预估合作金额(元)"
                placeholderClass={styles.placeholder}
              />
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={contactName}
                onInput={(e) => setContactName((e.detail as any).value)}
                placeholder="联系人(选填)"
                placeholderClass={styles.placeholder}
                maxlength={20}
              />
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                type="number"
                value={contactPhone}
                onInput={(e) => setContactPhone((e.detail as any).value)}
                placeholder="联系电话(选填)"
                placeholderClass={styles.placeholder}
                maxlength={15}
              />
            </View>
            <View className={styles.inputRow}>
              <Input
                className={styles.input}
                value={contactEmail}
                onInput={(e) => setContactEmail((e.detail as any).value)}
                placeholder="联系邮箱(选填)"
                placeholderClass={styles.placeholder}
                maxlength={40}
              />
            </View>
            <View className={styles.sheetBtn} onClick={handleApply}>
              {submitting ? '提交中...' : '提交合作申请'}
            </View>
          </View>
        ) : (
          <View className={styles.card}>
            <View className={styles.cardTitle}>我的申请</View>
            {mineLoading ? (
              <View className={styles.empty}>加载中...</View>
            ) : myApp ? (
              <>
                <View className={styles.appTop}>
                  <View className={styles.appName}>{myApp.partnerName}</View>
                  <View className={`${styles.appBadge} ${myApp.status === 'approved' || myApp.status === 'signed' ? styles.badgeOk : ''}`}>
                    {applyStatusName(myApp.status)}
                  </View>
                </View>
                <View className={styles.appMeta}>
                  申请单号 {myApp.applicationId} · {partnerTypeName(myApp.partnerType)}
                  · {applyTypeName(myApp.type)} · {formatDate(myApp.createdAt)}
                </View>
                <View className={styles.appMeta}>预估金额 ¥{myApp.estimatedAmount.toFixed(2)}</View>
                <View className={styles.appMeta}>{myApp.businessScope}</View>
                {myApp.reviewNote && (
                  <View className={styles.appReview}>审核意见: {myApp.reviewNote}</View>
                )}
                <View className={styles.statusFlow}>
                  <View className={styles.flowStep}>
                    <View className={styles.flowDot}>✓</View>
                    <View className={styles.flowLabel}>提交申请</View>
                  </View>
                  <View className={styles.flowLine} />
                  <View className={styles.flowStep}>
                    <View className={styles.flowDot}>{['approved', 'signed'].includes(myApp.status) ? '✓' : '2'}</View>
                    <View className={styles.flowLabel}>AI 审核</View>
                  </View>
                  <View className={styles.flowLine} />
                  <View className={styles.flowStep}>
                    <View className={styles.flowDot}>{myApp.status === 'signed' ? '✓' : '3'}</View>
                    <View className={styles.flowLabel}>签约合作</View>
                  </View>
                </View>
              </>
            ) : (
              <View className={styles.empty}>
                <View className={styles.emptyIcon}>🤝</View>
                <View>暂无申请记录, 去「提交申请」发起合作</View>
              </View>
            )}
          </View>
        )}

        {/* 规则说明 */}
        <View className={styles.noteCard}>
          <View className={styles.noteTitle}>合作流程</View>
          <View className={styles.noteLine}>· 提交申请: 合作方信息 + 业务范围 + 预估金额</View>
          <View className={styles.noteLine}>· AI 资质审核: 合规评分 ≥80 通过, 大额转人工复核</View>
          <View className={styles.noteLine}>· 审核通过后签约, 进入合作履约</View>
          <View className={styles.noteLine}>· 合作类型: 企业 / 个人 / 政府机构 / 经销商</View>
        </View>
        <View className={styles.bottomSpacer} />
      </ScrollView>
    </View>
  );
};

export default CooperationPage;
