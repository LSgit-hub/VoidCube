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
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>VoidCube Supervisor Room</title>
<style>
/*━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  VoidCube Supervisor Room  v2
  A living observability space for the mother-system's heartbeat.
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━*/
:root {
  color-scheme: dark;
  --ink: #1e2c3a;
  --paper: #fef9ed;
  --wall: #e6d7b8;
  --wall-dark: #d4c4a2;
  --floor: #b07752;
  --floor-dark: #8b5a3c;
  --trim: #3d5d6b;
  --mint: #7cc9a0;
  --mint-glow: rgba(124,201,160,.55);
  --coral: #e07362;
  --coral-glow: rgba(224,115,98,.5);
  --gold: #e2b04a;
  --gold-glow: rgba(226,176,74,.55);
  --blue: #5c8db8;
  --blue-glow: rgba(92,141,184,.4);
  --shadow: rgba(20,36,52,.28);
  --lamp: rgba(255,220,150,.7);
  --transition-speed: .5s;
}

* { box-sizing:border-box; }

body {
  margin:0; min-height:100vh; overflow:hidden;
  font-family:"Inter","Segoe UI",system-ui,sans-serif;
  background:#1e2c36;
  color:var(--ink);
  transition:background .8s ease;
}

/* ── Room shell ── */
.room {
  position:relative; min-height:100vh;
  display:grid;
  grid-template-columns:minmax(200px,26vw) 1fr minmax(200px,26vw);
  grid-template-rows:1fr 28vh;
  background:
    /* ceiling gradient */
    linear-gradient(180deg,rgba(255,255,255,.5) 0,rgba(255,255,255,0) 38%),
    /* wallpaper stripes */
    repeating-linear-gradient(90deg,rgba(61,93,107,.08) 0 1px,transparent 1px 64px),
    /* wall base */
    linear-gradient(175deg,var(--wall),var(--wall-dark));
  transition:background .6s ease;
}

/* ── Floor ── */
.room::after {
  content:""; position:absolute; left:0;right:0;bottom:0; height:36vh; z-index:0;
  background:
    /* floor planks */
    repeating-linear-gradient(90deg,rgba(60,30,18,.3) 0 2px,transparent 2px 88px),
    repeating-linear-gradient(0deg,rgba(0,0,0,.06) 0 1px,transparent 1px 28px),
    linear-gradient(170deg,var(--floor),var(--floor-dark));
  clip-path:polygon(0 22%,100% 4%,100% 100%,0 100%);
}

/* ── Ceiling lamp glow ── */
.lamp-glow {
  position:absolute; left:50%; top:0; transform:translateX(-50%);
  width:min(48vw,480px); height:18vh;
  background:radial-gradient(ellipse at 50% 0,rgba(255,235,180,.18),transparent 72%);
  z-index:0; pointer-events:none;
  transition:opacity .6s ease;
}

/* ── Ambient particles container ── */
.particles {
  position:absolute; inset:0; z-index:0; pointer-events:none; overflow:hidden;
}
.particle {
  position:absolute; border-radius:50%; pointer-events:none;
  animation:drift linear infinite;
}
.particle.dust {
  width:2px;height:2px; background:rgba(255,248,220,.6);
  animation-duration:8s;
}
.particle.spark {
  width:3px;height:3px; background:var(--gold); opacity:0;
  box-shadow:0 0 6px var(--gold-glow);
}
.particle.data {
  width:2px;height:4px; border-radius:1px;
  background:var(--mint); opacity:0;
  animation-duration:3s;
}

@keyframes drift {
  0% { transform:translateY(0) translateX(0); opacity:0; }
  10% { opacity:1; }
  90% { opacity:.3; }
  100% { transform:translateY(-60vh) translateX(30px); opacity:0; }
}

