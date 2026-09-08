/**
 * AI 智能客服 · 会话列表 + 聊天窗口
 * 数据来源: 后端 /api/chat/*(AI 优先接待, 知识库自动回复, 触发规则转人工)
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  ChatAPI, ChatSessionVO, ChatMessageVO,
  SESSION_TYPES, sessionStatusName,
} from '@/api/chat';
import { requireLogin } from '@/services/auth-service';

// 会话状态 → 徽标样式
const STATUS_BADGE: Record<string, string> = {
  ai_chatting: 'ai',
  human_chatting: 'human',
  transferring: 'human',
  waiting: 'human',
  ended: 'ended',
  archived: 'ended',
};

// 会话类型显示名
const TYPE_NAME: Record<string, string> = SESSION_TYPES.reduce(
  (acc, t) => ({ ...acc, [t.key]: t.label }), {} as Record<string, string>
);

const SENDER_NAME: Record<string, string> = {
  ai: 'AI 助手',
  customer_service: '人工客服',
  system: '系统',
};

const formatTime = (t?: string): string => {
  if (!t) return '';
  return t.slice(0, 16).replace('T', ' ');
};

const ChatPage: React.FC = () => {
  // 视图模式: list=会话列表, chat=聊天窗口
  const [view, setView] = useState<'list' | 'chat'>('list');
  const [sessions, setSessions] = useState<ChatSessionVO[]>([]);
  const [activeSession, setActiveSession] = useState<ChatSessionVO | null>(null);
  const [messages, setMessages] = useState<ChatMessageVO[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  // 新建会话: 类型选择弹层
  const [newPanel, setNewPanel] = useState(false);
  const [newType, setNewType] = useState('presale');
  // 已评价标记(前端态, 防重复弹)
  const [rated, setRated] = useState(false);
  // 滚动锚点(state 驱动, ScrollView scrollIntoView 生效)
  const [scrollAnchor, setScrollAnchor] = useState('');

  const loadSessions = useCallback(async () => {
    try {
      const list = await ChatAPI.mySessions();
      setSessions(list);
    } catch (e) {
      console.warn('[chat] 会话列表加载失败:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (requireLogin()) {
      loadSessions();
    } else {
      setLoading(false);
    }
  }, [loadSessions]);

  // 消息变化 → 锚定最后一条(驱动滚动到底部)
  useEffect(() => {
    if (messages.length > 0) {
      setScrollAnchor(`msg-${messages.length - 1}`);
    }
  }, [messages]);

  // 打开会话
  const openSession = async (s: ChatSessionVO) => {
    setView('chat');
    setActiveSession(s);
    setRated(Boolean(s.satisfaction && s.satisfaction > 0));
    setMessages([]);
    try {
      const msgs = await ChatAPI.messages(s.sessionId);
      setMessages(msgs);
    } catch (e) {
      console.warn('[chat] 消息加载失败:', e);
    }
  };

  // 新建会话(类型确认 + 酒类合规 18 岁声明)
  const handleNew = async () => {
    const typeLabel = TYPE_NAME[newType] || newType;
    const res = await Taro.showModal({
      title: '开始新咨询',
      content: `类型: ${typeLabel}\n本人已满 18 周岁, 知悉酒类产品咨询相关事项。`,
      confirmText: '开始咨询',
    });
    if (!res.confirm) return;
    try {
      const s = await ChatAPI.createSession({ sessionType: newType, ageConfirmed: true });
      setNewPanel(false);
      await loadSessions();
      await openSession(s);
    } catch (e) {
      console.warn('[chat] 创建会话失败:', e);
    }
  };

  // 发送消息(AI 自动回复)
  const handleSend = async () => {
    const content = input.trim();
    if (!content || sending) return;
    if (activeSession?.status === 'ended') {
      Taro.showToast({ title: '会话已结束, 请开始新咨询', icon: 'none' });
      return;
    }
    setSending(true);
    // 乐观追加用户消息
    const optimistic: ChatMessageVO = {
      id: -Date.now(),
      sessionId: activeSession!.sessionId,
      senderType: 'user',
      senderId: 0,
      messageType: 'text',
      content,
      aiConfidence: null,
      createdAt: new Date().toISOString(),
    };
    setMessages(prev => [...prev, optimistic]);
    setInput('');
    try {
      const result = await ChatAPI.send(activeSession!.sessionId, content);
      // 以服务端为准重载(AI 回复/系统提示/转人工通知全量同步)
      const msgs = await ChatAPI.messages(activeSession!.sessionId);
      setMessages(msgs);
      if (result.transferred) {
        const reason = result.transferTrigger?.reason || '已为您转接人工客服';
        Taro.showToast({ title: reason, icon: 'none', duration: 2500 });
      }
      // 转人工后刷新会话状态
      if (result.transferred && activeSession) {
        const updated = await ChatAPI.sessionDetail(activeSession.sessionId);
        setActiveSession(updated);
      }
    } catch (e) {
      // 失败撤回乐观消息
      setMessages(prev => prev.filter(m => m.id !== optimistic.id));
      setInput(content);
      console.warn('[chat] 发送失败:', e);
    } finally {
      setSending(false);
    }
  };

  // 转人工
  const handleTransfer = async () => {
    if (!activeSession || sending) return;
    try {
      await ChatAPI.transfer(activeSession.sessionId, '用户主动转人工');
      const updated = await ChatAPI.sessionDetail(activeSession.sessionId);
      setActiveSession(updated);
      const msgs = await ChatAPI.messages(activeSession.sessionId);
      setMessages(msgs);
      Taro.showToast({ title: '已转接人工客服', icon: 'success' });
    } catch (e) {
      console.warn('[chat] 转人工失败:', e);
    }
  };

  // 关闭会话 → 满意度评价
  const handleClose = async () => {
    if (!activeSession || activeSession.status === 'ended') return;
    const res = await Taro.showModal({
      title: '结束咨询',
      content: '确定结束本次咨询吗? 结束后可对服务进行评价。',
    });
    if (!res.confirm) return;
    try {
      await ChatAPI.close(activeSession.sessionId);
      const updated = await ChatAPI.sessionDetail(activeSession.sessionId);
      setActiveSession(updated);
      const msgs = await ChatAPI.messages(activeSession.sessionId);
      setMessages(msgs);
      setRated(false);
      await handleRate();
      loadSessions();
    } catch (e) {
      console.warn('[chat] 关闭失败:', e);
    }
  };

  // 满意度评价
  const handleRate = async () => {
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
    const score = tapIndex + 1;
    try {
      await ChatAPI.rate(activeSession!.sessionId, score);
      setRated(true);
      Taro.showToast({ title: '感谢您的评价', icon: 'success' });
    } catch (e) {
      console.warn('[chat] 评价失败:', e);
    }
  };

  // ============ 会话列表视图 ============
  const renderList = () => (
    <View className={styles.page}>
      <NavBar title="在线客服" />
      <ScrollView scrollY className={styles.scrollView}>
        <View className={styles.heroCard}>
          <View className={styles.heroTitle}>AI 智能客服</View>
          <View className={styles.heroDesc}>知识库秒级应答 · 7×24 小时在线 · 复杂问题无缝转人工</View>
        </View>

        <View className={styles.newBtn} onClick={() => setNewPanel(true)}>
          ＋ 开始新咨询
        </View>

        <View className={styles.card}>
          <View className={styles.cardTitle}>我的咨询</View>
          {loading ? (
            <View className={styles.empty}>加载中...</View>
          ) : sessions.length === 0 ? (
            <View className={styles.empty}>暂无咨询记录</View>
          ) : (
            sessions.map(s => (
              <View
                key={s.sessionId}
                className={styles.sessionRow}
                onClick={() => openSession(s)}
              >
                <View className={styles.sessionLeft}>
                  <View className={styles.sessionType}>
                    {TYPE_NAME[s.sessionType] || s.sessionType}
                  </View>
                  <View className={styles.sessionMeta}>
                    {formatTime(s.createdAt)} · 共 {s.unresolvedCount} 次待解决
                  </View>
                </View>
                <View className={styles.sessionRight}>
                  <View className={`${styles.badge} ${styles[STATUS_BADGE[s.status] || 'ended']}`}>
                    {sessionStatusName(s.status)}
                  </View>
                  {s.satisfaction > 0 && (
                    <View className={styles.sessionStars}>{'⭐'.repeat(Math.min(5, s.satisfaction))}</View>
                  )}
                  <View className={styles.arrow}>›</View>
                </View>
              </View>
            ))
          )}
        </View>
        <View className={styles.bottomSpacer} />
      </ScrollView>

      {/* 新建会话类型选择弹层 */}
      {newPanel && (
        <View className={styles.mask} onClick={() => setNewPanel(false)}>
          <View className={styles.panel} onClick={(e) => e.stopPropagation()}>
            <View className={styles.panelTitle}>选择咨询类型</View>
            <View className={styles.typeGrid}>
              {SESSION_TYPES.map(t => (
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
            <View className={styles.complianceTip}>
              根据《未成年人保护法》, 酒类产品咨询仅面向已满 18 周岁用户
            </View>
            <View className={styles.panelBtn} onClick={handleNew}>开始咨询</View>
          </View>
        </View>
      )}
    </View>
  );

  // ============ 聊天窗口视图 ============
  const renderChat = () => {
    const s = activeSession;
    if (!s) return null;
    const ended = s.status === 'ended';
    // 已在人工/转接中/排队: 隐藏转人工入口(重复转接后端 409)
    const humanMode = ['human_chatting', 'transferring', 'waiting'].includes(s.status);
    return (
      <View className={`${styles.page} ${styles.chatPage}`}>
        <NavBar
          title={TYPE_NAME[s.sessionType] || '在线客服'}
          onBack={() => { setView('list'); loadSessions(); }}
        />
        {/* 状态条 */}
        <View className={`${styles.statusBar} ${ended ? styles.statusEnded : ''}`}>
          <Text className={styles.statusText}>
            {ended ? '会话已结束' : sessionStatusName(s.status)}
          </Text>
          {!ended && !humanMode && (
            <Text className={styles.statusAction} onClick={handleTransfer}>转人工</Text>
          )}
          {!ended && (
            <Text className={styles.statusAction} onClick={handleClose}>结束咨询</Text>
          )}
          {ended && !rated && (
            <Text className={styles.statusAction} onClick={handleRate}>评价</Text>
          )}
        </View>

        {/* 消息流 */}
        <ScrollView
          scrollY
          className={styles.msgScroll}
          scrollIntoView={scrollAnchor}
          scrollWithAnimation
        >
          <View className={styles.msgList}>
            {messages.map((m, i) => {
              const isUser = m.senderType === 'user';
              const showName = !isUser && m.senderType !== 'system';
              return (
                <View key={m.id} id={`msg-${i}`} className={`${styles.msgRow} ${isUser ? styles.msgRight : styles.msgLeft}`}>
                  <View className={styles.msgBubbleWrap}>
                    {showName && (
                      <View className={styles.msgSender}>{SENDER_NAME[m.senderType] || m.senderType}</View>
                    )}
                    <View
                      className={`${styles.msgBubble} ${isUser ? styles.bubbleUser : m.senderType === 'system' ? styles.bubbleSystem : styles.bubbleOther}`}
                    >
                      {m.content}
                    </View>
                  </View>
                </View>
              );
            })}
            {messages.length === 0 && (
              <View className={styles.empty}>正在加载消息...</View>
            )}
          </View>
          <View className={styles.bottomSpacer} />
        </ScrollView>

        {/* 已结束评价提示 */}
        {ended && (
          <View className={styles.rateBar}>
            {rated ? (
              <Text className={styles.rateDone}>已评价, 感谢反馈</Text>
            ) : (
              <Text className={styles.rateLink} onClick={handleRate}>本次服务已结束, 点击评价 ⭐</Text>
            )}
            <Text className={styles.rateLink} onClick={() => Taro.navigateTo({ url: '/pages/tickets/index' })}>· 提交工单</Text>
          </View>
        )}

        {/* 输入区 */}
        <View className={styles.inputBar}>
          <Input
            className={styles.input}
            value={input}
            onInput={(e) => setInput(e.detail.value)}
            onConfirm={handleSend}
            disabled={ended}
            placeholder={ended ? '会话已结束' : '请输入您的问题, 回复"转人工"可转接客服'}
            placeholderClass={styles.placeholder}
            confirmType="send"
          />
          <View
            className={`${styles.sendBtn} ${(!input.trim() || sending || ended) ? styles.sendDisabled : ''}`}
            onClick={handleSend}
          >
            {sending ? '...' : '发送'}
          </View>
        </View>
      </View>
    );
  };

  return view === 'chat' ? renderChat() : renderList();
};

export default ChatPage;
