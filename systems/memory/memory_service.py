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


# ── Tier 1 Models (Short-term conversation store) ──────────────────

class SessionCreate(BaseModel):
    metadata: Dict[str, Any] = {}


class TurnCreate(BaseModel):
    speaker: str  # "user" | "agent" | "system"
    text: str
    metadata: Dict[str, Any] = {}


class SessionResponse(BaseModel):
    session_id: str
    created_at: str
    updated_at: Optional[str] = None
    turn_count: int = 0
    metadata: Dict[str, Any] = {}


class TurnResponse(BaseModel):
    turn_id: str
    session_id: str
    speaker: str
    text: str
    timestamp: str
    relevance_score: float = 1.0
    decay_factor: float = 0.01
    compressed_to_tier2: bool = False
    tags: List[str] = []
    metadata: Dict[str, Any] = {}


class TimelineQuery(BaseModel):
    date: str  # ISO date e.g. "2026-06-24"
    session_id: Optional[str] = None
    speaker: Optional[str] = None
    limit: int = 100


class Tier2CompressRequest(BaseModel):
    retention_days: int = 30
    batch_size: int = 100
    min_relevance: float = 0.1
    dry_run: bool = False


class MemoryServiceConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 6001
    db_path: str = "./memory.db"
    gateway_address: str = "http://127.0.0.1:6000"
    llm_api_key: Optional[str] = None
    llm_base_url: str = "https://api.deepseek.com"
    decay_interval_hours: int = 24
    compression_interval: int = 3600  # seconds between auto-compression runs
    # Tier 1 config
    tier1_retention_days: int = 30
    tier1_max_turns: int = 10000
    tier1_decay_rate: float = 0.99
    tier1_min_relevance: float = 0.1
    tier1_archive_keep_original: bool = True


