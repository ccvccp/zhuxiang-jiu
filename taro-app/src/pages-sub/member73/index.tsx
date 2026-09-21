/**
 * 73号·会员体验中心 · 四页签: 地平线 / 导师 / 权益 / 代办
 * 数据来源: 后端 /api/member73/*(X-Member-Id 本人鉴权)
 * 口径: 观测面不受 MEMBER73_MODE 影响; 代办授权显式性铁律
 *       (资金类永不授权; renewal_prefill 仅预填); 隐私红线:
 *       响应回流仅形式效果学习, 不画像。
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  Member73API, HorizonVO, MomentVO,
  DELEGATE_ACTION_NAME, DELEGATE_RISK_NAME,
  MOMENT_TYPE_NAME,
} from '@/api/member73';
import { requireLogin } from '@/services/auth-service';

type Tab = 'horizon' | 'mentor' | 'benefits' | 'delegate';

const TABS: { key: Tab; label: string }[] = [
  { key: 'horizon', label: '地平线' },
  { key: 'mentor', label: '导师' },
  { key: 'benefits', label: '权益' },
  { key: 'delegate', label: '代办' },
];

/** 代办动作风险语义(封闭五动作) */
const ACTION_META: Record<string, { desc: string; execMode: string }> = {
  profile_completion: { desc: '低风险 · 授权后可代', execMode: 'authorized_execute' },
  benefit_claim: { desc: '低风险 · 授权后可代', execMode: 'authorized_execute' },
  renewal_prefill: { desc: '中风险 · 仅预填不执行(不代付)', execMode: 'prefill_only' },
  review_order: { desc: '低风险 · 授权后可代', execMode: 'authorized_execute' },
  address_confirm: { desc: '低风险 · 授权后可代', execMode: 'authorized_execute' },
};

const fmtTime = (s: string): string =>
  s ? new Date(s).toLocaleString('zh-CN') : '';

