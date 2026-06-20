from __future__ import annotations

import asyncio
import json
import os
import threading
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional
import uuid

from fastapi import Request
from fastapi.responses import HTMLResponse, StreamingResponse

from VoidCube_core.utils import atomic_json_write


UI_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>VoidCube Supervisor Room</title>
  <style>
    :root {
      color-scheme: dark;
      --ink: #223047;
      --paper: #fff8e8;
      --wall: #e8d9bf;
      --floor: #b98662;
      --trim: #496a78;
      --mint: #8ac7a4;
      --coral: #de6f5f;
      --gold: #e5b75d;
      --blue: #5a87b7;
      --shadow: rgba(34, 48, 71, .24);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      overflow: hidden;
      font-family: Inter, "Segoe UI", system-ui, sans-serif;
      background: #263743;
      color: var(--ink);
    }

    .room {
      position: relative;
      min-height: 100vh;
      display: grid;
      grid-template-columns: minmax(220px, 28vw) 1fr minmax(220px, 26vw);
      grid-template-rows: 1fr 30vh;
      background:
        linear-gradient(180deg, rgba(255,255,255,.45), rgba(255,255,255,0) 44%),
        linear-gradient(90deg, rgba(73,106,120,.16) 1px, transparent 1px),
        linear-gradient(var(--wall), #dcc69e);
      background-size: auto, 72px 72px, auto;
    }

    .room::after {
      content: "";
      position: absolute;
      left: 0;
      right: 0;
      bottom: 0;
      height: 34vh;
      background:
        repeating-linear-gradient(90deg, rgba(79, 45, 28, .15) 0 2px, transparent 2px 96px),
        linear-gradient(165deg, var(--floor), #9d684a);
      clip-path: polygon(0 18%, 100% 0, 100% 100%, 0 100%);
      z-index: 0;
    }

    .shelf, .desk, .console, .window, .status, .xizi, .thoughts {
      position: relative;
      z-index: 1;
    }

    .shelf {
      align-self: end;
      justify-self: center;
      width: min(78%, 320px);
      height: 48vh;
      margin-bottom: 22vh;
      border: 10px solid #6c4a35;
      border-radius: 8px;
      background: #8e6549;
      box-shadow: 0 20px 32px var(--shadow);
      display: grid;
      grid-template-rows: repeat(4, 1fr);
      padding: 12px;
      gap: 12px;
    }

    .shelf-row {
      border-bottom: 8px solid #6c4a35;
      display: flex;
      align-items: end;
      gap: 8px;
    }

    .book {
      width: 18px;
      border-radius: 3px 3px 0 0;
      box-shadow: inset -4px 0 rgba(255,255,255,.18);
    }

    .book:nth-child(3n) { height: 62%; background: var(--blue); }
    .book:nth-child(3n+1) { height: 82%; background: var(--coral); }
    .book:nth-child(3n+2) { height: 72%; background: var(--gold); }

    .window {
      align-self: start;
      justify-self: end;
      width: min(78%, 300px);
      height: 180px;
      margin: 9vh 8vw 0 0;
      border: 10px solid #f6efe0;
      border-radius: 8px;
      background:
        radial-gradient(circle at 72% 28%, #fff6b3 0 16px, transparent 17px),
        linear-gradient(#7eb4d9, #b8d7e6 58%, #7db88d 59%);
      box-shadow: 0 16px 30px var(--shadow);
    }

    .window::before, .window::after {
      content: "";
      position: absolute;
      background: #f6efe0;
    }

    .window::before { left: 50%; top: 0; bottom: 0; width: 8px; transform: translateX(-50%); }
    .window::after { left: 0; right: 0; top: 50%; height: 8px; transform: translateY(-50%); }

    .desk {
      grid-column: 2;
      grid-row: 2;
      align-self: center;
      justify-self: center;
      width: min(64vw, 540px);
      height: 118px;
      border-radius: 8px;
      background: linear-gradient(#76503a, #5d3d2d);
      box-shadow: 0 22px 28px var(--shadow);
    }

    .desk::before {
      content: "";
      position: absolute;
      left: 8%;
      right: 8%;
      top: -18px;
      height: 28px;
      border-radius: 8px;
      background: #936746;
    }

    .console {
      grid-column: 2;
      grid-row: 2;
      align-self: start;
      justify-self: end;
      width: 168px;
      height: 106px;
      margin-right: 12vw;
      margin-top: -28px;
      border: 8px solid #344756;
      border-radius: 8px;
      background:
        linear-gradient(90deg, rgba(255,255,255,.14) 1px, transparent 1px),
        linear-gradient(#5d8aa7, #375a72);
      background-size: 18px 18px, auto;
      box-shadow: 0 18px 24px var(--shadow);
    }

    .console::after {
      content: "";
      position: absolute;
      left: 50%;
      bottom: -34px;
      width: 64px;
      height: 26px;
      transform: translateX(-50%);
      border-radius: 4px;
      background: #344756;
    }

    .xizi {
      grid-column: 2;
      grid-row: 1 / 3;
      align-self: end;
      justify-self: center;
      width: 190px;
      height: 292px;
      margin-bottom: 13vh;
      transition: transform .45s ease;
      animation: idle-breathe 2.8s ease-in-out infinite;
    }

    .head {
      position: absolute;
      left: 48px;
      top: 18px;
      width: 94px;
      height: 88px;
      border-radius: 46% 46% 44% 44%;
      background: #ffe0bc;
      box-shadow: inset -8px -8px rgba(214, 143, 112, .28);
      z-index: 3;
    }

    .hair {
      position: absolute;
      left: 40px;
      top: 4px;
      width: 110px;
      height: 72px;
      border-radius: 48px 48px 22px 22px;
      background: #2c3540;
      z-index: 4;
      clip-path: polygon(0 0, 100% 0, 94% 78%, 74% 52%, 58% 83%, 39% 54%, 24% 82%, 6% 58%);
    }

    .eye {
      position: absolute;
      top: 51px;
      width: 10px;
      height: 15px;
      border-radius: 50%;
      background: #26313d;
      z-index: 5;
      animation: blink 5s infinite;
    }

    .eye.left { left: 76px; }
    .eye.right { left: 110px; }

    .mouth {
      position: absolute;
      left: 91px;
      top: 82px;
      width: 18px;
      height: 8px;
      border-bottom: 3px solid #935d56;
      border-radius: 50%;
      z-index: 5;
    }

    .body {
      position: absolute;
      left: 58px;
      top: 100px;
      width: 76px;
      height: 104px;
      border-radius: 24px 24px 20px 20px;
      background: linear-gradient(135deg, var(--mint), #4e9a83);
      box-shadow: inset -10px -10px rgba(20, 80, 85, .18);
      z-index: 2;
    }

    .arm {
      position: absolute;
      top: 122px;
      width: 28px;
      height: 82px;
      border-radius: 16px;
      background: #ffe0bc;
      transform-origin: top center;
      z-index: 2;
    }

    .arm.left { left: 40px; transform: rotate(15deg); }
    .arm.right { left: 122px; transform: rotate(-18deg); }

    .leg {
      position: absolute;
      top: 196px;
      width: 32px;
      height: 82px;
      border-radius: 16px;
      background: #384e63;
      z-index: 1;
    }

    .leg.left { left: 64px; }
    .leg.right { left: 102px; }

    .prop {
      position: absolute;
      left: 132px;
      top: 154px;
      width: 52px;
      height: 40px;
      border-radius: 4px;
      background: var(--paper);
      border: 4px solid #72533f;
      transform: rotate(-10deg);
      z-index: 6;
    }

    .thoughts {
      grid-column: 2;
      grid-row: 1;
      align-self: center;
      justify-self: center;
      transform: translate(130px, -36px);
      width: 112px;
      height: 78px;
      opacity: .92;
    }

    .bubble {
      position: absolute;
      border-radius: 50%;
      background: rgba(255, 248, 232, .92);
      border: 3px solid rgba(52, 71, 86, .35);
      box-shadow: 0 8px 16px rgba(34,48,71,.12);
      animation: float 2.4s ease-in-out infinite;
    }

    .bubble.one { width: 82px; height: 52px; left: 24px; top: 0; }
    .bubble.two { width: 18px; height: 18px; left: 12px; top: 50px; animation-delay: .2s; }
    .bubble.three { width: 11px; height: 11px; left: 0; top: 70px; animation-delay: .4s; }

    .glyph {
      position: absolute;
      left: 49px;
      top: 11px;
      font-size: 28px;
      font-weight: 800;
      color: var(--trim);
      animation: glyph-pulse 1.6s ease-in-out infinite;
    }

    .status {
      grid-column: 3;
      grid-row: 2;
      align-self: end;
      justify-self: center;
      width: min(86%, 360px);
      margin-bottom: 4vh;
      padding: 16px;
      border: 3px solid rgba(34, 48, 71, .28);
      border-radius: 8px;
      background: rgba(255, 248, 232, .86);
      box-shadow: 0 16px 28px var(--shadow);
      backdrop-filter: blur(5px);
    }

    .status h1 {
      margin: 0 0 8px;
      font-size: 18px;
      line-height: 1.2;
      letter-spacing: 0;
    }

    .status p {
      margin: 0;
      color: #4e5c6b;
      font-size: 13px;
      line-height: 1.45;
    }

    .queue {
      margin-top: 12px;
      display: grid;
      gap: 8px;
    }

    .timeline {
      margin-top: 12px;
      display: grid;
      gap: 6px;
      max-height: 132px;
      overflow: hidden;
    }

    .task {
      display: grid;
      grid-template-columns: 10px 1fr auto;
      align-items: center;
      gap: 8px;
      min-height: 34px;
      padding: 7px 9px;
      border: 1px solid rgba(34, 48, 71, .16);
      border-radius: 8px;
      background: rgba(255,255,255,.44);
      font-size: 12px;
    }

    .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--blue);
    }

    .task.memory .dot { background: var(--gold); }
    .task.learning .dot { background: var(--mint); }
    .task.evolution .dot { background: var(--coral); }

    .badge {
      min-width: 64px;
      text-align: center;
      padding: 4px 6px;
      border-radius: 999px;
      background: rgba(73, 106, 120, .14);
      color: #3a5260;
      font-size: 11px;
    }

    .event {
      display: grid;
      grid-template-columns: 64px 1fr;
      gap: 8px;
      align-items: start;
      min-height: 28px;
      padding: 6px 8px;
      border-left: 3px solid rgba(73, 106, 120, .42);
      background: rgba(255,255,255,.3);
      border-radius: 6px;
      font-size: 11px;
    }

    .event-time {
      color: #6d7884;
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }

    .event-text {
      color: #2f4050;
      line-height: 1.35;
    }

    body[data-scene="memory"] .xizi { transform: translateX(-21vw); }
    body[data-scene="memory"] .prop { transform: rotate(8deg) scale(1.15); background: #f4d078; }
    body[data-scene="memory"] .arm.right { animation: page-turn 1.2s ease-in-out infinite; }
    body[data-scene="learning"] .xizi { transform: translateX(16vw); }
    body[data-scene="learning"] .prop { background: #bce8d0; animation: card-glow 1.8s ease-in-out infinite; }
    body[data-scene="planning"] .glyph::before { content: ""; }
    body[data-scene="planning"] .glyph { color: var(--coral); }
    body[data-scene="execution"] .xizi { transform: translateX(12vw) translateY(4px); }
    body[data-scene="execution"] .arm.left { animation: type-tap .6s ease-in-out infinite; }
    body[data-scene="idle"] .thoughts { opacity: .72; }

    @keyframes idle-breathe {
      0%, 100% { margin-bottom: 13vh; }
      50% { margin-bottom: calc(13vh + 5px); }
    }

    @keyframes blink {
      0%, 96%, 100% { transform: scaleY(1); }
      97%, 99% { transform: scaleY(.12); }
    }

    @keyframes float {
      0%, 100% { transform: translateY(0); }
      50% { transform: translateY(-6px); }
    }

    @keyframes glyph-pulse {
      0%, 100% { transform: scale(1); opacity: .8; }
      50% { transform: scale(1.18); opacity: 1; }
    }

    @keyframes page-turn {
      0%, 100% { transform: rotate(-18deg); }
      50% { transform: rotate(-50deg); }
    }

    @keyframes type-tap {
      0%, 100% { transform: rotate(15deg); }
      50% { transform: rotate(35deg) translateY(8px); }
    }

    @keyframes card-glow {
      0%, 100% { box-shadow: 0 0 0 rgba(138,199,164,0); }
      50% { box-shadow: 0 0 22px rgba(138,199,164,.72); }
    }

    @media (max-width: 820px) {
      .room {
        grid-template-columns: 1fr;
        grid-template-rows: 25vh 45vh 30vh;
      }

      .shelf {
        grid-column: 1;
        grid-row: 2;
        align-self: end;
        justify-self: start;
        width: 180px;
        height: 260px;
        margin: 0 0 18vh 5vw;
      }

      .window {
        grid-column: 1;
        grid-row: 1;
        width: 180px;
        height: 110px;
        margin: 4vh 6vw 0 0;
      }

      .xizi {
        grid-column: 1;
        grid-row: 2 / 4;
        transform: scale(.86);
        margin-bottom: 17vh;
      }

      body[data-scene="memory"] .xizi { transform: translateX(-16vw) scale(.86); }
      body[data-scene="learning"] .xizi,
      body[data-scene="execution"] .xizi { transform: translateX(16vw) scale(.86); }

      .desk {
        grid-column: 1;
        grid-row: 3;
        width: 78vw;
        height: 92px;
      }

      .console {
        grid-column: 1;
        grid-row: 3;
        width: 128px;
        height: 86px;
        margin-right: 10vw;
      }

      .thoughts {
        grid-column: 1;
        grid-row: 2;
        transform: translate(88px, 0);
      }

      .status {
        grid-column: 1;
        grid-row: 3;
        align-self: end;
        width: 92vw;
        margin-bottom: 2vh;
      }
    }
  </style>
</head>
<body data-scene="idle">
  <main class="room" aria-label="VoidCube supervisor room">
    <section class="shelf" aria-hidden="true">
      <div class="shelf-row"><span class="book"></span><span class="book"></span><span class="book"></span><span class="book"></span><span class="book"></span></div>
      <div class="shelf-row"><span class="book"></span><span class="book"></span><span class="book"></span><span class="book"></span><span class="book"></span></div>
      <div class="shelf-row"><span class="book"></span><span class="book"></span><span class="book"></span><span class="book"></span><span class="book"></span></div>
      <div class="shelf-row"><span class="book"></span><span class="book"></span><span class="book"></span><span class="book"></span><span class="book"></span></div>
    </section>
    <div class="window" aria-hidden="true"></div>
    <div class="thoughts" aria-hidden="true">
      <span class="bubble one"></span><span class="bubble two"></span><span class="bubble three"></span>
      <span class="glyph" id="glyph">?</span>
    </div>
    <section class="xizi" aria-hidden="true">
      <div class="hair"></div><div class="head"></div><div class="eye left"></div><div class="eye right"></div><div class="mouth"></div>
      <div class="body"></div><div class="arm left"></div><div class="arm right"></div><div class="leg left"></div><div class="leg right"></div><div class="prop"></div>
    </section>
    <div class="desk" aria-hidden="true"></div>
    <div class="console" aria-hidden="true"></div>
    <aside class="status" aria-live="polite">
      <h1 id="sceneTitle">Waking supervisor room</h1>
      <p id="sceneSummary">Connecting to VoidCube supervisor.</p>
      <div class="queue" id="queue"></div>
      <div class="timeline" id="timeline"></div>
    </aside>
  </main>
  <script>
    const sceneTitle = document.getElementById("sceneTitle");
    const sceneSummary = document.getElementById("sceneSummary");
    const glyph = document.getElementById("glyph");
    const queue = document.getElementById("queue");
    const timeline = document.getElementById("timeline");

    const glyphs = {
      idle: "?",
      planning: "!",
      memory: "M",
      learning: "L",
      execution: ">"
    };

    function taskClass(task) {
      const family = String(task.task_family || task.governance_task_type || "");
      if (family.includes("memory")) return "memory";
      if (family.includes("learning")) return "learning";
      if (family.includes("evolution") || family.includes("body")) return "evolution";
      return "";
    }

    function renderTasks(tasks) {
      queue.replaceChildren();
      (tasks || []).slice(0, 4).forEach((task) => {
        const row = document.createElement("div");
        row.className = `task ${taskClass(task)}`;
        const dot = document.createElement("span");
        dot.className = "dot";
        const title = document.createElement("span");
        title.textContent = task.title || "Untitled task";
        const badge = document.createElement("span");
        badge.className = "badge";
        badge.textContent = task.status || task.priority || "queued";
        row.append(dot, title, badge);
        queue.append(row);
      });
    }

    function renderTimeline(events) {
      timeline.replaceChildren();
      (events || []).slice(0, 5).forEach((item) => {
        const row = document.createElement("div");
        row.className = "event";
        const time = document.createElement("span");
        time.className = "event-time";
        const date = item.recorded_at ? new Date(item.recorded_at) : null;
        time.textContent = date && !Number.isNaN(date.getTime())
          ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
          : "--:--:--";
        const text = document.createElement("span");
        text.className = "event-text";
        text.textContent = item.summary || item.event_type || "Supervisor activity";
        row.append(time, text);
        timeline.append(row);
      });
    }

    function applyState(state) {
      document.body.dataset.scene = state.scene || "idle";
      glyph.textContent = glyphs[state.scene] || "?";
      sceneTitle.textContent = state.title || "Supervisor room";
      sceneSummary.textContent = state.summary || "";
      renderTasks(state.tasks || []);
      renderTimeline(state.timeline || []);
    }

    async function refresh() {
      try {
        const response = await fetch("/ui/state", { cache: "no-store" });
        applyState(await response.json());
      } catch (error) {
        document.body.dataset.scene = "idle";
        sceneTitle.textContent = "Supervisor room waiting";
        sceneSummary.textContent = "State channel is not available yet.";
        glyph.textContent = "?";
      }
    }

    let fallbackTimer = null;
    function startSnapshotFallback() {
      if (fallbackTimer) return;
      refresh();
      fallbackTimer = setInterval(refresh, 4000);
    }

    if ("EventSource" in window) {
      const events = new EventSource("/ui/events");
      events.addEventListener("state", (event) => {
        if (fallbackTimer) {
          clearInterval(fallbackTimer);
          fallbackTimer = null;
        }
        applyState(JSON.parse(event.data));
      });
      events.onerror = () => {
        startSnapshotFallback();
      };
    } else {
      startSnapshotFallback();
    }
  </script>
</body>
</html>
"""


class SupervisorUIMixin:
    """Small built-in supervisor room UI and state mapper."""

    def _initialize_supervisor_ui_runtime(self) -> None:
        runtime_root = Path(
            getattr(self, "_runtime_root", None)
            or self.config.soul_store_path
            or (Path(self.config.execution.git_repo_path) / ".soul-runtime")
        ).resolve()
        runtime_root.mkdir(parents=True, exist_ok=True)
        self._supervisor_ui_activity_path = runtime_root / "supervisor-ui-activity.json"
        self._supervisor_ui_events: Deque[Dict[str, Any]] = deque(
            self._load_supervisor_ui_activity(),
            maxlen=self.config.ui_activity_buffer_size,
        )

    def _record_supervisor_ui_activity(
        self,
        event_type: str,
        *,
        scene: str = "planning",
        summary: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "scene": scene,
            "summary": summary or event_type.replace("_", " "),
            "metadata": dict(metadata or {}),
            "recorded_at": datetime.utcnow().isoformat(),
        }
        self._supervisor_ui_events.appendleft(event)
        self._persist_supervisor_ui_activity()
        self._record_supervisor_ui_activity_history(event)
        return event

    def _recent_supervisor_ui_activity(self, limit: int = 20) -> List[Dict[str, Any]]:
        events = getattr(self, "_supervisor_ui_events", None)
        if events is None:
            return []
        return list(events)[: max(limit, 0)]

    def _load_supervisor_ui_activity(self) -> List[Dict[str, Any]]:
        path = getattr(self, "_supervisor_ui_activity_path", None)
        if path is None or not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except Exception:
            return []
        events = raw.get("events") if isinstance(raw, dict) else None
        if not isinstance(events, list):
            return []
        normalized = [
            dict(event)
            for event in events
            if isinstance(event, dict)
        ]
        return normalized[: max(int(self.config.ui_activity_buffer_size), 0)]

    def _persist_supervisor_ui_activity(self) -> None:
        path = getattr(self, "_supervisor_ui_activity_path", None)
        events = getattr(self, "_supervisor_ui_events", None)
        if path is None or events is None:
            return
        payload = {
            "version": 1,
            "updated_at": datetime.utcnow().isoformat(),
            "events": list(events),
        }
        try:
            atomic_json_write(path, payload)
        except Exception:
            return

    def _record_supervisor_ui_activity_history(self, event: Dict[str, Any]) -> None:
        governor = getattr(self, "_governor", None)
        if governor is None or not hasattr(governor, "record_supervisor_activity"):
            return
        try:
            governor.record_supervisor_activity(event=event)
        except Exception:
            return

    async def get_supervisor_ui(self) -> HTMLResponse:
        return HTMLResponse(UI_HTML)

    async def get_supervisor_ui_events(self, request: Request) -> StreamingResponse:
        async def event_stream():
            while True:
                if await request.is_disconnected():
                    break
                state = await self.get_supervisor_ui_state()
                yield self._format_supervisor_ui_event("state", state)
                await asyncio.sleep(self.config.ui_event_interval_seconds)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    def _format_supervisor_ui_event(self, event_name: str, payload: Dict[str, Any]) -> str:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return f"event: {event_name}\ndata: {data}\n\n"

    async def get_supervisor_ui_state(self) -> Dict[str, Any]:
        tasks = [
            self._serialize_self_evolution_task(task)
            for task in self._self_evolution_queue.list_tasks()
            if task.status in {"planned", "deferred", "paused", "approved"}
        ]
        tasks.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)

        drive_candidates: List[Dict[str, Any]] = []
        drive_available = True
        try:
            drive = await self.evaluate_endogenous_drive(
                {"max_candidates": 3, "record_activity": False}
            )
            drive_candidates = list(drive.get("candidates") or [])
        except Exception:
            drive_available = False

        scene, title, summary = self._map_supervisor_scene(
            tasks=tasks,
            drive_candidates=drive_candidates,
            drive_available=drive_available,
        )
        return {
            "status": "ok",
            "scene": scene,
            "title": title,
            "summary": summary,
            "generated_at": datetime.utcnow().isoformat(),
            "tasks": tasks[:6],
            "drive_candidates": drive_candidates[:3],
            "drive_available": drive_available,
            "timeline": await self._recent_supervisor_observation_timeline(limit=10),
        }

    async def _recent_supervisor_observation_timeline(self, limit: int = 10) -> List[Dict[str, Any]]:
        try:
            result = await self.get_runtime_timeline(limit=limit)
        except Exception:
            return self._recent_supervisor_ui_activity(limit=limit)
        timeline = result.get("timeline") if isinstance(result, dict) else None
        if not isinstance(timeline, list):
            return []
        return [
            dict(event)
            for event in timeline
            if isinstance(event, dict)
        ]

    def _map_supervisor_scene(
        self,
        *,
        tasks: List[Dict[str, Any]],
        drive_candidates: List[Dict[str, Any]],
        drive_available: bool,
    ) -> tuple[str, str, str]:
        active = tasks[0] if tasks else None
        if active is not None:
            task_family = str(active.get("task_family") or active.get("governance_task_type") or "")
            status = str(active.get("status") or "queued")
            title = str(active.get("title") or "Supervisor task queued")
            if "memory" in task_family:
                return (
                    "memory",
                    "Xizi is sorting memory",
                    f"{title} is {status}; long-term continuity is being guarded.",
                )
            if "learning" in task_family:
                return (
                    "learning",
                    "Xizi is preparing a learning pass",
                    f"{title} is {status}; the agent body will do the evidence work.",
                )
            if "body" in task_family or "evolution" in task_family:
                return (
                    "execution",
                    "Xizi is checking evolution work",
                    f"{title} is {status}; execution still follows governance and rollback rules.",
                )
            return (
                "planning",
                "Xizi is organizing the queue",
                f"{title} is {status}; the supervisor is deciding when it belongs.",
            )

        if drive_candidates:
            first = drive_candidates[0]
            value_tags = ", ".join(first.get("value_tags") or [])
            return (
                "planning",
                "Xizi is thinking",
                f"{first.get('title', 'A candidate task')} emerged from {value_tags or 'core values'}.",
            )

        if not drive_available:
            return (
                "idle",
                "Xizi is waiting by the window",
                "Gateway activity is unavailable, so the room is showing the local supervisor state.",
            )

        return (
            "idle",
            "Xizi is resting",
            "No queued supervisor work needs attention right now.",
        )

    def _maybe_open_supervisor_ui(self) -> None:
        if not self.config.ui_enabled or not self.config.ui_auto_open:
            return
        if os.getenv("PYTEST_CURRENT_TEST"):
            return
        url = f"http://{self.config.host}:{self.config.port}{self.config.ui_path}"
        delay = max(float(self.config.ui_auto_open_delay_seconds), 0.0)

        def open_later() -> None:
            try:
                webbrowser.open(url)
            except Exception:
                return

        timer = threading.Timer(delay, open_later)
        timer.daemon = True
        timer.start()
