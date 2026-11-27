import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


class ModelUnavailable(RuntimeError):
    pass


@dataclass
class ModelResult:
    data: dict | None
    generation: str
    model: str
    warning: str = ""


class ModelAdapter:
    def __init__(self, mode="auto", model="", client=None):
        if mode not in {"auto", "external", "rule"}:
            raise ValueError("未知生成模式")
        self.mode = mode
        self.model = model or os.environ.get("LLM_MODEL", "glm-4-flash")
        self.client = client or self._default_client()

    @staticmethod
    def _default_client():
        backend = Path(__file__).resolve().parents[2] / "backend"
        if str(backend) not in sys.path:
            sys.path.insert(0, str(backend))
        from services.llm_client import LLMProviderClient
        return LLMProviderClient()

    @staticmethod
    def _parse(text):
        stripped = str(text or "").strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            stripped = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            if stripped.lstrip().startswith("json"):
                stripped = stripped.lstrip()[4:].lstrip()
        value = json.loads(stripped)
        if not isinstance(value, dict):
            raise ValueError("模型结果必须是 JSON 对象")
        return value

    def generate(self, stage, instruction, payload, validator):
        if self.mode == "rule":
            return ModelResult(None, "rule", "", "按用户选择使用规则轨")
        system = (
            f"你是竹创流水线的 {stage} Agent。{instruction}\n"
            "只输出一个 JSON 对象；现有项目事实必须引用输入证据编号；"
            "未来方案标为建议；不得声称已经部署或通过验收。"
        )
        correction = ""
        last_warning = "外部模型不可用或返回空响应"
        for attempt in range(2):
            user = json.dumps({"payload": payload, "correction": correction}, ensure_ascii=False)
            try:
                raw = self.client.chat(system, user, temperature=0.2, model=self.model)
                if raw is None:
                    break
                data = self._parse(raw)
                errors = list(validator(data))
                if not errors:
                    return ModelResult(data, "external", self.model)
                last_warning = "；".join(errors)
                correction = "上次输出校验失败：" + last_warning + "。请修正完整 JSON 对象。"
            except Exception as exc:
                last_warning = f"模型调用或解析失败：{type(exc).__name__}"
                break
        if self.mode == "external":
            raise ModelUnavailable(last_warning)
        return ModelResult(None, "rule", self.model, last_warning)
