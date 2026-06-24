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


def _write_compressed_memories(conn, pipeline_result, now: str) -> int:
    """Write Event/Scene/Arc/Epoch summaries from PipelineResult into SQLite.

    Lifecycle fields:
      - compression_level: 0=Event, 1=Scene, 2=Arc, 3=Epoch
      - status: 'active' (superseded entries are handled by _apply_compression_lifecycle)
      - weight: 1.0 for Events, 0.7 for Scenes, 0.4 for Arcs, 0.2 for Epochs
    """
    written = 0
    # Write Events (level=0, base_weight=1.0)
    for event in pipeline_result.events:
        event_scene_id = event.parent_ids[0] if event.parent_ids else None
        ek = event.event_kind.value if hasattr(event.event_kind, 'value') else str(event.event_kind)
        conn.execute(
            "INSERT OR REPLACE INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, "
            "parent_id, compressed_at, compression_level, status, weight, event_kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.id, "event", event.title, event.summary,
                event.timespan_start.isoformat(), event.timespan_end.isoformat(),
                event.importance, event.confidence,
                json.dumps(event.topics), json.dumps(event.entities),
                json.dumps(event.source_turns), event_scene_id, now,
                0, "active", 1.0, ek,
            ),
        )
        written += 1
    # Write Scenes (level=1, base_weight=0.7)
    # Derive dominant event_kind from child events
    child_event_kinds = [
        e.event_kind.value if hasattr(e.event_kind, 'value') else str(e.event_kind)
        for e in pipeline_result.events
        if e.id in (scene.child_ids if hasattr(scene, 'child_ids') else [])
    ]
    scene_kind = max(set(child_event_kinds), key=child_event_kinds.count) if child_event_kinds else None
    for scene in pipeline_result.scenes:
        scene_arc_id = scene.parent_ids[0] if scene.parent_ids else None
        conn.execute(
            "INSERT OR REPLACE INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, "
            "parent_id, compressed_at, compression_level, status, weight, event_kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scene.id, "scene", scene.title, scene.summary,
                scene.timespan_start.isoformat(), scene.timespan_end.isoformat(),
                scene.importance, scene.confidence,
                json.dumps(scene.topics), json.dumps(scene.entities),
                json.dumps(scene.evidence_refs), scene_arc_id, now,
                1, "active", 0.7, scene_kind,
            ),
        )
        written += 1
    # Write Arcs (level=2, base_weight=0.4, event_kind=NULL)
    for arc in pipeline_result.arcs:
        arc_epoch_id = arc.parent_ids[0] if arc.parent_ids else None
        conn.execute(
            "INSERT OR REPLACE INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, "
            "parent_id, compressed_at, compression_level, status, weight, event_kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                arc.id, "arc", arc.title, arc.summary,
                arc.timespan_start.isoformat(), arc.timespan_end.isoformat(),
                arc.importance, arc.confidence,
                json.dumps(arc.topics), json.dumps(arc.entities),
                json.dumps(arc.evidence_refs), arc_epoch_id, now,
                2, "active", 0.4, None,
            ),
        )
        written += 1
    # Write Epochs (level=3, base_weight=0.2, event_kind=NULL)
    for epoch in pipeline_result.epochs:
        conn.execute(
            "INSERT OR REPLACE INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, "
            "parent_id, compressed_at, compression_level, status, weight, event_kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                epoch.id, "epoch", epoch.title, epoch.summary,
                epoch.timespan_start.isoformat(), epoch.timespan_end.isoformat(),
                epoch.importance, epoch.confidence,
                json.dumps(epoch.topics), json.dumps(epoch.entities),
                json.dumps(epoch.evidence_refs), None, now,
                3, "active", 0.2, None,
            ),
        )
        written += 1
    return written


# ── Content-aware weight model (five dimensions) ─────────────────
# W_final = clamp(W_base + content_bonus + access_bonus + citation_bonus, 0, 1)
# Then: if pinned → W=1.0; if hidden → W=0.0

