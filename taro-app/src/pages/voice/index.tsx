/**
 * 48号·小竹语音购物 · 微信小程序版
 * ============================================================
 * 架构: 小程序原生 RecorderManager 录音(无 X5 浏览器采集
 *       延迟) → 后端 /voice 端点(35号 ASR + 指令直达, 与 H5
 *       语音完全同链) → 服务端 cogtts 合成 /tts 下载 →
 *       InnerAudioContext 原生播放(无自动播放限制)。
 *
 *       (WechatSI 插件仅企业主体可用, 本方案后端零改动)
 *
 * 状态机(互斥锁——防自录循环):
 *   idle → listening → thinking → speaking → idle
 *   播报前必 recorder.stop() 停录音;
 *   开始录音前必 audio.stop() 停播报;
 *   onEnded 后 300ms 延迟, 免提模式自动重启录音。
 */
import React, {
  useCallback, useEffect, useRef, useState,
} from 'react';
import { View, Text, ScrollView, Input } from '@tarojs/components';
import Taro, { useDidHide, useDidShow } from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  XiaozhuAPI, XzCard, XzTurnResp,
} from '@/api/xiaozhu';
import { requireLogin } from '@/services/auth-service';

// ============ 类型 ============

interface VoiceMsg {
  id: number;
  role: 'user' | 'bot';
  text: string;
  card?: XzCard | null;
  /** H5 跳转路径(需映射) */
  jump?: string;
}

type Phase = 'idle' | 'listening' | 'thinking' | 'speaking';

/** 阻断自动续听的卡片(等用户屏幕操作) */
const BLOCKING_CARDS = ['confirm', 'order_done', 'order_paid'];

// 默认快捷指令(loadCommands 失败兜底)
const DEFAULT_CHIPS = ['看新品', '换一款', '查订单', '查优惠', '结算'];

// tab 页路径(switchTab 专用——不带参)
const TAB_PATHS = [
  '/pages/index/index', '/pages/products/index', '/pages/mine/index',
];

let msgSeq = 0;
const nextMsgId = () => ++msgSeq;

// 录音计时上限展示(60s 自动停)
const REC_MAX_MS = 60000;

// ============ 页面 ============

