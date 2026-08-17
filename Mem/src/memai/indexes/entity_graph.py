"""Lightweight entity graph for graph-based memory retrieval.

The graph is a co-occurrence model over durable (Tier 2) memories:

- ``entity_nodes`` — canonical entity names (normalized), with reference counts.
- ``entity_memory_links`` — which memory records reference an entity.
- ``entity_edges`` — undirected co-occurrence edges (two entities appearing
  together in one memory record), with a strength counter.

Retrieval uses the graph to expand recall beyond lexical/semantic match:
given entity names detected in the query, we surface memories that reference
those entities directly, plus memories referencing co-occurring ("neighbor")
entities — the multi-hop access pattern that plain FTS cannot provide.

This realizes the "optional graph edges materialized" note in the Mem schema
v1 design (Mem/docs/02-schema-v1.md).
"""

from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from memai.domain.scope import GLOBAL_SCOPE_ID


_EVALUATION_SOURCE_EXCLUSION_SQL = (
    "NOT EXISTS (SELECT 1 FROM json_each(CASE WHEN "
    "json_valid(COALESCE(cm.source_turns, '[]')) "
    "THEN cm.source_turns ELSE '[]' END) source "
    "JOIN turns source_turn ON source_turn.turn_id = CAST(source.value AS TEXT) "
    "WHERE json_valid(COALESCE(source_turn.tags, '[]')) AND EXISTS ("
    "SELECT 1 FROM json_each(source_turn.tags) source_tag "
    "WHERE lower(CAST(source_tag.value AS TEXT)) = 'evaluation'))"
)


def normalize_entity(name: object) -> str:
    """Canonical form of an entity name (NFKC + casefold + trim)."""
    return unicodedata.normalize("NFKC", str(name or "")).casefold().strip()


def setup_entity_graph(conn) -> None:
    """Create the entity graph tables (idempotent)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entity_nodes (
            entity_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            memory_domain TEXT NOT NULL DEFAULT 'agent_interaction',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            reference_count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (entity_id, owner_id, workspace_id, memory_domain)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entity_edges (
            source_entity TEXT NOT NULL,
            target_entity TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            memory_domain TEXT NOT NULL DEFAULT 'agent_interaction',
            strength INTEGER NOT NULL DEFAULT 1,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (source_entity, target_entity, owner_id, workspace_id, memory_domain)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entity_memory_links (
            entity_id TEXT NOT NULL,
            memory_id TEXT NOT NULL,
            memory_type TEXT NOT NULL DEFAULT 'event',
            owner_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            memory_domain TEXT NOT NULL DEFAULT 'agent_interaction',
            created_at TEXT NOT NULL,
            PRIMARY KEY (entity_id, memory_id, owner_id, workspace_id, memory_domain)
        )
        """
    )
    for index_sql in (
        "CREATE INDEX IF NOT EXISTS idx_entity_nodes_scope "
        "ON entity_nodes(owner_id, workspace_id, memory_domain)",
        "CREATE INDEX IF NOT EXISTS idx_entity_edges_target "
        "ON entity_edges(target_entity, owner_id, workspace_id, memory_domain)",
        "CREATE INDEX IF NOT EXISTS idx_entity_edges_source "
        "ON entity_edges(source_entity, owner_id, workspace_id, memory_domain)",
        "CREATE INDEX IF NOT EXISTS idx_entity_links_memory "
        "ON entity_memory_links(memory_id, owner_id, workspace_id, memory_domain)",
        "CREATE INDEX IF NOT EXISTS idx_entity_links_entity "
        "ON entity_memory_links(entity_id, owner_id, workspace_id, memory_domain)",
    ):
        conn.execute(index_sql)


