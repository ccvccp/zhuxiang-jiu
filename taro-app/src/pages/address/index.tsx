import React, { useState, useEffect } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro, { useRouter, useDidShow } from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import { MemberAPI, AddressVO } from '@/api/member';
import { requireLogin } from '@/services/auth-service';

/** 结算页选中地址暂存键(checkout useDidShow 时读取) */
export const CHECKOUT_SELECTED_ADDRESS_KEY = 'checkout_selected_address';

/**
 * 收货地址簿页 · 对接 GET/POST/PUT/DELETE /api/member/addresses
 * 两种模式:
 *   - manage(默认): 从"我的"进入, 纯管理
 *   - select: 从结算页进入, 点选地址后暂存并返回
 */
const AddressPage: React.FC = () => {
  const router = useRouter();
  const mode = router.params.mode === 'select' ? 'select' : 'manage';

  const [addresses, setAddresses] = useState<AddressVO[]>([]);
  const [loading, setLoading] = useState(true);

  const loadAddresses = async () => {
    setLoading(true);
    try {
      const list = await MemberAPI.addresses.list();
      setAddresses(list);
    } catch (e) {
      console.warn('[address] 地址列表加载失败:', e);
      setAddresses([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!requireLogin()) return;
    loadAddresses();
  }, []);

  // 从编辑页返回时刷新
  useDidShow(() => {
    if (requireLogin()) loadAddresses();
  });

  // 选择模式: 暂存选中地址并返回结算页
  const handleSelect = (addr: AddressVO) => {
    if (mode !== 'select') return;
    Taro.setStorageSync(CHECKOUT_SELECTED_ADDRESS_KEY, JSON.stringify(addr));
    Taro.navigateBack();
  };

  const handleEdit = (addr: AddressVO) => {
    // 编辑预填数据经 storage 传递
    Taro.setStorageSync('address_edit_cache', JSON.stringify(addr));
    Taro.navigateTo({ url: '/pages/address-edit/index' });
  };

  const handleAdd = () => {
    Taro.removeStorageSync('address_edit_cache');
    Taro.navigateTo({ url: '/pages/address-edit/index' });
  };

  const handleDelete = (addr: AddressVO) => {
    Taro.showModal({
      title: '删除地址',
      content: `确定删除 ${addr.name} 的收货地址吗?`,
      confirmColor: '#e64340',
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await MemberAPI.addresses.remove(addr.id);
          Taro.showToast({ title: '已删除', icon: 'success' });
          loadAddresses();
        } catch (e) {
          console.warn('[address] 删除失败:', e);
          Taro.showToast({ title: '删除失败', icon: 'none' });
        }
      },
    });
  };

  // 设为默认(仅管理模式)
  const handleSetDefault = async (addr: AddressVO) => {
    if (mode !== 'manage' || addr.isDefault) return;
    try {
      await MemberAPI.addresses.update(addr.id, { isDefault: true });
      Taro.showToast({ title: '已设为默认', icon: 'success' });
      loadAddresses();
    } catch (e) {
      console.warn('[address] 设默认失败:', e);
      Taro.showToast({ title: '设置失败', icon: 'none' });
    }
  };

  return (
    <View className={styles.page}>
      <NavBar title={mode === 'select' ? '选择收货地址' : '收货地址'} />
      <ScrollView scrollY className={styles.scrollView}>
        {loading ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>⏳</View>
            <View className={styles.emptyText}>加载中...</View>
          </View>
        ) : addresses.length === 0 ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>📍</View>
            <View className={styles.emptyText}>暂无收货地址</View>
            <View className={styles.emptyAddBtn} onClick={handleAdd}>+ 新增收货地址</View>
          </View>
        ) : (
          <>
            {mode === 'select' && (
              <View className={styles.selectTip}>点击地址即可选用</View>
            )}
            {addresses.map(addr => (
              <View
                key={addr.id}
                className={`${styles.addressCard} ${mode === 'select' ? styles.selectable : ''}`}
                onClick={() => handleSelect(addr)}
              >
                <View className={styles.addressTop}>
                  <View className={styles.nameWrap}>
                    <Text className={styles.name}>{addr.name}</Text>
                    <Text className={styles.phone}>{addr.phone}</Text>
                    {addr.isDefault && <Text className={styles.defaultTag}>默认</Text>}
                  </View>
                </View>
                <View className={styles.addressDetail}>
                  {addr.province} {addr.city} {addr.district}
                  <Text className={styles.addressStreet}>{addr.detail}</Text>
                </View>
                <View className={styles.addressActions}>
                  {!addr.isDefault && mode === 'manage' && (
                    <Text
                      className={styles.actionBtn}
                      onClick={(e) => { e.stopPropagation(); handleSetDefault(addr); }}
                    >
                      设为默认
                    </Text>
                  )}
                  <Text
                    className={styles.actionBtn}
                    onClick={(e) => { e.stopPropagation(); handleEdit(addr); }}
                  >
                    编辑
                  </Text>
                  {!addr.isDefault && (
                    <Text
                      className={`${styles.actionBtn} ${styles.danger}`}
                      onClick={(e) => { e.stopPropagation(); handleDelete(addr); }}
                    >
                      删除
                    </Text>
                  )}
                </View>
              </View>
            ))}
          </>
        )}
        <View className={styles.bottomSpacer} />
      </ScrollView>

      {addresses.length > 0 && (
        <View className={styles.addButton} onClick={handleAdd}>
          + 新增收货地址
        </View>
      )}
    </View>
  );
};

export default AddressPage;
