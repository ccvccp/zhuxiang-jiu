# 竹创：多 Agent 创新规划与 Word 交付

从真实项目注册表和代码证据生成创新规划、设计、部署方案、验收计划／报告，并在硬门禁审查通过后输出一份合并 Word。

## 快速运行

```bash
python agent-infra/zhuchuang/cli.py run --topic "你的创新主题" --generation auto
```

`auto` 在外部模型不可用时明确降级；`external` 要求真实模型成功；`rule` 完全离线。密钥使用项目已有 `LLM_API_KEY` 等环境变量，不出现在命令行。

## 重新排版与恢复

```bash
python agent-infra/zhuchuang/cli.py render --run-dir output/zhuchuang/<run-id>
python agent-infra/zhuchuang/cli.py run --resume output/zhuchuang/<run-id>
```

源稿变化后必须重新审查；仓库提交变化后应新建运行。

## 导入真实验收证据

证据文件是 JSON 数组，每项包含 `id`、`case_id`、`command`、`environment`、`executed_at`、`exit_code`、`result`、`output_summary`、`artifact`。所有用例具有通过证据后结论才是 `passed`，任何失败证据使结论为 `failed`，证据不全保持 `pending`。

```bash
python agent-infra/zhuchuang/cli.py accept --run-dir output/zhuchuang/<run-id> --evidence acceptance-evidence.json
```

## 边界

不自动合并 PR，不执行生产部署，不上传完整仓库，不记录密钥。运行产物默认在 `output/zhuchuang/`，不进入 Git。