def update_entity_graph(
    conn,
    *,
    memory_id: str,
    memory_type: str,
    entities: Iterable[object],
    owner_id: str,
    workspace_id: str,
    memory_domain: str,
    now: str,
) -> None:
    """Record one memory's entities into the graph (nodes, links, edges).

    Idempotent per memory: node reference counts and edge strengths are
    incremented on repeat visits; memory links are insert-or-ignore.
    """
    memory_id = str(memory_id or "").strip()
    if not memory_id:
        return
    display_names: dict[str, str] = {}
    for raw in entities:
        entity_id = normalize_entity(raw)
        if not entity_id or entity_id in display_names:
            continue
        display_names[entity_id] = str(raw).strip()

    existing_entities = {
        str(row[0])
        for row in conn.execute(
            "SELECT entity_id FROM entity_memory_links WHERE memory_id = ? "
            "AND owner_id = ? AND workspace_id = ? AND memory_domain = ?",
            (memory_id, owner_id, workspace_id, memory_domain),
        ).fetchall()
    }
    if existing_entities == set(display_names):
        return
    if existing_entities:
        rebuild_entity_graph(
            conn,
            owner_id=owner_id,
            workspace_id=workspace_id,
            memory_domain=memory_domain,
        )
        return

    entity_ids = list(display_names)
    for entity_id, display_name in display_names.items():
        conn.execute(
            "INSERT INTO entity_nodes "
            "(entity_id, display_name, owner_id, workspace_id, memory_domain, "
            "first_seen_at, last_seen_at, reference_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(entity_id, owner_id, workspace_id, memory_domain) "
            "DO UPDATE SET reference_count = entity_nodes.reference_count + 1, "
            "last_seen_at = excluded.last_seen_at",
            (
                entity_id,
                display_name,
                owner_id,
                workspace_id,
                memory_domain,
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT OR IGNORE INTO entity_memory_links "
            "(entity_id, memory_id, memory_type, owner_id, workspace_id, "
            "memory_domain, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entity_id,
                memory_id,
                str(memory_type or "event"),
                owner_id,
                workspace_id,
                memory_domain,
                now,
            ),
        )

    for i in range(len(entity_ids)):
        for j in range(i + 1, len(entity_ids)):
            left, right = entity_ids[i], entity_ids[j]
            if left == right:
                continue
            if left < right:
                source_entity, target_entity = left, right
            else:
                source_entity, target_entity = right, left
            conn.execute(
                "INSERT INTO entity_edges "
                "(source_entity, target_entity, owner_id, workspace_id, "
                "memory_domain, strength, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?) "
                "ON CONFLICT(source_entity, target_entity, owner_id, workspace_id, memory_domain) "
                "DO UPDATE SET strength = entity_edges.strength + 1, "
                "last_seen_at = excluded.last_seen_at",
                (
                    source_entity,
                    target_entity,
                    owner_id,
                    workspace_id,
                    memory_domain,
                    now,
                ),
            )


def rebuild_entity_graph(
    conn,
    *,
    owner_id: str | None = None,
    workspace_id: str | None = None,
    memory_domain: str | None = None,
) -> int:
    """Drop and rebuild the graph from current active compressed memories.

    Returns the number of memory records linked into the graph.
    """
    if (owner_id is None) != (workspace_id is None):
        raise ValueError("owner_id and workspace_id must be provided together")
    scoped = owner_id is not None and workspace_id is not None
    all_scopes = scoped and owner_id == GLOBAL_SCOPE_ID and workspace_id == GLOBAL_SCOPE_ID
    delete_scope = ""
    delete_params: list[Any] = []
    if scoped and not all_scopes:
        delete_scope = " WHERE owner_id = ? AND workspace_id = ?"
        delete_params = [owner_id, workspace_id]
        if memory_domain:
            delete_scope += " AND memory_domain = ?"
            delete_params.append(memory_domain)
    elif all_scopes and memory_domain:
        # A global rebuild with a domain still targets that domain only.
        delete_scope = " WHERE memory_domain = ?"
        delete_params = [memory_domain]
    for table in ("entity_memory_links", "entity_edges", "entity_nodes"):
        conn.execute(f"DELETE FROM {table}{delete_scope}", delete_params)

    now = datetime.now(timezone.utc).isoformat()
    clauses = [
        "cm.status = 'active'",
        "cm.hidden = 0",
        "COALESCE(cm.identity_layer, '') != 'founding'",
        _EVALUATION_SOURCE_EXCLUSION_SQL,
    ]
    params: list[Any] = []
    if scoped and not all_scopes:
        clauses.append("cm.owner_id = ?")
        clauses.append("cm.workspace_id = ?")
        params.extend([owner_id, workspace_id])
    if memory_domain:
        clauses.append("cm.memory_domain = ?")
        params.append(memory_domain)
    rows = conn.execute(
        "SELECT cm.memory_id, cm.memory_type, cm.entities, cm.owner_id, "
        "cm.workspace_id, cm.memory_domain FROM compressed_memories AS cm WHERE "
        + " AND ".join(clauses)
        + " ORDER BY memory_id",
        params,
    ).fetchall()
    linked = 0
    for memory_id, memory_type, entities_json, row_owner, row_ws, row_domain in rows:
        entities = []
        if entities_json:
            try:
                entities = json.loads(entities_json)
            except (TypeError, ValueError, json.JSONDecodeError):
                entities = []
        update_entity_graph(
            conn,
            memory_id=memory_id,
            memory_type=str(memory_type or "event"),
            entities=entities,
            owner_id=str(row_owner),
            workspace_id=str(row_ws),
            memory_domain=str(row_domain or "agent_interaction"),
            now=now,
        )
        linked += 1
    return linked


