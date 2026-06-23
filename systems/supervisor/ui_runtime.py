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
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>虚空立方监督室</title>
<style>
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
}
* { box-sizing:border-box; }
body { margin:0; min-height:100vh; overflow:hidden; font-family:"Inter",system-ui,sans-serif; background:var(--bg-dark); color:var(--text-primary); }
.room-bg { position:fixed; inset:0; z-index:0; background:linear-gradient(180deg, #0a0e17 0%, #0d1320 50%, #0a0e17 100%); }
.room-scene { position:absolute; inset:0; z-index:0; opacity:0; transition:opacity 1s ease; }
.room-scene.active { opacity:1; }
.scene-office { background:radial-gradient(ellipse at 30% 20%, rgba(80,100,140,0.15) 0%, transparent 50%), radial-gradient(ellipse at 70% 80%, rgba(60,40,100,0.1) 0%, transparent 40%), linear-gradient(180deg, #0d1525 0%, #0a0e17 100%); }
.scene-library { background:radial-gradient(ellipse at 50% 10%, rgba(100,80,60,0.1) 0%, transparent 50%), radial-gradient(ellipse at 20% 80%, rgba(80,60,40,0.1) 0%, transparent 40%), linear-gradient(180deg, #0d121a 0%, #0a0c12 100%); }
.scene-lab { background:radial-gradient(ellipse at 50% 20%, rgba(40,100,120,0.15) 0%, transparent 50%), radial-gradient(ellipse at 80% 60%, rgba(80,40,100,0.1) 0%, transparent 40%), linear-gradient(180deg, #0a1518 0%, #060a0c 100%); }
.scene-memory { background:radial-gradient(ellipse at 50% 50%, rgba(100,60,150,0.1) 0%, transparent 60%), radial-gradient(ellipse at 30% 30%, rgba(60,100,150,0.1) 0%, transparent 50%), linear-gradient(180deg, #0e0a17 0%, #0a0610 100%); }
.room { position:relative; z-index:1; display:grid; grid-template-columns:280px 1fr 320px; grid-template-rows:100vh; height:100vh; }
.sidebar-left, .sidebar-right { display:flex; flex-direction:column; padding:16px; gap:12px; overflow-y:auto; }
.sidebar-left { grid-column:1; }
.main-area { grid-column:2; display:flex; flex-direction:column; padding:16px; gap:12px; overflow-y:auto; }
.sidebar-right { grid-column:3; }
.card { background:var(--bg-card); border:1px solid rgba(100,150,255,0.15); border-radius:10px; padding:14px; backdrop-filter:blur(12px); transition:all 0.3s ease; }
.card:hover { border-color:var(--border-glow); box-shadow:0 0 20px rgba(100,150,255,0.1); }
.card-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; }
.card-title { font-size:13px; font-weight:600; color:var(--text-primary); text-transform:uppercase; letter-spacing:0.06em; }
.toggle-btn { width:24px; height:24px; border:none; border-radius:6px; background:rgba(100,150,255,0.1); color:var(--text-secondary); cursor:pointer; display:flex; align-items:center; justify-content:center; transition:all 0.2s ease; }
.toggle-btn:hover { background:rgba(100,150,255,0.2); color:var(--accent-blue); }
.character-scene { position:relative; display:flex; align-items:flex-end; justify-content:center; min-height:400px; border-radius:14px; background:rgba(20,30,50,0.4); border:1px solid rgba(100,150,255,0.1); overflow:hidden; }
.floor { position:absolute; bottom:0; left:0; right:0; height:60px; background:linear-gradient(180deg, rgba(80,90,110,0.3) 0%, rgba(60,70,90,0.4) 100%); border-top:2px solid rgba(100,120,150,0.2); }
.office-desk { position:absolute; bottom:60px; left:20%; width:280px; height:80px; background:linear-gradient(180deg, #5c4a3a 0%, #3d2f24 100%); border-radius:4px; z-index:1; }
.office-desk::before { content:''; position:absolute; top:-3px; left:0; right:0; height:3px; background:#7a6048; }
.computer-monitor { position:absolute; bottom:110px; left:22%; width:140px; height:90px; background:#1a1a2e; border:4px solid #3a3a5a; border-radius:6px; z-index:3; overflow:hidden; }
.monitor-screen { position:absolute; top:4px; left:4px; right:4px; bottom:4px; background:#0a0a15; border-radius:2px; }
.monitor-glow { position:absolute; top:0; left:0; right:0; bottom:0; background:linear-gradient(135deg, rgba(100,181,246,0.1) 0%, transparent 50%); animation:monitor-pulse 3s ease-in-out infinite; }
@keyframes monitor-pulse { 0%,100% { opacity:0.3; } 50% { opacity:0.6; } }
.monitor-stand { position:absolute; bottom:-15px; left:50%; transform:translateX(-50%); width:30px; height:15px; background:#3a3a5a; }
.monitor-base { position:absolute; bottom:-20px; left:50%; transform:translateX(-50%); width:50px; height:5px; background:#4a4a6a; }
.computer-keyboard { position:absolute; bottom:85px; left:24%; width:100px; height:12px; background:#2a2a3a; border-radius:2px; z-index:2; }
.keyboard-key { position:absolute; top:2px; width:8px; height:8px; background:#3a3a4a; border-radius:1px; }
.keyboard-key:nth-child(1) { left:4px; } .keyboard-key:nth-child(2) { left:14px; } .keyboard-key:nth-child(3) { left:24px; } .keyboard-key:nth-child(4) { left:34px; } .keyboard-key:nth-child(5) { left:44px; } .keyboard-key:nth-child(6) { left:54px; } .keyboard-key:nth-child(7) { left:64px; } .keyboard-key:nth-child(8) { left:74px; } .keyboard-key:nth-child(9) { left:84px; }
.computer-mouse { position:absolute; bottom:85px; left:33%; width:20px; height:14px; background:#2a2a3a; border-radius:50%; z-index:2; }
.library-bookshelf { position:absolute; bottom:60px; left:10%; width:180px; height:200px; background:#3d2f24; border-radius:4px; z-index:1; }
.shelf-row { position:absolute; left:5px; right:5px; height:45px; border-bottom:2px solid #2a2018; }
.shelf-row:nth-child(1) { top:5px; } .shelf-row:nth-child(2) { top:55px; } .shelf-row:nth-child(3) { top:105px; } .shelf-row:nth-child(4) { top:155px; border:none; }
.shelf-book { position:absolute; top:5px; height:35px; border-radius:2px; }
.shelf-row:nth-child(1) .shelf-book:nth-child(1) { left:5px; width:12px; background:#c44; }
.shelf-row:nth-child(1) .shelf-book:nth-child(2) { left:19px; width:14px; background:#4c4; }
.shelf-row:nth-child(1) .shelf-book:nth-child(3) { left:35px; width:10px; background:#44c; }
.shelf-row:nth-child(1) .shelf-book:nth-child(4) { left:47px; width:16px; background:#cc4; }
.shelf-row:nth-child(2) .shelf-book:nth-child(1) { left:5px; width:14px; background:#4cc; }
.shelf-row:nth-child(2) .shelf-book:nth-child(2) { left:21px; width:12px; background:#c4c; }
.shelf-row:nth-child(2) .shelf-book:nth-child(3) { left:35px; width:18px; background:#444; }
.shelf-row:nth-child(3) .shelf-book:nth-child(1) { left:5px; width:10px; background:#ccc; }
.shelf-row:nth-child(3) .shelf-book:nth-child(2) { left:17px; width:14px; background:#c84; }
.shelf-row:nth-child(3) .shelf-book:nth-child(3) { left:33px; width:12px; background:#84c; }
.shelf-row:nth-child(4) .shelf-book:nth-child(1) { left:5px; width:16px; background:#4c8; }
.shelf-row:nth-child(4) .shelf-book:nth-child(2) { left:23px; width:10px; background:#c48; }
.lab-equipment { position:absolute; bottom:60px; right:15%; width:120px; height:150px; z-index:1; }
.lab-computer { position:absolute; bottom:0; left:0; width:100px; height:60px; background:#1a2a2a; border-radius:4px; }
.lab-screen { position:absolute; top:5px; left:5px; right:5px; bottom:25px; background:#0a1515; border-radius:2px; }
.lab-flask { position:absolute; bottom:60px; left:10px; width:30px; height:50px; background:rgba(100,181,246,0.2); border:2px solid rgba(100,181,246,0.5); border-radius:5px 5px 15px 15px; }
.lab-flask::before { content:''; position:absolute; bottom:5px; left:5px; right:5px; height:20px; background:rgba(100,181,246,0.4); border-radius:0 0 13px 13px; animation:flask-bubble 2s ease-in-out infinite; }
@keyframes flask-bubble { 0%,100% { opacity:0.4; } 50% { opacity:0.8; } }
.memory-orbs { position:absolute; inset:0; pointer-events:none; z-index:0; }
.memory-orb { position:absolute; width:20px; height:20px; background:radial-gradient(circle, rgba(171,71,188,0.6) 0%, rgba(171,71,188,0.2) 100%); border-radius:50%; animation:orb-float 6s ease-in-out infinite; }
.memory-orb:nth-child(1) { left:20%; top:30%; animation-delay:0s; }
.memory-orb:nth-child(2) { left:70%; top:40%; animation-delay:1s; }
.memory-orb:nth-child(3) { left:40%; top:60%; animation-delay:2s; }
.memory-orb:nth-child(4) { left:80%; top:20%; animation-delay:3s; }
.memory-orb:nth-child(5) { left:30%; top:70%; animation-delay:4s; }
@keyframes orb-float { 0%,100% { transform:translateY(0) scale(1); opacity:0.6; } 50% { transform:translateY(-30px) scale(1.2); opacity:1; } }
.xizi { position:relative; width:180px; height:280px; z-index:10; }
body[data-scene="idle"] .xizi { animation:xizi-idle 4s ease-in-out infinite; }
@keyframes xizi-idle { 0%,100% { transform:translateY(0) rotate(0deg); } 25% { transform:translateY(-5px) rotate(1deg); } 75% { transform:translateY(-3px) rotate(-1deg); } }
body[data-scene="planning"] .xizi { animation:xizi-think 3s ease-in-out infinite; }
body[data-scene="planning"] .xz-arm.l { animation:arm-think 1.2s ease-in-out infinite; }
body[data-scene="planning"] .xz-head { animation:head-tilt 2s ease-in-out infinite; }
@keyframes xizi-think { 0%,100% { transform:translateY(0) translateX(20px); } 50% { transform:translateY(-8px) translateX(20px); } }
@keyframes arm-think { 0%,100% { transform:rotate(10deg) translateY(0); } 50% { transform:rotate(40deg) translateY(-15px); } }
@keyframes head-tilt { 0%,100% { transform:rotate(0deg); } 25% { transform:rotate(-3deg); } 75% { transform:rotate(3deg); } }
body[data-scene="learning"] .xizi { animation:xizi-read 3s ease-in-out infinite; transform:translateX(-30px); }
body[data-scene="learning"] .xz-prop { animation:book-read 2s ease-in-out infinite; }
@keyframes xizi-read { 0%,100% { transform:translateX(-30px) translateY(0); } 50% { transform:translateX(-30px) translateY(-6px); } }
@keyframes book-read { 0%,100% { transform:rotate(-5deg) scale(1); } 50% { transform:rotate(10deg) scale(1.1); } }
body[data-scene="execution"] .xizi { animation:xizi-type 1s ease-in-out infinite; transform:translateX(10px); }
body[data-scene="execution"] .xz-arm.l, body[data-scene="execution"] .xz-arm.r { animation:arm-type 0.3s ease-in-out infinite; }
body[data-scene="execution"] .xz-arm.r { animation-delay:0.15s; }
body[data-scene="execution"] .computer-keyboard .keyboard-key { animation:key-press 0.2s ease-in-out infinite; }
body[data-scene="execution"] .computer-keyboard .keyboard-key:nth-child(2n) { animation-delay:0.1s; }
body[data-scene="execution"] .computer-keyboard .keyboard-key:nth-child(3n) { animation-delay:0.05s; }
@keyframes xizi-type { 0%,100% { transform:translateX(10px) translateY(0); } 50% { transform:translateX(10px) translateY(3px); } }
@keyframes arm-type { 0%,100% { transform:rotate(15deg) translateY(0); } 50% { transform:rotate(40deg) translateY(18px); } }
@keyframes key-press { 0%,100% { transform:translateY(0); background:#3a3a4a; } 50% { transform:translateY(2px); background:#4a4a5a; } }
body[data-scene="memory"] .xizi { animation:xizi-reach 3s ease-in-out infinite; transform:translateX(-50px); }
body[data-scene="memory"] .xz-arm.r { animation:arm-reach-memory 2s ease-in-out infinite; }
body[data-scene="memory"] .memory-orb { animation:orb-attract 4s ease-in-out infinite; }
@keyframes xizi-reach { 0%,100% { transform:translateX(-50px) translateY(0); } 50% { transform:translateX(-50px) translateY(-10px); } }
@keyframes arm-reach-memory { 0%,100% { transform:rotate(-10deg); } 50% { transform:rotate(-60deg) translateY(-20px) translateX(10px); } }
@keyframes orb-attract { 0%,100% { transform:translateY(0) scale(1); } 50% { transform:translateY(-50px) scale(1.5); } }
.xz-head { position:absolute; left:45px; top:10px; width:88px; height:84px; border-radius:44% 44% 40% 42%; background:linear-gradient(155deg,#ffe4c0,#f0cfa0); box-shadow:inset -8px -8px rgba(190,120,90,.2); z-index:4; transform-origin:center bottom; }
.xz-hair { position:absolute; left:38px; top:2px; width:100px; height:68px; border-radius:42px 42px 18px 18px; background:#1a2028; z-index:5; clip-path:polygon(0 0,100% 0,92% 70%,70% 46%,54% 76%,36% 48%,20% 78%,6% 52%); }
.xz-eye { position:absolute; top:48px; width:12px; height:18px; border-radius:50%; background:#1e2835; z-index:6; animation:blink 6s infinite; }
.xz-eye.l { left:68px; } .xz-eye.r { left:98px; }
@keyframes blink { 0%,94%,100% { transform:scaleY(1); } 96%,98% { transform:scaleY(0.08); } }
.xz-brow { position:absolute; top:43px; width:16px; height:4px; border-radius:2px; background:#4a3a30; z-index:6; }
.xz-brow.l { left:66px; } .xz-brow.r { left:96px; }
.xz-mouth { position:absolute; left:82px; top:76px; width:20px; height:10px; border-bottom:3px solid #a06858; border-radius:50%; z-index:6; }
.xz-body { position:absolute; left:52px; top:92px; width:76px; height:102px; border-radius:20px 20px 16px 16px; background:linear-gradient(140deg,#4dd0e1,#26a69a); box-shadow:inset -8px -8px rgba(20,80,90,0.15); z-index:3; }
.xz-arm { position:absolute; top:118px; width:28px; height:80px; border-radius:14px; background:linear-gradient(90deg,#ffe4c0,#f0cfa0); transform-origin:top center; z-index:3; }
.xz-arm.l { left:35px; transform:rotate(12deg); } .xz-arm.r { left:112px; transform:rotate(-14deg); }
.xz-leg { position:absolute; top:188px; width:32px; height:82px; border-radius:13px; background:#253545; z-index:2; }
.xz-leg.l { left:58px; } .xz-leg.r { left:94px; }
.xz-prop { position:absolute; left:120px; top:150px; width:52px; height:38px; border-radius:5px; background:#e8f5e9; border:3px solid #5d4037; transform:rotate(-6deg); z-index:7; }
.xz-prop::before { content:'📖'; position:absolute; top:5px; left:50%; transform:translateX(-50%); font-size:20px; }
.thoughts { position:absolute; right:30px; top:60px; transform:translate(30px,-20px); width:110px; height:80px; z-index:3; }
.bubble { position:absolute; border-radius:50%; background:rgba(255,252,245,0.95); border:2px solid rgba(100,150,255,0.3); box-shadow:0 6px 16px rgba(0,0,0,0.2); animation:bob 2.8s ease-in-out infinite; }
.bubble.b1 { width:72px; height:48px; left:12px; top:0; } .bubble.b2 { width:20px; height:20px; left:0; top:45px; animation-delay:0.3s; } .bubble.b3 { width:12px; height:12px; left:-4px; top:62px; animation-delay:0.6s; }
@keyframes bob { 0%,100% { transform:translateY(0); } 50% { transform:translateY(-12px); } }
.glyph { position:absolute; left:42px; top:6px; font-size:28px; font-weight:800; color:#3d5d6b; animation:glyph-pulse 2s ease-in-out infinite; }
@keyframes glyph-pulse { 0%,100% { transform:scale(1); opacity:0.7; } 50% { transform:scale(1.3); opacity:1; } }
.status-orbs { position:absolute; bottom:20px; left:50%; transform:translateX(-50%); display:flex; gap:14px; }
.orb { width:14px; height:14px; border-radius:50%; transition:all 0.3s ease; }
.orb.active { box-shadow:0 0 14px currentColor; animation:pulse-glow 2s ease-in-out infinite; }
@keyframes pulse-glow { 0%,100% { transform:scale(1); opacity:1; } 50% { transform:scale(1.4); opacity:0.6; } }
.orb.blue { background:#64b5f6; } .orb.green { background:#66bb6a; } .orb.yellow { background:#ffb74d; } .orb.red { background:#ef5350; } .orb.purple { background:#ab47bc; }
.task-panel { flex:1; display:flex; flex-direction:column; min-height:0; }
.panel-content { flex:1; overflow-y:auto; display:flex; flex-direction:column; gap:8px; }
::-webkit-scrollbar { width:6px; }
::-webkit-scrollbar-track { background:rgba(100,150,255,0.05); border-radius:3px; }
::-webkit-scrollbar-thumb { background:rgba(100,150,255,0.3); border-radius:3px; }
::-webkit-scrollbar-thumb:hover { background:rgba(100,150,255,0.5); }
.task-item { display:flex; flex-direction:column; padding:12px; background:rgba(40,60,90,0.6); border:1px solid rgba(100,150,255,0.1); border-radius:6px; cursor:pointer; transition:all 0.25s ease; }
.task-item:hover { background:rgba(50,70,100,0.7); border-color:rgba(100,150,255,0.3); transform:translateX(4px); }
.task-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:6px; }
.task-title { font-size:13px; font-weight:500; color:var(--text-primary); display:-webkit-box; -webkit-line-clamp:1; -webkit-box-orient:vertical; overflow:hidden; }
.task-badge { font-size:10px; padding:2px 8px; border-radius:99px; text-transform:uppercase; letter-spacing:0.04em; }
.badge-running { background:rgba(100,181,246,0.15); color:#64b5f6; }
.badge-planned { background:rgba(102,187,106,0.15); color:#66bb6a; }
.badge-pending { background:rgba(255,183,77,0.15); color:#ffb74d; }
.badge-completed { background:rgba(171,71,188,0.15); color:#ab47bc; }
.badge-failed { background:rgba(239,83,80,0.15); color:#ef5350; }
.task-meta { display:flex; align-items:center; gap:8px; font-size:11px; color:var(--text-muted); }
.task-progress { margin-top:8px; height:4px; background:rgba(100,150,255,0.1); border-radius:2px; overflow:hidden; }
.task-progress-bar { height:100%; border-radius:2px; transition:width 0.5s ease; }
.progress-blue { background:linear-gradient(90deg,#64b5f6,#42a5f5); }
.progress-green { background:linear-gradient(90deg,#66bb6a,#43a047); }
.progress-yellow { background:linear-gradient(90deg,#ffb74d,#ffa726); }
.metrics-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:8px; }
.metric-card { background:rgba(30,50,80,0.6); border:1px solid rgba(100,150,255,0.1); border-radius:6px; padding:12px; text-align:center; transition:all 0.3s ease; }
.metric-card:hover { border-color:var(--border-glow); transform:scale(1.02); }
.metric-value { font-size:24px; font-weight:700; font-variant-numeric:tabular-nums; }
.metric-value.blue { color:#64b5f6; } .metric-value.green { color:#66bb6a; } .metric-value.yellow { color:#ffb74d; } .metric-value.red { color:#ef5350; }
.metric-label { font-size:10px; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.04em; margin-top:2px; }
.body-status { display:flex; align-items:center; gap:8px; padding:10px; background:rgba(30,50,80,0.6); border-radius:6px; }
.body-icon { width:32px; height:32px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:16px; }
.body-icon.active { background:rgba(102,187,106,0.2); color:#66bb6a; }
.body-icon.candidate { background:rgba(255,183,77,0.2); color:#ffb74d; }
.body-info { flex:1; }
.body-title { font-size:12px; color:var(--text-primary); font-weight:500; }
.body-desc { font-size:10px; color:var(--text-muted); }
.timeline-list { display:flex; flex-direction:column; gap:6px; }
.timeline-item { display:flex; gap:10px; padding:8px; background:rgba(30,50,80,0.4); border-left:3px solid rgba(100,150,255,0.3); border-radius:0 6px 6px 0; transition:all 0.2s ease; }
.timeline-item:hover { background:rgba(40,60,90,0.6); border-left-color:#64b5f6; }
.timeline-icon { font-size:14px; }
.timeline-content { flex:1; }
.timeline-text { font-size:12px; color:var(--text-secondary); }
.timeline-time { font-size:10px; color:var(--text-muted); }
.candidate-list { display:flex; flex-direction:column; gap:6px; }
.candidate-item { display:flex; align-items:center; gap:10px; padding:10px; background:rgba(30,50,80,0.4); border-radius:6px; border:1px solid rgba(100,150,255,0.1); transition:all 0.2s ease; }
.candidate-item:hover { background:rgba(40,60,90,0.6); border-color:rgba(255,183,77,0.3); }
.candidate-bulb { width:20px; height:20px; border-radius:50%; background:rgba(255,183,77,0.2); color:#ffb74d; display:flex; align-items:center; justify-content:center; font-size:10px; }
.candidate-title { font-size:12px; color:var(--text-primary); flex:1; }
.candidate-utility { font-size:11px; font-weight:600; color:#ffb74d; }
.scene-header { display:flex; align-items:center; gap:12px; padding:16px; background:rgba(20,30,50,0.8); border-radius:10px; border:1px solid rgba(100,150,255,0.1); }
.scene-icon { width:48px; height:48px; border-radius:10px; display:flex; align-items:center; justify-content:center; font-size:24px; }
.scene-icon.idle { background:rgba(100,181,246,0.15); }
.scene-icon.planning { background:rgba(255,183,77,0.15); }
.scene-icon.learning { background:rgba(102,187,106,0.15); }
.scene-icon.execution { background:rgba(239,83,80,0.15); }
.scene-icon.memory { background:rgba(171,71,188,0.15); }
.scene-title-area { flex:1; }
.scene-title { font-size:18px; font-weight:600; color:var(--text-primary); }
.scene-summary { font-size:12px; color:var(--text-secondary); margin-top:2px; }
.schedule-card { text-align:center; padding:14px; background:rgba(30,50,80,0.5); border-radius:10px; border:1px solid rgba(100,150,255,0.1); }
.schedule-label { font-size:11px; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.04em; }
.schedule-countdown { font-size:28px; font-weight:700; font-variant-numeric:tabular-nums; margin-top:6px; }
.schedule-countdown.urgent { color:#ef5350; }
.schedule-countdown.normal { color:#64b5f6; }
.particles { position:fixed; inset:0; z-index:0; pointer-events:none; overflow:hidden; }
.particle { position:absolute; border-radius:50%; animation:particle-drift linear infinite; }
.particle.small { width:2px; height:2px; background:rgba(100,181,246,0.6); }
.particle.medium { width:3px; height:3px; background:rgba(102,187,106,0.5); }
.particle.large { width:4px; height:4px; background:rgba(255,183,77,0.4); }
@keyframes particle-drift { 0% { transform:translateY(100vh) translateX(0); opacity:0; } 10% { opacity:1; } 90% { opacity:0.3; } 100% { transform:translateY(-10vh) translateX(50px); opacity:0; } }
@media (max-width:1200px) { .room { grid-template-columns:240px 1fr 280px; } }
@media (max-width:900px) { .room { grid-template-columns:1fr; grid-template-rows:auto 350px 1fr; } .sidebar-left { grid-row:1; } .main-area { grid-row:2; padding:8px; } .sidebar-right { grid-row:3; } }
</style>
</head>
<body data-scene="idle">
<div class="room-bg"></div>
<div class="room-scene scene-office" id="sceneOffice"></div>
<div class="room-scene scene-library" id="sceneLibrary"></div>
<div class="room-scene scene-lab" id="sceneLab"></div>
<div class="room-scene scene-memory" id="sceneMemory"></div>
<div class="particles" id="particles"></div>
<main class="room">
<aside class="sidebar-left">
<div class="card"><div class="card-header"><span class="card-title">系统指标</span></div><div class="metrics-grid" id="metrics"></div></div>
<div class="card"><div class="card-header"><span class="card-title">身体状态</span></div><div id="bodyStatus"></div></div>
<div class="card"><div class="card-header"><span class="card-title">执行计划</span></div><div id="schedule"></div></div>
<div class="card"><div class="card-header"><span class="card-title">内生驱动候选</span><button class="toggle-btn" id="toggleCandidates">▼</button></div><div class="panel-content" id="candidatesPanel"><div id="candidateList"></div></div></div>
</aside>
<section class="main-area">
<div class="scene-header">
<div class="scene-icon" id="sceneIcon">🌙</div>
<div class="scene-title-area"><div class="scene-title" id="sceneTitle">虚空立方监督室</div><div class="scene-summary" id="sceneSummary">系统正在初始化...</div></div>
<div class="status-orbs" id="statusOrbs"></div>
</div>
<div class="character-scene">
<div class="floor"></div>
<div class="office-desk"></div>
<div class="computer-monitor"><div class="monitor-screen"><div class="monitor-glow"></div></div><div class="monitor-stand"></div><div class="monitor-base"></div></div>
<div class="computer-keyboard"><div class="keyboard-key"></div><div class="keyboard-key"></div><div class="keyboard-key"></div><div class="keyboard-key"></div><div class="keyboard-key"></div><div class="keyboard-key"></div><div class="keyboard-key"></div><div class="keyboard-key"></div><div class="keyboard-key"></div></div>
<div class="computer-mouse"></div>
<div class="library-bookshelf"><div class="shelf-row"><div class="shelf-book"></div><div class="shelf-book"></div><div class="shelf-book"></div><div class="shelf-book"></div></div><div class="shelf-row"><div class="shelf-book"></div><div class="shelf-book"></div><div class="shelf-book"></div></div><div class="shelf-row"><div class="shelf-book"></div><div class="shelf-book"></div><div class="shelf-book"></div></div><div class="shelf-row"><div class="shelf-book"></div><div class="shelf-book"></div></div></div>
<div class="lab-equipment"><div class="lab-computer"><div class="lab-screen"></div></div><div class="lab-flask"></div></div>
<div class="memory-orbs"><div class="memory-orb"></div><div class="memory-orb"></div><div class="memory-orb"></div><div class="memory-orb"></div><div class="memory-orb"></div></div>
<div class="thoughts"><span class="bubble b1"></span><span class="bubble b2"></span><span class="bubble b3"></span><span class="glyph" id="glyph">🌙</span></div>
<div class="xizi"><div class="xz-hair"></div><div class="xz-head"></div><div class="xz-brow l"></div><div class="xz-brow r"></div><div class="xz-eye l"></div><div class="xz-eye r"></div><div class="xz-mouth"></div><div class="xz-body"></div><div class="xz-arm l"></div><div class="xz-arm r"></div><div class="xz-leg l"></div><div class="xz-leg r"></div><div class="xz-prop"></div></div>
</div>
<div class="card task-panel"><div class="card-header"><span class="card-title">任务队列</span><button class="toggle-btn" id="toggleTasks">▼</button></div><div class="panel-content" id="tasksPanel"><div id="taskList"></div></div></div>
</section>
<aside class="sidebar-right">
<div class="card"><div class="card-header"><span class="card-title">执行中任务</span><button class="toggle-btn" id="toggleExecutions">▼</button></div><div class="panel-content" id="executionsPanel"><div id="execList"></div></div></div>
<div class="card"><div class="card-header"><span class="card-title">活动时间线</span><button class="toggle-btn" id="toggleTimeline">▼</button></div><div class="panel-content" id="timelinePanel"><div id="timeline"></div></div></div>
<div class="card"><div class="card-header"><span class="card-title">系统信息</span></div><div id="systemInfo"></div></div>
</aside>
</main>
<script>
const $ = (sel, el) => (el||document).querySelector(sel);
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
  scenes: { office: document.getElementById("sceneOffice"), library: document.getElementById("sceneLibrary"), lab: document.getElementById("sceneLab"), memory: document.getElementById("sceneMemory") },
};
const SCENE_ROOMS = { idle:"office", planning:"office", learning:"library", execution:"office", memory:"memory", maintenance:"lab", body_switch:"lab", drive:"memory" };
const SCENE_LABELS = { idle:"待机状态", planning:"计划制定中", learning:"自主学习中", execution:"任务执行中", memory:"记忆处理中", maintenance:"系统维护", body_switch:"身体切换", drive:"内生驱动" };
const SCENE_DESCRIPTIONS = { idle:"系统正在等待指令，监督者保持警觉", planning:"监督者正在分析任务，制定执行计划", learning:"监督者正在学习新知识，扩充知识库", execution:"监督者正在执行任务，双手在键盘上飞快敲击", memory:"监督者正在整理记忆，确保信息的连续性", maintenance:"监督者正在进行系统维护，优化性能", body_switch:"监督者正在准备身体切换，确保安全过渡", drive:"内生驱动激活，正在评估候选任务" };
const GLYPHS = { idle:"🌙", planning:"⚡", learning:"📖", execution:"⌨️", memory:"💾", maintenance:"🔧", body_switch:"🔄", drive:"✨" };
const SCENE_ICONS = { idle:"🌙", planning:"⚡", learning:"📖", execution:"⌨️", memory:"💾", maintenance:"🔧", body_switch:"🔄", drive:"✨" };
const EVENT_ICONS = { endogenous_drive_evaluated:"🧠", endogenous_drive_planned:"💡", endogenous_drive_idle:"😴", task_planned:"📋", task_decided:"⚖️", tasks_reviewed:"🔍", tasks_planned:"📝", execution_dispatched:"🚀", self_learning_submitted:"📖", self_learning_completed:"✅", memory_compression:"💾" };
const toggles = { candidates:{btn:$("#toggleCandidates"),panel:els.candidatesPanel,expanded:true}, tasks:{btn:$("#toggleTasks"),panel:els.tasksPanel,expanded:true}, executions:{btn:$("#toggleExecutions"),panel:els.executionsPanel,expanded:true}, timeline:{btn:$("#toggleTimeline"),panel:els.timelinePanel,expanded:true} };
function initToggles() { Object.keys(toggles).forEach(k => { const t=toggles[k]; t.btn.addEventListener("click",()=>{ t.expanded=!t.expanded; t.btn.textContent=t.expanded?"▼":"▶"; t.panel.style.display=t.expanded?"flex":"none"; }); }); }
function renderMetrics(state) { els.metrics.replaceChildren(); const m=state.metrics||{}; const byPath=m.by_path||{}; [ {label:"总任务",value:m.queue_total||0,color:"blue"}, {label:"学习任务",value:byPath.learning||0,color:"green"}, {label:"维护任务",value:byPath.maintenance||0,color:"yellow"}, {label:"错误数",value:m.error_count||0,color:m.error_count>0?"red":"green"} ].forEach(cfg=>{ const el=document.createElement("div"); el.className="metric-card"; const val=document.createElement("div"); val.className=`metric-value ${cfg.color}`; val.textContent=cfg.value; const lab=document.createElement("div"); lab.className="metric-label"; lab.textContent=cfg.label; el.append(val,lab); els.metrics.append(el); }); }
function renderBodyStatus(status) { els.bodyStatus.replaceChildren(); if(!status||!status.active_slot){ const el=document.createElement("div"); el.className="body-desc"; el.textContent="无身体状态信息"; els.bodyStatus.append(el); return; } const isActive=!!status.candidate_slot; const iconEl=document.createElement("div"); iconEl.className=`body-icon ${isActive?"candidate":"active"}`; iconEl.textContent=isActive?"🔄":"🖥"; const infoEl=document.createElement("div"); infoEl.className="body-info"; const titleEl=document.createElement("div"); titleEl.className="body-title"; titleEl.textContent=`活动身体: ${status.active_slot}`; const descEl=document.createElement("div"); descEl.className="body-desc"; descEl.textContent=status.candidate_slot?`候选身体: ${status.candidate_slot}`:"无候选身体"; infoEl.append(titleEl,descEl); els.bodyStatus.append(iconEl,infoEl); }
function renderSchedule(schedule) { els.schedule.replaceChildren(); const nextAt=schedule.next_review_at||schedule.next_drive_at; const labelEl=document.createElement("div"); labelEl.className="schedule-label"; labelEl.textContent="下次执行周期"; const cdEl=document.createElement("div"); cdEl.className="schedule-countdown"; if(!nextAt){ cdEl.textContent="--"; els.schedule.append(labelEl,cdEl); return; } const target=new Date(nextAt); function tick(){ const now=Date.now(); const diff=Math.max(0,target.getTime()-now); const hours=Math.floor(diff/3600000); const mins=Math.floor((diff%3600000)/60000); const secs=Math.floor((diff%60000)/1000); cdEl.textContent=hours>0?`${hours}:${mins.toString().padStart(2,'0')}:${secs.toString().padStart(2,'0')}`:`${mins}:${secs.toString().padStart(2,'0')}`; cdEl.className=`schedule-countdown ${diff<60000?"urgent":"normal"}`; } tick(); els.schedule.append(labelEl,cdEl); setInterval(tick,1000); }
function renderCandidates(candidates) { els.candidateList.replaceChildren(); if(!candidates||!candidates.length){ const el=document.createElement("div"); el.className="body-desc"; el.textContent="无候选任务"; els.candidateList.append(el); return; } candidates.slice(0,5).forEach(c=>{ const el=document.createElement("div"); el.className="candidate-item"; const bulb=document.createElement("div"); bulb.className="candidate-bulb"; bulb.textContent="💡"; const title=document.createElement("div"); title.className="candidate-title"; title.textContent=(c.title||"候选任务").substring(0,35); const util=document.createElement("div"); util.className="candidate-utility"; util.textContent=Math.round((c.utility||0)*100)+"%"; el.append(bulb,title,util); els.candidateList.append(el); }); }
function getTaskBadge(status){ switch(status){ case"running":return{cls:"badge-running",text:"执行中"}; case"planned":return{cls:"badge-planned",text:"已计划"}; case"approved":return{cls:"badge-pending",text:"已批准"}; case"completed":return{cls:"badge-completed",text:"已完成"}; case"failed":return{cls:"badge-failed",text:"失败"}; default:return{cls:"badge-pending",text:status}; } }
function renderTasks(tasks) { els.taskList.replaceChildren(); if(!tasks||!tasks.length){ const el=document.createElement("div"); el.className="body-desc"; el.textContent="任务队列为空"; els.taskList.append(el); return; } tasks.slice(0,10).forEach(t=>{ const el=document.createElement("div"); el.className="task-item"; const header=document.createElement("div"); header.className="task-header"; const title=document.createElement("div"); title.className="task-title"; title.textContent=(t.title||"未命名任务").substring(0,45); const badgeInfo=getTaskBadge(t.status||""); const badge=document.createElement("div"); badge.className=`task-badge ${badgeInfo.cls}`; badge.textContent=badgeInfo.text; header.append(title,badge); const meta=document.createElement("div"); meta.className="task-meta"; const type=document.createElement("span"); type.textContent=(t.task_family||t.governance_task_type||"").replace(/_/g," ").substring(0,25); const time=document.createElement("span"); if(t.updated_at){ const d=new Date(t.updated_at); time.textContent=d.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"}); } meta.append(type,time); el.append(header,meta); els.taskList.append(el); }); }
function renderExecutions(tasks) { els.execList.replaceChildren(); if(!tasks||!tasks.length){ const el=document.createElement("div"); el.className="body-desc"; el.textContent="无执行中任务"; els.execList.append(el); return; } tasks.slice(0,5).forEach(t=>{ const el=document.createElement("div"); el.className="task-item"; const header=document.createElement("div"); header.className="task-header"; const title=document.createElement("div"); title.className="task-title"; title.textContent=(t.title||"运行中").substring(0,40); const badge=document.createElement("div"); badge.className="task-badge badge-running"; badge.textContent="执行中"; header.append(title,badge); const meta=document.createElement("div"); meta.className="task-meta"; meta.textContent=(t.task_family||"").replace(/_/g," "); const progress=document.createElement("div"); progress.className="task-progress"; const bar=document.createElement("div"); bar.className="task-progress-bar progress-blue"; bar.style.width="60%"; progress.append(bar); el.append(header,meta,progress); els.execList.append(el); }); }
function renderTimeline(events) { els.timeline.replaceChildren(); if(!events||!events.length){ const el=document.createElement("div"); el.className="body-desc"; el.textContent="无近期活动"; els.timeline.append(el); return; } events.slice(0,8).forEach(ev=>{ const el=document.createElement("div"); el.className="timeline-item"; const icon=document.createElement("div"); icon.className="timeline-icon"; icon.textContent=EVENT_ICONS[ev.event_type]||"●"; const content=document.createElement("div"); content.className="timeline-content"; const text=document.createElement("div"); text.className="timeline-text"; text.textContent=ev.summary||ev.event_type||"活动"; const time=document.createElement("div"); time.className="timeline-time"; if(ev.recorded_at){ const d=new Date(ev.recorded_at); time.textContent=d.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"}); } content.append(text,time); el.append(icon,content); els.timeline.append(el); }); }
function renderStatusOrbs(state) { els.statusOrbs.replaceChildren(); const m=state.metrics||{}; [{color:"green",active:true,title:"系统"},{color:"blue",active:m.running_count>0,title:"运行中"},{color:"yellow",active:m.error_count>0,title:"错误"},{color:"purple",active:m.drive_candidates>0,title:"候选"}].forEach(o=>{ const el=document.createElement("div"); el.className=`orb ${o.color} ${o.active?"active":""}`; el.title=o.title; els.statusOrbs.append(el); }); }
function applyState(state) { const scene=state.scene||"idle"; els.body.dataset.scene=scene; els.glyph.textContent=GLYPHS[scene]||"?"; els.sceneIcon.textContent=SCENE_ICONS[scene]||"?"; els.sceneIcon.className=`scene-icon ${scene}`; els.sceneTitle.textContent=SCENE_LABELS[scene]||"监督室"; els.sceneSummary.textContent=SCENE_DESCRIPTIONS[scene]||""; Object.keys(els.scenes).forEach(k=>els.scenes[k].classList.toggle("active",SCENE_ROOMS[scene]===k)); renderMetrics(state); renderBodyStatus(state.body_status||{}); renderSchedule(state.schedule||{}); renderCandidates(state.drive_candidates||[]); renderTasks(state.tasks||[]); renderExecutions(state.active_executions||[]); renderTimeline(state.timeline||[]); renderStatusOrbs(state); }
async function refresh() { try{ const resp=await fetch("/ui/state",{cache:"no-store"}); applyState(await resp.json()); }catch(e){ els.body.dataset.scene="idle"; els.sceneTitle.textContent="等待监督者"; els.sceneSummary.textContent="连接暂不可用"; els.glyph.textContent="🌙"; } }
let fallbackTimer=null;
function startFallback(){ if(fallbackTimer)return; refresh(); fallbackTimer=setInterval(refresh,4000); }
if("EventSource"in window){ const es=new EventSource("/ui/events"); es.addEventListener("state",ev=>{ if(fallbackTimer){clearInterval(fallbackTimer);fallbackTimer=null;} applyState(JSON.parse(ev.data)); }); es.onerror=startFallback; }else{ startFallback(); }
initToggles();
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
