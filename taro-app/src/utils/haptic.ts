/**
 * 触觉反馈工具(v2 D——设计文档 §4.3)
 * ============================================================
 * 三档震动: light(加购/清单变更) / medium(结算成功) /
 * heavy(高敏确认); 模块级 800ms 节流(防连续操作连震)。
 * iOS 系统关闭震动时 fail 静默——纯增强零风险。
 */
import Taro from '@tarojs/taro';

type HapticType = 'light' | 'medium' | 'heavy';

let lastBuzzAt = 0;

export function haptic(type: HapticType = 'light') {
  try {
    const now = Date.now();
    if (now - lastBuzzAt < 800) return; // 节流
    lastBuzzAt = now;
    Taro.vibrateShort({
      type,
      fail: () => undefined, // iOS 关闭震动等静默
    });
  } catch (_) { /* 非真机环境静默 */ }
}
