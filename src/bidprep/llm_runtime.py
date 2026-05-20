from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bidprep.config import settings


@dataclass(frozen=True)
class LLMRuntimeConfig:
    """单次流水线使用的 LLM 端点（与 .env 默认配置互斥，优先于全局 settings）。"""

    base_url: str  # OpenAI 兼容根地址，须以 /v1 结尾（由 normalize_openai_base 生成）
    api_key: str
    model: str
    temperature: float = 0.2


def normalize_openai_base(url: str) -> str:
    """将用户输入的地址规范为 OpenAI SDK 所需的 base（…/v1）。"""
    u = (url or "").strip().rstrip("/")
    if not u:
        raise ValueError("base_url 为空")
    if not u.endswith("/v1"):
        u = u + "/v1"
    return u


def runtime_from_settings() -> LLMRuntimeConfig:
    raw = (settings.llm_base_url or "").strip().rstrip("/")
    base = raw if raw.endswith("/v1") else normalize_openai_base(raw)
    return LLMRuntimeConfig(
        base_url=base,
        api_key=settings.llm_api_key or "sk-placeholder",
        model=settings.llm_model,
        temperature=settings.agent_temperature,
    )


def fetch_ollama_model_names(host: str, timeout_sec: float = 8.0) -> tuple[list[str], Optional[str]]:
    """
    请求 Ollama GET /api/tags。host 示例：http://127.0.0.1:11434
    返回 (模型名列表, 错误信息)；错误时列表为空。
    """
    base = (host or "").strip().rstrip("/")
    if not base:
        return [], "Ollama 地址为空"
    url = base + "/api/tags"
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        return [], f"HTTP {e.code}: {e.reason}"
    except URLError as e:
        return [], f"无法连接 Ollama：{e.reason}"
    except Exception as e:
        return [], str(e)

    import json

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [], "Ollama 返回非 JSON"

    models = data.get("models") or []
    names: list[str] = []
    for m in models:
        if isinstance(m, dict) and m.get("name"):
            names.append(str(m["name"]))
    names = sorted(set(names))
    return names, None


def llm_config_from_frontend(
    mode: str,
    ollama_host: str,
    ollama_model: str,
    openai_base_url: str,
    openai_api_key: str,
    openai_model: str,
) -> LLMRuntimeConfig:
    """
    根据 Web 表单构造 LLMRuntimeConfig。mode: ollama | openai
    校验失败抛出 ValueError。
    """
    m = (mode or "ollama").strip().lower()
    if m not in ("ollama", "openai"):
        m = "ollama"
    temp = settings.agent_temperature
    if m == "ollama":
        name = (ollama_model or "").strip()
        if not name:
            raise ValueError("请选择或填写 Ollama 模型")
        host = (ollama_host or "http://127.0.0.1:11434").strip().rstrip("/")
        base = normalize_openai_base(host)
        return LLMRuntimeConfig(base_url=base, api_key="ollama", model=name, temperature=temp)
    base_in = (openai_base_url or "").strip()
    key = (openai_api_key or "").strip()
    name = (openai_model or "").strip()
    if not base_in:
        raise ValueError("请填写 OpenAI 兼容 API 地址")
    if not key:
        raise ValueError("请填写 API Key")
    if not name:
        raise ValueError("请填写模型名称")
    base = normalize_openai_base(base_in)
    return LLMRuntimeConfig(base_url=base, api_key=key, model=name, temperature=temp)
