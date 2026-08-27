/**
 * 权限中心 · 权限AI智能管理(P0 核心闭环)
 * 功能: 我的权限(到期倒计时/责任书签署) / 权限申请(AI预检) /
 *       我的申请(审批链时间线) / 待我审批(逐级审批) / 超管分配直授
 * 权责共存: 未签署责任书的权限阻断行使, 前端明示「享此权, 担此责」
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, Textarea, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import {
  PermAPI, PermNodeVO, PermGrantVO, PermRequestVO, PermLogVO, PermScoreVO,
} from '@/api/perm';
import { getSession } from '@/services/auth-service';

const STATUS_NAME: Record<string, string> = {
  active: '生效中', expired: '已到期', revoked: '已吊销', frozen: '已冻结',
};
const REQ_STATUS_NAME: Record<string, string> = {
  pending: '审批中', approved: '已通过', rejected: '已驳回',
  cancelled: '已撤回',
};
const SENS_STYLE: Record<string, string> = {
  normal: styles.tagActive, important: styles.tagWarn, core: styles.tagRevoked,
};
const SENS_NAME: Record<string, string> = {
  normal: '一般', important: '重要', core: '核心',
};
const ACTION_NAME: Record<string, string> = {
  grant_assign: '直授权限', grant_revoke: '吊销权限',
  grant_expire: '权限到期', duty_sign: '签署责任书',
  apply_submit: '提交申请', apply_approve: '同意申请',
  apply_reject: '驳回申请', apply_cancel: '撤回申请',
  deny_access: '越权拦截', role_create: '创建角色',
  use: '权限使用', risk_escalation: '越权升级冻结',
  risk_review: '风险复核', assessment_run: '信用考核',
};

const REWARD_NAME: Record<string, string> = {
  bonus: '奖励', none: '无', demote: '降权', freeze: '冻结追责',
};

type TabKey = 'grants' | 'apply' | 'mine' | 'approve' | 'scores' | 'admin';

const PermCenterPage: React.FC = () => {
  const [role, setRole] = useState('');
  const [tab, setTab] = useState<TabKey>('grants');
  const [grants, setGrants] = useState<PermGrantVO[]>([]);
  const [stages, setStages] = useState<Record<string, PermNodeVO[]>>({});
  const [selected, setSelected] = useState<PermNodeVO | null>(null);
  const [reason, setReason] = useState('');
  const [days, setDays] = useState<number>(0);
  const [mine, setMine] = useState<PermRequestVO[]>([]);
  const [toApprove, setToApprove] = useState<PermRequestVO[]>([]);
  const [assignMemberId, setAssignMemberId] = useState('');
  const [assignNode, setAssignNode] = useState<PermNodeVO | null>(null);
  const [adminGrants, setAdminGrants] = useState<PermGrantVO[]>([]);
  const [logs, setLogs] = useState<PermLogVO[]>([]);
  const [myScoreList, setMyScoreList] = useState<PermScoreVO[]>([]);
  const [riskSum, setRiskSum] = useState<{
    totalEvents: number; byLevel: Record<string, number>;
    pendingReview: PermLogVO[];
  } | null>(null);
  const [adminScoreList, setAdminScoreList] = useState<PermScoreVO[]>([]);
  const [loading, setLoading] = useState(true);

  const loadData = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [g, s, r] = await Promise.all([
        PermAPI.myGrants(), PermAPI.nodes(), PermAPI.requests(),
      ]);
      setGrants(g);
      setStages(s);
      setMine(r.mine);
      setToApprove(r.toApprove);
      setMyScoreList(await PermAPI.myScores());
      if (role === 'admin') {
        const [ag, lg, rs, as_] = await Promise.all([
          PermAPI.adminGrants(), PermAPI.adminLogs(30),
          PermAPI.riskSummary(), PermAPI.adminScores(),
        ]);
        setAdminGrants(ag);
        setLogs(lg);
        setRiskSum(rs);
        setAdminScoreList(as_);
      }
    } catch (e: any) {
      Taro.showToast({ title: e.message || '加载失败', icon: 'none' });
    } finally {
      setLoading(false);
    }
  }, [role]);

  useEffect(() => {
    const session = getSession();
    setRole(session?.role || '');
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refresh = () => loadData(true);

  // ============ 责任书签署(权责共存) ============
  const handleSignDuty = (grant: PermGrantVO) => {
    Taro.showModal({
      title: '签署责任书',
      content: `确认承担「${grant.nodeName}」权限对应责任?\n\n${(grant.duties || []).map((d, i) => `${i + 1}. ${d}`).join('\n')}`,
      confirmText: '签署',
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await PermAPI.signDuty(grant.grantId);
          Taro.showToast({ title: '已签署, 权限可行使', icon: 'success' });
          refresh();
        } catch (e: any) {
          Taro.showToast({ title: e.message || '签署失败', icon: 'none' });
        }
      },
    });
  };

  // ============ 提交申请 ============
  const handleSelectNode = (node: PermNodeVO) => {
    setSelected(node);
    setDays(node.defaultDays || 30);
  };

  const handleSubmit = async () => {
    if (!selected) {
      Taro.showToast({ title: '请先选择权限', icon: 'none' });
      return;
    }
    if (!reason || reason.trim().length < 5) {
      Taro.showToast({ title: '申请理由不少于5字', icon: 'none' });
      return;
    }
    try {
      await PermAPI.submitRequest(selected.code, reason.trim(), days);
      Taro.showToast({ title: '已提交, 等待逐级审批', icon: 'success' });
      setSelected(null);
      setReason('');
      setTab('mine');
      refresh();
    } catch (e: any) {
      Taro.showToast({ title: e.message || '提交失败', icon: 'none' });
    }
  };

  // ============ 审批 ============
  const handleApprove = (req: PermRequestVO, action: 'approve' | 'reject') => {
    Taro.showModal({
      title: action === 'approve' ? '同意该权限申请' : '驳回该权限申请',
      content: `权限: ${req.nodeName}\n申请人ID: ${req.applicantId}\n理由: ${req.reason}\n期限: ${req.durationDays}天`,
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await PermAPI.approve(req.requestId, action,
            action === 'approve' ? '同意' : '驳回');
          Taro.showToast({ title: '已处理', icon: 'success' });
          refresh();
        } catch (e: any) {
          Taro.showToast({ title: e.message || '操作失败', icon: 'none' });
        }
      },
    });
  };

  const handleCancel = async (req: PermRequestVO) => {
    try {
      await PermAPI.cancel(req.requestId);
      Taro.showToast({ title: '已撤回', icon: 'success' });
      refresh();
    } catch (e: any) {
      Taro.showToast({ title: e.message || '撤回失败', icon: 'none' });
    }
  };

  // ============ 超管直授/吊销 ============
  const handleAssign = async () => {
    const memberId = parseInt(assignMemberId, 10);
    if (!memberId || !assignNode) {
      Taro.showToast({ title: '请填写会员ID并选择权限', icon: 'none' });
      return;
    }
    try {
      await PermAPI.assign(memberId, assignNode.code);
      Taro.showToast({
        title: `已直授「${assignNode.name}」(待签责任书)`, icon: 'success',
      });
      setAssignMemberId('');
      setAssignNode(null);
      refresh();
    } catch (e: any) {
      Taro.showToast({ title: e.message || '直授失败', icon: 'none' });
    }
  };

  const handleRevoke = (grant: PermGrantVO) => {
    Taro.showModal({
      title: '吊销授权',
      content: `确认吊销 ${grant.memberNickname || `会员${grant.memberId}`} 的「${grant.nodeName}」权限?`,
      confirmColor: '#f53f3f',
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await PermAPI.revoke(grant.grantId);
          Taro.showToast({ title: '已吊销', icon: 'success' });
          refresh();
        } catch (e: any) {
          Taro.showToast({ title: e.message || '吊销失败', icon: 'none' });
        }
      },
    });
  };

  const handleSweep = async () => {
    try {
      const r = await PermAPI.expireSweep();
      Taro.showToast({ title: `已回收 ${r.swept} 项到期权限`, icon: 'success' });
      refresh();
    } catch (e: any) {
      Taro.showToast({ title: e.message || '操作失败', icon: 'none' });
    }
  };

  // ============ P1: 风险复核 / 月度考核 ============
  const handleRiskReview = (log: PermLogVO,
                            action: 'unfreeze' | 'revoke') => {
    Taro.showModal({
      title: action === 'unfreeze' ? '复核通过 · 解除冻结'
        : '维持吊销 · 全权限收回',
      content: `会员 ${log.memberId} · ${ACTION_NAME[log.action] || log.action}`
        + `${log.nodeCode ? ` · ${log.nodeCode}` : ''}\n`
        + `风险级: ${log.riskLevel}`,
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await PermAPI.riskReview(log.logId, action, '小程序复核');
          Taro.showToast({ title: '复核完成', icon: 'success' });
          refresh();
        } catch (e: any) {
          Taro.showToast({ title: e.message || '复核失败', icon: 'none' });
        }
      },
    });
  };

  const handleRunAssessment = () => {
    Taro.showModal({
      title: '触发月度权责考核',
      content: '将按信用分自动执行奖惩:\n≥90分 奖金¥200+500竹叶\n80-89分 奖金¥100+200竹叶\n40-59分 核心权限降权\n<40分 全权限冻结追责',
      success: async (res) => {
        if (!res.confirm) return;
        try {
          const r = await PermAPI.runAssessment(undefined, true);
          Taro.showToast({
            title: `已考核 ${r.assessed} 人`, icon: 'success',
          });
          refresh();
        } catch (e: any) {
          Taro.showToast({ title: e.message || '考核失败', icon: 'none' });
        }
      },
    });
  };

  // ============ 渲染辅助 ============
  const renderTimeline = (req: PermRequestVO) => {
    const currentStep = req.approvals.findIndex(s => !s.approvedBy);
    return (
      <View className={styles.timeline}>
        {req.approvals.map((step, idx) => {
          const rejected = !!step.rejected;
          const done = !!step.approvedBy && !rejected;
          const current = idx === currentStep && req.status === 'pending';
          const dotCls = rejected ? styles.stepDotReject
            : done ? styles.stepDotDone
              : current ? styles.stepDotCur : styles.stepDot;
          return (
            <View className={styles.step} key={idx}>
              <View className={dotCls}>
                {done ? '✓' : rejected ? '✕' : idx + 1}
              </View>
              <View className={styles.stepBody}>
                <Text className={styles.stepRole}>{step.role}</Text>
                <Text
                  className={`${styles.stepState} ${rejected ? styles.riskHigh : done ? styles.riskLow : styles.riskMedium}`}
                >
                  {rejected ? ' 已驳回' : done
                    ? (step.auto ? ' 自动通过' : ' 已同意')
                    : current ? ' 待审批' : ' 等待中'}
                </Text>
                {step.opinion ? (
                  <View className={styles.stepOpinion}>意见: {step.opinion}</View>
                ) : null}
              </View>
            </View>
          );
        })}
      </View>
    );
  };

  const allNodes: PermNodeVO[] = Object.values(stages).flat();

  const tabs: Array<{ key: TabKey; label: string; badge?: number }> = [
    { key: 'grants', label: '我的权限' },
    { key: 'apply', label: '申请权限' },
    { key: 'mine', label: '我的申请' },
    { key: 'approve', label: '待我审批', badge: toApprove.length },
    { key: 'scores', label: '信用分' },
  ];
  if (role === 'admin') {
    tabs.push({ key: 'admin', label: '超管工作台' });
  }

  return (
    <View className={styles.page}>
      {/* 头部 */}
      <View className={styles.header}>
        <View className={styles.headerTitle}>🔐 权限中心</View>
        <View className={styles.headerDesc}>
          申请审批 · 权责共存 · 限时回收 · 全程审计留痕
        </View>
      </View>

      {/* Tab */}
      <View className={styles.tabs}>
        {tabs.map(t => (
          <View
            key={t.key}
            className={`${styles.tab} ${tab === t.key ? styles.tabActive : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}{t.badge ? `(${t.badge})` : ''}
          </View>
        ))}
      </View>

      {loading && <View className={styles.empty}>加载中...</View>}

      {/* ============ 我的权限 ============ */}
      {tab === 'grants' && !loading && (
        <View className={styles.section}>
          <View className={styles.sectionTitle}>
            我的权限({grants.filter(g => g.status === 'active').length} 项生效)
          </View>
          {grants.length === 0 && (
            <View className={styles.empty}>
              暂无权限{'\n'}到「申请权限」按生产流程申请
            </View>
          )}
          {grants.map(g => (
            <View className={styles.grantCard} key={g.grantId}>
              <View className={styles.grantHead}>
                <Text className={styles.grantName}>{g.nodeName}</Text>
                <Text className={`${styles.tag} ${
                  g.status === 'active'
                    ? (g.daysLeft <= 3 ? styles.tagWarn : styles.tagActive)
                    : g.status === 'expired' ? styles.tagExpired
                      : styles.tagRevoked
                }}`}>
                  {g.status === 'active'
                    ? (g.dutySigned ? `${g.daysLeft}天后到期` : '待签责任书')
                    : STATUS_NAME[g.status]}
                </Text>
              </View>
              <View className={styles.grantMeta}>
                来源: {g.source === 'assign' ? '超管直授' : '申请审批'} ·
                敏感级: {SENS_NAME[g.sensitivity] || g.sensitivity}
              </View>
              <View className={styles.grantDuties}>
                <View className={styles.dutyTitle}>⚠️ 责任条款(权责共存)</View>
                {(g.duties || []).map((d, i) => (
                  <View className={styles.dutyItem} key={i}>{i + 1}. {d}</View>
                ))}
              </View>
              {g.status === 'active' && !g.dutySigned && (
                <View className={styles.btnRow}>
                  <View
                    className={styles.primaryBtn}
                    onClick={() => handleSignDuty(g)}
                  >签署责任书</View>
                </View>
              )}
            </View>
          ))}
        </View>
      )}

      {/* ============ 申请权限 ============ */}
      {tab === 'apply' && !loading && (
        <View className={styles.section}>
          <View className={styles.sectionTitle}>按生产流程选择权限</View>
          {selected && (
            <View className={styles.selectedNode}>
              <View className={styles.selectedNodeName}>{selected.name}</View>
              <View className={styles.selectedNodeMeta}>
                {selected.code} · 敏感级 {selected.sensitivityName} ·
                默认期限 {selected.defaultDays} 天
                {(selected.conflictWith || []).length > 0
                  ? ' · 与收款审核互斥(SoD)' : ''}
              </View>
            </View>
          )}
          {Object.entries(stages).map(([stageName, nodes]) => (
            <View className={styles.stageGroup} key={stageName}>
              <View className={styles.stageName}>{stageName}</View>
              <View className={styles.nodeGrid}>
                {nodes.map(n => (
                  <View
                    key={n.code}
                    className={`${styles.nodeChip} ${selected?.code === n.code ? styles.nodeChipActive : ''}`}
                    onClick={() => handleSelectNode(n)}
                  >{n.levelName}</View>
                ))}
              </View>
            </View>
          ))}
          {selected && (
            <>
              <Textarea
                className={styles.input}
                placeholder='申请理由(不少于5字)'
                value={reason}
                maxlength={200}
                onInput={e => setReason(e.detail.value)}
              />
              <View className={styles.durationRow}>
                {[7, 30, 90].map(d => (
                  <View
                    key={d}
                    className={`${styles.durationChip} ${days === d ? styles.durationChipActive : ''}`}
                    onClick={() => setDays(d)}
                  >{d}天</View>
                ))}
              </View>
              <View className={styles.btnRow}>
                <View className={styles.primaryBtn} onClick={handleSubmit}>
                  提交申请(AI 预检)
                </View>
                <View
                  className={styles.ghostBtn}
                  onClick={() => { setSelected(null); setReason(''); }}
                >取消</View>
              </View>
            </>
          )}
        </View>
      )}

      {/* ============ 我的申请 ============ */}
      {tab === 'mine' && !loading && (
        <View className={styles.section}>
          <View className={styles.sectionTitle}>我的申请({mine.length})</View>
          {mine.length === 0 && <View className={styles.empty}>暂无申请记录</View>}
          {mine.map(r => (
            <View className={styles.reqCard} key={r.requestId}>
              <View className={styles.reqHead}>
                <Text className={styles.grantName}>{r.nodeName}</Text>
                <Text className={`${styles.tag} ${
                  r.status === 'approved' ? styles.tagActive
                    : r.status === 'rejected' ? styles.tagRevoked
                      : r.status === 'pending' ? styles.tagWarn
                        : styles.tagExpired
                }`}>{REQ_STATUS_NAME[r.status]}</Text>
              </View>
              <View className={styles.reqReason}>理由: {r.reason}</View>
              <View className={styles.reqMeta}>
                期限 {r.durationDays} 天 · 敏感级 {r.sensitivityName} ·
                单号 {r.requestId}
              </View>
              {renderTimeline(r)}
              {r.status === 'pending' && (
                <View className={styles.btnRow}>
                  <View
                    className={`${styles.ghostBtn} ${styles.dangerBtn}`}
                    onClick={() => handleCancel(r)}
                  >撤回申请</View>
                </View>
              )}
            </View>
          ))}
        </View>
      )}

      {/* ============ 待我审批 ============ */}
      {tab === 'approve' && !loading && (
        <View className={styles.section}>
          <View className={styles.sectionTitle}>待我审批({toApprove.length})</View>
          {toApprove.length === 0 && (
            <View className={styles.empty}>暂无待审批的申请</View>
          )}
          {toApprove.map(r => (
            <View className={styles.reqCard} key={r.requestId}>
              <View className={styles.reqHead}>
                <Text className={styles.grantName}>{r.nodeName}</Text>
                <Text className={`${styles.tag} ${SENS_STYLE[r.sensitivity] || ''}`}>
                  {r.sensitivityName}
                </Text>
              </View>
              <View className={styles.reqReason}>理由: {r.reason}</View>
              <View className={styles.reqMeta}>
                申请人ID: {r.applicantId} · 期限 {r.durationDays} 天
              </View>
              {renderTimeline(r)}
              <View className={styles.btnRow}>
                <View
                  className={styles.primaryBtn}
                  onClick={() => handleApprove(r, 'approve')}
                >同意</View>
                <View
                  className={`${styles.ghostBtn} ${styles.dangerBtn}`}
                  onClick={() => handleApprove(r, 'reject')}
                >驳回</View>
              </View>
            </View>
          ))}
        </View>
      )}

      {/* ============ 信用分 ============ */}
      {tab === 'scores' && !loading && (
        <View className={styles.section}>
          <View className={styles.sectionTitle}>
            权责信用分({myScoreList.length} 期)
          </View>
          {myScoreList.length === 0 && (
            <View className={styles.empty}>
              暂无考核记录{'\n'}每月按合规率/履责度/审批尽责度自动计分{'\n'}
              ≥90分有奖金, &lt;40分权限冻结
            </View>
          )}
          {myScoreList.map(s => (
            <View className={styles.reqCard} key={s.scoreId}>
              <View className={styles.reqHead}>
                <Text className={styles.grantName}>{s.period} 期</Text>
                <Text className={`${styles.tag} ${
                  s.creditScore >= 90 ? styles.tagActive
                    : s.creditScore >= 60 ? styles.tagWarn
                      : styles.tagRevoked
                }`}>
                  {s.creditScore} 分 · {REWARD_NAME[s.rewardType]}
                </Text>
              </View>
              <View className={styles.reqMeta}>
                合规 {s.complianceScore}/40 · 履责 {s.dutyScore}/30 ·
                审批 {s.approvalScore}/20 · 基础 {s.reportScore}/10
              </View>
              {s.rewardType === 'bonus' && (
                <View className={styles.reqReason}>
                  奖励: ¥{s.rewardAmount} 入钱包收益 + {s.rewardPoints} 竹叶
                </View>
              )}
              {(s.executed || []).map((e, i) => (
                <View className={styles.stepOpinion} key={i}>· {e}</View>
              ))}
            </View>
          ))}
        </View>
      )}

      {/* ============ 超管工作台 ============ */}
      {tab === 'admin' && role === 'admin' && !loading && (
        <>
          <View className={styles.section}>
            <View className={styles.sectionTitle}>分配主要权限(直授)</View>
            <View className={styles.assignRow}>
              <Input
                className={styles.assignInput}
                type='number'
                placeholder='会员ID(如 1)'
                value={assignMemberId}
                onInput={e => setAssignMemberId(e.detail.value)}
              />
            </View>
            <View className={styles.stageGroup}>
              <View className={styles.nodeGrid}>
                {allNodes.map(n => (
                  <View
                    key={n.code}
                    className={`${styles.nodeChip} ${assignNode?.code === n.code ? styles.nodeChipActive : ''}`}
                    onClick={() => setAssignNode(n)}
                  >{n.name}</View>
                ))}
              </View>
            </View>
            <View className={styles.btnRow}>
              <View className={styles.primaryBtn} onClick={handleAssign}>
                直授{assignNode ? `「${assignNode.name}」` : '(SoD 硬拦截)'}
              </View>
              <View className={styles.ghostBtn} onClick={handleSweep}>
                回收到期权限
              </View>
              <View className={styles.ghostBtn} onClick={handleRunAssessment}>
                月度考核
              </View>
            </View>
          </View>

          {/* AI 风险监控面板 */}
          <View className={styles.section}>
            <View className={styles.sectionTitle}>AI 风险监控</View>
            {riskSum && (
              <View className={styles.reqMeta}>
                事件总数 {riskSum.totalEvents} · 低 {riskSum.byLevel.low || 0} ·
                中 {riskSum.byLevel.medium || 0} ·
                高 {riskSum.byLevel.high || 0} ·
                极高 {riskSum.byLevel.extreme || 0}
              </View>
            )}
            {(riskSum?.pendingReview || []).length === 0 && (
              <View className={styles.empty}>暂无待复核的高危事件</View>
            )}
            {(riskSum?.pendingReview || []).map(l => (
              <View className={styles.reqCard} key={l.logId}>
                <View className={styles.reqHead}>
                  <Text className={styles.grantName}>
                    会员 {l.memberId} · {ACTION_NAME[l.action] || l.action}
                  </Text>
                  <Text className={`${styles.tag} ${
                    l.riskLevel === 'extreme' ? styles.tagRevoked
                      : styles.tagWarn
                  }`}>
                    {l.riskLevel === 'extreme' ? '极高' : '高危'}
                  </Text>
                </View>
                <View className={styles.reqMeta}>
                  {l.nodeCode || '-'} · 已处置: {l.handled || 'none'}
                </View>
                <View className={styles.btnRow}>
                  <View
                    className={styles.primaryBtn}
                    onClick={() => handleRiskReview(l, 'unfreeze')}
                  >复核通过·解冻</View>
                  <View
                    className={`${styles.ghostBtn} ${styles.dangerBtn}`}
                    onClick={() => handleRiskReview(l, 'revoke')}
                  >维持吊销</View>
                </View>
              </View>
            ))}
          </View>

          {/* 考核记录 */}
          <View className={styles.section}>
            <View className={styles.sectionTitle}>
              考核记录({adminScoreList.length})
            </View>
            {adminScoreList.length === 0 && (
              <View className={styles.empty}>
                暂无考核记录, 点击上方「月度考核」触发
              </View>
            )}
            {adminScoreList.map(s => (
              <View className={styles.logItem} key={s.scoreId}>
                <View>
                  <View className={styles.logAction}>
                    {s.memberNickname || `会员${s.memberId}`} ·
                    {s.period} 期 · {REWARD_NAME[s.rewardType]}
                  </View>
                  <View className={styles.logMeta}>{s.aiReport}</View>
                </View>
                <View className={`${
                  s.creditScore >= 90 ? styles.riskLow
                    : s.creditScore >= 60 ? styles.riskMedium
                      : styles.riskHigh
                }`}>{s.creditScore}分</View>
              </View>
            ))}
          </View>

          <View className={styles.section}>
            <View className={styles.sectionTitle}>
              全部授权({adminGrants.length})
            </View>
            {adminGrants.map(g => (
              <View className={styles.grantCard} key={g.grantId}>
                <View className={styles.grantHead}>
                  <Text className={styles.grantName}>
                    {g.memberNickname || `会员${g.memberId}`} · {g.nodeName}
                  </Text>
                  <Text className={`${styles.tag} ${
                    g.status === 'active' ? styles.tagActive
                      : g.status === 'expired' ? styles.tagExpired
                        : styles.tagRevoked
                  }`}>
                    {STATUS_NAME[g.status]}{g.status === 'active' && !g.dutySigned ? '(待签)' : ''}
                  </Text>
                </View>
                {g.status === 'active' && (
                  <View className={styles.btnRow}>
                    <View
                      className={`${styles.ghostBtn} ${styles.dangerBtn}`}
                      onClick={() => handleRevoke(g)}
                    >吊销</View>
                  </View>
                )}
              </View>
            ))}
          </View>

          <View className={styles.section}>
            <View className={styles.sectionTitle}>AI 监控审计日志</View>
            {logs.map(l => (
              <View className={styles.logItem} key={l.logId}>
                <View>
                  <View className={styles.logAction}>
                    {ACTION_NAME[l.action] || l.action}
                    {l.nodeCode ? ` · ${l.nodeCode}` : ''}
                  </View>
                  <View className={styles.logMeta}>会员 {l.memberId}</View>
                </View>
                <View className={`${styles.logMeta} ${
                  l.riskLevel === 'low' ? styles.riskLow
                    : l.riskLevel === 'medium' ? styles.riskMedium
                      : styles.riskHigh
                }`}>
                  {l.riskLevel === 'low' ? '低风险' : l.riskLevel === 'medium' ? '中风险' : '高风险'}
                </View>
              </View>
            ))}
          </View>
        </>
      )}
    </View>
  );
};

export default PermCenterPage;
