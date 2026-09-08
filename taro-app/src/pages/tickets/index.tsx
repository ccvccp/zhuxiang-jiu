/**
 * 客服工单 · 我的工单列表 / 创建 / 详情(处理记录+补充+确认评价)
 * 数据来源: 后端 /api/ticket/*(投诉/VIP 自动升紧急, SLA 动态标记)
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Textarea, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import { TicketAPI, TicketVO, TicketReplyVO, TICKET_TYPES, ticketStatusName } from '@/api/ticket';
import { requireLogin } from '@/services/auth-service';

// 状态筛选 tab
const STATUS_TABS = [
  { key: '', label: '全部' },
  { key: 'pending', label: '待分配' },
  { key: 'processing', label: '处理中' },
  { key: 'wait_confirm', label: '待确认' },
  { key: 'resolved', label: '已解决' },
];

// 状态 → 徽标样式
const STATUS_CLS: Record<string, string> = {
  pending: 'pending',
  processing: 'processing',
  wait_confirm: 'confirm',
  resolved: 'resolved',
  closed: 'closed',
};

// 优先级显示
const PRIORITY_NAME: Record<string, string> = {
  urgent: '紧急', high: '高', medium: '中', low: '低',
};

const TYPE_LABEL: Record<string, string> = TICKET_TYPES.reduce(
  (acc, t) => ({ ...acc, [t.key]: t.label }), {} as Record<string, string>
);

const formatTime = (t?: string): string => {
  if (!t) return '';
  return t.slice(0, 16).replace('T', ' ');
};

const TicketsPage: React.FC = () => {
  // 视图: list=列表, create=创建, detail=详情
  const [view, setView] = useState<'list' | 'create' | 'detail'>('list');
  const [tickets, setTickets] = useState<TicketVO[]>([]);
  const [filter, setFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  // 创建表单
  const [newType, setNewType] = useState('aftersale');
  const [newDesc, setNewDesc] = useState('');
  const [newOrderId, setNewOrderId] = useState('');
  // 详情态
  const [detail, setDetail] = useState<(TicketVO & { replies: TicketReplyVO[] }) | null>(null);
  const [replyInput, setReplyInput] = useState('');

  const loadTickets = useCallback(async (status?: string) => {
    try {
      const list = await TicketAPI.myList(status || undefined);
      setTickets(list);
    } catch (e) {
      console.warn('[ticket] 工单列表加载失败:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (requireLogin()) {
      loadTickets();
    } else {
      setLoading(false);
    }
  }, [loadTickets]);

  // 切换筛选
  const switchFilter = (key: string) => {
    setFilter(key);
    setLoading(true);
    loadTickets(key);
  };

  // 创建工单
  const handleCreate = async () => {
    if (submitting) return;
    if (!newDesc.trim()) {
      Taro.showToast({ title: '请描述您的问题', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const t = await TicketAPI.create({
        type: newType,
        description: newDesc.trim(),
        orderId: newOrderId.trim() || undefined,
      });
      Taro.showToast({ title: `工单已提交 ${t.ticketNo}`, icon: 'success', duration: 2000 });
      setView('list');
      setNewDesc('');
      setNewOrderId('');
      setFilter('');
      setLoading(true);
      await loadTickets();
      // 打开新工单详情
      await openDetail(t.ticketNo);
    } catch (e) {
      console.warn('[ticket] 创建失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 打开工单详情
  const openDetail = async (ticketNo: string) => {
    setView('detail');
    setReplyInput('');
    try {
      const d = await TicketAPI.myDetail(ticketNo);
      setDetail(d);
    } catch (e) {
      console.warn('[ticket] 详情加载失败:', e);
    }
  };

  // 用户补充
  const handleReply = async () => {
    const content = replyInput.trim();
    if (!content || !detail || submitting) return;
    if (detail.status === 'closed') {
      Taro.showToast({ title: '工单已关闭, 不可补充', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      await TicketAPI.myReply(detail.ticketNo, content);
      setReplyInput('');
      const d = await TicketAPI.myDetail(detail.ticketNo);
      setDetail(d);
      Taro.showToast({ title: '已补充', icon: 'success' });
    } catch (e) {
      console.warn('[ticket] 补充失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 确认解决 + 满意度
  const handleConfirm = async () => {
    if (!detail) return;
    let tapIndex = -1;
    try {
      const res = await Taro.showActionSheet({
        itemList: ['⭐ 很不满意', '⭐⭐ 不满意', '⭐⭐⭐ 一般', '⭐⭐⭐⭐ 满意', '⭐⭐⭐⭐⭐ 很满意'],
      });
      tapIndex = res.tapIndex;
    } catch (_) {
      return; // 用户取消
    }
    if (tapIndex < 0) return;
    try {
      await TicketAPI.confirm(detail.ticketNo, tapIndex + 1);
      Taro.showToast({ title: '已确认解决, 感谢评价', icon: 'success' });
      const d = await TicketAPI.myDetail(detail.ticketNo);
      setDetail(d);
      loadTickets(filter);
    } catch (e) {
      console.warn('[ticket] 确认失败:', e);
    }
  };

  // ============ 列表视图 ============
  const renderList = () => (
    <View className={styles.page}>
      <NavBar title="我的工单" />
      <ScrollView scrollY className={styles.scrollView}>
        <View className={styles.newBtn} onClick={() => setView('create')}>
          ＋ 提交工单
        </View>

        {/* 状态筛选 */}
        <ScrollView scrollX className={styles.tabs} showScrollbar={false}>
          {STATUS_TABS.map(tab => (
            <View
              key={tab.key}
              className={`${styles.tab} ${filter === tab.key ? styles.tabActive : ''}`}
              onClick={() => switchFilter(tab.key)}
            >
              {tab.label}
            </View>
          ))}
        </ScrollView>

        {/* 工单列表 */}
        {loading ? (
          <View className={styles.empty}>加载中...</View>
        ) : tickets.length === 0 ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>📋</View>
            <View>暂无工单, 有问题随时提交</View>
          </View>
        ) : (
          <View className={styles.card}>
            {tickets.map(t => (
              <View
                key={t.ticketNo}
                className={styles.ticketRow}
                onClick={() => openDetail(t.ticketNo)}
              >
                <View className={styles.ticketLeft}>
                  <View className={styles.ticketType}>
                    {TYPE_LABEL[t.type] || t.type}
                    {t.priority === 'urgent' && <Text className={styles.urgentTag}>紧急</Text>}
                    {t.overdue && <Text className={styles.overdueTag}>超时</Text>}
                  </View>
                  <View className={styles.ticketDesc}>{t.description}</View>
                  <View className={styles.ticketMeta}>
                    {t.ticketNo} · {formatTime(t.createdAt)}
                  </View>
                </View>
                <View className={styles.ticketRight}>
                  <View className={`${styles.badge} ${styles[STATUS_CLS[t.status] || 'closed']}`}>
                    {ticketStatusName(t.status)}
                  </View>
                  {t.satisfaction != null && t.satisfaction > 0 && (
                    <View className={styles.stars}>{'⭐'.repeat(Math.min(5, t.satisfaction))}</View>
                  )}
                  <View className={styles.arrow}>›</View>
                </View>
              </View>
            ))}
          </View>
        )}
        <View className={styles.bottomSpacer} />
      </ScrollView>
    </View>
  );

  // ============ 创建视图 ============
  const renderCreate = () => (
    <View className={styles.page}>
      <NavBar title="提交工单" onBack={() => setView('list')} />
      <ScrollView scrollY className={styles.scrollView}>
        {/* 工单类型 */}
        <View className={styles.card}>
          <View className={styles.cardTitle}>工单类型</View>
          <View className={styles.typeGrid}>
            {TICKET_TYPES.map(t => (
              <View
                key={t.key}
                className={`${styles.typeItem} ${newType === t.key ? styles.typeActive : ''}`}
                onClick={() => setNewType(t.key)}
              >
                <View className={styles.typeLabel}>{t.label}</View>
                <View className={styles.typeDesc}>{t.desc}</View>
              </View>
            ))}
          </View>
        </View>

        {/* 问题描述 */}
        <View className={styles.card}>
          <View className={styles.cardTitle}>问题描述</View>
          <Textarea
            value={newDesc}
            onInput={(e) => setNewDesc(e.detail.value)}
            maxlength={2000}
            placeholder='请详细描述您遇到的问题(必填)'
            className={styles.textarea}
          />
        </View>

        {/* 关联订单 */}
        <View className={styles.card}>
          <View className={styles.cardTitle}>关联订单号(选填)</View>
          <Input
            className={styles.input}
            value={newOrderId}
            onInput={(e) => setNewOrderId(e.detail.value)}
            placeholder='如 OD20260908000001'
            placeholderClass={styles.placeholder}
          />
        </View>

        <View className={styles.note}>
          投诉工单将自动升级为紧急优先级, 由专属客服跟进处理
        </View>

        <View className={styles.submitBtn} onClick={handleCreate}>
          {submitting ? '提交中...' : '提交工单'}
        </View>
        <View className={styles.bottomSpacer} />
      </ScrollView>
    </View>
  );

  // ============ 详情视图 ============
  const renderDetail = () => {
    if (!detail) return null;
    const closed = detail.status === 'closed';
    return (
      <View className={styles.page}>
        <NavBar
          title="工单详情"
          onBack={() => { setView('list'); loadTickets(filter); }}
        />
        <ScrollView scrollY className={styles.scrollView}>
          {/* 工单信息卡 */}
          <View className={styles.card}>
            <View className={styles.detailHeader}>
              <View className={styles.badgeLarge}>
                {ticketStatusName(detail.status)}
              </View>
              <View className={styles.detailNo}>{detail.ticketNo}</View>
            </View>
            <View className={styles.detailRow}>
              <Text className={styles.detailLabel}>类型</Text>
              <Text className={styles.detailValue}>{TYPE_LABEL[detail.type] || detail.type}</Text>
            </View>
            <View className={styles.detailRow}>
              <Text className={styles.detailLabel}>优先级</Text>
              <Text className={styles.detailValue}>{PRIORITY_NAME[detail.priority] || detail.priority}</Text>
            </View>
            {detail.handlerName ? (
              <View className={styles.detailRow}>
                <Text className={styles.detailLabel}>处理客服</Text>
                <Text className={styles.detailValue}>{detail.handlerName}</Text>
              </View>
            ) : null}
            <View className={styles.detailRow}>
              <Text className={styles.detailLabel}>创建时间</Text>
              <Text className={styles.detailValue}>{formatTime(detail.createdAt)}</Text>
            </View>
            {detail.orderId ? (
              <View className={styles.detailRow}>
                <Text className={styles.detailLabel}>关联订单</Text>
                <Text className={styles.detailValue}>{detail.orderId}</Text>
              </View>
            ) : null}
            <View className={styles.detailDesc}>{detail.description}</View>
            {detail.resolution ? (
              <View className={styles.resolutionBox}>
                <View className={styles.resolutionTitle}>解决方案</View>
                <View className={styles.resolutionText}>{detail.resolution}</View>
              </View>
            ) : null}
          </View>

          {/* 处理记录 */}
          <View className={styles.card}>
            <View className={styles.cardTitle}>处理记录</View>
            {detail.replies.length === 0 ? (
              <View className={styles.empty}>暂无处理记录, 客服将尽快跟进</View>
            ) : (
              detail.replies.map(r => (
                <View key={r.id} className={styles.replyItem}>
                  <View className={styles.replyHead}>
                    <Text className={r.replierRole === 'staff' ? styles.replyStaff : styles.replyUser}>
                      {r.replierRole === 'staff' ? '客服' : '我'}
                    </Text>
                    <Text className={styles.replyTime}>{formatTime(r.createdAt)}</Text>
                  </View>
                  <View className={styles.replyContent}>{r.content}</View>
                </View>
              ))
            )}
          </View>

          {/* 待确认 → 确认按钮 */}
          {detail.status === 'wait_confirm' && (
            <View className={styles.confirmBtn} onClick={handleConfirm}>
              确认已解决并评价
            </View>
          )}
          {detail.status === 'resolved' && detail.satisfaction != null && detail.satisfaction > 0 && (
            <View className={styles.resolvedNote}>
              已确认解决 · 满意度 {'⭐'.repeat(Math.min(5, detail.satisfaction))}
            </View>
          )}
          {closed && (
            <View className={styles.resolvedNote}>工单已关闭</View>
          )}

          {/* 补充输入 */}
          {!closed && (
            <View className={styles.card}>
              <View className={styles.cardTitle}>补充信息</View>
              <View className={styles.replyBar}>
                <Input
                  className={styles.input}
                  value={replyInput}
                  onInput={(e) => setReplyInput(e.detail.value)}
                  onConfirm={handleReply}
                  placeholder='补充说明问题细节...'
                  placeholderClass={styles.placeholder}
                  confirmType="send"
                />
                <View
                  className={`${styles.sendBtn} ${(!replyInput.trim() || submitting) ? styles.sendDisabled : ''}`}
                  onClick={handleReply}
                >
                  发送
                </View>
              </View>
            </View>
          )}
          <View className={styles.bottomSpacer} />
        </ScrollView>
      </View>
    );
  };

  return view === 'create' ? renderCreate() : view === 'detail' ? renderDetail() : renderList();
};

export default TicketsPage;