def entity_names_matching_query(
    conn,
    terms: Sequence[str],
    *,
    owner_id: str,
    workspace_id: str,
    source_domains: Sequence[str],
    limit: int = 16,
) -> list[str]:
    """Return entity ids whose names overlap the query terms."""
    domains = tuple(dict.fromkeys(str(d) for d in source_domains))
    if not domains:
        return []
    domain_placeholders = ",".join("?" for _ in domains)
    scope_params = [owner_id, workspace_id, GLOBAL_SCOPE_ID, GLOBAL_SCOPE_ID]
    found: list[str] = []
    for term in terms:
        term = str(term or "").strip()
        if not term:
            continue
        rows = conn.execute(
            "SELECT entity_id FROM entity_nodes "
            "WHERE ((owner_id = ? AND workspace_id = ?) OR "
            "(owner_id = ? AND workspace_id = ?)) "
            f"AND memory_domain IN ({domain_placeholders}) "
            "AND (entity_id LIKE ? OR ? LIKE entity_id) LIMIT 5",
            [*scope_params, *domains, f"%{term}%", term],
        ).fetchall()
        found.extend(str(row[0]) for row in rows)
    return list(dict.fromkeys(found))[:max(1, min(int(limit), 64))]


def graph_expand_memory_ids(
    conn,
    entity_ids: Sequence[str],
    *,
    owner_id: str,
    workspace_id: str,
    source_domains: Sequence[str],
    as_of: str | None = None,
    max_depth: int = 1,
    limit: int = 50,
) -> dict[str, float]:
    """Expand to memory ids connected to the given entities.

    Returns ``{memory_id: proximity}`` where direct references score 1.0 and
    one-hop neighbor references score 0.6. Only visible (active, non-hidden),
    in-scope, in-domain memories are returned.
    """
    if not entity_ids:
        return {}
    entity_ids = list(dict.fromkeys(str(e) for e in entity_ids))
    domains = tuple(dict.fromkeys(str(d) for d in source_domains))
    domain_placeholders = ",".join("?" for _ in domains) if domains else "''"
    scope_params = [owner_id, workspace_id, GLOBAL_SCOPE_ID, GLOBAL_SCOPE_ID]
    visible = (
        "status = 'active' AND hidden = 0 AND "
        "COALESCE(identity_layer, '') != 'founding' AND "
        + _EVALUATION_SOURCE_EXCLUSION_SQL
    )
    as_of_clause = ""
    as_of_params: list[Any] = []
    if as_of:
        as_of_clause = "AND COALESCE(created_at, compressed_at) <= ?"
        as_of_params = [as_of]

    proximity: dict[str, float] = {}
    direct_ph = ",".join("?" for _ in entity_ids)
    direct_rows = conn.execute(
        "SELECT l.memory_id FROM entity_memory_links l "
        "JOIN compressed_memories cm ON cm.memory_id = l.memory_id "
        f"WHERE l.entity_id IN ({direct_ph}) "
        f"AND {visible} AND ((cm.owner_id = ? AND cm.workspace_id = ?) OR "
        f"(cm.owner_id = ? AND cm.workspace_id = ?)) "
        f"AND cm.memory_domain IN ({domain_placeholders}) {as_of_clause} LIMIT ?",
        [*entity_ids, *scope_params, *domains, *as_of_params, max(1, int(limit))],
    ).fetchall()
    for (memory_id,) in direct_rows:
        proximity[str(memory_id)] = 1.0

    if max_depth >= 1:
        edge_ph = ",".join("?" for _ in entity_ids)
        neighbor_rows = conn.execute(
            "SELECT source_entity AS neighbor FROM entity_edges "
            f"WHERE target_entity IN ({edge_ph}) "
            "AND ((owner_id = ? AND workspace_id = ?) OR "
            "(owner_id = ? AND workspace_id = ?)) "
            f"AND memory_domain IN ({domain_placeholders}) "
            "UNION "
            "SELECT target_entity FROM entity_edges "
            f"WHERE source_entity IN ({edge_ph}) "
            "AND ((owner_id = ? AND workspace_id = ?) OR "
            "(owner_id = ? AND workspace_id = ?)) "
            f"AND memory_domain IN ({domain_placeholders})",
            [
                *entity_ids,
                *scope_params,
                *domains,
                *entity_ids,
                *scope_params,
                *domains,
            ],
        ).fetchall()
        neighbor_ids = [
            str(row[0]) for row in neighbor_rows if str(row[0]) not in entity_ids
        ]
        if neighbor_ids:
            neighbor_ph = ",".join("?" for _ in neighbor_ids)
            neighbor_rows = conn.execute(
                "SELECT l.memory_id FROM entity_memory_links l "
                "JOIN compressed_memories cm ON cm.memory_id = l.memory_id "
                f"WHERE l.entity_id IN ({neighbor_ph}) "
                f"AND {visible} AND ((cm.owner_id = ? AND cm.workspace_id = ?) OR "
                f"(cm.owner_id = ? AND cm.workspace_id = ?)) "
                f"AND cm.memory_domain IN ({domain_placeholders}) {as_of_clause} LIMIT ?",
                [*neighbor_ids, *scope_params, *domains, *as_of_params, max(1, int(limit))],
            ).fetchall()
            for (memory_id,) in neighbor_rows:
                proximity.setdefault(str(memory_id), 0.6)

    return proximity


