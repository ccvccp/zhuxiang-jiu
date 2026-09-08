/**
 * 地址表单校验与映射 · 纯逻辑(供 address-edit 页面与 test-address-form 单测复用)
 * 字段契约对齐后端 AddressRequest:
 *   name/phone/province/city/district/detail 必填, is_default 0/1
 */

export interface AddressForm {
  name: string;
  phone: string;
  province: string;
  city: string;
  district: string;
  detail: string;
  isDefault: boolean;
}

export const EMPTY_FORM: AddressForm = {
  name: '', phone: '', province: '', city: '', district: '', detail: '',
  isDefault: false,
};

/** 手机号校验: 大陆 11 位(1 开头) */
export function isValidPhone(phone: string): boolean {
  return /^1\d{10}$/.test(phone.trim());
}

/** 表单校验: 返回首个错误提示(通过返回 null) */
export function validateForm(f: AddressForm): string | null {
  if (!f.name.trim()) return '请填写收货人姓名';
  if (!isValidPhone(f.phone)) return '请填写正确的 11 位手机号';
  if (!f.province.trim()) return '请填写省份';
  if (!f.city.trim()) return '请填写城市';
  if (!f.district.trim()) return '请填写区/县';
  if (!f.detail.trim()) return '请填写详细地址';
  if (f.detail.trim().length < 3) return '详细地址过短';
  return null;
}

/** 表单 → 后端请求体(is_default 归一化为 0/1) */
export function toRequestBody(f: AddressForm): Record<string, any> {
  return {
    name: f.name.trim(),
    phone: f.phone.trim(),
    province: f.province.trim(),
    city: f.city.trim(),
    district: f.district.trim(),
    detail: f.detail.trim(),
    is_default: f.isDefault ? 1 : 0,
  };
}

/** 后端地址对象 → 表单(编辑预填) */
export function fromAddress(a: any): AddressForm {
  return {
    name: a.name || '',
    phone: a.phone || '',
    province: a.province || '',
    city: a.city || '',
    district: a.district || '',
    detail: a.detail || '',
    isDefault: Number(a.is_default) === 1,
  };
}
