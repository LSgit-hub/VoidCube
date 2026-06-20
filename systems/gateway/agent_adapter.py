from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Union

import aiohttp

from run_agent import AIAgent

logger = logging.getLogger("gateway.agent_adapter")


class GatewayAgentAdapter:
    def __init__(self, gateway_url: str, session_id: Optional[str] = None):
        self.gateway_url = gateway_url.rstrip("/")
        self.session_id = session_id or str(uuid.uuid4())
        self._client_session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._client_session is None or self._client_session.closed:
            self._client_session = aiohttp.ClientSession()
        return self._client_session

    async def chat_completion(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        session = await self._get_session()
        url = f"{self.gateway_url}/v1/chat/completions"
        
        payload = {
            "model": kwargs.get("model", "default"),
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens"),
            "temperature": kwargs.get("temperature"),
            "tools": kwargs.get("tools"),
            "stream": kwargs.get("stream", False),
        }
        
        async with session.post(url, json=payload) as response:
            if response.status != 200:
                error_text = await response.text()
                raise RuntimeError(f"Gateway request failed: {response.status} - {error_text}")
            return await response.json()

    async def agent_query(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        session = await self._get_session()
        url = f"{self.gateway_url}/v1/agent/query"
        
        payload = {
            "session_id": self.session_id,
            "messages": messages,
            "tools": kwargs.get("tools"),
            "max_tokens": kwargs.get("max_tokens"),
            "temperature": kwargs.get("temperature"),
            "metadata": kwargs.get("metadata"),
        }
        
        async with session.post(url, json=payload) as response:
            if response.status != 200:
                error_text = await response.text()
                raise RuntimeError(f"Gateway request failed: {response.status} - {error_text}")
            return await response.json()

    async def get_session_info(self) -> Dict[str, Any]:
        session = await self._get_session()
        url = f"{self.gateway_url}/v1/sessions/{self.session_id}"
        
        async with session.get(url) as response:
            if response.status != 200:
                error_text = await response.text()
                raise RuntimeError(f"Failed to get session info: {response.status} - {error_text}")
            return await response.json()

    async def delete_session(self) -> Dict[str, Any]:
        session = await self._get_session()
        url = f"{self.gateway_url}/v1/sessions/{self.session_id}"
        
        async with session.delete(url) as response:
            if response.status != 200:
                error_text = await response.text()
                raise RuntimeError(f"Failed to delete session: {response.status} - {error_text}")
            return await response.json()

    async def health_check(self) -> Dict[str, Any]:
        session = await self._get_session()
        url = f"{self.gateway_url}/"
        
        async with session.get(url) as response:
            if response.status != 200:
                error_text = await response.text()
                raise RuntimeError(f"Health check failed: {response.status} - {error_text}")
            return await response.json()

    async def close(self):
        if self._client_session and not self._client_session.closed:
            await self._client_session.close()


class AgentProxy:
    def __init__(
        self,
        mode: str = "local",
        gateway_url: str = "http://localhost:6000",
        **agent_kwargs
    ):
        self.mode = mode.lower()
        self.gateway_url = gateway_url
        self._local_agent: Optional[AIAgent] = None
        self._gateway_adapter: Optional[GatewayAgentAdapter] = None
        self._agent_kwargs = agent_kwargs
        self._initialized = False

    def _ensure_local_agent(self):
        if self._local_agent is None:
            self._local_agent = AIAgent(**self._agent_kwargs)

    def _ensure_gateway_adapter(self):
        if self._gateway_adapter is None:
            self._gateway_adapter = GatewayAgentAdapter(self.gateway_url)

    async def run_conversation(self, query: str, **kwargs) -> str:
        if self.mode == "local":
            self._ensure_local_agent()
            return self._local_agent.run_conversation(query, **kwargs)
        else:
            self._ensure_gateway_adapter()
            messages = [{"role": "user", "content": query}]
            response = await self._gateway_adapter.agent_query(messages, **kwargs)
            return response.get("response", {}).get("content", "")

    async def chat_completion(self, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        if self.mode == "local":
            self._ensure_local_agent()
            return await self._local_agent.chat_completion(messages, **kwargs)
        else:
            self._ensure_gateway_adapter()
            return await self._gateway_adapter.chat_completion(messages, **kwargs)

    async def get_session_info(self) -> Dict[str, Any]:
        if self.mode == "local":
            return {
                "session_id": self._local_agent.session_id if self._local_agent else "N/A",
                "mode": "local",
            }
        else:
            self._ensure_gateway_adapter()
            return await self._gateway_adapter.get_session_info()

    async def health_check(self) -> Dict[str, Any]:
        if self.mode == "local":
            return {"status": "healthy", "mode": "local"}
        else:
            self._ensure_gateway_adapter()
            return await self._gateway_adapter.health_check()

    async def close(self):
        if self._gateway_adapter:
            await self._gateway_adapter.close()


def create_agent(
    mode: str = "local",
    gateway_url: str = "http://localhost:6000",
    **agent_kwargs
) -> AgentProxy:
    return AgentProxy(mode=mode, gateway_url=gateway_url, **agent_kwargs)


def is_gateway_available(gateway_url: str, timeout: int = 5) -> bool:
    async def check():
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                async with session.get(f"{gateway_url.rstrip('/')}/") as response:
                    return response.status == 200
        except Exception:
            return False
    
    return asyncio.run(check())