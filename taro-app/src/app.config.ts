export default defineAppConfig({
  pages: [
    'pages/index/index',
    'pages/products/index',
    'pages/mine/index',
    'pages/checkout/index',
    'pages/privacy/index',
    'pages/agreement/index'
  ],
  window: {
    backgroundTextStyle: 'light',
    navigationBarBackgroundColor: '#355c44',
    navigationBarTitleText: '竹香酒',
    navigationBarTextStyle: 'white'
  },
  tabBar: {
    color: '#999999',
    selectedColor: '#355c44',
    backgroundColor: '#ffffff',
    borderStyle: 'black',
    list: [
      {
        pagePath: 'pages/index/index',
        text: '首页',
        iconPath: 'assets/tabbar/home.svg',
        selectedIconPath: 'assets/tabbar/home-selected.svg'
      },
      {
        pagePath: 'pages/products/index',
        text: '商品',
        iconPath: 'assets/tabbar/products.svg',
        selectedIconPath: 'assets/tabbar/products-selected.svg'
      },
      {
        pagePath: 'pages/mine/index',
        text: '我的',
        iconPath: 'assets/tabbar/mine.svg',
        selectedIconPath: 'assets/tabbar/mine-selected.svg'
      }
    ]
  }
})