def _turn_row_to_dict(row) -> Dict[str, Any]:
    """Convert a turns table row to a dict (module-level helper)."""
    return {
        "turn_id": row[0],
        "session_id": row[1],
        "speaker": row[2],
        "text": row[3],
        "timestamp": row[4],
        "relevance_score": row[5],
        "decay_factor": row[6],
        "tags": json.loads(row[7]) if row[7] else [],
        "metadata": json.loads(row[8]) if row[8] else {},
        "compressed_to_tier2": bool(row[9]),
    }


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

        # ── Tier 1 tables (short-term conversation store) ──────────
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                metadata TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS turns (
                turn_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                speaker TEXT NOT NULL,
                text TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                relevance_score REAL DEFAULT 1.0,
                decay_factor REAL DEFAULT 0.01,
                tags TEXT,
                metadata TEXT,
                compressed_to_tier2 INTEGER DEFAULT 0,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS turns_archive (
                turn_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                speaker TEXT NOT NULL,
                text_summary TEXT,
                original_text TEXT,
                timestamp TEXT NOT NULL,
                compressed_at TEXT NOT NULL,
                event_ids TEXT,
                scene_ids TEXT
            )
        ''')

        # Tier 1 indexes
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_turns_timestamp ON turns(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_turns_relevance ON turns(relevance_score)",
            "CREATE INDEX IF NOT EXISTS idx_turns_compressed ON turns(compressed_to_tier2)",
            "CREATE INDEX IF NOT EXISTS idx_archive_timestamp ON turns_archive(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_archive_session ON turns_archive(session_id)",
        ]:
            cursor.execute(idx_sql)

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
        # ── Tier 1 routes ──────────────────────────────────────────
        self.app.add_api_route("/sessions", self.create_session, methods=["POST"])
        self.app.add_api_route("/sessions", self.list_sessions, methods=["GET"])
        self.app.add_api_route("/sessions/{session_id}", self.get_session, methods=["GET"])
        self.app.add_api_route("/sessions/{session_id}/turns", self.add_turn, methods=["POST"])
        self.app.add_api_route("/sessions/{session_id}/turns", self.get_session_turns, methods=["GET"])
        self.app.add_api_route("/turns", self.query_turns, methods=["GET"])
        self.app.add_api_route("/turns/{turn_id}", self.get_turn, methods=["GET"])
        self.app.add_api_route("/turns/timeline", self.timeline_view, methods=["POST"])
        self.app.add_api_route("/tier2/compress", self.tier2_compress, methods=["POST"])
        self.app.add_api_route("/tier1/stats", self.tier1_stats, methods=["GET"])

    @asynccontextmanager
    async def _app_lifespan(self, app: FastAPI):
        """Register with Gateway on startup, run compression loop, cleanup on shutdown."""
        del app
        # Register with Gateway so the supervisor can route memory requests
        svc_id = await self.register_with_gateway()
        if svc_id:
            logger.info("Memory service registered with gateway: %s", svc_id)
        else:
            logger.warning("Memory service failed to register with gateway")
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

        Now also runs Tier 1 decay + Tier 2 bridge (two-tier architecture).
        """
        while True:
            await asyncio.sleep(self.config.compression_interval)
            try:
                for namespace in list(self._namespace_cache.keys()):
                    await self._compress_namespace(namespace)
                # ── Tier 1 decay + Tier 2 bridge ─────────────────
                await self._tier1_decay_cycle()
                await self._tier2_bridge_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Background compression skipped", exc_info=True)

    async def _tier1_decay_cycle(self) -> None:
        """Apply exponential decay to Tier 1 turn relevance scores."""
        conn = sqlite3.connect(str(self._db_path))
        rate = self.config.tier1_decay_rate
        conn.execute(
            "UPDATE turns SET relevance_score = relevance_score * ? "
            "WHERE compressed_to_tier2 = 0",
            (rate,),
        )
        updated = conn.rowcount
        conn.commit()
        conn.close()
        if updated:
            logger.debug("Tier 1 decay applied to %d turns (rate=%.3f)", updated, rate)

    async def _tier2_bridge_cycle(self) -> None:
        """Auto-trigger Tier 1→Tier 2 compression for expired turns."""
        conn = sqlite3.connect(str(self._db_path))
        cutoff = (
            datetime.now() - timedelta(days=self.config.tier1_retention_days)
        ).isoformat()
        # Also check max_turns threshold
        total_active = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE compressed_to_tier2 = 0"
        ).fetchone()[0]
        if total_active < self.config.tier1_max_turns:
            # Only compress by age
            candidate = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE timestamp < ? AND compressed_to_tier2 = 0",
                (cutoff,),
            ).fetchone()[0]
            conn.close()
            if candidate == 0:
                return
        else:
            conn.close()
        # Run compression with small batch
        req = Tier2CompressRequest(
            retention_days=self.config.tier1_retention_days,
            batch_size=50,
            min_relevance=self.config.tier1_min_relevance,
        )
        try:
            result = await self.tier2_compress(req)
            if result.get("status") != "no_candidates":
                logger.info(
                    "Tier 2 bridge cycle: %s — %s turns → %s events",
                    result.get("status"),
                    result.get("turns_processed", 0),
                    result.get("events_generated", 0),
                )
        except Exception:
            logger.debug("Tier 2 bridge cycle skipped", exc_info=True)

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

    # ── Tier 1: Short-term Conversation Store ──────────────────────

    async def create_session(self, request: SessionCreate):
        """Create a new conversation session."""
        session_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        conn = sqlite3.connect(str(self._db_path))
        conn.execute(
            "INSERT INTO sessions (session_id, created_at, updated_at, metadata) VALUES (?, ?, ?, ?)",
            (session_id, now, now, json.dumps(request.metadata)),
        )
        conn.commit()
        conn.close()
        logger.info("Session created: %s", session_id)
        return {"session_id": session_id, "created_at": now, "status": "created"}

    async def list_sessions(self, limit: int = 50, offset: int = 0):
        """List all sessions ordered by creation time desc."""
        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute(
            "SELECT s.session_id, s.created_at, s.updated_at, s.metadata, "
            "COUNT(t.turn_id) as turn_count "
            "FROM sessions s LEFT JOIN turns t ON s.session_id = t.session_id "
            "GROUP BY s.session_id ORDER BY s.created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        conn.close()
        return {
            "sessions": [
                {
                    "session_id": r[0],
                    "created_at": r[1],
                    "updated_at": r[2],
                    "metadata": json.loads(r[3]) if r[3] else {},
                    "turn_count": r[4],
                }
                for r in rows
            ],
            "count": len(rows),
        }

    async def get_session(self, session_id: str):
        """Get a session with its turn count."""
        conn = sqlite3.connect(str(self._db_path))
        row = conn.execute(
            "SELECT session_id, created_at, updated_at, metadata FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Session not found")
        turn_count = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
        conn.close()
        return {
            "session_id": row[0],
            "created_at": row[1],
            "updated_at": row[2],
            "metadata": json.loads(row[3]) if row[3] else {},
            "turn_count": turn_count,
        }

    async def add_turn(self, session_id: str, request: TurnCreate):
        """Add a conversation turn to a session."""
        conn = sqlite3.connect(str(self._db_path))
        # Verify session exists
        ses = conn.execute(
            "SELECT session_id FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not ses:
            conn.close()
            raise HTTPException(status_code=404, detail="Session not found")
        turn_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO turns (turn_id, session_id, speaker, text, timestamp, "
            "relevance_score, decay_factor, tags, metadata, compressed_to_tier2) "
            "VALUES (?, ?, ?, ?, ?, 1.0, 0.01, ?, ?, 0)",
            (
                turn_id,
                session_id,
                request.speaker,
                request.text,
                now,
                json.dumps([]),
                json.dumps(request.metadata),
            ),
        )
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        conn.commit()
        conn.close()
        logger.debug("Turn %s added to session %s", turn_id, session_id)
        return {"turn_id": turn_id, "session_id": session_id, "timestamp": now, "status": "created"}

    async def get_session_turns(self, session_id: str, limit: int = 200, offset: int = 0):
        """Get all turns for a session, ordered by timestamp."""
        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute(
            "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "decay_factor, tags, metadata, compressed_to_tier2 "
            "FROM turns WHERE session_id = ? ORDER BY timestamp ASC LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
        conn.close()
        return {
            "session_id": session_id,
            "turns": [_turn_row_to_dict(r) for r in rows],
            "count": len(rows),
            "total": total,
        }

    async def query_turns(
        self, start: str = None, end: str = None, speaker: str = None,
        session_id: str = None, limit: int = 100, offset: int = 0,
    ):
        """Query turns by time range, speaker, or session."""
        conn = sqlite3.connect(str(self._db_path))
        sql = "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score, " \
              "decay_factor, tags, metadata, compressed_to_tier2 FROM turns WHERE 1=1"
        params: list = []
        if start:
            sql += " AND timestamp >= ?"
            params.append(start)
        if end:
            sql += " AND timestamp <= ?"
            params.append(end)
        if speaker:
            sql += " AND speaker = ?"
            params.append(speaker)
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        sql += " ORDER BY timestamp ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return {
            "turns": [_turn_row_to_dict(r) for r in rows],
            "count": len(rows),
        }

    async def get_turn(self, turn_id: str):
        """Get a single turn by ID."""
        conn = sqlite3.connect(str(self._db_path))
        row = conn.execute(
            "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "decay_factor, tags, metadata, compressed_to_tier2 "
            "FROM turns WHERE turn_id = ?",
            (turn_id,),
        ).fetchone()
        if not row:
            # Check archive
            row = conn.execute(
                "SELECT turn_id, session_id, speaker, text_summary, timestamp, "
                "compressed_at, event_ids, scene_ids, original_text "
                "FROM turns_archive WHERE turn_id = ?",
                (turn_id,),
            ).fetchone()
            if not row:
                conn.close()
                raise HTTPException(status_code=404, detail="Turn not found")
            conn.close()
            return {
                "turn_id": row[0], "session_id": row[1], "speaker": row[2],
                "text": row[8] or row[3], "timestamp": row[4],
                "in_archive": True, "compressed_at": row[5],
                "event_ids": json.loads(row[6]) if row[6] else [],
                "scene_ids": json.loads(row[7]) if row[7] else [],
            }
        conn.close()
        return _turn_row_to_dict(row)

    async def timeline_view(self, request: TimelineQuery):
        """Get timeline view for a specific date with turn summaries."""
        conn = sqlite3.connect(str(self._db_path))
        date_start = f"{request.date}T00:00:00"
        date_end = f"{request.date}T23:59:59"
        sql = "SELECT turn_id, session_id, speaker, text, timestamp FROM turns " \
              "WHERE timestamp >= ? AND timestamp <= ?"
        params: list = [date_start, date_end]
        if request.session_id:
            sql += " AND session_id = ?"
            params.append(request.session_id)
        if request.speaker:
            sql += " AND speaker = ?"
            params.append(request.speaker)
        sql += " ORDER BY timestamp ASC LIMIT ?"
        params.append(request.limit)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return {
            "date": request.date,
            "turns": [
                {
                    "turn_id": r[0], "session_id": r[1], "speaker": r[2],
                    "text_preview": r[3][:200] + ("..." if len(r[3]) > 200 else ""),
                    "timestamp": r[4],
                }
                for r in rows
            ],
            "count": len(rows),
        }

    async def tier2_compress(self, request: Tier2CompressRequest = None):
        """Trigger Tier 1 → Tier 2 compression for turns older than retention window."""
        req = request or Tier2CompressRequest()
        conn = sqlite3.connect(str(self._db_path))
        cutoff = (datetime.now() - timedelta(days=req.retention_days)).isoformat()
        rows = conn.execute(
            "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score "
            "FROM turns WHERE timestamp < ? AND compressed_to_tier2 = 0 "
            "AND relevance_score >= ? "
            "ORDER BY timestamp ASC LIMIT ?",
            (cutoff, req.min_relevance, req.batch_size),
        ).fetchall()
        conn.close()
        if not rows:
            return {"status": "no_candidates", "cutoff": cutoff}
        if req.dry_run:
            return {
                "status": "dry_run",
                "candidate_count": len(rows),
                "cutoff": cutoff,
                "sample_turn_ids": [r[0] for r in rows[:5]],
            }
        # Convert to TranscriptTurn and feed into ChroniclePipeline
        result = await self._bridge_to_tier2(rows)
        return {
            "status": "compressed",
            "turns_processed": len(rows),
            "events_generated": len(result.get("events", [])),
            "scenes_generated": len(result.get("scenes", [])),
            "arcs_generated": len(result.get("arcs", [])),
            "cutoff": cutoff,
        }

    async def _bridge_to_tier2(self, rows) -> Dict[str, Any]:
        """Feed Tier 1 turns into the Mem ChroniclePipeline and archive them."""
        from memai.pipeline import ChroniclePipeline
        from memai.schema import TranscriptTurn
        from datetime import datetime as dt, timezone

        transcript_turns = []
        for r in rows:
            turn_id, session_id, speaker, text, timestamp_str, relevance = r
            parsed_ts = dt.fromisoformat(timestamp_str)
            if parsed_ts.tzinfo is None:
                parsed_ts = parsed_ts.replace(tzinfo=timezone.utc)
            transcript_turns.append(
                TranscriptTurn(
                    turn_id=turn_id,
                    speaker=speaker,
                    text=text,
                    timestamp=parsed_ts,
                )
            )

        pipeline = ChroniclePipeline()
        result = pipeline.ingest(transcript_turns)

        # Archive processed turns with back-references
        turn_to_events: Dict[str, list] = {}
        for event in result.events:
            for src_turn_id in event.source_turns:
                turn_to_events.setdefault(src_turn_id, []).append(event.id)

        turn_to_scenes: Dict[str, list] = {}
        for scene in result.scenes:
            for ev_id in scene.child_ids:
                for turn_id, ev_ids in turn_to_events.items():
                    if ev_id in ev_ids and turn_id not in turn_to_scenes:
                        turn_to_scenes.setdefault(turn_id, []).append(scene.id)

        now = datetime.now().isoformat()
        conn = sqlite3.connect(str(self._db_path))
        for r in rows:
            turn_id, session_id, speaker, text, timestamp_str, relevance = r
            event_ids = turn_to_events.get(turn_id, [])
            scene_ids = turn_to_scenes.get(turn_id, [])
            original_text = text if self.config.tier1_archive_keep_original else None
            text_summary = text[:500] if len(text) > 500 else text
            conn.execute(
                "INSERT OR REPLACE INTO turns_archive "
                "(turn_id, session_id, speaker, text_summary, original_text, "
                "timestamp, compressed_at, event_ids, scene_ids) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    turn_id, session_id, speaker, text_summary, original_text,
                    timestamp_str, now,
                    json.dumps(event_ids), json.dumps(scene_ids),
                ),
            )
            conn.execute(
                "UPDATE turns SET compressed_to_tier2 = 1 WHERE turn_id = ?",
                (turn_id,),
            )
        conn.commit()
        conn.close()

        logger.info(
            "Tier2 bridge: %d turns → %d events, %d scenes, %d arcs",
            len(rows), len(result.events), len(result.scenes), len(result.arcs),
        )
        return {
            "events": [e.to_dict() for e in result.events],
            "scenes": [s.to_dict() for s in result.scenes],
            "arcs": [a.to_dict() for a in result.arcs],
            "epochs": [ep.to_dict() for ep in result.epochs],
            "profile_memories": [p.to_dict() for p in result.profile_memories],
        }

    async def tier1_stats(self):
        """Return Tier 1 storage statistics."""
        conn = sqlite3.connect(str(self._db_path))
        total_turns = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        active_turns = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE compressed_to_tier2 = 0"
        ).fetchone()[0]
        compressed_turns = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE compressed_to_tier2 = 1"
        ).fetchone()[0]
        archived_turns = conn.execute("SELECT COUNT(*) FROM turns_archive").fetchone()[0]
        total_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        oldest = conn.execute(
            "SELECT MIN(timestamp) FROM turns WHERE compressed_to_tier2 = 0"
        ).fetchone()[0]
        conn.close()
        return {
            "tier1": {
                "total_turns": total_turns,
                "active_turns": active_turns,
                "compressed_turns": compressed_turns,
                "archived_turns": archived_turns,
                "total_sessions": total_sessions,
                "oldest_active_turn": oldest,
                "retention_days": self.config.tier1_retention_days,
                "max_turns": self.config.tier1_max_turns,
            }
        }

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