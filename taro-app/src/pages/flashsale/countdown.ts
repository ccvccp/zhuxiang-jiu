/**
 * 秒杀倒计时 · 纯逻辑(供 flashsale 页面与 test-flash-countdown 单测复用)
 */

/** 距目标时间的剩余描述(未开始倒计时用); 返回 null 表示已过点 */
export function countdownParts(targetIso: string, now: Date = new Date()): {
  days: number; hours: number; minutes: number; seconds: number;
} | null {
  const target = new Date(targetIso).getTime();
  if (Number.isNaN(target)) return null;
  const diff = target - now.getTime();
  if (diff <= 0) return null;
  const totalSec = Math.floor(diff / 1000);
  return {
    days: Math.floor(totalSec / 86400),
    hours: Math.floor((totalSec % 86400) / 3600),
    minutes: Math.floor((totalSec % 3600) / 60),
    seconds: totalSec % 60,
  };
}

/** 倒计时文案(如 "01:23:45" 或 "1天 02:03:04") */
export function countdownText(targetIso: string, now: Date = new Date()): string {
  const parts = countdownParts(targetIso, now);
  if (!parts) return '00:00:00';
  const hms = [parts.hours, parts.minutes, parts.seconds]
    .map(n => String(n).padStart(2, '0')).join(':');
  return parts.days > 0 ? `${parts.days}天 ${hms}` : hms;
}

/** 按场次起止时间判定当前交互态 */
export type FlashPhase = 'upcoming' | 'inProgress' | 'ended' | 'unknown';

export function phaseOf(startTimeIso: string, endTimeIso: string, now: Date = new Date()): FlashPhase {
  const start = new Date(startTimeIso).getTime();
  const end = new Date(endTimeIso).getTime();
  if (Number.isNaN(start) || Number.isNaN(end)) return 'unknown';
  const t = now.getTime();
  if (t < start) return 'upcoming';
  if (t > end) return 'ended';
  return 'inProgress';
}

/** 抢购按钮可用性(按钮态聚合) */
export function buyButtonState(
  phase: FlashPhase, remainingStock: number,
): { label: string; disabled: boolean } {
  if (phase === 'upcoming') return { label: '未开始', disabled: true };
  if (phase === 'ended') return { label: '已结束', disabled: true };
  if (phase === 'unknown') return { label: '暂不可购', disabled: true };
  if (remainingStock <= 0) return { label: '已售罄', disabled: true };
  return { label: '马上抢', disabled: false };
}
