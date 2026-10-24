/* 语法门禁: 提取语音页全部内联 <script> 合并做 node --check
   (部署前拦截花括号错位/引号未闭合类低级炸页) */
var fs = require("fs");
var cp = require("child_process");

/* 765cc6e 资产迁移(2026-10-01): 语音页 backend/ → taro-app/public/,
 * 新位置优先, 旧位置兜底(混合检出环境)
 * v124(2026-10-24): 语音页现役位置已是 zhuxiang-jiu 仓库根(release-push
 * 只打包仓库根 html), 必须排第一——否则命中 taro-app/public 下 10-19 的
 * 陈旧副本(164KB vs 现役 194KB), 语法门禁用旧文件"通过", 形同虚设 */
function findVoicePage() {
  var cands = [
    "d:/网站架构设计/zhuxiang-jiu/xiaozhu-voice.html",
    "d:/网站架构设计/taro-app/public/xiaozhu-voice.html",
    "d:/网站架构设计/zhuxiang-jiu/backend/xiaozhu-voice.html",
  ];
  for (var i = 0; i < cands.length; i++) {
    try { fs.accessSync(cands[i]); return cands[i]; } catch (e) {}
  }
  console.error("VOICE PAGE NOT FOUND: " + cands.join(" | "));
  process.exit(1);
}
var src = fs.readFileSync(findVoicePage(), "utf8");
var blocks = [];
var re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
var m;
while ((m = re.exec(src)) !== null) {
  var body = m[1];
  if (body.trim()) blocks.push(body);
}
if (!blocks.length) { console.log("NO-INLINE-SCRIPT"); process.exit(1); }
var js = blocks.join("\n;\n");
var tmp = "d:/网站架构设计/zhuxiang-jiu/deploy/_voicepage_inline.js";
fs.writeFileSync(tmp, js);
try {
  cp.execSync('"' + process.execPath + '" --check "' + tmp + '"',
              { stdio: "pipe" });
  console.log("SYNTAX OK (" + blocks.length + " blocks, "
              + js.length + " chars)");
} catch (e) {
  console.log("SYNTAX FAIL:\n" + (e.stderr || e.message));
  process.exit(1);
} finally {
  fs.unlinkSync(tmp);
}
