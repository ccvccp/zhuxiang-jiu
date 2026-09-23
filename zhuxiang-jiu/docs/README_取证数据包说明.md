# Trae CN BOM 污染 · 取证数据包说明（回访附件用）

> 打包时间：2026-09-23 ｜ 关联反馈：IDE 内报告（中文，2026-09-23）+ 国际版邮件（英文，feedback@mail.trae.ai）
> 本 README 与包内全部数据来自实测取证，可复核。

## 一、包内清单

| 文件 | 作用 |
|---|---|
| `README_取证数据包说明.md` | 本说明 |
| `Trae_CN_BOM污染Bug反馈.md` | 中文版完整报告（IDE 内渠道已提交版） |
| `Trae_BOM_Pollution_Bug_Report_EN.md` | 英文版报告（邮件已发送版） |
| `BOM污染取证_证据总览图.png` | 四组证据可视化（十六进制对比/mtime 时间线/CPU 快照/哨兵实验） |
| `BOM污染取证_原始watcher日志.txt` | 原始监控日志：写入事件时刻、BOM 层数、CPU 2s 增量 Top12 |
| `evidence_samples/` | 真实损坏文件样本（每轮清理自动留存 3 个，文件名含原始路径与轮次时间） |
| `tools/bom_trap_watcher.py` | 取证工具：哨兵部署 + mtime 高频监控 + 全进程 CPU 双快照（纯标准库） |
| `tools/clean_bom_pollution.py` | 清理工具：逐文件"纯 BOM 差异"验证后 git checkout 还原，--dry-run 预览 |
| `tools/make_evidence_png.py` | 证据图生成脚本（图可从原始数据重新生成，可证伪） |

## 二、证据要点（四组）

1. **每轮叠加一层 BOM**：损坏文件首字节 `EF BB BF` 重复 2~10 层；同一文件多轮后持续增长（docs 文件曾达 10 层）
2. **字母序逐文件写入**：71 文件 mtime 按目录树字母序推进（02:57:14→03:02:41，每秒 1-3 个），非人工行为
3. **写入者进程归属**：写入窗口内全进程 CPU 双快照 Top5 全为 Trae CN.exe（renderer/tsserver/NodeService）；凌晨 02:53:41 Trae 自动维护拉起进程，02:54:30 污染准时开始
4. **索引清单范围**：项目新建 14 个哨兵文件零触碰；Trae 索引过的 71 文件全部命中——写入者遍历 Trae 自身索引缓存清单

## 三、复核方法（工程师可独立验证）

1. **重跑取证**：`python -B bom_trap_watcher.py 4`（4 小时监控窗口；凌晨维护时段必现）
2. **证据图重生成**：`python -B make_evidence_png.py`（PIL 绘制，数据为 09-22/23 实测记录）
3. **样本字节查看**：`Format-Hex evidence_samples/<轮次>/<文件>` 首部可见多层 `EF BB BF`
4. **清理工具安全策略**：每文件先验证"去 BOM 后与 HEAD 无差异"才还原，真实修改一律跳过（防误伤）

## 四、样本说明

`evidence_samples/` 内为**真实损坏副本**（还原前留存）。样本随每轮污染自动累积——如需更多层样本（如 10 层 docs 文件），可指定轮次补存。

## 五、联系方式

发件账户：ccvccpg@outlook.com ｜ 回复请附工单/报告引用编号
