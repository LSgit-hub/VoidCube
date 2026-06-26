import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    enable_task_polling: bool = False


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

        @asynccontextmanager
        async def _agent_lifespan(app: FastAPI):
            del app
            svc_id = await self.register_with_gateway()
            if svc_id:
                logger.info("Agent registered with gateway: %s", svc_id)
            else:
                logger.warning("Agent failed to register with gateway")

            if self.config.enable_task_polling:
                self._task_polling_task = asyncio.create_task(self._task_polling_loop())
                logger.info("Task polling loop started")
            else:
                self._task_polling_task = None
                logger.info("Task polling loop disabled; agent is serving as the active API-A runtime only")
            
            try:
                yield
            finally:
                if self._task_polling_task:
                    self._task_polling_task.cancel()
                    try:
                        await self._task_polling_task
                    except asyncio.CancelledError:
                        pass
                    logger.info("Task polling loop stopped")
                # Deregister on shutdown
                try:
                    import aiohttp
                    async with aiohttp.ClientSession() as s:
                        await s.post(
                            f"{self.config.gateway_address}/deregister/{self._service_name()}",
                            timeout=aiohttp.ClientTimeout(total=5),
                        )
                except Exception:
                    pass

        self.app = FastAPI(
            title="VoidCube Agent Instance",
            version="1.0",
            lifespan=_agent_lifespan,
        )
        self._session_data: Dict[str, Dict[str, Any]] = {}
        self._runtime_paths = self._initialize_runtime_paths()
        # ── Scene state (baseline §3.5 / §8.1) ──
        # The Agent (API-A) is the learning/code-editing execution body.
        # It reports its own scene to the gateway so the supervisor's
        # /ui/state can surface it on the CLI status bar.  The supervisor
        # never reports `learning` / `code_editing` / `executing` —
        # those are Agent scenes exclusively.
        self._scene: str = "idle"
        self._scene_lock = asyncio.Lock()
        self._scene_task_id: Optional[str] = None  # task being executed, if any
        self._scene_changed_at: datetime = datetime.now()
        # How long an "executing" scene survives without explicit
        # re-assertion.  After this, the agent reverts to "idle" so the
        # UI does not get stuck.
        self._scene_idle_timeout_seconds: float = 60.0
        self._setup_routes()
        self._task_polling_task: Optional[asyncio.Task] = None

    # ── Scene legal values (baseline §8.1) ──
    AGENT_LEGAL_SCENES: frozenset = frozenset(
        {"idle", "learning", "code_editing", "executing"}
    )

    def _setup_routes(self):
        self.app.add_api_route("/", self.health_check, methods=["GET"])
        self.app.add_api_route("/health", self.health_check, methods=["GET"])
        self.app.add_api_route("/chat", self.handle_chat, methods=["POST"])
        self.app.add_api_route("/v1/agent/query", self.handle_agent_query, methods=["POST"])
        self.app.add_api_route("/v1/chat/completions", self.handle_chat_completions, methods=["POST"])
        self.app.add_api_route("/memory", self.handle_memory_operation, methods=["POST"])
        # ── Scene endpoints (baseline §8.1) ──
        self.app.add_api_route("/v1/agent/scene", self.get_agent_scene, methods=["GET"])
        self.app.add_api_route("/v1/agent/scene", self.set_agent_scene, methods=["POST"])

    def _service_name(self) -> str:
        return f"agent-{self.config.active_slot}"

    async def health_check(self):
        # Auto-revert stale "executing" / "learning" / "code_editing" scenes
        # to "idle" so the status bar does not get stuck.
        await self._maybe_revert_stale_scene()
        return {
            "status": "healthy",
            "agent_id": self._service_name(),
            "service_name": self._service_name(),
            "slot_id": self.config.active_slot,
            "body_version": self.config.body_version,
            "body_worktree": self.config.body_worktree,
            "body_runtime": self.config.body_runtime,
            "enable_task_polling": self.config.enable_task_polling,
            "runtime_paths": self._runtime_paths,
            "scene": self._scene,
            "scene_changed_at": self._scene_changed_at.isoformat(),
            "scene_task_id": self._scene_task_id,
            "timestamp": datetime.now().isoformat()
        }

    # ── Scene control (baseline §8.1) ──

    async def get_agent_scene(self) -> Dict[str, Any]:
        await self._maybe_revert_stale_scene()
        return {
            "agent_id": self._service_name(),
            "scene": self._scene,
            "scene_changed_at": self._scene_changed_at.isoformat(),
            "scene_task_id": self._scene_task_id,
            "legal_scenes": sorted(self.AGENT_LEGAL_SCENES),
        }

    async def set_agent_scene(self, request: dict) -> Dict[str, Any]:
        scene = str(request.get("scene") or "").strip()
        if scene not in self.AGENT_LEGAL_SCENES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid agent scene={scene!r}. Legal scenes: "
                    f"{sorted(self.AGENT_LEGAL_SCENES)}"
                ),
            )
        async with self._scene_lock:
            self._scene = scene
            self._scene_changed_at = datetime.now()
            self._scene_task_id = request.get("task_id") or None
        # Best-effort push to gateway; never let this break the call.
        try:
            await self._push_scene_to_gateway()
        except Exception as exc:
            logger.debug("Agent → gateway scene push failed: %s", exc)
        return await self.get_agent_scene()

    async def _maybe_revert_stale_scene(self) -> None:
        """Revert to `idle` if the active scene is stale.

        Non-idle scenes are time-bounded.  If the agent crashed or hung
        mid-task, this prevents the status bar from being stuck in
        `learning` / `executing` forever.
        """
        if self._scene == "idle":
            return
        age = (datetime.now() - self._scene_changed_at).total_seconds()
        if age < self._scene_idle_timeout_seconds:
            return
        async with self._scene_lock:
            if self._scene == "idle":
                return
            logger.info(
                "Agent scene %r aged %.1fs (timeout=%.1fs); reverting to idle",
                self._scene, age, self._scene_idle_timeout_seconds,
            )
            self._scene = "idle"
            self._scene_changed_at = datetime.now()
            self._scene_task_id = None
            try:
                await self._push_scene_to_gateway()
            except Exception:
                pass

    async def _push_scene_to_gateway(self) -> None:
        """Push the current scene to the gateway's activity state."""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "activity_kind": "agent_scene",
                    "source_service": self._service_name(),
                    "metadata": {
                        "scene": self._scene,
                        "task_id": self._scene_task_id,
                    },
                }
                async with session.post(
                    f"{self.config.gateway_address}/admin/activity/touch",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    if resp.status >= 400:
                        logger.debug("Gateway rejected scene push: %d", resp.status)
        except Exception as exc:
            logger.debug("Agent scene push error: %s", exc)

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

            # ── Agent scene: executing (user-task handling) ──
            await self.set_agent_scene({"scene": "executing"})

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
        finally:
            # Scene reverts to idle after the call returns; the
            # stale-timeout in `_maybe_revert_stale_scene` is a safety
            # net for crashed handlers.
            try:
                await self.set_agent_scene({"scene": "idle"})
            except Exception:
                pass

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

    async def handle_chat_completions(self, request: dict):
        """Transparently proxy chat completions to the configured LLM provider.

        Accepts the full OpenAI-compatible payload (messages, model, tools,
        stream, etc.) and forwards it to the LLM API.  Supports streaming
        via Server-Sent Events when ``stream: true``.
        """
        from fastapi.responses import StreamingResponse

        messages = request.get("messages") or []
        if not messages:
            raise HTTPException(status_code=400, detail="messages is required")

        requested_model = str(request.get("model") or "").strip()
        is_stream = request.get("stream", False)
        tools = request.get("tools")
        tool_choice = request.get("tool_choice")
        temperature = request.get("temperature")
        max_tokens = request.get("max_tokens")

        # Resolve provider credentials from the canonical runtime/provider config.
        import aiohttp
        try:
            runtime = self._resolve_active_runtime()
            api_key = runtime.get("api_key", "")
            base_url = runtime.get("base_url", "")
            runtime_model = str(runtime.get("model") or "").strip()
        except Exception:
            api_key = os.getenv("AGENT_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
            base_url = os.getenv("AGENT_BASE_URL", "")
            runtime_model = str(os.getenv("AGENT_MODEL", "")).strip()

        model = runtime_model
        if requested_model and runtime_model and requested_model != runtime_model:
            logger.info(
                "Ignoring chat completion request model override %r; using active runtime model %r",
                requested_model,
                runtime_model,
            )
        elif requested_model and not runtime_model:
            model = requested_model

        if not api_key or not base_url:
            raise HTTPException(status_code=503, detail="Agent LLM provider not configured")

        api_url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "model": model,
            "messages": messages,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
        if is_stream:
            payload["stream"] = True

        if is_stream:
            async def _stream_sse():
                async with aiohttp.ClientSession() as session:
                    async with session.post(api_url, headers=headers, json=payload) as resp:
                        if resp.status != 200:
                            err = await resp.text()
                            yield f"data: {{\"error\": \"upstream {resp.status}: {err[:200]}\"}}\n\n"
                            yield "data: [DONE]\n\n"
                            return
                        async for line in resp.content:
                            yield line.decode("utf-8", errors="replace")
            return StreamingResponse(_stream_sse(), media_type="text/event-stream")

        # Non-streaming
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    logger.warning(
                        "Agent chat completions upstream error: status=%s detail=%s",
                        resp.status,
                        err[:200],
                    )
                    raise HTTPException(
                        status_code=502,
                        detail=f"LLM upstream error: {resp.status} {err[:160]}".strip(),
                    )
                result = await resp.json()
                result["slot_id"] = self.config.active_slot
                result["body_version"] = self.config.body_version
                result["agent_id"] = self._service_name()
                return result

    def _latest_user_message(self, messages: List[Dict[str, Any]]) -> str:
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            if message.get("role") == "user" and message.get("content"):
                return str(message["content"])
        return ""

    def _resolve_active_runtime(self) -> Dict[str, str]:
        """Resolve the current API-A runtime from the canonical provider config."""
        from VoidCube_cli.config import (
            get_active_model_config,
            get_active_provider_key,
            load_config,
        )
        from VoidCube_cli.runtime_provider import resolve_runtime_provider

        cfg = load_config()
        active_provider = get_active_provider_key(cfg) or None
        active_model = get_active_model_config(cfg)
        runtime = resolve_runtime_provider(requested=active_provider)

        base_url = str(runtime.get("base_url") or "").strip()
        api_key = str(runtime.get("api_key") or "").strip()
        if not api_key and base_url and "openrouter.ai" not in base_url:
            api_key = "no-key-required"

        return {
            "provider": str(runtime.get("provider") or active_model.get("provider") or "").strip(),
            "api_mode": str(runtime.get("api_mode") or active_model.get("api_mode") or "").strip(),
            "base_url": base_url,
            "api_key": api_key,
            "model": str(
                runtime.get("model")
                or active_model.get("model")
                or active_model.get("default")
                or ""
            ).strip(),
        }

    async def _generate_response(self, message: str, context: Dict[str, Any]) -> str:
        """Simple LLM call for basic agent queries (used by /chat and /v1/agent/query).

        For full chat-completions with tools/streaming, use handle_chat_completions.
        """
        try:
            import aiohttp

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
                    "max_tokens": 500,
                }
                async with session.post(url, headers=headers, json=payload) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result["choices"][0]["message"]["content"].strip()
            return f"Response to: {message}"
        except Exception as e:
            logger.warning(f"LLM call failed: {e}")
            return f"Processed request: {message}"

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

    async def _task_polling_loop(self):
        """Periodically poll the gateway for approved Agent-executable tasks.
        
        Agent pulls tasks from the shared task manager (via gateway) instead of
        being pushed tasks by the supervisor. This follows the principle that
        the supervisor only manages/decides, and the agent executes.
        """
        import aiohttp
        
        poll_interval = 30
        processing_tasks = set()
        
        while True:
            try:
                await asyncio.sleep(poll_interval)

                tasks = await self._fetch_agent_executable_tasks()
                if not tasks:
                    continue
                
                for task in tasks:
                    task_id = task.get("task_id", "")
                    if not task_id or task_id in processing_tasks:
                        continue
                    
                    processing_tasks.add(task_id)
                    logger.info(
                        "Found approved agent task: %s (%s)",
                        task.get("title", task_id),
                        task.get("execution_kind") or task.get("governance_task_type") or task.get("task_family"),
                    )
                    
                    try:
                        claimed = await self._update_gateway_task_decision(
                            task_id,
                            decision="running",
                            reason="Agent pulled task for execution in AUTO mode.",
                            actor="agent",
                            context={"source": "agent_task_polling"},
                        )
                        if not claimed:
                            logger.warning("Skipping task %s because running claim failed", task_id)
                            continue

                        result = await self._execute_approved_task(task)
                        decision = "completed" if result.get("status") == "completed" else "failed"
                        updated = await self._update_gateway_task_decision(
                            task_id,
                            decision=decision,
                            reason=result.get("reason", "Task finished by agent"),
                            actor="agent",
                            context={
                                "status": result.get("status", decision),
                                "parsed_output": result.get("parsed_output"),
                                "tool_events": result.get("tool_events", []),
                                "api_calls": result.get("api_calls", 0),
                                "model": result.get("model", ""),
                                "source": "agent_task_polling",
                            },
                        )
                        if updated:
                            logger.info("Task %s marked as %s", task_id, decision)
                        else:
                            logger.warning("Failed to mark task %s as %s", task_id, decision)
                    except Exception as e:
                        logger.error("Failed to execute task %s: %s", task_id, e)
                        await self._update_gateway_task_decision(
                            task_id,
                            decision="failed",
                            reason=f"Agent task execution crashed: {e}",
                            actor="agent",
                            context={"source": "agent_task_polling", "error": str(e)},
                        )
                    finally:
                        processing_tasks.discard(task_id)
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Task polling loop error: %s", e)
                await asyncio.sleep(poll_interval)

    async def _fetch_agent_executable_tasks(self) -> List[Dict[str, Any]]:
        import aiohttp

        requests = (
            {"task_type": "self_learning"},
            {"execution_kind": "body_improvement"},
        )
        tasks: List[Dict[str, Any]] = []
        seen: set[str] = set()

        async with aiohttp.ClientSession() as session:
            for params in requests:
                url = f"{self.config.gateway_address}/v1/tasks"
                query = dict(params)
                try:
                    async with session.get(
                        url,
                        params=query,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status != 200:
                            logger.debug("Failed to fetch tasks for %s: %d", query, resp.status)
                            continue
                        result = await resp.json()
                except Exception as exc:
                    logger.debug("Task polling failed for %s: %s", query, exc)
                    continue

                for task in result.get("tasks", []):
                    task_id = str(task.get("task_id") or "").strip()
                    if not task_id or task_id in seen:
                        continue
                    seen.add(task_id)
                    tasks.append(task)
        return tasks

    async def _execute_approved_task(self, task: dict) -> dict:
        """Execute an approved Agent task via the active API-A agent."""
        execution_kind = self._task_execution_kind(task)
        if execution_kind not in {"self_learning", "body_improvement"}:
            return {"status": "rejected", "reason": f"Agent does not execute task kind: {execution_kind or 'unknown'}"}

        title = task.get("title", "Learning task")
        summary = task.get("summary", "")
        prompt = self._build_agent_task_prompt(task, execution_kind=execution_kind)

        if not prompt:
            return {"status": "rejected", "reason": "No prompt or summary provided"}

        task_id = task.get("task_id", "")
        scene = "learning" if execution_kind == "self_learning" else "code_editing"
        await self.set_agent_scene({"scene": scene, "task_id": task_id})

        try:
            agent = self._build_task_agent(
                task_id=task_id,
                execution_kind=execution_kind,
                toolsets=self._task_toolsets(execution_kind),
            )
            result = await asyncio.to_thread(
                agent.run_conversation,
                user_message=prompt,
                task_id=f"agent-task-{task_id}",
            )
            final_response = result.get("final_response", "")
            status = "completed"
            if result.get("failed") or result.get("partial"):
                status = "failed"

            tool_events = self._collect_tool_events(result.get("messages", []))
            parsed = self._parse_task_report(final_response, execution_kind=execution_kind)

            return {
                "status": status,
                "final_response": final_response,
                "parsed_output": parsed,
                "tool_events": tool_events,
                "api_calls": result.get("api_calls", 0),
                "model": getattr(agent, "model", ""),
                "reason": (
                    f"{execution_kind} task '{title}' completed"
                    if status == "completed"
                    else f"{execution_kind} task '{title}' failed: {result.get('error', 'unknown error')}"
                ),
            }
        except Exception as e:
            logger.error(f"Approved task execution failed: {e}")
            return {"status": "error", "reason": str(e)}
        finally:
            try:
                await self.set_agent_scene({"scene": "idle"})
            except Exception:
                pass

    def _task_execution_kind(self, task: Dict[str, Any]) -> str:
        execution_kind = str(task.get("execution_kind") or "").strip().lower()
        if execution_kind == "body_improvement":
            return "body_improvement"
        task_type = str(task.get("governance_task_type") or task.get("task_type") or "").strip().lower()
        if task_type == "self_learning":
            return "self_learning"
        task_family = str(task.get("task_family") or "").strip().lower()
        if task_family == "self_learning":
            return "self_learning"
        return execution_kind or task_type or task_family

    def _task_toolsets(self, execution_kind: str) -> List[str]:
        if execution_kind == "body_improvement":
            return ["file", "terminal", "code_execution"]
        return ["learn"]

    def _build_agent_task_prompt(self, task: Dict[str, Any], *, execution_kind: str) -> str:
        title = str(task.get("title") or "Agent task").strip()
        summary = str(task.get("summary") or "").strip()
        constraints = dict(task.get("constraints") or {})

        if execution_kind == "body_improvement":
            worktree_path = str(
                constraints.get("worktree_path")
                or constraints.get("target_worktree")
                or ""
            ).strip()
            editable_dirs = constraints.get("editable_dirs") or []
            forbidden_patterns = constraints.get("forbidden_patterns") or []
            max_files = constraints.get("max_files_changed")
            details = [f"[AUTO Body Improvement Task] {title}"]
            if summary:
                details.append(summary)
            details.append("Edit the shell body code directly and implement the approved improvement.")
            if worktree_path:
                details.append(f"Worktree path: {worktree_path}")
            if editable_dirs:
                details.append(f"Editable dirs: {', '.join(str(x) for x in editable_dirs)}")
            if forbidden_patterns:
                details.append(f"Forbidden patterns: {', '.join(str(x) for x in forbidden_patterns)}")
            if max_files:
                details.append(f"Max files changed: {max_files}")
            details.append("Produce a concise implementation summary with the concrete files changed and reasoning.")
            return "\n\n".join(details)

        prompt = summary if summary else title
        return (
            f"[AUTO Learning Task] {title}\n\n"
            f"{prompt}\n\n"
            "Execute this research task thoroughly. Produce structured findings and conclusions."
        )

    def _build_task_agent(self, *, task_id: str, execution_kind: str, toolsets: List[str]):
        from run_agent import AIAgent

        runtime = self._resolve_active_runtime()
        return AIAgent(
            model=runtime.get("model") or "",
            api_key=runtime.get("api_key") or "",
            base_url=runtime.get("base_url") or "",
            provider=runtime.get("provider") or "",
            api_mode=runtime.get("api_mode") or "",
            max_iterations=30,
            enabled_toolsets=toolsets,
            quiet_mode=True,
            verbose_logging=False,
            session_id=f"agent-runtime-{task_id}-{uuid.uuid4().hex[:8]}",
            platform="agent",
            skip_context_files=False,
            skip_memory=True,
            persist_session=False,
        )

    def _collect_tool_events(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        tool_events = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "")
            if role != "tool":
                continue
            tool_name = msg.get("name", "") or msg.get("tool_name", "")
            tool_args_preview = str(msg.get("tool_args", "") or "")[:120]
            result_preview = str(msg.get("content", "") or "")[:200]
            tool_events.append({
                "tool": tool_name,
                "kind": "tool",
                "args_preview": tool_args_preview,
                "result_preview": result_preview,
            })
        return tool_events

    def _parse_task_report(self, final_response: str, *, execution_kind: str) -> Optional[Dict[str, Any]]:
        import re

        text = str(final_response or "").strip()
        if not text:
            return None

        try:
            direct = json.loads(text)
            if isinstance(direct, dict) and self._is_expected_task_report(direct, execution_kind=execution_kind):
                return direct
        except Exception:
            pass

        parsed = None
        fence = re.findall(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE)
        for c in reversed(fence):
            try:
                p = json.loads(c.strip())
                if isinstance(p, dict) and self._is_expected_task_report(p, execution_kind=execution_kind):
                    parsed = p
                    break
            except Exception:
                pass
        return parsed

    def _is_expected_task_report(self, payload: Dict[str, Any], *, execution_kind: str) -> bool:
        if execution_kind == "body_improvement":
            return any(key in payload for key in ("changed_files", "implementation_summary", "commit_message"))
        return "technology_evaluations" in payload

    async def _update_gateway_task_decision(
        self,
        task_id: str,
        *,
        decision: str,
        reason: str,
        actor: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        import aiohttp

        url = f"{self.config.gateway_address}/v1/tasks/{task_id}/decision"
        payload = {
            "decision": decision,
            "reason": reason,
            "actor": actor,
            "context": dict(context or {}),
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        return True
                    detail = await resp.text()
                    logger.warning(
                        "Gateway task decision update failed for %s (%s): %d %s",
                        task_id,
                        decision,
                        resp.status,
                        detail[:200],
                    )
        except Exception as exc:
            logger.warning(
                "Gateway task decision update error for %s (%s): %s",
                task_id,
                decision,
                exc,
            )
        return False

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
    parser.add_argument(
        "--enable-task-polling",
        action="store_true",
        default=str(os.getenv("VOIDCUBE_AGENT_ENABLE_TASK_POLLING", "")).strip().lower() in {"1", "true", "yes", "on"},
        help="Enable daemon-side task polling. Disabled by default so CLI /auto remains the canonical executor.",
    )
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
        enable_task_polling=bool(args.enable_task_polling),
    )
    agent = AgentInstance(config)
    
    import asyncio
    asyncio.run(agent.start())
