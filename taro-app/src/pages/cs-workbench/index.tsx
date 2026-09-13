/**
 * 人工客服工作台 · 四页签: 排队 → 我的会话 → 聊天窗 → 统计
 * 数据来源: 后端 /api/chat/cs/*(admin 头鉴权)
 * 口径: 人工回复走敏感词过滤不触发 AI · 聊天窗 3s 增量轮询 ·
 *       AI 智能层灰度(CHAT_LLM_MODE)不影响人工侧
 */
import React, { useState, useEffect, useRef } from 'react';
import { View, Text, ScrollView, Input } from '@tarojs/components';
import Taro, { useDidShow, useDidHide } from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  CsChatAPI, ChatSessionVO, ChatMessageVO,
  queueStatusName, sessionStatusName,
} from '@/api/cs-chat';

type Tab = 'queue' | 'mine' | 'chat' | 'stats';

const TABS: { key: Tab; label: string }[] = [
  { key: 'queue', label: '排队' },
  { key: 'mine', label: '我的会话' },
  { key: 'chat', label: '聊天窗' },
  { key: 'stats', label: '统计' },
];

// 排队状态筛选
const QUEUE_FILTERS = ['human_chatting', 'transferring', 'waiting'];

// 发送方显示名
const SENDER_NAME: Record<string, string> = {
  user: '会员',
  ai: 'AI 助手',
  customer_service: '客服',
  system: '系统',
};

// 聊天窗 3s 增量轮询
const POLL_INTERVAL = 3000;

const formatTime = (t?: string): string => {
  if (!t) return '';
  return t.slice(0, 16).replace('T', ' ');
};

const pctStr = (v: any): string =>
  v == null ? '—' : `${(Number(v) * 100).toFixed(1)}%`;

const errMsg = (e: any): string => String(e?.message || e).slice(0, 40);

const CsWorkbenchPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('queue');
  const [loading, setLoading] = useState(true);
  // 排队页
  const [qFilter, setQFilter] = useState('human_chatting');
  const [queue, setQueue] = useState<ChatSessionVO[]>([]);
  // 我的会话页(human_chatting 全量, 客服视角)
  const [mine, setMine] = useState<ChatSessionVO[]>([]);
  // 聊天窗
  const [activeSession, setActiveSession] = useState<ChatSessionVO | null>(null);
  const [messages, setMessages] = useState<ChatMessageVO[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [scrollAnchor, setScrollAnchor] = useState('');
  // 统计页
  const [stats, setStats] = useState<any>(null);
  const [mode, setMode] = useState<any>(null);
  // 轮询游标 + 页面可见性
  const lastMessageId = useRef(0);
  const [visible, setVisible] = useState(true);
  useDidShow(() => setVisible(true));
  useDidHide(() => setVisible(false));

  const loadQueue = async (status = qFilter) => {
    try {
      setQueue(await CsChatAPI.queue(status));
    } catch (e) {
      console.warn('[cs] 排队列表加载失败:', e);
    }
  };

  const loadMine = async () => {
    try {
      setMine(await CsChatAPI.queue('human_chatting'));
    } catch (e) {
      console.warn('[cs] 我的会话加载失败:', e);
    }
  };

  const loadStats = async () => {
    try {
      setStats(await CsChatAPI.stats());
    } catch (e) {
      console.warn('[cs] 统计加载失败:', e);
    }
  };

  const loadMode = async () => {
    try {
      setMode(await CsChatAPI.mode());
    } catch (e) {
      console.warn('[cs] 灰度态加载失败:', e);
    }
  };

  useEffect(() => {
    (async () => {
      await Promise.all([loadQueue(), loadMine()]);
      setLoading(false);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 消息变化 → 滚动锚点 + 轮询游标
  useEffect(() => {
    if (messages.length > 0) {
      setScrollAnchor(`msg-${messages.length - 1}`);
      const maxId = messages.reduce((mx, m) => (m.id > mx ? m.id : mx), 0);
      if (maxId > lastMessageId.current) {
        lastMessageId.current = maxId;
      }
    }
  }, [messages]);

  // 聊天窗 3s 增量轮询(页面可见 + 有活跃会话)
  useEffect(() => {
    const sid = activeSession?.sessionId;
    const st = activeSession?.status || '';
    if (tab !== 'chat' || !sid || !visible || st === 'ended') {
      return;
    }
    const timer = setInterval(async () => {
      try {
        const fresh = await CsChatAPI.messages(sid, lastMessageId.current);
        if (fresh.length > 0) {
          setMessages(prev => [...prev, ...fresh]);
        }
      } catch (_) {
        // best-effort: 轮询失败静默(下轮重试)
      }
    }, POLL_INTERVAL);
    return () => clearInterval(timer);
  }, [tab, visible, activeSession?.sessionId, activeSession?.status]);

  // 排队状态筛选
  const pickQueueFilter = (s: string) => {
    setQFilter(s);
    loadQueue(s);
  };

  // 接入会话
  const handleAccept = async (s: ChatSessionVO) => {
    try {
      await CsChatAPI.accept(s.sessionId);
      Taro.showToast({ title: '已接入会话', icon: 'success' });
      await openSession(s);
    } catch (e) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };

  // 打开会话(聊天窗)
  const openSession = async (s: ChatSessionVO) => {
    setActiveSession(s);
    setMessages([]);
    lastMessageId.current = 0;
    setTab('chat');
    try {
      const msgs = await CsChatAPI.messages(s.sessionId);
      setMessages(msgs);
    } catch (e) {
      console.warn('[cs] 消息加载失败:', e);
    }
  };

  // 客服回复
  const handleSend = async () => {
    const content = input.trim();
    if (!content || !activeSession || sending) return;
    if (activeSession.status === 'ended') {
      Taro.showToast({ title: '会话已结束', icon: 'none' });
      return;
    }
    setSending(true);
    // 乐观追加
    const optimistic: ChatMessageVO = {
      id: -Date.now(),
      sessionId: activeSession.sessionId,
      senderType: 'customer_service',
      senderId: 0,
      messageType: 'text',
      content,
      aiConfidence: null,
      createdAt: new Date().toISOString(),
    };
    setMessages(prev => [...prev, optimistic]);
    setInput('');
    try {
      await CsChatAPI.reply(activeSession.sessionId, content);
      // 以服务端为准重载(替换乐观消息)
      const msgs = await CsChatAPI.messages(activeSession.sessionId);
      setMessages(msgs);
    } catch (e) {
      setMessages(prev => prev.filter(m => m.id !== optimistic.id));
      setInput(content);
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    } finally {
      setSending(false);
    }
  };

  // 关闭会话
  const handleClose = async () => {
    if (!activeSession) return;
    const res = await Taro.showModal({
      title: '结束服务',
      content: '确定结束该会员的本次咨询吗? 结束后会员可评价。',
    });
    if (!res.confirm) return;
    try {
      await CsChatAPI.close(activeSession.sessionId);
      setActiveSession(prev => (prev ? { ...prev, status: 'ended' } : prev));
      Taro.showToast({ title: '会话已关闭', icon: 'success' });
      loadQueue();
      loadMine();
    } catch (e) {
      Taro.showToast({ title: errMsg(e), icon: 'none' });
    }
  };

  const renderQueueRow = (s: ChatSessionVO, showAccept: boolean) => (
    <View key={s.sessionId} className={styles.sessionRow}>
      <View className={styles.sessionLeft} onClick={() => openSession(s)}>
        <View className={styles.sessionType}>
          {s.sessionId} · 会员 {s.userId}
        </View>
        <View className={styles.sessionMeta}>
          {formatTime(s.createdAt)} · 待解决 {s.unresolvedCount} 次
        </View>
      </View>
      <View className={styles.sessionRight}>
        <View className={`${styles.badge} ${styles.badgeHuman}`}>
          {queueStatusName(s.status)}
        </View>
        {showAccept && (
          <View className={styles.acceptBtn} onClick={() => handleAccept(s)}>
            接入
          </View>
        )}
      </View>
    </View>
  );

  const statusDist = stats?.statusDistribution || {};
  const s = stats || {};

  return (
    <View className={styles.page}>
      <NavBar title="客服工作台" />

      <ScrollView scrollY className={styles.body}>
        <View className={styles.section}>
          <View className={styles.heroCard}>
            <View className={styles.heroTitle}>人工客服工作台</View>
            <View className={styles.heroSub}>
              排队接入 · 实时收发(3s) · 敏感词同口径 · AI 灰度不影响人工侧
            </View>
          </View>
        </View>

        <View className={styles.tabBar}>
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

        {/* ============ 排队页 ============ */}
        {tab === 'queue' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>排队会话(状态筛选)</View>
            <View className={styles.chipRow}>
              {QUEUE_FILTERS.map(f => (
                <View
                  key={f}
                  className={`${styles.chip} ${qFilter === f ? styles.chipActive : ''}`}
                  onClick={() => pickQueueFilter(f)}
                >
                  {queueStatusName(f)}
                </View>
              ))}
              <View className={styles.chip} onClick={() => loadQueue()}>刷新</View>
            </View>
            {loading ? (
              <View className={styles.empty}>加载中...</View>
            ) : queue.length === 0 ? (
              <View className={styles.empty}>当前状态无排队会话</View>
            ) : (
              queue.map(item => renderQueueRow(item, !['ended', 'archived'].includes(item.status)))
            )}
          </View>
        )}

        {/* ============ 我的会话页 ============ */}
        {tab === 'mine' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>人工服务中会话</View>
            <View className={styles.chipRow}>
              <View className={styles.chip} onClick={() => loadMine()}>刷新</View>
            </View>
            {mine.length === 0 ? (
              <View className={styles.empty}>暂无人工服务中会话</View>
            ) : (
              mine.map(item => renderQueueRow(item, false))
            )}
          </View>
        )}

        {/* ============ 聊天窗页 ============ */}
        {tab === 'chat' && (
          <View className={styles.section}>
            {activeSession ? (
              <>
                <View className={styles.cardTitle}>
                  {activeSession.sessionId} · 会员 {activeSession.userId} ·{' '}
                  {sessionStatusName(activeSession.status)}
                </View>
                <View className={styles.chatWindow}>
                  {messages.map((m, i) => (
                    <View
                      key={m.id}
                      id={`msg-${i}`}
                      className={`${styles.msgRow} ${
                        m.senderType === 'customer_service' ? styles.msgMine : styles.msgOther
                      }`}
                    >
                      <View className={styles.msgBubble}>
                        <View className={styles.msgSender}>
                          {SENDER_NAME[m.senderType] || m.senderType}
                        </View>
                        <View className={styles.msgContent}>{m.content}</View>
                        <View className={styles.msgTime}>{formatTime(m.createdAt)}</View>
                      </View>
                    </View>
                  ))}
                  {messages.length === 0 && (
                    <View className={styles.empty}>暂无消息</View>
                  )}
                </View>
                {activeSession.status !== 'ended' ? (
                  <View className={styles.inputRow}>
                    <Input
                      className={styles.input}
                      value={input}
                      onInput={e => setInput(e.detail.value)}
                      placeholder="输入回复内容..."
                      confirmType="send"
                      onConfirm={handleSend}
                    />
                    <View
                      className={`${styles.sendBtn} ${sending ? styles.sendDisabled : ''}`}
                      onClick={handleSend}
                    >
                      发送
                    </View>
                    <View className={styles.closeBtn} onClick={handleClose}>
                      结束
                    </View>
                  </View>
                ) : (
                  <View className={styles.empty}>会话已结束</View>
                )}
              </>
            ) : (
              <View className={styles.empty}>请从「排队」或「我的会话」选择会话接入</View>
            )}
          </View>
        )}

        {/* ============ 统计页 ============ */}
        {tab === 'stats' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>会话统计(观测面)</View>
            <View className={styles.runBtn} onClick={() => { loadStats(); loadMode(); }}>
              刷新统计
            </View>
            <View className={styles.resultCard}>
              <View className={styles.statGrid}>
                <View className={styles.statCell}>
                  <View className={styles.statNum}>{s.totalSessions ?? '—'}</View>
                  <View className={styles.statLbl}>总会话</View>
                </View>
                <View className={styles.statCell}>
                  <View className={styles.statNum}>{pctStr(s.aiResolutionRate)}</View>
                  <View className={styles.statLbl}>AI 解决率</View>
                </View>
                <View className={styles.statCell}>
                  <View className={styles.statNum}>{s.avgSatisfaction ?? '—'}</View>
                  <View className={styles.statLbl}>平均满意度</View>
                </View>
              </View>
              <View className={styles.resHead}>状态分布</View>
              <View className={styles.resItem}>
                {Object.keys(statusDist).length
                  ? Object.entries(statusDist).map(([k, v]) => `${sessionStatusName(k)}:${v}`).join(' · ')
                  : '—'}
              </View>
              <View className={styles.resHead}>AI 智能层灰度态</View>
              <View className={styles.resItem}>
                {mode ? `mode=${mode.mode} · source=${mode.source} · env=${mode.envMode}` : '—'}
              </View>
            </View>
          </View>
        )}

        <View className={styles.footNote}>
          人工回复与用户消息同口径敏感词过滤 · 客服端点 X-Role: admin 鉴权 ·
          观测面(统计/消息)不受 AI 灰度影响
        </View>
        <View className={styles.bottomSpace} />
      </ScrollView>
    </View>
  );
};

export default CsWorkbenchPage;
