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
<title>职场小星成长记</title>
<style>
:root {
  --bg-planning: #eef4ff;
  --bg-learning: #fff6e8;
  --bg-execution: #f0f4fd;
  --bg-memory: #fff8f0;
  --panel-bg: rgba(255,255,255,0.75);
  --panel-border: rgba(168,130,255,0.2);
  --text-primary: #3d2f5c;
  --text-secondary: #7a6a9a;
  --text-muted: #aaa;
  --accent-purple: #8a6cff;
  --accent-blue: #64b5f6;
  --accent-green: #66bb6a;
  --accent-yellow: #ffb74d;
  --accent-red: #ef5350;
}
* { box-sizing:border-box; margin:0; padding:0; }
body { min-height:100vh; overflow:hidden; font-family:"微软雅黑",system-ui,sans-serif; background:var(--bg-planning); color:var(--text-primary); }

@keyframes breatheLight {
  0%,100% { filter: drop-shadow(0 0 8px rgba(255,200,120,0.4)); }
  50% { filter: drop-shadow(0 0 16px rgba(255,200,120,0.7)); }
}
@keyframes bodySway {
  0% { transform: rotate(-2.5deg); }
  50% { transform: rotate(2.5deg); }
  100% { transform: rotate(-2.5deg); }
}
@keyframes flowLight {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(100%); }
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
@keyframes fadeOut { from { opacity: 1; } to { opacity: 0; } }

#gameWrap {
  width: 100%;
  height: 100vh;
  position: relative;
  overflow: hidden;
}

.scene-bg {
  position: absolute;
  width: 100%;
  height: 100%;
  top: 0;
  left: 0;
  opacity: 0;
  transition: opacity 0.5s ease;
  background-size: cover;
  background-position: center;
}
.scene-bg.active { opacity: 1; }

