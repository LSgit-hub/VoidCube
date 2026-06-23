from __future__ import annotations

UI_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>虚空立方监督室</title>
<style>
/*━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  VoidCube Supervisor Room  v3 - Game Style UI
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━*/
:root {
  --bg-dark: #0a0e17;
  --bg-panel: rgba(20, 30, 50, 0.85);
  --bg-card: rgba(30, 45, 70, 0.7);
  --border-glow: rgba(100, 150, 255, 0.3);
  --text-primary: #e8f0ff;
  --text-secondary: #8892a8;
  --text-muted: #5a6478;
  --accent-blue: #64b5f6;
  --accent-green: #66bb6a;
  --accent-yellow: #ffb74d;
  --accent-red: #ef5350;
  --accent-purple: #ab47bc;
  --accent-cyan: #4dd0e1;
  --glow-blue: rgba(100, 181, 246, 0.4);
  --glow-green: rgba(102, 187, 106, 0.4);
  --glow-yellow: rgba(255, 183, 77, 0.4);
  --glow-red: rgba(239, 83, 80, 0.4);
  --glow-purple: rgba(171, 71, 188, 0.4);
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 14px;
}

* { box-sizing:border-box; }

body {
  margin:0; min-height:100vh; overflow:hidden;
  font-family:"Inter","SF Pro Display","Segoe UI",system-ui,sans-serif;
  background:var(--bg-dark);
  color:var(--text-primary);
}

