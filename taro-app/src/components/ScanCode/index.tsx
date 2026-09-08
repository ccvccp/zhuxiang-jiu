/**
 * H5 端扫码组件 · 实时扫码 + 拍照识别双模式
 * ============================================================
 * 背景: Taro.scanCode 是微信小程序原生 API, H5(手机浏览器)不可用;
 *       且 getUserMedia(实时摄像头)要求 HTTPS 安全上下文——局域网
 *       http 环境被浏览器禁用。
 *
 * 双模式策略(能力检测自动选择):
 *   实时扫码: isSecureContext + mediaDevices 可用(HTTPS/localhost)
 *            → getUserMedia 后置摄像头 + rAF 循环 jsQR 解码
 *   拍照识别: 其余环境(HTTP 局域网等)——input file + capture
 *            直接调起相机拍瓶身码照片再本地解码(无需 HTTPS)
 *
 * 命中后回调 onResult(码值); weapp 端不渲染(原生 Taro.scanCode)。
 */
import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from '@tarojs/components';
import Taro from '@tarojs/taro';
import jsQR from 'jsqr';
import styles from './index.module.scss';

interface ScanCodeProps {
  /** 是否显示 */
  visible: boolean;
  /** 关闭回调 */
  onClose: () => void;
  /** 扫码结果回调(码值文本) */
  onResult: (code: string) => void;
  /** 顶部提示文案 */
  hint?: string;
}

/** 实时扫码能力检测(HTTPS 或 localhost 才有 getUserMedia) */
export function canLiveScan(): boolean {
  if (typeof navigator === 'undefined' || typeof window === 'undefined') {
    return false;
  }
  return !!navigator.mediaDevices?.getUserMedia
    && (window.isSecureContext === true
      || ['localhost', '127.0.0.1'].includes(window.location?.hostname || ''));
}

/** 图片文件解码(jsQR; 超大图先缩到 1280 长边防卡顿) */
export async function decodeImageFile(file: File): Promise<string | null> {
  const bitmap = await createImageBitmap(file);
  try {
    const MAX = 1280;
    const scale = Math.min(1, MAX / Math.max(bitmap.width, bitmap.height));
    const w = Math.round(bitmap.width * scale);
    const h = Math.round(bitmap.height * scale);
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.drawImage(bitmap, 0, 0, w, h);
    const data = ctx.getImageData(0, 0, w, h);
    const code = jsQR(data.data, w, h);
    return code?.data ?? null;
  } finally {
    bitmap.close?.();
  }
}

const ScanCode: React.FC<ScanCodeProps> = ({
  visible, onClose, onResult, hint,
}) => {
  const liveMode = canLiveScan();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const rafRef = useRef<number>(0);
  const decodingRef = useRef(false);
  const doneRef = useRef(false); // 已命中标记(防重复回调)
  const [decoding, setDecoding] = useState(false);
  const [camError, setCamError] = useState('');

  /** 命中处理(幂等: 只回调一次) */
  const finish = (code: string) => {
    if (doneRef.current) return;
    doneRef.current = true;
    cleanup();
    onResult(code);
  };

  /** 关闭(清理摄像头/循环) */
  const cleanup = () => {
    cancelAnimationFrame(rafRef.current);
    streamRef.current?.getTracks().forEach(t => t.stop());
    streamRef.current = null;
  };

  // 实时模式: 打开时启动摄像头 + 解码循环
  useEffect(() => {
    doneRef.current = false;
    if (!visible || !liveMode) return;

    let cancelled = false;
    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'environment' },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach(t => t.stop());
          return;
        }
        streamRef.current = stream;
        const video = videoRef.current;
        if (video) {
          video.srcObject = stream;
          await video.play();
          tick();
        }
      } catch (e) {
        console.error('[ScanCode] 摄像头启动失败:', e);
        setCamError('摄像头不可用, 请改用拍照识别或手动输入');
      }
    })();

    /** rAF 循环: 抓帧 → jsQR */
    function tick() {
      rafRef.current = requestAnimationFrame(async () => {
        const video = videoRef.current;
        if (video && video.videoWidth > 0 && !decodingRef.current) {
          decodingRef.current = true;
          try {
            const w = video.videoWidth;
            const h = video.videoHeight;
            const canvas = document.createElement('canvas');
            canvas.width = w;
            canvas.height = h;
            const ctx = canvas.getContext('2d');
            if (ctx) {
              ctx.drawImage(video, 0, 0, w, h);
              const data = ctx.getImageData(0, 0, w, h);
              const code = jsQR(data.data, w, h);
              if (code?.data) finish(code.data);
            }
          } catch (e) {
            console.warn('[ScanCode] 帧解码异常:', e);
          } finally {
            decodingRef.current = false;
          }
        }
        if (!doneRef.current) tick();
      });
    }

    return () => {
      cancelled = true;
      cleanup();
    };
  }, [visible, liveMode]);

  // 拍照模式: input file 选择/拍摄后解码
  const handleCapture = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    // 直接调起后置相机(支持的浏览器), 不支持则进入相册选择
    input.setAttribute('capture', 'environment');
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      setDecoding(true);
      try {
        const code = await decodeImageFile(file);
        if (code) {
          finish(code);
        } else {
          Taro.showToast({
            title: '未识别到二维码, 请对准瓶身码重拍',
            icon: 'none', duration: 2500,
          });
        }
      } catch (e) {
        console.error('[ScanCode] 照片解码失败:', e);
        Taro.showToast({ title: '识别失败, 请重试或手动输入', icon: 'none' });
      } finally {
        setDecoding(false);
      }
    };
    input.click();
  };

  // weapp 端不渲染(原生 Taro.scanCode 由页面调用;
  // 置于 hooks 之后保证 Rules of Hooks 一致)
  if (process.env.TARO_ENV !== 'h5') {
    return null;
  }

  if (!visible) {
    return null;
  }

  return (
    <View className={styles.mask}>
      {liveMode ? (
        <View className={styles.liveBox}>
          <video
            ref={videoRef}
            className={styles.video}
            playsInline
            muted
          />
          <View className={styles.finder} />
          <View className={styles.hintBar}>
            {camError || hint || '将二维码/瓶身码置于取景框内'}
          </View>
          <View className={styles.closeBtn} onClick={onClose}>
            <Text>关闭</Text>
          </View>
          {decoding && <View className={styles.hintBar}>识别中…</View>}
        </View>
      ) : (
        <View className={styles.photoBox}>
          <View className={styles.photoIcon}>📷</View>
          <View className={styles.photoTitle}>拍照识别二维码</View>
          <View className={styles.photoDesc}>
            {'当前为非 HTTPS 环境, 不支持实时扫码\n'
              + '点击下方按钮拍摄瓶身码照片, 自动识别'}
          </View>
          <View
            className={styles.captureBtn}
            onClick={handleCapture}
          >
            {decoding ? '识别中…' : '拍摄瓶身码'}
          </View>
          <View className={styles.cancelBtn} onClick={onClose}>
            <Text>取消</Text>
          </View>
        </View>
      )}
    </View>
  );
};

export default ScanCode;
