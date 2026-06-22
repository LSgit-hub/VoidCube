import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("memory_service")


class MemoryEntry(BaseModel):
    memory_id: str
    namespace: str
    content: str
    summary: str = ""
    relevance_score: float = 0.0
    created_at: datetime = None
    updated_at: datetime = None
    accessed_at: datetime = None
    decay_factor: float = 0.0
    tags: List[str] = []
    metadata: Dict[str, Any] = {}


class MemoryQuery(BaseModel):
    query: str
    namespace: Optional[str] = None
    limit: int = 10
    min_score: float = 0.0
    tags: List[str] = []


class CompressionRequest(BaseModel):
    namespace: str
    max_entries: int = 100
    target_size: int = 10000


class MemoryServiceConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 6001
    db_path: str = "./memory.db"
    gateway_address: str = "http://127.0.0.1:6000"
    llm_api_key: Optional[str] = None
    llm_base_url: str = "https://api.deepseek.com"
    decay_interval_hours: int = 24
    compression_interval: int = 3600  # seconds between auto-compression runs


class MemoryService:
    def __init__(self, config: MemoryServiceConfig = None):
        self.config = config or MemoryServiceConfig()
        self._compression_task: asyncio.Task | None = None
        self.app = FastAPI(
            title="VoidCube Memory Service",
            version="1.0",
            lifespan=self._app_lifespan,
        )
        self._db_path = Path(self.config.db_path)
        self._namespace_cache: Dict[str, List[MemoryEntry]] = {}
        self._setup_database()
        self._setup_routes()

    def _setup_database(self):
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL,
                content TEXT NOT NULL,
                summary TEXT,
                relevance_score REAL DEFAULT 0.0,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                accessed_at TEXT,
                decay_factor REAL DEFAULT 0.0,
                tags TEXT,
                metadata TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_namespace ON memories(namespace)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_relevance ON memories(relevance_score)
        ''')
        
        conn.commit()
        conn.close()
        logger.info(f"Memory database initialized at {self._db_path}")

    def _setup_routes(self):
        self.app.add_api_route("/", self.health_check, methods=["GET"])
        self.app.add_api_route("/mem/usage", self.get_mem_usage, methods=["GET"])
        self.app.add_api_route("/memories", self.write_memory, methods=["POST"])
        self.app.add_api_route("/memories/{memory_id}", self.read_memory, methods=["GET"])
        self.app.add_api_route("/memories/{memory_id}", self.update_memory, methods=["PUT"])
        self.app.add_api_route("/memories/{memory_id}", self.delete_memory, methods=["DELETE"])
        self.app.add_api_route("/memories/search", self.search_memories, methods=["POST"])
        self.app.add_api_route("/memories/namespace/{namespace}", self.list_by_namespace, methods=["GET"])
        self.app.add_api_route("/memories/compress", self.compress_memories, methods=["POST"])
        self.app.add_api_route("/memories/decay", self.apply_decay, methods=["POST"])
        self.app.add_api_route("/memories/summarize/{memory_id}", self.summarize_memory, methods=["POST"])

    @asynccontextmanager
    async def _app_lifespan(self, app: FastAPI):
        """Start background compression loop on startup, cancel on shutdown."""
        del app
        self._compression_task = asyncio.create_task(self._compression_loop())
        try:
            yield
        finally:
            if self._compression_task and not self._compression_task.done():
                self._compression_task.cancel()
            try:
                await self._compression_task
            except asyncio.CancelledError:
                pass

    async def _compression_loop(self) -> None:
        """Periodically trigger memory compression (runs in the memory service).

        Per architecture baseline §3.4, Mem is responsible for its own
        maintenance — the supervisor should not be running maintenance
        loops on Mem's behalf.
        """
        while True:
            await asyncio.sleep(self.config.compression_interval)
            try:
                for namespace in list(self._namespace_cache.keys()):
                    await self._compress_namespace(namespace)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Background compression skipped", exc_info=True)

    async def health_check(self):
        return {"status": "healthy", "service": "memory-service"}

    async def get_mem_usage(self) -> Dict[str, Any]:
        """Return cumulative LLM token usage for the memory model.

        NOTE: This endpoint reports the in-process ``memai.llm_client``
        accumulator, which only counts calls routed through
        ``OpenAICompatibleLLMClient`` (the default transport).  The memory
        service itself uses ``aiohttp`` directly for summarisation and does
        NOT populate this counter — so the endpoint will return all zeros
        under normal operation.

        The canonical source for the supervisor's memory-model context
        usage is ``/ui/state.mem_usage`` on the supervisor (port 6002),
        which runs in the same process as the MemAI pipeline.

        This endpoint remains available for external monitoring tools that
        may inject LLM calls through the ``OpenAICompatibleLLMClient`` path.
        """
        try:
            from memai.llm_client import get_memory_token_usage
            usage = get_memory_token_usage()
        except Exception:
            usage = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "request_count": 0,
                "context_length": 65536,
            }
        context_length = usage.get("context_length", 65536)
        total_tokens = usage.get("total_tokens", 0)
        percent = round((total_tokens / context_length) * 100) if context_length > 0 else None
        return {
            "status": "ok",
            "usage": usage,
            "context_percent": percent,
            "context_length": context_length,
        }

    async def write_memory(self, request: dict):
        try:
            memory_id = request.get("memory_id", str(uuid.uuid4()))
            namespace = request.get("namespace", "default")
            content = request.get("content")
            
            if not content:
                raise HTTPException(status_code=400, detail="Content is required")
            
            entry = MemoryEntry(
                memory_id=memory_id,
                namespace=namespace,
                content=content,
                summary=request.get("summary", ""),
                relevance_score=request.get("relevance_score", 1.0),
                created_at=datetime.now(),
                updated_at=datetime.now(),
                accessed_at=datetime.now(),
                decay_factor=request.get("decay_factor", 0.01),
                tags=request.get("tags", []),
                metadata=request.get("metadata", {})
            )
            
            self._save_to_db(entry)
            
            if namespace not in self._namespace_cache:
                self._namespace_cache[namespace] = []
            self._namespace_cache[namespace].append(entry)
            
            logger.info(f"Memory written: {memory_id} in namespace {namespace}")
            return {"memory_id": memory_id, "status": "created"}
        
        except Exception as e:
            logger.error(f"Error writing memory: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    def _save_to_db(self, entry: MemoryEntry):
        conn = sqlite3.connect(str(self._db_path))
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO memories (
                memory_id, namespace, content, summary, relevance_score,
                created_at, updated_at, accessed_at, decay_factor, tags, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            entry.memory_id,
            entry.namespace,
            entry.content,
            entry.summary,
            entry.relevance_score,
            entry.created_at.isoformat(),
            entry.updated_at.isoformat() if entry.updated_at else None,
            entry.accessed_at.isoformat() if entry.accessed_at else None,
            entry.decay_factor,
            json.dumps(entry.tags),
            json.dumps(entry.metadata)
        ))
        
        conn.commit()
        conn.close()

    async def read_memory(self, memory_id: str):
        try:
            conn = sqlite3.connect(str(self._db_path))
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM memories WHERE memory_id = ?', (memory_id,))
            row = cursor.fetchone()
            
            if row:
                entry = self._row_to_entry(row)
                entry.accessed_at = datetime.now()
                self._save_to_db(entry)
                conn.close()
                return entry.dict()
            
            conn.close()
            raise HTTPException(status_code=404, detail="Memory not found")
        
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error reading memory: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    def _row_to_entry(self, row) -> MemoryEntry:
        return MemoryEntry(
            memory_id=row[0],
            namespace=row[1],
            content=row[2],
            summary=row[3] or "",
            relevance_score=row[4],
            created_at=datetime.fromisoformat(row[5]),
            updated_at=datetime.fromisoformat(row[6]) if row[6] else None,
            accessed_at=datetime.fromisoformat(row[7]) if row[7] else None,
            decay_factor=row[8],
            tags=json.loads(row[9]) if row[9] else [],
            metadata=json.loads(row[10]) if row[10] else {}
        )

    async def update_memory(self, memory_id: str, request: dict):
        try:
            conn = sqlite3.connect(str(self._db_path))
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM memories WHERE memory_id = ?', (memory_id,))
            row = cursor.fetchone()
            
            if not row:
                conn.close()
                raise HTTPException(status_code=404, detail="Memory not found")
            
            entry = self._row_to_entry(row)
            
            if "content" in request:
                entry.content = request["content"]
            if "summary" in request:
                entry.summary = request["summary"]
            if "relevance_score" in request:
                entry.relevance_score = request["relevance_score"]
            if "tags" in request:
                entry.tags = request["tags"]
            if "metadata" in request:
                entry.metadata = request["metadata"]
            
            entry.updated_at = datetime.now()
            self._save_to_db(entry)
            conn.close()
            
            logger.info(f"Memory updated: {memory_id}")
            return {"status": "updated"}
        
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating memory: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def delete_memory(self, memory_id: str):
        try:
            conn = sqlite3.connect(str(self._db_path))
            cursor = conn.cursor()
            
            cursor.execute('SELECT namespace FROM memories WHERE memory_id = ?', (memory_id,))
            row = cursor.fetchone()
            
            if not row:
                conn.close()
                raise HTTPException(status_code=404, detail="Memory not found")
            
            namespace = row[0]
            
            cursor.execute('DELETE FROM memories WHERE memory_id = ?', (memory_id,))
            conn.commit()
            conn.close()
            
            if namespace in self._namespace_cache:
                self._namespace_cache[namespace] = [
                    e for e in self._namespace_cache[namespace] if e.memory_id != memory_id
                ]
            
            logger.info(f"Memory deleted: {memory_id}")
            return {"status": "deleted"}
        
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting memory: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def search_memories(self, query: dict):
        try:
            q = query.get("query", "")
            namespace = query.get("namespace")
            limit = query.get("limit", 10)
            min_score = query.get("min_score", 0.0)
            tags = query.get("tags", [])
            
            conn = sqlite3.connect(str(self._db_path))
            cursor = conn.cursor()
            
            sql = "SELECT * FROM memories WHERE relevance_score >= ?"
            params = [min_score]
            
            if namespace:
                sql += " AND namespace = ?"
                params.append(namespace)
            
            if tags:
                sql += " AND (" + " OR ".join(["tags LIKE ?" for _ in tags]) + ")"
                params.extend([f"%{tag}%" for tag in tags])
            
            sql += " ORDER BY relevance_score DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            conn.close()
            
            results = [self._row_to_entry(row).dict() for row in rows]
            
            for result in results:
                result["match_score"] = self._calculate_match_score(result["content"], q)
            
            results.sort(key=lambda x: x["match_score"], reverse=True)
            
            return {"results": results, "count": len(results)}
        
        except Exception as e:
            logger.error(f"Error searching memories: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    def _calculate_match_score(self, content: str, query: str) -> float:
        if not query or not content:
            return 0.0
        
        query_tokens = set(query.lower().split())
        content_tokens = set(content.lower().split())
        
        if not query_tokens:
            return 0.0
        
        intersection = query_tokens & content_tokens
        return len(intersection) / len(query_tokens)

    async def list_by_namespace(self, namespace: str):
        try:
            conn = sqlite3.connect(str(self._db_path))
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM memories WHERE namespace = ? ORDER BY created_at DESC', (namespace,))
            rows = cursor.fetchall()
            conn.close()
            
            results = [self._row_to_entry(row).dict() for row in rows]
            return {"results": results, "count": len(results)}
        
        except Exception as e:
            logger.error(f"Error listing memories by namespace: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def compress_memories(self, request: dict):
        try:
            namespace = request.get("namespace")
            max_entries = request.get("max_entries", 100)
            target_size = request.get("target_size", 10000)
            
            conn = sqlite3.connect(str(self._db_path))
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT * FROM memories WHERE namespace = ? 
                ORDER BY relevance_score DESC LIMIT ?
            ''', (namespace, max_entries))
            
            rows = cursor.fetchall()
            entries = [self._row_to_entry(row) for row in rows]
            
            if len(entries) <= 1:
                conn.close()
                return {"status": "no_compression_needed", "entries_considered": len(entries)}
            
            combined_content = "\n\n---\n\n".join([e.content for e in entries])
            
            if len(combined_content) <= target_size:
                conn.close()
                return {"status": "already_compressed", "size": len(combined_content)}
            
            summary = await self._call_llm_for_summary(combined_content)
            
            compressed_entry = MemoryEntry(
                memory_id=str(uuid.uuid4()),
                namespace=f"{namespace}_compressed",
                content=summary,
                summary=summary[:100] + "..." if len(summary) > 100 else summary,
                relevance_score=sum(e.relevance_score for e in entries) / len(entries),
                created_at=datetime.now(),
                decay_factor=0.005
            )
            
            self._save_to_db(compressed_entry)
            
            for entry in entries[:-5]:
                cursor.execute('DELETE FROM memories WHERE memory_id = ?', (entry.memory_id,))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Memory compression completed for namespace {namespace}")
            return {"status": "compressed", "compressed_entries": len(entries), "new_size": len(summary)}
        
        except Exception as e:
            logger.error(f"Error compressing memories: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def _call_llm_for_summary(self, content: str) -> str:
        """Summarise via MemAI LLM pipeline (API-B, baseline SS4.3, M-04)."""
        try:
            from memai.llm_client import OpenAICompatibleLLMClient
            from memai.model_config import load_voidcube_mem_model_config
            import asyncio as _asyncio

            prompt = (
                "Summarise the following content. Extract key information "
                "and core points. Keep the summary concise, under 500 words."
                + "\n\n" + content + "\n\n----\n\nSummary:"
            )

            def _run_sync() -> str:
                mem_cfg = load_voidcube_mem_model_config()
                api_key = os.environ.get(mem_cfg.api_key_env or "", "")
                if not api_key:
                    raise ValueError(f"Missing API key: {mem_cfg.api_key_env}")
                client = OpenAICompatibleLLMClient(
                    model=mem_cfg.model or "deepseek-chat",
                    api_key=api_key,
                    base_url=mem_cfg.base_url or "https://api.deepseek.com/v1",
                )
                result = client.complete_json(
                    system_prompt="You are a precise content summariser.",
                    user_payload={"text": prompt},
                    task="summarisation",
                )
                if isinstance(result, dict):
                    return str(result.get("content", result.get("summary", "")))
                return str(result) if result else ""

            summary = await _asyncio.to_thread(_run_sync)
            if summary and len(summary) > 10:
                return summary
            return content[:500] + "..."

        except Exception as e:
            logger.warning(f"LLM summarisation failed, using fallback: {e}")
            return content[:500] + "..."

    async def apply_decay(self, request: dict):
        try:
            namespace = request.get("namespace")
            decay_factor = request.get("decay_factor", 0.01)
            
            conn = sqlite3.connect(str(self._db_path))
            cursor = conn.cursor()
            
            if namespace:
                cursor.execute('''
                    UPDATE memories 
                    SET relevance_score = relevance_score * (1 - ?),
                        updated_at = ?
                    WHERE namespace = ?
                ''', (decay_factor, datetime.now().isoformat(), namespace))
            else:
                cursor.execute('''
                    UPDATE memories 
                    SET relevance_score = relevance_score * (1 - ?),
                        updated_at = ?
                ''', (decay_factor, datetime.now().isoformat()))
            
            updated = cursor.rowcount
            conn.commit()
            conn.close()
            
            logger.info(f"Decay applied to {updated} memories")
            return {"status": "decay_applied", "updated_count": updated}
        
        except Exception as e:
            logger.error(f"Error applying decay: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def summarize_memory(self, memory_id: str, request: dict = None):
        try:
            conn = sqlite3.connect(str(self._db_path))
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM memories WHERE memory_id = ?', (memory_id,))
            row = cursor.fetchone()
            
            if not row:
                conn.close()
                raise HTTPException(status_code=404, detail="Memory not found")
            
            entry = self._row_to_entry(row)
            summary = await self._call_llm_for_summary(entry.content)
            
            entry.summary = summary
            entry.updated_at = datetime.now()
            self._save_to_db(entry)
            conn.close()
            
            logger.info(f"Memory summarized: {memory_id}")
            return {"summary": summary, "status": "summarized"}
        
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error summarizing memory: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def register_with_gateway(self):
        import asyncio as _asyncio

        url = f"{self.config.gateway_address}/register"
        payload = {
            "service_name": "memory-service",
            "service_type": "memory",
            "address": f"http://{self.config.host}:{self.config.port}",
            "health_endpoint": "/",
            "metadata": {"version": "1.0"},
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
                            return result["service_id"]
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
        
        logger.info(f"Starting memory service on {self.config.host}:{self.config.port}")
        await uvicorn.Server(
            uvicorn.Config(
                self.app,
                host=self.config.host,
                port=self.config.port,
                log_level="info"
            )
        ).serve()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="VoidCube Memory Service")
    parser.add_argument("--host", default="127.0.0.1", help="Service host")
    parser.add_argument("--port", type=int, default=6001, help="Service port")
    parser.add_argument("--db-path", default="./memory.db", help="SQLite database path")
    parser.add_argument("--gateway", default="http://127.0.0.1:6000", help="Gateway address")
    args = parser.parse_args()
    
    config = MemoryServiceConfig(
        host=args.host,
        port=args.port,
        db_path=args.db_path,
        gateway_address=args.gateway
    )
    service = MemoryService(config)
    
    import asyncio
    asyncio.run(service.start())