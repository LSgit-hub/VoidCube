import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent_instance")


class AgentConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 6080
    gateway_address: str = "http://127.0.0.1:6000"
    active_slot: str = "slot-A"
    body_worktree: str = ""
    body_runtime: str = ""
    body_logs: str = ""
    body_version: str = "unknown"


class MessageRequest(BaseModel):
    message: str
    session_id: str = None
    context: Dict[str, Any] = {}


class MemoryOperation(BaseModel):
    operation: str  # "read", "write", "search", "delete"
    key: str = None
    value: str = None
    query: str = None
    namespace: str = "default"


class AgentInstance:
    def __init__(self, config: AgentConfig = None):
        self.config = config or AgentConfig()
        self.app = FastAPI(title="VoidCube Agent Instance", version="1.0")
        self._session_data: Dict[str, Dict[str, Any]] = {}
        self._runtime_paths = self._initialize_runtime_paths()
        self._setup_routes()

    def _setup_routes(self):
        self.app.add_api_route("/", self.health_check, methods=["GET"])
        self.app.add_api_route("/health", self.health_check, methods=["GET"])
        self.app.add_api_route("/chat", self.handle_chat, methods=["POST"])
        self.app.add_api_route("/v1/agent/query", self.handle_agent_query, methods=["POST"])
        self.app.add_api_route("/v1/chat/completions", self.handle_chat_completions, methods=["POST"])
        self.app.add_api_route("/memory", self.handle_memory_operation, methods=["POST"])
        self.app.add_api_route("/v1/agent/governance-task", self.handle_governance_task, methods=["POST"])

    def _service_name(self) -> str:
        return f"agent-{self.config.active_slot}"

    async def health_check(self):
        return {
            "status": "healthy",
            "agent_id": self._service_name(),
            "service_name": self._service_name(),
            "slot_id": self.config.active_slot,
            "body_version": self.config.body_version,
            "body_worktree": self.config.body_worktree,
            "body_runtime": self.config.body_runtime,
            "runtime_paths": self._runtime_paths,
            "timestamp": datetime.now().isoformat()
        }

    async def handle_chat(self, request: dict):
        try:
            message = request.get("message")
            session_id = request.get("session_id")
            context = request.get("context", {})
            
            if not message:
                raise HTTPException(status_code=400, detail="Message is required")
            
            if session_id not in self._session_data:
                self._session_data[session_id] = {"messages": [], "context": {}}
            
            self._session_data[session_id]["messages"].append({
                "role": "user",
                "content": message,
                "timestamp": datetime.now().isoformat()
            })
            
            response = await self._generate_response(message, context)
            
            self._session_data[session_id]["messages"].append({
                "role": "assistant",
                "content": response,
                "timestamp": datetime.now().isoformat()
            })
            self._persist_session_snapshot(session_id)
            
            return {
                "response": response,
                "session_id": session_id,
                "context": context,
                "slot_id": self.config.active_slot,
            }
        
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error handling chat: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def handle_agent_query(self, request: dict):
        messages = request.get("messages") or []
        latest_user_message = self._latest_user_message(messages)
        if not latest_user_message:
            raise HTTPException(status_code=400, detail="messages must include a user message")

        session_id = request.get("session_id")
        context = dict(request.get("context") or {})
        response = await self.handle_chat(
            {
                "message": latest_user_message,
                "session_id": session_id,
                "context": context,
            }
        )
        return {
            "response": response["response"],
            "session_id": response["session_id"],
            "slot_id": self.config.active_slot,
            "body_version": self.config.body_version,
            "agent_id": self._service_name(),
        }

    async def handle_governance_task(self, request: dict):
        """Execute a supervisor-assigned governance task via sub-agent.

        Only self_learning tasks are executed by the Agent. Other task types
        (body_upgrade, memory_maintenance) are handled by executors.
        """
        task_type = request.get("task_type") or request.get("governance_task_type", "")
        if task_type != "self_learning":
            return {
                "status": "rejected",
                "reason": f"Agent only executes self_learning tasks, got '{task_type}'",
            }

        title = request.get("title", "Learning task")
        prompt = request.get("prompt", "")
        if not prompt:
            return {"status": "rejected", "reason": "No prompt provided"}

        try:
            from tools.delegate_tool import _build_child_agent, _resolve_delegation_credentials
            from VoidCube_cli.config import load_config

            cfg = load_config()
            creds = _resolve_delegation_credentials(cfg, None) or {}
            child = _build_child_agent(
                parent_agent=None,
                goal=prompt,
                enabled_toolsets=["learn"],
                max_iterations=30,
                delegation_config=cfg.get("delegation", {}),
            )
            result = child.run_conversation(
                user_message=prompt,
                task_id=f"governance-{request.get('task_id', '')}",
            )
            final_response = result.get("final_response", "")
            # Parse structured output from sub-agent
            import re, json
            parsed = None
            fence = re.findall(r"```(?:json)?\s*\n(.*?)\n```", final_response, re.DOTALL | re.IGNORECASE)
            for c in reversed(fence):
                try:
                    p = json.loads(c.strip())
                    if isinstance(p, dict) and "technology_evaluations" in p:
                        parsed = p; break
                except Exception:
                    pass
            return {
                "status": "completed",
                "final_response": final_response,
                "parsed_output": parsed,
            }
        except Exception as e:
            logger.error(f"Governance task failed: {e}")
            return {"status": "error", "error": str(e)}

    async def handle_chat_completions(self, request: dict):
        messages = request.get("messages") or []
        latest_user_message = self._latest_user_message(messages)
        if not latest_user_message:
            raise HTTPException(status_code=400, detail="messages must include a user message")

        content = await self._generate_response(latest_user_message, {})
        return {
            "id": f"voidcube-{self.config.active_slot}-{int(datetime.now().timestamp())}",
            "object": "chat.completion",
            "created": int(datetime.now().timestamp()),
            "model": "voidcube-agent-instance",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content,
                    },
                    "finish_reason": "stop",
                }
            ],
            "slot_id": self.config.active_slot,
            "body_version": self.config.body_version,
        }

    def _latest_user_message(self, messages: List[Dict[str, Any]]) -> str:
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            if message.get("role") == "user" and message.get("content"):
                return str(message["content"])
        return ""

    async def _generate_response(self, message: str, context: Dict[str, Any]) -> str:
        try:
            import aiohttp
            
            llm_response = await self._call_llm(message)
            
            tool_calls = self._parse_tool_calls(llm_response)
            
            if tool_calls:
                tool_results = await self._execute_tools(tool_calls)
                final_response = await self._summarize_results(llm_response, tool_results)
                return final_response
            
            return llm_response
        
        except Exception as e:
            logger.warning(f"Error generating response: {e}")
            return f"Processed your request: {message[:50]}..."

    async def _call_llm(self, message: str) -> str:
        try:
            import aiohttp

            # Agent uses API-A (baseline §4.3) — configurable provider/model
            api_key = os.getenv("AGENT_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
            if not api_key:
                return f"Processed request: {message}"
            
            base_url = os.getenv("AGENT_BASE_URL", "https://api.deepseek.com/v1")
            model = os.getenv("AGENT_MODEL", "deepseek-chat")
            
            async with aiohttp.ClientSession() as session:
                url = f"{base_url.rstrip('/')}/chat/completions"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                }
                
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": message}],
                    "max_tokens": 500
                }
                
                async with session.post(url, headers=headers, json=payload) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result["choices"][0]["message"]["content"].strip()
            
            return f"Response to: {message}"
        
        except Exception as e:
            logger.warning(f"LLM call failed: {e}")
            return f"Processed request: {message}"

    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        return []

    async def _execute_tools(self, tool_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {}

    async def _summarize_results(self, original_response: str, tool_results: Dict[str, Any]) -> str:
        return original_response

    async def handle_memory_operation(self, request: dict):
        try:
            operation = request.get("operation")
            
            if operation == "read":
                return await self._read_memory(request)
            elif operation == "write":
                return await self._write_memory(request)
            elif operation == "search":
                return await self._search_memory(request)
            elif operation == "delete":
                return await self._delete_memory(request)
            else:
                raise HTTPException(status_code=400, detail="Invalid operation")
        
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error handling memory operation: {e}")
            raise HTTPException(status_code=500, detail=str(e))


    # NOTE(A-02): Memory operations (_read/_write/_search/_delete) currently
    # call gateway directly without governance-layer access control.
    # Per constitution §5.4, gateway should enforce memory access policies.
    # This is acceptable for Phase 1 single-user mode.

    async def _read_memory(self, request: dict):
        try:
            import aiohttp
            
            memory_id = request.get("key")
            namespace = request.get("namespace", "default")
            
            async with aiohttp.ClientSession() as session:
                url = f"{self.config.gateway_address}/mem/memories/{memory_id}"
                
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.json()
                    elif response.status == 404:
                        raise HTTPException(status_code=404, detail="Memory not found")
            
            raise HTTPException(status_code=500, detail="Failed to read memory")
        
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Memory read failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def _write_memory(self, request: dict):
        try:
            import aiohttp

            # A-02: Require governance trace_id for all memory writes
            trace_id = request.get("trace_id") or request.get("metadata", {}).get("trace_id")
            if not trace_id:
                raise HTTPException(status_code=400, detail="trace_id required for memory write governance")

            content = request.get("value")
            namespace = request.get("namespace", "default")
            memory_id = request.get("key")
            
            async with aiohttp.ClientSession() as session:
                url = f"{self.config.gateway_address}/mem/memories"
                payload = {
                    "memory_id": memory_id,
                    "namespace": namespace,
                    "content": content,
                    "tags": request.get("tags", []),
                    "metadata": request.get("metadata", {})
                }
                
                async with session.post(url, json=payload) as response:
                    if response.status == 201:
                        return await response.json()
            
            raise HTTPException(status_code=500, detail="Failed to write memory")
        
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Memory write failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def _search_memory(self, request: dict):
        try:
            import aiohttp
            
            query = request.get("query")
            namespace = request.get("namespace")
            
            async with aiohttp.ClientSession() as session:
                url = f"{self.config.gateway_address}/mem/memories/search"
                payload = {
                    "query": query,
                    "namespace": namespace,
                    "limit": request.get("limit", 10)
                }
                
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        return await response.json()
            
            raise HTTPException(status_code=500, detail="Failed to search memory")
        
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Memory search failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def _delete_memory(self, request: dict):
        try:
            import aiohttp
            
            memory_id = request.get("key")
            
            async with aiohttp.ClientSession() as session:
                url = f"{self.config.gateway_address}/mem/memories/{memory_id}"
                
                async with session.delete(url) as response:
                    if response.status == 200:
                        return await response.json()
                    elif response.status == 404:
                        raise HTTPException(status_code=404, detail="Memory not found")
            
            raise HTTPException(status_code=500, detail="Failed to delete memory")
        
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Memory delete failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def register_with_gateway(self):
        import asyncio as _asyncio

        url = f"{self.config.gateway_address}/register"
        payload = {
            "service_name": self._service_name(),
            "service_type": "agent",
            "address": f"http://{self.config.host}:{self.config.port}",
            "health_endpoint": "/health",
            "metadata": {
                "slot_id": self.config.active_slot,
                "body_version": self.config.body_version,
            },
        }

        max_retries = 5
        base_delay = 1.0

        for attempt in range(1, max_retries + 1):
            try:
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, timeout=10) as response:
                        if response.status == 201:
                            result = await response.json()
                            logger.info("Registered with gateway (attempt %d): %s", attempt, result)
                            return result.get("service_id")
                        else:
                            logger.debug(
                                "Gateway registration attempt %d returned status %d",
                                attempt,
                                response.status,
                            )
            except Exception as e:
                logger.debug("Gateway registration attempt %d failed: %s", attempt, e)

            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                logger.info(
                    "Waiting %.1fs before retrying gateway registration (attempt %d/%d)...",
                    delay,
                    attempt + 1,
                    max_retries,
                )
                await _asyncio.sleep(delay)

        logger.warning("Failed to register with gateway after %d attempts", max_retries)
        return None

    async def start(self):
        import uvicorn
        
        await self.register_with_gateway()
        
        logger.info(
            "Starting agent instance on %s:%s (slot=%s, body_version=%s)",
            self.config.host,
            self.config.port,
            self.config.active_slot,
            self.config.body_version,
        )
        await uvicorn.Server(
            uvicorn.Config(
                self.app,
                host=self.config.host,
                port=self.config.port,
                log_level="info"
            )
        ).serve()

    def _ensure_body_slot_layout(self) -> Dict[str, str]:
        """Resolve canonical ``.body-slots/slot-{id}/`` layout.

        Per architecture baseline §3.3, each agent must have independent
        worktree / runtime / logs / meta.  When env vars are not set,
        derive paths from the canonical layout instead of falling back
        to CWD (which would cause slot collisions).
        """
        slots_dir = Path(
            os.environ.get("VOIDCUBE_BODY_SLOTS_DIR", ".body-slots")
        ).resolve()
        slot_root = slots_dir / self.config.active_slot
        worktree = Path(self.config.body_worktree) if self.config.body_worktree else (slot_root / "worktree")
        runtime = Path(self.config.body_runtime) if self.config.body_runtime else (slot_root / "runtime")
        logs = Path(self.config.body_logs) if self.config.body_logs else (slot_root / "logs")
        return {
            "slot_root": str(slot_root),
            "worktree": str(worktree),
            "runtime": str(runtime),
            "logs": str(logs),
        }

    def _initialize_runtime_paths(self) -> Dict[str, str]:
        layout = self._ensure_body_slot_layout()
        runtime_root = Path(layout["runtime"]).resolve()
        logs_root = Path(layout["logs"]).resolve()
        sessions_root = runtime_root / "sessions"
        cache_root = runtime_root / "cache"
        state_root = runtime_root / "state"

        for path in (runtime_root, logs_root, sessions_root, cache_root, state_root):
            path.mkdir(parents=True, exist_ok=True)

        # Write canonical slot meta.json per baseline §3.3
        slot_root = Path(layout["slot_root"])
        slot_root.mkdir(parents=True, exist_ok=True)
        meta = {
            "slot_id": self.config.active_slot,
            "body_version": self.config.body_version,
            "worktree_path": layout["worktree"],
            "runtime_path": layout["runtime"],
            "logs_path": layout["logs"],
            "initialized_at": datetime.now().isoformat(),
        }
        (slot_root / "meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        manifest = {
            "slot_id": self.config.active_slot,
            "body_version": self.config.body_version,
            "body_worktree": self.config.body_worktree,
            "runtime_root": str(runtime_root),
            "logs_root": str(logs_root),
            "sessions_root": str(sessions_root),
            "cache_root": str(cache_root),
            "state_root": str(state_root),
            "initialized_at": datetime.now().isoformat(),
        }

        manifest_path = state_root / "agent-runtime.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return {
            "runtime_root": str(runtime_root),
            "logs_root": str(logs_root),
            "sessions_root": str(sessions_root),
            "cache_root": str(cache_root),
            "state_root": str(state_root),
            "manifest_path": str(manifest_path),
        }


    # NOTE(A-03/A-04): Session snapshots and runtime cache should have a
    # retention policy.  Stale sessions and old meta files should be cleaned
    # up periodically to prevent unbounded disk usage.


    def _cleanup_stale_sessions(self, max_sessions: int = 50) -> None:
        """Remove oldest session files beyond max_sessions (A-03/A-04)."""
        try:
            sessions_root = Path(self._runtime_paths.get("sessions_root", ""))
            if not sessions_root.exists():
                return
            files = sorted(
                sessions_root.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for stale in files[max_sessions:]:
                stale.unlink(missing_ok=True)
        except Exception:
            pass

    def _persist_session_snapshot(self, session_id: str) -> None:
        if not session_id:
            return
        self._cleanup_stale_sessions()
        session = self._session_data.get(session_id)
        if session is None:
            return

        sessions_root = Path(self._runtime_paths["sessions_root"])
        snapshot_path = sessions_root / f"{session_id}.json"
        payload = {
            "session_id": session_id,
            "slot_id": self.config.active_slot,
            "body_version": self.config.body_version,
            "saved_at": datetime.now().isoformat(),
            "data": session,
        }
        snapshot_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VoidCube Agent Instance")
    parser.add_argument("--host", default="127.0.0.1", help="Agent host")
    parser.add_argument("--port", type=int, default=6080, help="Agent port")
    parser.add_argument("--gateway", default=os.getenv("GATEWAY_ADDRESS", "http://127.0.0.1:6000"), help="Gateway address")
    args = parser.parse_args()
    
    config = AgentConfig(
        host=args.host,
        port=args.port,
        gateway_address=args.gateway,
        active_slot=os.getenv("VOIDCUBE_ACTIVE_SLOT", "slot-A"),
        body_worktree=os.getenv("VOIDCUBE_BODY_WORKTREE", ""),
        body_runtime=os.getenv("VOIDCUBE_BODY_RUNTIME", ""),
        body_logs=os.getenv("VOIDCUBE_BODY_LOGS", ""),
        body_version=os.getenv("VOIDCUBE_BODY_VERSION", "unknown"),
    )
    agent = AgentInstance(config)
    
    import asyncio
    asyncio.run(agent.start())
