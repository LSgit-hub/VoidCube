# Mem Integration Contract

## Ownership

Mem is the owner of the persistent memory system. Its runtime implementation
lives under `Mem/src/memai/`:

```text
domain/       memory contracts, scopes, lifecycle rules, time-summary objects
application/  session close, day aggregation, recall, maintenance use cases
repository/   repository ports, SQLite implementation, backup and caches
migrations/   schema bootstrap and persistent-state migrations
indexes/      lexical, semantic, entity and timeline index capabilities
transport/    optional HTTP adapter
```

`plugins/memory/mem/` is the Agent-facing adapter. It may register Mem, convert
VoidCube configuration into Mem host callbacks, translate Agent tool calls into
the Mem protocol, and durably deliver completed turns. Supervisor companion
and Gateway autonomous-finding adapters use the same `MemoryWriteOutbox`
implementation with separate queue files and authorized memory domains. It does not own Mem
schema, recall, summaries, lifecycle policy, or canonical storage.

`systems/memory/` is retired. A generic host may start the Mem application, but
no Mem-specific domain, repository, index, migration, or transport module may
be reintroduced under `systems/`.

The boundary between Agent current context, Mem Tier 1 recent memory, durable
memory, and identity memory is defined in
[`docs/mem-temporary-memory-contract.md`](mem-temporary-memory-contract.md).

## Dependency direction

```text
Agent -> plugins.memory.mem -> Mem protocol/application
VoidCube host -> MemHostIntegration callbacks
Mem application -> domain + repository ports + indexes
SQLite repository -> migrations/schema
transport -> Mem application handlers
```

Mem runtime code must not import `agent`, `VoidCube_app`, or `systems`. Host
configuration, credential stores, integration retirement policy, and Agent
protocol conversion are supplied by `plugins.memory.mem.host_integration`.

## Persistence

`MemoryRepository` is the application-facing persistence port.
`SQLiteMemoryRepository` is the canonical implementation and owns connection
policy, backup manager and schema initialization. `migrations/schema.py` is the
only owner of active Mem schema creation and reconciliation.

All active Turn, archive, durable memory and time-summary tables remain in one
canonical SQLite database. Moving ownership does not create a second store or
perform a data migration because the runtime database path and schema are
unchanged.

## Index capabilities

Timeline summaries are one index capability. Lexical, semantic, entity,
relation and application Profile indexes may coexist and use independent
candidate-generation strategies. No index is a universal replacement for the
others.

## Retirement gate

The following are retired integration markers:

- Python imports beginning with `systems.memory`
- runtime files below `systems/memory/`
- wheel entries below `systems/memory/`
- Mem imports from `agent`, `VoidCube_app`, or `systems`

Tests and packaging verification must keep these entry counts at zero.