const VoicePage: React.FC = () => {
  const [messages, setMessages] = useState<VoiceMsg[]>([]);
  const [phase, setPhase] = useState<Phase>('idle');
  const [input, setInput] = useState('');
  const [chips, setChips] = useState<string[]>(DEFAULT_CHIPS);
  const [scrollAnchor, setScrollAnchor] = useState('');
  /** 录音已进行毫秒数(录音中展示) */
  const [recMs, setRecMs] = useState(0);
  /** 4 位确认码(高敏卡片) */
  const [code, setCode] = useState('');
  const [confirmToken, setConfirmToken] = useState('');
  const [verifying, setVerifying] = useState(false);
  /** 免提连续对话开关(播报完自动续听) */
  const [handsFree, setHandsFree] = useState(true);

  // ---- refs(事件回调里访问最新态, 避免闭包陷阱) ----
  const sessionIdRef = useRef<number>(0);
  const phaseRef = useRef<Phase>('idle');
  const handsFreeRef = useRef(true);
  const recorderRef = useRef<any>(null);
  const audioRef = useRef<any>(null);
  const recTimerRef = useRef<any>(null);
  const sendingRef = useRef(false);

  const setPhaseSafe = useCallback((p: Phase) => {
    phaseRef.current = p;
    setPhase(p);
  }, []);

  useEffect(() => { handsFreeRef.current = handsFree; }, [handsFree]);

  const pushMsg = useCallback((m: Omit<VoiceMsg, 'id'>) => {
    setMessages(prev => {
      const next = [...prev, { ...m, id: nextMsgId() }];
      setScrollAnchor(`msg-${next.length - 1}`);
      return next;
    });
  }, []);

  // ============ 跳转映射(H5 路径 → 小程序页) ============

  const handleJump = useCallback((jump?: string) => {
    if (!jump) return;
    let p = String(jump).replace(/^#/, '');
    if (p.startsWith('/')) p = p.slice(1);
    p = '/' + p;
    const path = p.split('?')[0];
    if (TAB_PATHS.includes(path)) {
      Taro.switchTab({ url: path });
    } else {
      Taro.navigateTo({ url: p });
    }
  }, []);

  // ============ TTS 播报(互斥: 先停录音) ============

  const startListenRef = useRef<(() => void) | null>(null);

  const speak = useCallback(async (text: string, blocking: boolean) => {
    const audio = audioRef.current;
    if (!audio || !text) {
      setPhaseSafe('idle');
      return;
    }
    // 互斥: 停录音(防自录循环)
    try { recorderRef.current?.stop(); } catch (_) { /* best-effort */ }
    setPhaseSafe('speaking');
    try {
      // 后端 cogtts 合成下载(同文本本地缓存, 热词零网络)
      const filePath = await XiaozhuAPI.fetchTtsAudio(text);
      if (phaseRef.current !== 'speaking') return; // 已被取消
      // 播放错误显式提示(此前静默失败——真机无从判断无声原因)
      audio.offError?.();
      audio.onError((err: any) => {
        console.warn('[voice] 播放错误:', err);
        if (phaseRef.current === 'speaking') {
          setPhaseSafe('idle');
          Taro.showToast({
            title: '播放失败:' + String(err?.errMsg || '').slice(0, 30),
            icon: 'none', duration: 3000,
          });
        }
      });
      audio.offEnded?.();
      audio.onEnded(() => {
        // 播报结束 → 800ms 延迟后: 免提且非阻断卡 → 自动续听
        // (Android 音频焦点切换 + TTS 外放余音散去需缓冲——
        //  过早开录会录进播报尾音产生自言自语循环)
        setTimeout(() => {
          if (phaseRef.current !== 'speaking') return;
          if (handsFreeRef.current && !blocking) {
            startListenRef.current?.();
          } else {
            setPhaseSafe('idle');
          }
        }, 800);
      });
      audio.src = filePath;
      audio.play();
    } catch (e) {
      console.warn('[voice] TTS 播报失败:', e);
      Taro.showToast({
        title: '播报失败:' + String((e as Error).message || e).slice(0, 30),
        icon: 'none', duration: 3000,
      });
      setPhaseSafe('idle');
    }
  }, [setPhaseSafe]);

  // ============ 响应统一处理 ============

  const handleResponse = useCallback((resp: XzTurnResp) => {
    const card = resp.card || null;
    pushMsg({ role: 'bot', text: resp.reply, card, jump: resp.jump });
    // TTS 分支预合成(推荐轮带两分支文本)——后台预下载
    // → 用户答"需要"/"不要这款"时本地缓存命中秒播
    const preheat = (resp as any).ttsPreheat;
    if (Array.isArray(preheat)) {
      preheat.forEach((t: string) => {
        XiaozhuAPI.fetchTtsAudio(t).catch(() => undefined);
      });
    }
    // 高敏确认卡 → 进入 4 位码输入态
    if (resp.confirmRequired && resp.confirmToken) {
      setConfirmToken(resp.confirmToken);
      setCode('');
    }
    const blocking = BLOCKING_CARDS.includes(card?.type || '');
    if (resp.reply) {
      speak(resp.reply, blocking);
    } else {
      setPhaseSafe('idle');
    }
  }, [pushMsg, speak, setPhaseSafe]);

  // ============ 发送指令(文本/语音统一入口) ============

  const sendCmd = useCallback(async (text: string) => {
    const t = text.trim();
    if (!t || !sessionIdRef.current || sendingRef.current) return;
    sendingRef.current = true;
    pushMsg({ role: 'user', text: t });
    setPhaseSafe('thinking');
    try {
      const resp = await XiaozhuAPI.sendText(sessionIdRef.current, t);
      handleResponse(resp);
    } catch (e) {
      console.warn('[voice] 指令失败:', e);
      pushMsg({ role: 'bot', text: '网络开小差了, 请再试一次', card: null });
      setPhaseSafe('idle');
    } finally {
      sendingRef.current = false;
    }
  }, [handleResponse, pushMsg, setPhaseSafe]);

  // ============ 语音引擎(原生 RecorderManager + 后端链路) ============

  /** 录音结果上传: mp3 base64 → /voice(后端 ASR + 指令直达) */
  const handleRecording = useCallback(async (
    tempFilePath: string, durationSec?: number,
  ) => {
    if (!sessionIdRef.current || sendingRef.current) return;
    sendingRef.current = true; // 与文本发送互斥
    setPhaseSafe('thinking');
    try {
      // 读文件转 base64(音频即转即传, 不落业务库——后端红线)
      const b64: string = await new Promise((resolve, reject) => {
        Taro.getFileSystemManager().readFile({
          filePath: tempFilePath,
          encoding: 'base64',
          success: (r: any) => resolve(r.data as string),
          fail: (e: any) => reject(new Error(e.errMsg || '读文件失败')),
        });
      });
      const resp = await XiaozhuAPI.voiceTurn(
        sessionIdRef.current, b64, durationSec,
      );
      // ASR 转写文本入消息流(用户气泡——所见即所说;
      // rawText 已 PII 脱敏——与后端留痕同源)
      const asrText = (resp as any)?.turn?.rawText;
      if (asrText) {
        pushMsg({ role: 'user', text: String(asrText) });
      }
      handleResponse(resp);
    } catch (e) {
      console.warn('[voice] 语音轮次失败:', e);
      pushMsg({ role: 'bot', text: '没听清, 请再试一次', card: null });
      setPhaseSafe('idle');
    } finally {
      sendingRef.current = false;
    }
  }, [handleResponse, pushMsg, setPhaseSafe]);

  const startListen = useCallback(() => {
    // 互斥: 停播报(防自录循环)
    try { audioRef.current?.stop(); } catch (_) { /* best-effort */ }
    setRecMs(0);
    // 录音计时展示(100ms 粒度)
    if (recTimerRef.current) clearInterval(recTimerRef.current);
    const startedAt = Date.now();
    recTimerRef.current = setInterval(() => {
      setRecMs(Date.now() - startedAt);
    }, 100);
    try {
      recorderRef.current?.start({
        duration: REC_MAX_MS,
        format: 'mp3',              // 后端 ASR 白名单格式(webm/mp3/wav)
        sampleRate: 16000,          // ASR 标准采样率(识别引擎内部 16k)
        numberOfChannels: 1,
        encodeBitRate: 64000,       // 16kHz mp3 合法范围(24000-96000)中段
      });
      setPhaseSafe('listening');
    } catch (e: any) {
      console.warn('[voice] 录音启动失败:', e);
      if (recTimerRef.current) clearInterval(recTimerRef.current);
      Taro.showToast({
        title: '录音失败:' + String(e?.errMsg || e?.message || e).slice(0, 40),
        icon: 'none', duration: 3500,
      });
    }
  }, [setPhaseSafe]);

  const stopListen = useCallback(() => {
    try { recorderRef.current?.stop(); } catch (_) { /* best-effort */ }
  }, []);

  useEffect(() => { startListenRef.current = startListen; }, [startListen]);

  // 初始化: 原生录音管理器 + 播报音频上下文
  const initVoiceEngine = useCallback(() => {
    if (process.env.TARO_ENV !== 'weapp') return;
    if (recorderRef.current) return; // 已初始化

    const recorder = Taro.getRecorderManager();
    recorder.onStart(() => {
      setPhaseSafe('listening');
    });
    recorder.onStop((res: { tempFilePath: string; duration: number }) => {
      if (recTimerRef.current) clearInterval(recTimerRef.current);
      if (phaseRef.current !== 'listening') return;
      const dur = Math.round((res?.duration || 0) / 100) / 10;
      // 过短录音不上传(TTS 余音/误触——自动续听时外放
      // 尾音常被录进, 900ms 以下多为非人声指令)
      if (res?.tempFilePath && (res.duration || 0) > 900) {
        handleRecording(res.tempFilePath, dur);
      } else {
        setPhaseSafe('idle');
      }
    });
    recorder.onError((err: { errCode?: number; errMsg?: string }) => {
      console.warn('[voice] 录音错误:', err);
      if (recTimerRef.current) clearInterval(recTimerRef.current);
      setPhaseSafe('idle');
      const denied = (err?.errMsg || '').includes('auth')
        || err?.errMsg?.includes('deny') || err?.errCode === 10001;
      if (denied) {
        Taro.showModal({
          title: '需要麦克风权限',
          content: '请在设置中开启麦克风权限后重试',
          confirmText: '去设置',
          success: (r) => { if (r.confirm) Taro.openSetting({}); },
        });
      } else {
        Taro.showToast({ title: '录音出错, 请重试', icon: 'none' });
      }
    });
    recorderRef.current = recorder;

    // 播报音频上下文(静音开关下也可播——语音购物播报场景)
    const audio = Taro.createInnerAudioContext();
    audio.obeyMuteSwitch = false;
    audio.onError(() => {
      if (phaseRef.current === 'speaking') setPhaseSafe('idle');
    });
    audioRef.current = audio;
  }, [handleRecording, setPhaseSafe]);

  // ============ 高敏确认码核销 ============

  const handleConfirm = useCallback(async () => {
    if (!/^\d{4}$/.test(code) || !confirmToken || verifying) return;
    setVerifying(true);
    try {
      const resp = await XiaozhuAPI.confirmOrder(confirmToken, code);
      setConfirmToken('');
      setCode('');
      // 核销响应(reply + order_done 卡)统一入消息流
      handleResponse(resp);
    } catch (e) {
      console.warn('[voice] 核销失败:', e);
      Taro.showToast({ title: '确认码有误或已过期', icon: 'none' });
    } finally {
      setVerifying(false);
    }
  }, [code, confirmToken, verifying, handleResponse]);

  // ============ 生命周期 ============

  const boot = useCallback(async () => {
    // 已开会话: 切页返回不重复开会话/推欢迎语
    if (sessionIdRef.current) return;
    if (requireLogin()) {
      initVoiceEngine();
      try {
        const s = await XiaozhuAPI.openSession('voice');
        sessionIdRef.current = s.sessionId;
        pushMsg({
          role: 'bot',
          text: '你好，我是小竹 🎋 点击下方麦克风直接说，'
            + '如「看新品」「要加购两件」「结算」——也可以打字。',
          card: null,
        });
      } catch (e) {
        console.warn('[voice] 开会话失败:', e);
      }
      // 热句预取(后台预热 TTS——首次唤醒应答"在呢!"零等待;
      // cogtts 冷合成 2-5s 是延迟大头, 预取落本地缓存即秒播)
      XiaozhuAPI.fetchTtsAudio('在呢！').catch(() => undefined);
      // 快捷指令(登录态才有意义——未登录调用会 401 弹提示)
      try {
        const r = await XiaozhuAPI.loadCommands();
        if (r?.commands?.length) {
          setChips(r.commands.slice(0, 8).map(c => c.label));
        }
      } catch (_) { /* 用 DEFAULT_CHIPS */ }
    }
  }, [initVoiceEngine, pushMsg]);

  useDidShow(() => { boot(); });
  useDidHide(() => {
    // 页面隐藏: 停录音 + 停播报(资源红线)
    try { recorderRef.current?.stop(); } catch (_) { /* best-effort */ }
    try { audioRef.current?.stop(); } catch (_) { /* best-effort */ }
    if (recTimerRef.current) clearInterval(recTimerRef.current);
    setPhaseSafe('idle');
  });

  useEffect(() => () => {
    if (recTimerRef.current) clearInterval(recTimerRef.current);
    try { audioRef.current?.destroy?.(); } catch (_) { /* best-effort */ }
  }, []);

  // ============ 渲染 ============

  // 卡片: 商品推荐列表(product_list)
  const renderProductCard = (card: XzCard) => (
    <View className={styles.kcard}>
      <View className={styles.kcardTitle}>为您推荐</View>
      {(card.items || []).map(it => (
        <View
          key={String(it.id)}
          className={styles.prow}
          onClick={() => Taro.navigateTo({
            url: `/pages/product-detail/index?id=${it.id}`,
          })}
        >
          <View className={styles.prowMain}>
            <View className={styles.prowName}>{it.name}</View>
            {it.subtitle
              && <View className={styles.prowSub}>{it.subtitle}</View>}
          </View>
          <View className={styles.prowPrice}>¥{it.price}</View>
        </View>
      ))}
      <View className={styles.kcardTip}>说「换一款」看下一款 · 「要加购两件」</View>
    </View>
  );

  // 卡片: 加购回执(cart_added)
  const renderCartCard = (card: XzCard) => (
    <View className={styles.kcard}>
      <View className={styles.kcardTitle}>🛒 已加入购物车</View>
      <View className={styles.prow}>
        <View className={styles.prowMain}>
          <View className={styles.prowName}>{card.subject}</View>
          <View className={styles.prowSub}>
            ×{card.quantity} · 购物车共 {card.cartCount} 件
          </View>
        </View>
        <View className={styles.prowPrice}>
          ¥{((card.price || 0) * (card.quantity || 1)).toFixed(2)}
        </View>
      </View>
      <View
        className={styles.kcardBtn}
        onClick={() => Taro.switchTab({ url: '/pages/products/index' })}
      >
        去选购
      </View>
    </View>
  );

  // 卡片: 高敏确认(confirm——4 位屏幕码, 语音念码不算)
  // 仅当前活跃 token 的卡片可输入(历史卡显示已处理)
  const renderConfirmCard = (card: XzCard) => {
    const active = card.confirmToken && card.confirmToken === confirmToken;
    if (!active) {
      return (
        <View className={styles.kcard}>
          <View className={styles.kcardTitle}>⚠️ 高敏操作确认</View>
          <View className={styles.confirmSummary}>{card.subject}</View>
          <View className={styles.kcardTip}>该确认已处理或已过期</View>
        </View>
      );
    }
    return (
      <View className={styles.kcard}>
        <View className={styles.kcardTitleWarn}>⚠️ 高敏操作确认</View>
        <View className={styles.confirmSummary}>{card.subject}</View>
        {card.consentPhrase
          && <View className={styles.confirmPhrase}>{card.consentPhrase}</View>}
        {card.screenCode && (
          <View className={styles.screenCodeRow}>
            <Text className={styles.screenCodeLabel}>屏幕确认码</Text>
            <Text className={styles.screenCodeDigits}>
              {String(card.screenCode).split('').join(' ')}
            </Text>
          </View>
        )}
        <View className={styles.codeRow}>
          <Input
            className={styles.codeInput}
            type='number'
            maxlength={4}
            value={code}
            onInput={(e) => setCode(e.detail.value)}
            placeholder='****'
            placeholderClass={styles.codePlaceholder}
          />
          <View
            className={`${styles.kcardBtn} ${!/^\d{4}$/.test(code) ? styles.kcardBtnDisabled : ''}`}
            onClick={handleConfirm}
          >
            {verifying ? '核销中...' : '确认执行'}
          </View>
        </View>
        <View className={styles.kcardTip}>
          按上方屏幕码输入确认 · 有效期
          {' '}{Math.round((card.expiresIn || 300) / 60)} 分钟
        </View>
      </View>
    );
  };

  // 卡片: 下单完成(order_done)
  const renderOrderCard = (card: XzCard) => (
    <View className={styles.kcard}>
      <View className={styles.kcardTitle}>✅ 订单已提交</View>
      <View className={styles.orderMeta}>单号 {card.orderId}</View>
      <View className={styles.orderAmount}>
        合计 ¥{card.totalPrice ?? '-'}
      </View>
      <View
        className={styles.kcardBtn}
        onClick={() => card.orderId && Taro.navigateTo({
          url: `/pages/order-detail/index?id=${card.orderId}`,
        })}
      >
        查看订单
      </View>
    </View>
  );

  const renderCard = (card: XzCard) => {
    switch (card.type) {
      case 'product_list':
      case 'product_detail':
        return renderProductCard(card);
      case 'cart_added':
        return renderCartCard(card);
      case 'confirm':
        return renderConfirmCard(card);
      case 'order_done':
      case 'order_paid':
        return renderOrderCard(card);
      default:
        return card.subject
          ? <View className={styles.kcardPlain}>{card.subject}</View>
          : null;
    }
  };

  const recSec = (recMs / 1000).toFixed(1);
  const phaseText: Record<Phase, string> = {
    idle: '点击麦克风说话',
    listening: `正在听 ${(recSec || '0.0')}s · 再点结束`,
    thinking: '小竹思考中...',
    speaking: '播报中',
  };

  return (
    <View className={styles.page}>
      <NavBar title='小竹语音购物' />

      {/* 消息流 */}
      <ScrollView
        scrollY
        className={styles.msgScroll}
        scrollIntoView={scrollAnchor}
        scrollWithAnimation
      >
        <View className={styles.msgList}>
          {messages.map((m, i) => (
            <View
              key={m.id}
              id={`msg-${i}`}
              className={`${styles.msgRow} ${m.role === 'user' ? styles.msgRight : styles.msgLeft}`}
            >
              <View className={styles.msgBubbleWrap}>
                <View className={`${styles.msgBubble} ${m.role === 'user' ? styles.bubbleUser : styles.bubbleBot}`}>
                  {m.text}
                </View>
                {m.card && renderCard(m.card)}
                {m.jump && (
                  <View className={styles.jumpBtn} onClick={() => handleJump(m.jump)}>
                    前往查看 ›
                  </View>
                )}
              </View>
            </View>
          ))}
        </View>
        <View className={styles.bottomSpacer} />
      </ScrollView>

      {/* 快捷指令 */}
      <ScrollView scrollX className={styles.chipBar}>
        {chips.map(c => (
          <View
            key={c}
            className={styles.chip}
            onClick={() => sendCmd(c)}
          >
            {c}
          </View>
        ))}
      </ScrollView>

      {/* 语音栏 */}
      <View className={styles.voiceBar}>
        <View className={styles.phaseRow}>
          <Text className={styles.phaseText}>
            {phaseText[phase]}
          </Text>
          {phase === 'idle' && (
            <Text
              className={handsFree ? styles.hfOn : styles.hfOff}
              onClick={() => setHandsFree(v => !v)}
            >
              免提 {handsFree ? '开' : '关'}
            </Text>
          )}
        </View>
        <View className={styles.barMain}>
          <View
            className={`${styles.micBtn} ${phase === 'listening' ? styles.micListening
              : phase === 'thinking' ? styles.micBusy
                : phase === 'speaking' ? styles.micSpeaking : ''}`}
            onClick={() => {
              if (phase === 'idle') startListen();
              else if (phase === 'listening') stopListen();
              else if (phase === 'speaking') {
                try { audioRef.current?.stop(); } catch (_) { /* best-effort */ }
                setPhaseSafe('idle');
              }
            }}
          >
            {phase === 'listening' && <View className={styles.micRipple} />}
            <Text className={styles.micIcon}>
              {phase === 'listening' ? '⏹' : phase === 'thinking' ? '⋯'
                : phase === 'speaking' ? '🔊' : '🎤'}
            </Text>
          </View>
        </View>
        {/* 文本输入兜底 */}
        <View className={styles.inputBar}>
          <Input
            className={styles.input}
            value={input}
            onInput={(e) => setInput(e.detail.value)}
            onConfirm={() => { sendCmd(input); setInput(''); }}
            placeholder='也可以打字, 如: 小竹，看新品'
            placeholderClass={styles.placeholder}
            confirmType='send'
          />
          <View
            className={`${styles.sendBtn} ${!input.trim() ? styles.sendDisabled : ''}`}
            onClick={() => { sendCmd(input); setInput(''); }}
          >
            发送
          </View>
        </View>
      </View>
    </View>
  );
};

export default VoicePage;