const Member73CenterPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('horizon');
  const [loading, setLoading] = useState(true);
  // 地平线
  const [horizon, setHorizon] = useState<HorizonVO | null>(null);
  // 导师
  const [moments, setMoments] = useState<MomentVO[]>([]);
  // 权益
  const [preview, setPreview] = useState<any>(null);
  const [reveals, setReveals] = useState<any[]>([]);
  // 代办
  const [predict, setPredict] = useState<any>(null);
  const [grants, setGrants] = useState<any[]>([]);
  const [logs, setLogs] = useState<any[]>([]);
  const [busyAction, setBusyAction] = useState('');
  const [execResult, setExecResult] = useState<any>(null);

  const loadAll = useCallback(async () => {
    try {
      const [h, ms, pv, rv, pd, gs, ls] = await Promise.all([
        Member73API.horizon().catch(() => null),
        Member73API.mentorMoments(30).catch(() => [] as MomentVO[]),
        Member73API.benefitsPreview().catch(() => null),
        Member73API.benefitReveals(20).catch(() => []),
        Member73API.delegatePredict().catch(() => null),
        Member73API.delegateGrants().catch(() => []),
        Member73API.delegateLogs(20).catch(() => []),
      ]);
      setHorizon(h);
      setMoments(ms);
      setPreview(pv);
      setReveals(rv);
      setPredict(pd);
      setGrants(gs);
      setLogs(ls);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (requireLogin()) {
      loadAll();
    } else {
      setLoading(false);
    }
  }, [loadAll]);

  /** 时刻响应回流(形式效果学习) */
  const handleRespond = async (momentId: number, responseType: string) => {
    try {
      await Member73API.momentRespond(momentId, responseType);
      Taro.showToast({ title: '已记录', icon: 'success' });
      setMoments(await Member73API.mentorMoments(30).catch(() => [] as MomentVO[]));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  /** 授权/撤回/执行 */
  const handleGrant = async (action: string, grant: boolean) => {
    if (busyAction) return;
    setBusyAction(action);
    try {
      const r = grant
        ? await Member73API.delegateGrant(action)
        : await Member73API.delegateRevoke(action);
      Taro.showToast({
        title: (grant ? '已授权 ' : '已撤回 ') + (DELEGATE_ACTION_NAME[action] || action),
        icon: 'success',
      });
      setGrants(await Member73API.delegateGrants().catch(() => []));
      setPredict(await Member73API.delegatePredict().catch((() => predict)));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    } finally {
      setBusyAction('');
    }
  };

  const handleExecute = async (action: string) => {
    if (busyAction) return;
    setBusyAction(action);
    try {
      const r = await Member73API.delegateExecute(action);
      setExecResult(r);
      setLogs(await Member73API.delegateLogs(20).catch(() => []));
      setPredict(await Member73API.delegatePredict().catch((() => predict)));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    } finally {
      setBusyAction('');
    }
  };

  const grantedActions = new Set(grants.filter(g => g.granted).map(g => g.action));
  const kr = horizon?.keepRisk;

  return (
    <View className={styles.page}>
      <NavBar title="会员体验中心" />
      <View className={styles.tabs}>
        {TABS.map(t => (
          <View
            key={t.key}
            className={`${styles.tab} ${tab === t.key ? styles.tabActive : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </View>
        ))}
      </View>
      <ScrollView scrollY className={styles.scrollView}>

        {/* ============ 地平线 ============ */}
        {tab === 'horizon' && (
          <>
            <View className={styles.levelCard}>
              <View className={styles.levelLabel}>当前等级</View>
              <View className={styles.levelName}>{horizon?.levelName || '--'}</View>
              <View className={styles.levelMeta}>
                成长值 {horizon?.growthValue ?? '--'}
                {horizon?.next
                  ? ` · 距 L${horizon.next.level} 还需 ¥${horizon.next.gap} 累计消费`
                  : ' · 已达最高等级'}
              </View>
              {horizon?.estimatedArrival && (
                <View className={styles.levelMeta}>
                  按当前节奏预计 {fmtTime(horizon.estimatedArrival).slice(0, 10)} 达成
                </View>
              )}
              {horizon?.coldStart?.inShadow && (
                <View className={styles.levelMeta}>
                  新会员影子保护期(剩 {horizon.coldStart.daysRemaining || 0} 天)——体验功能渐进开放
                </View>
              )}
            </View>

            {horizon?.next && (
              <View className={styles.card}>
                <View className={styles.cardTitle}>升级缺口</View>
                <View className={styles.kv}>
                  <View className={styles.kvKey}>目标等级</View>
                  <View className={styles.kvVal}>L{horizon.next.level}</View>
                </View>
                <View className={styles.kv}>
                  <View className={styles.kvKey}>所需累计消费</View>
                  <View className={styles.kvVal}>¥{horizon.next.requirement}</View>
                </View>
                <View className={styles.gapBar}>
                  <View
                    className={styles.gapFill}
                    style={{
                      width: `${Math.min(100, Math.round(
                        (horizon.growthValue / Math.max(1, horizon.next.requirement)) * 100))}%`,
                    }}
                  />
                </View>
              </View>
            )}

            {kr && (
              <View className={styles.card}>
                <View className={styles.cardTitle}>保级窗口</View>
                <View className={styles.kv}>
                  <View className={styles.kvKey}>周期消费要求</View>
                  <View className={styles.kvVal}>¥{kr.requirement}</View>
                </View>
                <View className={styles.kv}>
                  <View className={styles.kvKey}>已消费</View>
                  <View className={styles.kvVal}>¥{kr.periodConsume}</View>
                </View>
                <View className={styles.kv}>
                  <View className={styles.kvKey}>窗口剩余</View>
                  <View className={styles.kvVal}>{kr.daysRemaining} 天</View>
                </View>
                {kr.atRisk && (
                  <View className={styles.riskWarn}>
                    ⚠️ 周期临期且保级消费未足——建议尽快补足 ¥{kr.remainingAmount} 以保持等级。
                  </View>
                )}
              </View>
            )}
          </>
        )}

        {/* ============ 导师时刻 ============ */}
        {tab === 'mentor' && (
          <View className={styles.card}>
            <View className={styles.sectionHead}>
              <View className={styles.cardTitle}>AI 导师触达留痕</View>
              <View className={styles.sectionCount}>{moments.length} 条</View>
            </View>
            {loading ? (
              <View className={styles.empty}>加载中…</View>
            ) : moments.length === 0 ? (
              <View className={styles.empty}>暂无导师触达记录</View>
            ) : moments.map(m => (
              <View key={m.momentId} className={styles.momentItem}>
                <View className={styles.momentHead}>
                  <View className={styles.momentType}>
                    {MOMENT_TYPE_NAME[m.momentType] || m.momentType}
                  </View>
                  <View className={styles.momentDecision}>
                    {m.decision === 'present' ? '已呈现' : m.decision === 'shadow' ? '影子观察' : '静默放弃'}
                  </View>
                </View>
                {m.hintPayload?.text && (
                  <View className={styles.momentHint}>{m.hintPayload.text}</View>
                )}
                <View className={styles.momentMeta}>
                  {fmtTime(m.at)} · 触发分 {m.triggerScore}
                </View>
                {m.responded ? (
                  <View className={styles.respondedTag}>已回应({m.responseType})</View>
                ) : m.rendered ? (
                  <View className={styles.respondRow}>
                    <View className={styles.respondBtn} onClick={() => handleRespond(m.momentId, 'click')}>有帮助</View>
                    <View className={styles.respondBtn} onClick={() => handleRespond(m.momentId, 'upgrade')}>去升级</View>
                    <View className={styles.respondBtn} onClick={() => handleRespond(m.momentId, 'ignore')}>忽略</View>
                  </View>
                ) : null}
              </View>
            ))}
          </View>
        )}

        {/* ============ 权益揭示 ============ */}
        {tab === 'benefits' && (
          <>
            <View className={styles.card}>
              <View className={styles.cardTitle}>升级权益对比</View>
              {preview ? (
                <>
                  {(preview.rows || []).map((r: any, i: number) => (
                    <View key={i} className={styles.previewRow}>
                      <View className={styles.previewName}>{r.name}</View>
                      <View className={styles.previewFrom}>{String(r.from)}</View>
                      <View className={styles.previewTo}>{String(r.to)}</View>
                    </View>
                  ))}
                  {preview.note ? (
                    <View className={styles.momentMeta}>{preview.note}</View>
                  ) : null}
                </>
              ) : (
                <View className={styles.empty}>
                  {loading ? '加载中…' : '您已是最高等级 L5 竹海 SVIP——无升级对比'}
                </View>
              )}
            </View>

            <View className={styles.card}>
              <View className={styles.sectionHead}>
                <View className={styles.cardTitle}>权益告知留痕</View>
                <View className={styles.sectionCount}>{reveals.length} 条</View>
              </View>
              {reveals.length === 0 ? (
                <View className={styles.empty}>暂无升级告知记录</View>
              ) : reveals.map((r: any, i: number) => (
                <View key={r.revealId ?? i} className={styles.revealItem}>
                  <View className={styles.kv}>
                    <View className={styles.kvKey}>
                      L{r.fromLevel} → L{r.toLevel} 升级告知
                    </View>
                    <View className={styles.kvVal}>{fmtTime(r.at)}</View>
                  </View>
                </View>
              ))}
            </View>
          </>
        )}

        {/* ============ 代办授权管理 ============ */}
        {tab === 'delegate' && (
          <>
            {predict && (
              <View className={styles.predictCard}>
                <View className={styles.predictLabel}>AI 预判您的下一步</View>
                <View className={styles.predictAction}>
                  {DELEGATE_ACTION_NAME[predict.topAction] || predict.topAction}
                </View>
                <View className={styles.predictReason}>
                  {predict.topReason} · {DELEGATE_RISK_NAME[predict.execMode] || predict.execMode}
                </View>
              </View>
            )}

            <View className={styles.card}>
              <View className={styles.cardTitle}>代办授权管理</View>
              <View className={styles.momentMeta} style={{ marginBottom: '16rpx' }}>
                授权后代办低风险操作; 资金类(支付/绑定)永不授权——宪法铁律
              </View>
              {Object.entries(ACTION_META).map(([action, meta]) => {
                const granted = grantedActions.has(action);
                return (
                  <View key={action} className={styles.actionItem}>
                    <View className={styles.actionInfo}>
                      <View className={styles.actionName}>
                        {DELEGATE_ACTION_NAME[action] || action}
                      </View>
                      <View className={styles.actionDesc}>{meta.desc}</View>
                    </View>
                    <View className={styles.actionBtns}>
                      {granted ? (
                        <>
                          <View
                            className={`${styles.miniBtn} ${busyAction === action ? styles.btnDisabled : styles.btnExec}`}
                            onClick={() => handleExecute(action)}
                          >
                            {busyAction === action ? '执行中' : '代办'}
                          </View>
                          <View
                            className={`${styles.miniBtn} ${busyAction === action ? styles.btnDisabled : styles.btnRevoke}`}
                            onClick={() => handleGrant(action, false)}
                          >
                            撤回
                          </View>
                        </>
                      ) : (
                        <View
                          className={`${styles.miniBtn} ${busyAction === action ? styles.btnDisabled : styles.btnGrant}`}
                          onClick={() => handleGrant(action, true)}
                        >
                          {busyAction === action ? '处理中' : '授权'}
                        </View>
                      )}
                    </View>
                  </View>
                );
              })}
              {execResult && (
                <View className={styles.execResult}>
                  执行结果: {String(execResult.executeResult || execResult.result || '')}
                  {execResult.note ? ` —— ${execResult.note}` : ''}
                </View>
              )}
            </View>

            <View className={styles.card}>
              <View className={styles.sectionHead}>
                <View className={styles.cardTitle}>代办执行留痕</View>
                <View className={styles.sectionCount}>{logs.length} 条</View>
              </View>
              {logs.length === 0 ? (
                <View className={styles.empty}>暂无代办执行记录</View>
              ) : logs.map(l => (
                <View key={l.logId} className={styles.logItem}>
                  <View className={styles.kv}>
                    <View className={styles.kvKey}>
                      {DELEGATE_ACTION_NAME[l.action] || l.action}
                    </View>
                    <View className={styles.kvVal}>{l.executeResult}</View>
                  </View>
                  <View className={styles.momentMeta}>{fmtTime(l.at)}</View>
                </View>
              ))}
            </View>
          </>
        )}

        <View className={styles.footerNote}>
          会员体验中心 · 授权显式性铁律 · 资金类永不代办
        </View>
      </ScrollView>
    </View>
  );
};

export default Member73CenterPage;