.bg-planning { background: linear-gradient(160deg, #eef4ff 0%, #e1ecff 100%); }
.bg-learning { background: linear-gradient(160deg, #fff6e8 0%, #ffe9cf 100%); }
.bg-execution { background: linear-gradient(160deg, #f0f4fd 0%, #dce6fc 100%); }
.bg-memory { background: linear-gradient(160deg, #fff8f0 0%, #ffeeda 100%); }

.room {
  position: relative;
  z-index: 10;
  display: grid;
  grid-template-columns: 280px 1fr 320px;
  grid-template-rows: 100vh;
  height: 100vh;
}

.sidebar-left, .sidebar-right {
  display: flex;
  flex-direction: column;
  padding: 16px;
  gap: 12px;
  overflow-y: auto;
}

.sidebar-left { grid-column: 1; }
.main-area { 
  grid-column: 2; 
  display: flex; 
  flex-direction: column; 
  padding: 16px; 
  gap: 12px; 
  overflow-y: auto;
}
.sidebar-right { grid-column: 3; }

.card {
  background: var(--panel-bg);
  border: 1px solid var(--panel-border);
  border-radius: 16px;
  padding: 14px;
  backdrop-filter: blur(12px);
  transition: all 0.3s ease;
  box-shadow: 0 4px 12px rgba(0,0,0,0.05);
}
.card:hover { 
  border-color: rgba(168,130,255,0.4); 
  box-shadow: 0 6px 20px rgba(168,130,255,0.1);
}

.card-header { 
  display: flex; 
  align-items: center; 
  justify-content: space-between; 
  margin-bottom: 10px; 
}
.card-title { 
  font-size: 13px; 
  font-weight: 600; 
  color: var(--text-primary); 
  text-transform: uppercase; 
  letter-spacing: 0.06em; 
}
.toggle-btn { 
  width: 24px; 
  height: 24px; 
  border: none; 
  border-radius: 8px; 
  background: rgba(138,108,255,0.1); 
  color: var(--accent-purple); 
  cursor: pointer; 
  display: flex; 
  align-items: center; 
  justify-content: center; 
  transition: all 0.2s ease; 
}
.toggle-btn:hover { 
  background: rgba(138,108,255,0.2); 
  transform: scale(1.1);
}

.character-scene {
  position: relative;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  min-height: 450px;
  border-radius: 20px;
  background: rgba(255,255,255,0.5);
  border: 1px solid rgba(168,130,255,0.15);
  overflow: hidden;
  box-shadow: inset 0 0 60px rgba(168,130,255,0.05);
}

.floor-planning {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 80px;
  background: linear-gradient(180deg, #e8d5b7 0%, #d4bf96 100%);
}
.floor-planning::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 4px;
  background: #c4a878;
}

.window-bg {
  position: absolute;
  top: 0;
  right: 0;
  width: 35%;
  height: 70%;
  background: linear-gradient(180deg, #87ceeb 0%, #b0e0e6 50%, #add8e6 100%);
  border-left: 2px solid #5a9bd5;
}
.window-frame {
  position: absolute;
  top: 0;
  right: 0;
  width: 35%;
  height: 70%;
  background: linear-gradient(90deg, rgba(139,90,43,0.3) 0%, transparent 5%);
}
.window-frame::before, .window-frame::after {
  content: '';
  position: absolute;
  background: #8b5a2b;
}
.window-frame::before { top: 0; left: 0; right: 0; height: 15px; }
.window-frame::after { top: 15px; left: 0; right: 0; height: 2px; background: linear-gradient(90deg, #8b5a2b 0%, #a06030 50%, #8b5a2b 100%); }

.curtain-left, .curtain-right {
  position: absolute;
  top: 0;
  width: 60px;
  height: 70%;
  background: linear-gradient(90deg, #f8bbd9 0%, #f48fb1 100%);
  border-radius: 0 10px 10px 0;
  animation: curtain-sway 4s ease-in-out infinite;
}
.curtain-left { right: 35%; }
.curtain-right { right: calc(35% + 60px); animation-delay: 0.5s; }
@keyframes curtain-sway {
  0%,100% { transform: translateX(0); }
  50% { transform: translateX(5px); }
}

.office-desk {
  position: absolute;
  bottom: 80px;
  left: 15%;
  width: 320px;
  height: 90px;
  background: linear-gradient(180deg, #deb887 0%, #cd853f 100%);
  border-radius: 4px;
  z-index: 1;
}
.office-desk::before {
  content: '';
  position: absolute;
  top: -4px;
  left: 0;
  right: 0;
  height: 4px;
  background: #daa520;
}
.desk-leg {
  position: absolute;
  bottom: -30px;
  width: 15px;
  height: 30px;
  background: linear-gradient(180deg, #8b4513 0%, #654321 100%);
}
.desk-leg:nth-child(1) { left: 20px; }
.desk-leg:nth-child(2) { right: 20px; }

.desk-plant {
  position: absolute;
  bottom: 140px;
  left: 18%;
  width: 30px;
  height: 60px;
  z-index: 2;
}
.plant-pot {
  position: absolute;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 30px;
  height: 20px;
  background: linear-gradient(180deg, #d4a574 0%, #b8860b 100%);
  border-radius: 0 0 5px 5px;
}
.plant-leaf {
  position: absolute;
  width: 12px;
  height: 35px;
  background: linear-gradient(180deg, #90ee90 0%, #32cd32 100%);
  border-radius: 50% 50% 50% 50% / 80% 80% 20% 20%;
  animation: plant-sway 3s ease-in-out infinite;
}
.plant-leaf:nth-child(1) { bottom: 15px; left: 50%; transform: translateX(-50%) rotate(-20deg); --r: -20deg; }
.plant-leaf:nth-child(2) { bottom: 10px; left: 60%; transform: rotate(15deg); --r: 15deg; animation-delay: 0.3s; }
.plant-leaf:nth-child(3) { bottom: 8px; left: 40%; transform: rotate(-30deg); --r: -30deg; animation-delay: 0.6s; }
@keyframes plant-sway {
  0%,100% { transform: rotate(var(--r, 0deg)); }
  50% { transform: rotate(calc(var(--r, 0deg) + 5deg)); }
}

.desk-mug {
  position: absolute;
  bottom: 100px;
  left: 25%;
  width: 28px;
  height: 35px;
  background: linear-gradient(180deg, #ff6b6b 0%, #ee5a5a 100%);
  border-radius: 3px 3px 8px 8px;
  z-index: 2;
}
.desk-mug::before {
  content: '';
  position: absolute;
  top: 8px;
  right: -8px;
  width: 8px;
  height: 18px;
  border: 3px solid #ee5a5a;
  border-left: none;
  border-radius: 0 8px 8px 0;
}
.desk-mug::after {
  content: '';
  position: absolute;
  top: -5px;
  left: 2px;
  right: 2px;
  height: 6px;
  background: #f8f5f0;
  border-radius: 3px;
}
.mug-steam {
  position: absolute;
  top: -15px;
  left: 50%;
  transform: translateX(-50%);
  width: 6px;
  height: 6px;
  background: rgba(255,255,255,0.6);
  border-radius: 50%;
  animation: steam-rise 2s ease-out infinite;
}
.mug-steam:nth-child(2) { animation-delay: 0.3s; }
.mug-steam:nth-child(3) { animation-delay: 0.6s; }
@keyframes steam-rise {
  0% { opacity: 0.6; transform: translateX(-50%) translateY(0) scale(1); }
  100% { opacity: 0; transform: translateX(-50%) translateY(-25px) scale(0.5); }
}

.desk-sticky {
  position: absolute;
  bottom: 160px;
  left: 28%;
  width: 35px;
  height: 40px;
  background: #fff9c4;
  transform: rotate(-5deg);
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
  animation: sticky-float 3s ease-in-out infinite;
  z-index: 3;
}
@keyframes sticky-float {
  0%,100% { transform: rotate(-5deg) translateY(0); }
  50% { transform: rotate(-3deg) translateY(-3px); }
}

.office-chair {
  position: absolute;
  bottom: 80px;
  left: 32%;
  width: 60px;
  height: 45px;
  background: linear-gradient(180deg, #4a4a6a 0%, #3a3a5a 100%);
  border-radius: 10px 10px 0 0;
  z-index: 0;
}
.chair-back {
  position: absolute;
  top: -35px;
  left: 50%;
  transform: translateX(-50%);
  width: 50px;
  height: 35px;
  background: linear-gradient(180deg, #4a4a6a 0%, #3a3a5a 100%);
  border-radius: 5px 5px 0 0;
}

.library-bg {
  position: absolute;
  inset: 0;
  z-index: 0;
  display: none;
}
.library-floor {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 100px;
  background: linear-gradient(180deg, #e8dfd0 0%, #d4c8b0 100%);
}
.library-shelf {
  position: absolute;
  bottom: 100px;
  left: 8%;
  width: 200px;
  height: 240px;
  background: linear-gradient(180deg, #8b7355 0%, #6b5344 100%);
  border-radius: 5px;
  z-index: 1;
}
.shelf-layer {
  position: absolute;
  left: 8px;
  right: 8px;
  height: 55px;
  border-bottom: 3px solid #5a4335;
}
.shelf-layer:nth-child(1) { top: 8px; }
.shelf-layer:nth-child(2) { top: 71px; }
.shelf-layer:nth-child(3) { top: 134px; }
.shelf-layer:nth-child(4) { top: 197px; border: none; }

.shelf-book {
  position: absolute;
  top: 5px;
  height: 42px;
  border-radius: 2px;
  animation: book-wobble 4s ease-in-out infinite;
}
.shelf-layer:nth-child(1) .shelf-book:nth-child(1) { left: 5px; width: 14px; background: #e53935; }
.shelf-layer:nth-child(1) .shelf-book:nth-child(2) { left: 21px; width: 16px; background: #43a047; }
.shelf-layer:nth-child(1) .shelf-book:nth-child(3) { left: 39px; width: 12px; background: #1e88e5; }
.shelf-layer:nth-child(1) .shelf-book:nth-child(4) { left: 53px; width: 18px; background: #fb8c00; }
.shelf-layer:nth-child(1) .shelf-book:nth-child(5) { left: 73px; width: 10px; background: #8e24aa; }
.shelf-layer:nth-child(2) .shelf-book:nth-child(1) { left: 5px; width: 12px; background: #00acc1; }
.shelf-layer:nth-child(2) .shelf-book:nth-child(2) { left: 19px; width: 14px; background: #e91e63; }
.shelf-layer:nth-child(2) .shelf-book:nth-child(3) { left: 35px; width: 16px; background: #66bb6a; }
.shelf-layer:nth-child(2) .shelf-book:nth-child(4) { left: 53px; width: 10px; background: #795548; }
.shelf-layer:nth-child(3) .shelf-book:nth-child(1) { left: 5px; width: 16px; background: #ffeb3b; }
.shelf-layer:nth-child(3) .shelf-book:nth-child(2) { left: 23px; width: 12px; background: #9c27b0; }
.shelf-layer:nth-child(3) .shelf-book:nth-child(3) { left: 37px; width: 14px; background: #3f51b5; }
.shelf-layer:nth-child(4) .shelf-book:nth-child(1) { left: 5px; width: 18px; background: #f44336; }
.shelf-layer:nth-child(4) .shelf-book:nth-child(2) { left: 25px; width: 12px; background: #00bcd4; }
.shelf-layer:nth-child(4) .shelf-book:nth-child(3) { left: 39px; width: 14px; background: #8bc34a; }
@keyframes book-wobble {
  0%,100% { transform: translateY(0); }
  50% { transform: translateY(1px); }
}

.reading-table {
  position: absolute;
  bottom: 100px;
  left: 35%;
  width: 180px;
  height: 60px;
  background: linear-gradient(180deg, #deb887 0%, #cd853f 100%);
  border-radius: 4px;
  z-index: 1;
}
.reading-table::before {
  content: '';
  position: absolute;
  top: -3px;
  left: 0;
  right: 0;
  height: 3px;
  background: #daa520;
}
.reading-lamp {
  position: absolute;
  bottom: 150px;
  left: 42%;
  width: 80px;
  height: 50px;
  z-index: 2;
}
.lamp-base {
  position: absolute;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 40px;
  height: 8px;
  background: #666;
  border-radius: 4px;
}
.lamp-pole {
  position: absolute;
  bottom: 5px;
  left: 50%;
  transform: translateX(-50%);
  width: 6px;
  height: 25px;
  background: #888;
}
.lamp-shade {
  position: absolute;
  bottom: 25px;
  left: 50%;
  transform: translateX(-50%);
  width: 80px;
  height: 30px;
  background: linear-gradient(180deg, #fff8dc 0%, #f0e68c 100%);
  border-radius: 40px 40px 0 0;
}
.lamp-glow {
  position: absolute;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 120px;
  height: 80px;
  background: radial-gradient(ellipse, rgba(255,255,200,0.4) 0%, transparent 70%);
  animation: lamp-glow-pulse 3s ease-in-out infinite;
}
@keyframes lamp-glow-pulse {
  0%,100% { opacity: 0.6; }
  50% { opacity: 1; }
}

.execution-bg {
  position: absolute;
  inset: 0;
  z-index: 0;
  display: none;
}
.execution-floor {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 80px;
  background: linear-gradient(180deg, #e8d5b7 0%, #d4bf96 100%);
}

.exec-desk {
  position: absolute;
  bottom: 80px;
  left: 20%;
  width: 300px;
  height: 80px;
  background: linear-gradient(180deg, #deb887 0%, #cd853f 100%);
  border-radius: 4px;
  z-index: 1;
}
.exec-desk::before {
  content: '';
  position: absolute;
  top: -4px;
  left: 0;
  right: 0;
  height: 4px;
  background: #daa520;
}

.exec-monitor {
  position: absolute;
  bottom: 140px;
  left: 25%;
  width: 160px;
  height: 100px;
  background: #2a2a3a;
  border: 5px solid #4a4a5a;
  border-radius: 8px;
  z-index: 3;
  overflow: hidden;
}
.exec-screen {
  position: absolute;
  top: 5px;
  left: 5px;
  right: 5px;
  bottom: 5px;
  background: #0d1117;
  border-radius: 3px;
}
.screen-glow {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: linear-gradient(135deg, rgba(100,181,246,0.15) 0%, transparent 50%);
  animation: screen-pulse 3s ease-in-out infinite;
}
@keyframes screen-pulse {
  0%,100% { opacity: 0.3; }
  50% { opacity: 0.6; }
}
.exec-monitor-stand {
  position: absolute;
  bottom: -18px;
  left: 50%;
  transform: translateX(-50%);
  width: 35px;
  height: 18px;
  background: #4a4a5a;
}
.exec-monitor-base {
  position: absolute;
  bottom: -25px;
  left: 50%;
  transform: translateX(-50%);
  width: 55px;
  height: 7px;
  background: #5a5a6a;
  border-radius: 3px;
}

.exec-keyboard {
  position: absolute;
  bottom: 95px;
  left: 30%;
  width: 120px;
  height: 18px;
  background: #2a2a3a;
  border-radius: 3px;
  z-index: 2;
  display: flex;
  flex-wrap: wrap;
  padding: 2px;
  gap: 1px;
}
.key-cap {
  width: 10px;
  height: 10px;
  background: #3a3a4a;
  border-radius: 2px;
  transition: all 0.1s ease;
}

.exec-mouse {
  position: absolute;
  bottom: 92px;
  left: 40%;
  width: 24px;
  height: 16px;
  background: #3a3a4a;
  border-radius: 50%;
  z-index: 2;
}

.exec-pen-holder {
  position: absolute;
  bottom: 100px;
  left: 42%;
  width: 15px;
  height: 30px;
  background: linear-gradient(90deg, #8b4513 0%, #a0522d 50%, #8b4513 100%);
  border-radius: 3px;
  z-index: 2;
}

.type-particle {
  position: absolute;
  font-size: 10px;
  color: rgba(255,255,255,0.8);
  font-weight: bold;
  pointer-events: none;
  animation: type-float 0.8s ease-out forwards;
}
@keyframes type-float {
  0% { opacity: 1; transform: translateY(0) scale(1); }
  100% { opacity: 0; transform: translateY(-30px) scale(0.5); }
}

.memory-bg {
  position: absolute;
  inset: 0;
  z-index: 0;
  display: none;
}
.memory-floor {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 80px;
  background: linear-gradient(180deg, #d4c4b0 0%, #b8a890 100%);
}

.memory-desk {
  position: absolute;
  bottom: 80px;
  left: 25%;
  width: 220px;
  height: 70px;
  background: linear-gradient(180deg, #c4a484 0%, #a08060 100%);
  border-radius: 4px;
  z-index: 1;
}

.memory-lamp {
  position: absolute;
  bottom: 130px;
  left: 38%;
  width: 50px;
  height: 40px;
  z-index: 2;
}
.memory-lamp-base {
  position: absolute;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 30px;
  height: 6px;
  background: #555;
  border-radius: 3px;
}
.memory-lamp-pole {
  position: absolute;
  bottom: 4px;
  left: 50%;
  transform: translateX(-50%) rotate(-15deg);
  width: 4px;
  height: 25px;
  background: #666;
  transform-origin: bottom center;
}
.memory-lamp-shade {
  position: absolute;
  bottom: 25px;
  left: 30%;
  width: 45px;
  height: 20px;
  background: linear-gradient(180deg, #fffacd 0%, #f0e68c 100%);
  border-radius: 20px 20px 0 0;
}
.memory-lamp-glow {
  position: absolute;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%) rotate(-15deg);
  width: 100px;
  height: 70px;
  background: radial-gradient(ellipse, rgba(255,252,200,0.3) 0%, transparent 70%);
  animation: memory-glow-pulse 4s ease-in-out infinite;
}
@keyframes memory-glow-pulse {
  0%,100% { opacity: 0.5; }
  50% { opacity: 0.8; }
}

.memory-notebook {
  position: absolute;
  bottom: 95px;
  left: 30%;
  width: 90px;
  height: 65px;
  background: #fff;
  border: 2px solid #8b7355;
  border-radius: 2px;
  z-index: 2;
  box-shadow: 0 3px 8px rgba(0,0,0,0.1);
}
.notebook-line {
  position: absolute;
  left: 10px;
  right: 10px;
  height: 1px;
  background: #ddd;
}
.notebook-line:nth-child(1) { top: 15px; }
.notebook-line:nth-child(2) { top: 25px; }
.notebook-line:nth-child(3) { top: 35px; }
.notebook-line:nth-child(4) { top: 45px; }

.memory-pen {
  position: absolute;
  bottom: 120px;
  left: 38%;
  width: 6px;
  height: 50px;
  background: linear-gradient(90deg, #333 0%, #555 50%, #333 100%);
  border-radius: 2px;
  z-index: 3;
}
.pen-cap {
  position: absolute;
  top: 0;
  left: -1px;
  width: 8px;
  height: 12px;
  background: #e53935;
  border-radius: 2px;
}
.pen-point {
  position: absolute;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 0;
  height: 0;
  border-left: 3px solid transparent;
  border-right: 3px solid transparent;
  border-top: 8px solid #333;
}

.ink-particle {
  position: absolute;
  width: 3px;
  height: 3px;
  background: #333;
  border-radius: 50%;
  pointer-events: none;
  animation: ink-dot 0.5s ease-out forwards;
}
@keyframes ink-dot {
  0% { opacity: 1; transform: scale(1); }
  100% { opacity: 0; transform: scale(0.3); }
}

.star-particle {
  position: absolute;
  width: 3px;
  height: 3px;
  background: rgba(255,255,255,0.6);
  border-radius: 50%;
  pointer-events: none;
  animation: star-rise 8s linear infinite;
}
@keyframes star-rise {
  0% { opacity: 0; transform: translateY(100%) scale(0.5); }
  10% { opacity: 1; }
  90% { opacity: 0.8; }
  100% { opacity: 0; transform: translateY(-10%) scale(1); }
}

.character-box {
  position: relative;
  width: 180px;
  height: 320px;
  z-index: 10;
  animation: bodySway 1.8s ease-in-out infinite;
}

.character {
  position: relative;
  width: 180px;
  height: 320px;
  animation: breathe 2s ease-in-out infinite;
}
@keyframes breathe {
  0%,100% { transform: scale(1); }
  50% { transform: scale(1.02); }
}

.ch-head {
  position: absolute;
  left: 40px;
  top: 10px;
  width: 100px;
  height: 95px;
  border-radius: 48% 48% 42% 42%;
  background: linear-gradient(155deg, #ffe4c0 0%, #f0cfa0 100%);
  box-shadow: inset -10px -10px rgba(190,120,90,0.15);
  z-index: 4;
  transform-origin: center bottom;
}

.ch-hair {
  position: absolute;
  left: 32px;
  top: 0;
  width: 116px;
  height: 75px;
  background: #2d3436;
  border-radius: 50% 50% 20% 20%;
  z-index: 5;
  clip-path: polygon(5% 0, 95% 0, 90% 75%, 70% 50%, 50% 80%, 30% 52%, 10% 82%, 2% 55%);
}

.ch-eye {
  position: absolute;
  top: 55px;
  width: 14px;
  height: 20px;
  border-radius: 50%;
  background: #1e272e;
  z-index: 6;
  animation: blink 6s infinite;
}
.ch-eye.l { left: 60px; }
.ch-eye.r { left: 105px; }
.ch-eye::before {
  content: '';
  position: absolute;
  top: 4px;
  left: 4px;
  width: 5px;
  height: 5px;
  background: #fff;
  border-radius: 50%;
}
@keyframes blink {
  0%,94%,100% { transform: scaleY(1); }
  96%,98% { transform: scaleY(0.08); }
}

.ch-brow {
  position: absolute;
  top: 48px;
  width: 18px;
  height: 5px;
  border-radius: 3px;
  background: #5d4037;
  z-index: 6;
}
.ch-brow.l { left: 58px; }
.ch-brow.r { left: 103px; }

.ch-mouth {
  position: absolute;
  left: 78px;
  top: 85px;
  width: 24px;
  height: 12px;
  border-bottom: 3px solid #b07060;
  border-radius: 50%;
  z-index: 6;
}

.ch-body {
  position: absolute;
  left: 55px;
  top: 100px;
  width: 70px;
  height: 100px;
  border-radius: 25px 25px 20px 20px;
  background: linear-gradient(140deg, #4dd0e1 0%, #26a69a 100%);
  box-shadow: inset -8px -8px rgba(20,80,90,0.15);
  z-index: 3;
}

.ch-arm {
  position: absolute;
  top: 125px;
  width: 22px;
  height: 75px;
  border-radius: 11px;
  background: linear-gradient(90deg, #ffe4c0 0%, #f0cfa0 100%);
  transform-origin: top center;
  z-index: 3;
}
.ch-arm.l { left: 35px; }
.ch-arm.r { left: 120px; }

.ch-leg {
  position: absolute;
  top: 190px;
  width: 30px;
  height: 85px;
  border-radius: 15px;
  background: #37474f;
  z-index: 2;
}
.ch-leg.l { left: 58px; }
.ch-leg.r { left: 90px; }

.ch-hand {
  position: absolute;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 20px;
  height: 18px;
  background: linear-gradient(180deg, #ffe4c0 0%, #f0cfa0 100%);
  border-radius: 50%;
  z-index: 4;
}

.book-prop {
  position: absolute;
  left: 115px;
  top: 145px;
  width: 55px;
  height: 42px;
  background: linear-gradient(180deg, #fff8e1 0%, #fff3e0 100%);
  border: 3px solid #8d6e63;
  border-radius: 5px;
  z-index: 7;
  display: none;
}
.book-page {
  position: absolute;
  top: 5px;
  left: 5px;
  right: 5px;
  bottom: 5px;
  background: #fff;
  border-radius: 2px;
}
.book-page::before, .book-page::after {
  content: '';
  position: absolute;
  left: 8px;
  right: 8px;
  height: 1px;
  background: #ddd;
}
.book-page::before { top: 10px; }
.book-page::after { top: 20px; }

.pen-prop {
  position: absolute;
  left: 95px;
  top: 150px;
  width: 5px;
  height: 45px;
  background: #333;
  border-radius: 2px;
  z-index: 7;
  display: none;
}
.pen-prop::before {
  content: '';
  position: absolute;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 0;
  height: 0;
  border-left: 3px solid transparent;
  border-right: 3px solid transparent;
  border-top: 7px solid #333;
}

body[data-scene="planning"] .ch-arm.l { animation: arm-think-l 1.2s ease-in-out infinite; }
body[data-scene="planning"] .ch-arm.r { animation: arm-think-r 1.2s ease-in-out infinite; }
body[data-scene="planning"] .ch-head { animation: head-think 3s ease-in-out infinite; }
body[data-scene="planning"] .ch-brow { animation: brow-furrow 2s ease-in-out infinite; }

@keyframes arm-think-l {
  0%,100% { transform: rotate(25deg) translateY(-10px); }
  50% { transform: rotate(40deg) translateY(-20px); }
}
@keyframes arm-think-r {
  0%,100% { transform: rotate(-25deg) translateY(-10px); }
  50% { transform: rotate(-40deg) translateY(-20px); }
}
@keyframes head-think {
  0%,100% { transform: rotate(0deg); }
  25% { transform: rotate(-12deg); }
  75% { transform: rotate(12deg); }
}
@keyframes brow-furrow {
  0%,100% { transform: translateY(0); }
  50% { transform: translateY(2px); }
}

body[data-scene="learning"] .library-bg { display: block; }
body[data-scene="learning"] .ch-arm.l { animation: arm-read-l 2s ease-in-out infinite; }
body[data-scene="learning"] .ch-arm.r { animation: arm-read-r 2s ease-in-out infinite; }
body[data-scene="learning"] .book-prop { display: block; animation: book-flip 4s ease-in-out infinite; }

@keyframes arm-read-l {
  0%,100% { transform: rotate(30deg) translateY(-5px); }
  50% { transform: rotate(35deg) translateY(-8px); }
}
@keyframes arm-read-r {
  0%,100% { transform: rotate(-30deg) translateY(-5px); }
  50% { transform: rotate(-35deg) translateY(-8px); }
}
@keyframes book-flip {
  0%,20%,100% { transform: rotate(-5deg); }
  25% { transform: rotate(15deg) scale(1.05); }
  30% { transform: rotate(-3deg) scale(1.02); }
}

body[data-scene="execution"] .execution-bg { display: block; }
body[data-scene="execution"] .ch-arm.l { animation: arm-type-l 0.3s ease-in-out infinite; }
body[data-scene="execution"] .ch-arm.r { animation: arm-type-r 0.3s ease-in-out infinite; }
body[data-scene="execution"] .exec-keyboard .key-cap { animation: key-tap 0.2s ease-in-out infinite; }
body[data-scene="execution"] .exec-keyboard .key-cap:nth-child(odd) { animation-delay: 0.05s; }
body[data-scene="execution"] .exec-keyboard .key-cap:nth-child(3n) { animation-delay: 0.1s; }
body[data-scene="execution"] .exec-keyboard .key-cap:nth-child(4n) { animation-delay: 0.15s; }

@keyframes arm-type-l {
  0%,100% { transform: rotate(20deg) translateY(0); }
  50% { transform: rotate(45deg) translateY(20px); }
}
@keyframes arm-type-r {
  0%,100% { transform: rotate(-20deg) translateY(0); }
  50% { transform: rotate(-45deg) translateY(20px); }
}
@keyframes key-tap {
  0%,100% { transform: translateY(0); background: #3a3a4a; }
  50% { transform: translateY(2px); background: #5a5a6a; }
}

body[data-scene="memory"] .memory-bg { display: block; }
body[data-scene="memory"] .ch-arm.r { animation: arm-write 3.5s ease-in-out infinite; }
body[data-scene="memory"] .pen-prop { display: block; animation: pen-write 3.5s ease-in-out infinite; }
body[data-scene="memory"] .memory-notebook { animation: notebook-wobble 3.5s ease-in-out infinite; }

@keyframes arm-write {
  0%,100% { transform: rotate(-30deg) translateY(0); }
  25% { transform: rotate(-35deg) translateY(5px); }
  50% { transform: rotate(-28deg) translateY(2px); }
  75% { transform: rotate(-33deg) translateY(4px); }
}
@keyframes pen-write {
  0%,100% { transform: translateX(0) translateY(0); }
  25% { transform: translateX(3px) translateY(2px); }
  50% { transform: translateX(-2px) translateY(1px); }
  75% { transform: translateX(4px) translateY(3px); }
}
@keyframes notebook-wobble {
  0%,100% { transform: rotate(0deg); }
  50% { transform: rotate(1deg); }
}

.thought-bubble {
  position: absolute;
  right: -40px;
  top: 40px;
  z-index: 5;
}
.bubble-main {
  width: 80px;
  height: 55px;
  background: rgba(255,255,255,0.95);
  border: 2px solid rgba(168,130,255,0.4);
  border-radius: 50%;
  box-shadow: 0 4px 12px rgba(0,0,0,0.1);
  animation: bubble-bob 2.8s ease-in-out infinite;
}
.bubble-dot-1 {
  position: absolute;
  left: -8px;
  top: 35px;
  width: 15px;
  height: 15px;
  background: rgba(255,255,255,0.95);
  border: 2px solid rgba(168,130,255,0.4);
  border-radius: 50%;
  animation: bubble-bob 2.8s ease-in-out infinite;
  animation-delay: 0.3s;
}
.bubble-dot-2 {
  position: absolute;
  left: -12px;
  top: 48px;
  width: 10px;
  height: 10px;
  background: rgba(255,255,255,0.95);
  border: 2px solid rgba(168,130,255,0.4);
  border-radius: 50%;
  animation: bubble-bob 2.8s ease-in-out infinite;
  animation-delay: 0.6s;
}
@keyframes bubble-bob {
  0%,100% { transform: translateY(0); }
  50% { transform: translateY(-12px); }
}
.bubble-icon {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  font-size: 30px;
  animation: icon-pulse 2s ease-in-out infinite;
}
@keyframes icon-pulse {
  0%,100% { transform: translate(-50%, -50%) scale(1); opacity: 0.8; }
  50% { transform: translate(-50%, -50%) scale(1.2); opacity: 1; }
}

.library-shelf-right {
  left: auto;
  right: 8%;
}
.library-carpet {
  position: absolute;
  bottom: 100px;
  left: 25%;
  width: 280px;
  height: 180px;
  background: linear-gradient(135deg, #f5e6d3 0%, #e8d5c0 100%);
  border-radius: 10px;
  z-index: 0;
}
.library-lamp {
  position: absolute;
  bottom: 220px;
  left: 38%;
  width: 80px;
  height: 50px;
  z-index: 2;
}
.library-lamp .lamp-glow {
  position: absolute;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 120px;
  height: 80px;
  background: radial-gradient(ellipse, rgba(255,255,200,0.4) 0%, transparent 70%);
  animation: lamp-glow-pulse 3s ease-in-out infinite;
}
.library-lamp .lamp-light {
  position: absolute;
  bottom: 5px;
  left: 50%;
  transform: translateX(-50%);
  width: 60px;
  height: 25px;
  background: linear-gradient(180deg, #fff8dc 0%, #f0e68c 100%);
  border-radius: 30px 30px 0 0;
}
.library-plant {
  position: absolute;
  bottom: 180px;
  right: 20%;
  width: 25px;
  height: 50px;
  z-index: 2;
}
.library-plant .plant-pot {
  position: absolute;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 25px;
  height: 15px;
  background: linear-gradient(180deg, #d4a574 0%, #b8860b 100%);
  border-radius: 0 0 4px 4px;
}
.library-plant .plant-leaf {
  position: absolute;
  width: 10px;
  height: 30px;
  background: linear-gradient(180deg, #90ee90 0%, #32cd32 100%);
  border-radius: 50% 50% 50% 50% / 80% 80% 20% 20%;
  animation: plant-sway 3s ease-in-out infinite;
}
.library-plant .plant-leaf:nth-child(1) { bottom: 12px; left: 50%; transform: translateX(-50%) rotate(-15deg); --r: -15deg; }
.library-plant .plant-leaf:nth-child(2) { bottom: 8px; left: 60%; transform: rotate(12deg); --r: 12deg; animation-delay: 0.3s; }

.scene-tabs {
  display: flex;
  justify-content: center;
  gap: 8px;
  padding: 12px;
  background: rgba(255,255,255,0.6);
  border-radius: 16px;
  border: 1px solid rgba(168,130,255,0.15);
  backdrop-filter: blur(8px);
}
.scene-tabs .tab-btn {
  padding: 8px 16px;
  border: none;
  border-radius: 99px;
  background: rgba(138,108,255,0.1);
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.3s ease;
}
.scene-tabs .tab-btn:hover {
  background: rgba(138,108,255,0.2);
  transform: scale(1.05);
}
.scene-tabs .tab-btn.active {
  background: #8a6cff;
  color: #fff;
  box-shadow: 0 0 12px rgba(138,108,255,0.5);
  animation: breatheLight 2s ease-in-out infinite;
}

.status-orbs {
  position: absolute;
  bottom: 25px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: 16px;
}
.orb {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  transition: all 0.3s ease;
}
.orb.active {
  box-shadow: 0 0 16px currentColor;
  animation: pulse-glow 2s ease-in-out infinite;
}
@keyframes pulse-glow {
  0%,100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.4); opacity: 0.6; }
}
.orb.blue { background: #64b5f6; }
.orb.green { background: #66bb6a; }
.orb.yellow { background: #ffb74d; }
.orb.red { background: #ef5350; }
.orb.purple { background: #ab47bc; }

.task-panel { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.panel-content { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }

::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: rgba(138,108,255,0.1); border-radius: 3px; }
::-webkit-scrollbar-thumb { background: rgba(138,108,255,0.3); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: rgba(138,108,255,0.5); }

.task-item {
  display: flex;
  flex-direction: column;
  padding: 12px;
  background: rgba(255,255,255,0.6);
  border: 1px solid rgba(138,108,255,0.15);
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.25s ease;
}
.task-item:hover {
  background: rgba(255,255,255,0.8);
  border-color: rgba(138,108,255,0.3);
  transform: translateX(4px);
}

.task-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.task-title { font-size: 13px; font-weight: 500; color: var(--text-primary); display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden; }
.task-badge { font-size: 10px; padding: 2px 8px; border-radius: 99px; text-transform: uppercase; letter-spacing: 0.04em; }

.badge-running { background: rgba(100,181,246,0.15); color: #64b5f6; }
.badge-planned { background: rgba(102,187,106,0.15); color: #66bb6a; }
.badge-pending { background: rgba(255,183,77,0.15); color: #ffb74d; }
.badge-completed { background: rgba(171,71,188,0.15); color: #ab47bc; }
.badge-failed { background: rgba(239,83,80,0.15); color: #ef5350; }

.task-meta { display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--text-muted); }
.task-progress { margin-top: 8px; height: 4px; background: rgba(138,108,255,0.1); border-radius: 2px; overflow: hidden; }
.task-progress-bar { height: 100%; border-radius: 2px; transition: width 0.5s ease; }
.progress-blue { background: linear-gradient(90deg, #64b5f6, #42a5f5); }
.progress-green { background: linear-gradient(90deg, #66bb6a, #43a047); }
.progress-yellow { background: linear-gradient(90deg, #ffb74d, #ffa726); }

.metrics-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }
.metric-card {
  background: rgba(255,255,255,0.7);
  border: 1px solid rgba(138,108,255,0.15);
  border-radius: 10px;
  padding: 12px;
  text-align: center;
  transition: all 0.3s ease;
}
.metric-card:hover {
  border-color: rgba(138,108,255,0.3);
  transform: scale(1.02);
}
.metric-value { font-size: 24px; font-weight: 700; font-variant-numeric: tabular-nums; }
.metric-value.blue { color: #64b5f6; }
.metric-value.green { color: #66bb6a; }
.metric-value.yellow { color: #ffb74d; }
.metric-value.red { color: #ef5350; }
.metric-label { font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; margin-top: 2px; }

.body-status { display: flex; align-items: center; gap: 8px; padding: 10px; background: rgba(255,255,255,0.7); border-radius: 10px; }
.body-icon { width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 16px; }
.body-icon.active { background: rgba(102,187,106,0.2); color: #66bb6a; }
.body-icon.candidate { background: rgba(255,183,77,0.2); color: #ffb74d; }
.body-info { flex: 1; }
.body-title { font-size: 12px; color: var(--text-primary); font-weight: 500; }
.body-desc { font-size: 10px; color: var(--text-muted); }

.timeline-list { display: flex; flex-direction: column; gap: 6px; }
.timeline-item {
  display: flex;
  gap: 10px;
  padding: 8px;
  background: rgba(255,255,255,0.5);
  border-left: 3px solid rgba(138,108,255,0.3);
  border-radius: 0 8px 8px 0;
  transition: all 0.2s ease;
}
.timeline-item:hover {
  background: rgba(255,255,255,0.7);
  border-left-color: #8a6cff;
}
.timeline-icon { font-size: 14px; }
.timeline-content { flex: 1; }
.timeline-text { font-size: 12px; color: var(--text-secondary); }
.timeline-time { font-size: 10px; color: var(--text-muted); }

.candidate-list { display: flex; flex-direction: column; gap: 6px; }
.candidate-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px;
  background: rgba(255,255,255,0.5);
  border-radius: 10px;
  border: 1px solid rgba(138,108,255,0.15);
  transition: all 0.2s ease;
}
.candidate-item:hover {
  background: rgba(255,255,255,0.7);
  border-color: rgba(255,183,77,0.3);
}
.candidate-bulb { width: 20px; height: 20px; border-radius: 50%; background: rgba(255,183,77,0.2); color: #ffb74d; display: flex; align-items: center; justify-content: center; font-size: 10px; }
.candidate-title { font-size: 12px; color: var(--text-primary); flex: 1; }
.candidate-utility { font-size: 11px; font-weight: 600; color: #ffb74d; }

.scene-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  background: rgba(255,255,255,0.8);
  border-radius: 16px;
  border: 1px solid rgba(138,108,255,0.15);
}
.scene-icon { width: 48px; height: 48px; border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 24px; }
.scene-icon.idle { background: rgba(100,181,246,0.15); }
.scene-icon.planning { background: rgba(255,183,77,0.15); }
.scene-icon.learning { background: rgba(102,187,106,0.15); }
.scene-icon.execution { background: rgba(239,83,80,0.15); }
.scene-icon.memory { background: rgba(171,71,188,0.15); }
.scene-title-area { flex: 1; }
.scene-title { font-size: 18px; font-weight: 600; color: var(--text-primary); }
.scene-summary { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }

.schedule-card {
  text-align: center;
  padding: 14px;
  background: rgba(255,255,255,0.6);
  border-radius: 12px;
  border: 1px solid rgba(138,108,255,0.15);
}
.schedule-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; }
.schedule-countdown { font-size: 28px; font-weight: 700; font-variant-numeric: tabular-nums; margin-top: 6px; }
.schedule-countdown.urgent { color: #ef5350; }
.schedule-countdown.normal { color: #8a6cff; }

.particles { position: fixed; inset: 0; z-index: 1; pointer-events: none; overflow: hidden; }
.particle { position: absolute; border-radius: 50%; animation: particle-drift linear infinite; }
.particle.small { width: 2px; height: 2px; background: rgba(138,108,255,0.5); }
.particle.medium { width: 3px; height: 3px; background: rgba(102,187,106,0.4); }
.particle.large { width: 4px; height: 4px; background: rgba(255,183,77,0.3); }
@keyframes particle-drift {
  0% { transform: translateY(100vh) translateX(0); opacity: 0; }
  10% { opacity: 1; }
  90% { opacity: 0.3; }
  100% { transform: translateY(-10vh) translateX(50px); opacity: 0; }
}

@media (max-width: 1200px) {
  .room { grid-template-columns: 240px 1fr 280px; }
}
@media (max-width: 900px) {
  .room { grid-template-columns: 1fr; grid-template-rows: auto 350px 1fr; }
  .sidebar-left { grid-row: 1; }
  .main-area { grid-row: 2; padding: 8px; }
  .sidebar-right { grid-row: 3; }
}

.char-card { text-align:center; padding:16px; }
.char-sprite-wrap { position:relative; display:inline-block; margin:0 auto 10px; }
.xizi-sprite { width:64px; height:80px; position:relative; margin:0 auto; transition:transform 0.3s ease; }
.xizi-sprite:hover { transform:scale(1.1); }
.xz-body { position:absolute; bottom:16px; left:8px; width:48px; height:40px; background:linear-gradient(140deg,#4dd0e1,#26a69a); border-radius:12px 12px 8px 8px; }
.xz-head { position:absolute; top:0; left:12px; width:40px; height:36px; background:linear-gradient(155deg,#ffe4c0,#f0cfa0); border-radius:44% 44% 40% 42%; }
.xz-eyes { position:absolute; top:16px; left:8px; right:8px; display:flex; justify-content:space-between; }
.eye-l,.eye-r { width:6px; height:8px; background:#1e2835; border-radius:50%; }
.xz-mouth { position:absolute; bottom:6px; left:50%; transform:translateX(-50%); width:10px; height:4px; border-bottom:2px solid #a06858; border-radius:50%; }
.xz-hair { position:absolute; top:-2px; left:6px; width:44px; height:24px; background:#1a2028; border-radius:20px 20px 8px 8px; z-index:5; }
.xz-arms { position:absolute; top:24px; left:-4px; right:-4px; }
.arm-l,.arm-r { position:absolute; width:10px; height:28px; background:linear-gradient(90deg,#ffe4c0,#f0cfa0); border-radius:5px; }
.arm-l { left:2px; transform:rotate(15deg); } .arm-r { right:2px; transform:rotate(-15deg); }
.xz-legs { position:absolute; bottom:2px; left:14px; right:14px; }
.leg-l,.leg-r { position:absolute; width:10px; height:16px; background:#253545; border-radius:4px; }
.leg-l { left:4px; } .leg-r { right:4px; }
.char-mood-bubble { position:absolute; top:-8px; right:-8px; font-size:18px; transition:transform 0.3s ease; }
.char-info { margin-top:8px; }
.char-name { font-size:15px; font-weight:700; color:var(--text-primary); }
.char-title { font-size:11px; color:var(--accent-purple); margin-left:4px; }
.char-level { font-size:12px; color:var(--text-secondary); margin:4px 0; }
.exp-bar-wrap { height:6px; background:rgba(0,0,0,0.08); border-radius:3px; overflow:hidden; margin:4px 0; }
.exp-bar-fill { height:100%; width:0; background:linear-gradient(90deg,var(--accent-purple),var(--accent-blue)); border-radius:3px; transition:width 0.5s ease; }
.exp-text { font-size:10px; color:var(--text-muted); }
.char-stats-row { display:flex; gap:10px; justify-content:center; margin-top:6px; font-size:11px; color:var(--text-secondary); }
.stat-icon { margin-right:2px; }
.metrics-grid { display:grid; gap:6px; }
.metric-item { display:grid; grid-template-columns:20px 1fr auto; gap:6px; align-items:center; padding:4px 0; font-size:11px; border-bottom:1px solid rgba(0,0,0,0.04); }
.metric-item.warn { color:var(--accent-red); }
.mi-icon { font-size:13px; } .mi-label { color:var(--text-secondary); } .mi-value { font-weight:600; }
.dim-text { color:var(--text-muted); font-size:11px; }
.scene-overlay { text-align:center; z-index:2; position:relative; }
.scene-icon-large { font-size:48px; margin-bottom:8px; }
.scene-title-lg { font-size:22px; font-weight:700; color:var(--text-primary); }
.scene-summary { font-size:13px; color:var(--text-secondary); margin-top:4px; }
.status-orbs { display:flex; gap:8px; justify-content:center; margin-top:12px; }
.orb { width:10px; height:10px; border-radius:50%; transition:all 0.5s ease; }
.orb-green { background:#66bb6a; box-shadow:0 0 8px rgba(102,187,106,0.5); }
.orb-blue { background:#64b5f6; box-shadow:0 0 8px rgba(100,181,246,0.5); }
.orb-yellow { background:#ffb74d; box-shadow:0 0 8px rgba(255,183,77,0.5); }
.orb-red { background:#ef5350; box-shadow:0 0 8px rgba(239,83,80,0.5); }
.orb-purple { background:#ab47bc; box-shadow:0 0 8px rgba(171,71,188,0.5); }
.orb-dim { background:#888; box-shadow:none; }
.click-hint { text-align:center; font-size:11px; color:var(--text-muted); margin-top:10px; cursor:pointer; }
.achievement-toast { position:fixed; top:20px; left:50%; transform:translateX(-50%); z-index:100; display:flex; align-items:center; gap:8px; padding:10px 20px; background:linear-gradient(135deg,#ffb74d,#ff9800); color:#fff; border-radius:20px; font-weight:600; box-shadow:0 4px 20px rgba(255,152,0,0.4); }
.achieve-icon { font-size:24px; } .achieve-text { font-size:14px; }
.progress-track { display:grid; gap:4px; }
.progress-cat { font-size:11px; padding:4px 8px; border-radius:6px; background:rgba(0,0,0,0.03); }
.cat-learning { border-left:3px solid #66bb6a; } .cat-maintenance { border-left:3px solid #ffb74d; } .cat-evolution { border-left:3px solid #ab47bc; }
.pc-icon { margin-right:4px; }
.card-badge { background:var(--accent-purple); color:#fff; font-size:10px; padding:2px 8px; border-radius:99px; min-width:20px; text-align:center; }
.system-log { max-height:150px; overflow-y:auto; font-size:10px; color:var(--text-secondary); }
.log-entry { padding:2px 0; border-bottom:1px solid rgba(0,0,0,0.03); }
.body-slot { font-size:12px; font-weight:600; color:var(--text-primary); }
.schedule-info { font-size:12px; color:var(--text-secondary); }
.candidate-item { display:flex; justify-content:space-between; padding:4px 0; font-size:11px; border-bottom:1px solid rgba(0,0,0,0.04); }
.cand-util { font-weight:600; color:var(--accent-yellow); }
.candidates-panel { margin-top:8px; }
.decor-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }
.decor-item { width:44px; height:44px; display:flex; align-items:center; justify-content:center; font-size:20px; border-radius:10px; background:rgba(0,0,0,0.04); transition:all 0.3s ease; }
.decor-item.locked { filter:grayscale(1); opacity:0.3; }
.decor-item:not(.locked):hover { transform:scale(1.15); background:rgba(168,130,255,0.1); }

</style>
</head>
<body data-scene="idle" data-error-count="0" data-exec-window="true">
<div id="gameWrap">
  <div class="scene-bg bg-planning active" id="bgPlanning"></div>
  <div class="scene-bg bg-learning" id="bgLearning"></div>
  <div class="scene-bg bg-execution" id="bgExecution"></div>
  <div class="scene-bg bg-memory" id="bgMemory"></div>
  <div class="room">
    <div class="sidebar-left">
      <div class="card char-card">
        <div class="char-sprite-wrap">
          <div class="xizi-sprite"><div class="xz-body"></div><div class="xz-head"><div class="xz-eyes"><span class="eye-l"></span><span class="eye-r"></span></div><div class="xz-mouth"></div></div><div class="xz-hair"></div><div class="xz-arms"><span class="arm-l"></span><span class="arm-r"></span></div><div class="xz-legs"><span class="leg-l"></span><span class="leg-r"></span></div></div>
          <div class="char-mood-bubble" id="moodBubble">&#x2728;</div>
        </div>
        <div class="char-info">
          <div class="char-name">&#x4E49;&#x5B50; <span class="char-title" id="charTitle">&#x89C1;&#x4E60;&#x7BA1;&#x7406;&#x5458;</span></div>
          <div class="char-level">Lv.<span id="charLevel">1</span></div>
          <div class="exp-bar-wrap"><div class="exp-bar-fill" id="expBar"></div></div>
          <div class="exp-text"><span id="expCurrent">0</span> / <span id="expNext">100</span> EXP</div>
          <div class="char-stats-row"><span class="stat-icon">&#x2764;&#xFE0F;</span><span id="statEnergy">100</span>% <span class="stat-icon">&#x1F60A;</span><span id="statMood">&#x666E;&#x901A;</span></div>
        </div>
      </div>
      <div class="card"><div class="card-header"><span class="card-title">&#x1F4CA; &#x7CFB;&#x7EDF;&#x72B6;&#x6001;</span></div><div class="metrics-grid" id="metrics"></div></div>
    </div>
    <div class="main-area">
      <div class="character-scene" id="charScene">
        <div class="scene-overlay"><div class="scene-icon-large" id="sceneIcon">&#x1F319;</div><div class="scene-title-lg" id="sceneTitle">&#x4F11;&#x606F;&#x4E2D;</div><div class="scene-summary" id="sceneSummary">&#x6682;&#x65E0;&#x4EFB;&#x52A1;&#x9700;&#x8981;&#x5904;&#x7406;</div></div>
        <div class="status-orbs" id="statusOrbs"><div class="orb orb-green" title="Gateway"></div><div class="orb orb-blue" title="Supervisor"></div><div class="orb orb-yellow" title="Learning"></div></div>
        <div class="click-hint" id="clickHint">&#x1F446; &#x70B9;&#x51FB;&#x4E49;&#x5B50;&#x4E92;&#x52A8;</div>
        <div class="achievement-toast" id="achievementToast" style="display:none"><div class="achieve-icon" id="achieveIcon">&#x1F3C6;</div><div class="achieve-text" id="achieveText"></div></div>
      </div>
      <div class="card"><div class="card-header"><span class="card-title">&#x1F4CB; &#x4EFB;&#x52A1;&#x8FDB;&#x5EA6;</span><span class="card-badge" id="taskCount">0</span></div><div class="progress-track" id="progressTrack"></div></div>
      <div class="card"><div class="card-header"><span class="card-title">&#x1F4DC; &#x7CFB;&#x7EDF;&#x65E5;&#x5FD7;</span></div><div class="system-log" id="systemLog"></div></div>
    </div>
    <div class="sidebar-right">
      <div class="card"><div class="card-header"><span class="card-title">&#x1F5A5; Body &#x72B6;&#x6001;</span></div><div class="body-info" id="bodyStatus"></div></div>
      <div class="card"><div class="card-header"><span class="card-title">&#x23F0; &#x8FD0;&#x884C;&#x5468;&#x671F;</span></div><div class="schedule-info" id="schedule"></div></div>
      <div class="card" id="candidatesCard" style="display:none"><div class="card-header"><span class="card-title">&#x1F4A1; &#x5185;&#x751F;&#x5019;&#x9009;</span><button class="toggle-btn" onclick="togglePanel('candidatesPanel')">&#x25BC;</button></div><div class="candidates-panel" id="candidatesPanel"><div id="candidateList"></div></div></div>
      <div class="card"><div class="card-header"><span class="card-title">&#x1F3E0; &#x623F;&#x95F4;&#x88C5;&#x9970;</span></div><div class="decor-grid" id="decorGrid"><div class="decor-item locked" title="10&#x4E2A;&#x4EFB;&#x52A1;">&#x1F331;</div><div class="decor-item locked" title="24h&#x8FD0;&#x884C;">&#x1F56F;</div><div class="decor-item locked" title="0&#x9519;&#x8BEF;">&#x1F48E;</div><div class="decor-item locked" title="Governor&#x6A21;&#x5F0F;">&#x1F451;</div><div class="decor-item locked" title="&#x8EAB;&#x4F53;&#x5207;&#x6362;">&#x1F3C6;</div><div class="decor-item locked" title="50&#x4E2A;&#x5B66;&#x4E60;">&#x1F4DA;</div></div></div>
    </div>
  </div>
  <div id="particles"></div>
</div>
<script>
const $=id=>document.getElementById(id);
const els={sceneIcon:$('sceneIcon'),sceneTitle:$('sceneTitle'),sceneSummary:$('sceneSummary'),metrics:$('metrics'),bodyStatus:$('bodyStatus'),schedule:$('schedule'),progressTrack:$('progressTrack'),systemLog:$('systemLog'),candidateList:$('candidateList'),candidatesCard:$('candidatesCard'),taskCount:$('taskCount'),statusOrbs:$('statusOrbs'),charLevel:$('charLevel'),charTitle:$('charTitle'),expBar:$('expBar'),expCurrent:$('expCurrent'),expNext:$('expNext'),statEnergy:$('statEnergy'),statMood:$('statMood'),moodBubble:$('moodBubble'),achievementToast:$('achievementToast'),achieveIcon:$('achieveIcon'),achieveText:$('achieveText'),decorGrid:$('decorGrid'),clickHint:$('clickHint'),charScene:$('charScene')};
let gs={level:1,exp:0,expToNext:100,totalTasksDone:0,totalErrors:0,governorTime:0,lastScene:'idle',unlockedDecor:new Set()};
const SCENES={idle:{icon:'\u{1F319}',bg:'bgPlanning',title:'休息中',mood:'\u{1F634}'},planning:{icon:'\u{1F4CB}',bg:'bgPlanning',title:'正在规划',mood:'\u{1F914}'},learning:{icon:'\u{1F4D6}',bg:'bgLearning',title:'学习中',mood:'\u{1F9D0}'},execution:{icon:'\u{1F680}',bg:'bgExecution',title:'执行中',mood:'\u{1F4AA}'},memory:{icon:'\u{1F4BE}',bg:'bgMemory',title:'维护记忆',mood:'\u{1F4DD}'},maintenance:{icon:'\u{1F527}',bg:'bgMemory',title:'系统维护',mood:'\u{1F527}'},body_switch:{icon:'\u{1F504}',bg:'bgExecution',title:'身体切换',mood:'⚡'},drive:{icon:'✨',bg:'bgPlanning',title:'灵感涌现',mood:'\u{1F4A1}'}};
const MOODS=[{min:0,label:'第惹',emoji:'\u{1F62B}',color:'#ef5350'},{min:30,label:'低落',emoji:'\u{1F614}',color:'#ff9800'},{min:50,label:'普通',emoji:'\u{1F60A}',color:'#66bb6a'},{min:70,label:'愉快',emoji:'\u{1F604}',color:'#4caf50'},{min:85,label:'兴奋',emoji:'\u{1F929}',color:'#ffb74d'},{min:95,label:'完美',emoji:'✨',color:'#ab47bc'}];
function getMood(e){for(let i=MOODS.length-1;i>=0;i--){if(e>=MOODS[i].min)return MOODS[i]}return MOODS[0]}
function switchScene(n){const c=SCENES[n]||SCENES.idle;document.querySelectorAll('.scene-bg').forEach(e=>e.classList.remove('active'));const bg=document.getElementById(c.bg);if(bg)bg.classList.add('active');els.sceneIcon.textContent=c.icon;els.sceneTitle.textContent=c.title;document.body.dataset.scene=n;gs.lastScene=n;els.moodBubble.textContent=c.mood}
function applyState(s){const sc=s.scene||'idle';switchScene(sc);if(s.title)els.sceneTitle.textContent=s.title;if(s.summary)els.sceneSummary.textContent=s.summary;const m=s.metrics||{};renderMetrics(m);renderBS(s.body_status);renderSched(s.schedule);renderProg(s.panels||{},s.active_executions||[]);renderCands(s.drive_candidates||[]);const errs=m.error_count||s.error_count||0;gs.totalErrors=errs;const energy=Math.max(0,100-errs*10);const mood=getMood(energy);els.statEnergy.textContent=energy;els.statMood.textContent=mood.label;document.body.dataset.errorCount=errs>0?'true':'false';document.body.dataset.execWindow=s.in_execution_window!==false?'true':'false';const done=(m.by_path?.learning||0)+(m.by_path?.maintenance||0)+(m.by_path?.evolution||0);if(done>gs.totalTasksDone){gainExp((done-gs.totalTasksDone)*15);gs.totalTasksDone=done}if(s.governor_mode?.active)gs.governorTime+=1;checkDecor(m);const tl=s.timeline||[];if(tl.length>0)addLog(tl[0].summary||'系统活动');updOrbs(s)}
function renderMetrics(m){els.metrics.innerHTML='';const bp=m.by_path||{};[{l:'任务总数',v:m.queue_total||0,i:'\u{1F4E6}'},{l:'学习任务',v:bp.learning||0,i:'\u{1F4D6}'},{l:'维护任务',v:bp.maintenance||0,i:'\u{1F527}'},{l:'进化任务',v:bp.evolution||0,i:'\u{1F680}'},{l:'错误数',v:m.error_count||0,i:'⚠',w:(m.error_count||0)>0},{l:'Body',v:m.active_slot||'-',i:'\u{1F5A5}'},{l:'执行窗口',v:document.body.dataset.execWindow==='true'?'开放':'关闭',i:'\u{1F550}'}].forEach(it=>{const d=document.createElement('div');d.className='metric-item'+(it.w?' warn':'');d.innerHTML='<span class="mi-icon">'+it.i+'</span><span class="mi-label">'+it.l+'</span><span class="mi-value">'+it.v+'</span>';els.metrics.appendChild(d)})}
function renderBS(bs){if(!bs||!bs.active_slot){els.bodyStatus.innerHTML='<span class="dim-text">未检测到 Body</span>';return}els.bodyStatus.innerHTML='<span class="body-slot">\u{1F5A5} '+bs.active_slot+'</span>'+(bs.candidate_slot?' → 候选: '+bs.candidate_slot:'')}
function renderSched(sched){if(!sched){els.schedule.innerHTML='<span class="dim-text">暂无周期信息</span>';return}const n=sched.next_review_at||sched.next_drive_at;if(!n){els.schedule.innerHTML='<span>⏳ 等待首次周期</span>';return}const d=new Date(n);const r=Math.max(0,Math.floor((d.getTime()-Date.now())/1000));els.schedule.innerHTML='<span>⏰ 下次检查: '+Math.floor(r/60)+'m '+r%60+'s</span>'}
function renderProg(panels,execs){els.progressTrack.innerHTML='';let t=0;['learning','maintenance','evolution'].forEach(cat=>{const p=panels[cat];if(!p||!p.count)return;t+=p.count;const d=document.createElement('div');d.className='progress-cat cat-'+cat;const r=execs.filter(e=>(e.task_family||'').includes(cat)).length;d.innerHTML='<span class="pc-icon">'+(cat==='learning'?'\u{1F4D6}':cat==='maintenance'?'\u{1F527}':'\u{1F680}')+'</span> '+p.label+': <b>'+p.count+'</b> '+(r>0?'⚡'+r+'执行中':'');els.progressTrack.appendChild(d)});els.taskCount.textContent=t}
function renderCands(cands){if(!cands||!cands.length){els.candidatesCard.style.display='none';return}els.candidatesCard.style.display='block';els.candidateList.innerHTML='';cands.slice(0,4).forEach(c=>{const d=document.createElement('div');d.className='candidate-item';d.innerHTML='<span>\u{1F4A1} '+(c.title||'').substring(0,30)+'</span><span class="cand-util">'+Math.round((c.utility||0)*100)+'%</span>';els.candidateList.appendChild(d)})}
function addLog(msg){const d=document.createElement('div');d.className='log-entry';d.textContent='['+new Date().toLocaleTimeString()+'] '+msg;els.systemLog.prepend(d);if(els.systemLog.children.length>20)els.systemLog.lastChild.remove()}
function gainExp(a){gs.exp+=a;while(gs.exp>=gs.expToNext){gs.exp-=gs.expToNext;gs.level++;gs.expToNext=Math.floor(gs.expToNext*1.3);els.charLevel.textContent=gs.level;showAch('⬆','升级! 义子达到 Lv.'+gs.level);updTitle()}els.expCurrent.textContent=gs.exp;els.expNext.textContent=gs.expToNext;els.expBar.style.width=(gs.exp/gs.expToNext*100)+'%'}
function updTitle(){const ts=['见习管理员','初级监督者','熟练管理者','资深守护者','传奇监督者','虚空大师'];els.charTitle.textContent=ts[Math.min(Math.floor((gs.level-1)/5),ts.length-1)]}
function showAch(icon,text){els.achieveIcon.textContent=icon;els.achieveText.textContent=text;els.achievementToast.style.display='flex';els.achievementToast.style.animation='none';void els.achievementToast.offsetHeight;els.achievementToast.style.animation='fadeIn 0.5s ease, fadeOut 0.5s ease 2.5s forwards';setTimeout(()=>els.achievementToast.style.display='none',3100)}
const AQ=[];function procAchQ(){if(AQ.length>0){const a=AQ.shift();showAch(a.icon,a.text)}}
let dm=null;function checkDecor(m){if(!dm){dm=[{idx:0,check:()=>gs.totalTasksDone>=10,icon:'\u{1F331}',label:'新芽'},{idx:1,check:()=>gs.governorTime>=24,icon:'\u{1F56F}',label:'长明烛'},{idx:2,check:()=>gs.totalErrors===0&&gs.totalTasksDone>0,icon:'\u{1F48E}',label:'纯净水晶'},{idx:3,check:()=>m.body_switch_active,icon:'\u{1F451}',label:'王者之冠'},{idx:4,check:()=>gs.totalTasksDone>=50,icon:'\u{1F4DA}',label:'智慧书架'}]}dm.forEach(dm_=>{if(dm_.check()&&!gs.unlockedDecor.has(dm_.idx)){gs.unlockedDecor.add(dm_.idx);const el=els.decorGrid.children[dm_.idx];if(el){el.classList.remove('locked');el.title=dm_.label;el.textContent=dm_.icon}else{const nd=document.createElement('div');nd.className='decor-item';nd.textContent=dm_.icon;nd.title=dm_.label;els.decorGrid.appendChild(nd)}showAch(dm_.icon,'解锁装饰: '+dm_.label+'!')}})}
function updOrbs(s){const orbs=els.statusOrbs.children;if(orbs[0])orbs[0].className='orb '+(s.error_count>0?'orb-red':'orb-green');if(orbs[1])orbs[1].className='orb '+(s.governor_mode?.active?'orb-purple':'orb-blue');if(orbs[2])orbs[2].className='orb '+((s.panels?.learning?.count||0)>0?'orb-yellow':'orb-dim')}
els.charScene.addEventListener('click',()=>{const ds=['当前 Lv.'+gs.level+', 状态良好~','已完成 '+gs.totalTasksDone+' 个任务!',gs.totalErrors>0?'有 '+gs.totalErrors+' 个错误需要关注...':'一切运行正常!','点击 /help 查看可用命令'];const m_=ds[Math.floor(Math.random()*ds.length)];els.clickHint.textContent=m_;els.clickHint.style.animation='none';void els.clickHint.offsetHeight;els.clickHint.style.animation='fadeIn 0.3s ease';setTimeout(()=>els.clickHint.textContent='\u{1F446} 点击义子互动',4000)})
function togglePanel(id){document.getElementById(id).style.display=document.getElementById(id).style.display==='none'?'block':'none'}
let ft=null;async function refresh(){try{const r=await fetch('/ui/state',{cache:'no-store'});applyState(await r.json())}catch(e){els.sceneTitle.textContent='等待连接...';els.sceneSummary.textContent='监督者尚未就绪'}}
function startFB(){if(!ft){refresh();ft=setInterval(refresh,4000)}}
if(typeof EventSource!=='undefined'){const es=new EventSource('/ui/events');es.addEventListener('state',ev=>{if(ft){clearInterval(ft);ft=null}applyState(JSON.parse(ev.data))});es.onerror=()=>startFB()}else{startFB()}
refresh();setInterval(()=>{if(gs.governorTime<9999)gs.governorTime++;checkDecor({body_switch_active:false})},60000);
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
