/**
 * hls.js 桩模块
 * ============================================================
 * 项目未使用 Taro <Video> 组件, 通过 webpack alias 将其动态依赖
 * hls.js(610KB 视频流库)替换为本空实现:
 *   - bundle 不再生成 610KB 的 hls 异步 chunk
 *   - 若未来误用 Video 组件播放 HLS 源, isSupported() 返回 false
 *     会静默回退原生 video 标签(而非崩溃)
 */
module.exports = { isSupported: () => false };
