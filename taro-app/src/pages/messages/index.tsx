/**
 * 消息中心 · 站内消息列表/已读/批量已读
 * 数据来源: 后端 /api/message/*(分类筛选, 未读标记)
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import { MessageAPI, MessageVO, CATEGORY_ICON, categoryName } from '@/api/message';
import { requireLogin } from '@/services/auth-service';

// 分类筛选 tab(全部 + 高频分类)
const CATEGORY_TABS = [
  { key: '', label: '全部' },
  { key: 'order', label: '订单' },
  { key: 'logistics', label: '物流' },
  { key: 'activity', label: '活动' },
  { key: 'coupon', label: '优惠券' },
  { key: 'system', label: '系统' },
];

const formatTime = (t?: string): string => {
  if (!t) return '';
  return t.slice(5, 16).replace('T', ' ');
};

const MessagesPage: React.FC = () => {
  const [messages, setMessages] = useState<MessageVO[]>([]);
  const [filter, setFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [unread, setUnread] = useState(0);
  // 展开的消息(点击展开全文)
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const loadData = useCallback(async (category?: string) => {
    try {
      const list = await MessageAPI.list({ category: category || undefined, limit: 100 });
      setMessages(list);
    } catch (e) {
      console.warn('[message] 消息加载失败:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshUnread = useCallback(async () => {
    try {
      const s = await MessageAPI.stats();
      setUnread(s.unread);
    } catch (_) { /* 静默 */ }
  }, []);

  useEffect(() => {
    if (requireLogin()) {
      loadData();
      refreshUnread();
    } else {
      setLoading(false);
    }
  }, [loadData, refreshUnread]);

  // 切换分类
  const switchFilter = (key: string) => {
    setFilter(key);
    setLoading(true);
    loadData(key);
  };

  // 点击消息: 展开全文 + 标记已读
  const handleMessageTap = async (m: MessageVO) => {
    // 展开/收起
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(m.id)) next.delete(m.id);
      else next.add(m.id);
      return next;
    });
    // 未读 → 标记已读
    if (m.status === 'unread') {
      try {
        await MessageAPI.markRead(m.id);
        setMessages(prev => prev.map(x => x.id === m.id ? { ...x, status: 'read' } : x));
        setUnread(u => Math.max(0, u - 1));
      } catch (e) {
        console.warn('[message] 已读标记失败:', e);
      }
    }
  };

  // 全部已读
  const handleMarkAll = async () => {
    if (unread === 0) {
      Taro.showToast({ title: '没有未读消息', icon: 'none' });
      return;
    }
    try {
      await MessageAPI.markAllRead();
      setMessages(prev => prev.map(x => x.status === 'unread' ? { ...x, status: 'read' } : x));
      setUnread(0);
      Taro.showToast({ title: '已全部标记已读', icon: 'success' });
    } catch (e) {
      console.warn('[message] 批量已读失败:', e);
    }
  };

  return (
    <View className={styles.page}>
      <NavBar title="消息中心" />
      <ScrollView scrollY className={styles.scrollView}>
        {/* 未读统计头部 */}
        <View className={styles.headerCard}>
          <View className={styles.headerLeft}>
            <View className={styles.headerTitle}>站内消息</View>
            <View className={styles.headerDesc}>
              {unread > 0 ? `${unread} 条未读` : '消息都已读完'}
            </View>
          </View>
          <View className={styles.markAllBtn} onClick={handleMarkAll}>
            全部已读
          </View>
        </View>

        {/* 分类筛选 */}
        <ScrollView scrollX className={styles.tabs} showScrollbar={false}>
          {CATEGORY_TABS.map(tab => (
            <View
              key={tab.key}
              className={`${styles.tab} ${filter === tab.key ? styles.tabActive : ''}`}
              onClick={() => switchFilter(tab.key)}
            >
              {tab.label}
            </View>
          ))}
        </ScrollView>

        {/* 消息列表 */}
        {loading ? (
          <View className={styles.empty}>加载中...</View>
        ) : messages.length === 0 ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>📭</View>
            <View>暂无消息</View>
          </View>
        ) : (
          messages.map(m => {
            const isOpen = expanded.has(m.id);
            return (
              <View
                key={m.id}
                className={`${styles.msgCard} ${m.status === 'unread' ? styles.msgUnread : ''}`}
                onClick={() => handleMessageTap(m)}
              >
                <View className={styles.msgHead}>
                  <View className={styles.msgIcon}>{CATEGORY_ICON[m.category] || '📢'}</View>
                  <View className={styles.msgTitleWrap}>
                    <View className={styles.msgTitle}>
                      {m.status === 'unread' && <View className={styles.unreadDot} />}
                      {m.title}
                    </View>
                    <View className={styles.msgMeta}>
                      {categoryName(m.category)} · {formatTime(m.createdAt)}
                    </View>
                  </View>
                </View>
                <View className={`${styles.msgContent} ${isOpen ? '' : styles.msgClamp}`}>
                  {m.content}
                </View>
              </View>
            );
          })
        )}
        <View className={styles.bottomSpacer} />
      </ScrollView>
    </View>
  );
};

export default MessagesPage;