def list_graph_entities(
    conn,
    *,
    owner_id: str,
    workspace_id: str,
    source_domains: Sequence[str],
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Top entities by reference count within scope/domain."""
    domains = tuple(dict.fromkeys(str(d) for d in source_domains))
    domain_placeholders = ",".join("?" for _ in domains) if domains else "''"
    rows = conn.execute(
        "SELECT entity_id, display_name, reference_count, last_seen_at "
        "FROM entity_nodes "
        "WHERE ((owner_id = ? AND workspace_id = ?) OR "
        "(owner_id = ? AND workspace_id = ?)) "
        f"AND memory_domain IN ({domain_placeholders}) "
        "ORDER BY reference_count DESC, entity_id LIMIT ?",
        [owner_id, workspace_id, GLOBAL_SCOPE_ID, GLOBAL_SCOPE_ID, *domains, max(1, int(limit))],
    ).fetchall()
    return [
        {
            "entity_id": str(row[0]),
            "display_name": str(row[1]),
            "reference_count": int(row[2] or 0),
            "last_seen_at": str(row[3] or ""),
        }
        for row in rows
    ]


def list_graph_neighbors(
    conn,
    entity_id: str,
    *,
    owner_id: str,
    workspace_id: str,
    source_domains: Sequence[str],
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Neighbors of one entity with edge strength."""
    entity_id = normalize_entity(entity_id)
    if not entity_id:
        return []
    domains = tuple(dict.fromkeys(str(d) for d in source_domains))
    domain_placeholders = ",".join("?" for _ in domains) if domains else "''"
    rows = conn.execute(
        "SELECT source_entity, target_entity, strength FROM entity_edges "
        "WHERE (source_entity = ? OR target_entity = ?) "
        "AND ((owner_id = ? AND workspace_id = ?) OR "
        "(owner_id = ? AND workspace_id = ?)) "
        f"AND memory_domain IN ({domain_placeholders}) "
        "ORDER BY strength DESC LIMIT ?",
        [entity_id, entity_id, owner_id, workspace_id, GLOBAL_SCOPE_ID, GLOBAL_SCOPE_ID, *domains, max(1, int(limit))],
    ).fetchall()
    return [
        {
            "neighbor": str(row[1]) if str(row[0]) == entity_id else str(row[0]),
            "strength": int(row[2] or 0),
        }
        for row in rows
    ]
