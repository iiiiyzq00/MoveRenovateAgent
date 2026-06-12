"""
LLM 客户端封装 — 统一 OpenAI-compatible API 调用。

Usage:
    from app.agent.llm_client import LLMClient
    client = LLMClient()
    response = await client.chat("Hello")
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_openai import ChatOpenAI

from app.config import get_config

logger = logging.getLogger(__name__)


class LLMClient:
    """
    LLM 客户端 — 封装 OpenAI-compatible Chat API。

    支持: DeepSeek / OpenAI / Anthropic（通过 LiteLLM 代理）
    """

    def __init__(self, temperature: float | None = None) -> None:
        cfg = get_config()

        self.model = ChatOpenAI(
            model=cfg.llm_model,
            api_key=cfg.llm_api_key,
            base_url=cfg.llm_base_url,
            temperature=temperature if temperature is not None else cfg.llm_temperature,
            max_tokens=cfg.llm_max_tokens,
        )

        self._default_temperature = cfg.llm_temperature

    async def chat(self, prompt: str, **kwargs: Any) -> str:
        """
        发送 prompt 并返回文本响应。

        Args:
            prompt: 完整 prompt 文本
            **kwargs: 覆盖 ChatOpenAI 参数（temperature 等）

        Returns:
            LLM 返回的文本内容
        """
        response = await self.model.ainvoke(prompt, **kwargs)
        return response.content

    async def chat_json(
        self,
        prompt: str,
        expected_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        发送 prompt 并解析 JSON 响应。

        Args:
            prompt: 包含「输出 JSON 格式」指令的 prompt
            expected_keys: 预期必须包含的键列表（用于校验）

        Returns:
            解析后的 dict

        Raises:
            ValueError: JSON 解析失败或缺少必要键
        """
        raw = await self.chat(prompt)

        # 提取 JSON 块（处理 markdown code fence）
        json_str = self._extract_json(raw)

        try:
            result = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}\nRaw response: {raw[:500]}")
            raise ValueError(f"LLM 返回了无效 JSON: {e}")

        # 校验必要键
        if expected_keys:
            missing = [k for k in expected_keys if k not in result]
            if missing:
                logger.warning(f"JSON 缺少预期键: {missing}")

        return result

    def _extract_json(self, raw: str) -> str:
        """从 LLM 响应中提取 JSON 字符串（处理 ```json ... ``` 包裹）。"""
        raw = raw.strip()

        # 去除 markdown code fence
        if raw.startswith("```"):
            lines = raw.split("\n")
            # 去掉首行 ```json 和末行 ```
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines)

        return raw.strip()

    def with_temperature(self, temperature: float) -> "LLMClient":
        """创建具有不同 temperature 的新客户端实例。"""
        return LLMClient(temperature=temperature)
