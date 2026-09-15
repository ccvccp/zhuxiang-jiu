/**
 * 73号·信任面板(用户权利面) · 四可: 可解释/可撤回/可验证/可遗忘
 * 数据来源: 后端 /api/member73/trust/*(本人鉴权)
 * 口径: 用户权利不受 MEMBER73_MODE 影响(永不关停);
 *       遗忘=五表硬删除+留痕, 账户本体保留, 不可重复。
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  Member73API, TrustPanelVO, ForgetLedgerVO,
} from '@/api/member73';
import { requireLogin } from '@/services/auth-service';

/** 动作类型标签 */
const KIND_NAME: Record<string, string> = {
  hint: '触达',
  reveal: '权益',
  delegate: '代办',
};

const Member73TrustPage: React.FC = () => {
  const [loading, setLoading] = useState(true);
  const [panel, setPanel] = useState<TrustPanelVO | null>(null);
  // 遗忘流
  const [forgetting, setForgetting] = useState(false);
  const [forgetResult, setForgetResult] = useState<ForgetLedgerVO | null>(null);

  const loadPanel = useCallback(async () => {
    try {
      setPanel(await Member73API.trustPanel());
    } catch (e) {
      console.warn('[member73-trust] 面板加载失败:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (requireLogin()) {
      loadPanel();
    } else {
      setLoading(false);
    }
  }, [loadPanel]);

  /** 画像遗忘(二次确认——不可逆+不可重复) */
  const handleForget = async () => {
    if (forgetting || forgetResult) return;
    const { confirm } = await Taro.showModal({
      title: '确认遗忘 AI 画像?',
      content: '将硬删除您的全部体验画像数据(触达留痕/权益告知/授权/代办记录), '
        + '账户与订单资产不受影响。此操作不可逆, 且不可重复执行。',
      confirmText: '我已知晓, 遗忘',
      confirmColor: '#f56c6c',
    });
    if (!confirm) return;
    setForgetting(true);
    try {
      const ledger = await Member73API.trustForget();
      setForgetResult(ledger);
      Taro.showToast({ title: '画像已遗忘', icon: 'success' });
      loadPanel();
    } catch (e: any) {
      const msg = String(e?.message || e);
      Taro.showModal({
        title: '遗忘未执行',
        content: msg.includes('重复') || msg.includes('已遗忘')
          ? '您的画像已处于遗忘状态(账户保留), 无需重复操作。'
          : (msg || '请稍后重试'),
        showCancel: false,
      });
    } finally {
      setForgetting(false);
    }
  };

  const actions = panel?.actions || [];
  const revocable = panel?.revocable || [];
  const principles = panel?.fourPrinciples || {};

  return (
    <View className={styles.page}>
      <NavBar title="信任面板" />
      <ScrollView scrollY className={styles.scrollView}>
        {/* 头部卡 */}
        <View className={styles.heroCard}>
          <View className={styles.heroTitle}>AI 为我做了什么 · 全程留痕</View>
          <View className={styles.heroName}>{panel?.nickname || '我的信任面板'}</View>
          <View className={styles.heroMeta}>
            会员 ID {panel?.memberId ?? '--'} · 共 {panel?.actionTotal ?? 0} 条动作留痕
          </View>
        </View>

        {/* 四可原则 */}
        <View className={styles.principlesCard}>
          <View className={styles.cardTitle}>您的四项数据权利</View>
          <View className={styles.principlesGrid}>
            {Object.entries(principles).map(([name, desc]) => (
              <View key={name} className={styles.principleItem}>
                <View className={styles.principleName}>{name}</View>
                <View className={styles.principleDesc}>{desc}</View>
              </View>
            ))}
          </View>
        </View>

        {/* 动作流(可解释) */}
        <View className={styles.sectionCard}>
          <View className={styles.sectionHead}>
            <View className={styles.cardTitle}>动作流 · 可解释</View>
            <View className={styles.sectionCount}>最近 {actions.length} 条</View>
          </View>
          {loading ? (
            <View className={styles.empty}>加载中…</View>
          ) : actions.length === 0 ? (
            <View className={styles.empty}>
              {forgetResult ? '画像已遗忘——动作流已清空' : '暂无 AI 动作留痕'}
            </View>
          ) : actions.map(a => (
            <View key={`${a.kind}-${a.refId}`} className={styles.actionItem}>
              <View className={`${styles.kindBadge} ${a.kind === 'delegate' ? styles.kindBadgeDelegate : ''} ${a.kind === 'reveal' ? styles.kindBadgeReveal : ''}`}>
                {KIND_NAME[a.kind] || a.kind}
              </View>
              <View className={styles.actionBody}>
                <View className={styles.actionSummary}>{a.summary || '—'}</View>
                <View className={styles.actionMeta}>
                  {a.at ? new Date(a.at).toLocaleString('zh-CN') : ''}
                  {a.responded ? ' · 已回应' : ''}
                </View>
              </View>
            </View>
          ))}
        </View>

        {/* 可撤回授权 */}
        <View className={styles.sectionCard}>
          <View className={styles.sectionHead}>
            <View className={styles.cardTitle}>生效中授权 · 可撤回</View>
            <View className={styles.sectionCount}>{revocable.length} 项</View>
          </View>
          {revocable.length === 0 ? (
            <View className={styles.empty}>
              {forgetResult ? '画像已遗忘——授权已清空' : '暂无生效授权(AI 未获任何代办授权)'}
            </View>
          ) : revocable.map(g => (
            <View key={g.action} className={styles.grantItem}>
              <View className={styles.grantAction}>{g.action}</View>
              <View className={styles.grantAt}>
                {g.grantedAt ? new Date(g.grantedAt).toLocaleString('zh-CN') : ''}
              </View>
            </View>
          ))}
        </View>

        {/* 遗忘权(危险区) */}
        <View className={styles.forgetCard}>
          <View className={styles.forgetTitle}>🗑️ 画像遗忘权</View>
          <View className={styles.forgetDesc}>
            根据《个人信息保护法》第 47 条, 您有权删除您的个人信息。
            执行后将硬删除以上全部画像数据(五表: 触达留痕/权益告知/授权/代办记录/体验快照)。
          </View>
          <View className={styles.forgetWarn}>
            · 账户本体、订单与资产不受影响, 仅清除 AI 体验画像<br />
            · 操作不可逆, 且每位会员仅可执行一次(重复请求将被拒绝)<br />
            · 删除行为将以匿名序号留痕(不含个人数据), 用于满足审计要求
          </View>
          {forgetResult ? (
            <View className={styles.forgetResult}>
              <View className={styles.forgetResultTitle}>
                ✓ 遗忘已执行 · 留痕序号 #{forgetResult.forgetSeq}
              </View>
              <View className={styles.forgetResultTables}>
                {Object.entries(forgetResult.deletedTables).map(
                  ([table, count]) => `${table}: 删除 ${count} 条`).join(' · ')}
              </View>
            </View>
          ) : (
            <View
              className={`${styles.forgetBtn} ${forgetting ? styles.forgetBtnDisabled : ''}`}
              onClick={handleForget}
            >
              {forgetting ? '执行中…' : '删除我的 AI 画像'}
            </View>
          )}
        </View>

        <View className={styles.footerNote}>
          信任面板 · 用户权利不受模型运行模式影响 · 数据可审计
        </View>
      </ScrollView>
    </View>
  );
};

export default Member73TrustPage;