/* ── Window ── */
.window {
  grid-column:3; grid-row:1;
  align-self:start; justify-self:start;
  width:min(76%,270px); height:170px;
  margin:8vh 0 0 4vw;
  border:10px solid #efe5cf;
  border-radius:10px;
  position:relative; z-index:1;
  background:linear-gradient(#8cc4e0,#c8e0ee 54%,#85bf8e 55%);
  box-shadow:0 18px 34px var(--shadow),inset 0 0 40px rgba(255,255,255,.2);
  transition:background .8s ease;
  overflow:hidden;
}
.window::before,.window::after {
  content:""; position:absolute; background:#efe5cf; z-index:2;
}
.window::before { left:50%;top:0;bottom:0;width:8px;transform:translateX(-50%); }
.window::after { left:0;right:0;top:50%;height:8px;transform:translateY(-50%); }

/* sun / moon */
.window-frame {
  position:absolute; z-index:1;
  border-radius:50%; transition:all 1.2s ease;
}
.sun {
  right:18%; top:16%;
  width:38px;height:38px;
  background:radial-gradient(circle,#fff8c4,#f7d86c);
  box-shadow:0 0 32px rgba(255,220,100,.6);
}
.moon {
  right:18%; top:16%;
  width:28px;height:28px;
  background:radial-gradient(circle at 38% 38%,#f0f4f8,#c8d6e0);
  box-shadow:0 0 18px rgba(200,210,230,.5);
  display:none;
}
.star {
  position:absolute; border-radius:50%; background:#fff;
  opacity:0; transition:opacity 1s ease;
}

/* ── Bookshelf ── */
.shelf {
  grid-column:1; grid-row:1/3;
  align-self:end; justify-self:center;
  width:min(76%,290px); height:44vh;
  margin-bottom:19vh;
  border:11px solid #5c3d2a;
  border-radius:8px;
  background:linear-gradient(#8b6345,#73502f);
  box-shadow:0 22px 36px var(--shadow);
  display:grid;
  grid-template-rows:repeat(4,1fr);
  padding:14px; gap:14px;
  position:relative; z-index:1;
  transition:box-shadow .5s ease;
}
.shelf-row {
  border-bottom:9px solid #5c3d2a;
  display:flex; align-items:end; gap:8px;
}
.book {
  width:20px; border-radius:3px 3px 0 0;
  box-shadow:inset -5px 0 rgba(255,255,255,.16);
  transition:height .4s ease,background .4s ease,box-shadow .5s ease;
}
.book:nth-child(3n)   { height:58%; background:var(--blue); }
.book:nth-child(3n+1) { height:78%; background:var(--coral); }
.book:nth-child(3n+2) { height:68%; background:var(--gold); }

/* bookshelf glow per scene */
body[data-scene="memory"]   .shelf { box-shadow:0 22px 36px var(--shadow),0 0 40px var(--gold-glow); }
body[data-scene="learning"] .shelf { box-shadow:0 22px 36px var(--shadow),0 0 40px var(--mint-glow); }

/* ── Desk + lamp ── */
.desk {
  grid-column:2; grid-row:2;
  align-self:center; justify-self:center;
  width:min(62vw,520px); height:108px;
  border-radius:10px;
  background:linear-gradient(#704a35,#553320);
  box-shadow:0 24px 30px var(--shadow);
  position:relative; z-index:1;
}
.desk::before {
  content:""; position:absolute;
  left:6%;right:6%; top:-20px; height:30px;
  border-radius:10px;
  background:#8c5f44;
}
.desk-lamp {
  position:absolute; left:12%; top:-76px;
  width:52px; height:66px;
  background:radial-gradient(ellipse at 50% 32%,#fce9b0,#d4954a);
  border-radius:50% 50% 30% 30%;
  box-shadow:0 0 38px var(--lamp);
  z-index:2; transition:box-shadow .5s ease;
}
.desk-lamp::after {
  content:""; position:absolute;
  left:50%; bottom:-18px; width:8px; height:28px;
  transform:translateX(-50%);
  background:#3d2818; border-radius:2px;
}

/* lamp papers */
.papers {
  position:absolute; left:30%;right:12%; top:-32px; bottom:8px;
  z-index:1; display:flex; gap:12px;
}
.paper {
  flex:1; border-radius:4px;
  background:var(--paper);
  border:1px solid rgba(0,0,0,.08);
  transform:rotate(var(--r,0deg));
  transition:transform .4s ease,background .4s ease;
  opacity:.85;
}

/* ── Console terminal ── */
.console {
  grid-column:2; grid-row:2;
  align-self:start; justify-self:end;
  width:156px; height:96px;
  margin-right:10vw; margin-top:-22px;
  border:8px solid #2d404c;
  border-radius:8px;
  background:
    repeating-linear-gradient(0deg,rgba(255,255,255,.06) 0 2px,transparent 2px 18px),
    linear-gradient(#538198,#34505e);
  box-shadow:0 16px 22px var(--shadow);
  position:relative; z-index:1;
  overflow:hidden;
}
.console::after {
  content:""; position:absolute;
  left:50%; bottom:-30px;
  width:56px; height:22px;
  transform:translateX(-50%);
  border-radius:4px; background:#2d404c;
}
.console-line {
  position:absolute; left:6px; height:2px; border-radius:1px;
  background:rgba(180,220,240,.6);
  animation:console-scroll 2.4s linear infinite;
}
.console-line:nth-child(1) { top:12px; width:72%; animation-delay:0s; }
.console-line:nth-child(2) { top:26px; width:48%; animation-delay:.3s; }
.console-line:nth-child(3) { top:40px; width:84%; animation-delay:.6s; }
.console-line:nth-child(4) { top:54px; width:36%; animation-delay:.9s; }
.console-line:nth-child(5) { top:68px; width:64%; animation-delay:1.2s; }
@keyframes console-scroll {
  0% { transform:translateY(0); opacity:.9; }
  80% { opacity:.5; }
  100% { transform:translateY(-72px); opacity:0; }
}

/* ── Character: 兮子 ── */
.xizi {
  grid-column:2; grid-row:1/3;
  align-self:end; justify-self:center;
  width:180px; height:280px;
  margin-bottom:14vh;
  position:relative; z-index:2;
  transition:transform var(--transition-speed) cubic-bezier(.4,0,.2,1);
}
/* character parts */
.xz-head {
  position:absolute; left:44px; top:14px;
  width:88px; height:82px;
  border-radius:44% 44% 40% 42%;
  background:linear-gradient(155deg,#ffe4c0,#f0cfa0);
  box-shadow:inset -9px -10px rgba(190,120,90,.22);
  z-index:4; transition:transform .4s ease;
}
.xz-hair {
  position:absolute; left:36px; top:2px;
  width:106px; height:68px;
  border-radius:46px 46px 20px 20px;
  background:#242d38;
  z-index:5;
  clip-path:polygon(0 0,100% 0,93% 72%,72% 48%,56% 78%,38% 50%,22% 80%,8% 54%);
}
.xz-eye {
  position:absolute; top:48px; width:10px; height:16px;
  border-radius:50%; background:#1e2835; z-index:6;
  transition:height .15s ease;
}
.xz-eye.l { left:70px; }
.xz-eye.r { left:100px; }
.xz-brow {
  position:absolute; top:43px; width:14px; height:3px;
  border-radius:2px; background:#4a3a30; z-index:6;
  transition:transform .3s ease;
}
.xz-brow.l { left:68px; }
.xz-brow.r { left:98px; }
.xz-mouth {
  position:absolute; left:83px; top:76px;
  width:18px; height:8px;
  border-bottom:3px solid #a06858;
  border-radius:50%; z-index:6;
  transition:all .4s ease;
}
.xz-body {
  position:absolute; left:51px; top:92px;
  width:74px; height:100px;
  border-radius:22px 22px 18px 18px;
  background:linear-gradient(140deg,var(--mint),#45997e);
  box-shadow:inset -10px -10px rgba(20,70,60,.16);
  z-index:3; transition:background .5s ease;
}
.xz-arm {
  position:absolute; top:114px;
  width:26px; height:76px;
  border-radius:15px;
  background:linear-gradient(90deg,#ffe4c0,#f0cfa0);
  transform-origin:top center; z-index:3;
  transition:transform .5s ease;
}
.xz-arm.l { left:35px; transform:rotate(12deg); }
.xz-arm.r { left:115px; transform:rotate(-14deg); }
.xz-leg {
  position:absolute; top:184px;
  width:30px; height:78px;
  border-radius:14px;
  background:#314453; z-index:2;
}
.xz-leg.l { left:58px; }
.xz-leg.r { left:96px; }
.xz-prop {
  position:absolute; left:126px; top:146px;
  width:50px; height:38px;
  border-radius:5px;
  background:var(--paper);
  border:4px solid #6b4a34;
  transform:rotate(-8deg);
  z-index:7;
  transition:all .5s ease;
}
/* character idle animation */
.xizi { animation:xz-breathe 3s ease-in-out infinite; }
@keyframes xz-breathe {
  0%,100% { margin-bottom:14vh; }
  50% { margin-bottom:calc(14vh + 6px); }
}
@keyframes xz-blink {
  0%,94%,100% { transform:scaleY(1); }
  96%,98% { transform:scaleY(.08); }
}
.xz-eye { animation:xz-blink 5s infinite; }

/* ── Thought bubbles ── */
.thoughts {
  grid-column:2; grid-row:1;
  align-self:center; justify-self:center;
  transform:translate(120px,-48px);
  width:120px; height:80px;
  position:relative; z-index:3;
  opacity:.85; transition:opacity .5s ease;
}
.bubble {
  position:absolute; border-radius:50%;
  background:rgba(255,250,240,.9);
  border:3px solid rgba(45,60,75,.3);
  box-shadow:0 8px 18px rgba(20,36,52,.14);
  animation:bob 2.6s ease-in-out infinite;
  transition:all .5s ease;
}
.bubble.b1 { width:76px;height:48px; left:18px; top:0; }
.bubble.b2 { width:20px;height:20px; left:4px; top:44px; animation-delay:.25s; }
.bubble.b3 { width:12px;height:12px; left:0; top:64px; animation-delay:.5s; }
@keyframes bob {
  0%,100% { transform:translateY(0); }
  50% { transform:translateY(-8px); }
}
.glyph {
  position:absolute; left:44px; top:6px;
  font-size:30px; font-weight:800;
  color:var(--trim);
  animation:glyph-pulse 1.8s ease-in-out infinite;
  transition:color .4s ease;
}
@keyframes glyph-pulse {
  0%,100% { transform:scale(1); opacity:.75; }
  50% { transform:scale(1.22); opacity:1; }
}

/* ── Status dashboard ── */
.status {
  grid-column:3; grid-row:2;
  align-self:end; justify-self:center;
  width:min(86%,370px); margin-bottom:3vh;
  padding:18px 18px 14px;
  border:2px solid rgba(30,44,58,.22);
  border-radius:12px;
  background:rgba(255,250,240,.88);
  box-shadow:0 18px 32px var(--shadow);
  backdrop-filter:blur(8px);
  position:relative; z-index:3;
  transition:border-color .4s ease;
}
.status h1 {
  margin:0 0 4px; font-size:17px; line-height:1.25;
  font-weight:650; letter-spacing:-.01em;
}
.status-summary {
  margin:0 0 14px; color:#4a5a6a; font-size:12.5px; line-height:1.5;
}
/* metrics row */
.metrics {
  display:flex; gap:10px; margin-bottom:14px; flex-wrap:wrap;
}
.metric {
  flex:1; min-width:60px; text-align:center;
  padding:8px 6px; border-radius:10px;
  background:rgba(255,255,255,.55);
  border:1px solid rgba(30,44,58,.1);
  transition:all .4s ease;
}
.metric-value {
  font-size:22px; font-weight:700; line-height:1.1;
  font-variant-numeric:tabular-nums;
}
.metric-label {
  font-size:10px; color:#5a6b7a; margin-top:2px;
  text-transform:uppercase; letter-spacing:.04em;
}
.metric.error .metric-value { color:var(--coral); }
.metric.ok .metric-value { color:var(--mint); }
/* schedule countdown */
.schedule { text-align:center; margin-bottom:12px; padding:6px 10px;
  border-radius:8px; background:rgba(61,93,107,.08); }
.schedule-label { font-size:10px; color:#5a6b7a; text-transform:uppercase; letter-spacing:.04em; margin-bottom:2px; }
.schedule-countdown { font-size:18px; font-weight:700; color:var(--trim);
  font-variant-numeric:tabular-nums; }
/* queue */
.queue { display:grid; gap:7px; margin-bottom:12px; }
.task {
  display:grid; grid-template-columns:10px 1fr auto; align-items:center;
  gap:8px; min-height:32px; padding:6px 10px;
  border:1px solid rgba(30,44,58,.12); border-radius:8px;
  background:rgba(255,255,255,.5); font-size:11.5px;
  transition:all .35s ease;
}
.task-dot {
  width:10px; height:10px; border-radius:50%;
  transition:background .4s ease;
}
.task-dot.memory   { background:var(--gold); box-shadow:0 0 6px var(--gold-glow); }
.task-dot.learning { background:var(--mint); box-shadow:0 0 6px var(--mint-glow); }
.task-dot.evolution{ background:var(--coral); box-shadow:0 0 6px var(--coral-glow); }
.task-dot.planning { background:var(--blue); box-shadow:0 0 6px var(--blue-glow); }
.task-badge {
  min-width:56px; text-align:center; padding:3px 8px;
  border-radius:99px; font-size:10.5px;
  background:rgba(61,93,107,.1); color:#3a5260;
}
/* timeline */
.timeline { display:grid; gap:5px; max-height:120px; overflow-y:auto; }
.event {
  display:grid; grid-template-columns:18px 58px 1fr; gap:7px;
  align-items:start; min-height:26px; padding:5px 8px;
  border-left:3px solid rgba(61,93,107,.35);
  background:rgba(255,255,255,.32); border-radius:5px;
  font-size:10.5px; animation:event-in .35s ease;
}
@keyframes event-in {
  from { opacity:0; transform:translateX(6px); }
  to { opacity:1; transform:translateX(0); }
}
.event-icon {
  font-size:12px; text-align:center; line-height:1.4;
}
.event-time {
  color:#5d6e7e; white-space:nowrap;
  font-variant-numeric:tabular-nums; font-size:10px;
}
.event-text { color:#293b4a; line-height:1.4; }

/* ── Scene states ── */
/* idle: calm breathing, dim lamp, gentle window */
body[data-scene="idle"] .desk-lamp { box-shadow:0 0 22px var(--lamp); }
body[data-scene="idle"] .thoughts { opacity:.65; }
body[data-scene="idle"] .glyph { color:var(--trim); }

/* memory: character near shelf, gold theme, books highlighted */
body[data-scene="memory"] .xizi { transform:translateX(-19vw); }
body[data-scene="memory"] .xz-body { background:linear-gradient(140deg,#d4af6a,#b08830); }
body[data-scene="memory"] .xz-prop { transform:rotate(6deg) scale(1.12); background:#f4dc82; }
body[data-scene="memory"] .xz-arm.r { animation:arm-reach .9s ease-in-out infinite; }
body[data-scene="memory"] .glyph { color:var(--gold); }
body[data-scene="memory"] .status { border-color:rgba(226,176,74,.35); }
body[data-scene="memory"] .bubble { background:rgba(255,245,210,.92); }

/* learning: character right side, mint glow, card flipping */
body[data-scene="learning"] .xizi { transform:translateX(15vw); }
body[data-scene="learning"] .xz-body { background:linear-gradient(140deg,#5cc497,#3a8e6e); }
body[data-scene="learning"] .xz-prop { background:#c0efd4; animation:card-flip 1.6s ease-in-out infinite; }
body[data-scene="learning"] .glyph { color:var(--mint); }
body[data-scene="learning"] .status { border-color:rgba(124,201,160,.35); }
body[data-scene="learning"] .bubble { background:rgba(225,250,238,.92); }
@keyframes card-flip {
  0%,100% { transform:rotate(-6deg) scale(1); }
  50% { transform:rotate(10deg) scale(1.1); box-shadow:0 0 24px var(--mint-glow); }
}

/* planning: centered, leaning forward, coral accent */
body[data-scene="planning"] .xz-head { transform:translateY(-4px); }
body[data-scene="planning"] .glyph { color:var(--coral); animation-duration:1.2s; }
body[data-scene="planning"] .xz-arm.l { animation:arm-think .8s ease-in-out infinite; }
body[data-scene="planning"] .bubble { animation-duration:1.8s; }
body[data-scene="planning"] .status { border-color:rgba(224,115,98,.3); }
@keyframes arm-think {
  0%,100% { transform:rotate(12deg) translateY(0); }
  50% { transform:rotate(28deg) translateY(-8px); }
}

/* execution: right-forward, typing, console active, coral glow */
body[data-scene="execution"] .xizi { transform:translateX(10vw) translateY(6px); }
body[data-scene="execution"] .xz-body { background:linear-gradient(140deg,var(--coral),#c55a48); }
body[data-scene="execution"] .xz-arm.l { animation:arm-type .5s ease-in-out infinite; }
body[data-scene="execution"] .xz-arm.r { animation:arm-type .5s ease-in-out .25s infinite; }
body[data-scene="execution"] .glyph { color:var(--coral); }
body[data-scene="execution"] .status { border-color:rgba(224,115,98,.4); }
body[data-scene="execution"] .console { box-shadow:0 16px 22px var(--shadow),0 0 28px var(--coral-glow); }
body[data-scene="execution"] .bubble { opacity:.55; }
@keyframes arm-type {
  0%,100% { transform:rotate(12deg) translateY(0); }
  50% { transform:rotate(30deg) translateY(10px); }
}
@keyframes arm-reach {
  0%,100% { transform:rotate(-14deg); }
  50% { transform:rotate(-44deg) translateY(-10px); }
}

/* ── Error state (overlay on any scene) ── */
body[data-has-errors="true"] .window { background:linear-gradient(#a890a0,#c8b8c8 50%,#9a8898 51%); }
body[data-has-errors="true"] .xz-mouth { border-bottom-color:#b84040; width:14px; }
body[data-has-errors="true"] .xz-brow.l { transform:rotate(-8deg) translateY(-2px); }
body[data-has-errors="true"] .xz-brow.r { transform:rotate(8deg) translateY(-2px); }
body[data-has-errors="true"] .desk-lamp { box-shadow:0 0 32px rgba(255,180,150,.7); }

/* ── Execution window (night mode) ── */
body[data-exec-window="false"] .sun { display:none; }
body[data-exec-window="false"] .moon { display:block; }
body[data-exec-window="false"] .window { background:linear-gradient(#1e3050,#2d4470 48%,#1a2e38 49%); }
body[data-exec-window="false"] .star { opacity:1; }

@keyframes twinkle {
  0%,100% { opacity:.4; }
  50% { opacity:1; }
}

/* ── Responsive ── */
@media (max-width:820px) {
  .room { grid-template-columns:1fr; grid-template-rows:22vh 42vh 36vh; }
  .window { grid-column:1; grid-row:1; width:160px; height:110px; margin:4vh 6vw 0 0; }
  .shelf { grid-column:1; grid-row:2; align-self:end; justify-self:start;
    width:150px; height:220px; margin:0 0 16vh 4vw; }
  .xizi { grid-column:1; grid-row:2/4; transform:scale(.8); margin-bottom:18vh; }
  body[data-scene="memory"] .xizi { transform:translateX(-14vw) scale(.8); }
  body[data-scene="learning"] .xizi,
  body[data-scene="execution"] .xizi { transform:translateX(14vw) scale(.8); }
  .desk { grid-column:1; grid-row:3; width:88vw; height:84px; }
  .console { grid-column:1; grid-row:3; width:118px; height:76px; margin-right:8vw; }
  .thoughts { grid-column:1; grid-row:2; transform:translate(80px,-10px); }
  .status { grid-column:1; grid-row:3; width:92vw; margin-bottom:2vh; }
  .desk-lamp { left:8%; top:-58px; width:38px; height:50px; }
  .papers { left:22%; }
}

@media (max-width:480px) {
  .shelf { display:none; }
  .window { width:120px; height:90px; }
  .console { display:none; }
  .desk-lamp { display:none; }
  .papers { display:none; }
  .thoughts { transform:translate(60px,-20px) scale(.8); }
}
</style>
</head>
<body data-scene="idle" data-has-errors="false" data-exec-window="true">
<main class="room" aria-label="VoidCube supervisor room">

  <!-- ambient particles -->
  <div class="particles" id="particles" aria-hidden="true"></div>

  <!-- ceiling lamp -->
  <div class="lamp-glow" aria-hidden="true"></div>

  <!-- bookshelf -->
  <section class="shelf" aria-hidden="true">
    <div class="shelf-row"><span class="book"></span><span class="book"></span><span class="book"></span><span class="book"></span><span class="book"></span></div>
    <div class="shelf-row"><span class="book"></span><span class="book"></span><span class="book"></span><span class="book"></span><span class="book"></span></div>
    <div class="shelf-row"><span class="book"></span><span class="book"></span><span class="book"></span><span class="book"></span><span class="book"></span></div>
    <div class="shelf-row"><span class="book"></span><span class="book"></span><span class="book"></span><span class="book"></span><span class="book"></span></div>
  </section>

  <!-- window with day/night -->
  <div class="window" aria-hidden="true">
    <div class="window-frame sun"></div>
    <div class="window-frame moon"></div>
    <span class="star" style="left:22%;top:18%;width:2px;height:2px;animation:twinkle 2.1s infinite;"></span>
    <span class="star" style="left:48%;top:10%;width:3px;height:3px;animation:twinkle 3.4s infinite .5s;"></span>
    <span class="star" style="left:68%;top:24%;width:2px;height:2px;animation:twinkle 2.7s infinite 1.2s;"></span>
    <span class="star" style="left:34%;top:32%;width:2px;height:2px;animation:twinkle 1.9s infinite .8s;"></span>
  </div>

  <!-- thought bubbles -->
  <div class="thoughts" aria-hidden="true">
    <span class="bubble b1"></span>
    <span class="bubble b2"></span>
    <span class="bubble b3"></span>
    <span class="glyph" id="glyph">?</span>
  </div>

  <!-- 兮子 character -->
  <section class="xizi" aria-hidden="true">
    <div class="xz-hair"></div>
    <div class="xz-head"></div>
    <div class="xz-brow l"></div><div class="xz-brow r"></div>
    <div class="xz-eye l"></div><div class="xz-eye r"></div>
    <div class="xz-mouth"></div>
    <div class="xz-body"></div>
    <div class="xz-arm l"></div><div class="xz-arm r"></div>
    <div class="xz-leg l"></div><div class="xz-leg r"></div>
    <div class="xz-prop"></div>
  </section>

  <!-- desk + lamp + papers -->
  <div class="desk" aria-hidden="true">
    <div class="desk-lamp"></div>
    <div class="papers">
      <span class="paper" style="--r:-5deg"></span>
      <span class="paper" style="--r:3deg"></span>
      <span class="paper" style="--r:-2deg"></span>
    </div>
  </div>

  <!-- console terminal -->
  <div class="console" aria-hidden="true">
    <span class="console-line"></span><span class="console-line"></span>
    <span class="console-line"></span><span class="console-line"></span>
    <span class="console-line"></span>
  </div>

  <!-- status dashboard -->
  <aside class="status" aria-live="polite">
    <h1 id="sceneTitle">Waking supervisor room</h1>
    <p class="status-summary" id="sceneSummary">Connecting to VoidCube supervisor…</p>
    <div class="metrics" id="metrics"></div>
    <div class="schedule" id="schedule" style="display:none;">
      <div class="schedule-label">⏳ next auto-cycle</div>
      <div class="schedule-countdown" id="countdown">—</div>
    </div>
    <div class="queue" id="queue"></div>
    <div class="executions" id="executions" style="display:none;">
      <div class="exec-label">⚡ 执行中</div>
      <div id="execList"></div>
    </div>
    <div class="timeline" id="timeline"></div>
  </aside>

</main>
<script>
/*━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  VoidCube Supervisor Room  v2  — JS runtime
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━*/
const $ = (sel, el) => (el||document).querySelector(sel);
const $$ = (sel, el) => [...(el||document).querySelectorAll(sel)];

/* ── DOM refs ── */
const els = {
  body: document.body,
  title: document.getElementById("sceneTitle"),
  summary: document.getElementById("sceneSummary"),
  glyph: document.getElementById("glyph"),
  queue: document.getElementById("queue"),
  executions: document.getElementById("executions"),
  execList: document.getElementById("execList"),
  timeline: document.getElementById("timeline"),
  metrics: document.getElementById("metrics"),
  particles: document.getElementById("particles"),
};

/* ── Glyphs per scene ── */
const GLYPHS = {
  idle:"?", planning:"!", memory:"¶", learning:"λ", execution:"⟩"
};

/* ── Icons per event type ── */
const EVENT_ICONS = {
  endogenous_drive_evaluated:"🧠", endogenous_drive_planned:"💡",
  endogenous_drive_idle:"😴", task_planned:"📋", task_decided:"⚖️",
  tasks_reviewed:"🔍", tasks_planned:"📝", execution_dispatched:"🚀",
  self_learning_submitted:"📖", self_learning_completed:"✅",
  memory_compression:"💾", task_decision:"⚖️",
};
function eventIcon(type) {
  return EVENT_ICONS[type] || "●";
}

/* ── Task dot class ── */
function taskDotClass(task) {
  var f = String(task.task_family||task.governance_task_type||"");
  if (f.includes("memory")) return "memory";
  if (f.includes("learning")) return "learning";
  if (f.includes("evolution")||f.includes("body")) return "evolution";
  return "planning";
}

/* ── Render queue ── */
function renderQueue(tasks) {
  els.queue.replaceChildren();
  (tasks||[]).slice(0,5).forEach(function(t) {
    var row = document.createElement("div");
    row.className = "task";
    var dot = document.createElement("span");
    dot.className = "task-dot " + taskDotClass(t);
    var title = document.createElement("span");
    title.textContent = t.title||"Untitled";
    var badge = document.createElement("span");
    badge.className = "task-badge";
    badge.textContent = t.status||t.priority||"queued";
    row.append(dot,title,badge);
    els.queue.append(row);
  });
}

/* ── Render timeline ── */
function renderTimeline(events) {
  els.timeline.replaceChildren();
  (events||[]).slice(0,6).forEach(function(ev) {
    var row = document.createElement("div");
    row.className = "event";
    var icon = document.createElement("span");
    icon.className = "event-icon";
    icon.textContent = eventIcon(ev.event_type||"");
    var time = document.createElement("span");
    time.className = "event-time";
    var d = ev.recorded_at ? new Date(ev.recorded_at) : null;
    time.textContent = d&&!isNaN(d.getTime())
      ? d.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit",second:"2-digit"})
      : "--:--:--";
    var text = document.createElement("span");
    text.className = "event-text";
    text.textContent = ev.summary||ev.event_type||"Activity";
    row.append(icon,time,text);
    els.timeline.append(row);
  });
}

/* ── Render active executions ── */
function renderExecutions(tasks) {
  els.execList.replaceChildren();
  if (!tasks || !tasks.length) {
    els.executions.style.display = "none";
    return;
  }
  els.executions.style.display = "block";
  tasks.slice(0,3).forEach(function(t) {
    var row = document.createElement("div");
    row.className = "exec-item";
    var dot = document.createElement("span");
    dot.className = "task-dot " + taskDotClass(t);
    var title = document.createElement("span");
    title.textContent = (t.title||"Untitled").substring(0,40);
    var type = document.createElement("span");
    type.className = "exec-type";
    type.textContent = (t.governance_task_type||t.task_family||"").replace(/_/g," ");
    row.append(dot,title,type);
    els.execList.append(row);
  });
}

/* ── Render metrics ── */
function renderMetrics(state) {
  els.metrics.replaceChildren();
  var tasks = state.tasks||[];
  var queueCount = tasks.length;
  var approved = tasks.filter(function(t){return t.status==="approved";}).length;
  var candidates = (state.drive_candidates||[]).length;
  var errors = state.error_count||0;
  var inWindow = state.in_execution_window !== false;

  function addMetric(cls,value,label) {
    var m = document.createElement("div");
    m.className = "metric "+cls;
    var v = document.createElement("div");
    v.className = "metric-value";
    v.textContent = value;
    var l = document.createElement("div");
    l.className = "metric-label";
    l.textContent = label;
    m.append(v,l);
    els.metrics.append(m);
  }
  addMetric("ok",queueCount,"Queued");
  addMetric(approved>0?"ok":"","",approved>0?approved:"—","Approved");
  addMetric(errors>0?"error":"ok",errors>0?errors:"—","Errors");
	  addMetric(inWindow?"ok":"",inWindow?"open":"closed","Exec Win");
}

/* ── Schedule countdown ── */
var _countdownTimer = null;
var _nextReviewAt = null;

function formatCountdown(seconds) {
  if (seconds <= 0) return "due now";
  var m = Math.floor(seconds / 60);
  var s = Math.floor(seconds % 60);
  if (m > 0) return m + "m " + (s < 10 ? "0" : "") + s + "s";
  return s + "s";
}

function renderSchedule(schedule) {
  var el = document.getElementById("schedule");
  var cdEl = document.getElementById("countdown");
  if (!el || !cdEl) return;

  var nextAt = schedule.next_review_at || schedule.next_drive_at;

  if (!nextAt) {
    el.style.display = "none";
    _nextReviewAt = null;
    return;
  }

  _nextReviewAt = nextAt;
  el.style.display = "block";

  function tick() {
    if (!_nextReviewAt) { cdEl.textContent = "—"; return; }
    var d = new Date(_nextReviewAt);
    if (isNaN(d.getTime())) { cdEl.textContent = "—"; return; }
    var remaining = Math.max(0, (d.getTime() - Date.now()) / 1000);
    cdEl.textContent = formatCountdown(remaining);
    if (remaining <= 10) {
      cdEl.style.color = "var(--coral)";
    } else {
      cdEl.style.color = "";
    }
  }
  tick();
  if (_countdownTimer) clearInterval(_countdownTimer);
  _countdownTimer = setInterval(tick, 1000);
}

/* ── Apply full state ── */
function applyState(state) {
  var scene = state.scene||"idle";
  var prevScene = els.body.dataset.scene;
  els.body.dataset.scene = scene;
  els.glyph.textContent = GLYPHS[scene]||"?";
  els.title.textContent = state.title||"Supervisor room";
  els.summary.textContent = state.summary||"";

  /* execution window indicator */
  var timeline = state.timeline||[];
  els.body.dataset.execWindow = "true"; /* default; server could send this */

  /* error indicator — from gateway error_count */
  var hasErrors = (state.error_count||0) > 0;
  els.body.dataset.hasErrors = hasErrors?"true":"false";

  /* execution window */
  els.body.dataset.execWindow = state.in_execution_window !== false ? "true" : "false";

  renderQueue(state.tasks||[]);
  renderExecutions(state.active_executions||[]);
  renderTimeline(timeline);
  renderMetrics(state);
  if (state.schedule) renderSchedule(state.schedule);

  /* scene transition: briefly flash particles */
  if (scene !== prevScene) {
    spawnParticles(scene, 12);
  }
}

/* ── Ambient particles ── */
var particleTimer = null;
function spawnParticles(scene, count) {
  var colors = {memory:"#e2b04a",learning:"#7cc9a0",planning:"#e07362",execution:"#e07362",idle:"rgba(255,248,220,.6)"};
  var color = colors[scene]||"rgba(255,248,220,.5)";
  for (var i=0;i<(count||6);i++) {
    var p = document.createElement("span");
    p.className = "particle " + (scene==="execution"?"spark":"dust");
    p.style.left = (20+Math.random()*60)+"%";
    p.style.bottom = (10+Math.random()*30)+"%";
    p.style.animationDuration = (4+Math.random()*6)+"s";
    p.style.animationDelay = Math.random()*2+"s";
    if (scene==="execution") p.style.background = color;
    els.particles.append(p);
    setTimeout(function(){ p.remove(); }, 7000);
  }
}
/* gentle ambient particles on idle */
function ambientParticles() {
  if (els.body.dataset.scene==="execution") spawnParticles("execution",3);
}
particleTimer = setInterval(ambientParticles, 5000);

/* ── State fetching ── */
async function refresh() {
  try {
    var resp = await fetch("/ui/state",{cache:"no-store"});
    applyState(await resp.json());
  } catch(e) {
    els.body.dataset.scene = "idle";
    els.title.textContent = "Supervisor room waiting";
    els.summary.textContent = "State channel not available yet.";
    els.glyph.textContent = "?";
    els.metrics.replaceChildren();
    els.queue.replaceChildren();
    els.timeline.replaceChildren();
  }
}

var fallbackTimer = null;
function startFallback() {
  if (fallbackTimer) return;
  refresh();
  fallbackTimer = setInterval(refresh,4000);
}

if ("EventSource" in window) {
  var es = new EventSource("/ui/events");
  es.addEventListener("state",function(ev) {
    if (fallbackTimer) { clearInterval(fallbackTimer); fallbackTimer=null; }
    applyState(JSON.parse(ev.data));
  });
  es.onerror = function(){ startFallback(); };
} else {
  startFallback();
}

/* initial ambient */
ambientParticles();
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
        idle_snapshot: Dict[str, Any] = {}
        try:
            drive = await self.evaluate_endogenous_drive(
                {"max_candidates": 3, "record_activity": False}
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

        # ── Schedule visibility: expose next-review / next-drive timestamps ──
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

        # ── Supervisor's own LLM token usage (from MemAI pipeline) ──
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

        scene, title, summary = self._map_supervisor_scene(
            tasks=tasks,
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
            "tasks": tasks[:6],
            "schedule": schedule,
            "mem_usage": mem_usage,
            "drive_candidates": drive_candidates[:3],
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
        error_count: int = 0,
        in_execution_window: bool = True,
    ) -> tuple[str, str, str]:
        active = tasks[0] if tasks else None
        error_note = f" · {error_count} recent error(s)" if error_count > 0 else ""

        if active is not None:
            task_family = str(active.get("task_family") or active.get("governance_task_type") or "")
            status = str(active.get("status") or "queued")
            title = str(active.get("title") or "Supervisor task queued")
            status_label = {"planned":"queued","deferred":"waiting","paused":"paused","approved":"ready","running":"running"}.get(status, status)
            if "memory" in task_family:
                return (
                    "memory",
                    f"Xizi is tending the memory shelves{error_note}",
                    f"「{title}」is {status_label}. Long-term continuity is being guarded — memories compressed, lineage preserved.",
                )
            if "learning" in task_family:
                if status == "running":
                    return (
                        "learning",
                        f"Xizi is researching{error_note}",
                        f"「{title}」is {status_label}. Agent is actively executing this learning task.",
                    )
                if status == "approved":
                    return (
                        "planning",
                        f"Xizi has approved learning{error_note}",
                        f"「{title}」is {status_label}. Task awaits agent pull via /v1/tasks; agent body executes learn-only research.",
                    )
                    return (
                        "planning",
                        f"Xizi is reviewing learning proposals{error_note}",
                        f"「{title}」is {status_label}. Assessing whether to approve this learning task.",
                    )
            if "body" in task_family or "evolution" in task_family:
                window_note = " · execution window open" if in_execution_window else " · awaiting execution window"
                if status == "running":
                    return (
                        "execution",
                        f"Xizi is at the console{error_note}{window_note}",
                        f"「{title}」is {status_label}. Body evolution is executing now.",
                    )
                if status == "approved":
                    return (
                        "execution",
                        f"Xizi is at the console{error_note}{window_note}",
                        f"「{title}」is {status_label}. Body evolution follows governance → probe → activate → watch-window → rollback rules.",
                    )
                else:
                    return (
                        "planning",
                        f"Xizi is reviewing evolution proposals{error_note}{window_note}",
                        f"「{title}」is {status_label}. Weighing evidence strength and rollback safety before approval.",
                    )
            return (
                "planning",
                f"Xizi is reviewing the queue{error_note}",
                f"「{title}」is {status_label}. The supervisor weighs idle-window conditions, evidence strength, and rollback safety.",
            )

        if drive_candidates:
            first = drive_candidates[0]
            value_tags = ", ".join(first.get("value_tags") or [])
            utility_pct = int((first.get("utility") or 0) * 100)
            return (
                "planning",
                f"Xizi senses something worth doing{error_note}",
                f"「{first.get('title', 'A candidate task')}」emerged from core values [{value_tags}] with utility {utility_pct}%. Awaiting governance review.",
            )

        if not drive_available:
            return (
                "idle",
                "Xizi gazes out the window",
                "Gateway activity is unreachable. The room shows local supervisor state — endogenous drive will resume when the signal returns.",
            )

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
