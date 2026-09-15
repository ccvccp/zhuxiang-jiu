/**
 * 老酒/新酒回收 · 购买日期边界与酒龄计算
 * ============================================================
 * 口径对齐后端 recycle_service.calculate_wine_age(按整年):
 *   - 老酒估价: 购买日期须 ≤ 3 年前(酒龄 ≥ 3 年)
 *   - 新酒议价: 购买日期须在近 3 年内(酒龄 0-3 年)
 *
 * 全部使用本地时区构造日期字符串——禁止 new Date().toISOString().slice(0, 10)
 * (UTC 偏移: 东八区 08:00 前会取到昨天, 边界日差一天)。
 */

const pad2 = (n: number): string => String(n).padStart(2, '0');

/** Date → 本地时区 YYYY-MM-DD */
export const toLocalDateStr = (d: Date): string =>
  `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;

/** 今天往前推 N 年(2/29 溢出时回退一天, 保证月日对齐 → 酒龄整年) */
export const minusYears = (d: Date, years: number): Date => {
  const shifted = new Date(d);
  shifted.setFullYear(shifted.getFullYear() - years);
  if (shifted.getMonth() !== d.getMonth() || shifted.getDate() !== d.getDate()) {
    shifted.setDate(shifted.getDate() - 1);
  }
  return shifted;
};

/** 今天(本地时区) */
export const TODAY_LOCAL = toLocalDateStr(new Date());

/** 3 年前(老酒购买日期上限 / 新酒购买日期下限) */
export const THREE_YEARS_AGO = toLocalDateStr(minusYears(new Date(), 3));

/** 计算酒龄(按整年, 与后端 calculate_wine_age 同口径; now 可注入便于测试) */
export const calcWineAge = (purchaseDate: string, now: Date = new Date()): number => {
  const [y, m, d] = purchaseDate.split('-').map(Number);
  let age = now.getFullYear() - y;
  if (now.getMonth() + 1 < m || (now.getMonth() + 1 === m && now.getDate() < d)) {
    age -= 1;
  }
  return age;
};
