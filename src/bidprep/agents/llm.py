from __future__ import annotations

import json
import re
from typing import Any, Optional, Type, TypeVar

import httpx
from openai import OpenAI
from pydantic import BaseModel

from bidprep.config import settings
from bidprep.llm_runtime import LLMRuntimeConfig, normalize_openai_base, runtime_from_settings

T = TypeVar("T", bound=BaseModel)


def get_client(llm: LLMRuntimeConfig | None = None) -> OpenAI:
    cfg = llm if llm is not None else runtime_from_settings()
    base = (cfg.base_url or "").strip().rstrip("/")
    if not base.endswith("/v1"):
        base = normalize_openai_base(base)
    kw: dict[str, Any] = {
        "base_url": base,
        "api_key": cfg.api_key or "sk-placeholder",
    }
    if not settings.llm_http_trust_env:
        kw["http_client"] = httpx.Client(trust_env=False, timeout=httpx.Timeout(120.0))
    return OpenAI(**kw)


def _model_for(llm: LLMRuntimeConfig | None) -> str:
    if llm is not None:
        return llm.model
    return settings.llm_model


def _temp_for(llm: LLMRuntimeConfig | None, override: Optional[float]) -> float:
    if override is not None:
        return override
    if llm is not None:
        return llm.temperature
    return settings.agent_temperature


def chat_json(
    system: str,
    user: str,
    model_cls: Type[T],
    temperature: Optional[float] = None,
    llm: LLMRuntimeConfig | None = None,
) -> T:
    client = get_client(llm)
    temp = _temp_for(llm, temperature)
    # 要求输出可解析 JSON；部分 Ollama 模型不支持 response_format
    schema_hint = json.dumps(model_cls.model_json_schema(), ensure_ascii=False)[:8000]
    user2 = f"{user}\n\n请只输出一个 JSON 对象，字段必须满足下列 JSON Schema（可省略未知字段）：\n{schema_hint}"
    model = _model_for(llm)
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=temp,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user2},
            ],
            response_format={"type": "json_object"},
        )
    except Exception:
        resp = client.chat.completions.create(
            model=model,
            temperature=temp,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user2 + "\n只输出 JSON，不要 Markdown。"},
            ],
        )
    content = resp.choices[0].message.content or "{}"
    content = content.strip()
    if content.startswith("```"):
        content = _strip_fence(content)
    data = json.loads(content)
    return model_cls.model_validate(data)


def _strip_fence(s: str) -> str:
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def chat_json_raw(
    system: str,
    user: str,
    temperature: Optional[float] = None,
    llm: LLMRuntimeConfig | None = None,
) -> dict[str, Any]:
    client = get_client(llm)
    temp = _temp_for(llm, temperature)
    model = _model_for(llm)
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=temp,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
    except Exception:
        resp = client.chat.completions.create(
            model=model,
            temperature=temp,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    content = (resp.choices[0].message.content or "{}").strip()
    if content.startswith("```"):
        content = _strip_fence(content)
    return json.loads(content)
