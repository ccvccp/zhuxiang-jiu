/**
 * test-address-form.js · 收货地址表单校验与映射单元测试
 * ============================================================
 * 范式: 纯 Node 脚本(对齐 test-domain-config.js, 零外部测试框架)
 *       + TypeScript 内存编译(transpileModule)真实源码
 *
 * 覆盖(地址簿功能——字段契约对齐后端 AddressRequest):
 *   [手机号校验]
 *   1.  合法 11 位(1 开头)通过
 *   2.  非 1 开头拒绝
 *   3.  位数不足/超出拒绝
 *   4.  非数字拒绝
 *   5.  前后空格容忍
 *   [表单校验]
 *   6.  完整表单通过(null)
 *   7.  空姓名 → 首个错误提示
 *   8.  非法手机号提示
 *   9.  逐字段缺失提示(省/市/区/详址)
 *   10. 详细地址过短提示
 *   [请求体映射]
 *   11. is_default true → 1 / false → 0
 *   12. 所有字段 trim 归一
 *   [后端对象 → 表单]
 *   13. is_default 0/1 → boolean
 *   14. 缺省字段兜底空串
 *   [常量契约]
 *   15. EMPTY_FORM 初始结构
 */
const fs = require('fs');
const path = require('path');
const ts = require('typescript');

const SRC = path.resolve(__dirname, '..', 'src', 'pages', 'address-edit', 'form.ts');

let passed = 0, failed = 0;
function record(name, ok, detail = '') {
  if (ok) { passed++; console.log(`  ✓ ${name}`); }
  else { failed++; console.log(`  ✗ ${name}${detail ? ` → ${detail}` : ''}`); }
}

// ---------- 编译加载 form.ts ----------
const { outputText } = ts.transpileModule(fs.readFileSync(SRC, 'utf8'), {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2019,
  },
});
const sandbox = { module: { exports: {} }, exports: {}, require, console };
sandbox.global = sandbox;
const wrapper = new Function('module', 'exports', 'require', 'console', 'globalThis',
  `"use strict"; ${outputText}`);
wrapper(sandbox.module, sandbox.module.exports, require, console, sandbox);
const { isValidPhone, validateForm, toRequestBody, fromAddress, EMPTY_FORM } = sandbox.module.exports;

console.log('收货地址表单校验单元测试');
console.log('='.repeat(52));

// ---------- 手机号校验 ----------
record('合法手机号 13812345678 通过', isValidPhone('13812345678') === true);
record('非 1 开头 23812345678 拒绝', isValidPhone('23812345678') === false);
record('位数不足 1381234567 拒绝', isValidPhone('1381234567') === false);
record('位数超出 138123456789 拒绝', isValidPhone('138123456789') === false);
record('非数字 1381234567a 拒绝', isValidPhone('1381234567a') === false);
record('前后空格容忍', isValidPhone('  13812345678  ') === true);

// ---------- 表单校验 ----------
const FULL_FORM = {
  name: '张三', phone: '13812345678', province: '山东省', city: '泰安市',
  district: '岱岳区', detail: 'XX路123号', isDefault: false,
};
record('完整表单校验通过(返回 null)', validateForm(FULL_FORM) === null);
record('空姓名 → 提示收货人', validateForm({ ...FULL_FORM, name: '' }).includes('收货人'));
record('非法手机号 → 提示手机号', validateForm({ ...FULL_FORM, phone: '123' }).includes('手机号'));
record('缺省份 → 提示省份', validateForm({ ...FULL_FORM, province: ' ' }).includes('省份'));
record('缺城市 → 提示城市', validateForm({ ...FULL_FORM, city: '' }).includes('城市'));
record('缺区县 → 提示区/县', validateForm({ ...FULL_FORM, district: '' }).includes('区/县'));
record('缺详址 → 提示详细地址', validateForm({ ...FULL_FORM, detail: '' }).includes('详细地址'));
record('详址过短(2字) → 提示过短', validateForm({ ...FULL_FORM, detail: '路1' }).includes('过短'));

// ---------- 请求体映射 ----------
const reqTrue = toRequestBody({ ...FULL_FORM, isDefault: true });
const reqFalse = toRequestBody({ ...FULL_FORM, isDefault: false });
record('isDefault=true → is_default=1', reqTrue.is_default === 1);
record('isDefault=false → is_default=0', reqFalse.is_default === 0);
const reqTrim = toRequestBody({ ...FULL_FORM, name: '  张三  ', detail: '  XX路123号  ' });
record('字段 trim 归一(姓名)', reqTrim.name === '张三');
record('字段 trim 归一(详址)', reqTrim.detail === 'XX路123号');
record('请求体六字段齐备', ['name', 'phone', 'province', 'city', 'district', 'detail', 'is_default']
  .every(k => k in reqTrim));

// ---------- 后端对象 → 表单 ----------
const fromDefault = fromAddress({ name: '李四', phone: '13987654321', province: '江苏省',
  city: '南京市', district: '玄武区', detail: 'YY路45号', is_default: 1 });
record('后端 is_default=1 → true', fromDefault.isDefault === true);
const fromNonDefault = fromAddress({ name: '李四', is_default: 0 });
record('后端 is_default=0 → false', fromNonDefault.isDefault === false);
const fromSparse = fromAddress({});
record('缺省字段兜底空串', fromSparse.name === '' && fromSparse.phone === ''
  && fromSparse.province === '' && fromSparse.detail === '');

// ---------- 常量契约 ----------
record('EMPTY_FORM 初始结构(全空 + 非默认)',
  EMPTY_FORM.name === '' && EMPTY_FORM.phone === '' && EMPTY_FORM.isDefault === false
  && Object.keys(EMPTY_FORM).length === 7);

console.log('='.repeat(52));
console.log(`结果: ${passed} 通过, ${failed} 失败`);
process.exit(failed > 0 ? 1 : 0);
