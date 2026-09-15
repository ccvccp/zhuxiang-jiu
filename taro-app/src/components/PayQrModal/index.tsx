/**
 * 支付二维码弹层(P1-3 扫码支付)
 * ============================================================
 * 用于 real 渠道扫码方式(微信 native codeUrl / 支付宝 qrCode):
 * 页面通过 completePay 的 onQrCode 回调拿到码值后渲染本弹层,
 * 轮询由页面 completePay 继续进行, paid 后页面关闭弹层刷新数据。
 *
 * H5: 离屏 2d canvas → PNG data URL(对齐 promotion 页范式)
 * 小程序: 旧版 canvasId 模式
 */
import { useEffect, useState } from 'react';
import { View, Image, Canvas } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { qrMatrix, renderQrMatrix, qrMatrixToDataUrl } from '@/utils/qrcode';
import styles from './index.module.scss';

const IS_H5 = process.env.TARO_ENV === 'h5';

interface PayQrModalProps {
  /** 是否显示 */
  visible: boolean;
  /** 码值(微信 codeUrl / 支付宝 qrCode) */
  code: string;
  /** 支付金额(元, 展示用) */
  amount?: number;
  /** 顶部提示文案 */
  tip?: string;
  /** 关闭回调(用户放弃扫码) */
  onClose: () => void;
}

const PayQrModal: React.FC<PayQrModalProps> = ({
  visible, code, amount, tip, onClose,
}) => {
  const [qrUrl, setQrUrl] = useState('');

  // 渲染二维码(H5 data URL / 小程序 canvas)
  useEffect(() => {
    if (!visible || !code) return;
    if (IS_H5) {
      try {
        setQrUrl(qrMatrixToDataUrl(qrMatrix(code), 480));
      } catch (e) {
        console.warn('[PayQrModal] 二维码生成失败:', e);
      }
      return;
    }
    Taro.nextTick(() => {
      setTimeout(() => {
        try {
          const sys = Taro.getSystemInfoSync();
          // 320rpx → 实际 px
          const sizePx = Math.round((320 / 750) * sys.windowWidth);
          const ctx = Taro.createCanvasContext('payQr');
          renderQrMatrix(ctx, qrMatrix(code), sizePx);
          ctx.draw();
        } catch (e) {
          console.warn('[PayQrModal] 二维码绘制失败:', e);
        }
      }, 100);
    });
  }, [visible, code]);

  if (!visible || !code) return null;

  return (
    <View className={styles.mask} onClick={onClose}>
      <View className={styles.modal} onClick={e => e.stopPropagation()}>
        <View className={styles.title}>{tip || '扫码支付'}</View>
        {amount != null && (
          <View className={styles.amount}>¥{amount.toFixed(2)}</View>
        )}
        <View className={styles.qrWrap}>
          {IS_H5
            ? (qrUrl
              ? <Image src={qrUrl} className={styles.qrImage} mode="aspectFit" />
              : <View className={styles.qrPlaceholder}>二维码生成中...</View>)
            : <Canvas canvasId="payQr" className={styles.qrCanvas} />}
        </View>
        <View className={styles.tip}>请使用对应渠道 App 扫码完成支付</View>
        <View className={styles.tipSub}>支付成功后本页自动确认到账</View>
        <View className={styles.closeBtn} onClick={onClose}>放弃支付</View>
      </View>
    </View>
  );
};

export default PayQrModal;
