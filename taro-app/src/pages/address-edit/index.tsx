import React, { useState, useEffect } from 'react';
import { View, Text, Input, Switch, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import { MemberAPI, AddressVO } from '@/api/member';
import { requireLogin } from '@/services/auth-service';
import { AddressForm, EMPTY_FORM, validateForm, toRequestBody, fromAddress } from './form';

/**
 * 新增/编辑收货地址页 · 对接 POST /api/member/addresses + PUT /api/member/addresses/{id}
 * 编辑模式经 storage(address_edit_cache) 预填
 */
const AddressEditPage: React.FC = () => {
  const [editing, setEditing] = useState<AddressVO | null>(null);
  const [form, setForm] = useState<AddressForm>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [inited, setInited] = useState(false);

  useEffect(() => {
    // 编辑模式: 读取预填缓存
    try {
      const cache = Taro.getStorageSync('address_edit_cache');
      if (cache) {
        const addr = JSON.parse(cache);
        setEditing(addr);
        setForm(fromAddress(addr));
      }
    } catch (e) {
      console.warn('[address-edit] 预填缓存读取失败:', e);
    }
    setInited(true);
  }, []);

  const setField = (key: keyof AddressForm, value: string | boolean) => {
    setForm(f => ({ ...f, [key]: value }));
  };

  const handleSubmit = async () => {
    const error = validateForm(form);
    if (error) {
      Taro.showToast({ title: error, icon: 'none' });
      return;
    }
    if (submitting) return;
    setSubmitting(true);
    try {
      const body = toRequestBody(form);
      if (editing) {
        await MemberAPI.addresses.update(editing.id, body);
        Taro.showToast({ title: '地址已更新', icon: 'success' });
      } else {
        await MemberAPI.addresses.create(body);
        Taro.showToast({ title: '地址已添加', icon: 'success' });
      }
      setTimeout(() => Taro.navigateBack(), 800);
    } catch (e) {
      console.warn('[address-edit] 保存失败:', e);
      Taro.showToast({ title: '保存失败,请稍后再试', icon: 'none' });
    } finally {
      setSubmitting(false);
    }
  };

  if (!inited) return null;
  if (!requireLogin()) return null;

  return (
    <View className={styles.page}>
      <NavBar title={editing ? '编辑地址' : '新增地址'} />
      <ScrollView scrollY className={styles.scrollView}>
        <View className={styles.formCard}>
          <View className={styles.fieldRow}>
            <Text className={styles.fieldLabel}>收货人</Text>
            <Input
              className={styles.fieldInput}
              placeholder="请填写收货人姓名"
              placeholderClass={styles.placeholder}
              value={form.name}
              onInput={(e) => setField('name', e.detail.value)}
            />
          </View>
          <View className={styles.fieldRow}>
            <Text className={styles.fieldLabel}>手机号</Text>
            <Input
              className={styles.fieldInput}
              type="number"
              maxlength={11}
              placeholder="11 位手机号"
              placeholderClass={styles.placeholder}
              value={form.phone}
              onInput={(e) => setField('phone', e.detail.value)}
            />
          </View>
          <View className={styles.fieldRow}>
            <Text className={styles.fieldLabel}>省份</Text>
            <Input
              className={styles.fieldInput}
              placeholder="如: 山东省"
              placeholderClass={styles.placeholder}
              value={form.province}
              onInput={(e) => setField('province', e.detail.value)}
            />
          </View>
          <View className={styles.fieldRow}>
            <Text className={styles.fieldLabel}>城市</Text>
            <Input
              className={styles.fieldInput}
              placeholder="如: 泰安市"
              placeholderClass={styles.placeholder}
              value={form.city}
              onInput={(e) => setField('city', e.detail.value)}
            />
          </View>
          <View className={styles.fieldRow}>
            <Text className={styles.fieldLabel}>区/县</Text>
            <Input
              className={styles.fieldInput}
              placeholder="如: 岱岳区"
              placeholderClass={styles.placeholder}
              value={form.district}
              onInput={(e) => setField('district', e.detail.value)}
            />
          </View>
          <View className={styles.fieldRow}>
            <Text className={styles.fieldLabel}>详细地址</Text>
            <Input
              className={styles.fieldInput}
              placeholder="街道、门牌号等"
              placeholderClass={styles.placeholder}
              value={form.detail}
              onInput={(e) => setField('detail', e.detail.value)}
            />
          </View>
          <View className={styles.switchRow}>
            <View>
              <Text className={styles.fieldLabel}>设为默认地址</Text>
              <Text className={styles.switchDesc}>下单时自动填入该地址</Text>
            </View>
            <Switch
              checked={form.isDefault}
              color="#355c44"
              onChange={(e) => setField('isDefault', e.detail.value)}
            />
          </View>
        </View>

        <View className={styles.submitBtn} onClick={handleSubmit}>
          {submitting ? '保存中...' : '保存地址'}
        </View>
        <View className={styles.bottomSpacer} />
      </ScrollView>
    </View>
  );
};

export default AddressEditPage;
