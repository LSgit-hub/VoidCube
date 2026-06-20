"""Mem Memory Provider - Time-series long-term memory integration.

This provider integrates the Mem time-series memory system into VoidCube,
providing structured temporal memory with Events, Scenes, Arcs, Epochs,
and Profile Memories stored in SQLite.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)


def _ensure_memai_import_path() -> None:
    mem_src = Path(__file__).resolve().parents[3] / "Mem" / "src"
    if mem_src.exists() and str(mem_src) not in sys.path:
        sys.path.insert(0, str(mem_src))


_ensure_memai_import_path()


class MemMemoryProvider(MemoryProvider):
    """Memory provider for the Mem time-series memory system."""

    @property
    def name(self) -> str:
        return "mem"

    def __init__(self):
        self._initialized = False
        self._db = None
        self._config = {}
        self._sync_lock = threading.Lock()
        self._sync_queue = []
        self._temp_files = []

    def is_available(self) -> bool:
        """Return True if Mem is configured and available."""
        try:
            from memai.repository import MemoryStateRepository
            from memai.pipeline import ChroniclePipeline
            return True
        except ImportError as e:
            logger.debug("Mem module not available: %s", e)
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialize the Mem provider."""
        from VoidCube_core.state import SessionDB
        
        self._session_id = session_id
        self._VoidCube_home = kwargs.get("VoidCube_home", "")
        self._platform = kwargs.get("platform", "cli")
        
        # Load config
        try:
            from VoidCube_cli.config import load_config
            self._config = load_config().get("memory", {}).get("mem", {})
        except Exception:
            self._config = {}
        
        # Get the shared SQLite database
        self._db = SessionDB()
        
        # Initialize Mem engine
        from memai.repository import MemoryStateRepository
        from memai.pipeline import ChroniclePipeline
        
        self._pipeline = ChroniclePipeline()
        self._repository = MemoryStateRepository(
            pipeline=self._pipeline,
            incremental_lookback_days=self._config.get("incremental_lookback_days", 14)
        )
        
        # Load or initialize memory state
        self._memory_state_path = Path(self._VoidCube_home) / "mem_state.json" if self._VoidCube_home else Path("mem_state.json")
        
        if self._memory_state_path.exists():
            try:
                self._memory_state = self._repository.load(str(self._memory_state_path))
            except Exception as e:
                logger.warning("Failed to load existing memory state: %s", e)
                self._memory_state = None
        else:
            from memai.repository import MemoryState
            self._memory_state = MemoryState(version=1, result=self._pipeline.ingest([]))
            try:
                self._repository.save(str(self._memory_state_path), self._memory_state)
                logger.info("Created new empty memory state at %s", self._memory_state_path)
            except Exception as e:
                logger.warning("Failed to save initial memory state: %s", e)
        
        # Ensure Mem tables exist in SQLite
        self._init_mem_tables()
        
        # Set initialized flag BEFORE starting background thread
        self._initialized = True
        
        # Start background sync thread
        self._sync_thread = threading.Thread(target=self._background_sync, daemon=True)
        self._sync_thread.start()
        
        logger.info("Mem memory provider initialized")

    def _init_mem_tables(self):
        """Create Mem-specific tables if they don't exist."""
        if not self._db:
            return
            
        schema_sql = """
            CREATE TABLE IF NOT EXISTS mem_events (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                title TEXT,
                summary TEXT,
                timespan_start REAL NOT NULL,
                timespan_end REAL NOT NULL,
                time_precision TEXT,
                importance REAL,
                confidence REAL,
                status TEXT,
                main_or_side TEXT,
                topics TEXT,
                entities TEXT,
                evidence_refs TEXT,
                parent_ids TEXT,
                child_ids TEXT,
                supersedes TEXT,
                compression_level INTEGER,
                event_kind TEXT,
                novelty REAL,
                impact_scope TEXT,
                source_turns TEXT,
                created_at REAL,
                updated_at REAL,
                last_reviewed_at REAL
            );
            
            CREATE TABLE IF NOT EXISTS mem_scenes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                title TEXT,
                summary TEXT,
                timespan_start REAL NOT NULL,
                timespan_end REAL NOT NULL,
                time_precision TEXT,
                importance REAL,
                confidence REAL,
                status TEXT,
                main_or_side TEXT,
                topics TEXT,
                entities TEXT,
                evidence_refs TEXT,
                parent_ids TEXT,
                child_ids TEXT,
                supersedes TEXT,
                compression_level INTEGER,
                scene_goal TEXT,
                key_events TEXT,
                local_turning_points TEXT,
                open_questions TEXT,
                created_at REAL,
                updated_at REAL,
                last_reviewed_at REAL
            );
            
            CREATE TABLE IF NOT EXISTS mem_arcs (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                title TEXT,
                summary TEXT,
                timespan_start REAL NOT NULL,
                timespan_end REAL NOT NULL,
                time_precision TEXT,
                importance REAL,
                confidence REAL,
                status TEXT,
                main_or_side TEXT,
                topics TEXT,
                entities TEXT,
                evidence_refs TEXT,
                parent_ids TEXT,
                child_ids TEXT,
                supersedes TEXT,
                compression_level INTEGER,
                arc_goal TEXT,
                arc_state TEXT,
                drivers TEXT,
                obstacles TEXT,
                milestones TEXT,
                turning_points TEXT,
                created_at REAL,
                updated_at REAL,
                last_reviewed_at REAL
            );
            
            CREATE TABLE IF NOT EXISTS mem_epochs (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                title TEXT,
                summary TEXT,
                timespan_start REAL NOT NULL,
                timespan_end REAL NOT NULL,
                time_precision TEXT,
                importance REAL,
                confidence REAL,
                status TEXT,
                main_or_side TEXT,
                topics TEXT,
                entities TEXT,
                evidence_refs TEXT,
                parent_ids TEXT,
                child_ids TEXT,
                supersedes TEXT,
                compression_level INTEGER,
                epoch_theme TEXT,
                major_arcs TEXT,
                chapter_shift TEXT,
                long_term_effects TEXT,
                created_at REAL,
                updated_at REAL,
                last_reviewed_at REAL
            );
            
            CREATE TABLE IF NOT EXISTS mem_profile (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                memory_kind TEXT,
                subject TEXT,
                predicate TEXT,
                value TEXT,
                summary TEXT,
                confidence REAL,
                certainty_state TEXT,
                status TEXT,
                valid_from REAL,
                valid_to REAL,
                evidence_refs TEXT,
                source_turns TEXT,
                parent_timeline_refs TEXT,
                supersedes TEXT,
                conflict_refs TEXT,
                created_at REAL,
                updated_at REAL,
                last_reviewed_at REAL
            );
            
            CREATE INDEX IF NOT EXISTS idx_mem_events_time ON mem_events(timespan_start, timespan_end);
            CREATE INDEX IF NOT EXISTS idx_mem_scenes_time ON mem_scenes(timespan_start, timespan_end);
            CREATE INDEX IF NOT EXISTS idx_mem_arcs_time ON mem_arcs(timespan_start, timespan_end);
            CREATE INDEX IF NOT EXISTS idx_mem_epochs_time ON mem_epochs(timespan_start, timespan_end);
            CREATE INDEX IF NOT EXISTS idx_mem_profile_subject ON mem_profile(subject);
        """
        
        try:
            def _do_init(conn):
                conn.executescript(schema_sql)
            self._db._execute_write(_do_init)
        except Exception as e:
            logger.warning("Failed to create Mem tables: %s", e)

    def _background_sync(self):
        """Background thread for async memory sync."""
        while self._initialized:
            try:
                with self._sync_lock:
                    if self._sync_queue:
                        item = self._sync_queue.pop(0)
                        self._process_sync_item(item)
            except Exception as e:
                logger.debug("Background sync error: %s", e)
            time.sleep(1)

    def _process_sync_item(self, item):
        """Process a sync item."""
        user_content = item.get("user_content", "")
        assistant_content = item.get("assistant_content", "")
        session_id = item.get("session_id", "")
        
        try:
            # Create transcript turn
            from memai.schema import TranscriptTurn, utc_now
            
            turns = []
            turn = TranscriptTurn(
                turn_id=f"{session_id}_{int(time.time())}",
                speaker="user",
                text=user_content,
                timestamp=utc_now()
            )
            turns.append(turn)
            
            # Add assistant response as separate turn
            if assistant_content:
                assistant_turn = TranscriptTurn(
                    turn_id=f"{session_id}_{int(time.time())}_asst",
                    speaker="assistant",
                    text=assistant_content,
                    timestamp=utc_now()
                )
                turns.append(assistant_turn)
            
            # Update memory state
            if self._memory_state and self._memory_state_path.exists():
                # Create temp JSON file for update
                temp_path = self._create_transcript_file(turns)
                try:
                    update_result = self._repository.update_with_report(
                        str(self._memory_state_path), temp_path
                    )
                    self._memory_state = update_result.state
                    # Sync to SQLite
                    self._sync_to_sqlite(update_result)
                finally:
                    # Clean up temp file
                    try:
                        os.unlink(temp_path)
                    except (OSError, Exception):
                        pass
            else:
                # Initialize from scratch
                temp_path = self._create_transcript_file(turns)
                try:
                    self._memory_state = self._repository.initialize_from_transcript(
                        str(self._memory_state_path),
                        temp_path
                    )
                finally:
                    try:
                        os.unlink(temp_path)
                    except (OSError, Exception):
                        pass
        except Exception as e:
            logger.warning("Failed to sync turn to Mem: %s", e)

    def _create_transcript_file(self, turns):
        """Create a temp transcript file (works on Windows too)."""
        fd, path = tempfile.mkstemp(suffix='.json')
        fd_closed = False
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                fd_closed = True  # with statement closes fd
                json.dump({"turns": [t.to_dict() for t in turns]}, f)
        except Exception:
            if not fd_closed:
                try:
                    os.close(fd)
                except Exception:
                    pass
            try:
                os.unlink(path)
            except Exception:
                pass
            raise
        return path

    def _sync_to_sqlite(self, update_result):
        """Sync updated memory state to SQLite."""
        if not self._db or not update_result:
            return
        
        result = update_result.state.result
        
        def write_ops(conn):
            # Insert/update events
            for event in result.events:
                conn.execute("""
                    INSERT OR REPLACE INTO mem_events
                    (id, type, title, summary, timespan_start, timespan_end, time_precision,
                     importance, confidence, status, main_or_side, topics, entities,
                     evidence_refs, parent_ids, child_ids, supersedes, compression_level,
                     event_kind, novelty, impact_scope, source_turns, created_at, updated_at, last_reviewed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event.id, event.type, event.title, event.summary,
                    event.timespan_start.timestamp(), event.timespan_end.timestamp(),
                    event.time_precision.value, event.importance, event.confidence,
                    event.status.value, event.main_or_side.value,
                    json.dumps(event.topics), json.dumps(event.entities),
                    json.dumps(event.evidence_refs), json.dumps(event.parent_ids),
                    json.dumps(event.child_ids), json.dumps(event.supersedes),
                    event.compression_level, event.event_kind.value, event.novelty,
                    event.impact_scope.value, json.dumps(event.source_turns),
                    event.created_at.timestamp(), event.updated_at.timestamp(),
                    event.last_reviewed_at.timestamp()
                ))
            
            # Insert/update scenes
            for scene in result.scenes:
                conn.execute("""
                    INSERT OR REPLACE INTO mem_scenes
                    (id, type, title, summary, timespan_start, timespan_end, time_precision,
                     importance, confidence, status, main_or_side, topics, entities,
                     evidence_refs, parent_ids, child_ids, supersedes, compression_level,
                     scene_goal, key_events, local_turning_points, open_questions,
                     created_at, updated_at, last_reviewed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    scene.id, scene.type, scene.title, scene.summary,
                    scene.timespan_start.timestamp(), scene.timespan_end.timestamp(),
                    scene.time_precision.value, scene.importance, scene.confidence,
                    scene.status.value, scene.main_or_side.value,
                    json.dumps(scene.topics), json.dumps(scene.entities),
                    json.dumps(scene.evidence_refs), json.dumps(scene.parent_ids),
                    json.dumps(scene.child_ids), json.dumps(scene.supersedes),
                    scene.compression_level, scene.scene_goal,
                    json.dumps(scene.key_events), json.dumps(scene.local_turning_points),
                    json.dumps(scene.open_questions),
                    scene.created_at.timestamp(), scene.updated_at.timestamp(),
                    scene.last_reviewed_at.timestamp()
                ))
            
            # Insert/update arcs
            for arc in result.arcs:
                conn.execute("""
                    INSERT OR REPLACE INTO mem_arcs
                    (id, type, title, summary, timespan_start, timespan_end, time_precision,
                     importance, confidence, status, main_or_side, topics, entities,
                     evidence_refs, parent_ids, child_ids, supersedes, compression_level,
                     arc_goal, arc_state, drivers, obstacles, milestones, turning_points,
                     created_at, updated_at, last_reviewed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    arc.id, arc.type, arc.title, arc.summary,
                    arc.timespan_start.timestamp(), arc.timespan_end.timestamp(),
                    arc.time_precision.value, arc.importance, arc.confidence,
                    arc.status.value, arc.main_or_side.value,
                    json.dumps(arc.topics), json.dumps(arc.entities),
                    json.dumps(arc.evidence_refs), json.dumps(arc.parent_ids),
                    json.dumps(arc.child_ids), json.dumps(arc.supersedes),
                    arc.compression_level, arc.arc_goal, arc.arc_state.value,
                    json.dumps(arc.drivers), json.dumps(arc.obstacles),
                    json.dumps(arc.milestones), json.dumps(arc.turning_points),
                    arc.created_at.timestamp(), arc.updated_at.timestamp(),
                    arc.last_reviewed_at.timestamp()
                ))
            
            # Insert/update epochs
            for epoch in result.epochs:
                conn.execute("""
                    INSERT OR REPLACE INTO mem_epochs
                    (id, type, title, summary, timespan_start, timespan_end, time_precision,
                     importance, confidence, status, main_or_side, topics, entities,
                     evidence_refs, parent_ids, child_ids, supersedes, compression_level,
                     epoch_theme, major_arcs, chapter_shift, long_term_effects,
                     created_at, updated_at, last_reviewed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    epoch.id, epoch.type, epoch.title, epoch.summary,
                    epoch.timespan_start.timestamp(), epoch.timespan_end.timestamp(),
                    epoch.time_precision.value, epoch.importance, epoch.confidence,
                    epoch.status.value, epoch.main_or_side.value,
                    json.dumps(epoch.topics), json.dumps(epoch.entities),
                    json.dumps(epoch.evidence_refs), json.dumps(epoch.parent_ids),
                    json.dumps(epoch.child_ids), json.dumps(epoch.supersedes),
                    epoch.compression_level, epoch.epoch_theme,
                    json.dumps(epoch.major_arcs), epoch.chapter_shift,
                    json.dumps(epoch.long_term_effects),
                    epoch.created_at.timestamp(), epoch.updated_at.timestamp(),
                    epoch.last_reviewed_at.timestamp()
                ))
            
            # Insert/update profile memories
            for profile in result.profile_memories:
                conn.execute("""
                    INSERT OR REPLACE INTO mem_profile
                    (id, type, memory_kind, subject, predicate, value, summary,
                     confidence, certainty_state, status, valid_from, valid_to,
                     evidence_refs, source_turns, parent_timeline_refs, supersedes,
                     conflict_refs, created_at, updated_at, last_reviewed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    profile.id, profile.type, profile.memory_kind.value,
                    profile.subject, profile.predicate, profile.value, profile.summary,
                    profile.confidence, profile.certainty_state.value,
                    profile.status.value, profile.valid_from.timestamp(),
                    profile.valid_to.timestamp() if profile.valid_to else None,
                    json.dumps(profile.evidence_refs), json.dumps(profile.source_turns),
                    json.dumps(profile.parent_timeline_refs), json.dumps(profile.supersedes),
                    json.dumps(profile.conflict_refs),
                    profile.created_at.timestamp(), profile.updated_at.timestamp(),
                    profile.last_reviewed_at.timestamp()
                ))
        
        # Execute all write operations with proper transaction handling and retries
        self._db._execute_write(write_ops)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return tool schemas for Mem queries."""
        return [
            {
                "name": "mem_query_point",
                "description": (
                    "在特定时间点查询记忆。返回那个时刻活跃的事件、场景和故事弧。"
                    "用这个来回忆某个特定时间发生了什么。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "when": {
                            "type": "string",
                            "format": "date-time",
                            "description": "要查询的时间点（ISO 8601 格式）"
                        },
                        "detail_level": {
                            "type": "string",
                            "enum": ["brief", "standard", "deep"],
                            "default": "standard",
                            "description": "响应的详细程度（brief/简略、standard/标准、deep/深度）"
                        },
                        "max_results": {
                            "type": "integer",
                            "default": 10,
                            "minimum": 1,
                            "description": "返回的最大结果数量"
                        }
                    },
                    "required": ["when"]
                }
            },
            {
                "name": "mem_query_range",
                "description": (
                    "在一个时间范围内查询记忆。返回与指定时间段重叠的场景和故事弧。"
                    "用于理解某个特定时间段发生了什么。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start": {
                            "type": "string",
                            "format": "date-time",
                            "description": "时间范围的开始（ISO 8601 格式）"
                        },
                        "end": {
                            "type": "string",
                            "format": "date-time",
                            "description": "时间范围的结束（ISO 8601 格式）"
                        },
                        "topic": {
                            "type": "string",
                            "description": "可选，按主题过滤"
                        },
                        "entity": {
                            "type": "string",
                            "description": "可选，按实体过滤"
                        },
                        "detail_level": {
                            "type": "string",
                            "enum": ["brief", "standard", "deep"],
                            "default": "standard",
                            "description": "响应的详细程度（brief/简略、standard/标准、deep/深度）"
                        },
                        "max_results": {
                            "type": "integer",
                            "default": 10,
                            "minimum": 1,
                            "description": "返回的最大结果数量"
                        }
                    },
                    "required": ["start", "end"]
                }
            },
            {
                "name": "mem_query_theme",
                "description": (
                    "查询一个主题或话题随时间的演变。返回与指定主题相关的场景和故事弧的时间线。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "theme": {
                            "type": "string",
                            "description": "要追踪的主题或话题"
                        },
                        "detail_level": {
                            "type": "string",
                            "enum": ["brief", "standard", "deep"],
                            "default": "standard",
                            "description": "响应的详细程度（brief/简略、standard/标准、deep/深度）"
                        },
                        "max_results": {
                            "type": "integer",
                            "default": 10,
                            "minimum": 1,
                            "description": "返回的最大结果数量"
                        }
                    },
                    "required": ["theme"]
                }
            },
            {
                "name": "mem_query_arcs",
                "description": (
                    "查询活跃的故事弧。返回当前活跃或最近活跃的故事弧，以及它们的状态和里程碑。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["active", "dormant", "stalled", "emerging", "resolved"],
                            "default": "active",
                            "description": "按故事弧状态过滤（active/活跃、dormant/休眠、stalled/停滞、emerging/新兴、resolved/已解决）"
                        },
                        "max_results": {
                            "type": "integer",
                            "default": 10,
                            "minimum": 1,
                            "description": "返回的最大结果数量"
                        }
                    }
                }
            },
            {
                "name": "mem_query_profile",
                "description": (
                    "查询档案记忆（关于人物、系统或概念的事实）。"
                    "返回以主语-谓语-宾语三元组存储的结构化事实。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "subject": {
                            "type": "string",
                            "description": "要查找的主语"
                        },
                        "memory_kind": {
                            "type": "string",
                            "enum": ["preference", "constraint", "definition", "fact"],
                            "description": "记忆类型过滤（preference/偏好、constraint/约束、definition/定义、fact/事实）"
                        },
                        "max_results": {
                            "type": "integer",
                            "default": 10,
                            "minimum": 1,
                            "description": "返回的最大结果数量"
                        }
                    },
                    "required": ["subject"]
                }
            }
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        """Handle a tool call for Mem queries."""
        if not self._initialized:
            return json.dumps({"success": False, "error": "Mem provider not initialized"})
        
        try:
            if tool_name == "mem_query_point":
                result = self._query_point(
                    when=args.get("when"),
                    detail_level=args.get("detail_level", "standard"),
                    max_results=args.get("max_results", 10)
                )
            elif tool_name == "mem_query_range":
                result = self._query_range(
                    start=args.get("start"),
                    end=args.get("end"),
                    topic=args.get("topic"),
                    entity=args.get("entity"),
                    detail_level=args.get("detail_level", "standard"),
                    max_results=args.get("max_results", 10)
                )
            elif tool_name == "mem_query_theme":
                result = self._query_theme(
                    theme=args.get("theme"),
                    detail_level=args.get("detail_level", "standard"),
                    max_results=args.get("max_results", 10)
                )
            elif tool_name == "mem_query_arcs":
                result = self._query_arcs(
                    status=args.get("status", "active"),
                    max_results=args.get("max_results", 10)
                )
            elif tool_name == "mem_query_profile":
                result = self._query_profile(
                    subject=args.get("subject"),
                    memory_kind=args.get("memory_kind"),
                    max_results=args.get("max_results", 10)
                )
            else:
                result = {"success": False, "error": f"Unknown tool: {tool_name}"}
            
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.error("Mem tool call failed: %s", e)
            return json.dumps({"success": False, "error": str(e)})

    def _query_point(self, when: str, detail_level: str = "standard", max_results: int = 10):
        """Query memory at a specific point in time."""
        if not self._memory_state:
            return {"success": False, "error": "No memory state available"}
        
        from memai.query import MemoryQueryEngine
        from memai.schema import parse_datetime
        
        result = self._memory_state.result
        engine = MemoryQueryEngine(
            events=result.events,
            scenes=result.scenes,
            arcs=result.arcs,
            epochs=result.epochs,
            profile_memories=result.profile_memories
        )
        
        query_time = parse_datetime(when)
        response = engine.point_query(query_time, detail_level=detail_level, max_results=max_results)
        
        return {"success": True, "data": response}

    def _query_range(self, start: str, end: str, topic: str = None, entity: str = None,
                     detail_level: str = "standard", max_results: int = 10):
        """Query memory over a time range."""
        if not self._memory_state:
            return {"success": False, "error": "No memory state available"}
        
        from memai.query import MemoryQueryEngine
        from memai.schema import parse_datetime
        
        result = self._memory_state.result
        engine = MemoryQueryEngine(
            events=result.events,
            scenes=result.scenes,
            arcs=result.arcs,
            epochs=result.epochs,
            profile_memories=result.profile_memories
        )
        
        start_time = parse_datetime(start)
        end_time = parse_datetime(end)
        response = engine.range_query(
            start_time, end_time,
            topic=topic, entity=entity,
            detail_level=detail_level, max_results=max_results
        )
        
        return {"success": True, "data": response}

    def _query_theme(self, theme: str, detail_level: str = "standard", max_results: int = 10):
        """查询主题演变。"""
        if not self._memory_state:
            return {"success": False, "error": "No memory state available"}
        
        from memai.query import MemoryQueryEngine
        
        result = self._memory_state.result
        engine = MemoryQueryEngine(
            events=result.events,
            scenes=result.scenes,
            arcs=result.arcs,
            epochs=result.epochs,
            profile_memories=result.profile_memories
        )
        
        response = engine.theme_evolution(theme, detail_level=detail_level, max_results=max_results)
        
        return {"success": True, "data": response}

    def _query_arcs(self, status: str = "active", max_results: int = 10):
        """查询活跃的故事弧。"""
        if not self._memory_state:
            return {"success": False, "error": "No memory state available"}
        
        from memai.query import MemoryQueryEngine
        from memai.schema import ArcState, Status
        
        result = self._memory_state.result
        engine = MemoryQueryEngine(
            events=result.events,
            scenes=result.scenes,
            arcs=result.arcs,
            epochs=result.epochs,
            profile_memories=result.profile_memories
        )
        
        normalized_status = str(status or "active").strip().lower()

        if normalized_status in {"stalled", "emerging", "resolved"}:
            desired_arc_state = ArcState(normalized_status)
            response = engine.active_arcs(
                statuses=[Status.ACTIVE, Status.DORMANT, Status.CLOSED],
                max_results=max_results,
            )
            response["arcs"] = [
                arc for arc in response.get("arcs", [])
                if arc.get("arc_state") == desired_arc_state.value
            ][: max(1, max_results)]
        else:
            status_map = {
                "active": Status.ACTIVE,
                "dormant": Status.DORMANT,
            }
            response = engine.active_arcs(
                statuses=[status_map.get(normalized_status, Status.ACTIVE)],
                max_results=max_results
            )
        
        return {"success": True, "data": response}

    def _query_profile(self, subject: str, memory_kind: str = None, max_results: int = 10):
        """查询档案记忆。"""
        if not self._memory_state:
            return {"success": False, "error": "No memory state available"}
        
        from memai.query import MemoryQueryEngine
        from memai.schema import MemoryKind
        
        result = self._memory_state.result
        engine = MemoryQueryEngine(
            events=result.events,
            scenes=result.scenes,
            arcs=result.arcs,
            epochs=result.epochs,
            profile_memories=result.profile_memories
        )
        
        kind = MemoryKind(memory_kind) if memory_kind else None
        response = engine.profile_lookup(
            subject=subject,
            memory_kind=kind,
            max_results=max_results
        )
        
        return {"success": True, "data": response}

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Queue a turn for async sync to memory."""
        if not self._initialized:
            return
        
        with self._sync_lock:
            self._sync_queue.append({
                "user_content": user_content,
                "assistant_content": assistant_content,
                "session_id": session_id or self._session_id
            })

    def shutdown(self) -> None:
        """Shut down the provider."""
        self._initialized = False
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Prefetch relevant memory context."""
        if not self._memory_state:
            return ""
        
        try:
            from memai.query import MemoryQueryEngine
            from memai.schema import utc_now
            
            result = self._memory_state.result
            engine = MemoryQueryEngine(
                events=result.events,
                scenes=result.scenes,
                arcs=result.arcs,
                epochs=result.epochs,
                profile_memories=result.profile_memories
            )
            
            # Query for recent active arcs and profile memories
            active_arcs = engine.active_arcs(max_results=3)
            profile_result = engine.profile_lookup(max_results=3)
            
            context = []
            if active_arcs.get("arcs"):
                context.append("Active story arcs:")
                for arc in active_arcs["arcs"][:3]:
                    context.append(f"- {arc.get('title', '')}: {arc.get('summary', '')[:100]}...")
            
            if profile_result.get("items"):
                context.append("\nProfile memories:")
                for item in profile_result["items"][:3]:
                    context.append(f"- {item.get('subject')} {item.get('predicate')} {item.get('value')}")
            
            return "\n".join(context)
        except Exception as e:
            logger.debug("Prefetch failed: %s", e)
            return ""