/* ── Room Background ── */
.room-bg {
  position:fixed; inset:0; z-index:0;
  background:
    radial-gradient(ellipse at 50% 0%, rgba(50, 80, 130, 0.15) 0%, transparent 50%),
    radial-gradient(ellipse at 80% 100%, rgba(80, 50, 120, 0.1) 0%, transparent 40%),
    radial-gradient(ellipse at 20% 100%, rgba(30, 80, 100, 0.1) 0%, transparent 40%),
    linear-gradient(180deg, #0a0e17 0%, #0d1320 50%, #0a0e17 100%);
}

/* ── Grid Layout ── */
.room {
  position:relative; z-index:1;
  display:grid;
  grid-template-columns:280px 1fr 320px;
  grid-template-rows:100vh;
  height:100vh;
}

/* ── Sidebar: Left ── */
.sidebar-left {
  grid-column:1;
  display:flex; flex-direction:column;
  padding:16px; gap:12px;
  overflow-y:auto;
}

/* ── Main Area ── */
.main-area {
  grid-column:2;
  display:flex; flex-direction:column;
  padding:16px; gap:12px;
  overflow-y:auto;
}

/* ── Sidebar: Right ── */
.sidebar-right {
  grid-column:3;
  display:flex; flex-direction:column;
  padding:16px; gap:12px;
  overflow-y:auto;
}

/* ── Card Base ── */
.card {
  background:var(--bg-card);
  border:1px solid rgba(100, 150, 255, 0.15);
  border-radius:var(--radius-md);
  padding:14px;
  backdrop-filter:blur(12px);
  transition:all 0.3s ease;
}
.card:hover {
  border-color:var(--border-glow);
  box-shadow:0 0 20px rgba(100, 150, 255, 0.1);
}
.card-header {
  display:flex; align-items:center; justify-content:space-between;
  margin-bottom:10px;
}
.card-title {
  font-size:13px; font-weight:600; color:var(--text-primary);
  text-transform:uppercase; letter-spacing:0.06em;
}
.card-icon { font-size:16px; }

/* ── Toggle Button ── */
.toggle-btn {
  width:24px; height:24px;
  border:none; border-radius:var(--radius-sm);
  background:rgba(100, 150, 255, 0.1);
  color:var(--text-secondary);
  cursor:pointer;
  display:flex; align-items:center; justify-content:center;
  transition:all 0.2s ease;
}
.toggle-btn:hover {
  background:rgba(100, 150, 255, 0.2);
  color:var(--accent-blue);
}

/* ── Character Scene ── */
.character-scene {
  grid-column:2;
  position:relative;
  display:flex; align-items:center; justify-content:center;
  min-height:300px;
  border-radius:var(--radius-lg);
  background:
    radial-gradient(ellipse at 50% 80%, rgba(50, 70, 100, 0.15) 0%, transparent 60%),
    rgba(20, 30, 50, 0.6);
  border:1px solid rgba(100, 150, 255, 0.1);
  overflow:hidden;
}

/* ── Character: 兮子 ── */
.xizi {
  position:relative;
  width:160px; height:240px;
  transition:transform 0.5s cubic-bezier(0.4,0,0.2,1);
  animation:breathe 4s ease-in-out infinite;
}
@keyframes breathe {
  0%,100% { transform:translateY(0); }
  50% { transform:translateY(-8px); }
}

.xz-head {
  position:absolute; left:38px; top:8px;
  width:80px; height:76px;
  border-radius:44% 44% 40% 42%;
  background:linear-gradient(155deg,#ffe4c0,#f0cfa0);
  box-shadow:inset -8px -8px rgba(190,120,90,.2);
  z-index:4;
}
.xz-hair {
  position:absolute; left:30px; top:0;
  width:96px; height:62px;
  border-radius:42px 42px 18px 18px;
  background:#1a2028;
  z-index:5;
  clip-path:polygon(0 0,100% 0,92% 70%,70% 46%,54% 76%,36% 48%,20% 78%,6% 52%);
}
.xz-eye {
  position:absolute; top:44px; width:10px; height:16px;
  border-radius:50%; background:#1e2835; z-index:6;
  animation:blink 6s infinite;
}
.xz-eye.l { left:62px; }
.xz-eye.r { left:90px; }
@keyframes blink {
  0%,94%,100% { transform:scaleY(1); }
  96%,98% { transform:scaleY(0.08); }
}
.xz-brow {
  position:absolute; top:39px; width:14px; height:3px;
  border-radius:2px; background:#4a3a30; z-index:6;
}
.xz-brow.l { left:60px; }
.xz-brow.r { left:90px; }
.xz-mouth {
  position:absolute; left:76px; top:70px;
  width:18px; height:8px;
  border-bottom:3px solid #a06858;
  border-radius:50%; z-index:6;
}
.xz-body {
  position:absolute; left:44px; top:84px;
  width:68px; height:92px;
  border-radius:20px 20px 16px 16px;
  background:linear-gradient(140deg,#4dd0e1,#26a69a);
  box-shadow:inset -8px -8px rgba(20,80,90,0.15);
  z-index:3;
}
.xz-arm {
  position:absolute; top:106px;
  width:24px; height:70px;
  border-radius:14px;
  background:linear-gradient(90deg,#ffe4c0,#f0cfa0);
  transform-origin:top center; z-index:3;
}
.xz-arm.l { left:30px; transform:rotate(12deg); }
.xz-arm.r { left:102px; transform:rotate(-14deg); }
.xz-leg {
  position:absolute; top:170px;
  width:28px; height:72px;
  border-radius:13px;
  background:#253545; z-index:2;
}
.xz-leg.l { left:50px; }
.xz-leg.r { left:86px; }
.xz-prop {
  position:absolute; left:110px; top:138px;
  width:46px; height:34px;
  border-radius:5px;
  background:#e8f5e9;
  border:3px solid #5d4037;
  transform:rotate(-6deg);
  z-index:7;
}

/* Scene-specific animations */
body[data-scene="idle"] .xizi { transform:none; }
body[data-scene="planning"] .xizi { transform:scale(1.05); }
body[data-scene="planning"] .xz-arm.l { animation:arm-think 0.9s ease-in-out infinite; }
body[data-scene="learning"] .xizi { transform:translateX(30px); }
body[data-scene="learning"] .xz-prop { animation:card-flip 1.8s ease-in-out infinite; }
body[data-scene="execution"] .xizi { transform:translateX(-20px); }
body[data-scene="execution"] .xz-arm.l,
body[data-scene="execution"] .xz-arm.r { animation:arm-type 0.4s ease-in-out infinite; }
body[data-scene="execution"] .xz-arm.r { animation-delay:0.2s; }
body[data-scene="memory"] .xizi { transform:translateX(-40px); }
body[data-scene="memory"] .xz-arm.r { animation:arm-reach 1.2s ease-in-out infinite; }
body[data-scene="maintenance"] .xizi { transform:translateX(-40px); }
body[data-scene="maintenance"] .xz-arm.r { animation:arm-reach 1.2s ease-in-out infinite; }
body[data-scene="drive"] .xizi { transform:scale(1.05); }
body[data-scene="drive"] .xz-arm.l { animation:arm-think 0.9s ease-in-out infinite; }
body[data-scene="body_switch"] .xizi { transform:translateX(-20px); }
body[data-scene="body_switch"] .xz-arm.l,
body[data-scene="body_switch"] .xz-arm.r { animation:arm-type 0.4s ease-in-out infinite; }
body[data-scene="body_switch"] .xz-arm.r { animation-delay:0.2s; }

@keyframes arm-think {
  0%,100% { transform:rotate(12deg) translateY(0); }
  50% { transform:rotate(32deg) translateY(-10px); }
}
@keyframes card-flip {
  0%,100% { transform:rotate(-6deg) scale(1); }
  50% { transform:rotate(12deg) scale(1.15); }
}
@keyframes arm-type {
  0%,100% { transform:rotate(12deg) translateY(0); }
  50% { transform:rotate(35deg) translateY(12px); }
}
@keyframes arm-reach {
  0%,100% { transform:rotate(-14deg); }
  50% { transform:rotate(-50deg) translateY(-12px); }
}

/* ── Thought Bubbles ── */
.thoughts {
  position:absolute; right:30px; top:40px;
  transform:translate(30px,-20px);
  width:100px; height:70px;
  z-index:3;
}
.bubble {
  position:absolute; border-radius:50%;
  background:rgba(255,252,245,0.95);
  border:2px solid rgba(100,150,255,0.3);
  box-shadow:0 6px 16px rgba(0,0,0,0.2);
  animation:bob 2.8s ease-in-out infinite;
}
.bubble.b1 { width:68px; height:44px; left:12px; top:0; }
.bubble.b2 { width:18px; height:18px; left:0; top:40px; animation-delay:0.3s; }
.bubble.b3 { width:10px; height:10px; left:-4px; top:56px; animation-delay:0.6s; }
@keyframes bob {
  0%,100% { transform:translateY(0); }
  50% { transform:translateY(-10px); }
}
.glyph {
  position:absolute; left:38px; top:4px;
  font-size:26px; font-weight:800;
  color:#3d5d6b;
  animation:glyph-pulse 2s ease-in-out infinite;
}
@keyframes glyph-pulse {
  0%,100% { transform:scale(1); opacity:0.7; }
  50% { transform:scale(1.3); opacity:1; }
}

/* ── Status Orbs ── */
.status-orbs {
  position:absolute; bottom:16px; left:50%; transform:translateX(-50%);
  display:flex; gap:12px;
}
.orb {
  width:12px; height:12px; border-radius:50%;
  transition:all 0.3s ease;
}
.orb.active {
  box-shadow:0 0 12px currentColor;
  animation:pulse-glow 2s ease-in-out infinite;
}
@keyframes pulse-glow {
  0%,100% { transform:scale(1); opacity:1; }
  50% { transform:scale(1.3); opacity:0.7; }
}
.orb.blue { background:#64b5f6; }
.orb.green { background:#66bb6a; }
.orb.yellow { background:#ffb74d; }
.orb.red { background:#ef5350; }
.orb.purple { background:#ab47bc; }

/* ── Task Panel ── */
.task-panel {
  flex:1;
  display:flex; flex-direction:column;
  min-height:0;
}
.panel-content {
  flex:1;
  overflow-y:auto;
  display:flex; flex-direction:column;
  gap:8px;
}

/* Custom Scrollbar */
::-webkit-scrollbar { width:6px; }
::-webkit-scrollbar-track { background:rgba(100,150,255,0.05); border-radius:3px; }
::-webkit-scrollbar-thumb { background:rgba(100,150,255,0.3); border-radius:3px; }
::-webkit-scrollbar-thumb:hover { background:rgba(100,150,255,0.5); }

/* ── Task Item ── */
.task-item {
  display:flex; flex-direction:column;
  padding:12px;
  background:rgba(40, 60, 90, 0.6);
  border:1px solid rgba(100, 150, 255, 0.1);
  border-radius:var(--radius-sm);
  cursor:pointer;
  transition:all 0.25s ease;
}
.task-item:hover {
  background:rgba(50, 70, 100, 0.7);
  border-color:rgba(100, 150, 255, 0.3);
  transform:translateX(4px);
}
.task-header {
  display:flex; align-items:center; justify-content:space-between;
  margin-bottom:6px;
}
.task-title {
  font-size:13px; font-weight:500; color:var(--text-primary);
  display:-webkit-box; -webkit-line-clamp:1; -webkit-box-orient:vertical; overflow:hidden;
}
.task-badge {
  font-size:10px; padding:2px 8px; border-radius:99px;
  text-transform:uppercase; letter-spacing:0.04em;
}
.badge-running { background:rgba(100,181,246,0.15); color:#64b5f6; }
.badge-planned { background:rgba(102,187,106,0.15); color:#66bb6a; }
.badge-pending { background:rgba(255,183,77,0.15); color:#ffb74d; }
.badge-completed { background:rgba(171,71,188,0.15); color:#ab47bc; }
.badge-failed { background:rgba(239,83,80,0.15); color:#ef5350; }
.task-meta {
  display:flex; align-items:center; gap:8px;
  font-size:11px; color:var(--text-muted);
}
.task-progress {
  margin-top:8px;
  height:4px;
  background:rgba(100,150,255,0.1);
  border-radius:2px;
  overflow:hidden;
}
.task-progress-bar {
  height:100%;
  border-radius:2px;
  transition:width 0.5s ease;
}
.progress-blue { background:linear-gradient(90deg,#64b5f6,#42a5f5); }
.progress-green { background:linear-gradient(90deg,#66bb6a,#43a047); }
.progress-yellow { background:linear-gradient(90deg,#ffb74d,#ffa726); }

/* ── Metrics Grid ── */
.metrics-grid {
  display:grid;
  grid-template-columns:repeat(2,1fr);
  gap:8px;
}
.metric-card {
  background:rgba(30, 50, 80, 0.6);
  border:1px solid rgba(100,150,255,0.1);
  border-radius:var(--radius-sm);
  padding:12px;
  text-align:center;
  transition:all 0.3s ease;
}
.metric-card:hover {
  border-color:var(--border-glow);
  transform:scale(1.02);
}
.metric-value {
  font-size:24px; font-weight:700;
  font-variant-numeric:tabular-nums;
}
.metric-value.blue { color:#64b5f6; }
.metric-value.green { color:#66bb6a; }
.metric-value.yellow { color:#ffb74d; }
.metric-value.red { color:#ef5350; }
.metric-label {
  font-size:10px; color:var(--text-muted);
  text-transform:uppercase; letter-spacing:0.04em;
  margin-top:2px;
}

/* ── Body Status ── */
.body-status {
  display:flex; align-items:center; gap:8px;
  padding:10px;
  background:rgba(30,50,80,0.6);
  border-radius:var(--radius-sm);
}
.body-icon {
  width:32px; height:32px;
  border-radius:50%;
  display:flex; align-items:center; justify-content:center;
  font-size:16px;
}
.body-icon.active { background:rgba(102,187,106,0.2); color:#66bb6a; }
.body-icon.candidate { background:rgba(255,183,77,0.2); color:#ffb74d; }
.body-info { flex:1; }
.body-title { font-size:12px; color:var(--text-primary); font-weight:500; }
.body-desc { font-size:10px; color:var(--text-muted); }

/* ── Timeline ── */
.timeline-list {
  display:flex; flex-direction:column;
  gap:6px;
}
.timeline-item {
  display:flex; gap:10px;
  padding:8px;
  background:rgba(30,50,80,0.4);
  border-left:3px solid rgba(100,150,255,0.3);
  border-radius:0 var(--radius-sm) var(--radius-sm) 0;
  transition:all 0.2s ease;
}
.timeline-item:hover {
  background:rgba(40,60,90,0.6);
  border-left-color:#64b5f6;
}
.timeline-icon { font-size:14px; }
.timeline-content { flex:1; }
.timeline-text { font-size:12px; color:var(--text-secondary); }
.timeline-time { font-size:10px; color:var(--text-muted); }

/* ── Candidates ── */
.candidate-list {
  display:flex; flex-direction:column;
  gap:6px;
}
.candidate-item {
  display:flex; align-items:center; gap:10px;
  padding:10px;
  background:rgba(30,50,80,0.4);
  border-radius:var(--radius-sm);
  border:1px solid rgba(100,150,255,0.1);
  transition:all 0.2s ease;
}
.candidate-item:hover {
  background:rgba(40,60,90,0.6);
  border-color:rgba(255,183,77,0.3);
}
.candidate-bulb {
  width:20px; height:20px;
  border-radius:50%;
  background:rgba(255,183,77,0.2);
  color:#ffb74d;
  display:flex; align-items:center; justify-content:center;
  font-size:10px;
}
.candidate-title { font-size:12px; color:var(--text-primary); flex:1; }
.candidate-utility { font-size:11px; font-weight:600; color:#ffb74d; }

/* ── Scene Header ── */
.scene-header {
  display:flex; align-items:center; gap:12px;
  padding:16px;
  background:rgba(20,30,50,0.8);
  border-radius:var(--radius-md);
  border:1px solid rgba(100,150,255,0.1);
}
.scene-icon {
  width:48px; height:48px;
  border-radius:var(--radius-md);
  display:flex; align-items:center; justify-content:center;
  font-size:24px;
}
.scene-icon.idle { background:rgba(100,181,246,0.15); }
.scene-icon.planning { background:rgba(255,183,77,0.15); }
.scene-icon.learning { background:rgba(102,187,106,0.15); }
.scene-icon.execution { background:rgba(239,83,80,0.15); }
.scene-icon.memory { background:rgba(171,71,188,0.15); }
.scene-title-area { flex:1; }
.scene-title { font-size:18px; font-weight:600; color:var(--text-primary); }
.scene-summary { font-size:12px; color:var(--text-secondary); margin-top:2px; }

/* ── Schedule Countdown ── */
.schedule-card {
  text-align:center;
  padding:14px;
  background:rgba(30,50,80,0.5);
  border-radius:var(--radius-md);
  border:1px solid rgba(100,150,255,0.1);
}
.schedule-label {
  font-size:11px; color:var(--text-muted);
  text-transform:uppercase; letter-spacing:0.04em;
}
.schedule-countdown {
  font-size:28px; font-weight:700;
  font-variant-numeric:tabular-nums;
  margin-top:6px;
}
.schedule-countdown.urgent { color:#ef5350; }
.schedule-countdown.normal { color:#64b5f6; }

/* ── Ambient Particles ── */
.particles {
  position:fixed; inset:0; z-index:0; pointer-events:none; overflow:hidden;
}
.particle {
  position:absolute; border-radius:50%;
  animation:particle-drift linear infinite;
}
.particle.small { width:2px; height:2px; background:rgba(100,181,246,0.6); }
.particle.medium { width:3px; height:3px; background:rgba(102,187,106,0.5); }
.particle.large { width:4px; height:4px; background:rgba(255,183,77,0.4); }
@keyframes particle-drift {
  0% { transform:translateY(100vh) translateX(0); opacity:0; }
  10% { opacity:1; }
  90% { opacity:0.3; }
  100% { transform:translateY(-10vh) translateX(50px); opacity:0; }
}

/* ── Responsive ── */
@media (max-width:1200px) {
  .room { grid-template-columns:240px 1fr 280px; }
}
@media (max-width:900px) {
  .room { grid-template-columns:1fr; grid-template-rows:auto 300px 1fr; }
  .sidebar-left { grid-row:1; }
  .main-area { grid-row:2; padding:8px; }
  .sidebar-right { grid-row:3; }
}
</style>
</head>
<body data-scene="idle">
<div class="room-bg"></div>
<div class="particles" id="particles"></div>

<main class="room">
  <!-- Left Sidebar -->
  <aside class="sidebar-left">
    <div class="card">
      <div class="card-header">
        <span class="card-title">Metrics</span>
      </div>
      <div class="metrics-grid" id="metrics"></div>
    </div>
    
    <div class="card">
      <div class="card-header">
        <span class="card-title">Body Status</span>
      </div>
      <div id="bodyStatus"></div>
    </div>
    
    <div class="card">
      <div class="card-header">
        <span class="card-title">Schedule</span>
      </div>
      <div id="schedule"></div>
    </div>
    
    <div class="card">
      <div class="card-header">
        <span class="card-title">Drive Candidates</span>
        <button class="toggle-btn" id="toggleCandidates">▼</button>
      </div>
      <div class="panel-content" id="candidatesPanel">
        <div id="candidateList"></div>
      </div>
    </div>
  </aside>

  <!-- Main Area -->
  <section class="main-area">
    <div class="scene-header">
      <div class="scene-icon" id="sceneIcon">?</div>
      <div class="scene-title-area">
        <div class="scene-title" id="sceneTitle">Waking...</div>
        <div class="scene-summary" id="sceneSummary">Connecting to supervisor...</div>
      </div>
      <div class="status-orbs" id="statusOrbs"></div>
    </div>

    <div class="character-scene">
      <div class="thoughts">
        <span class="bubble b1"></span>
        <span class="bubble b2"></span>
        <span class="bubble b3"></span>
        <span class="glyph" id="glyph">?</span>
      </div>
      <div class="xizi">
        <div class="xz-hair"></div>
        <div class="xz-head"></div>
        <div class="xz-brow l"></div><div class="xz-brow r"></div>
        <div class="xz-eye l"></div><div class="xz-eye r"></div>
        <div class="xz-mouth"></div>
        <div class="xz-body"></div>
        <div class="xz-arm l"></div><div class="xz-arm r"></div>
        <div class="xz-leg l"></div><div class="xz-leg r"></div>
        <div class="xz-prop"></div>
      </div>
    </div>

    <div class="card task-panel">
      <div class="card-header">
        <span class="card-title">Task Queue</span>
        <button class="toggle-btn" id="toggleTasks">▼</button>
      </div>
      <div class="panel-content" id="tasksPanel">
        <div id="taskList"></div>
      </div>
    </div>
  </section>

  <!-- Right Sidebar -->
  <aside class="sidebar-right">
    <div class="card">
      <div class="card-header">
        <span class="card-title">Active Executions</span>
        <button class="toggle-btn" id="toggleExecutions">▼</button>
      </div>
      <div class="panel-content" id="executionsPanel">
        <div id="execList"></div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <span class="card-title">Timeline</span>
        <button class="toggle-btn" id="toggleTimeline">▼</button>
      </div>
      <div class="panel-content" id="timelinePanel">
        <div id="timeline"></div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <span class="card-title">System Info</span>
      </div>
      <div id="systemInfo"></div>
    </div>
  </aside>
</main>

<script>
/*━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  VoidCube Supervisor Room  v3  — JS runtime
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━*/
const $ = (sel, el) => (el||document).querySelector(sel);
const $$ = (sel, el) => [...(el||document).querySelectorAll(sel)];

/* ── DOM refs ── */
const els = {
  body: document.body,
  sceneTitle: document.getElementById("sceneTitle"),
  sceneSummary: document.getElementById("sceneSummary"),
  sceneIcon: document.getElementById("sceneIcon"),
  glyph: document.getElementById("glyph"),
  metrics: document.getElementById("metrics"),
  bodyStatus: document.getElementById("bodyStatus"),
  schedule: document.getElementById("schedule"),
  candidatesPanel: document.getElementById("candidatesPanel"),
  candidateList: document.getElementById("candidateList"),
  tasksPanel: document.getElementById("tasksPanel"),
  taskList: document.getElementById("taskList"),
  executionsPanel: document.getElementById("executionsPanel"),
  execList: document.getElementById("execList"),
  timelinePanel: document.getElementById("timelinePanel"),
  timeline: document.getElementById("timeline"),
  systemInfo: document.getElementById("systemInfo"),
  statusOrbs: document.getElementById("statusOrbs"),
  particles: document.getElementById("particles"),
};

/* ── Glyphs per scene ── */
const GLYPHS = {
  idle:"🌙", planning:"⚡", learning:"📚", execution:"🚀", 
  memory:"💾", maintenance:"🔧", body_switch:"🔄", drive:"✨"
};

/* ── Scene icons ── */
const SCENE_ICONS = {
  idle:"🌙", planning:"⚡", learning:"📚", execution:"🚀",
  memory:"💾", maintenance:"🔧", body_switch:"🔄", drive:"✨"
};

/* ── Event icons ── */
const EVENT_ICONS = {
  endogenous_drive_evaluated:"🧠", endogenous_drive_planned:"💡",
  endogenous_drive_idle:"😴", task_planned:"📋", task_decided:"⚖️",
  tasks_reviewed:"🔍", tasks_planned:"📝", execution_dispatched:"🚀",
  self_learning_submitted:"📖", self_learning_completed:"✅",
  memory_compression:"💾", task_decision:"⚖️",
};

/* ── Toggle buttons ── */
const toggles = {
  candidates: { btn: $("#toggleCandidates"), panel: els.candidatesPanel, expanded: true },
  tasks: { btn: $("#toggleTasks"), panel: els.tasksPanel, expanded: true },
  executions: { btn: $("#toggleExecutions"), panel: els.executionsPanel, expanded: true },
  timeline: { btn: $("#toggleTimeline"), panel: els.timelinePanel, expanded: true },
};

function initToggles() {
  Object.keys(toggles).forEach(key => {
    const t = toggles[key];
    t.btn.addEventListener("click", () => {
      t.expanded = !t.expanded;
      t.btn.textContent = t.expanded ? "▼" : "▶";
      t.panel.style.display = t.expanded ? "flex" : "none";
    });
  });
}

/* ── Render metrics ── */
function renderMetrics(state) {
  els.metrics.replaceChildren();
  const m = state.metrics||{};
  const byPath = m.by_path||{};
  
  const metricConfigs = [
    { label: "Total", value: m.queue_total||0, color: "blue" },
    { label: "Learning", value: byPath.learning||0, color: "green" },
    { label: "Maint", value: byPath.maintenance||0, color: "yellow" },
    { label: "Errors", value: m.error_count||0, color: m.error_count > 0 ? "red" : "green" },
  ];
  
  metricConfigs.forEach(cfg => {
    const el = document.createElement("div");
    el.className = "metric-card";
    const val = document.createElement("div");
    val.className = `metric-value ${cfg.color}`;
    val.textContent = cfg.value;
    const lab = document.createElement("div");
    lab.className = "metric-label";
    lab.textContent = cfg.label;
    el.append(val, lab);
    els.metrics.append(el);
  });
}

/* ── Render body status ── */
function renderBodyStatus(status) {
  els.bodyStatus.replaceChildren();
  if (!status || !status.active_slot) {
    const el = document.createElement("div");
    el.className = "body-desc";
    el.textContent = "No body status available";
    els.bodyStatus.append(el);
    return;
  }
  
  const isActive = !!status.candidate_slot;
  const iconEl = document.createElement("div");
  iconEl.className = `body-icon ${isActive ? "candidate" : "active"}`;
  iconEl.textContent = isActive ? "🔄" : "🖥";
  
  const infoEl = document.createElement("div");
  infoEl.className = "body-info";
  const titleEl = document.createElement("div");
  titleEl.className = "body-title";
  titleEl.textContent = `Body: ${status.active_slot}`;
  const descEl = document.createElement("div");
  descEl.className = "body-desc";
  if (status.candidate_slot) {
    descEl.textContent = `Candidate: ${status.candidate_slot}`;
  } else {
    descEl.textContent = "No candidate";
  }
  infoEl.append(titleEl, descEl);
  
  els.bodyStatus.append(iconEl, infoEl);
}

/* ── Render schedule ── */
function renderSchedule(schedule) {
  els.schedule.replaceChildren();
  const nextAt = schedule.next_review_at || schedule.next_drive_at;
  
  const labelEl = document.createElement("div");
  labelEl.className = "schedule-label";
  labelEl.textContent = "Next cycle";
  
  const cdEl = document.createElement("div");
  cdEl.className = "schedule-countdown";
  
  if (!nextAt) {
    cdEl.textContent = "--";
    els.schedule.append(labelEl, cdEl);
    return;
  }
  
  const target = new Date(nextAt);
  
  function tick() {
    const now = Date.now();
    const diff = Math.max(0, target.getTime() - now);
    const hours = Math.floor(diff / 3600000);
    const mins = Math.floor((diff % 3600000) / 60000);
    const secs = Math.floor((diff % 60000) / 1000);
    
    cdEl.textContent = hours > 0 
      ? `${hours}:${mins.toString().padStart(2,'0')}:${secs.toString().padStart(2,'0')}`
      : `${mins}:${secs.toString().padStart(2,'0')}`;
    
    cdEl.className = `schedule-countdown ${diff < 60000 ? "urgent" : "normal"}`;
  }
  
  tick();
  els.schedule.append(labelEl, cdEl);
  setInterval(tick, 1000);
}

/* ── Render candidates ── */
function renderCandidates(candidates) {
  els.candidateList.replaceChildren();
  if (!candidates || !candidates.length) {
    const el = document.createElement("div");
    el.className = "body-desc";
    el.textContent = "No candidates";
    els.candidateList.append(el);
    return;
  }
  
  candidates.slice(0,5).forEach(c => {
    const el = document.createElement("div");
    el.className = "candidate-item";
    
    const bulb = document.createElement("div");
    bulb.className = "candidate-bulb";
    bulb.textContent = "💡";
    
    const title = document.createElement("div");
    title.className = "candidate-title";
    title.textContent = (c.title||"Candidate").substring(0,35);
    
    const util = document.createElement("div");
    util.className = "candidate-utility";
    util.textContent = Math.round((c.utility||0)*100) + "%";
    
    el.append(bulb, title, util);
    els.candidateList.append(el);
  });
}

/* ── Task status badge ── */
function getTaskBadge(status) {
  switch(status) {
    case "running": return { cls: "badge-running", text: "Running" };
    case "planned": return { cls: "badge-planned", text: "Planned" };
    case "approved": return { cls: "badge-pending", text: "Approved" };
    case "completed": return { cls: "badge-completed", text: "Done" };
    case "failed": return { cls: "badge-failed", text: "Failed" };
    default: return { cls: "badge-pending", text: status };
  }
}

/* ── Render tasks ── */
function renderTasks(tasks) {
  els.taskList.replaceChildren();
  if (!tasks || !tasks.length) {
    const el = document.createElement("div");
    el.className = "body-desc";
    el.textContent = "No tasks in queue";
    els.taskList.append(el);
    return;
  }
  
  tasks.slice(0,10).forEach(t => {
    const el = document.createElement("div");
    el.className = "task-item";
    
    const header = document.createElement("div");
    header.className = "task-header";
    
    const title = document.createElement("div");
    title.className = "task-title";
    title.textContent = (t.title||"Untitled").substring(0,45);
    
    const badgeInfo = getTaskBadge(t.status||"");
    const badge = document.createElement("div");
    badge.className = `task-badge ${badgeInfo.cls}`;
    badge.textContent = badgeInfo.text;
    
    header.append(title, badge);
    
    const meta = document.createElement("div");
    meta.className = "task-meta";
    const type = document.createElement("span");
    type.textContent = (t.task_family||t.governance_task_type||"").replace(/_/g," ").substring(0,25);
    const time = document.createElement("span");
    if (t.updated_at) {
      const d = new Date(t.updated_at);
      time.textContent = d.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"});
    }
    meta.append(type, time);
    
    el.append(header, meta);
    els.taskList.append(el);
  });
}

/* ── Render executions ── */
function renderExecutions(tasks) {
  els.execList.replaceChildren();
  if (!tasks || !tasks.length) {
    const el = document.createElement("div");
    el.className = "body-desc";
    el.textContent = "No active executions";
    els.execList.append(el);
    return;
  }
  
  tasks.slice(0,5).forEach(t => {
    const el = document.createElement("div");
    el.className = "task-item";
    
    const header = document.createElement("div");
    header.className = "task-header";
    
    const title = document.createElement("div");
    title.className = "task-title";
    title.textContent = (t.title||"Running").substring(0,40);
    
    const badge = document.createElement("div");
    badge.className = "task-badge badge-running";
    badge.textContent = "Running";
    
    header.append(title, badge);
    
    const meta = document.createElement("div");
    meta.className = "task-meta";
    meta.textContent = (t.task_family||"").replace(/_/g," ");
    
    const progress = document.createElement("div");
    progress.className = "task-progress";
    const bar = document.createElement("div");
    bar.className = "task-progress-bar progress-blue";
    bar.style.width = "60%";
    progress.append(bar);
    
    el.append(header, meta, progress);
    els.execList.append(el);
  });
}

/* ── Render timeline ── */
function renderTimeline(events) {
  els.timeline.replaceChildren();
  if (!events || !events.length) {
    const el = document.createElement("div");
    el.className = "body-desc";
    el.textContent = "No recent events";
    els.timeline.append(el);
    return;
  }
  
  events.slice(0,8).forEach(ev => {
    const el = document.createElement("div");
    el.className = "timeline-item";
    
    const icon = document.createElement("div");
    icon.className = "timeline-icon";
    icon.textContent = EVENT_ICONS[ev.event_type] || "●";
    
    const content = document.createElement("div");
    content.className = "timeline-content";
    
    const text = document.createElement("div");
    text.className = "timeline-text";
    text.textContent = ev.summary||ev.event_type||"Activity";
    
    const time = document.createElement("div");
    time.className = "timeline-time";
    if (ev.recorded_at) {
      const d = new Date(ev.recorded_at);
      time.textContent = d.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"});
    }
    
    content.append(text, time);
    el.append(icon, content);
    els.timeline.append(el);
  });
}

/* ── Render status orbs ── */
function renderStatusOrbs(state) {
  els.statusOrbs.replaceChildren();
  const m = state.metrics||{};
  const orbs = [
    { color: "green", active: true, title: "System" },
    { color: "blue", active: m.running_count > 0, title: "Running" },
    { color: "yellow", active: m.error_count > 0, title: "Errors" },
    { color: "purple", active: m.drive_candidates > 0, title: "Candidates" },
  ];
  
  orbs.forEach(o => {
    const el = document.createElement("div");
    el.className = `orb ${o.color} ${o.active ? "active" : ""}`;
    el.title = o.title;
    els.statusOrbs.append(el);
  });
}

/* ── Apply state ── */
function applyState(state) {
  const scene = state.scene||"idle";
  els.body.dataset.scene = scene;
  els.glyph.textContent = GLYPHS[scene]||"?";
  els.sceneIcon.textContent = SCENE_ICONS[scene]||"?";
  els.sceneIcon.className = `scene-icon ${scene}`;
  els.sceneTitle.textContent = state.title||"Supervisor Room";
  els.sceneSummary.textContent = state.summary||"";
  
  renderMetrics(state);
  renderBodyStatus(state.body_status||{});
  renderSchedule(state.schedule||{});
  renderCandidates(state.drive_candidates||[]);
  renderTasks(state.tasks||[]);
  renderExecutions(state.active_executions||[]);
  renderTimeline(state.timeline||[]);
  renderStatusOrbs(state);
  
  // Spawn particles on scene change
  spawnParticles(scene);
}

/* ── Particles ── */
function spawnParticles(scene) {
  els.particles.replaceChildren();
  const colors = {
    idle: "#64b5f6", learning: "#66bb6a", 
    planning: "#ffb74d", execution: "#ef5350",
    memory: "#ab47bc", maintenance: "#4dd0e1"
  };
  const color = colors[scene] || "#64b5f6";
  
  for (let i=0; i<15; i++) {
    const p = document.createElement("span");
    const sizes = ["small", "medium", "large"];
    p.className = `particle ${sizes[Math.floor(Math.random()*3)]}`;
    p.style.left = Math.random() * 100 + "%";
    p.style.animationDuration = (8 + Math.random() * 10) + "s";
    p.style.animationDelay = Math.random() * 5 + "s";
    p.style.background = color;
    els.particles.append(p);
  }
}

/* ── State fetching ── */
async function refresh() {
  try {
    const resp = await fetch("/ui/state", {cache:"no-store"});
    applyState(await resp.json());
  } catch(e) {
    els.body.dataset.scene = "idle";
    els.sceneTitle.textContent = "Waiting for supervisor";
    els.sceneSummary.textContent = "Connection not available yet";
    els.glyph.textContent = "🌙";
  }
}

let fallbackTimer = null;
function startFallback() {
  if (fallbackTimer) return;
  refresh();
  fallbackTimer = setInterval(refresh, 4000);
}

if ("EventSource" in window) {
  const es = new EventSource("/ui/events");
  es.addEventListener("state", function(ev) {
    if (fallbackTimer) { clearInterval(fallbackTimer); fallbackTimer=null; }
    applyState(JSON.parse(ev.data));
  });
  es.onerror = function() { startFallback(); };
} else {
  startFallback();
}

/* ── Init ── */
initToggles();
spawnParticles("idle");
</script>
</body>
</html>
"""

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
        all_tasks = [
            self._serialize_self_evolution_task(task)
            for task in self._self_evolution_queue.list_tasks()
            if task.status in {"planned", "deferred", "paused", "approved", "running", "completed", "failed"}
        ]
        all_tasks.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)

        # ── Grouped task panels by execution path ──
        panels = self._build_task_panels(all_tasks)

        drive_candidates: List[Dict[str, Any]] = []
        drive_available = True
        idle_snapshot: Dict[str, Any] = {}
        try:
            drive = await self.evaluate_endogenous_drive(
                {"max_candidates": 6, "record_activity": False}
            )
            drive_candidates = list(drive.get("candidates") or [])
            idle_snapshot = dict(drive.get("idle_window") or {})
        except Exception:
            drive_available = False

        # Extract metrics from gateway activity for richer UI expression
        activity = dict(idle_snapshot.get("activity") or {})
        counts = dict(activity.get("counts") or {})
        checks = dict(idle_snapshot.get("checks") or {})
        error_count = int(counts.get("error_count") or 0)
        in_execution_window = bool(checks.get("in_execution_window", True))

        # ── Body status (direct from registry, not task queue) ──
        body_status: Dict[str, Any] = {}
        try:
            registry = self._body_registry
            body_status = {
                "active_slot": getattr(registry, "active_slot", None),
                "candidate_slot": getattr(registry, "candidate_slot", None),
                "retired_slot": getattr(registry, "retired_slot", None),
                "shell_agents": getattr(registry, "shell_agent_count", 0),
                "last_switch_result": dict(getattr(registry, "last_switch_result", {}) or {}),
            }
        except Exception:
            pass

        # ── Schedule visibility ──
        schedule: Dict[str, Any] = {
            "review_interval_seconds": self.config.service_runtime.self_evolution_review_interval,
            "drive_interval_seconds": self.config.service_runtime.endogenous_drive_interval,
        }
        if self._service_runtime.next_review_at is not None:
            schedule["next_review_at"] = self._service_runtime.next_review_at.isoformat()
            schedule["last_review_at"] = self._service_runtime.last_review_at.isoformat() if self._service_runtime.last_review_at else None
        if self._service_runtime.next_drive_at is not None:
            schedule["next_drive_at"] = self._service_runtime.next_drive_at.isoformat()
            schedule["last_drive_at"] = self._service_runtime.last_drive_at.isoformat() if self._service_runtime.last_drive_at else None

        # ── LLM token usage ──
        mem_usage: Dict[str, Any] = {}
        try:
            from memai.llm_client import get_memory_token_usage
            raw = get_memory_token_usage()
            ctx = raw.get("context_length", 65536)
            total = raw.get("total_tokens", 0)
            mem_usage = {
                "total_tokens": total,
                "prompt_tokens": raw.get("prompt_tokens", 0),
                "completion_tokens": raw.get("completion_tokens", 0),
                "request_count": raw.get("request_count", 0),
                "context_length": ctx,
                "context_percent": round((total / ctx) * 100) if ctx > 0 else 0,
            }
        except Exception:
            pass

        # ── Metrics panel (upgraded with per-path stats) ──
        metrics = self._build_ui_metrics(all_tasks, panels, drive_candidates, body_status, error_count)

        scene, title, summary = self._map_supervisor_scene(
            panels=panels,
            all_tasks=all_tasks,
            drive_candidates=drive_candidates,
            drive_available=drive_available,
            error_count=error_count,
            in_execution_window=in_execution_window,
        )
        return {
            "status": "ok",
            "scene": scene,
            "title": title,
            "summary": summary,
            "generated_at": datetime.utcnow().isoformat(),
            "panels": panels,
            "tasks": all_tasks[:12],
            "schedule": schedule,
            "metrics": metrics,
            "mem_usage": mem_usage,
            "body_status": body_status,
            "drive_candidates": drive_candidates,
            "drive_available": drive_available,
            "error_count": error_count,
            "in_execution_window": in_execution_window,
            "active_sessions": int(activity.get("active_sessions") or 0),
            "timeline": await self._recent_supervisor_observation_timeline(limit=10),
            "governor_mode": self._governor_mode_status(),
            "active_executions": [
                self._serialize_self_evolution_task(task)
                for task in self._self_evolution_queue.list_tasks()
                if task.status == "running"
                and not task.metadata.get("execution_failed")
            ],
        }

    def _build_task_panels(self, all_tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Group tasks by execution path for panel display."""
        learning: List[Dict[str, Any]] = []
        maintenance: List[Dict[str, Any]] = []
        evolution: List[Dict[str, Any]] = []
        for task in all_tasks:
            tf = str(task.get("task_family") or task.get("governance_task_type") or "")
            if "learning" in tf:
                learning.append(task)
            elif "memory" in tf:
                maintenance.append(task)
            else:
                evolution.append(task)
        return {
            "learning": {"label": "Learning", "count": len(learning), "tasks": learning},
            "maintenance": {"label": "Maintenance", "count": len(maintenance), "tasks": maintenance},
            "evolution": {"label": "Evolution", "count": len(evolution), "tasks": evolution},
        }

    def _build_ui_metrics(
        self,
        all_tasks: List[Dict[str, Any]],
        panels: Dict[str, Any],
        drive_candidates: List[Dict[str, Any]],
        body_status: Dict[str, Any],
        error_count: int,
    ) -> Dict[str, Any]:
        """Build upgraded metrics with per-path grouping stats."""
        queue_total = len(all_tasks)
        learning_count = panels.get("learning", {}).get("count", 0)
        maintenance_count = panels.get("maintenance", {}).get("count", 0)
        evolution_count = panels.get("evolution", {}).get("count", 0)

        # Recent learning results (completed/failed in last 20 tasks)
        learning_completed = sum(
            1 for t in all_tasks
            if "learning" in str(t.get("task_family", "")) and t.get("status") == "completed"
        )
        learning_failed = sum(
            1 for t in all_tasks
            if "learning" in str(t.get("task_family", "")) and t.get("status") == "failed"
        )

        return {
            "queue_total": queue_total,
            "by_path": {
                "learning": learning_count,
                "maintenance": maintenance_count,
                "evolution": evolution_count,
            },
            "learning_results": {
                "completed": learning_completed,
                "failed": learning_failed,
            },
            "drive_candidates": len(drive_candidates),
            "body_switch_active": bool(body_status.get("candidate_slot")),
            "active_slot": body_status.get("active_slot"),
            "error_count": error_count,
            "running_count": sum(1 for t in all_tasks if t.get("status") == "running"),
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
        panels: Dict[str, Any],
        all_tasks: List[Dict[str, Any]],
        drive_candidates: List[Dict[str, Any]],
        drive_available: bool,
        error_count: int = 0,
        in_execution_window: bool = True,
    ) -> tuple[str, str, str]:
        """Redefined scenes driven by real activity rather than static heuristics."""
        error_note = f" · {error_count} recent error(s)" if error_count > 0 else ""

        # ── Scene priority: running > drive > queued > idle ──

        # 1. Active execution: body_switch
        running = [t for t in all_tasks if t.get("status") == "running"]
        if running:
            r = running[0]
            rtitle = str(r.get("title") or "Running task")
            rfamily = str(r.get("task_family") or "")
            if "learning" in rfamily:
                return (
                    "learning",
                    f"Xizi is researching{error_note}",
                    f"「{rtitle}」Agent is actively executing this learning task.",
                )
            if "memory" in rfamily:
                return (
                    "maintenance",
                    f"Xizi is tending memory{error_note}",
                    f"「{rtitle}」Memory maintenance is executing now.",
                )
            return (
                "body_switch",
                f"Xizi is at the console{error_note}",
                f"「{rtitle}」Body evolution is executing now.",
            )

        # 2. Learning tasks awaiting Agent pull
        learning_pending = [t for t in all_tasks if "learning" in str(t.get("task_family", "")) and t.get("status") == "approved"]
        if learning_pending:
            lp = learning_pending[0]
            return (
                "learning",
                f"Xizi has approved learning{error_note}",
                f"「{lp.get('title', 'Learning task')}」is ready. Agent pulls via /v1/tasks; learn-only research awaits execution.",
            )

        # 3. Endogenous drive active
        if drive_candidates:
            first = drive_candidates[0]
            value_tags = ", ".join(first.get("value_tags") or [])
            utility_pct = int((first.get("utility") or 0) * 100)
            return (
                "drive",
                f"Xizi senses something worth doing{error_note}",
                f"「{first.get('title', 'A candidate task')}」emerged from core values [{value_tags}] with utility {utility_pct}%. Awaiting governance review.",
            )

        # 4. Memory maintenance queued
        maintenance_pending = [t for t in all_tasks if "memory" in str(t.get("task_family", "")) and t.get("status") in ("approved", "planned")]
        if maintenance_pending:
            mp = maintenance_pending[0]
            return (
                "maintenance",
                f"Xizi is tending the memory shelves{error_note}",
                f"「{mp.get('title', 'Maintenance task')}」Long-term continuity is being guarded.",
            )

        # 5. Drive unavailable
        if not drive_available:
            return (
                "idle",
                "Xizi gazes out the window",
                "Gateway activity is unreachable. The room shows local supervisor state — endogenous drive will resume when the signal returns.",
            )

        # 6. Truly idle
        window_mood = "The execution window is open and the system is quiet." if in_execution_window else "Outside the execution window, the system rests."
        return (
            "idle",
            f"Xizi rests by the window{error_note}",
            f"No queued work needs attention. {window_mood} Core values are watchful but still.",
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