# Dimension 1: Content importance — derived from EventKind
_CONTENT_IMPORTANCE_BONUS = {
    "decision":   0.15,   # 决定 → 最高
    "correction": 0.12,   # 更正 → 很高
    "shift":      0.12,   # 转向 → 很高
    "completion": 0.08,   # 完成
    "conflict":   0.08,   # 冲突
    "blocker":    0.06,   # 阻塞
    "progress":   0.04,   # 进展
    None:         0.00,   # 无分类 → 不加分
}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two float vectors (pure Python, no numpy)."""
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    norm_a = sum(v * v for v in a[:n]) ** 0.5
    norm_b = sum(v * v for v in b[:n]) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _hash_embedding(text: str, dims: int = 64) -> list[float]:
    """Deterministic pseudo-embedding from text hash (fallback when LLM unavailable).

    Uses character n-gram hashing — fast, deterministic, no LLM dependency.
    NOT semantically meaningful but provides consistent similarity for exact matches.
    """
    import hashlib
    result = [0.0] * dims
    # Character trigram hashing
    for i in range(len(text) - 2):
        trigram = text[i:i + 3]
        h = int(hashlib.md5(trigram.encode()).hexdigest()[:8], 16)
        result[h % dims] += 1.0
    # Normalize
    total = sum(v * v for v in result) ** 0.5
    if total > 0:
        result = [v / total for v in result]
    return result


def compute_dynamic_weight(
    base_weight: float,
    *,
    event_kind: str | None = None,
    access_count: int = 0,
    citation_count: int = 0,
    pinned: bool = False,
    hidden: bool = False,
) -> float:
    """Compute the final query relevance weight from five signal dimensions.

    Formula:
        W = clamp(W_base + content_bonus + access_bonus + citation_bonus, 0.0, 1.0)
        W = 1.0 if pinned
        W = 0.0 if hidden

    Dimension 2 (access_bonus): log-scaled, max +0.10 at 100 accesses
    Dimension 3 (citation_bonus): linear, max +0.10 at 5+ citations
    """
    if hidden:
        return 0.0
    if pinned:
        return 1.0

    content_bonus = _CONTENT_IMPORTANCE_BONUS.get(event_kind, 0.0)

    # log(access_count+1) / log(101) → 0..1, scaled to max 0.10
    import math
    access_bonus = min(math.log(access_count + 1) / math.log(101), 1.0) * 0.10

    # citation_count / 5, capped at 0.10
    citation_bonus = min(citation_count / 5.0, 1.0) * 0.10

    return max(0.0, min(1.0, base_weight + content_bonus + access_bonus + citation_bonus))


def _cmem_row_to_dict(row) -> Dict[str, Any]:
    """Convert a compressed_memories table row to a dict (up to 23 columns)."""
    base = {
        "memory_id": row[0],
        "memory_type": row[1],
        "title": row[2],
        "summary": row[3],
        "timespan_start": row[4],
        "timespan_end": row[5],
        "importance": row[6],
        "confidence": row[7],
        "topics": json.loads(row[8]) if row[8] else [],
        "entities": json.loads(row[9]) if row[9] else [],
        "source_turns": json.loads(row[10]) if row[10] else [],
        "parent_id": row[11],
        "compressed_at": row[12],
        "compression_level": row[13] if len(row) > 13 else 0,
        "status": row[14] if len(row) > 14 else "active",
        "superseded_by": row[15] if len(row) > 15 else None,
        "weight": row[16] if len(row) > 16 else 1.0,
        # Five-dimensional content-aware fields (cols 17-22)
        "event_kind": row[17] if len(row) > 17 else None,
        "access_count": row[18] if len(row) > 18 else 0,
        "last_accessed_at": row[19] if len(row) > 19 else None,
        "citation_count": row[20] if len(row) > 20 else 0,
        "pinned": bool(row[21]) if len(row) > 21 else False,
        "hidden": bool(row[22]) if len(row) > 22 else False,
    }
    # Compute dynamic weight from all signals
    base["dynamic_weight"] = compute_dynamic_weight(
        base_weight=base["weight"],
        event_kind=base["event_kind"],
        access_count=base["access_count"],
        citation_count=base["citation_count"],
        pinned=base["pinned"],
        hidden=base["hidden"],
    )
    return base


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
        # Rule execution tracking
        self._last_rule_run: Dict[str, str] = {}
        self._rule_run_counts: Dict[str, int] = {}
        # LLM status (verified once at startup)
        self._llm_healthy: bool = False
        self._llm_model: str = ""
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

        # Tier 2 compressed memories table (structured Event/Scene/Arc summaries)
        # Lifecycle: Event(level=0) → Scene(level=1) → Arc(level=2) → Epoch(level=3) → purged
        # Five-dimensional weight model (see §3.4.2 in architecture baseline):
        #   W_final = W_base(level) + content_bonus + access_bonus + citation_bonus + pin_override
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS compressed_memories (
                memory_id TEXT PRIMARY KEY,
                memory_type TEXT NOT NULL,       -- "event" | "scene" | "arc" | "epoch"
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                timespan_start TEXT NOT NULL,
                timespan_end TEXT NOT NULL,
                importance REAL DEFAULT 0.5,
                confidence REAL DEFAULT 0.5,
                topics TEXT,                     -- JSON array
                entities TEXT,                   -- JSON array
                source_turns TEXT,               -- JSON array of turn_ids
                parent_id TEXT,                  -- parent memory_id in hierarchy
                compressed_at TEXT NOT NULL,
                compression_level INTEGER DEFAULT 0,  -- 0=Event, 1=Scene, 2=Arc, 3=Epoch, 4=FinalSummary
                status TEXT DEFAULT 'active',         -- 'active' | 'superseded' | 'purged'
                superseded_by TEXT,                   -- memory_id that replaced this entry
                weight REAL DEFAULT 1.0,              -- base structural weight
                -- ── Five-dimensional content-aware weight signals ──
                event_kind TEXT,                      -- decision|progress|blocker|shift|completion|conflict|correction
                access_count INTEGER DEFAULT 0,       -- incremented on each query match
                last_accessed_at TEXT,                -- ISO timestamp of last query hit
                citation_count INTEGER DEFAULT 0,     -- times referenced by parent arcs/scenes
                pinned INTEGER DEFAULT 0,             -- 1 = user pinned (weight locked at 1.0)
                hidden INTEGER DEFAULT 0,             -- 1 = user hidden (excluded from default queries)
                embedding TEXT                        -- JSON float array for semantic similarity
            )
        ''')

        # Migrate existing compressed_memories table (add columns if missing)
        self._migrate_compressed_memories_schema(cursor)

        # Tier 1 + Tier 2 indexes
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_turns_timestamp ON turns(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_turns_relevance ON turns(relevance_score)",
            "CREATE INDEX IF NOT EXISTS idx_turns_compressed ON turns(compressed_to_tier2)",
            "CREATE INDEX IF NOT EXISTS idx_archive_timestamp ON turns_archive(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_archive_session ON turns_archive(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_cmem_type ON compressed_memories(memory_type)",
            "CREATE INDEX IF NOT EXISTS idx_cmem_timespan ON compressed_memories(timespan_start, timespan_end)",
            "CREATE INDEX IF NOT EXISTS idx_cmem_status ON compressed_memories(status)",
            "CREATE INDEX IF NOT EXISTS idx_cmem_level ON compressed_memories(compression_level)",
        ]:
            cursor.execute(idx_sql)

        conn.commit()
        conn.close()
        logger.info(f"Memory database initialized at {self._db_path}")

    @staticmethod
    def _migrate_compressed_memories_schema(cursor) -> None:
        """Add lifecycle + content-aware weight columns to existing table if missing."""
        existing = {row[1] for row in cursor.execute("PRAGMA table_info(compressed_memories)").fetchall()}
        migrations = [
            ("compression_level", "INTEGER DEFAULT 0"),
            ("status", "TEXT DEFAULT 'active'"),
            ("superseded_by", "TEXT"),
            ("weight", "REAL DEFAULT 1.0"),
            # Five-dimensional content-aware weight signals
            ("event_kind", "TEXT"),
            ("access_count", "INTEGER DEFAULT 0"),
            ("last_accessed_at", "TEXT"),
            ("citation_count", "INTEGER DEFAULT 0"),
            ("pinned", "INTEGER DEFAULT 0"),
            ("hidden", "INTEGER DEFAULT 0"),
            ("embedding", "TEXT"),
        ]
        for col_name, col_def in migrations:
            if col_name not in existing:
                cursor.execute(f"ALTER TABLE compressed_memories ADD COLUMN {col_name} {col_def}")

    # ── Compression Lifecycle ─────────────────────────────────────

    # Weight decay by compression level:
    #   Level 0 (Event,  <30d):   weight = 1.00
    #   Level 1 (Scene,  <180d):  weight = 0.70
    #   Level 2 (Arc,    <365d):  weight = 0.40
    #   Level 3 (Epoch,  <730d):  weight = 0.20
    #   Level 4 (Final,  >=730d): weight = 0.05 → purge candidate
    _LEVEL_WEIGHT = {0: 1.0, 1: 0.7, 2: 0.4, 3: 0.2, 4: 0.05}

    _LEVEL_MAX_AGE_DAYS = {
        # (memory_type, compression_level) → max age before escalation
        ("event", 0): 30,     # Event → escalate to Scene after 30d
        ("scene", 1): 180,    # Scene → escalate to Arc after 180d
        ("arc", 2): 365,      # Arc → escalate to Epoch after 365d
        ("epoch", 3): 730,    # Epoch → escalate to Final after 730d
        ("epoch", 4): 9999,   # Final → purge candidate (kept for audit)
    }

    async def _apply_compression_lifecycle(self) -> Dict[str, Any]:
        """Cascade compressed memories through compression levels.

        For each memory, if its age exceeds the threshold for its current level,
        it gets superseded by a higher-level summary. Eventually, level-4
        (FinalSummary) entries are purged.
        """
        now = datetime.now()
        conn = sqlite3.connect(str(self._db_path))
        escalated = 0
        purged = 0

        # ── Escalate: find entries past their level's max age ──
        for (mem_type, level), max_age_days in self._LEVEL_MAX_AGE_DAYS.items():
            cutoff = (now - timedelta(days=max_age_days)).isoformat()
            rows = conn.execute(
                "SELECT memory_id, title, summary, topics, entities, "
                "timespan_start, timespan_end, importance, confidence "
                "FROM compressed_memories "
                "WHERE memory_type = ? AND compression_level = ? "
                "AND status = 'active' AND compressed_at < ?",
                (mem_type, level, cutoff),
            ).fetchall()

            for row in rows:
                mem_id, title, summary, topics_json, entities_json, \
                    ts_start, ts_end, importance, confidence = row

                if level >= 4:
                    # Final level: LLM reviews before permanent deletion
                    should_keep = await self._llm_purge_review(
                        mem_id=mem_id, title=title, summary=summary,
                        topics=json.loads(topics_json) if topics_json else [],
                    )
                    if should_keep:
                        conn.execute(
                            "UPDATE compressed_memories SET compression_level = 3, "
                            "status = 'active', weight = 0.15, compressed_at = ? "
                            "WHERE memory_id = ?",
                            (now.isoformat(), mem_id),
                        )
                    else:
                        conn.execute(
                            "UPDATE compressed_memories SET status = 'purged', "
                            "weight = 0.0 WHERE memory_id = ?",
                            (mem_id,),
                        )
                        purged += 1
                    continue

                # Escalate to next level with LLM re-summarization
                next_level = level + 1
                next_type = {0: "scene", 1: "arc", 2: "epoch", 3: "epoch"}[level]
                next_weight = self._LEVEL_WEIGHT.get(next_level, 0.1)

                # ── LLM generates higher-level abstract ──
                escalated_title, escalated_summary = await self._llm_escalate_summary(
                    mem_id=mem_id,
                    title=title,
                    summary=summary,
                    from_type=mem_type,
                    from_level=level,
                    to_type=next_type,
                    to_level=next_level,
                    topics=json.loads(topics_json) if topics_json else [],
                )

                parent_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO compressed_memories "
                    "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
                    "importance, confidence, topics, entities, source_turns, "
                    "parent_id, compressed_at, compression_level, status, weight) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        parent_id, next_type, escalated_title, escalated_summary,
                        ts_start, ts_end,
                        importance * 0.85, confidence * 0.9,
                        topics_json, entities_json,
                        json.dumps([mem_id]),
                        None, now.isoformat(), next_level, "active", next_weight,
                    ),
                )

                # Mark old entry as superseded
                conn.execute(
                    "UPDATE compressed_memories SET status = 'superseded', "
                    "superseded_by = ?, weight = weight * 0.3 WHERE memory_id = ?",
                    (parent_id, mem_id),
                )
                # Increment citation_count on the new parent (Dimension 3)
                conn.execute(
                    "UPDATE compressed_memories SET citation_count = citation_count + 1 "
                    "WHERE memory_id = ?",
                    (parent_id,),
                )
                escalated += 1

        conn.commit()
        conn.close()
        if escalated or purged:
            logger.info(
                "Compression lifecycle: %d escalated, %d purged", escalated, purged
            )
        return {"escalated": escalated, "purged": purged}

    async def _llm_escalate_summary(
        self, *, mem_id: str, title: str, summary: str,
        from_type: str, from_level: int, to_type: str, to_level: int,
        topics: list,
    ) -> tuple[str, str]:
        """Use LLM to produce a higher-level abstract when escalating memory.

        Without LLM: falls back to mechanical prefix (e.g. "[L2] original title").
        With LLM: generates a genuinely more abstract summary appropriate for
        the target level (Scene→Arc: synthesize scene into arc narrative,
        Arc→Epoch: distill arc into epoch-level historical significance).
        """
        level_names = {0: "事件", 1: "场景", 2: "弧线", 3: "纪元", 4: "终章"}
        from_name = level_names.get(from_level, str(from_level))
        to_name = level_names.get(to_level, str(to_level))

        # Try LLM
        try:
            api_key = (
                os.environ.get("DEEPSEEK_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
                or ""
            ).strip()
            if api_key:
                from memai.llm_client import OpenAICompatibleLLMClient
                model = os.environ.get("MEMAI_LLM_MODEL", "deepseek-chat")
                base_url = os.environ.get("MEMAI_LLM_BASE_URL", "https://api.deepseek.com/v1")
                client = OpenAICompatibleLLMClient(model=model, api_key=api_key, base_url=base_url)

                prompt = (
                    f"将以下{from_name}级别的记忆升级为{to_name}级别的摘要。\n"
                    f"原始标题: {title}\n"
                    f"原始摘要: {summary}\n"
                    f"主题: {', '.join(topics[:5]) if topics else '通用'}\n\n"
                    f"{to_name}级别的摘要应该更抽象、更关注长期意义和结构性变化，"
                    f"而不是具体细节。保留核心事实但提升抽象层次。\n"
                    f"用中文输出JSON: {{\"title\": \"...\", \"summary\": \"...\"}}"
                )
                result = client.complete_json(
                    system_prompt=(
                        "你是长期记忆的编年史学者。你的任务是将低层记忆升级为更高抽象层次。"
                        "保持历史准确性，但提升视角——从具体事件到模式，从模式到意义。"
                    ),
                    user_payload={"task": prompt},
                    task="scholar.revision",
                )
                if isinstance(result, dict):
                    llm_title = str(result.get("title", "")).strip()
                    llm_summary = str(result.get("summary", "")).strip()
                    if llm_title and llm_summary:
                        logger.info(
                            "LLM escalated %s: %s→%s (%s→%s)",
                            mem_id, from_name, to_name, title[:40], llm_title[:40],
                        )
                        return llm_title, llm_summary
        except Exception as exc:
            logger.debug("LLM escalation unavailable for %s: %s", mem_id, exc)

        # Fallback: mechanical
        fallback_title = f"[{to_name}] {title}"
        fallback_summary = (
            f"【从{from_name}升级】{summary}\n"
            f"（自动升级，非LLM重摘要。设置API密钥以启用智能升级。）"
        )
        return fallback_title, fallback_summary

    async def _llm_purge_review(
        self, *, mem_id: str, title: str, summary: str, topics: list,
    ) -> bool:
        """LLM final review before permanent deletion (>730 days old)."""
        try:
            api_key = (
                os.environ.get("DEEPSEEK_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
                or ""
            ).strip()
            if not api_key:
                return False  # No LLM → purge (safe: entries are >2 years old)
            from memai.llm_client import OpenAICompatibleLLMClient
            model = os.environ.get("MEMAI_LLM_MODEL", "deepseek-chat")
            base_url = os.environ.get("MEMAI_LLM_BASE_URL", "https://api.deepseek.com/v1")
            client = OpenAICompatibleLLMClient(model=model, api_key=api_key, base_url=base_url)
            prompt = (
                f"以下是一条即将被永久删除的长期记忆（超过730天）。"
                f"判断是否具有持久历史价值应保留。\n"
                f"标题: {title}\n摘要: {summary}\n主题: {', '.join(topics[:5]) if topics else '无'}\n"
                f"重大决策/架构转折/身份定义 → 保留。过时进度细节 → 删除。"
                f"输出JSON: {{\"keep\": true/false, \"reason\": \"...\"}}"
            )
            result = client.complete_json(
                system_prompt="你是长期记忆的守护者。审慎判断历史记录的去留。",
                user_payload={"task": prompt},
                task="scholar.revision",
            )
            if isinstance(result, dict):
                return bool(result.get("keep", False))
        except Exception:
            pass
        return False

    async def _purge_expired_memories(self) -> int:
        """Hard-delete purged memories older than the audit retention period."""
        conn = sqlite3.connect(str(self._db_path))
        cutoff = (datetime.now() - timedelta(days=90)).isoformat()
        # Only purge entries marked 'purged' for >90 days
        cursor = conn.execute(
            "DELETE FROM compressed_memories WHERE status = 'purged' AND compressed_at < ?",
            (cutoff,),
        )
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        if deleted:
            logger.info("Purged %d expired compressed memories", deleted)
        return deleted

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
        self.app.add_api_route("/compressed/search", self.search_compressed, methods=["POST"])
        self.app.add_api_route("/compressed/{memory_id}", self.get_compressed, methods=["GET"])
        self.app.add_api_route("/compressed/trace/{turn_id}", self.trace_compressed_by_turn, methods=["GET"])
        self.app.add_api_route("/compressed/lifecycle", self.trigger_lifecycle, methods=["POST"])
        self.app.add_api_route("/compressed/{memory_id}/pin", self.pin_memory, methods=["POST"])
        self.app.add_api_route("/compressed/{memory_id}/hide", self.hide_memory, methods=["POST"])
        self.app.add_api_route("/compressed/{memory_id}/unpin", self.unpin_memory, methods=["POST"])
        self.app.add_api_route("/compressed/semantic-search", self.semantic_search, methods=["POST"])
        self.app.add_api_route("/compressed/run-all-rules", self.run_all_rules, methods=["POST"])
        self.app.add_api_route("/compressed/rules-status", self.rules_status, methods=["GET"])
        self.app.add_api_route("/llm/health", self.llm_health, methods=["GET"])

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
        # Startup LLM health check
        await self._check_llm_health()
        if self._llm_healthy:
            logger.info("LLM health check passed: model=%s", self._llm_model)
        else:
            logger.warning("LLM health check FAILED — memory compression will be degraded")
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
                # ── Tier 1 decay + Tier 2 bridge + Lifecycle ─────
                await self._run_all_rules_internal()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Background compression skipped", exc_info=True)

    async def _run_all_rules_internal(self) -> Dict[str, Any]:
        """Execute all five memory rules in correct order (internal, track execution)."""
        now = datetime.now().isoformat()
        results: Dict[str, Any] = {}
        rules = [
            ("tier1_decay", self._tier1_decay_cycle),
            ("tier2_bridge", self._tier2_bridge_cycle),
            ("lifecycle_escalation", self._apply_compression_lifecycle),
            ("purge_expired", self._purge_expired_memories),
        ]
        for rule_name, rule_fn in rules:
            try:
                result = await rule_fn()
                results[rule_name] = result
                self._last_rule_run[rule_name] = now
                self._rule_run_counts[rule_name] = self._rule_run_counts.get(rule_name, 0) + 1
            except Exception as exc:
                results[rule_name] = {"error": str(exc)}
        return results

    async def run_all_rules(self, request: dict = None):
        """Execute all five memory compression rules (public API for supervisor).

        Rules executed in order:
          1. tier1_decay        — Exponential decay of turn relevance_scores
          2. tier2_bridge        — Feed expired turns into ChroniclePipeline → compressed_memories
          3. lifecycle_escalation — Escalate entries through compression levels (Event→Scene→Arc→Epoch→Final)
          4. purge_expired       — Hard-delete purged entries past audit retention
          5. (implicit) dynamic_weight — Recalculated on every search_compressed call
        """
        results = await self._run_all_rules_internal()
        return {"status": "ok", "rules": results, "executed_at": datetime.now().isoformat()}

    async def rules_status(self):
        """Return the last execution time and count for each rule."""
        return {
            "rules": {
                name: {
                    "last_run": self._last_rule_run.get(name),
                    "run_count": self._rule_run_counts.get(name, 0),
                }
                for name in ["tier1_decay", "tier2_bridge", "lifecycle_escalation", "purge_expired"]
            },
            "compression_interval": self.config.compression_interval,
            "tier1_retention_days": self.config.tier1_retention_days,
            "llm_healthy": self._llm_healthy,
            "llm_model": self._llm_model,
        }

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

    async def _check_llm_health(self) -> bool:
        """Verify LLM connectivity once at startup — simple ping, no periodic loop."""
        api_key = (
            os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        ).strip()
        if not api_key:
            self._llm_healthy = False
            self._llm_model = "none"
            return False
        try:
            from memai.llm_client import OpenAICompatibleLLMClient
            import asyncio as _asyncio
            model = os.environ.get("MEMAI_LLM_MODEL", "deepseek-chat")
            base_url = os.environ.get("MEMAI_LLM_BASE_URL", "https://api.deepseek.com/v1")
            def _ping():
                client = OpenAICompatibleLLMClient(model=model, api_key=api_key, base_url=base_url)
                result = client.complete_json(
                    system_prompt="Reply with exactly: {\"ok\": true}",
                    user_payload={"ping": True},
                    task="health_check",
                )
                return isinstance(result, dict) and result.get("ok") is True
            ok = await _asyncio.to_thread(_ping)
            self._llm_healthy = ok
            self._llm_model = model
            return ok
        except Exception:
            self._llm_healthy = False
            return False

    async def llm_health(self):
        """Return LLM status (verified at startup, not continuously monitored)."""
        return {"healthy": self._llm_healthy, "model": self._llm_model}

    def _build_compression_pipeline(self):
        """Build ChroniclePipeline — LLM-first with explicit degraded fallback.

        When LLM is healthy: uses LLMEventExtractionBackend + LLMScholarBackend.
        When LLM is degraded: falls back to heuristic (keyword-based).
        Caller should check self._llm_healthy to decide whether to proceed.
        """
        from memai.pipeline import ChroniclePipeline
        from memai.llm_client import OpenAICompatibleLLMClient
        from memai.extraction import (
            EventExtractor,
            LLMEventExtractionBackend,
        )
        from memai.scholar import LLMScholarBackend

        if not self._llm_healthy:
            logger.warning("LLM unhealthy — using heuristic compression (degraded mode)")
            return ChroniclePipeline()

        api_key = (
            os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        ).strip()
        if not api_key:
            logger.warning("No LLM API key — using heuristic compression")
            return ChroniclePipeline()

        model = os.environ.get("MEMAI_LLM_MODEL", "deepseek-chat")
        base_url = os.environ.get(
            "MEMAI_LLM_BASE_URL", "https://api.deepseek.com/v1"
        )

        try:
            llm_client = OpenAICompatibleLLMClient(
                model=model, api_key=api_key, base_url=base_url
            )

            # LLMEventExtractionBackend needs an LLMExtractionClient protocol;
            # wrap OpenAICompatibleLLMClient into an adapter
            class LLMExtractionAdapter:
                """Adapt OpenAICompatibleLLMClient → LLMExtractionClient protocol."""
                def __init__(self, llm):
                    self._llm = llm

                def extract_events(self, turns):
                    """Call LLM to extract structured events from conversation turns."""
                    turn_texts = [
                        f"[{t.turn_id}] {t.speaker}: {t.text}"
                        for t in turns
                    ]
                    prompt = (
                        "Extract memory-worthy events from the following conversation. "
                        "For each event, output JSON with: title, summary, event_kind "
                        "(decision|progress|blocker|shift|completion|conflict|correction), "
                        "importance (0-1), confidence (0-1), topics (list), entities (list), "
                        "source_turns (list of turn_ids), impact_scope (local|thread|arc|epoch).\n\n"
                        + "\n".join(turn_texts)
                    )
                    result = self._llm.complete_json(
                        system_prompt=(
                            "You are a precise memory extraction assistant. "
                            "Extract only genuinely meaningful events — ignore greetings, "
                            "filler, and trivial remarks.  Output a JSON array of event objects."
                        ),
                        user_payload={"conversation": prompt},
                        task="extractor.events",
                    )
                    if isinstance(result, list):
                        return result
                    if isinstance(result, dict):
                        items = result.get("events") or result.get("result") or []
                        return items if isinstance(items, list) else [result]
                    return []

            extraction_client = LLMExtractionAdapter(llm_client)
            extraction_backend = LLMEventExtractionBackend(client=extraction_client)  # type: ignore[arg-type]
            scholar_backend = LLMScholarBackend(client=llm_client)

            pipeline = ChroniclePipeline(
                event_extractor=EventExtractor(backend=extraction_backend),
                scholar_backend=scholar_backend,
            )
            logger.info(
                "LLM compression pipeline built: model=%s extraction=llm scholar=llm",
                model,
            )
            return pipeline
        except Exception as exc:
            logger.warning(
                "Failed to build LLM compression pipeline: %s; falling back to heuristic", exc
            )
            return ChroniclePipeline()

    async def _bridge_to_tier2(self, rows) -> Dict[str, Any]:
        """Feed Tier 1 turns into the Mem ChroniclePipeline and archive them.

        Uses LLM-backed extraction and scholar backends when an API key is
        available.  Falls back to heuristic (keyword-based) compression when
        no LLM credentials are configured — the system always works, but
        compression quality depends on LLM availability.
        """
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

        if not self._llm_healthy:
            logger.warning(
                "Tier2 bridge: LLM unhealthy — compression degraded to heuristic "
                "(keyword matching, template summaries). Quality will be low."
            )
        pipeline = self._build_compression_pipeline()
        result = pipeline.ingest(transcript_turns)
        # ... rest unchanged

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

        # ── Write compressed memories back to SQLite ─────────────
        # Store Event/Scene/Arc summaries so they are queryable from SQLite
        _write_compressed_memories(conn, result, now)

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
        # Query compressed memories stats
        conn2 = sqlite3.connect(str(self._db_path))
        compressed_total = conn2.execute(
            "SELECT COUNT(*) FROM compressed_memories"
        ).fetchone()[0]
        compressed_events = conn2.execute(
            "SELECT COUNT(*) FROM compressed_memories WHERE memory_type='event'"
        ).fetchone()[0]
        compressed_scenes = conn2.execute(
            "SELECT COUNT(*) FROM compressed_memories WHERE memory_type='scene'"
        ).fetchone()[0]
        compressed_arcs = conn2.execute(
            "SELECT COUNT(*) FROM compressed_memories WHERE memory_type='arc'"
        ).fetchone()[0]
        conn2.close()
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
            },
            "tier2": {
                "total_compressed": compressed_total,
                "events": compressed_events,
                "scenes": compressed_scenes,
                "arcs": compressed_arcs,
            },
        }

    # ── Compressed Memories Query ─────────────────────────────────

    async def search_compressed(self, request: dict):
        """Search compressed memories by type, topic, time range, or text.

        Default: excludes superseded and purged entries, sorts by weight DESC.
        Pass include_superseded=true to see historical versions.
        """
        conn = sqlite3.connect(str(self._db_path))
        memory_type = request.get("memory_type")  # "event"|"scene"|"arc"|"epoch"
        topic = request.get("topic")
        query_text = request.get("query", "")
        start = request.get("timespan_start")
        end = request.get("timespan_end")
        limit = request.get("limit", 20)
        min_weight = request.get("min_weight", 0.0)
        include_superseded = request.get("include_superseded", False)

        sql = "SELECT * FROM compressed_memories WHERE 1=1"
        params: list = []
        # Default: only active entries
        if not include_superseded:
            sql += " AND status = 'active'"
        if memory_type:
            sql += " AND memory_type = ?"
            params.append(memory_type)
        if topic:
            sql += " AND topics LIKE ?"
            params.append(f"%{topic}%")
        if start:
            sql += " AND timespan_end >= ?"
            params.append(start)
        if end:
            sql += " AND timespan_start <= ?"
            params.append(end)
        if query_text:
            sql += " AND (title LIKE ? OR summary LIKE ?)"
            params.extend([f"%{query_text}%", f"%{query_text}%"])
        if min_weight > 0:
            sql += " AND weight >= ?"
            params.append(min_weight)
        sql += " ORDER BY timespan_start DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        # Track access and compute dynamic weights
        now_iso = datetime.now().isoformat()
        results = []
        for r in rows:
            d = _cmem_row_to_dict(r)
            results.append(d)
        # Sort by dynamic_weight DESC after computing
        results.sort(key=lambda x: x.get("dynamic_weight", 0), reverse=True)
        # Update access_count in background (best-effort)
        try:
            for d in results[:limit]:
                conn.execute(
                    "UPDATE compressed_memories SET access_count = access_count + 1, "
                    "last_accessed_at = ? WHERE memory_id = ?",
                    (now_iso, d["memory_id"]),
                )
            conn.commit()
        except Exception:
            pass
        conn.close()
        return {
            "results": results,
            "count": len(results),
        }

    async def get_compressed(self, memory_id: str):
        """Get a single compressed memory by ID."""
        conn = sqlite3.connect(str(self._db_path))
        row = conn.execute(
            "SELECT * FROM compressed_memories WHERE memory_id = ?", (memory_id,)
        ).fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Compressed memory not found")
        return _cmem_row_to_dict(row)

    async def trace_compressed_by_turn(self, turn_id: str):
        """Find all compressed memories that reference a given turn_id."""
        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute(
            "SELECT * FROM compressed_memories WHERE source_turns LIKE ?",
            (f"%{turn_id}%",),
        ).fetchall()
        conn.close()
        return {
            "turn_id": turn_id,
            "compressed_memories": [_cmem_row_to_dict(r) for r in rows],
            "count": len(rows),
        }

    async def trigger_lifecycle(self, request: dict = None):
        """Manually trigger compression lifecycle (escalation + purge)."""
        req = request or {}
        result = {}
        if req.get("escalate", True):
            result["escalation"] = await self._apply_compression_lifecycle()
        if req.get("purge", True):
            result["purge"] = {"deleted": await self._purge_expired_memories()}
        return {"status": "ok", **result}

    # ── User Feedback: Pin / Hide ──────────────────────────────────

    async def pin_memory(self, memory_id: str):
        """Pin a memory: lock weight at 1.0, immune to decay/escalation."""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute(
            "UPDATE compressed_memories SET pinned = 1, hidden = 0, "
            "weight = 1.0 WHERE memory_id = ?",
            (memory_id,),
        )
        if conn.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Memory not found")
        conn.commit()
        conn.close()
        return {"memory_id": memory_id, "pinned": True, "status": "ok"}

    async def hide_memory(self, memory_id: str):
        """Hide a memory: weight = 0.0, excluded from default queries."""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute(
            "UPDATE compressed_memories SET hidden = 1, pinned = 0, "
            "weight = 0.0 WHERE memory_id = ?",
            (memory_id,),
        )
        if conn.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Memory not found")
        conn.commit()
        conn.close()
        return {"memory_id": memory_id, "hidden": True, "status": "ok"}

    async def unpin_memory(self, memory_id: str):
        """Remove pin/hide: restore to normal dynamic weight."""
        conn = sqlite3.connect(str(self._db_path))
        row = conn.execute(
            "SELECT memory_type, compression_level FROM compressed_memories "
            "WHERE memory_id = ?", (memory_id,),
        ).fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="Memory not found")
        mem_type, level = row[0], row[1] or 0
        base_w = self._LEVEL_WEIGHT.get(level, 0.2)
        conn.execute(
            "UPDATE compressed_memories SET pinned = 0, hidden = 0, "
            "weight = ? WHERE memory_id = ?",
            (base_w, memory_id),
        )
        conn.commit()
        conn.close()
        return {"memory_id": memory_id, "pinned": False, "hidden": False, "base_weight": base_w, "status": "ok"}

    # ── Semantic Search (Dimension 5) ─────────────────────────────

    async def semantic_search(self, request: dict):
        """Search compressed memories by semantic similarity using cosine on embeddings.

        If embeddings are not pre-computed, generates them on-the-fly via the Mem LLM.
        Falls back to keyword search if LLM is unavailable.
        """
        query_text = request.get("query", "")
        limit = request.get("limit", 10)
        min_similarity = request.get("min_similarity", 0.3)

        if not query_text:
            return {"results": [], "count": 0, "method": "none"}

        # Generate query embedding
        query_embedding = await self._generate_embedding(query_text)
        if query_embedding is None:
            # Fallback to keyword search
            return await self.search_compressed({"query": query_text, "limit": limit})

        # Fetch active memories with embeddings, compute cosine similarity
        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute(
            "SELECT * FROM compressed_memories WHERE status = 'active' AND hidden = 0 "
            "AND embedding IS NOT NULL LIMIT 500"
        ).fetchall()
        conn.close()

        scored = []
        for r in rows:
            d = _cmem_row_to_dict(r)
            emb_json = r[23] if len(r) > 23 else None  # col index 23 = embedding
            if not emb_json:
                continue
            try:
                emb = json.loads(emb_json)
            except (json.JSONDecodeError, TypeError):
                continue
            sim = _cosine_similarity(query_embedding, emb)
            if sim >= min_similarity:
                d["semantic_similarity"] = round(sim, 4)
                scored.append(d)

        scored.sort(key=lambda x: x.get("semantic_similarity", 0), reverse=True)
        return {
            "results": scored[:limit],
            "count": len(scored[:limit]),
            "method": "semantic",
        }

    async def _generate_embedding(self, text: str) -> list[float] | None:
        """Generate embedding vector via the configured Mem LLM."""
        try:
            from memai.llm_client import OpenAICompatibleLLMClient
            from memai.model_config import load_voidcube_mem_model_config
            import asyncio as _asyncio

            def _run() -> list[float]:
                mem_cfg = load_voidcube_mem_model_config()
                api_key = os.environ.get(mem_cfg.api_key_env or "", "")
                if not api_key:
                    raise ValueError("No API key")
                client = OpenAICompatibleLLMClient(
                    model=mem_cfg.model or "deepseek-chat",
                    api_key=api_key,
                    base_url=mem_cfg.base_url or "https://api.deepseek.com/v1",
                )
                # Use the embeddings endpoint if available, else fallback to completion
                result = client.complete_json(
                    system_prompt="You are an embedding generator. Output JSON: {\"embedding\": [float, ...]}",
                    user_payload={"text": text},
                    task="embedding",
                )
                if isinstance(result, dict) and "embedding" in result:
                    emb = result["embedding"]
                    if isinstance(emb, list) and len(emb) > 0 and isinstance(emb[0], (int, float)):
                        return [float(v) for v in emb[:256]]
                # Fallback: hash-based pseudo-embedding (deterministic but not semantic)
                return _hash_embedding(text)
            return await _asyncio.to_thread(_run)
        except Exception:
            return _hash_embedding(text)

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