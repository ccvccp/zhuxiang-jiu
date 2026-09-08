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

/** jsQR 解码参数: 双极性尝试(白底黑码/黑底白码都识别) */
const JSQR_OPTS = { inversionAttempts: 'attemptBoth' as const };

/** 亮度近似(BT.601: 0.3R+0.59G+0.11B, 定点化降开销) */
function luminance(r: number, g: number, b: number): number {
  return (r * 306 + g * 601 + b * 117) >> 10;
}

/**
 * 对比度增强(灰度直方图 min-max 拉伸), 原地修改像素
 * 场景: 瓶身反光/弱光下拍的码模块对比不足, jsQR 二值化失败
 * @returns 是否执行了增强(原始对比度已足够时返回 false 跳过)
 */
function enhanceContrast(data: Uint8ClampedArray): boolean {
  // 采样统计亮度范围(每 7 个像素取 1, 降遍历开销)
  let min = 255, max = 0;
  for (let i = 0; i + 2 < data.length; i += 28) {
    const v = luminance(data[i], data[i + 1], data[i + 2]);
    if (v < min) min = v;
    if (v > max) max = v;
  }
  if (max - min < 40) return false; // 对比度极低, 拉伸无意义
  const range = max - min;
  for (let i = 0; i + 2 < data.length; i += 4) {
    const v = luminance(data[i], data[i + 1], data[i + 2]);
    const stretched = Math.min(255, Math.max(0, Math.round(((v - min) * 255) / range)));
    data[i] = data[i + 1] = data[i + 2] = stretched;
  }
  return true;
}

/**
 * 解码位图的指定区域(源区域→目标尺寸重采样)
 * @param enhance 是否先做对比度增强(拍照模式第二遍兜底)
 */
function decodeRegion(
  bitmap: ImageBitmap,
  sx: number, sy: number, sw: number, sh: number,
  dw: number, dh: number,
  enhance: boolean,
): string | null {
  const canvas = document.createElement('canvas');
  canvas.width = dw;
  canvas.height = dh;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  if (!ctx) return null;
  ctx.drawImage(bitmap, sx, sy, sw, sh, 0, 0, dw, dh);
  const data = ctx.getImageData(0, 0, dw, dh);
  if (enhance && !enhanceContrast(data.data)) return null;
  const code = jsQR(data.data, dw, dh, JSQR_OPTS);
  return code?.data ?? null;
}

/**
 * 图片文件解码(多尺度 + 中心裁剪 + 对比度增强多遍兜底)
 * ============================================================
 * 识别率调优策略(单遍 1280 降采样改为四层递进):
 *   1. EXIF 方向修正: 手机竖拍照片旋转 90°, 不修正直接解码必失败
 *   2. 全幅多尺度: 2000/1200/700 长边——远拍小码靠高分辨率保像素,
 *      近拍大码靠降采样去噪(瓶身曲面摩尔纹)
 *   3. 每个尺度双遍: 原图 → 对比度增强(反光/弱光场景)
 *   4. 中心裁剪: 50%/30% 全分辨率——码小且居中时等比放大像素密度
 *   5. 双极性: inversionAttempts=attemptBoth(反白印刷码也能识)
 * @param onMeta 图片元信息回调(宽高/文件KB, 识别率诊断用)
 */
export async function decodeImageFile(
  file: File,
  onMeta?: (meta: { width: number; height: number; kb: number }) => void,
): Promise<string | null> {
  // 带 EXIF 方向的位图(老浏览器不支持 options 时降级)
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  } catch {
    bitmap = await createImageBitmap(file);
  }
  try {
    onMeta?.({
      width: bitmap.width,
      height: bitmap.height,
      kb: Math.round(file.size / 1024),
    });
    const long = Math.max(bitmap.width, bitmap.height);
    if (long === 0) return null;
    // canvas 像素上限 2000: 超大图防卡顿, 又保住小码像素密度
    const cap = Math.min(long, 2000);

    // 1) 全幅多尺度(大→小), 每尺度先原图后增强
    const targets = Array.from(new Set([cap, Math.round(cap * 0.6), Math.round(cap * 0.35)]));
    for (const target of targets) {
      const r = target / long;
      const dw = Math.max(1, Math.round(bitmap.width * r));
      const dh = Math.max(1, Math.round(bitmap.height * r));
      const code = decodeRegion(bitmap, 0, 0, bitmap.width, bitmap.height, dw, dh, false)
        ?? decodeRegion(bitmap, 0, 0, bitmap.width, bitmap.height, dw, dh, true);
      if (code) return code;
    }

    // 2) 中心裁剪(全分辨率上限), 码小且居中的远拍场景
    for (const ratio of [0.5, 0.3]) {
      const sw = Math.round(bitmap.width * ratio);
      const sh = Math.round(bitmap.height * ratio);
      if (sw < 80 || sh < 80) continue; // 裁剪后过小无意义
      const sx = (bitmap.width - sw) >> 1;
      const sy = (bitmap.height - sh) >> 1;
      const r = Math.min(1, cap / Math.max(sw, sh));
      const dw = Math.max(1, Math.round(sw * r));
      const dh = Math.max(1, Math.round(sh * r));
      const code = decodeRegion(bitmap, sx, sy, sw, sh, dw, dh, false)
        ?? decodeRegion(bitmap, sx, sy, sw, sh, dw, dh, true);
      if (code) return code;
    }
    return null;
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
              const code = jsQR(data.data, w, h, JSQR_OPTS);
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

  // 拍照模式: 选图(直拍调相机 / 相册选全幅高清图)后解码
  // capture 链路在部分手机浏览器只返回低分辨率视频帧(如 640×480),
  // 码在帧内占比小时每模块仅 1-2 像素导致识别失败——相册入口
  // (系统相机 App 拍的照片必为全幅)用于绕开该链路
  const pickImage = (useCapture: boolean) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    if (useCapture) {
      // 直接调起后置相机(支持的浏览器), 不支持则进入相册选择
      input.setAttribute('capture', 'environment');
    }
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      setDecoding(true);
      try {
        // 闭包外赋值会被 TS 控制流收窄为初始类型, 用对象持有保住联合类型
        const diag: { meta?: { width: number; height: number; kb: number } } = {};
        const code = await decodeImageFile(file, (m) => { diag.meta = m; });
        if (code) {
          finish(code);
        } else {
          // 诊断留痕 + 低清图引导换相册入口(下次测试可直接定位设备交付质量)
          const meta = diag.meta;
          console.warn('[ScanCode] 解码失败:', file.name, JSON.stringify(meta));
          const lowRes = !!meta && Math.max(meta.width, meta.height) < 1000;
          Taro.showToast({
            title: lowRes
              ? `相机仅返回${meta.width}×${meta.height}低清图, 请改用「从相册选择」`
              : '未识别到二维码: 请靠近拍摄、对焦清晰、避免反光',
            icon: 'none', duration: 3000,
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
  const handleCapture = () => pickImage(true);
  const handleAlbum = () => pickImage(false);

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
          <View className={styles.albumBtn} onClick={handleAlbum}>
            从相册选择(高清图)
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
