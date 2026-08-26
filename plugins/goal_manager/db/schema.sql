PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS goal_projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    root_node_id TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS goal_nodes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    node_type TEXT NOT NULL CHECK (node_type IN
        ('project','objective','milestone','feature','task','bug','test','release')),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN
        ('planned','in_progress','blocked','waiting_review','completed','cancelled')),
    progress REAL NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 1),
    progress_mode TEXT NOT NULL DEFAULT 'manual' CHECK
        (progress_mode IN ('manual','weighted_children','evidence_based')),
    confidence REAL NOT NULL DEFAULT 1 CHECK (confidence >= 0 AND confidence <= 1),
    priority INTEGER NOT NULL DEFAULT 0,
    start_at TEXT,
    due_at TEXT,
    completed_at TEXT,
    acceptance_criteria_json TEXT NOT NULL DEFAULT '[]',
    owner TEXT,
    assigned_to TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    FOREIGN KEY (project_id) REFERENCES goal_projects(id)
);
CREATE INDEX IF NOT EXISTS idx_goal_nodes_project
    ON goal_nodes(project_id, deleted_at);

CREATE TABLE IF NOT EXISTS goal_edges (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    edge_type TEXT NOT NULL CHECK (edge_type IN
        ('decomposes_to','depends_on','blocks')),
    progress_weight REAL NOT NULL DEFAULT 1,
    required INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deleted_at TEXT,
    FOREIGN KEY (project_id) REFERENCES goal_projects(id),
    FOREIGN KEY (source_id) REFERENCES goal_nodes(id),
    FOREIGN KEY (target_id) REFERENCES goal_nodes(id),
    UNIQUE (project_id, source_id, target_id, edge_type)
);
CREATE INDEX IF NOT EXISTS idx_goal_edges_source
    ON goal_edges(source_id, deleted_at);
CREATE INDEX IF NOT EXISTS idx_goal_edges_target
    ON goal_edges(target_id, deleted_at);

CREATE TABLE IF NOT EXISTS goal_events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    batch_id TEXT,
    actor_type TEXT NOT NULL CHECK (actor_type IN
        ('user','agent','supervisor','system')),
    actor_id TEXT,
    session_id TEXT,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    reason TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_goal_events_project_time
    ON goal_events(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_goal_events_batch
    ON goal_events(batch_id);

CREATE TABLE IF NOT EXISTS goal_evidence (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    evidence_type TEXT NOT NULL CHECK (evidence_type IN
        ('test_result','ci_build','git_commit','pr','issue','note','file','manual')),
    title TEXT,
    content TEXT,
    uri TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deleted_at TEXT,
    FOREIGN KEY (node_id) REFERENCES goal_nodes(id)
);
CREATE INDEX IF NOT EXISTS idx_goal_evidence_node
    ON goal_evidence(node_id, deleted_at);
