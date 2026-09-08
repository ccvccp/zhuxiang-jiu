import { defineConfig, type UserConfigExport } from '@tarojs/cli';
import path from 'path';
import TsconfigPathsPlugin from 'tsconfig-paths-webpack-plugin';
import devConfig from './dev';
import prodConfig from './prod';
import vitePluginImp from 'vite-plugin-imp';
// https://taro-docs.jd.com/docs/next/config#defineconfig-辅助函数
export default defineConfig<'webpack5'>(async (merge, { command, mode }) => {
  const baseConfig: UserConfigExport<'webpack5'> = {
    projectName: 'taro_template',
    date: '2025-12-10',
    designWidth: 375,
    deviceRatio: {
      640: 2.34 / 2,
      750: 1,
      375: 2,
      828: 1.81 / 2,
    },
    sourceRoot: 'src',
    outputRoot: process.env.TARO_OUTPUT_DIR || 'dist',
    plugins: ['@tarojs/plugin-html'],
    defineConstants: {},
    copy: {
      patterns: [],
      options: {},
    },
    framework: 'react',
    compiler: {
      type: 'webpack5',
      prebundle: {
        enable: false,
      },
    },
    cache: {
      enable: false, // Webpack 持久化缓存配置，建议开启。默认配置请参考：https://docs.taro.zone/docs/config-detail#cache
    },
    mini: {
      postcss: {
        pxtransform: {
          enable: true,
          config: {
            selectorBlackList: ['nut-'],
          },
        },
        cssModules: {
          enable: true, // 开启 CSS Modules
          config: {
            namingPattern: 'module', // 仅 *.module.scss 生效
            generateScopedName: '[name]__[local]___[hash:base64:5]',
          },
        },
      },
      webpackChain(chain) {
        chain.resolve.plugin('tsconfig-paths').use(TsconfigPathsPlugin);
      },
    },
    h5: {
      publicPath: '/',
      staticDirectory: 'static',
      output: {
        filename: 'js/[name].[contenthash:8].js',
        chunkFilename: 'js/[name].[contenthash:8].js',
      },
      miniCssExtractPluginOption: {
        ignoreOrder: true,
        filename: 'css/[name].[contenthash].css',
        chunkFilename: 'css/[name].[contenthash].css',
      },
      postcss: {
        autoprefixer: {
          enable: true,
          config: {},
        },
        cssModules: {
          enable: true, // 开启 CSS Modules
          config: {
            namingPattern: 'module', // 仅 *.module.scss 生效
            generateScopedName: '[name]__[local]___[hash:base64:5]',
          },
        },
        pxtransform: {
          enable: true,
          config: {
            selectorBlackList: ['body'],
            baseFontSize: 37.5,
            unitPrecision: 5,
          },
        },
      },
      webpackChain(chain) {
        chain.resolve.plugin('tsconfig-paths').use(TsconfigPathsPlugin);
        // 公共依赖抽离: Taro 默认 chunks:'initial' 不覆盖异步页面 chunk,
        // @tarojs/components(含 swiper)会在每个懒加载页面重复打包(总产物 5.9MB);
        // 改为 'all' 后共享模块提取为单份公共 chunk, 页面 chunk 仅含自身代码
        chain.optimization.splitChunks({
          chunks: 'all',
          minSize: 0,
          cacheGroups: {
            default: false,
            defaultVendors: false,
            common: { name: 'common', minChunks: 2, priority: 1 },
            vendors: { name: 'vendors', minChunks: 2, test: /[\\/]node_modules[\\/]/, priority: 10 },
            taro: { name: 'taro', test: /@tarojs[/\\/]/, priority: 40 },
          },
        });
        // 项目未使用 Taro <Video> 组件(ScanCode 的 video 是原生 HTML 元素),
        // 用桩模块替换 hls.js(Video 组件的动态依赖, 独占 610KB chunk)
        chain.resolve.alias.set('hls.js', path.resolve(__dirname, 'hls-stub.js'));
        // 体积护栏: app 入口(React+Taro 运行时)约 280KB 属合理水平;
        // 阈值放宽到 400KB/1MB, 仍可拦截 hls 级别(610KB+)的依赖膨胀回归
        chain.performance
          .maxAssetSize(400 * 1024)
          .maxEntrypointSize(1024 * 1024);
      },
    },
    rn: {
      appName: 'taroDemo',
      postcss: {
        cssModules: {
          enable: true,
        },
      },
    },
  };
  if (process.env.NODE_ENV === 'development') {
    // 本地开发构建配置（不混淆压缩）
    return merge({}, baseConfig, devConfig);
  }
  // 生产构建配置（默认开启压缩混淆等）
  return merge({}, baseConfig, prodConfig);
});
