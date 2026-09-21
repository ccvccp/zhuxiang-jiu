/**
 * 48号·小竹智能语音中枢 API · 小程序端
 * ============================================================
 * 后端: /api/xiaozhu/*(纯 HTTP 服务, 小程序 wx.request 无
 *       Origin 头, CORS 不拦截; 鉴权走 request.ts 自动注入
 *       X-Member-Id + Bearer)
 *
 * 端点:
 *   POST /sessions                    开会话 {channel: voice|text}
 *   POST /sessions/{id}/text          文本轮次(WechatSI 插件已
 *                                     转文字, 无需传音频)
 *   POST /confirm/{token}             高敏确认码核销 {code}
 *   GET  /commands                    指令集自描述(快捷指令数据源)
 */

import Taro from '@tarojs/taro';
import { API_BASE } from '@/config';
import { getMemberId, getSession } from '@/services/auth-service';
import { request } from './request';

/** 商品卡片单项(product_list.items) */
export interface XzProductItem {
  id: number | string;
  name: string;
  price: number;
  subtitle?: string;
}

/** 小竹回复卡片(按 type 渲染) */
export interface XzCard {
  type: string;
  subject?: string;
  /** product_list */
  items?: XzProductItem[];
  preferenceApplied?: string[];
  /** cart_added */
  productId?: number;
  price?: number;
  quantity?: number;
  cartCount?: number;
  /** confirm(高敏确认) */
  confirmToken?: string;
  /** 屏幕确认码(完整 4 位——显示在用户屏幕, 纯语音攻击者不可见) */
  screenCode?: string;
  codeHint?: string;
  expiresIn?: number;
  consentPhrase?: string;
  /** order_done */
  orderId?: string;
  totalPrice?: number;
}

/** 文本轮次响应 */
export interface XzTurnResp {
  reply: string;
  card: XzCard | null;
  /** H5 路径跳转(需映射为小程序页路径) */
  jump?: string;
  /** 高敏确认流(cart.submit) */
  confirmRequired?: boolean;
  confirmToken?: string;
  consentPhrase?: string;
  summary?: string;
  /** 唤醒判定回包 */
  needsWake?: boolean;
  intent?: string;
}

/** 会话 */
export interface XzSession {
  success: boolean;
  sessionId: number;
  memberId: number;
  channel: string;
  status: string;
}

/** 指令集(commands 数据源) */
export interface XzCommandsResp {
  success: boolean;
  commands: Array<{
    action: string;
    label: string;
    examples: string[];
  }>;
  wakeWords: string[];
  wakeFreeWindowSeconds: number;
}

export const XiaozhuAPI = {
  /** 开启小竹会话 */
  openSession(channel: 'voice' | 'text' = 'voice'): Promise<XzSession> {
    return request<XzSession>({
      url: '/api/xiaozhu/sessions',
      method: 'POST',
      data: { channel },
    });
  },

  /** 文本轮次(与语音同链——免唤醒窗 5 分钟) */
  sendText(sessionId: number, text: string): Promise<XzTurnResp> {
    return request<XzTurnResp>({
      url: `/api/xiaozhu/sessions/${sessionId}/text`,
      method: 'POST',
      data: { text },
    });
  },

  /** 高敏确认码核销(4 位数字) */
  confirmOrder(token: string, code: string): Promise<XzTurnResp> {
    return request<XzTurnResp>({
      url: `/api/xiaozhu/confirm/${token}`,
      method: 'POST',
      data: { code },
    });
  },

  /** 指令集自描述(快捷指令 chips 数据源) */
  loadCommands(): Promise<XzCommandsResp> {
    return request<XzCommandsResp>({ url: '/api/xiaozhu/commands' });
  },

  /** 轮次反馈(v2 A: 👍/👎——落 turn hash, 覆盖式可切换) */
  turnFeedback(sessionId: number, turnId: string,
               rating: 'up' | 'down'): Promise<{ success: boolean }> {
    return request<{ success: boolean }>({
      url: `/api/xiaozhu/sessions/${sessionId}/turns/${turnId}/feedback`,
      method: 'POST',
      data: { rating },
    });
  },

  /**
   * 语音轮次: 原生录音(mp3 base64)上传 → 后端 ASR(35号链路)
   * → 指令直达。mode='tap'(点击录音——用户主动按下麦克风
   * =明确交互, 免唤醒词)
   */
  async voiceTurn(
    sessionId: number, audioBase64: string, durationSec?: number,
  ): Promise<XzTurnResp> {
    return request<XzTurnResp>({
      url: `/api/xiaozhu/sessions/${sessionId}/voice`,
      method: 'POST',
      data: {
        audioBase64,
        filename: 'audio.mp3',
        durationSec: durationSec ?? undefined,
        mode: 'tap',
      },
    });
  },

  /**
   * TTS 播报音频下载(服务端 cogtts 合成 mp3, Redis 缓存 10 分钟)
   * → InnerAudioContext 播放(原生; mp3 双端兼容——wav 在
   * Android InnerAudioContext 无声)
   * 返回本地临时 mp3 文件路径(同文本本地缓存, 零重复下载)
   */
  async fetchTtsAudio(text: string): Promise<string> {
    const t = text.slice(0, 200);
    // v2 G: 语速偏好(慢0.8/标准1/快1.2——本地存储; cogtts
    // 服务端 speed 实证生效; 缓存文件名含语速维度防串台)
    const spd = Number(Taro.getStorageSync('xz_tts_speed')) || 1;
    const fs = Taro.getFileSystemManager();
    const name = `xz_tts_${hashText(t + '|' + spd)}.mp3`;
    const filePath = `${Taro.env.USER_DATA_PATH}/${name}`;

    // 本地缓存命中校验(大小>500B 才算有效——防损坏缓存死循环)
    const cached = await new Promise<number>(resolve => {
      fs.stat({
        path: filePath,
        success: (s: any) => resolve((s.stats || s).size || 0),
        fail: () => resolve(0),
      });
    });
    if (cached > 500) return filePath;

    // 鉴权下载(InnerAudioContext 不支持自定义 header → 先下文件)
    const session = getSession();
    const header: Record<string, string> = {
      'X-Member-Id': getMemberId(),
      ...(session?.accessToken
        ? { Authorization: `Bearer ${session.accessToken}` } : {}),
    };
    const res = await Taro.request({
      url: `${API_BASE}/api/xiaozhu/tts?text=${encodeURIComponent(t)}`
        + `&speed=${spd}`,
      responseType: 'arraybuffer',
      header,
      timeout: 15000,
    });
    if (res.statusCode !== 200 || !res.data) {
      throw new Error(`tts下载失败(${res.statusCode})`);
    }
    // 二进制校验(responseType 失效时 data 变字符串→写盘必损坏无声)
    if (typeof res.data === 'string') {
      throw new Error('tts响应非二进制(responseType失效)');
    }
    const buf = res.data as ArrayBuffer;
    if (buf.byteLength < 500) {
      throw new Error(`tts音频过小(${buf.byteLength}B)`);
    }
    await new Promise<void>((resolve, reject) => {
      fs.writeFile({
        filePath,
        data: buf,
        success: () => resolve(),
        fail: e => reject(new Error(e.errMsg || '写文件失败')),
      });
    });
    return filePath;
  },
};

/** 轻量文本 hash(本地 TTS 缓存文件名——非安全用途) */
function hashText(t: string): string {
  let h = 5381;
  for (let i = 0; i < t.length; i++) {
    h = ((h << 5) + h + t.charCodeAt(i)) | 0;
  }
  return (h >>> 0).toString(36);
}
