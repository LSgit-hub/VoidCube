from __future__ import annotations

import asyncio
import json
import logging
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

logger = logging.getLogger("supervisor")

UI_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>VoidCube · 星子与西子的小屋</title>
<!-- VoidCube Supervisor Room -->
<!-- EventSource("/ui/events") -->
<style>
/*━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  VoidCube 监督者小屋  v4  ——  完整自由重设计
  视觉: 暖橘日落 / 木质 + 棉麻 / 现代插画感
  角色: 义子(动漫比例少女), 4 个可切换动作 + 自动跟随
  状态: 保留任务卡片 + 状态面板, 通过 data-action/data-scene 驱动
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━*/
:root {
  color-scheme: dark;
  /* 主色 */
  --ink: #2a1d18;
  --ink-soft: #4a3a30;
  --paper: #fbf3e2;
  --wall-a: #f6c9a8;          /* 暖橘 */
  --wall-b: #e89b78;          /* 砖橘 */
  --floor-a: #b07a52;
  --floor-b: #7a4f30;
  --trim:   #5a3320;
  /* 强调色 */
  --mint:   #6fc6a0;
  --mint-g: rgba(111,198,160,.45);
  --coral:  #e8826e;
  --coral-g: rgba(232,130,110,.5);
  --gold:   #e2b04a;
  --gold-g: rgba(226,176,74,.55);
  --indigo: #6a7eb8;
  --indigo-g: rgba(106,126,184,.4);
  --plum:   #a78ad4;
  --plum-g: rgba(167,138,212,.5);
  --shadow: rgba(60,30,15,.25);
  --shadow-deep: rgba(60,30,15,.45);
  /* 文本(任务卡片用) */
  --text-primary: #f0e2c8;
  --text-secondary: #c8b89c;
  --text-muted: #8a7a64;
  --accent-purple: #c89af0;
  --accent-blue:   #6a9ee8;
  --accent-green:  #6fc6a0;
  --accent-yellow: #e2b04a;
  --accent-red:    #e8826e;
  --radius-md: 12px;
  /* 房间 */
  --room-w: 1440px;
  --room-h: 810px;
  /* 缓动 */
  --ease-out: cubic-bezier(.22,.9,.32,1);
  --ease-in-out: cubic-bezier(.4,0,.2,1);
}

* { box-sizing: border-box; }

html, body {
  margin: 0; padding: 0; overflow: hidden;
  width: 100%; height: 100%;
  font-family: "Inter","PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  background: #2a1812;
  color: var(--ink);
}

/* ── 房间舞台 ── */
.stage {
  position: fixed; inset: 0;
  display: flex; align-items: center; justify-content: center;
  overflow: hidden;
  background: radial-gradient(ellipse at 50% 30%, #3a2418 0%, #1a0e08 80%);
}
.room {
  position: relative;
  width: var(--room-w); height: var(--room-h);
  transform: scale(var(--room-scale, 1));
  transform-origin: center center;
  overflow: hidden;
  border-radius: 4px;
  box-shadow: 0 40px 100px rgba(0,0,0,.6);
}

/* ── 墙(带横纹墙纸) ── */
.wall {
  position: absolute; inset: 0 0 32% 0;
  background:
    /* 顶部高光 */
    linear-gradient(180deg, rgba(255,235,200,.18) 0%, rgba(255,235,200,0) 30%),
    /* 横纹墙纸 */
    repeating-linear-gradient(0deg, rgba(90,40,20,.04) 0 1px, transparent 1px 24px),
    /* 暖橘渐变 */
    linear-gradient(168deg, var(--wall-a) 0%, #f0a878 50%, var(--wall-b) 100%);
}

/* ── 踢脚线 ── */
.baseboard {
  position: absolute; left: 0; right: 0; bottom: 32%;
  height: 14px;
  background: linear-gradient(180deg, #4a2818 0%, #2a1608 100%);
  box-shadow: 0 6px 8px rgba(0,0,0,.3);
  z-index: 2;
}

/* ── 地板(宽木条) ── */
.floor {
  position: absolute; left: 0; right: 0; bottom: 0; height: 32%;
  background:
    /* 木条接缝 */
    repeating-linear-gradient(90deg, rgba(0,0,0,.25) 0 2px, transparent 2px 120px),
    /* 细纹 */
    repeating-linear-gradient(0deg, rgba(0,0,0,.05) 0 1px, transparent 1px 6px),
    /* 渐变 */
    linear-gradient(180deg, var(--floor-a) 0%, var(--floor-b) 100%);
  box-shadow: inset 0 4px 12px rgba(0,0,0,.3);
}

/* ── 墙上的钟 ── */
.wall-clock {
  position: absolute;
  left: 50%; top: 5%;
  transform: translateX(-50%);
  width: 100px; height: 100px;
  z-index: 2;
}
.wc-body {
  position: absolute; inset: 0;
  border-radius: 50%;
  background: radial-gradient(circle at 30% 30%, #fbf3e2, #d4a878);
  border: 4px solid #5a3320;
  box-shadow:
    0 8px 16px var(--shadow-deep),
    inset 0 0 0 2px #b08850;
}
.wc-tick {
  position: absolute;
  left: 50%; top: 8px;
  width: 2px; height: 10px;
  background: var(--ink);
  transform-origin: 50% 42px;
}
.wc-tick.major { width: 3px; height: 14px; background: var(--trim); }
.wc-hand {
  position: absolute; left: 50%; top: 50%;
  background: var(--ink);
  transform-origin: 50% 100%;
  border-radius: 2px;
  z-index: 2;
}
.wc-hand.h { width: 4px; height: 28px; margin-left: -2px; margin-top: -28px; }
.wc-hand.m {
  width: 3px; height: 36px; margin-left: -1.5px; margin-top: -36px;
  background: var(--trim);
  animation: clock-tick 60s steps(60) infinite;
}
.wc-hand.s {
  width: 1.5px; height: 42px; margin-left: -.75px; margin-top: -42px;
  background: var(--coral);
  animation: clock-tick 1s steps(60) infinite;
}
.wc-center {
  position: absolute; left: 50%; top: 50%;
  width: 8px; height: 8px; margin: -4px;
  border-radius: 50%;
  background: var(--coral);
  z-index: 3;
  box-shadow: 0 0 4px var(--coral-g);
}
@keyframes clock-tick { to { transform: rotate(360deg); } }

/* ── 窗户(可日夜切换) ── */
.window {
  position: absolute;
  right: 6%; top: 14%;
  width: 180px; height: 220px;
  background: #efe5cf;
  border-radius: 60px 60px 8px 8px;
  padding: 8px;
  box-shadow:
    0 16px 30px var(--shadow-deep),
    inset 0 0 0 3px #d4a878;
  z-index: 2;
}
.window-glass {
  position: absolute; inset: 8px;
  border-radius: 54px 54px 4px 4px;
  background: linear-gradient(180deg, #8cc4e0 0%, #c8e0ee 50%, #a8c8a0 51%, #7a9a70 100%);
  overflow: hidden;
  transition: background 1.2s ease;
}
.window-frame-h, .window-frame-v {
  position: absolute; background: #efe5cf;
  z-index: 3;
}
.window-frame-h { left: 8px; right: 8px; top: 50%; height: 6px; transform: translateY(-50%); }
.window-frame-v { top: 8px; bottom: 8px; left: 50%; width: 6px; transform: translateX(-50%); }
.window-sill {
  position: absolute; left: -16px; right: -16px; bottom: -10px;
  height: 14px;
  background: linear-gradient(180deg, #8a5a3a, #5a3a20);
  border-radius: 2px;
  box-shadow: 0 4px 6px rgba(0,0,0,.3);
  z-index: 1;
}
.window-sun {
  position: absolute; right: 22%; top: 26%;
  width: 30px; height: 30px;
  border-radius: 50%;
  background: radial-gradient(circle, #fff8c4, #f7d86c);
  box-shadow: 0 0 24px rgba(255,220,100,.7);
  z-index: 2;
  transition: opacity .8s;
}
.window-moon {
  position: absolute; right: 22%; top: 26%;
  width: 22px; height: 22px;
  border-radius: 50%;
  background: radial-gradient(circle at 38% 38%, #f0f4f8, #c8d6e0);
  box-shadow: 0 0 16px rgba(200,210,230,.6);
  z-index: 2;
  display: none;
}
.window-cloud {
  position: absolute; background: rgba(255,255,255,.7);
  border-radius: 20px;
  z-index: 2;
  animation: cloud-drift 30s linear infinite;
}
.window-cloud.c1 { width: 30px; height: 8px; top: 18%; left: -10%; }
.window-cloud.c2 { width: 22px; height: 6px; top: 38%; left: -20%; animation-delay: -10s; }
@keyframes cloud-drift {
  to { transform: translateX(180px); }
}
.star {
  position: absolute; border-radius: 50%; background: #fff;
  z-index: 2; opacity: 0; animation: twinkle 2.5s ease-in-out infinite;
}
@keyframes twinkle { 50% { opacity: .8; } }

/* 夜间模式 */
body[data-exec-window="false"] .window-glass {
  background: linear-gradient(180deg, #1e3050 0%, #2d4470 50%, #1a2530 100%);
}
body[data-exec-window="false"] .window-sun { opacity: 0; }
body[data-exec-window="false"] .window-moon { display: block; }
body[data-exec-window="false"] .window-cloud { opacity: 0; }
body[data-exec-window="false"] .star { opacity: 1; }
body[data-exec-window="false"] .wall {
  filter: brightness(.7) saturate(.8);
}
body[data-exec-window="false"] .floor {
  filter: brightness(.55) saturate(.7);
}

/* ── 书架(左墙) ── */
.shelf {
  position: absolute;
  left: 3%; top: 12%;
  width: 280px; height: 360px;
  z-index: 3;
}
.shelf-frame {
  position: absolute; inset: 0;
  border: 18px solid #5a3320;
  border-radius: 8px;
  background: linear-gradient(180deg, #3a2418 0%, #2a1608 100%);
  box-shadow:
    0 24px 40px var(--shadow-deep),
    inset 0 0 30px rgba(0,0,0,.5);
}
.shelf-board {
  position: absolute; left: 0; right: 0;
  height: 16px;
  background: linear-gradient(180deg, #8a5a3a 0%, #5a3320 100%);
  box-shadow: 0 4px 6px rgba(0,0,0,.4);
  z-index: 3;
}
.shelf-board.b1 { top: 0; }
.shelf-board.b2 { top: 33.3%; transform: translateY(-50%); }
.shelf-board.b3 { top: 66.6%; transform: translateY(-50%); }
.shelf-board.b4 { bottom: 0; }
.shelf-row {
  position: absolute; left: 20px; right: 20px;
  display: flex; align-items: flex-end;
  gap: 4px;
  z-index: 2;
}
.shelf-row.r1 { top: 20px; height: 28%; }
.shelf-row.r2 { top: calc(33.3% + 16px); height: 28%; }
.shelf-row.r3 { top: calc(66.6% + 16px); height: 28%; }
.shelf-row.r4 { bottom: 20px; height: 28%; }
.book {
  position: relative;
  border-radius: 2px 2px 0 0;
  box-shadow:
    inset -2px 0 rgba(255,255,255,.18),
    inset 2px 0 rgba(0,0,0,.15),
    0 2px 3px rgba(0,0,0,.3);
  transition: transform .3s var(--ease-out);
}
.book::after {
  content: ""; position: absolute;
  left: 50%; top: 20%;
  width: 1.5px; height: 60%;
  transform: translateX(-50%);
  background: rgba(255,255,255,.25);
  border-radius: 1px;
}
.book.b1  { width: 20px; height: 88%; background: linear-gradient(180deg, #7ba8c8, #4a7a9a); }
.book.b2  { width: 22px; height: 72%; background: linear-gradient(180deg, #e89a8a, #c66858); }
.book.b3  { width: 18px; height: 95%; background: linear-gradient(180deg, #e8c878, #b08830); }
.book.b4  { width: 24px; height: 78%; background: linear-gradient(180deg, #a8c89a, #689070); }
.book.b5  { width: 20px; height: 90%; background: linear-gradient(180deg, #c8a0c8, #906098); }
.book.b6  { width: 18px; height: 65%; background: linear-gradient(180deg, #e8a878, #b87848); }
.book.b7  { width: 22px; height: 82%; background: linear-gradient(180deg, #88b0c0, #588098); }
.book.b8  { width: 20px; height: 95%; background: linear-gradient(180deg, #d4a878, #a47840); }
.book.b9  { width: 18px; height: 70%; background: linear-gradient(180deg, #b8c8d0, #7890a0); }
.book.b10 { width: 22px; height: 80%; background: linear-gradient(180deg, #e89898, #b06868); }
.book.b11 { width: 20px; height: 76%; background: linear-gradient(180deg, #88c8a8, #58a080); }
.book.b12 { width: 18px; height: 88%; background: linear-gradient(180deg, #c8b888, #a09060); }
.book.lean-l { transform: rotate(-8deg) translateY(2px); }
.book.lean-r { transform: rotate(6deg) translateY(1px); }
.book.short { height: 50% !important; }
.book.tall  { height: 100% !important; }
.book.flat  { width: 70px; height: 18%; align-self: flex-start; margin-top: auto; border-radius: 1px; }
.book.flat::after { display: none; }

/* 书架上的小物件 */
.shelf-deco {
  position: absolute; z-index: 3;
  font-size: 24px; line-height: 1;
  filter: drop-shadow(0 2px 2px rgba(0,0,0,.4));
}
.shelf-deco.vase { right: 26px; top: 6%; }
.shelf-deco.plant { left: 30px; top: 39%; }
.shelf-deco.cup  { right: 32px; top: 72%; }
.shelf-deco.frame { left: 32px; bottom: 4%; }

/* ── 沙发(左下) ── */
.sofa {
  position: absolute;
  left: 4%; bottom: 6%;
  width: 380px; height: 160px;
  z-index: 4;
}
.sofa-shadow {
  position: absolute; left: 8px; right: 8px; bottom: -8px; height: 16px;
  background: radial-gradient(ellipse, rgba(0,0,0,.35), transparent 70%);
}
.sofa-back {
  position: absolute;
  left: 0; right: 0; top: 0;
  height: 90px;
  background: linear-gradient(180deg, #d8956d 0%, #b87050 60%, #a05a40 100%);
  border-radius: 22px 22px 8px 8px;
  box-shadow:
    inset 0 -10px rgba(80,30,15,.25),
    inset 0 2px 0 rgba(255,255,255,.15);
}
.sofa-base {
  position: absolute;
  left: 0; right: 0; bottom: 0;
  height: 60px;
  background: linear-gradient(180deg, #b87555 0%, #7a4530 100%);
  border-radius: 8px;
  box-shadow:
    inset 0 -4px rgba(0,0,0,.3),
    0 6px 10px rgba(0,0,0,.25);
}
.sofa-cushion {
  position: absolute;
  border-radius: 10px;
  box-shadow:
    inset 0 -3px rgba(80,40,20,.2),
    0 2px 4px rgba(0,0,0,.2);
}
.sofa-cushion.back-l { left: 36px;  top: 12px;  width: 96px; height: 60px; background: linear-gradient(180deg, #e0a07e, #b87050); }
.sofa-cushion.back-c { left: 50%;   top: 12px;  width: 96px; height: 60px; transform: translateX(-50%); background: linear-gradient(180deg, #e0a07e, #b87050); }
.sofa-cushion.back-r { right: 36px; top: 12px;  width: 96px; height: 60px; background: linear-gradient(180deg, #e0a07e, #b87050); }
.sofa-cushion.seat-l { left: 36px;  bottom: 12px; width: 96px; height: 38px; background: linear-gradient(180deg, #e8a880, #c08060); }
.sofa-cushion.seat-c { left: 50%;   bottom: 12px; width: 96px; height: 38px; transform: translateX(-50%); background: linear-gradient(180deg, #e8a880, #c08060); }
.sofa-cushion.seat-r { right: 36px; bottom: 12px; width: 96px; height: 38px; background: linear-gradient(180deg, #e8a880, #c08060); }
.sofa-arm {
  position: absolute; bottom: 0;
  width: 30px; height: 110px;
  background: linear-gradient(180deg, #c8855e, #8a4830);
  border-radius: 12px 12px 4px 4px;
  box-shadow: inset -2px 0 rgba(80,30,15,.2), 0 2px 4px rgba(0,0,0,.2);
}
.sofa-arm.l { left: 0; }
.sofa-arm.r { right: 0; }
.sofa-leg {
  position: absolute; bottom: -6px;
  width: 10px; height: 8px;
  background: #3a1d10;
  border-radius: 1px;
}
.sofa-leg.l1 { left: 12px; }
.sofa-leg.l2 { left: 50%; transform: translateX(-50%); }
.sofa-leg.r1 { right: 12px; }
.sofa-pillow {
  position: absolute;
  width: 36px; height: 28px;
  background: linear-gradient(135deg, #8fd4a8 0%, #5fa888 100%);
  border-radius: 6px;
  box-shadow:
    inset 0 -2px rgba(0,0,0,.2),
    0 2px 3px rgba(0,0,0,.2);
  z-index: 2;
}
.sofa-pillow.p1 { left: 30px;  bottom: 30px; transform: rotate(-8deg); }
.sofa-pillow.p2 { right: 30px; bottom: 30px; transform: rotate(8deg); background: linear-gradient(135deg, #e2b04a 0%, #b08830 100%); }
.sofa-throw {
  position: absolute;
  right: 30px; top: 8px;
  width: 80px; height: 40px;
  background: linear-gradient(135deg, #c8a0c8 0%, #906098 100%);
  border-radius: 4px 4px 20px 4px;
  transform: rotate(-12deg);
  box-shadow: inset 0 -2px rgba(0,0,0,.2);
  z-index: 2;
}

/* ── 电脑桌(中后方) ── */
.desk-main {
  position: absolute;
  left: 50%; top: 54%;
  transform: translateX(-50%);
  width: 360px; height: 120px;
  z-index: 3;
}
.desk-monitor {
  position: absolute;
  left: 50%; top: -156px;
  transform: translateX(-50%);
  width: 200px; height: 130px;
  z-index: 5;
}
.monitor-body {
  position: absolute; inset: 0;
  background: linear-gradient(180deg, #2a2f38, #1a1f28);
  border-radius: 10px 10px 4px 4px;
  padding: 6px;
  box-shadow:
    0 8px 16px var(--shadow-deep),
    inset 0 0 0 2px #0a0e14;
}
.monitor-screen {
  position: absolute; inset: 6px;
  background: #0a0e14;
  border-radius: 4px;
  overflow: hidden;
}
.monitor-screen::after {
  content: ""; position: absolute; inset: 0;
  background: repeating-linear-gradient(0deg, rgba(255,255,255,.03) 0 1px, transparent 1px 3px);
  pointer-events: none;
}
.monitor-cursor {
  position: absolute;
  left: 50%; top: 60%;
  width: 8px; height: 14px;
  background: #7ee0c0;
  transform: translateX(-50%);
  animation: cursor-blink 1.1s steps(2) infinite;
  box-shadow: 0 0 6px rgba(126,224,192,.6);
}
@keyframes cursor-blink { 50% { opacity: 0; } }
.monitor-code {
  position: absolute; left: 8px; top: 8px;
  font: 9px/1.5 "Courier New", monospace;
  color: #7ee0c0;
  z-index: 2;
  white-space: pre;
}
.monitor-code .kw { color: var(--gold); }
.monitor-code .num { color: var(--coral); }
.monitor-stand {
  position: absolute;
  left: 50%; top: 100%;
  transform: translateX(-50%);
  width: 36px; height: 18px;
  background: linear-gradient(180deg, #2a2f38, #1a1f28);
  border-radius: 0 0 4px 4px;
}
.monitor-base {
  position: absolute;
  left: 50%; top: calc(100% + 18px);
  transform: translateX(-50%);
  width: 100px; height: 8px;
  background: linear-gradient(180deg, #2a2f38, #1a1f28);
  border-radius: 4px;
}
.monitor-glow {
  position: absolute; inset: -10px;
  background: radial-gradient(ellipse, rgba(126,224,192,.15), transparent 60%);
  pointer-events: none;
  z-index: -1;
}
.desk-top {
  position: absolute;
  left: -20px; right: -20px; top: 0;
  height: 16px;
  background: linear-gradient(180deg, #8a5a3a 0%, #5a3320 100%);
  border-radius: 6px;
  box-shadow:
    0 6px 10px rgba(0,0,0,.3),
    inset 0 2px 0 rgba(255,255,255,.1);
}
.desk-body {
  position: absolute;
  left: 0; right: 0; top: 16px; bottom: 0;
  background: linear-gradient(180deg, #6a4028, #4a2818);
  border-radius: 0 0 4px 4px;
  box-shadow: 0 16px 24px var(--shadow-deep);
}
.desk-keyboard {
  position: absolute;
  left: 50%; top: -22px;
  transform: translateX(-50%);
  width: 170px; height: 22px;
  background: linear-gradient(180deg, #3a3f48, #1e2228);
  border: 2px solid #1a1f28;
  border-radius: 4px;
  z-index: 6;
  box-shadow: 0 2px 4px rgba(0,0,0,.3);
}
.desk-keyboard::before {
  content: ""; position: absolute; left: 6px; right: 6px; top: 4px; bottom: 4px;
  background: repeating-linear-gradient(90deg, rgba(255,255,255,.05) 0 1px, transparent 1px 4px);
}
.desk-mouse {
  position: absolute;
  right: 40px; top: -20px;
  width: 18px; height: 26px;
  background: linear-gradient(180deg, #3a3f48, #1e2228);
  border: 2px solid #1a1f28;
  border-radius: 8px 8px 10px 10px;
  z-index: 6;
}
.desk-mug {
  position: absolute;
  left: 26px; top: -30px;
  width: 30px; height: 30px;
  z-index: 6;
}
.mug-body {
  position: absolute; inset: 0;
  background: linear-gradient(180deg, #fbf3e2, #d4a878);
  border-radius: 4px 4px 8px 8px;
  box-shadow: inset -4px 0 rgba(120,80,40,.2);
}
.mug-handle {
  position: absolute; right: -8px; top: 6px;
  width: 8px; height: 12px;
  border: 3px solid #d4a878;
  border-left: none;
  border-radius: 0 6px 6px 0;
}
.mug-steam {
  position: absolute;
  left: 50%; top: -10px;
  width: 3px; height: 8px;
  background: rgba(255,255,255,.6);
  border-radius: 2px;
  transform: translateX(-50%);
  animation: steam 2.4s ease-in-out infinite;
}
.mug-steam.s2 { left: 60%; animation-delay: .8s; height: 6px; }
.mug-steam.s3 { left: 40%; animation-delay: 1.4s; height: 10px; }
@keyframes steam {
  0%   { transform: translate(-50%, 0) scaleY(1); opacity: 0; }
  30%  { opacity: .7; }
  100% { transform: translate(-50%, -18px) scaleY(1.4); opacity: 0; }
}
.desk-notebook {
  position: absolute;
  right: 28px; top: 36px;
  width: 50px; height: 16px;
  background: linear-gradient(180deg, #6a7eb8, #4a6098);
  border-radius: 2px;
  z-index: 4;
  transform: rotate(8deg);
  box-shadow: 0 2px 3px rgba(0,0,0,.2);
}
.desk-legs {
  position: absolute; left: 0; right: 0; bottom: -36px; height: 36px;
}
.desk-leg {
  position: absolute; bottom: 0;
  width: 10px; height: 36px;
  background: linear-gradient(180deg, #4a2818, #2a1608);
  border-radius: 1px;
}
.desk-leg.l { left: 16px; }
.desk-leg.r { right: 16px; }

/* ── 办公椅(在电脑桌前) ── */
.chair {
  position: absolute;
  left: 50%; top: 60%;
  transform: translateX(-50%);
  width: 100px; height: 110px;
  z-index: 4;
}
.chair-back {
  position: absolute;
  left: 14px; right: 14px; top: 0;
  height: 60px;
  background: linear-gradient(180deg, #2a2f38, #1a1f28);
  border-radius: 16px 16px 4px 4px;
  box-shadow:
    inset 0 -3px rgba(0,0,0,.3),
    0 2px 4px rgba(0,0,0,.2);
}
.chair-seat {
  position: absolute;
  left: 0; right: 0; top: 56px;
  height: 22px;
  background: linear-gradient(180deg, #3a3f48, #1e2228);
  border-radius: 8px;
  box-shadow: 0 4px 6px rgba(0,0,0,.3);
}
.chair-base {
  position: absolute;
  left: 50%; top: 78px;
  transform: translateX(-50%);
  width: 8px; height: 18px;
  background: #1a1f28;
}
.chair-wheel {
  position: absolute; top: 96px;
  width: 10px; height: 10px;
  background: #1a1f28;
  border-radius: 50%;
  box-shadow: 0 1px 2px rgba(0,0,0,.5);
}
.chair-wheel.w1 { left: 16px; }
.chair-wheel.w2 { left: 50%; transform: translateX(-50%); }
.chair-wheel.w3 { right: 16px; }

/* ── 写字桌(右下) ── */
.desk-write {
  position: absolute;
  right: 4%; bottom: 6%;
  width: 320px; height: 150px;
  z-index: 3;
}
.dw-top {
  position: absolute;
  left: -10px; right: -10px; top: 0;
  height: 40px;
  background: linear-gradient(180deg, #b08858 0%, #7a5230 100%);
  border-radius: 4px;
  box-shadow: 0 4px 8px rgba(0,0,0,.3);
  z-index: 8;
}
.dw-body {
  position: absolute;
  left: 0; right: 0; top: 40px; bottom: 0;
  background: linear-gradient(180deg, #8a5a3a, #5a3320);
  border-radius: 0 0 4px 4px;
  box-shadow: 0 16px 24px var(--shadow-deep);
}
.dw-paper {
  position: absolute;
  left: 30px; top: -10px;
  width: 110px; height: 76px;
  background: linear-gradient(180deg, #fdf6e3, #f0e2c0);
  border: 1px solid rgba(120,80,40,.25);
  border-radius: 2px;
  box-shadow: 0 3px 6px rgba(0,0,0,.2);
  transform: rotate(-2deg);
  z-index: 12;
}
.dw-paper::before {
  content: ""; position: absolute; left: 10px; right: 10px; top: 14px; height: 1px;
  background: rgba(80,60,40,.25);
  box-shadow:
    0 10px 0 rgba(80,60,40,.25),
    0 20px 0 rgba(80,60,40,.2),
    0 30px 0 rgba(80,60,40,.25),
    0 40px 0 rgba(80,60,40,.2),
    0 50px 0 rgba(80,60,40,.25);
}
.dw-pen {
  position: absolute;
  left: 48px; top: -18px;
  width: 6px; height: 50px;
  background: linear-gradient(180deg, #2d3e58, #1a2538);
  border-radius: 3px 3px 1px 1px;
  transform: rotate(18deg);
  z-index: 13;
  box-shadow: 0 2px 4px rgba(0,0,0,.2);
}
.dw-pen::before {
  content: ""; position: absolute;
  left: 50%; top: -5px; transform: translateX(-50%);
  width: 0; height: 0;
  border-left: 3px solid transparent;
  border-right: 3px solid transparent;
  border-bottom: 7px solid #2d3e58;
}
.dw-pen::after {
  content: ""; position: absolute;
  left: 50%; bottom: 0; transform: translateX(-50%);
  width: 2px; height: 4px;
  background: var(--coral);
}
.dw-lamp {
  position: absolute;
  right: 20px; top: -68px;
  width: 8px; height: 60px;
  background: linear-gradient(180deg, #3a2518, #2a1810);
  border-radius: 2px;
  z-index: 3;
}
.dw-lamp-head {
  position: absolute;
  left: 50%; top: -14px;
  transform: translateX(-50%) rotate(20deg);
  width: 44px; height: 22px;
  background: radial-gradient(ellipse at 50% 32%, #fce9b0, #d4954a);
  border-radius: 50% 50% 30% 30%;
  box-shadow:
    0 0 30px rgba(252,233,176,.8),
    0 -2px 0 6px rgba(252,233,176,.2);
  z-index: 3;
}
.dw-lamp-glow {
  position: absolute;
  left: 50%; top: 8px;
  transform: translateX(-50%);
  width: 120px; height: 80px;
  background: radial-gradient(ellipse at 50% 0, rgba(255,235,180,.35), transparent 70%);
  pointer-events: none;
  z-index: -1;
}
.dw-book-stack {
  position: absolute;
  right: 80px; top: -6px;
  width: 50px; height: 16px;
  z-index: 3;
}
.dw-book-stack span {
  position: absolute; left: 0; right: 0;
  height: 6px; border-radius: 1px;
  box-shadow: 0 1px 2px rgba(0,0,0,.2);
}
.dw-book-stack span:nth-child(1) { bottom: 0; background: linear-gradient(90deg, #6fc6a0, #4a9070); }
.dw-book-stack span:nth-child(2) { bottom: 6px; background: linear-gradient(90deg, #e2b04a, #b08830); }
.dw-book-stack span:nth-child(3) { bottom: 12px; background: linear-gradient(90deg, #a78ad4, #7a5ab0); }
.dw-legs {
  position: absolute; left: 0; right: 0; bottom: -32px; height: 32px;
}
.dw-leg {
  position: absolute; bottom: 0;
  width: 10px; height: 32px;
  background: linear-gradient(180deg, #4a2818, #2a1608);
  border-radius: 1px;
}
.dw-leg.l { left: 12px; }
.dw-leg.r { right: 12px; }

/* ── 装饰: 地毯 ── */
.rug {
  position: absolute;
  left: 50%; bottom: 4%;
  transform: translateX(-50%);
  width: 600px; height: 100px;
  background:
    repeating-linear-gradient(90deg, rgba(0,0,0,.08) 0 2px, transparent 2px 24px),
    radial-gradient(ellipse, #c8755a 0%, #8a4530 100%);
  border-radius: 50%;
  box-shadow: 0 6px 12px rgba(0,0,0,.3);
  z-index: 1;
  opacity: .85;
}

/* ── 装饰: 盆栽(角落) ── */
.plant {
  position: absolute;
  z-index: 3;
}
.plant-corner {
  left: 26%; bottom: 30%;
  width: 80px; height: 130px;
}
.plant-corner .pot {
  position: absolute; left: 50%; bottom: 0;
  transform: translateX(-50%);
  width: 50px; height: 40px;
  background: linear-gradient(180deg, #c8755a, #8a4530);
  border-radius: 4px 4px 12px 12px;
  box-shadow:
    inset -4px 0 rgba(0,0,0,.2),
    0 4px 6px rgba(0,0,0,.3);
}
.plant-corner .leaves {
  position: absolute; left: 50%; bottom: 30px;
  transform: translateX(-50%);
  width: 70px; height: 100px;
}
.plant-corner .leaf {
  position: absolute; bottom: 0;
  width: 22px; height: 60px;
  background: linear-gradient(180deg, #6fa860 0%, #3a7838 100%);
  border-radius: 50% 50% 20% 20%;
  transform-origin: bottom center;
  box-shadow: inset -2px 0 rgba(0,0,0,.2);
}
.plant-corner .leaf:nth-child(1) { left: 0;   transform: rotate(-30deg); height: 70px; }
.plant-corner .leaf:nth-child(2) { left: 12px; transform: rotate(-12deg); height: 85px; }
.plant-corner .leaf:nth-child(3) { left: 24px; transform: rotate(8deg); height: 95px; }
.plant-corner .leaf:nth-child(4) { left: 36px; transform: rotate(22deg); height: 80px; }
.plant-corner .leaf:nth-child(5) { left: 48px; transform: rotate(38deg); height: 65px; }
.plant-corner .leaves {
  animation: plant-sway 4s ease-in-out infinite;
  transform-origin: bottom center;
}
@keyframes plant-sway {
  0%, 100% { transform: translateX(-50%) rotate(-2deg); }
  50%      { transform: translateX(-50%) rotate(2deg); }
}

/* ── 装饰: 落地灯 ── */
.floor-lamp {
  position: absolute;
  right: 2%; bottom: 4%;
  width: 70px; height: 240px;
  z-index: 2;
  transform: translateX(-30px);
}
.fl-base {
  position: absolute; left: 50%; bottom: 0;
  transform: translateX(-50%);
  width: 50px; height: 8px;
  background: #3a1d10;
  border-radius: 50%;
}
.fl-pole {
  position: absolute; left: 50%; bottom: 6px;
  transform: translateX(-50%);
  width: 4px; height: 200px;
  background: linear-gradient(180deg, #5a3320, #3a1d10);
}
.fl-shade {
  position: absolute; left: 50%; top: 0;
  transform: translateX(-50%);
  width: 50px; height: 60px;
  background: radial-gradient(ellipse at 50% 30%, #fce9b0, #d4954a);
  border-radius: 50% 50% 10% 10%;
  box-shadow: 0 0 30px rgba(252,233,176,.6);
  z-index: 2;
}
.fl-glow {
  position: absolute; left: 50%; top: 50px;
  transform: translateX(-50%);
  width: 220px; height: 200px;
  background: radial-gradient(ellipse at 50% 0, rgba(255,235,180,.3), transparent 70%);
  pointer-events: none;
  z-index: -1;
}

/* ── 装饰: 画框(墙上沙发上方) ── */
.picture {
  position: absolute;
  left: 8%; top: 18%;
  width: 90px; height: 110px;
  border: 6px solid #5a3320;
  border-radius: 4px;
  background: linear-gradient(180deg, #f4d4a8 0%, #e8b894 100%);
  z-index: 2;
  box-shadow: 0 8px 16px var(--shadow);
}
.picture::before {
  content: ""; position: absolute; left: 8px; right: 8px; top: 8px; bottom: 8px;
  background:
    radial-gradient(circle at 30% 30%, #e8826e, transparent 50%),
    radial-gradient(circle at 70% 60%, #6fc6a0, transparent 50%),
    linear-gradient(180deg, #f6c9a8, #e89b78);
  border-radius: 2px;
}

/* ── 角色: 义子(动漫比例少女) ── */
.xizi {
  position: absolute;
  left: 50%; bottom: 6%;
  width: 130px; height: 200px;
  margin-left: -65px;
  z-index: 6;
  transition:
    left 1s var(--ease-out),
    bottom 1s var(--ease-out),
    transform 1s var(--ease-out);
  animation: xizi-breathe 3.2s ease-in-out infinite;
}
@keyframes xizi-breathe {
  0%, 100% { transform: translateY(0); }
  50%      { transform: translateY(-3px); }
}

/* 头/发 */
.xz-hair-back {
  position: absolute;
  left: 16px; top: 16px;
  width: 98px; height: 130px;
  background: linear-gradient(180deg, #2a3a4e 0%, #1a2030 100%);
  border-radius: 36px 36px 18px 18px;
  z-index: 1;
  clip-path: polygon(
    0% 0%, 100% 0%, 96% 30%, 92% 60%, 96% 100%, 80% 96%,
    70% 100%, 60% 96%, 50% 100%, 40% 96%, 30% 100%, 20% 96%,
    4% 100%, 8% 60%, 4% 30%
  );
}
.xz-head {
  position: absolute;
  left: 28px; top: 18px;
  width: 74px; height: 70px;
  border-radius: 50% 50% 46% 48%;
  background: linear-gradient(155deg, #ffe8d0 0%, #f4d4b0 100%);
  box-shadow:
    inset -6px -8px rgba(190,120,90,.18),
    inset 4px 4px rgba(255,255,255,.3);
  z-index: 3;
}
.xz-hair {
  position: absolute;
  left: 22px; top: 6px;
  width: 86px; height: 50px;
  background: linear-gradient(180deg, #3a4a5e 0%, #1a2030 100%);
  border-radius: 40px 42px 8px 8px;
  z-index: 4;
  clip-path: polygon(
    0 0, 100% 0, 96% 70%, 88% 100%, 76% 80%, 64% 100%, 50% 70%,
    36% 100%, 24% 80%, 12% 100%, 4% 70%
  );
}
.xz-bangs {
  position: absolute;
  left: 28px; top: 26px;
  width: 74px; height: 22px;
  background: linear-gradient(180deg, #2a3a4e, #1a2030);
  border-radius: 0 0 50% 50%;
  z-index: 4;
  clip-path: polygon(
    0 0, 100% 0, 92% 60%, 80% 100%, 60% 70%, 50% 100%, 40% 70%, 20% 100%, 8% 60%
  );
}
.xz-eye {
  position: absolute; top: 48px;
  width: 10px; height: 14px;
  border-radius: 50%;
  background: #1a2530;
  z-index: 5;
}
.xz-eye.l { left: 46px; }
.xz-eye.r { left: 74px; }
.xz-eye::after {
  content: ""; position: absolute; left: 2px; top: 2px;
  width: 3px; height: 4px;
  border-radius: 50%;
  background: #fff;
}
.xz-brow {
  position: absolute; top: 42px;
  width: 14px; height: 3px;
  border-radius: 2px;
  background: #4a3a30;
  z-index: 5;
}
.xz-brow.l { left: 44px; }
.xz-brow.r { left: 72px; }
.xz-cheek {
  position: absolute; top: 58px;
  width: 10px; height: 6px;
  border-radius: 50%;
  background: rgba(232,130,110,.5);
  z-index: 4;
}
.xz-cheek.l { left: 38px; }
.xz-cheek.r { left: 82px; }
.xz-mouth {
  position: absolute; left: 60px; top: 68px;
  width: 12px; height: 5px;
  border-bottom: 2px solid #a05848;
  border-radius: 0 0 50% 50%;
  z-index: 5;
  transition: border-color .4s;
}
/* 围裙/连衣裙 */
.xz-dress {
  position: absolute;
  left: 26px; top: 86px;
  width: 78px; height: 88px;
  background: linear-gradient(140deg, #f4d4a8 0%, #d4954a 100%);
  border-radius: 20px 20px 30px 30px;
  z-index: 2;
  transition: background .6s var(--ease-out);
  box-shadow: inset 0 -8px rgba(0,0,0,.12);
}
.xz-dress::before {
  content: ""; position: absolute;
  left: 50%; top: 6px;
  transform: translateX(-50%);
  width: 30px; height: 14px;
  background: rgba(255,255,255,.5);
  border-radius: 4px;
}
/* 手臂 */
.xz-arm {
  position: absolute; top: 96px;
  width: 18px; height: 60px;
  border-radius: 10px;
  background: linear-gradient(180deg, #f4d4b0 0%, #d4a878 100%);
  transform-origin: top center;
  z-index: 3;
  transition: transform .6s var(--ease-out);
}
.xz-arm.l { left: 18px; transform: rotate(8deg); }
.xz-arm.r { left: 94px; transform: rotate(-8deg); }
/* 腿 */
.xz-leg {
  position: absolute; top: 162px;
  width: 22px; height: 36px;
  border-radius: 10px;
  background: linear-gradient(180deg, #3a4a5e, #2a3a4e);
  z-index: 2;
  transition: transform .6s var(--ease-out);
}
.xz-leg.l { left: 40px; }
.xz-leg.r { left: 68px; }
/* 道具(书/平板) */
.xz-prop {
  position: absolute;
  left: 90px; top: 124px;
  width: 36px; height: 26px;
  border-radius: 3px;
  background: var(--paper);
  border: 2px solid #6b4a34;
  transform: rotate(-12deg);
  z-index: 4;
  opacity: 0;
  transition: opacity .5s, transform .5s;
}
.xz-prop::before {
  content: ""; position: absolute; left: 4px; right: 4px; top: 4px; height: 1px;
  background: rgba(80,60,40,.4);
  box-shadow: 0 6px 0 rgba(80,60,40,.3), 0 12px 0 rgba(80,60,40,.4);
}

/* 想法气泡 */
.thoughts {
  position: absolute;
  left: 50%; top: -50px;
  transform: translateX(40px);
  z-index: 8;
  opacity: 0;
  transition: opacity .6s, transform .6s;
}
.bubble {
  position: absolute; border-radius: 50%;
  background: rgba(255,250,240,.92);
  border: 3px solid rgba(45,60,75,.25);
  box-shadow: 0 6px 14px rgba(20,36,52,.2);
}
.bubble.b1 { width: 80px; height: 50px; left: 0; top: 0; animation: bob 2.4s ease-in-out infinite; }
.bubble.b2 { width: 18px; height: 18px; left: -10px; top: 46px; animation: bob 2.4s ease-in-out .3s infinite; }
.bubble.b3 { width: 10px; height: 10px; left: -22px; top: 60px; animation: bob 2.4s ease-in-out .6s infinite; }
@keyframes bob { 50% { transform: translateY(-6px); } }
.glyph {
  position: absolute; left: 30px; top: 8px;
  font-size: 28px; font-weight: 800;
  color: var(--trim);
  animation: glyph-pulse 1.8s ease-in-out infinite;
}
@keyframes glyph-pulse {
  50% { transform: scale(1.2); opacity: 1; }
}

/* ═══════════════════════════════════════
   4 个角色动作状态
   ═══════════════════════════════════════ */

/* 默认: 站立 */
.xz-default {}

/* 整理书架: 站在书架前, 伸手够书 */
body[data-action="organize"] .xizi {
  left: 12%; margin-left: -65px;
  bottom: 30%;
  transform: scale(1) translateY(0);
}
body[data-action="organize"] .xz-dress {
  background: linear-gradient(140deg, #f4d4a8 0%, #d4954a 100%);
}
body[data-action="organize"] .xz-arm.l { transform: rotate(-65deg) translate(-6px, -4px); animation: arm-reach 1.1s ease-in-out infinite; }
body[data-action="organize"] .xz-arm.r { transform: rotate(20deg); }
body[data-action="organize"] .xz-prop { opacity: 1; transform: rotate(8deg) translate(-10px, -4px); }
body[data-action="organize"] .thoughts { opacity: 1; transform: translateX(20px); }

/* 坐沙发休息 */
body[data-action="rest"] .xizi {
  left: 17%; margin-left: -40px;
  bottom: 10%;
  transform: scale(.88);
}
body[data-action="rest"] .xz-dress {
  background: linear-gradient(140deg, #6fc6a0 0%, #4a9070 100%);
}
body[data-action="rest"] .xz-arm.l { transform: rotate(-30deg) translate(4px, 14px); }
body[data-action="rest"] .xz-arm.r { transform: rotate(30deg) translate(-4px, 14px); }
body[data-action="rest"] .xz-leg.l { transform: rotate(-22deg) translateY(-4px); }
body[data-action="rest"] .xz-leg.r { transform: rotate(22deg) translateY(-4px); }
body[data-action="rest"] .xz-hair-back,
body[data-action="rest"] .xz-hair,
body[data-action="rest"] .xz-bangs { transform: scaleY(.85) translateY(8px); transform-origin: top; }
body[data-action="rest"] .xz-prop { opacity: 0; }
body[data-action="rest"] .xz-eye { height: 6px; border-radius: 50%; animation: rest-eyes 3s ease-in-out infinite; }
@keyframes rest-eyes {
  0%, 40%, 100% { height: 14px; }
  50%, 90%      { height: 4px; }
}

/* 在电脑桌前工作 */
body[data-action="work"] .xizi {
  left: 50%; margin-left: -65px;
  bottom: 28%;
  transform: scale(.92);
}
body[data-action="work"] .xz-dress {
  background: linear-gradient(140deg, #6a7eb8 0%, #4a6098 100%);
}
body[data-action="work"] .xz-arm.l { transform: rotate(35deg) translate(2px, 6px); animation: arm-type .55s ease-in-out infinite; }
body[data-action="work"] .xz-arm.r { transform: rotate(40deg) translate(-2px, 6px); animation: arm-type .55s ease-in-out .27s infinite; }
body[data-action="work"] .xz-prop { opacity: 0; }
body[data-action="work"] .xz-eye { animation: work-blink 4s ease-in-out infinite; }
@keyframes work-blink { 95% { height: 14px; } 97% { height: 2px; } 100% { height: 14px; } }

/* 在写字桌前进行任务审核 */
body[data-action="write"] .xizi {
  left: 86%; margin-left: -65px;
  bottom: 20%;
  transform: scale(.92);
  z-index: 2;
}
body[data-action="write"] .xz-dress {
  background: linear-gradient(140deg, #a78ad4 0%, #7a5ab0 100%);
}
body[data-action="write"] .xz-arm.l { transform: rotate(45deg) translate(0, 8px); animation: arm-write .8s ease-in-out infinite; z-index: 9; }
body[data-action="write"] .xz-arm.r { transform: rotate(8deg); z-index: 9; }
body[data-action="write"] .xz-prop { opacity: 0; }
body[data-action="write"] .xz-mouth { width: 10px; }

@keyframes arm-reach {
  0%, 100% { transform: rotate(-55deg) translate(-4px, -2px); }
  50%      { transform: rotate(-72deg) translate(-8px, -8px); }
}
@keyframes arm-type {
  0%, 100% { transform: rotate(30deg) translate(0, 0); }
  50%      { transform: rotate(45deg) translate(0, 4px); }
}
@keyframes arm-write {
  0%, 100% { transform: rotate(40deg) translate(0, 4px); }
  50%      { transform: rotate(60deg) translate(2px, 12px); }
}

/* ── 错误状态(皱眉) ── */
body[data-has-errors="true"] .xz-mouth { border-bottom-color: #b84040; width: 16px; }
body[data-has-errors="true"] .xz-brow.l { transform: rotate(-12deg) translateY(-1px); }
body[data-has-errors="true"] .xz-brow.r { transform: rotate(12deg) translateY(-1px); }

/* ── 任务触发光效 ── */
.action-glow {
  position: absolute; inset: -20px;
  border-radius: 50%;
  pointer-events: none;
  z-index: 5;
  opacity: 0;
  transition: opacity .8s;
}
body[data-action="organize"] .shelf ~ .action-glow { background: radial-gradient(circle, var(--gold-g), transparent 70%); opacity: 1; }
body[data-action="rest"] .action-glow { background: radial-gradient(circle, rgba(111,198,160,.3), transparent 70%); opacity: 1; }
body[data-action="work"] .action-glow { background: radial-gradient(circle, var(--indigo-g), transparent 70%); opacity: 1; }
body[data-action="write"] .action-glow { background: radial-gradient(circle, var(--plum-g), transparent 70%); opacity: 1; }

/* ── 环境粒子 ── */
.particles {
  position: absolute; inset: 0;
  z-index: 7; pointer-events: none; overflow: hidden;
}
.particle {
  position: absolute; border-radius: 50%;
  pointer-events: none;
  animation: drift linear infinite;
}
.particle.dust {
  width: 2px; height: 2px;
  background: rgba(255,248,220,.6);
  animation-duration: 8s;
}
.particle.spark {
  width: 3px; height: 3px;
  background: var(--gold);
  box-shadow: 0 0 6px var(--gold-g);
}
.particle.mint {
  width: 2px; height: 4px;
  border-radius: 1px;
  background: var(--mint);
  box-shadow: 0 0 4px var(--mint-g);
}
@keyframes drift {
  0%   { transform: translate(0, 0); opacity: 0; }
  10%  { opacity: 1; }
  90%  { opacity: .3; }
  100% { transform: translate(30px, -50%); opacity: 0; }
}


/* ── 角色切换: data-character 控制显示哪个角色 ── */
.xizi, .xingzi { display: none; }
body[data-character="xizi"] .xizi { display: block; }
body[data-character="xingzi"] .xingzi { display: block; }
/* 默认显示西子(女) */
body:not([data-character]) .xizi,
body[data-character=""] .xizi { display: block; }

/* ── 角色: 星子(短发男) ── */
.xingzi {
  position: absolute;
  left: 50%; bottom: 6%;
  width: 140px; height: 210px;
  margin-left: -70px;
  z-index: 6;
  transition:
    left 1s var(--ease-out),
    bottom 1s var(--ease-out),
    transform 1s var(--ease-out);
  animation: xizi-breathe 3.2s ease-in-out infinite;
}

/* 星子 - 短发(后) */
.xs-hair-back {
  position: absolute;
  left: 16px; top: 14px;
  width: 108px; height: 80px;
  background: linear-gradient(180deg, #1a2530 0%, #0f1822 100%);
  border-radius: 22px 22px 10px 10px;
  z-index: 1;
}
/* 星子 - 脸 */
.xs-head {
  position: absolute;
  left: 28px; top: 16px;
  width: 78px; height: 74px;
  border-radius: 50% 50% 46% 48%;
  background: linear-gradient(155deg, #ffe8d0 0%, #f0c8a0 100%);
  box-shadow:
    inset -6px -8px rgba(170,110,70,.18),
    inset 4px 4px rgba(255,255,255,.3);
  z-index: 3;
}
/* 星子 - 短发(顶) */
.xs-hair {
  position: absolute;
  left: 22px; top: 4px;
  width: 90px; height: 32px;
  background: linear-gradient(180deg, #1e2d3a 0%, #121a24 100%);
  border-radius: 28px 30px 4px 4px;
  z-index: 4;
}
/* 星子 - 短刘海(碎发) */
.xs-bangs {
  position: absolute;
  left: 28px; top: 22px;
  width: 78px; height: 16px;
  background: linear-gradient(180deg, #1e2d3a, #121a24);
  border-radius: 2px 2px 50% 50%;
  z-index: 4;
  clip-path: polygon(
    0 0, 100% 0, 94% 60%, 84% 100%, 72% 70%, 60% 100%,
    48% 70%, 36% 100%, 24% 70%, 12% 100%, 4% 60%
  );
}
/* 星子 - 眼(略窄) */
.xs-eye {
  position: absolute; top: 46px;
  width: 9px; height: 12px;
  border-radius: 50%;
  background: #141e28;
  z-index: 5;
}
.xs-eye.l { left: 44px; }
.xs-eye.r { left: 74px; }
.xs-eye::after {
  content: ""; position: absolute; left: 1.5px; top: 1.5px;
  width: 2.5px; height: 3.5px;
  border-radius: 50%;
  background: #fff;
}
/* 星子 - 眉(略粗) */
.xs-brow {
  position: absolute; top: 40px;
  width: 15px; height: 3px;
  border-radius: 2px;
  background: #3a2820;
  z-index: 5;
}
.xs-brow.l { left: 42px; }
.xs-brow.r { left: 72px; }
/* 星子 - 腮红(更淡) */
.xs-cheek {
  position: absolute; top: 56px;
  width: 8px; height: 5px;
  border-radius: 50%;
  background: rgba(210,110,90,.35);
  z-index: 4;
}
.xs-cheek.l { left: 36px; }
.xs-cheek.r { left: 82px; }
/* 星子 - 嘴 */
.xs-mouth {
  position: absolute; left: 60px; top: 66px;
  width: 10px; height: 4px;
  border-bottom: 2px solid #904838;
  border-radius: 0 0 50% 50%;
  z-index: 5;
  transition: border-color .4s;
}
/* 星子 - 上衣 */
.xs-shirt {
  position: absolute;
  left: 24px; top: 84px;
  width: 86px; height: 50px;
  background: linear-gradient(140deg, #4a6088 0%, #2d4060 100%);
  border-radius: 16px 16px 6px 6px;
  z-index: 2;
  transition: background .6s var(--ease-out);
  box-shadow: inset 0 -4px rgba(0,0,0,.15);
}
.xs-shirt::before {
  content: ""; position: absolute;
  left: 50%; top: 12px;
  transform: translateX(-50%);
  width: 18px; height: 16px;
  background: rgba(255,255,255,.12);
  border-radius: 2px;
}
/* 星子 - 腰带 */
.xs-belt {
  position: absolute;
  left: 24px; top: 130px;
  width: 86px; height: 7px;
  background: linear-gradient(180deg, #5a4030, #3a2012);
  border-radius: 2px;
  z-index: 5;
}
.xs-belt::after {
  content: ""; position: absolute;
  left: 50%; top: 50%; transform: translate(-50%, -50%);
  width: 10px; height: 5px;
  background: #c8a868;
  border-radius: 2px;
  border: 1px solid #8a6830;
}
/* 星子 - 长裤 */
.xs-pants {
  position: absolute;
  left: 24px; top: 134px;
  width: 86px; height: 42px;
  background: linear-gradient(180deg, #2a3040 0%, #1a2030 100%);
  border-radius: 4px 4px 14px 14px;
  z-index: 2;
  box-shadow: inset 0 -6px rgba(0,0,0,.2);
}
/* 星子 - 手臂 */
.xs-arm {
  position: absolute; top: 96px;
  width: 18px; height: 58px;
  border-radius: 10px;
  background: linear-gradient(180deg, #f0c8a0 0%, #d4a070 100%);
  transform-origin: top center;
  z-index: 3;
  transition: transform .6s var(--ease-out);
}
.xs-arm.l { left: 16px; transform: rotate(8deg); }
.xs-arm.r { left: 100px; transform: rotate(-8deg); }
/* 星子 - 腿 */
.xs-leg {
  position: absolute; top: 170px;
  width: 24px; height: 38px;
  border-radius: 10px;
  background: linear-gradient(180deg, #2a3040, #1a2030);
  z-index: 1;
  transition: transform .6s var(--ease-out);
}
.xs-leg.l { left: 38px; }
.xs-leg.r { left: 68px; }
/* 星子 - 道具 */
.xs-prop {
  position: absolute;
  left: 94px; top: 118px;
  width: 36px; height: 26px;
  border-radius: 3px;
  background: var(--paper);
  border: 2px solid #6b4a34;
  transform: rotate(-12deg);
  z-index: 4;
  opacity: 0;
  transition: opacity .5s, transform .5s;
}
.xs-prop::before {
  content: ""; position: absolute; left: 4px; right: 4px; top: 4px; height: 1px;
  background: rgba(80,60,40,.4);
  box-shadow: 0 6px 0 rgba(80,60,40,.3), 0 12px 0 rgba(80,60,40,.4);
}

/* ═══════════════════════════════════════
   星子 4 个动作状态
   ═══════════════════════════════════════ */

/* 整理书架 */
body[data-action="organize"] .xingzi {
  left: 12%; margin-left: -70px;
  bottom: 29%;
  transform: scale(1) translateY(0);
}
body[data-action="organize"] .xs-shirt {
  background: linear-gradient(140deg, #f4d4a8 0%, #d4954a 100%);
}
body[data-action="organize"] .xs-arm.l { transform: rotate(-65deg) translate(-6px, -4px); animation: arm-reach 1.1s ease-in-out infinite; }
body[data-action="organize"] .xs-arm.r { transform: rotate(20deg); }
body[data-action="organize"] .xs-prop { opacity: 1; transform: rotate(8deg) translate(-10px, -4px); }
body[data-action="organize"] .xingzi .thoughts { opacity: 1; transform: translateX(20px); }

/* 坐沙发休息 */
body[data-action="rest"] .xingzi {
  left: 17%; margin-left: -40px;
  bottom: 10%;
  transform: scale(.88);
}
body[data-action="rest"] .xs-shirt {
  background: linear-gradient(140deg, #6fc6a0 0%, #4a9070 100%);
}
body[data-action="rest"] .xs-arm.l { transform: rotate(-30deg) translate(4px, 14px); }
body[data-action="rest"] .xs-arm.r { transform: rotate(30deg) translate(-4px, 14px); }
body[data-action="rest"] .xs-leg.l { transform: rotate(-22deg) translateY(-4px); }
body[data-action="rest"] .xs-leg.r { transform: rotate(22deg) translateY(-4px); }
body[data-action="rest"] .xs-hair-back,
body[data-action="rest"] .xs-hair,
body[data-action="rest"] .xs-bangs { transform: scaleY(.85) translateY(8px); transform-origin: top; }
body[data-action="rest"] .xs-prop { opacity: 0; }
body[data-action="rest"] .xs-eye { height: 5px; border-radius: 50%; animation: rest-eyes 3s ease-in-out infinite; }

/* 在电脑桌前工作 */
body[data-action="work"] .xingzi {
  left: 50%; margin-left: -70px;
  bottom: 27%;
  transform: scale(.92);
}
body[data-action="work"] .xs-shirt {
  background: linear-gradient(140deg, #6a7eb8 0%, #4a6098 100%);
}
body[data-action="work"] .xs-arm.l { transform: rotate(35deg) translate(2px, 6px); animation: arm-type .55s ease-in-out infinite; }
body[data-action="work"] .xs-arm.r { transform: rotate(40deg) translate(-2px, 6px); animation: arm-type .55s ease-in-out .27s infinite; }
body[data-action="work"] .xs-prop { opacity: 0; }
body[data-action="work"] .xs-eye { animation: work-blink 4s ease-in-out infinite; }

/* 在写字桌前任务审核 */
body[data-action="write"] .xingzi {
  left: 86%; margin-left: -70px;
  bottom: 19%;
  transform: scale(.92);
  z-index: 2;
}
body[data-action="write"] .xs-shirt {
  background: linear-gradient(140deg, #a78ad4 0%, #7a5ab0 100%);
}
body[data-action="write"] .xs-arm.l { transform: rotate(45deg) translate(0, 8px); animation: arm-write .8s ease-in-out infinite; z-index: 9; }
body[data-action="write"] .xs-arm.r { transform: rotate(8deg); z-index: 9; }
body[data-action="write"] .xs-prop { opacity: 0; }
body[data-action="write"] .xs-mouth { width: 8px; }

/* 星子 - 错误状态 */
body[data-has-errors="true"] .xs-mouth { border-bottom-color: #b84040; width: 14px; }
body[data-has-errors="true"] .xs-brow.l { transform: rotate(-12deg) translateY(-1px); }
body[data-has-errors="true"] .xs-brow.r { transform: rotate(12deg) translateY(-1px); }


/* ── 状态面板(右下方) ── */
.status {
  position: absolute;
  right: 18px; top: 18px;
  width: 360px; max-width: calc(100% - 36px);
  max-height: calc(100% - 120px);
  overflow-y: auto;
  padding: 16px 18px 14px;
  background: rgba(20, 14, 10, .82);
  border: 1px solid rgba(255,255,255,.08);
  border-radius: var(--radius-md);
  box-shadow: 0 18px 32px var(--shadow-deep);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  color: var(--text-primary);
  z-index: 10;
  font-size: 12px;
  line-height: 1.5;
  transition: transform .35s var(--ease-out), opacity .35s ease;
}
body[data-exec-window="false"] .status { border-color: rgba(167,138,212,.3); }
body[data-has-errors="true"] .status { border-color: rgba(232,130,110,.5); }

/* 状态面板收起 */
.status.collapsed {
  transform: translateX(calc(100% + 30px));
  opacity: 0;
  pointer-events: none;
}

/* 状态面板开关按钮 */
.status-toggle {
  position: absolute;
  right: 18px; top: 18px;
  width: 32px; height: 32px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,.12);
  background: rgba(20, 14, 10, .82);
  color: var(--text-secondary);
  font-size: 18px; font-weight: 700;
  line-height: 1;
  cursor: pointer;
  z-index: 11;
  display: flex; align-items: center; justify-content: center;
  transition: all .3s var(--ease-out);
  backdrop-filter: blur(10px);
}
.status-toggle:hover {
  background: rgba(40, 28, 20, .9);
  border-color: rgba(255,255,255,.25);
  color: var(--text-primary);
}
/* 面板收起时按钮位置 */
.status.collapsed ~ .status-toggle,
body:has(.status.collapsed) .status-toggle {
  right: 18px;
}
/* 面板展开时按钮在面板左侧 */
.status:not(.collapsed) ~ .status-toggle,
body:has(.status:not(.collapsed)) .status-toggle {
  right: calc(18px + 360px + 8px);
}
@media (max-width: 420px) {
  .status:not(.collapsed) ~ .status-toggle,
  body:has(.status:not(.collapsed)) .status-toggle {
    right: calc(18px + 100% - 36px + 8px);
  }
}

.status h1 {
  margin: 0 0 6px;
  font-size: 16px; font-weight: 700; line-height: 1.3;
  letter-spacing: -.01em;
}
.status-summary {
  margin: 0 0 14px;
  color: var(--text-secondary);
  font-size: 12px; line-height: 1.5;
}
.metrics {
  display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap;
}
.metric {
  flex: 1; min-width: 56px; text-align: center;
  padding: 8px 4px;
  border-radius: 8px;
  background: rgba(255,255,255,.05);
  border: 1px solid rgba(255,255,255,.05);
  transition: all .4s;
}
.metric-value {
  font-size: 20px; font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
.metric-label {
  font-size: 9.5px;
  color: var(--text-muted);
  margin-top: 2px;
  text-transform: uppercase; letter-spacing: .04em;
}
.metric.error .metric-value { color: var(--accent-red); }
.metric.ok .metric-value   { color: var(--accent-green); }
.metric.warn .metric-value { color: var(--accent-yellow); }
.task {
  display: grid; grid-template-columns: 10px 1fr auto; align-items: center;
  gap: 8px; min-height: 32px; padding: 6px 10px;
  border: 1px solid rgba(255,255,255,.06);
  border-radius: 8px;
  background: rgba(255,255,255,.04);
  font-size: 11.5px;
  transition: opacity .5s, max-height .5s;
}
.task.completed { opacity: 0; max-height: 0; overflow: hidden; margin: 0; padding: 0; border: none; }
.task-text { display: flex; flex-direction: column; min-width: 0; gap: 2px; }
.task-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-gov {
  font-size: 10px;
  color: rgba(244,228,188,.78);
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-dot {
  width: 10px; height: 10px; border-radius: 50%;
}
.task-dot.memory    { background: var(--gold); box-shadow: 0 0 6px var(--gold-g); }
.task-dot.learning  { background: var(--mint); box-shadow: 0 0 6px var(--mint-g); }
.task-dot.evolution { background: var(--coral); box-shadow: 0 0 6px var(--coral-g); }
.task-dot.planning  { background: var(--accent-blue); box-shadow: 0 0 6px var(--indigo-g); }
.task-dot.supervisor { background: var(--accent-green); box-shadow: 0 0 6px var(--mint-g); }
.task-dot.agent { background: var(--accent-purple); box-shadow: 0 0 6px var(--plum-g); }
.task-badge {
  min-width: 56px; text-align: center; padding: 3px 8px;
  border-radius: 99px; font-size: 10.5px;
  background: rgba(255,255,255,.06); color: var(--text-secondary);
}
.task-badge.approved   { background: rgba(111,198,160,.15); color: var(--mint); }
.task-badge.running    { background: rgba(106,158,232,.15); color: var(--accent-blue); }
.task-badge.completed  { background: rgba(255,255,255,.04); color: var(--text-muted); }
.task-badge.failed     { background: rgba(255,255,255,.04); color: var(--text-muted); }
.task-badge.cancelled  { background: rgba(255,255,255,.04); color: var(--text-muted); }
.task-badge.planned    { background: rgba(226,176,74,.12); color: var(--gold); }
.task-badge.queued     { background: rgba(226,176,74,.12); color: var(--gold); }
.task-badge.deferred   { background: rgba(226,176,74,.12); color: var(--gold); }

/* ── 任务卡片(浮动, 左上) ── */
.char-card {
  position: absolute;
  left: 18px; top: 18px;
  z-index: 20;
  background: rgba(20,14,10,.88);
  border: 1px solid rgba(255,255,255,.08);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  display: grid; grid-template-columns: 52px 1fr; gap: 10px; align-items: center;
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  min-width: 220px; max-width: 260px;
  color: var(--text-primary);
  box-shadow: 0 12px 24px var(--shadow-deep);
  transition: max-width .35s var(--ease-out), min-width .35s var(--ease-out), padding .35s var(--ease-out);
}
.char-toggle {
  position: absolute;
  top: 6px; right: 6px;
  width: 22px; height: 22px;
  border: 0;
  background: rgba(255,255,255,.08);
  color: var(--text-secondary);
  border-radius: 50%;
  cursor: pointer;
  font-size: 16px; font-weight: 700;
  line-height: 1;
  display: flex; align-items: center; justify-content: center;
  z-index: 5;
  transition: background .2s, color .2s, transform .2s;
  font-family: inherit;
  padding: 0;
}
.char-toggle:hover {
  background: rgba(255,255,255,.18);
  color: var(--text-primary);
  transform: scale(1.1);
}
.char-card.collapsed {
  min-width: 0; max-width: 0;
  padding: 12px 8px;
  grid-template-columns: 52px;
  overflow: hidden;
  border-color: rgba(255,255,255,.04);
}
.char-card.collapsed .char-info { display: none; }
.char-card.collapsed .char-toggle { right: 50%; transform: translateX(50%); }
.char-card.collapsed .char-toggle:hover { transform: translateX(50%) scale(1.1); }
.char-avatar {
  width: 52px; height: 64px; position: relative; margin: 0 auto;
}
.char-avatar .av-head {
  position: absolute; top: 4px; left: 8px;
  width: 36px; height: 34px;
  background: linear-gradient(155deg, #ffe8d0, #f4d4b0);
  border-radius: 50% 50% 46% 48%;
  box-shadow: inset -2px -2px rgba(190,120,90,.18);
}
.char-avatar .av-eyes {
  position: absolute; top: 18px; left: 12px; display: flex; gap: 10px;
}
.char-avatar .av-eye {
  width: 5px; height: 7px; background: #1a2530; border-radius: 50%;
}
.char-avatar .av-mouth {
  position: absolute; top: 30px; left: 50%; transform: translateX(-50%);
  width: 8px; height: 3px; border-bottom: 2px solid #a05848; border-radius: 0 0 50% 50%;
}
.char-avatar .av-hair {
  position: absolute; top: 0; left: 2px;
  width: 48px; height: 22px;
  background: linear-gradient(180deg, #3a4a5e, #1a2030);
  border-radius: 24px 24px 4px 4px;
  z-index: 5;
}
.char-avatar .av-body {
  position: absolute; bottom: 0; left: 10px;
  width: 32px; height: 28px;
  background: linear-gradient(140deg, #6fc6a0, #4a9070);
  border-radius: 10px 10px 8px 8px;
  box-shadow: inset 0 -2px rgba(0,0,0,.15);
  transition: background .6s;
}
body[data-action="organize"] .av-body { background: linear-gradient(140deg, #f4d4a8, #d4954a); }
body[data-action="rest"]     .av-body { background: linear-gradient(140deg, #6fc6a0, #4a9070); }
body[data-action="work"]     .av-body { background: linear-gradient(140deg, #6a7eb8, #4a6098); }
body[data-action="write"]    .av-body { background: linear-gradient(140deg, #a78ad4, #7a5ab0); }
.char-info { display: flex; flex-direction: column; gap: 2px; }
.char-name {
  font-size: 13px; font-weight: 700; color: var(--text-primary);
  display: flex; align-items: baseline; gap: 4px;
}
.ch-title {
  font-size: 10px; color: var(--accent-purple);
  font-weight: 500;
}
.char-lv {
  font-size: 11px; color: var(--text-secondary);
  display: flex; align-items: center; gap: 4px;
}
.lv-val { color: var(--accent-blue); font-weight: 700; }
.char-exp-wrap {
  height: 4px; background: rgba(255,255,255,.08); border-radius: 2px; overflow: hidden;
}
.char-exp-fill {
  height: 100%; width: 0;
  background: linear-gradient(90deg, var(--accent-purple), var(--accent-blue));
  border-radius: 2px;
  transition: width .5s ease;
}
.char-exp-text { font-size: 9.5px; color: var(--text-muted); }
.char-health { font-size: 10px; color: var(--text-secondary); display: flex; gap: 8px; }
.ch-hp { color: var(--accent-green); }
.ch-hp.warn { color: var(--accent-yellow); }
.ch-hp.danger { color: var(--accent-red); }

/* ── 动作切换器(底部) ── */
.action-bar {
  position: absolute;
  left: 50%; bottom: 18px;
  transform: translateX(-50%);
  display: flex; gap: 8px;
  z-index: 20;
  padding: 6px;
  background: rgba(20,14,10,.85);
  border: 1px solid rgba(255,255,255,.1);
  border-radius: 99px;
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  box-shadow: 0 12px 24px var(--shadow-deep);
}
.action-btn {
  position: relative;
  padding: 8px 16px;
  border: 0;
  background: transparent;
  color: var(--text-secondary);
  font: 12px/1 "Inter","PingFang SC",system-ui,sans-serif;
  font-weight: 600;
  border-radius: 99px;
  cursor: pointer;
  transition: all .35s var(--ease-out);
  display: flex; align-items: center; gap: 6px;
}
.action-btn:hover { background: rgba(255,255,255,.06); color: var(--text-primary); }
.action-btn.active {
  background: linear-gradient(135deg, var(--coral) 0%, #c66858 100%);
  color: #fff;
  box-shadow: 0 4px 12px var(--coral-g);
}
.action-btn[data-action="organize"].active { background: linear-gradient(135deg, #e2b04a, #b08830); box-shadow: 0 4px 12px var(--gold-g); }
.action-btn[data-action="rest"].active     { background: linear-gradient(135deg, var(--mint), #4a9070); box-shadow: 0 4px 12px var(--mint-g); }
.action-btn[data-action="work"].active     { background: linear-gradient(135deg, #6a7eb8, #4a6098); box-shadow: 0 4px 12px var(--indigo-g); }
.action-btn[data-action="write"].active    { background: linear-gradient(135deg, #a78ad4, #7a5ab0); box-shadow: 0 4px 12px var(--plum-g); }
.action-btn .ico { font-size: 14px; }

/* ── 响应式 ── */
@media (max-width: 1024px) {
  .status { width: 300px; font-size: 11px; }
  .char-card { min-width: 200px; }
  .action-bar { bottom: 12px; }
}
@media (max-width: 720px) {
  .status { display: none; }
  .char-card { top: 12px; left: 12px; min-width: 180px; }
  .action-bar { flex-wrap: wrap; max-width: calc(100% - 24px); justify-content: center; }
  .action-btn { padding: 6px 12px; font-size: 11px; }
}

/*━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  底部菜单系统  v1  ——  游戏风格 Dock + 滑出面板
  默认隐藏，鼠标触及下边缘自动弹出
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━*/

/* ── 底部触发区 ── */
.bottom-trigger {
  position: fixed; left: 0; right: 0; bottom: 0;
  height: 36px;
  z-index: 100;
  /* 透明不可见，仅用于 hover 检测 */
}

/* ── 底部 Dock 栏 ── */
.bottom-dock {
  position: fixed; left: 0; right: 0; bottom: 0;
  height: 48px;
  z-index: 99;
  display: flex; align-items: center; justify-content: center;
  gap: 2px;
  padding: 0 16px;
  background: linear-gradient(180deg,
    rgba(18,12,8,.94) 0%,
    rgba(28,18,12,.96) 100%);
  border-top: 1px solid rgba(255,255,255,.12);
  box-shadow:
    0 -4px 20px rgba(0,0,0,.5),
    0 -1px 0 rgba(255,255,255,.06),
    inset 0 1px 0 rgba(255,255,255,.04);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  transform: translateY(calc(100% - 4px));
  transition: transform .35s var(--ease-out);
  /* 顶部发光线 */
}
.bottom-dock::before {
  content: ""; position: absolute; left: 8px; right: 8px; top: -1px;
  height: 1px;
  background: linear-gradient(90deg,
    transparent 0%,
    rgba(226,176,74,.5) 20%,
    rgba(111,198,160,.5) 50%,
    rgba(167,138,212,.5) 80%,
    transparent 100%);
  opacity: .6;
  transition: opacity .4s;
}
.bottom-dock.visible {
  transform: translateY(0);
}
.bottom-dock.visible::before {
  opacity: 1;
}
/* 底部小凸起指示器 */
.bottom-dock::after {
  content: ""; position: absolute; left: 50%; top: -6px;
  transform: translateX(-50%);
  width: 36px; height: 6px;
  background: rgba(255,255,255,.18);
  border-radius: 0 0 6px 6px;
  transition: opacity .35s;
}
.bottom-dock.visible::after {
  opacity: 0;
}

/* ── Dock 按钮 ── */
.dock-btn {
  position: relative;
  width: 48px; height: 40px;
  border: 0; background: transparent;
  color: rgba(244,228,188,.55);
  cursor: pointer;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 2px;
  border-radius: 8px;
  transition: all .25s var(--ease-out);
  font-family: inherit;
  padding: 0;
  z-index: 1;
}
.dock-btn .db-icon {
  font-size: 18px; line-height: 1;
  transition: transform .25s var(--ease-out), filter .25s;
}
.dock-btn .db-label {
  font-size: 8.5px; font-weight: 600;
  letter-spacing: .04em;
  text-transform: uppercase;
  color: rgba(244,228,188,.35);
  transition: color .25s;
}
.dock-btn:hover {
  background: rgba(255,255,255,.08);
  color: rgba(244,228,188,.9);
}
.dock-btn:hover .db-icon {
  transform: translateY(-2px);
  filter: drop-shadow(0 0 6px currentColor);
}
.dock-btn:hover .db-label {
  color: rgba(244,228,188,.7);
}
.dock-btn.active {
  background: rgba(255,255,255,.1);
  color: #fff;
  box-shadow: 0 0 14px rgba(226,176,74,.25);
}
.dock-btn.active .db-icon {
  filter: drop-shadow(0 0 8px currentColor);
}
.dock-btn.active .db-label {
  color: var(--gold);
}
/* 分隔线 */
.dock-sep {
  width: 1px; height: 22px;
  background: rgba(255,255,255,.08);
  margin: 0 6px;
  border-radius: 1px;
}

/* ── 面板容器 ── */
.dock-panels {
  position: fixed; left: 0; right: 0; bottom: 48px;
  z-index: 98;
  display: flex; align-items: flex-end; justify-content: center;
  pointer-events: none;
  padding: 0 16px 8px;
}

/* ── 单个面板 ── */
.dock-panel {
  display: none;
  width: 100%; max-width: 900px; max-height: 420px;
  background: linear-gradient(180deg,
    rgba(20,14,10,.96) 0%,
    rgba(28,18,14,.98) 100%);
  border: 1px solid rgba(255,255,255,.1);
  border-bottom: none;
  border-radius: 14px 14px 0 0;
  box-shadow:
    0 -8px 32px rgba(0,0,0,.55),
    inset 0 1px 0 rgba(255,255,255,.04);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  overflow: hidden;
  flex-direction: column;
  pointer-events: auto;
  animation: panel-slide-up .3s var(--ease-out);
}
.dock-panel.open {
  display: flex;
}
@keyframes panel-slide-up {
  from { transform: translateY(20px); opacity: 0; }
  to   { transform: translateY(0); opacity: 1; }
}

/* ── 面板标题栏 ── */
.panel-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 16px;
  border-bottom: 1px solid rgba(255,255,255,.06);
  flex-shrink: 0;
  background: rgba(255,255,255,.02);
}
.panel-title {
  font-size: 13px; font-weight: 700;
  color: var(--text-primary);
  display: flex; align-items: center; gap: 8px;
  letter-spacing: .01em;
}
.panel-title .pt-icon {
  font-size: 16px;
}
.panel-close {
  width: 26px; height: 26px;
  border: 0; background: rgba(255,255,255,.06);
  color: var(--text-secondary);
  border-radius: 50%;
  cursor: pointer;
  font-size: 14px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  transition: all .2s;
  font-family: inherit;
  line-height: 1;
  padding: 0;
}
.panel-close:hover {
  background: rgba(232,130,110,.25);
  color: var(--coral);
}

/* ── 面板内容滚动区 ── */
.panel-body {
  flex: 1; overflow-y: auto; overflow-x: hidden;
  padding: 12px 16px;
  display: grid; gap: 10px;
  scrollbar-width: thin;
  scrollbar-color: rgba(255,255,255,.1) transparent;
}
.panel-body::-webkit-scrollbar { width: 4px; }
.panel-body::-webkit-scrollbar-thumb {
  background: rgba(255,255,255,.1);
  border-radius: 2px;
}

/* ── 游戏风格卡片(面板内用) ── */
.game-card {
  display: grid; gap: 8px;
  padding: 11px 14px;
  border-radius: 10px;
  background: rgba(255,255,255,.035);
  border: 1px solid rgba(255,255,255,.07);
  transition: all .3s;
  position: relative;
  overflow: hidden;
}
.game-card::before {
  content: ""; position: absolute; left: 0; top: 0; bottom: 0;
  width: 3px;
  border-radius: 0 2px 2px 0;
}
.game-card:hover {
  background: rgba(255,255,255,.055);
  border-color: rgba(255,255,255,.12);
}
/* 稀有度颜色 */
.game-card.rarity-common::before    { background: #8a8a7a; }
.game-card.rarity-uncommon::before  { background: #6fc6a0; box-shadow: 0 0 8px rgba(111,198,160,.3); }
.game-card.rarity-rare::before      { background: #6a9ee8; box-shadow: 0 0 10px rgba(106,158,232,.4); }
.game-card.rarity-epic::before      { background: #a78ad4; box-shadow: 0 0 12px rgba(167,138,212,.5); }
.game-card.rarity-legendary::before { background: #e2b04a; box-shadow: 0 0 16px rgba(226,176,74,.6); }
.game-card.rarity-mythic::before    {
  background: linear-gradient(180deg, #e8826e, #e2b04a, #6fc6a0);
  box-shadow: 0 0 20px rgba(232,130,110,.5);
}

.game-card-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 10px;
}
.game-card-title {
  font-size: 12.5px; font-weight: 700;
  color: var(--text-primary);
  line-height: 1.3;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.game-card-badge {
  flex-shrink: 0;
  padding: 3px 10px;
  border-radius: 99px;
  font-size: 9.5px; font-weight: 600;
  letter-spacing: .03em;
  text-transform: uppercase;
}
.game-card-badge.planned   { background: rgba(226,176,74,.15); color: var(--gold); }
.game-card-badge.approved  { background: rgba(111,198,160,.15); color: var(--mint); }
.game-card-badge.running   { background: rgba(106,158,232,.2); color: var(--accent-blue); animation: badge-pulse 2s ease-in-out infinite; }
.game-card-badge.completed { background: rgba(255,255,255,.05); color: var(--text-muted); }
.game-card-badge.failed    { background: rgba(232,130,110,.12); color: var(--coral); }
.game-card-badge.deferred  { background: rgba(167,138,212,.12); color: var(--plum); }
.game-card-badge.paused    { background: rgba(255,255,255,.05); color: var(--text-muted); }
@keyframes badge-pulse {
  0%, 100% { box-shadow: 0 0 0 rgba(106,158,232,0); }
  50%      { box-shadow: 0 0 10px rgba(106,158,232,.35); }
}

.game-card-sub {
  font-size: 10.5px; color: rgba(244,228,188,.7);
  line-height: 1.4;
  overflow: hidden; text-overflow: ellipsis;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
}
.game-card-meta {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px;
  font-size: 10px; color: var(--text-muted);
}
.game-card-tags {
  display: flex; gap: 5px; flex-wrap: wrap;
}
.game-card-tag {
  padding: 2px 8px;
  border-radius: 99px;
  font-size: 9px; font-weight: 600;
  letter-spacing: .03em;
  background: rgba(255,255,255,.06);
  color: var(--text-secondary);
}
.game-card-tag.memory    { background: rgba(226,176,74,.14); color: var(--gold); }
.game-card-tag.learning  { background: rgba(111,198,160,.14); color: var(--mint); }
.game-card-tag.evolution { background: rgba(232,130,110,.14); color: var(--coral); }
.game-card-tag.truthfulness { background: rgba(106,158,232,.14); color: var(--accent-blue); }
.game-card-tag.creativity   { background: rgba(167,138,212,.14); color: var(--plum); }

.autonomous-loop {
  display: grid;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid rgba(255,255,255,.06);
  background: rgba(255,255,255,.035);
}
.autonomous-loop-boundary {
  font-size: 10px;
  color: var(--text-muted);
  line-height: 1.45;
}
.autonomous-loop-stages {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}
@media (max-width: 720px) {
  .autonomous-loop-stages {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.autonomous-loop-stage {
  display: grid;
  gap: 5px;
  padding: 9px 10px;
  border-radius: 9px;
  border: 1px solid rgba(255,255,255,.05);
  background: rgba(255,255,255,.03);
}
.autonomous-loop-stage.active {
  border-color: rgba(106,158,232,.35);
  background: rgba(106,158,232,.08);
}
.autonomous-loop-stage.ready {
  border-color: rgba(111,198,160,.28);
  background: rgba(111,198,160,.08);
}
.autonomous-loop-stage.idle {
  opacity: .82;
}
.autonomous-loop-topline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.autonomous-loop-label {
  font-size: 10px;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: .04em;
}
.autonomous-loop-owner {
  font-size: 9px;
  color: var(--text-muted);
  text-transform: uppercase;
}
.autonomous-loop-status {
  font-size: 10px;
  color: var(--text-secondary);
}
.autonomous-loop-summary {
  font-size: 10px;
  color: rgba(244,228,188,.78);
  line-height: 1.45;
}
.autonomous-loop-focus {
  font-size: 10px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.4;
}
.autonomous-loop-reason,
.autonomous-loop-activity {
  font-size: 9.5px;
  color: var(--text-secondary);
  line-height: 1.45;
}
.autonomous-loop-writebacks {
  display: grid;
  gap: 5px;
}
.autonomous-loop-writeback {
  font-size: 10px;
  color: var(--text-secondary);
}

.board-section-label {
  font-size: 10px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: .05em;
  padding: 0 2px;
}
.observation-stack {
  display: grid;
  gap: 8px;
}
.current-card-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
@media (max-width: 720px) {
  .current-card-grid {
    grid-template-columns: 1fr;
  }
}
.chain-hero {
  display: grid;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 12px;
  border: 1px solid rgba(226,176,74,.18);
  background: linear-gradient(160deg, rgba(48,35,27,.96) 0%, rgba(33,24,19,.94) 100%);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.04);
}
.chain-hero-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  flex-wrap: wrap;
}
.chain-hero-main {
  display: grid;
  gap: 5px;
  min-width: min(320px, 100%);
}
.chain-hero-label {
  font-size: 9px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: .08em;
}
.chain-hero-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.2;
}
.chain-hero-summary {
  font-size: 10.5px;
  color: rgba(244,228,188,.78);
  line-height: 1.5;
}
.chain-stage-rail {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-top: 2px;
}
@media (max-width: 720px) {
  .chain-stage-rail {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.chain-stage-stop {
  position: relative;
  min-width: 0;
  padding: 10px 10px 11px;
  border-radius: 10px;
  background: rgba(255,255,255,.03);
  border: 1px solid rgba(255,255,255,.06);
  overflow: hidden;
}
.chain-stage-stop::before {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  top: 0;
  height: 2px;
  background: rgba(255,255,255,.10);
}
.chain-stage-stop.active::before { background: var(--gold); }
.chain-stage-stop.ready::before { background: var(--mint); }
.chain-stage-stop.idle::before { background: rgba(255,255,255,.10); }
.chain-stage-stop.focus {
  box-shadow: inset 0 0 0 1px rgba(226,176,74,.22);
}
.chain-stage-kicker {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 8.5px;
  color: var(--text-muted);
  margin-bottom: 6px;
}
.chain-stage-name {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-primary);
}
.chain-stage-state {
  margin-top: 4px;
  font-size: 10px;
  font-weight: 600;
  color: var(--text-secondary);
}
.chain-stage-note {
  margin-top: 4px;
  font-size: 9.5px;
  color: rgba(244,228,188,.62);
  line-height: 1.4;
}
.chain-hero-focus {
  display: grid;
  gap: 6px;
  justify-items: start;
}
.chain-hero-focus-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-primary);
}
.chain-hero-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chain-focus-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 9px;
  border-radius: 999px;
  font-size: 9px;
  font-weight: 600;
  background: rgba(255,255,255,.05);
  border: 1px solid rgba(255,255,255,.08);
  color: var(--text-secondary);
}
.chain-focus-pill.accent {
  background: rgba(226,176,74,.12);
  border-color: rgba(226,176,74,.22);
  color: var(--gold);
}
.chain-focus-pill.good {
  background: rgba(111,198,160,.12);
  border-color: rgba(111,198,160,.22);
  color: var(--mint);
}
.chain-focus-pill.info {
  background: rgba(106,158,232,.12);
  border-color: rgba(106,158,232,.22);
  color: var(--accent-blue);
}
.chain-focus-pill.warn {
  background: rgba(167,138,212,.12);
  border-color: rgba(167,138,212,.22);
  color: var(--plum);
}
.chain-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}
@media (max-width: 720px) {
  .chain-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.chain-metric {
  display: grid;
  gap: 3px;
  padding: 9px 10px;
  border-radius: 10px;
  background: rgba(255,255,255,.03);
  border: 1px solid rgba(255,255,255,.06);
}
.chain-metric.api-b { border-top: 2px solid var(--gold); }
.chain-metric.api-a { border-top: 2px solid var(--mint); }
.chain-metric.candidate { border-top: 2px solid var(--plum); }
.chain-metric.mem { border-top: 2px solid var(--coral); }
.chain-metric-label {
  font-size: 9px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: .05em;
}
.chain-metric-value {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.chain-metric-note {
  font-size: 9.5px;
  color: rgba(244,228,188,.62);
}
.chain-watch-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
@media (max-width: 720px) {
  .chain-watch-grid {
    grid-template-columns: 1fr;
  }
}
.watch-band {
  display: grid;
  gap: 8px;
  padding: 10px;
  border-radius: 11px;
  background: rgba(255,255,255,.025);
  border: 1px solid rgba(255,255,255,.06);
}
.watch-band-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.watch-band-title-wrap {
  display: grid;
  gap: 4px;
  min-width: 0;
}
.watch-band-title {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-primary);
}
.watch-band-subline {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.watch-band-owner,
.watch-band-stage {
  font-size: 9px;
  color: var(--text-muted);
}
.watch-band-summary {
  font-size: 9.5px;
  line-height: 1.45;
  color: rgba(244,228,188,.72);
}
.watch-band-latest {
  font-size: 9px;
  line-height: 1.4;
  color: rgba(226,176,74,.86);
}
.watch-band-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  padding: 2px 7px;
  border-radius: 999px;
  font-size: 9px;
  color: var(--text-muted);
  background: rgba(255,255,255,.05);
  border: 1px solid rgba(255,255,255,.07);
}
.watch-band-body {
  display: grid;
  gap: 6px;
}
.watch-band-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}
.watch-band-footnote {
  font-size: 9px;
  color: var(--text-muted);
}
.trace-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.trace-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 999px;
  border: 1px solid rgba(121,163,255,.25);
  background: rgba(121,163,255,.1);
  color: var(--text-primary);
  font-size: 9px;
  cursor: pointer;
}
.trace-chip.active {
  border-color: rgba(226,176,74,.38);
  background: rgba(226,176,74,.18);
  color: var(--accent-yellow);
}
.watch-inline-note {
  font-size: 10px;
  color: var(--text-muted);
  line-height: 1.45;
  padding: 0 2px;
}

/* score bar */
.game-score-bar {
  height: 5px; border-radius: 3px;
  background: rgba(255,255,255,.06);
  overflow: hidden;
}
.game-score-fill {
  height: 100%; border-radius: 3px;
  transition: width .6s var(--ease-out);
}
.game-score-fill.high  { background: linear-gradient(90deg, var(--mint), var(--accent-green)); }
.game-score-fill.mid   { background: linear-gradient(90deg, var(--gold), var(--accent-yellow)); }
.game-score-fill.low   { background: linear-gradient(90deg, var(--coral), var(--accent-red)); }

/* ── LM Input 面板专用 ── */
.lm-section {
  display: grid; gap: 8px;
}
.lm-section-label {
  font-size: 9.5px; font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: .06em;
}
.lm-stat-row {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 12px;
  border-radius: 8px;
  background: rgba(255,255,255,.03);
  border: 1px solid rgba(255,255,255,.04);
}
.lm-stat-icon { font-size: 14px; }
.lm-stat-label { font-size: 10.5px; color: var(--text-secondary); min-width: 70px; }
.lm-stat-value {
  font-size: 11px; font-weight: 700; color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}
.lm-stat-value.highlight { color: var(--gold); }
.lm-evidence-list {
  display: grid; gap: 4px;
  max-height: 140px; overflow-y: auto;
}
.lm-evidence-item {
  font-size: 10px; color: rgba(244,228,188,.65);
  padding: 5px 10px;
  border-radius: 6px;
  background: rgba(255,255,255,.025);
  border-left: 2px solid rgba(255,255,255,.08);
  line-height: 1.4;
}
.lm-evidence-item .ei-node {
  color: var(--accent-purple);
  font-weight: 600;
}
.lm-prompt-preview {
  font: 9px/1.5 "Courier New", monospace;
  color: #7ee0c0;
  background: rgba(0,0,0,.3);
  border-radius: 8px;
  padding: 10px 12px;
  max-height: 120px; overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

/* ── 认知面板专用 ── */
.cog-flow {
  display: grid; gap: 8px;
}
.cog-step {
  display: grid; grid-template-columns: 90px 1fr;
  gap: 8px; align-items: start;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(255,255,255,.025);
  border: 1px solid rgba(255,255,255,.04);
}
.cog-step-label {
  font-size: 10px; font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: .04em;
  padding-top: 1px;
}
.cog-step-content {
  display: grid; gap: 3px;
}
.cog-step-title {
  font-size: 11.5px; font-weight: 600;
  color: var(--text-primary);
}
.cog-step-detail {
  font-size: 10px; color: rgba(244,228,188,.65);
  line-height: 1.35;
}
.cog-need-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 99px;
  font-size: 9px; font-weight: 600;
  margin: 1px 2px;
}
.cog-need-tag.severity-high { background: rgba(232,130,110,.2); color: var(--coral); }
.cog-need-tag.severity-mid  { background: rgba(226,176,74,.18); color: var(--gold); }
.cog-need-tag.severity-low  { background: rgba(111,198,160,.14); color: var(--mint); }

/* ── 角色迷你状态条(Dock 左侧) ── */
.dock-char-strip {
  display: flex; align-items: center; gap: 8px;
  padding: 0 10px;
  height: 38px;
  border-radius: 8px;
  background: rgba(255,255,255,.03);
  border: 1px solid rgba(255,255,255,.05);
  margin-right: auto;
  min-width: 0;
}
.dock-char-strip .dcs-avatar {
  position: relative; width: 26px; height: 32px; flex-shrink: 0;
}
.dcs-avatar .dcs-head {
  position: absolute; top: 0; left: 3px;
  width: 20px; height: 18px;
  background: linear-gradient(155deg, #ffe8d0, #f4d4b0);
  border-radius: 50% 50% 46% 48%;
}
.dcs-avatar .dcs-hair {
  position: absolute; top: -2px; left: 1px;
  width: 24px; height: 10px;
  background: linear-gradient(180deg, #3a4a5e, #1a2030);
  border-radius: 12px 12px 2px 2px;
  z-index: 2;
}
.dcs-avatar .dcs-body-mini {
  position: absolute; bottom: 0; left: 5px;
  width: 16px; height: 14px;
  background: linear-gradient(140deg, #6fc6a0, #4a9070);
  border-radius: 6px 6px 4px 4px;
  transition: background .6s;
}
body[data-action="organize"] .dcs-body-mini { background: linear-gradient(140deg, #f4d4a8, #d4954a); }
body[data-action="rest"]     .dcs-body-mini { background: linear-gradient(140deg, #6fc6a0, #4a9070); }
body[data-action="work"]     .dcs-body-mini { background: linear-gradient(140deg, #6a7eb8, #4a6098); }
body[data-action="write"]    .dcs-body-mini { background: linear-gradient(140deg, #a78ad4, #7a5ab0); }
.dcs-info {
  display: flex; flex-direction: column; gap: 0;
  min-width: 0; overflow: hidden;
}
.dcs-name {
  font-size: 10.5px; font-weight: 700; color: var(--text-primary);
  white-space: nowrap;
}
.dcs-status {
  font-size: 9px; color: var(--text-muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.dcs-hp-bar {
  height: 2px; border-radius: 1px;
  background: rgba(255,255,255,.08);
  width: 40px; overflow: hidden;
}
.dcs-hp-fill {
  height: 100%; border-radius: 1px;
  transition: width .5s, background .5s;
}
.dcs-hp-fill.good   { background: var(--mint); }
.dcs-hp-fill.warn   { background: var(--gold); }
.dcs-hp-fill.danger { background: var(--coral); }

/* ── 面板空状态 ── */
.panel-empty {
  display: flex; flex-direction: column; align-items: center;
  justify-content: center;
  padding: 32px 16px;
  color: var(--text-muted);
  gap: 6px;
  text-align: center;
}
.panel-empty .pe-icon { font-size: 32px; opacity: .5; }
.panel-empty .pe-text { font-size: 12px; }

/* ── 场景迷你标题(左上角轻量提示) ── */
.scene-mini-title {
  position: absolute;
  left: 18px; top: 18px;
  z-index: 20;
  display: flex; align-items: center; gap: 6px;
  padding: 6px 12px;
  border-radius: 20px;
  background: rgba(20,14,10,.75);
  border: 1px solid rgba(255,255,255,.06);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  font-size: 11px; font-weight: 600;
  color: var(--text-primary);
  pointer-events: none;
  transition: opacity .8s;
}
.scene-mini-title span:first-child { font-size: 14px; }

/* ── Dock 响应式 ── */
@media (max-width: 720px) {
  .bottom-dock { height: 44px; padding: 0 8px; gap: 0; }
  .dock-btn { width: 38px; height: 36px; }
  .dock-btn .db-icon { font-size: 15px; }
  .dock-btn .db-label { font-size: 7px; }
  .dock-sep { margin: 0 2px; }
  .dock-char-strip { padding: 0 6px; gap: 4px; }
  .dock-panel { max-height: 320px; border-radius: 10px 10px 0 0; }
  .dock-panels { padding: 0 8px 6px; }
}

/* ── drill-down 详情抽屉 ── */
.drawer-mask {
  position: fixed; inset: 0;
  z-index: 200;
  display: flex; align-items: center; justify-content: center;
  background: rgba(30,14,8,.5);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  opacity: 0; pointer-events: none;
  transition: opacity .25s var(--ease-out);
}
.drawer-mask.open { opacity: 1; pointer-events: auto; }
.drawer {
  width: min(680px, 92vw);
  max-height: 84vh;
  overflow-y: auto;
  background: linear-gradient(168deg, #2f2118 0%, #241813 100%);
  border: 1px solid rgba(226,176,74,.25);
  border-radius: 16px;
  box-shadow: 0 30px 80px rgba(0,0,0,.6);
  padding: 18px 20px 22px;
  transform: translateY(16px) scale(.98);
  opacity: 0;
  transition: transform .28s var(--ease-out), opacity .28s var(--ease-out);
}
.drawer-mask.open .drawer { transform: translateY(0) scale(1); opacity: 1; }
.drawer-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 14px; padding-bottom: 10px;
  border-bottom: 1px solid rgba(255,255,255,.08);
}
.drawer-title {
  font-size: 14px; font-weight: 700; color: var(--text-primary);
  display: flex; align-items: center; gap: 8px;
}
.drawer-close {
  width: 28px; height: 28px; flex-shrink: 0;
  border: none; border-radius: 8px; cursor: pointer;
  background: rgba(255,255,255,.06); color: var(--text-secondary);
  font-size: 18px; line-height: 1;
  transition: background .2s;
}
.drawer-close:hover { background: rgba(232,130,110,.25); color: var(--text-primary); }
.drawer-sub {
  font-size: 10px; color: var(--text-muted);
  margin: -8px 0 14px; line-height: 1.5;
}
/* 自主链路观测 */
.segment-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 720px) { .segment-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 560px) { .segment-grid { grid-template-columns: 1fr; } }
.segment-col {
  border-radius: 12px; padding: 12px;
  background: rgba(255,255,255,.03);
  border: 1px solid rgba(255,255,255,.06);
}
.segment-col.supervisor { border-top: 2px solid var(--gold); }
.segment-col.agent { border-top: 2px solid var(--mint); }
.segment-col.mem { border-top: 2px solid var(--coral); }
.segment-col.candidate { border-top: 2px solid var(--plum); }
.segment-col-head {
  font-size: 11px; font-weight: 700; color: var(--text-primary);
  display: flex; align-items: center; gap: 6px; margin-bottom: 4px;
}
.segment-col-tag { font-size: 8px; color: var(--text-muted); font-weight: 600; }
.segment-metric {
  display: flex; justify-content: space-between; align-items: baseline;
  font-size: 10.5px; color: var(--text-secondary); padding: 3px 0;
}
.segment-metric b { color: var(--text-primary); font-size: 13px; font-variant-numeric: tabular-nums; }
.segment-active {
  margin-top: 8px; padding: 8px; border-radius: 8px;
  background: rgba(0,0,0,.2); font-size: 10px; color: var(--text-secondary);
}
.segment-active .la-title { color: var(--text-primary); font-weight: 600; font-size: 11px; }
/* 因果链 / 通用区块 */
.drawer-section { margin-bottom: 14px; }
.drawer-section-label {
  font-size: 10px; font-weight: 700; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: .05em; margin-bottom: 6px;
}
.prov-chain { display: grid; gap: 0; }
.prov-node {
  position: relative; padding: 8px 10px 8px 22px;
  border-left: 2px solid rgba(226,176,74,.3);
}
.prov-node::before {
  content: ''; position: absolute; left: -5px; top: 12px;
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--gold); box-shadow: 0 0 6px var(--gold-g);
}
.prov-node-label { font-size: 10px; font-weight: 700; color: var(--accent-yellow); }
.prov-node-body { font-size: 10.5px; color: var(--text-secondary); margin-top: 2px; line-height: 1.5; }
.health-row {
  display: flex; justify-content: space-between; align-items: center;
  font-size: 11px; color: var(--text-secondary);
  padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,.04);
}
.health-row .hr-val { color: var(--text-primary); font-weight: 600; font-variant-numeric: tabular-nums; }
.drill-link {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 9.5px; font-weight: 700; cursor: pointer;
  color: var(--accent-yellow);
  padding: 4px 10px; border-radius: 999px;
  background: rgba(226,176,74,.12);
  border: 1px solid rgba(226,176,74,.25);
  transition: background .2s;
}
.drill-link:hover { background: rgba(226,176,74,.24); }
</style>
</head>
<body data-scene="idle" data-action="rest" data-has-errors="false" data-exec-window="true">
<div class="stage">
  <main class="room" aria-label="VoidCube 监督者小屋">

    <!-- 墙 + 钟 + 窗户 + 装饰 -->
    <div class="wall"></div>
    <div class="baseboard"></div>

    <div class="wall-clock" aria-hidden="true">
      <div class="wc-body">
        <span class="wc-tick major" style="transform: rotate(0deg);"></span>
        <span class="wc-tick major" style="transform: rotate(90deg);"></span>
        <span class="wc-tick major" style="transform: rotate(180deg);"></span>
        <span class="wc-tick major" style="transform: rotate(270deg);"></span>
        <div class="wc-hand h" id="wcHour"></div>
        <div class="wc-hand m"></div>
        <div class="wc-hand s"></div>
        <div class="wc-center"></div>
      </div>
    </div>

    <div class="window" aria-hidden="true">
      <div class="window-glass">
        <div class="window-sun"></div>
        <div class="window-moon"></div>
        <div class="window-cloud c1"></div>
        <div class="window-cloud c2"></div>
        <span class="star" style="left:20%;top:20%;width:2px;height:2px;animation-delay:0s;"></span>
        <span class="star" style="left:50%;top:12%;width:3px;height:3px;animation-delay:.6s;"></span>
        <span class="star" style="left:70%;top:30%;width:2px;height:2px;animation-delay:1.2s;"></span>
        <span class="star" style="left:32%;top:38%;width:2px;height:2px;animation-delay:1.8s;"></span>
      </div>
      <div class="window-frame-h"></div>
      <div class="window-frame-v"></div>
      <div class="window-sill"></div>
    </div>

    <div class="picture" aria-hidden="true"></div>

    <!-- 地板 -->
    <div class="floor"></div>
    <div class="rug"></div>

    <!-- 书架(左墙) -->
    <section class="shelf" aria-hidden="true">
      <div class="shelf-frame"></div>
      <div class="shelf-board b1"></div>
      <div class="shelf-board b2"></div>
      <div class="shelf-board b3"></div>
      <div class="shelf-board b4"></div>
      <div class="shelf-row r1">
        <span class="book b1"></span><span class="book b2 lean-r"></span><span class="book b3"></span>
        <span class="book b4 short"></span><span class="book b5"></span><span class="book b6"></span>
        <span class="book b7 lean-l"></span><span class="book b8"></span><span class="book b9 short"></span>
        <span class="book b10"></span><span class="book b11"></span>
      </div>
      <div class="shelf-row r2">
        <span class="book b2"></span><span class="book b5 lean-l"></span><span class="book b8"></span>
        <span class="book b1 short"></span><span class="book b3"></span><span class="book b6 lean-r"></span>
        <span class="book b9"></span><span class="book b4"></span><span class="book b10 short"></span>
      </div>
      <div class="shelf-row r3">
        <span class="book b7"></span><span class="book b10"></span><span class="book b2 lean-r"></span>
        <span class="book b5"></span><span class="book b8 lean-l"></span>
        <span class="book flat b3"></span><span class="book flat b4"></span>
        <span class="book b1 short"></span><span class="book b6"></span>
      </div>
      <div class="shelf-row r4">
        <span class="book b3"></span><span class="book b9 lean-r"></span><span class="book b1 tall"></span>
        <span class="book b4"></span><span class="book b7 lean-l"></span>
        <span class="book b2"></span><span class="book b5"></span><span class="book b10 short"></span>
        <span class="book b8"></span>
      </div>
      <div class="shelf-deco vase">🏺</div>
      <div class="shelf-deco plant">🌿</div>
      <div class="shelf-deco cup">☕</div>
      <div class="shelf-deco frame">🖼️</div>
    </section>

    <!-- 沙发 -->
    <div class="sofa" aria-hidden="true">
      <div class="sofa-shadow"></div>
      <div class="sofa-back"></div>
      <div class="sofa-cushion back-l"></div>
      <div class="sofa-cushion back-c"></div>
      <div class="sofa-cushion back-r"></div>
      <div class="sofa-cushion seat-l"></div>
      <div class="sofa-cushion seat-c"></div>
      <div class="sofa-cushion seat-r"></div>
      <div class="sofa-arm l"></div>
      <div class="sofa-arm r"></div>
      <div class="sofa-base"></div>
      <div class="sofa-leg l1"></div>
      <div class="sofa-leg l2"></div>
      <div class="sofa-leg r1"></div>
      <div class="sofa-pillow p1"></div>
      <div class="sofa-pillow p2"></div>
      <div class="sofa-throw"></div>
    </div>

    <!-- 电脑桌 + 显示器 + 办公椅 -->
    <div class="desk-main" aria-hidden="true">
      <div class="desk-monitor">
        <div class="monitor-glow"></div>
        <div class="monitor-body">
          <div class="monitor-screen">
            <div class="monitor-code"><span class="kw">def</span> think():<br>  <span class="kw">for</span> step <span class="kw">in</span> plan:<br>    n = <span class="num">42</span><br>    run(n)<br>  <span class="kw">return</span> ok</div>
            <div class="monitor-cursor"></div>
          </div>
        </div>
        <div class="monitor-stand"></div>
        <div class="monitor-base"></div>
      </div>
      <div class="desk-top"></div>
      <div class="desk-body"></div>
      <div class="desk-keyboard"></div>
      <div class="desk-mouse"></div>
      <div class="desk-mug">
        <div class="mug-body"></div>
        <div class="mug-handle"></div>
        <span class="mug-steam"></span>
        <span class="mug-steam s2"></span>
        <span class="mug-steam s3"></span>
      </div>
      <div class="desk-notebook"></div>
      <div class="desk-legs">
        <div class="desk-leg l"></div>
        <div class="desk-leg r"></div>
      </div>
    </div>

    <div class="chair" aria-hidden="true">
      <div class="chair-back"></div>
      <div class="chair-seat"></div>
      <div class="chair-base"></div>
      <div class="chair-wheel w1"></div>
      <div class="chair-wheel w2"></div>
      <div class="chair-wheel w3"></div>
    </div>

    <!-- 写字桌 + 台灯 + 书本 + 信纸 -->
    <div class="desk-write" aria-hidden="true">
      <div class="dw-lamp">
        <div class="dw-lamp-glow"></div>
        <div class="dw-lamp-head"></div>
      </div>
      <div class="dw-paper"></div>
      <div class="dw-pen"></div>
      <div class="dw-book-stack">
        <span></span><span></span><span></span>
      </div>
      <div class="dw-top"></div>
      <div class="dw-body"></div>
      <div class="dw-legs">
        <div class="dw-leg l"></div>
        <div class="dw-leg r"></div>
      </div>
    </div>

    <!-- 装饰: 盆栽 + 落地灯 -->
    <div class="plant plant-corner" aria-hidden="true">
      <div class="leaves">
        <div class="leaf"></div><div class="leaf"></div><div class="leaf"></div>
        <div class="leaf"></div><div class="leaf"></div>
      </div>
      <div class="pot"></div>
    </div>

    <div class="floor-lamp" aria-hidden="true">
      <div class="fl-glow"></div>
      <div class="fl-shade"></div>
      <div class="fl-pole"></div>
      <div class="fl-base"></div>
    </div>

    <!-- 角色: 西子(长发女) -->
    <section class="xizi" aria-hidden="true">
      <div class="xz-hair-back"></div>
      <div class="xz-hair"></div>
      <div class="xz-bangs"></div>
      <div class="xz-head"></div>
      <div class="xz-brow l"></div><div class="xz-brow r"></div>
      <div class="xz-eye l"></div><div class="xz-eye r"></div>
      <div class="xz-cheek l"></div><div class="xz-cheek r"></div>
      <div class="xz-mouth"></div>
      <div class="xz-dress"></div>
      <div class="xz-arm l"></div><div class="xz-arm r"></div>
      <div class="xz-leg l"></div><div class="xz-leg r"></div>
      <div class="xz-prop"></div>
      <div class="thoughts">
        <span class="bubble b1"></span>
        <span class="bubble b2"></span>
        <span class="bubble b3"></span>
        <span class="glyph" id="glyph">·</span>
      </div>
    </section>

    <!-- 角色: 星子(短发男) -->
    <section class="xingzi" aria-hidden="true">
      <div class="xs-hair-back"></div>
      <div class="xs-hair"></div>
      <div class="xs-bangs"></div>
      <div class="xs-head"></div>
      <div class="xs-brow l"></div><div class="xs-brow r"></div>
      <div class="xs-eye l"></div><div class="xs-eye r"></div>
      <div class="xs-cheek l"></div><div class="xs-cheek r"></div>
      <div class="xs-mouth"></div>
      <div class="xs-shirt"></div>
      <div class="xs-belt"></div>
      <div class="xs-pants"></div>
      <div class="xs-arm l"></div><div class="xs-arm r"></div>
      <div class="xs-leg l"></div><div class="xs-leg r"></div>
      <div class="xs-prop"></div>
      <div class="thoughts">
        <span class="bubble b1"></span>
        <span class="bubble b2"></span>
        <span class="bubble b3"></span>
        <span class="glyph" id="glyphXingzi">·</span>
      </div>
    </section>

    <!-- 环境粒子 -->
    <div class="particles" id="particles" aria-hidden="true"></div>

    <!-- 场景标题(轻量) -->
    <div class="scene-mini-title" id="sceneMiniTitle">
      <span id="sceneMiniIcon">🛋</span>
      <span id="sceneMiniText">星子与西子的小屋</span>
    </div>

    <!-- ═══════════════════════════════════════════
    底部菜单系统 —— 游戏风格 Dock + 滑出面板
    默认隐藏, 鼠标触及下边缘自动弹出
    ════════════════════════════════════════════ -->

    <!-- 底部触发区 -->
    <div class="bottom-trigger" id="bottomTrigger"></div>

    <!-- 面板层(在 Dock 上方滑出) -->
    <div class="dock-panels" id="dockPanels">

      <!-- 🚦 自主链路总览 -->
      <div class="dock-panel" id="panelChain">
        <div class="panel-header">
          <div class="panel-title"><span class="pt-icon">🚦</span>自主链路总览</div>
          <button class="panel-close" data-panel="chain">×</button>
        </div>
        <div class="panel-body" id="panelChainBody">
        </div>
      </div>

      <!-- 🧠 LM 输入面板 -->
      <div class="dock-panel" id="panelLMInput">
        <div class="panel-header">
          <div class="panel-title"><span class="pt-icon">🧠</span>API-B LM 观察</div>
          <button class="panel-close" data-panel="lminput">×</button>
        </div>
        <div class="panel-body" id="panelLMInputBody">
        </div>
      </div>

      <!-- 📊 认知面板 -->
      <div class="dock-panel" id="panelCognition">
        <div class="panel-header">
          <div class="panel-title"><span class="pt-icon">📊</span>认知状态 · Perception → Intent</div>
          <button class="panel-close" data-panel="cognition">×</button>
        </div>
        <div class="panel-body" id="panelCognitionBody">
        </div>
      </div>

      <!-- ⚙️ API-B 观测面板 -->
      <div class="dock-panel" id="panelObservation">
        <div class="panel-header">
          <div class="panel-title"><span class="pt-icon">⚙️</span>API-B 观测</div>
          <button class="panel-close" data-panel="observation">×</button>
        </div>
        <div class="panel-body" id="panelObservationBody">
        </div>
      </div>

      <!-- 📈 统计面板 -->
      <div class="dock-panel" id="panelStats">
        <div class="panel-header">
          <div class="panel-title"><span class="pt-icon">📈</span>运行统计</div>
          <button class="panel-close" data-panel="stats">×</button>
        </div>
        <div class="panel-body" id="panelStatsBody">
        </div>
      </div>
    </div>

    <!-- 底部 Dock 栏 -->
    <nav class="bottom-dock" id="bottomDock">
      <!-- 角色迷你状态 -->
      <div class="dock-char-strip" id="dockCharStrip">
        <div class="dcs-avatar">
          <div class="dcs-hair"></div>
          <div class="dcs-head"></div>
          <div class="dcs-body-mini"></div>
        </div>
        <div class="dcs-info">
          <div class="dcs-name" id="dcsName">西子</div>
          <div class="dcs-status" id="dcsStatus">就绪</div>
        </div>
        <div class="dcs-hp-bar"><div class="dcs-hp-fill good" id="dcsHpFill" style="width:100%"></div></div>
      </div>

      <button class="dock-btn" data-panel="chain" title="自主链路总览">
        <span class="db-icon">🚦</span>
        <span class="db-label">链路</span>
      </button>
      <span class="dock-sep"></span>
      <button class="dock-btn" data-panel="lminput" title="API-B LM 观察">
        <span class="db-icon">🧠</span>
        <span class="db-label">LM输入</span>
      </button>
      <span class="dock-sep"></span>
      <button class="dock-btn" data-panel="cognition" title="认知状态">
        <span class="db-icon">📊</span>
        <span class="db-label">认知</span>
      </button>
      <span class="dock-sep"></span>
      <button class="dock-btn" data-panel="observation" title="API-B 观测">
        <span class="db-icon">⚙️</span>
        <span class="db-label">观测</span>
      </button>
      <span class="dock-sep"></span>
      <button class="dock-btn" data-panel="stats" title="运行统计">
        <span class="db-icon">📈</span>
        <span class="db-label">统计</span>
      </button>

      <!-- 动作切换(紧凑) -->
      <span class="dock-sep" style="margin-left:12px;"></span>
      <button class="dock-btn" data-action-btn="rest" title="休息">
        <span class="db-icon">🛋</span>
      </button>
      <button class="dock-btn" data-action-btn="work" title="工作">
        <span class="db-icon">💻</span>
      </button>
      <button class="dock-btn" data-action-btn="organize" title="整理">
        <span class="db-icon">📚</span>
      </button>
      <button class="dock-btn" data-action-btn="write" title="审核">
        <span class="db-icon">✍️</span>
      </button>
    </nav>

    <!-- drill-down 详情抽屉 -->
    <div class="drawer-mask" id="detailDrawer">
      <div class="drawer" id="detailDrawerCard">
        <div class="drawer-head">
          <div class="drawer-title" id="detailDrawerTitle">详情</div>
          <button class="drawer-close" id="detailDrawerClose">×</button>
        </div>
        <div id="detailDrawerBody"></div>
      </div>
    </div>

  </main>
</div>
<script>
/*━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  VoidCube 监督者小屋  v5  ——  底部菜单系统
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━*/
const $  = (s, el) => (el||document).querySelector(s);
const $$ = (s, el) => [...(el||document).querySelectorAll(s)];

/* ── 房间缩放 ── */
const ROOM_W = 1440, ROOM_H = 810;
function updateRoomScale() {
  const s = $('.stage'); const r = $('.room');
  if (!s || !r) return;
  const sw = s.clientWidth  || window.innerWidth;
  const sh = s.clientHeight || window.innerHeight;
  if (sw <= 0 || sh <= 0) return;
  const scale = Math.min(sw / ROOM_W, sh / ROOM_H);
  document.documentElement.style.setProperty('--room-scale', scale);
}

/* ── DOM refs ── */
const els = {
  body: document.body,
  glyph: $('#glyph'),
  glyphXingzi: $('#glyphXingzi'),
  /* character switching */
  activeChar: 'xizi',
  particles: $('#particles'),
  wcHour: $('#wcHour'),
  /* bottom dock */
  dock: $('#bottomDock'),
  trigger: $('#bottomTrigger'),
  panels: $('#dockPanels'),
  /* scene mini title */
  sceneMiniIcon: $('#sceneMiniIcon'),
  sceneMiniText: $('#sceneMiniText'),
  /* dock char strip */
  dcsName: $('#dcsName'),
  dcsStatus: $('#dcsStatus'),
  dcsHpFill: $('#dcsHpFill'),
  /* panel bodies */
  panelChainBody: $('#panelChainBody'),
  panelLMInputBody: $('#panelLMInputBody'),
  panelCognitionBody: $('#panelCognitionBody'),
  panelObservationBody: $('#panelObservationBody'),
  panelStatsBody: $('#panelStatsBody'),
  /* drill-down drawer */
  drawer: $('#detailDrawer'),
  drawerCard: $('#detailDrawerCard'),
  drawerTitle: $('#detailDrawerTitle'),
  drawerClose: $('#detailDrawerClose'),
  drawerBody: $('#detailDrawerBody'),
};

/* ── 场景 → 动作自动映射 ── */
const SCENE_TO_ACTION = {
  idle: 'rest', planning: 'work', drive: 'organize',
  memory: 'organize', maintenance: 'organize', dispatch: 'write',
};
const GLYPHS = {
  idle: '·', planning: '!', drive: '✦', memory: 'λ',
  maintenance: '¶', dispatch: '⟩',
};
const SCENE_ICONS = {
  idle: '🛋', planning: '💻', drive: '📚', memory: '🧠',
  maintenance: '🔧', dispatch: '✍️',
};

/* ── 工具函数 ── */
function taskLane(t) {
  return String(t.lane || '').trim() || (
    String(t.governance_task_type || '').trim() === 'self_learning' ||
    String(t.execution_kind || '').trim() === 'body_improvement'
      ? 'agent' : 'supervisor'
  );
}
function typeLabel(t) {
  const observationRole = String(t.observation_role || '').trim();
  if (observationRole === 'mem_writeback') return 'Mem 写回';
  if (observationRole === 'api_b_reread') return '再次判断';
  if (observationRole === 'api_b_judgement') return 'API-B 判断';
  if (observationRole === 'api_a_execution') return 'API-A 执行回报';
  if (observationRole === 'candidate') return '候选判断';
  const identity = t.task_identity || {};
  const displayKind = String(identity.display_kind || t.execution_kind || '').trim();
  const governance = String(t.governance_task_type || '').trim();
  const primary = displayKind || governance || String(t.task_family || '').trim();
  const typeMap = {
    self_learning: '自主学习', body_improvement: '替身改进',
    memory_maintenance: '记忆维护', self_evolution: '通用演化',
    general_self_evolution: '通用演化',
  };
  return typeMap[primary] || primary.replace(/_/g, ' ') || '链路项';
}
function taskIdentityHint(t) {
  const identity = t.task_identity || {};
  const family = String(identity.task_family || t.task_family || '').trim();
  const displayKind = String(identity.display_kind || identity.execution_kind || '').trim();
  if (family && displayKind && family !== displayKind) return '执行家族: ' + family + ' · 执行动作: ' + displayKind;
  if (displayKind) return '执行类型: ' + displayKind;
  if (family) return '执行家族: ' + family;
  return '';
}
function governanceHint(t) {
  const preview = t.governance_preview || {};
  const direct = preview.lm_governance_review || null;
  const shadow = preview.lm_governance_shadow || null;
  if (direct && direct.action) return '监督者已裁定: ' + String(direct.action) + ' · ' + String(direct.reason || '').slice(0, 80);
  if (shadow && shadow.action) {
    let extra = '';
    if (shadow.merge_into) extra = ' -> ' + String(shadow.merge_into).slice(0, 16);
    else if (shadow.priority) extra = ' -> ' + String(shadow.priority);
    return '监督者建议: ' + String(shadow.action) + extra + ' · ' + String(shadow.reason || '').slice(0, 80);
  }
  return '';
}
function statusLabel(s) {
  const map = {
    planned:'待审核',
    approved:'待执行',
    running:'执行中',
    active:'当前在途',
    ready:'已观察到',
    idle:'等待中',
    queued:'等待中',
    candidate:'候选判断',
    deferred:'已推迟',
    paused:'已暂停',
    completed:'已完成',
    failed:'失败',
    cancelled:'已取消',
    awaiting_review:'待审查',
    retry:'重试',
  };
  return map[s] || s || '待定';
}
function rarityClass(task) {
  const u = task.utility || 0;
  if (u >= 0.85) return 'rarity-mythic';
  if (u >= 0.70) return 'rarity-legendary';
  if (u >= 0.55) return 'rarity-epic';
  if (u >= 0.35) return 'rarity-rare';
  if (u >= 0.15) return 'rarity-uncommon';
  return 'rarity-common';
}
function tagClass(tag) {
  if (/memory/i.test(tag)) return 'memory';
  if (/learn/i.test(tag)) return 'learning';
  if (/evolution|body/i.test(tag)) return 'evolution';
  if (/truth/i.test(tag)) return 'truthfulness';
  if (/creat/i.test(tag)) return 'creativity';
  return '';
}
function scoreClass(v) { return v >= 0.7 ? 'high' : v >= 0.4 ? 'mid' : 'low'; }
function hpPercent(state) { return Math.max(0, 100 - (state.error_count || 0) * 10); }

/* ═══════════════════════════════════════════
   底部 Dock 交互逻辑
   ═══════════════════════════════════════════ */
let dockVisible = false, dockHovered = false, panelOpen = null;
let dockHideTimer = null;

function showDock() {
  if (!dockVisible) { dockVisible = true; els.dock.classList.add('visible'); }
  if (dockHideTimer) { clearTimeout(dockHideTimer); dockHideTimer = null; }
}
function hideDock() {
  if (dockHideTimer) clearTimeout(dockHideTimer);
  dockHideTimer = setTimeout(() => {
    if (!dockHovered && !panelOpen) {
      dockVisible = false;
      els.dock.classList.remove('visible');
    }
  }, 600);
}

// 底部触发区 hover
if (els.trigger) {
  els.trigger.addEventListener('mouseenter', showDock);
  els.trigger.addEventListener('mouseleave', hideDock);
}
// Dock 自身 hover
if (els.dock) {
  els.dock.addEventListener('mouseenter', () => { dockHovered = true; showDock(); });
  els.dock.addEventListener('mouseleave', () => { dockHovered = false; hideDock(); });
}

/* ── 面板切换 ── */
function openPanel(name) {
  if (panelOpen && panelOpen !== name) closePanel(panelOpen);
  const panelId = 'panel' + name.charAt(0).toUpperCase() + name.slice(1).replace(/input$/i, 'Input');
  const panel = document.getElementById(panelId);
  if (!panel) return;
  panel.classList.add('open');
  panelOpen = name;
  $$('.dock-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.panel === name);
  });
  showDock();
  // 立即渲染该面板
  if (lastState) {
    if (name === 'chain') renderChainPanel(lastState);
    if (name === 'lminput') renderLMInputPanel(lastState);
    if (name === 'cognition') renderCognitionPanel(lastState);
    if (name === 'observation') renderObservationPanel(lastState);
    if (name === 'stats') renderStatsPanel(lastState);
  }
}
function closePanel(name) {
  const panelId = 'panel' + name.charAt(0).toUpperCase() + name.slice(1).replace(/input$/i, 'Input');
  const panel = document.getElementById(panelId);
  if (!panel) return;
  panel.classList.remove('open');
  if (panelOpen === name) panelOpen = null;
  $$('.dock-btn').forEach(b => {
    if (b.dataset.panel === name) b.classList.remove('active');
  });
}

// Dock 按钮点击 → 切换面板
if (els.dock) {
  els.dock.addEventListener('click', e => {
    const btn = e.target.closest('.dock-btn');
    if (!btn) return;
    // 动作按钮
    if (btn.dataset.actionBtn) {
      const a = btn.dataset.actionBtn;
      setAction(a, false);
      userPickedAction = { scene: '__manual__', action: a };
      updateDockActionButtons();
      return;
    }
    // 面板按钮
    const panelName = btn.dataset.panel;
    if (!panelName) return;
    if (panelOpen === panelName) { closePanel(panelName); return; }
    openPanel(panelName);
  });
}

// 面板关闭按钮
document.addEventListener('click', e => {
  const closeBtn = e.target.closest('.panel-close');
  if (closeBtn) {
    const name = closeBtn.dataset.panel;
    if (name) closePanel(name);
  }
});

// 点击面板外区域关闭
document.addEventListener('click', e => {
  if (!panelOpen) return;
  const panelId = 'panel' + panelOpen.charAt(0).toUpperCase() + panelOpen.slice(1).replace(/input$/i, 'Input');
  const panel = document.getElementById(panelId);
  if (!panel) return;
  if (!panel.contains(e.target) && !e.target.closest('.bottom-dock')) {
    closePanel(panelOpen);
  }
});

/* ═══════════════════════════════════════════
   drill-down 详情抽屉
   ═══════════════════════════════════════════ */
let drawerOpen = null;  // 当前抽屉类型: 'autonomous' | 'provenance' | 'health' | null
let drawerContext = {};

const DRAWER_META = {
  autonomous: { icon: '🚦', title: '自主链路观测总览' },
  provenance: { icon: '🔎', title: '内生驱动决策溯源' },
  health:     { icon: '💗', title: '身体 / 记忆健康度' },
};

function openDrawer(type, context) {
  if (!els.drawer || !DRAWER_META[type]) return;
  drawerOpen = type;
  drawerContext = context || {};
  const meta = DRAWER_META[type];
  if (els.drawerTitle) els.drawerTitle.innerHTML = '<span>' + meta.icon + '</span>' + meta.title;
  renderDrawer();
  els.drawer.classList.add('open');
}

function closeDrawer() {
  drawerOpen = null;
  drawerContext = {};
  if (els.drawer) els.drawer.classList.remove('open');
}

function renderDrawer() {
  if (!drawerOpen || !els.drawerBody) return;
  const state = lastState || {};
  if (drawerOpen === 'autonomous') renderAutonomousDrawer(state);
  else if (drawerOpen === 'provenance') renderProvenanceDrawer(state);
  else if (drawerOpen === 'health') renderHealthDrawer(state);
}

// 抽屉关闭交互
if (els.drawerClose) els.drawerClose.addEventListener('click', closeDrawer);
if (els.drawer) {
  els.drawer.addEventListener('click', e => {
    if (e.target === els.drawer) closeDrawer();  // 点遮罩关闭
  });
}
if (els.drawerCard) {
  // 防止抽屉内点击冒泡到"点击面板外关闭"监听
  els.drawerCard.addEventListener('click', e => e.stopPropagation());
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && drawerOpen) closeDrawer();
});
// 入口点击(事件委托): 任意带 data-drill 的元素
document.addEventListener('click', e => {
  const trigger = e.target.closest('[data-drill]');
  if (!trigger) return;
  e.stopPropagation();
  const context = {};
  if (trigger.dataset.chainGroup) context.chainGroup = String(trigger.dataset.chainGroup);
  if (trigger.dataset.chainTrace) context.chainTrace = String(trigger.dataset.chainTrace);
  if (trigger.dataset.chainTraceExpanded) {
    context.chainTraceExpanded = String(trigger.dataset.chainTraceExpanded) === 'true';
  }
  if (trigger.dataset.chainTraceSource) {
    context.chainTraceSource = String(trigger.dataset.chainTraceSource);
  }
  openDrawer(trigger.dataset.drill, context);
});

function drillButton(type, label) {
  return '<span class="drill-link" data-drill="' + type + '">🔬 ' + label + '</span>';
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function pct(v) { return (v != null && !isNaN(v)) ? Math.round(v * 100) + '%' : '—'; }

function autonomousLoopStatusLabel(status) {
  const map = {
    active: '当前在途',
    ready: '已观察到',
    idle: '等待中',
  };
  return map[String(status || '').trim()] || '等待中';
}

function autonomousLoopStageStatusText(stage) {
  const explicit = String((stage || {}).status_label || '').trim();
  if (explicit) return explicit;
  return autonomousLoopStatusLabel((stage || {}).status || 'idle');
}

function autonomousLoopStageFocusTitle(stage) {
  const focusTask = (stage || {}).focus_task || {};
  return String(focusTask.title || '').trim();
}

function autonomousLoopStageReason(stage) {
  return String((stage || {}).chain_reason || '').trim();
}

function autonomousLoopStageActivity(stage) {
  return String((stage || {}).activity_text || '').trim();
}

function observationStateBadgeClass(status) {
  const normalized = String(status || '').trim().toLowerCase();
  if (normalized === 'active' || normalized === 'running') return 'running';
  if (normalized === 'ready' || normalized === 'approved') return 'approved';
  if (normalized === 'completed') return 'completed';
  if (normalized === 'failed') return 'failed';
  if (normalized === 'deferred') return 'deferred';
  return 'planned';
}

function renderAutonomousLoop(loop, options) {
  const opts = options || {};
  const stages = Array.isArray(loop.stages) ? loop.stages : [];
  const writebacks = Array.isArray(loop.recent_writebacks) ? loop.recent_writebacks : [];
  if (!stages.length) return '';

  let html = '<div class="autonomous-loop">';
  if (loop.boundary) {
    html += '<div class="autonomous-loop-boundary">' + esc(loop.boundary) + '</div>';
  }
  html += '<div class="autonomous-loop-stages">';
  html += stages.map(stage => {
    const status = String(stage.status || 'idle').trim() || 'idle';
    const summary = String(stage.summary || '暂无信号').trim() || '暂无信号';
    const statusText = autonomousLoopStageStatusText(stage);
    const focusTitle = autonomousLoopStageFocusTitle(stage);
    const reason = autonomousLoopStageReason(stage);
    const activity = autonomousLoopStageActivity(stage);
    return '<div class="autonomous-loop-stage ' + esc(status) + '">' +
      '<div class="autonomous-loop-topline"><span class="autonomous-loop-label">' + esc(stage.label || '阶段') +
      '</span><span class="autonomous-loop-owner">' + esc(stage.owner || '—') + '</span></div>' +
      '<div class="autonomous-loop-status">' + esc(statusText) + '</div>' +
      (focusTitle ? '<div class="autonomous-loop-focus">' + esc(focusTitle).substring(0, 72) + '</div>' : '') +
      '<div class="autonomous-loop-summary">' + esc(summary).substring(0, 120) + '</div>' +
      (reason && reason !== summary ? '<div class="autonomous-loop-reason">' + esc(reason).substring(0, 120) + '</div>' : '') +
      (activity && activity !== reason ? '<div class="autonomous-loop-activity">' + esc(activity).substring(0, 120) + '</div>' : '') +
      '</div>';
  }).join('');
  html += '</div>';
  if (opts.showWritebacks && writebacks.length) {
    html += '<div class="autonomous-loop-writebacks">' +
      writebacks.slice(0, 3).map(item =>
        '<div class="autonomous-loop-writeback">Mem 写回: ' +
        esc(item.title || '未命名') + ' · ' + esc(item.status_label || '—') +
        (item.summary ? ' · ' + esc(String(item.summary).substring(0, 80)) : '') +
        '</div>'
      ).join('') +
      '</div>';
  }
  html += '</div>';
  return html;
}

function chainSegments(observation) {
  const obs = observation || {};
  const chain = obs.chain || {};
  return Array.isArray(chain.segments) ? chain.segments : [];
}

function chainGroups(observation, keys) {
  const groups = chainSegments(observation);
  if (!Array.isArray(keys) || !keys.length) return groups;
  const wanted = new Set(keys);
  return groups.filter(group => wanted.has(group.key)).sort((left, right) => {
    const leftOrder = Number((left || {}).order || 0);
    const rightOrder = Number((right || {}).order || 0);
    return leftOrder - rightOrder;
  });
}

function chainGroupByKey(observation, key) {
  const groups = chainSegments(observation);
  return groups.find(group => String((group || {}).key || '').trim() === String(key || '').trim()) || null;
}

function loopStages(observation) {
  const obs = observation || {};
  const loop = obs.loop || {};
  return Array.isArray(loop.stages) ? loop.stages : [];
}

function loopStageByKey(loop, key) {
  const stages = Array.isArray((loop || {}).stages) ? loop.stages : [];
  return stages.find(stage => String((stage || {}).key || '').trim() === String(key || '').trim()) || null;
}

function loopStageObservationRole(stageKey) {
  const normalized = String(stageKey || '').trim();
  const roleMap = {
    api_b_judgement: 'api_b_judgement',
    api_a_execution: 'api_a_execution',
    mem_writeback: 'mem_writeback',
    api_b_reread: 'api_b_reread',
  };
  return roleMap[normalized] || normalized || 'autonomous_observation';
}

function observationLoopStageProjection(stage) {
  const row = stage && typeof stage === 'object' ? stage : {};
  const focusTask = row.focus_task && typeof row.focus_task === 'object' ? row.focus_task : {};
  const owner = String(row.owner || '').trim();
  const rawStatus = String(row.status || focusTask.status || 'idle').trim().toLowerCase() || 'idle';
  const lane = owner === 'API-A' ? 'agent' : (owner === 'Mem' ? 'mem' : 'supervisor');
  return {
    ...focusTask,
    title: String(focusTask.title || row.label || '阶段').trim() || '阶段',
    status: rawStatus,
    display_status: String(
      focusTask.display_status
      || row.status_label
      || autonomousLoopStageStatusText(row)
      || '等待中'
    ).trim() || '等待中',
    summary: String(
      focusTask.summary
      || row.summary
      || row.chain_reason
      || row.activity_text
      || ''
    ).trim(),
    observation_role: loopStageObservationRole(row.key),
    lane,
    stage_key: String(row.key || '').trim(),
    stage_owner: owner || '—',
  };
}

function observationCounts(observation) {
  const obs = observation || {};
  return obs.counts || {};
}

function isCreativityObservationTask(task) {
  const row = task || {};
  const governance = String(row.governance_task_type || '').trim().toLowerCase();
  const taskFamily = String(row.task_family || '').trim().toLowerCase();
  const executionKind = String(row.execution_kind || '').trim().toLowerCase();
  return governance === 'self_learning' || taskFamily === 'self_learning' || taskFamily === 'body_upgrade' || executionKind === 'body_improvement';
}

function creativityGovernanceBacklogCount(observation) {
  const backlog = chainGroupByKey(observation, 'api_b_backlog') || {};
  const items = Array.isArray(backlog.items) ? backlog.items : [];
  return items.filter(item => isCreativityObservationTask(item)).length;
}

function observationRuntime(observation) {
  const obs = observation || {};
  return obs.runtime || {};
}

function observationFocus(observation) {
  const obs = observation || {};
  const board = obs.board || {};
  const primary = board.primary_focus || {};
  const stages = loopStages(obs);
  const activeStage = stages.find(stage =>
    ['active', 'ready'].includes(String((stage || {}).status || '').trim().toLowerCase())
  ) || stages[0] || {};
  const focusStage = (
    (primary.stage_key ? loopStageByKey(obs.loop || {}, primary.stage_key) : null)
    || activeStage
    || {}
  );
  const focusProjection = observationLoopStageProjection(focusStage);
  return {
    title: String(primary.title || focusProjection.title || '自主链路闭环').trim() || '自主链路闭环',
    status: String(primary.status || focusProjection.display_status || '等待中').trim() || '等待中',
    stage_status: String(primary.stage_status || focusProjection.status || 'idle').trim().toLowerCase() || 'idle',
    stage_key: String(primary.stage_key || focusProjection.stage_key || '').trim(),
    stage_owner: String(primary.stage_owner || focusProjection.stage_owner || '').trim(),
    summary: String(primary.summary || focusProjection.summary || board.summary || '').trim(),
    observation_role: String(primary.observation_role || focusProjection.observation_role || '').trim(),
  };
}

function derivedHeroPills(observation) {
  const runtime = observationRuntime(observation);
  const guards = runtime.activity_guards || {};
  const userSignal = guards.user_chain_signal || {};
  const focus = observationFocus(observation);
  const counts = observationCounts(observation);
  const creativityBacklog = creativityGovernanceBacklogCount(observation);
  return [
    {
      key: 'focus',
      tone: 'accent',
      text: '当前焦点 · ' + (focus.title || '自主链路闭环'),
    },
    {
      key: 'chain_waiting',
      tone: creativityBacklog ? 'warn' : (intOrZero(counts.api_a_ready) ? 'accent' : 'info'),
      text: creativityBacklog
        ? ('治理段待放行 · ' + creativityBacklog)
        : (intOrZero(counts.api_a_ready)
            ? ('API-A 待拉取 · ' + intOrZero(counts.api_a_ready))
            : ('Mem 最近写回 · ' + intOrZero(counts.writebacks))),
    },
    {
      key: 'user_chain_signal',
      tone: userSignal.is_quiet ? 'good' : 'warn',
      text: userSignal.is_quiet ? '用户链路安静软信号' : '用户链路活跃软信号',
    },
    {
      key: 'autonomous_chain_gate',
      tone: 'info',
      text: runtime.autonomous_chain_gate_active ? '临时开关 · 已开启' : '临时开关 · 已关闭',
    },
  ];
}

function derivedMetricCards(observation) {
  const counts = observationCounts(observation);
  return [
    {key:'api_b_backlog', cls:'api-b', label:'API-B 判断', value:intOrZero(counts.api_b_backlog), note:'治理在途'},
    {key:'api_a_ready', cls:'api-a', label:'API-A 待拉取', value:intOrZero(counts.api_a_ready), note:'自主执行面'},
    {key:'api_b_candidates', cls:'candidate', label:'候选判断', value:intOrZero(counts.candidates), note:'待裁决'},
    {key:'mem_recent', cls:'mem', label:'Mem 写回', value:intOrZero(counts.writebacks), note:'结果回流'},
  ];
}

function derivedLoopRailEntries(observation) {
  const obs = observation || {};
  const loop = obs.loop || {};
  const stages = Array.isArray(loop.stages) ? loop.stages : [];
  const focus = observationFocus(observation);
  return stages.map(stage => {
    const stageKey = String((stage || {}).key || '').trim();
    const focusRole = String(focus.observation_role || '').trim();
    const isFocus =
      (stageKey === 'api_b_judgement' && focusRole === 'api_b_judgement') ||
      (stageKey === 'api_a_execution' && focusRole === 'api_a_execution') ||
      (stageKey === 'mem_writeback' && focusRole === 'mem_writeback') ||
      (stageKey === 'api_b_reread' && focusRole === 'api_b_reread');
    return {
      key: stageKey,
      label: String((stage || {}).label || '阶段').trim() || '阶段',
      owner: String((stage || {}).owner || '—').trim() || '—',
      status: String((stage || {}).status || 'idle').trim().toLowerCase() || 'idle',
      state: autonomousLoopStageStatusText(stage),
      note: autonomousLoopStageFocusTitle(stage) || String((stage || {}).summary || '').trim(),
      focus: isFocus,
    };
  });
}

function derivedObservationNotes(observation) {
  const runtime = observationRuntime(observation);
  const guards = runtime.activity_guards || {};
  const userSignal = guards.user_chain_signal || {};
  const counts = observationCounts(observation);
  const focus = observationFocus(observation);
  const creativityBacklog = creativityGovernanceBacklogCount(observation);
  const notes = [
    {
      key:'api_b_scope',
      tone:'info',
      title:'API-B 主视角',
      text:'Web 小屋只观察 API-B 判断、治理反馈、Mem 回流，以及 API-A 回报给 API-B 的自主链路项状态。',
    },
    {
      key:'api_a_feedback',
      tone:'accent',
      title:'API-A 执行回报',
      text:'当前待拉取 ' + intOrZero(counts.api_a_ready) +
        ' · 当前焦点 ' + (focus.title || '暂无') +
        ' · Web 只把 API-A 视为自主链路执行与回报来源。',
    },
    {
      key:'user_chain_signal',
      tone:userSignal.is_quiet ? 'good' : 'warn',
      title:'用户链路软感知',
      text:'活跃会话 ' + intOrZero(userSignal.active_sessions) +
        ' · 软静默阈值 ' + (userSignal.quiet_after_seconds != null ? userSignal.quiet_after_seconds : '—') +
        's · 只影响 API-B 判断让路，不展示用户聊天内容。',
    },
  ];
  if (creativityBacklog) {
    notes.splice(1, 0, {
      key:'governance_waiting',
      tone:'warn',
      title:'治理段待放行',
      text:'仍有 ' + creativityBacklog + ' 个创造类链路项停留在 API-B 治理段，等待监督者补证、重排或放行后，才会进入 API-A 待拉取段。',
    });
  }
  return notes;
}

function intOrZero(value) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(0, Math.trunc(n)) : 0;
}

function chainTraceSummary(group, traceId) {
  const traces = Array.isArray((group || {}).recent_traces) ? group.recent_traces : [];
  return traces.find(item => String((item || {}).trace_id || '').trim() === String(traceId || '').trim()) || null;
}

function autonomousEventTypeLabel(value) {
  const normalized = String(value || '').trim();
  const labels = {
    task_decided: '链路裁决',
    tasks_reviewed: '批量复核',
    tasks_planned: '链路规划',
    supervisor_activity: '监督活动',
    trace_marker: '轨迹标记',
    completed: '已完成',
    running: '执行中',
    approved: '已放行',
    planned: '已规划',
    failed: '执行失败',
    cancelled: '已取消',
    event: '事件',
  };
  return labels[normalized] || normalized || '事件';
}

function autonomousEventSourceLabel(value) {
  const normalized = String(value || '').trim();
  const labels = {
    supervisor_activity: '监督活动',
    autonomous_chain_store: '链路存储',
    mem_governor_history: '治理历史',
    gateway_activity_log: '网关活动',
    governor: '治理器',
  };
  return labels[normalized] || normalized || '未知来源';
}

function shortClock(ts) {
  if (!ts) return '最近';
  try {
    return new Date(ts).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  } catch (_err) {
    return '最近';
  }
}

function createBoardSectionLabel(text) {
  const label = document.createElement('div');
  label.className = 'board-section-label';
  label.textContent = text;
  return label;
}

function observationGroupCount(group) {
  if (group && typeof group.payload_count === 'number') return group.payload_count;
  return Array.isArray((group || {}).items) ? group.items.length : 0;
}

function observationGroupTraceCount(group) {
  if (group && typeof group.trace_count === 'number') return group.trace_count;
  return Array.isArray((group || {}).recent_traces) ? group.recent_traces.length : 0;
}

function observationGroupEventCount(group) {
  if (group && typeof group.event_count === 'number') return group.event_count;
  return Array.isArray((group || {}).recent_events) ? group.recent_events.length : 0;
}

function observationGroupFocusItem(group) {
  if (group && group.focus_item && typeof group.focus_item === 'object') return group.focus_item;
  if (group && group.latest_item && typeof group.latest_item === 'object') return group.latest_item;
  const items = Array.isArray((group || {}).items) ? group.items : [];
  return items[0] || null;
}

function observationGroupBadgeClass(group) {
  return observationStateBadgeClass((group || {}).segment_status || 'planned');
}

function observationSegmentDecor(group) {
  const key = String((group || {}).key || '').trim();
  if (key === 'api_b_candidates') return {cls: 'candidate', icon: '🪄'};
  if (key === 'api_a_ready') return {cls: 'agent', icon: '🤖'};
  if (key === 'mem_recent') return {cls: 'mem', icon: '💾'};
  return {cls: 'supervisor', icon: '🧠'};
}

function buildChainSectionBand(group, options) {
  const opts = options || {};
  const limit = typeof opts.limit === 'number' ? opts.limit : 3;
  const band = document.createElement('section');
  band.className = 'watch-band';
  const recentEvents = Array.isArray(group.recent_events) ? group.recent_events : [];
  const latestEvent = recentEvents[0] || null;
  const focusItem = observationGroupFocusItem(group);
  const payloadCount = observationGroupCount(group);
  const eventCount = observationGroupEventCount(group);
  const traceCount = observationGroupTraceCount(group);
  const latestSummary = String(
    group.latest_summary
    || (latestEvent && (latestEvent.summary || autonomousEventTypeLabel(latestEvent.event_type)))
    || group.summary
    || group.empty_text
    || '暂无链路信号'
  ).substring(0, 96);

  const head = document.createElement('div');
  head.className = 'watch-band-head';
  head.innerHTML =
    '<div class="watch-band-title-wrap">' +
      '<div class="watch-band-title">' + esc(group.label || '链路分段') + '</div>' +
      '<div class="watch-band-subline">' +
        '<span class="watch-band-owner">' + esc(group.owner || '自主链路') + '</span>' +
        '<span class="watch-band-stage">' + esc(group.stage_label || '链路阶段') + '</span>' +
      '</div>' +
      '<div class="watch-band-summary">' + esc(latestSummary) + '</div>' +
      (latestEvent
        ? '<div class="watch-band-latest">最近 ' + esc(shortClock(latestEvent.recorded_at)) +
          ' · ' + esc(String(latestEvent.summary || autonomousEventTypeLabel(latestEvent.event_type) || '链路事件').substring(0, 88)) + '</div>'
        : '') +
    '</div>' +
    '<div style="display:grid;justify-items:end;gap:6px;">' +
      '<span class="game-card-badge ' + observationGroupBadgeClass(group) + '">' +
      esc(group.segment_status_label || '链路分段') + '</span>' +
      '<span class="watch-band-count">' + payloadCount + '</span>' +
    '</div>';
  band.append(head);

  const body = document.createElement('div');
  body.className = 'watch-band-body';
  const summaryCard = document.createElement('div');
  summaryCard.className = 'game-card rarity-common';
  summaryCard.innerHTML =
    '<div class="game-card-head"><div class="game-card-title">' +
    esc(focusItem && focusItem.title ? String(focusItem.title).substring(0, 48) : (group.label || '链路分段')) +
    '</div><span class="game-card-badge ' + observationGroupBadgeClass(group) + '">' +
    esc(group.segment_status_label || '链路观察') + '</span></div>' +
    '<div class="game-card-sub">' +
    esc(latestSummary || group.empty_text || '暂无链路信号') +
    '</div>' +
    '<div class="game-card-meta"><div class="game-card-tags">' +
    '<span class="game-card-tag memory">载荷 ' + esc(payloadCount) + '</span>' +
    '<span class="game-card-tag truthfulness">事件 ' + esc(eventCount) + '</span>' +
    '<span class="game-card-tag creativity">轨迹 ' + esc(traceCount) + '</span>' +
    '</div></div>';
  body.append(summaryCard);
  const items = Array.isArray(group.items) ? group.items : [];
  if (!items.length) {
    if (!recentEvents.length && !traceCount) {
      const empty = document.createElement('div');
      empty.className = 'game-card rarity-common';
      empty.innerHTML =
        '<div class="game-card-sub" style="text-align:center;color:var(--text-muted);">' +
        esc(group.empty_text || '暂无信号') +
        '</div>';
      body.append(empty);
    }
  } else {
    items.slice(0, limit).forEach(item => {
      if (String(item.observation_role || '').trim() === 'candidate') {
        body.append(buildCandidateCard(item));
      } else {
        body.append(buildSectionCard(item));
      }
    });
  }
  band.append(body);

  const footer = document.createElement('div');
  footer.className = 'watch-band-footer';
  footer.innerHTML =
    '<span class="watch-band-footnote">' +
    esc(
      payloadCount || eventCount || traceCount
        ? ('载荷 ' + payloadCount + ' · 事件 ' + eventCount + ' · 轨迹 ' + traceCount)
        : '查看这一段自主链路的最近事件'
    ) +
    '</span>' +
    '<span class="drill-link" data-drill="autonomous" data-chain-group="' + esc(group.key || '') + '">🔬 段内链路</span>';
  band.append(footer);
  return band;
}

function appendLoopStageGrid(container, observation, stageKeys, title) {
  const stages = loopStages(observation);
  const items = (Array.isArray(stageKeys) && stageKeys.length
    ? stageKeys.map(key => loopStageByKey((observation || {}).loop || {}, key)).filter(Boolean)
    : stages
  ).map(stage => observationLoopStageProjection(stage))
   .filter(item => item && Object.keys(item).length);
  if (!container || !items.length) return;
  const section = document.createElement('section');
  section.className = 'observation-stack';
  section.append(createBoardSectionLabel(title || '闭环当前态'));
  const grid = document.createElement('div');
  grid.className = 'current-card-grid';
  items.forEach(card => {
    grid.append(buildStageCard(card));
  });
  section.append(grid);
  container.append(section);
}

function appendChainSectionGrid(container, state, keys, options) {
  if (!container) return;
  const observation = ((state || {}).autonomous_observation || {});
  const chain = observation.chain || {};
  const groups = chainGroups(observation, keys);
  if (!groups.length) return;
  const section = document.createElement('section');
  section.className = 'observation-stack';
  section.append(createBoardSectionLabel(chain.headline || '链路分段观察'));
  const grid = document.createElement('div');
  grid.className = 'chain-watch-grid';
  groups.forEach(group => grid.append(buildChainSectionBand(group, options)));
  section.append(grid);
  container.append(section);
}

function buildChainHero(state) {
  const obs = state.autonomous_observation || {};
  const board = obs.board || {};
  const chain = obs.chain || {};
  const loop = obs.loop || {};
  const focus = observationFocus(obs);
  const railEntries = derivedLoopRailEntries(obs);
  const focusStage = loopStageByKey(loop, focus.stage_key) || {};
  const focusCard = observationLoopStageProjection(focusStage);
  const focusBadgeClass = observationStateBadgeClass(focus.stage_status || focusCard.status || focus.status);
  const hero = document.createElement('section');
  hero.className = 'chain-hero';
  hero.innerHTML =
    '<div class="chain-hero-top">' +
      '<div class="chain-hero-main">' +
        '<div class="chain-hero-label">自主链路读模型 · v' + esc(obs.read_model_version != null ? obs.read_model_version : 7) + '</div>' +
        '<div class="chain-hero-title">' + esc(board.headline || '自主链路闭环观测') + '</div>' +
        '<div class="chain-hero-summary">' + esc(focus.summary || board.summary || chain.summary || 'Web 小屋以 API-B 为主视角，只观察判断、治理、Mem 回流与 API-A 执行回报。').substring(0, 220) + '</div>' +
      '</div>' +
      '<div class="chain-hero-focus">' +
        '<span class="game-card-badge ' + focusBadgeClass + '">' + esc(focus.status || focusCard.display_status || '等待中') + '</span>' +
        '<div class="chain-hero-focus-title">' + esc(focus.title || '当前没有突出焦点') + '</div>' +
      '</div>' +
    '</div>';

  if (railEntries.length) {
    const rail = document.createElement('div');
    rail.className = 'chain-stage-rail';
    rail.innerHTML = railEntries.map(entry =>
      '<div class="chain-stage-stop ' + esc(entry.status || 'idle') + (entry.focus ? ' focus' : '') + '">' +
        '<div class="chain-stage-kicker"><span>' + esc(entry.owner || '—') + '</span>' +
        '<span>' + esc(entry.focus ? '当前落点' : '闭环段') + '</span></div>' +
        '<div class="chain-stage-name">' + esc(entry.label || '阶段') + '</div>' +
        '<div class="chain-stage-state">' + esc(entry.state || '等待中') + '</div>' +
        '<div class="chain-stage-note">' + esc(String(entry.note || loop.boundary || '暂无信号')).substring(0, 68) + '</div>' +
      '</div>'
    ).join('');
    hero.append(rail);
  }

  const meta = document.createElement('div');
  meta.className = 'chain-hero-meta';
  const heroPills = derivedHeroPills(obs);
  heroPills.forEach(item => {
    const pill = document.createElement('div');
    pill.className = 'chain-focus-pill ' + esc(item.tone || 'accent');
    pill.textContent = String(item.text || '');
    meta.append(pill);
  });
  hero.append(meta);

  const metrics = document.createElement('div');
  metrics.className = 'chain-metrics';
  const metricCards = derivedMetricCards(obs);
  metricCards.forEach(item => {
    const box = document.createElement('div');
    box.className = 'chain-metric ' + esc(item.cls || 'neutral');
    box.innerHTML =
      '<div class="chain-metric-label">' + esc(item.label) + '</div>' +
      '<div class="chain-metric-value">' + esc(item.value) + '</div>' +
      '<div class="chain-metric-note">' + esc(item.note) + '</div>';
    metrics.append(box);
  });
  hero.append(metrics);
  return hero;
}

/* ── 🚦 自主链路观测总览 ── */
function renderAutonomousDrawer(state) {
  const obs = state.autonomous_observation || {};
  const runtime = obs.runtime || {};
  const guards = runtime.activity_guards || {};
  const userSignal = guards.user_chain_signal || {};
  const loop = obs.loop || {};
  const orderedGroups = chainGroups(obs);
  const rereadCard = observationLoopStageProjection(loopStageByKey(loop, 'api_b_reread') || {});
  const focusGroupKey = String((drawerContext || {}).chainGroup || '').trim();
  const focusTraceId = String((drawerContext || {}).chainTrace || '').trim();
  const focusTraceExpanded = Boolean((drawerContext || {}).chainTraceExpanded);
  const focusTraceSource = String((drawerContext || {}).chainTraceSource || '').trim();
  const focusGroup = focusGroupKey ? chainGroupByKey(obs, focusGroupKey) : null;
  const focusEvents = Array.isArray((focusGroup || {}).recent_events) ? focusGroup.recent_events : [];
  const focusTraces = Array.isArray((focusGroup || {}).recent_traces) ? focusGroup.recent_traces : [];
  const selectedTrace = focusTraceId ? chainTraceSummary(focusGroup, focusTraceId) : null;
  const selectedTraceDetail = selectedTrace && selectedTrace.detail ? selectedTrace.detail : null;
  const selectedTraceEvents = selectedTrace
    ? focusEvents.filter(event => String(event.trace_id || '').trim() === focusTraceId)
    : focusEvents;

  function segmentColFromGroup(cls, icon, group) {
    const focusItem = observationGroupFocusItem(group);
    const payloadCount = observationGroupCount(group);
    const eventCount = observationGroupEventCount(group);
    const traceCount = observationGroupTraceCount(group);
    const focusSummary = String(
      (group && group.latest_summary)
      || (focusItem && focusItem.summary)
      || (group && group.summary)
      || (group && group.empty_text)
      || '暂无链路信号'
    ).substring(0, 72);
    const focusTitle = focusItem && focusItem.title
      ? String(focusItem.title).substring(0, 48)
      : (group.label || '链路分段');
    return '<div class="segment-col ' + cls + '">' +
      '<div class="segment-col-head">' + icon + ' ' + esc(group.label || '链路分段') +
      ' <span class="segment-col-tag">' + esc(group.stage_label || '链路阶段') + '</span></div>' +
      '<div class="segment-metric"><span>链路载荷</span><b>' + payloadCount + '</b></div>' +
      '<div class="segment-metric"><span>最近事件</span><b>' + eventCount + '</b></div>' +
      '<div class="segment-metric"><span>最近轨迹</span><b>' + traceCount + '</b></div>' +
      '<div class="segment-active"><div class="la-title">' + esc(focusTitle) +
      '</div><div style="margin-top:3px;">' + esc(group.segment_status_label || '链路观察') +
      (focusSummary ? ' · ' + esc(focusSummary) : '') + '</div></div></div>';
  }
  const userState = userSignal.is_quiet ? '安静软信号' : '活跃软信号';
  const activeSessions = userSignal.active_sessions != null ? userSignal.active_sessions : 0;
  const quietAfter = userSignal.quiet_after_seconds != null ? userSignal.quiet_after_seconds : '—';

  let html = '<div class="drawer-sub">Web 小屋只观察 API-B 内生驱动、自主链路项状态和执行回报；用户链路只作为软感知信号进入治理判断，不展示聊天内容。</div>';
  if (focusGroup) {
    const focusItems = Array.isArray(focusGroup.items) ? focusGroup.items : [];
    const focusTraceIds = [];
    focusEvents.forEach(event => {
      const traceId = String(event.trace_id || '').trim();
      if (traceId && !focusTraceIds.includes(traceId)) focusTraceIds.push(traceId);
    });
    html += '<div class="drawer-section">' +
      '<div class="drawer-section-label">' + esc(focusGroup.label || '链路分段') + '</div>' +
      '<div class="drawer-sub" style="margin:0;">' +
      esc(focusGroup.owner || '自主链路') + ' · ' +
      esc(focusGroup.stage_label || '链路阶段') + ' · ' +
      esc(focusGroup.summary || focusGroup.empty_text || '暂无链路说明') +
      '</div>' +
      '<div class="drawer-sub" style="margin-top:8px;">当前可见链路项 ' + focusItems.length +
      ' · 最近链路事件 ' + focusEvents.length +
      (focusTraceIds.length ? (' · 轨迹 ' + esc(focusTraceIds.slice(0, 3).join(' / '))) : '') +
      '</div>' +
      (focusItems.length
        ? focusItems.slice(0, 4).map(item =>
            '<div class="segment-active" style="margin-top:6px;"><div class="la-title">' +
            esc(String(item.title || '未命名').substring(0, 56)) + '</div>' +
            '<div style="margin-top:3px;">状态: ' + esc(item.display_status || item.status || '—') +
            (item.summary ? ' · ' + esc(String(item.summary).substring(0, 88)) : '') +
            '</div></div>'
          ).join('')
        : '<div class="drawer-sub" style="margin-top:8px;">当前该分段没有可见链路项，但仍可能有最近链路事件。</div>') +
      '</div>';
    html += '<div class="drawer-section"><div class="drawer-section-label">最近链路事件</div>' +
      (selectedTraceEvents.length
        ? selectedTraceEvents.slice(0, 6).map(event =>
            '<div class="segment-active" style="margin-top:6px;"><div class="la-title">' +
            esc(shortClock(event.recorded_at)) + ' · ' +
            esc(String(event.summary || autonomousEventTypeLabel(event.event_type) || '链路事件').substring(0, 84)) + '</div>' +
            '<div style="margin-top:3px;">来源: ' + esc(autonomousEventSourceLabel(event.source)) +
            (event.task_id ? ' · 链路项 ' + esc(String(event.task_id).substring(0, 16)) : '') +
            (event.trace_id ? ' · 轨迹 ' + esc(String(event.trace_id).substring(0, 18)) : '') +
            '</div></div>'
          ).join('')
        : '<div class="drawer-sub" style="margin:0;">该分段暂时没有命中最近轨迹记录。</div>') +
      '</div>';
    html += '<div class="drawer-section"><div class="drawer-section-label">最近轨迹摘要</div>' +
      (focusTraces.length
        ? '<div class="trace-chip-row">' +
          focusTraces.slice(0, 5).map(trace =>
            '<span class="trace-chip" data-drill="autonomous" data-chain-group="' + esc(focusGroup.key || '') +
            '" data-chain-trace="' + esc(trace.trace_id || '') + '">轨迹 ' +
            esc(String(trace.trace_id || '').substring(0, 10)) + ' · ' +
            esc(String(autonomousEventTypeLabel(trace.last_event_type) || '事件').substring(0, 14)) + ' · ' +
            esc(String(trace.event_count || 0)) + '</span>'
          ).join('') + '</div>'
        : '<div class="drawer-sub" style="margin:0;">该分段最近没有可聚合的轨迹摘要。</div>') +
      '</div>';
    if (selectedTrace) {
      html += '<div class="drawer-section"><div class="drawer-section-label">选中轨迹</div>' +
        '<div class="drawer-sub" style="margin:0;">轨迹 ' + esc(String(selectedTrace.trace_id || '').substring(0, 24)) +
        ' · 最近 ' + esc(shortClock(selectedTrace.last_seen_at)) +
        ' · 事件 ' + esc(selectedTrace.event_count) +
        (selectedTrace.task_titles && selectedTrace.task_titles.length
          ? ' · 条目 ' + esc(selectedTrace.task_titles.slice(0, 2).join(' / '))
          : '') +
        '</div>' +
        '<div class="drawer-sub" style="margin-top:8px;">来源 ' + esc((selectedTrace.sources || []).map(autonomousEventSourceLabel).join(' / ') || '未知来源') +
        ' · ' + esc(String(selectedTrace.latest_summary || '').substring(0, 120) || '暂无摘要') +
        '</div></div>';
      if (selectedTraceDetail) {
        const sourceCounts = selectedTraceDetail.source_counts || {};
        const sourceSummary = Object.keys(sourceCounts).map(key => autonomousEventSourceLabel(key) + ':' + sourceCounts[key]).join(' / ');
        const taskFamilies = Array.isArray(selectedTraceDetail.task_families) ? selectedTraceDetail.task_families : [];
        const executionKinds = Array.isArray(selectedTraceDetail.execution_kinds) ? selectedTraceDetail.execution_kinds : [];
        const decisionIds = Array.isArray(selectedTraceDetail.decision_ids) ? selectedTraceDetail.decision_ids : [];
        const preview = Array.isArray(selectedTraceDetail.timeline_preview) ? selectedTraceDetail.timeline_preview : [];
        const allEvents = Array.isArray(selectedTraceDetail.timeline_events) ? selectedTraceDetail.timeline_events : [];
        const visibleEvents = (focusTraceExpanded ? allEvents : preview).filter(event => {
          if (!focusTraceSource) return true;
          return String(event.source || '').trim() === focusTraceSource;
        });
        const toggleLabel = focusTraceExpanded ? '收起事件流' : '展开全部事件';
        const sourceKeys = Object.keys(sourceCounts);
        html += '<div class="drawer-section"><div class="drawer-section-label">轨迹详情</div>' +
          '<div class="drawer-sub" style="margin:0;">记录 ' + esc(selectedTraceDetail.record_count) +
          ' · 首次 ' + esc(shortClock(selectedTraceDetail.first_seen_at)) +
          ' · 最近 ' + esc(shortClock(selectedTraceDetail.last_seen_at)) +
          (sourceSummary ? ' · 来源分布 ' + esc(sourceSummary) : '') +
          '</div>' +
          (taskFamilies.length
            ? '<div class="drawer-sub" style="margin-top:8px;">执行家族 ' + esc(taskFamilies.join(' / ')) + '</div>'
            : '') +
          (executionKinds.length
            ? '<div class="drawer-sub" style="margin-top:4px;">执行类型 ' + esc(executionKinds.join(' / ')) + '</div>'
            : '') +
          (decisionIds.length
            ? '<div class="drawer-sub" style="margin-top:4px;">决策记录 ' + esc(decisionIds.slice(0, 3).join(' / ')) + '</div>'
            : '') +
          (sourceKeys.length
            ? '<div class="trace-chip-row">' +
              '<span class="trace-chip ' + (!focusTraceSource ? 'active' : '') +
              '" data-drill="autonomous" data-chain-group="' + esc(focusGroup.key || '') +
              '" data-chain-trace="' + esc(selectedTrace.trace_id || '') +
              '" data-chain-trace-expanded="' + (focusTraceExpanded ? 'true' : 'false') +
              '" data-chain-trace-source="">全部来源</span>' +
              sourceKeys.map(source =>
                '<span class="trace-chip ' + (focusTraceSource === source ? 'active' : '') +
                '" data-drill="autonomous" data-chain-group="' + esc(focusGroup.key || '') +
                '" data-chain-trace="' + esc(selectedTrace.trace_id || '') +
                '" data-chain-trace-expanded="' + (focusTraceExpanded ? 'true' : 'false') +
                '" data-chain-trace-source="' + esc(source) + '">' +
                esc(autonomousEventSourceLabel(source)) + ' · ' + esc(sourceCounts[source]) + '</span>'
              ).join('') +
              '</div>'
            : '') +
          '<div class="drawer-sub" style="margin-top:8px;"><span class="drill-link" data-drill="autonomous" data-chain-group="' +
          esc(focusGroup.key || '') + '" data-chain-trace="' + esc(selectedTrace.trace_id || '') +
          '" data-chain-trace-expanded="' + (focusTraceExpanded ? 'false' : 'true') +
          '" data-chain-trace-source="' + esc(focusTraceSource) + '">🔬 ' + esc(toggleLabel) + '</span></div>' +
          (visibleEvents.length
            ? visibleEvents.map(event =>
                '<div class="segment-active" style="margin-top:6px;"><div class="la-title">' +
                esc(shortClock(event.recorded_at)) + ' · ' +
                esc(String(event.summary || autonomousEventTypeLabel(event.event_type) || '轨迹事件').substring(0, 84)) + '</div>' +
                '<div style="margin-top:3px;">来源: ' + esc(autonomousEventSourceLabel(event.source)) +
                (event.task_id ? ' · 链路项 ' + esc(String(event.task_id).substring(0, 16)) : '') +
                (event.decision_id ? ' · 决策 ' + esc(String(event.decision_id).substring(0, 16)) : '') +
                '</div></div>'
              ).join('')
            : '<div class="drawer-sub" style="margin-top:8px;">当前来源过滤下没有可显示的事件。</div>') +
          '</div>';
      }
    }
  }
  html += renderAutonomousLoop(loop, {showWritebacks: true});
  html += '<div class="drawer-sub" style="margin-top:10px;">用户链路感知: ' + esc(userState) +
    ' · 活跃会话 ' + esc(activeSessions) +
    ' · 软静默阈值 ' + esc(quietAfter) + 's' +
    ' · 临时开关 ' + (runtime.autonomous_chain_gate_active ? '已开启' : '已关闭') + '</div>';
  if (orderedGroups.length) {
    html += '<div class="segment-grid">' +
      orderedGroups.map(group => {
        const decor = observationSegmentDecor(group);
        return segmentColFromGroup(decor.cls, decor.icon, group);
      }).join('') +
      '</div>';
  }
  if (rereadCard && Object.keys(rereadCard).length) {
    html += '<div class="drawer-sub" style="margin-top:10px;">API-B 再读取: ' +
      esc(rereadCard.display_status || '等待中') + ' · ' +
      esc(String(rereadCard.summary || '').substring(0, 120)) + '</div>';
  }
  html += '<div class="drawer-sub" style="margin-top:10px;">当前抽屉聚焦 ' +
    esc((focusGroup && focusGroup.label) || '自主链路闭环') +
    (selectedTrace ? (' · 轨迹 ' + esc(String(selectedTrace.trace_id || '').substring(0, 18))) : '') +
    ' · 用户链路只作为软感知输入，不参与这条观测链的展示。</div>';
  els.drawerBody.innerHTML = html;
}

/* ── 🔎 内生驱动决策溯源 ── */
function renderProvenanceDrawer(state) {
  const cog = state.cognition || {};
  const p = cog.perception || {};
  const wm = cog.world_model || {};
  const needs = Array.isArray(cog.needs) ? cog.needs : [];
  const intents = Array.isArray(cog.intents) ? cog.intents : [];
  const policy = cog.adaptive_policy || {};
  const candidateGroup = chainGroupByKey(state.autonomous_observation || {}, 'api_b_candidates') || {};
  const cands = Array.isArray(candidateGroup.items) ? candidateGroup.items : [];

  if (!Object.keys(p).length && !needs.length) {
    els.drawerBody.innerHTML = '<div class="drawer-sub">认知状态尚未初始化。激活 Governor 模式后内生驱动会填充感知→意图链。</div>';
    return;
  }

  let chain = '<div class="prov-chain">';
  chain += '<div class="prov-node"><div class="prov-node-label">👁 感知 PERCEPTION</div><div class="prov-node-body">' +
    '系统姿态 ' + esc(p.system_posture || '—') + ' · 用户姿态 ' + esc(p.user_mode || '—') + ' · 治理在途 ' + (p.governance_backlog_count || 0) +
    ' · 近期错误 ' + (p.recent_errors || 0) + ' · 修正信号 ' + (p.correction_signals || 0) + '</div></div>';
  chain += '<div class="prov-node"><div class="prov-node-label">🌍 世界模型 WORLD MODEL</div><div class="prov-node-body">' +
    '治理健康 ' + esc(wm.governance_load_state || '—') + ' · 记忆压力 ' + pct(wm.memory_pressure) +
    ' · 真实性压力 ' + pct(wm.truthfulness_pressure) + ' · 学习动量 ' + pct(wm.learning_momentum) + '</div></div>';
  // needs
  let needBody = needs.length
    ? needs.map(n => '· ' + esc(n.need_type || 'unknown') + ' (强度 ' + pct(n.severity) + (n.rationale ? ', ' + esc(String(n.rationale).substring(0, 60)) : '') + ')').join('<br>')
    : '无活跃需求(有判断地不行动)';
  chain += '<div class="prov-node"><div class="prov-node-label">🎯 需求 NEEDS</div><div class="prov-node-body">' + needBody + '</div></div>';
  // intents
  let intentBody = intents.length
    ? intents.map(i => '· ' + esc(i.intent_type || 'intent') + ' → ' + esc(i.output_channel || '—') + ' (' + esc(i.target_horizon || '—') + ')').join('<br>')
    : '无活跃意图';
  chain += '<div class="prov-node"><div class="prov-node-label">🧭 意图 INTENTS</div><div class="prov-node-body">' + intentBody + '</div></div>';
  // policy
  chain += '<div class="prov-node"><div class="prov-node-label">🎚 策略 POLICY</div><div class="prov-node-body">' +
    '偏好焦点 ' + esc(policy.preferred_focus || '—') + ' · 候选预算 ' + (policy.candidate_budget != null ? policy.candidate_budget : '—') +
    ' · 观察偏置 ' + pct(policy.observation_bias) + '</div></div>';
  chain += '</div>';

  // candidate provenance
  let candHtml = '<div class="drawer-section-label">候选产出 (' + cands.length + ')</div>';
  if (!cands.length) {
    candHtml += '<div class="drawer-sub" style="margin:0;">当前无待治理候选。弱证据下空候选是正确行为。</div>';
  } else {
    candHtml += cands.slice(0, 6).map(c => {
      const meta = c && c.metadata ? c.metadata : {};
      const rawTags = Array.isArray(meta.core_values) ? meta.core_values : (Array.isArray(c.value_tags) ? c.value_tags : []);
      const rationale = c.rationale || meta.rationale || '';
      const tags = rawTags.map(esc).join(' · ');
      return '<div class="segment-active" style="margin-top:6px;"><div class="la-title">' + esc(String(c.title || '未命名').substring(0, 52)) + '</div>' +
        (rationale ? '<div style="margin-top:3px;">理由: ' + esc(String(rationale).substring(0, 120)) + '</div>' : '') +
        (tags ? '<div style="margin-top:3px;color:var(--text-muted);">价值标签: ' + tags + '</div>' : '') + '</div>';
    }).join('');
  }

  els.drawerBody.innerHTML =
    '<div class="drawer-sub">回答"当前为什么这样判断 / 为什么产出(或不产出)这些候选与链路项"。链路: 感知 → 世界模型 → 需求 → 意图 → 策略 → 候选。</div>' +
    '<div class="drawer-section">' + chain + '</div>' +
    '<div class="drawer-section">' + candHtml + '</div>';
}

/* ── 💗 身体 / 记忆健康度 ── */
function renderHealthDrawer(state) {
  const bs = state.body_status || {};
  const ts = state.tier1_stats || {};
  const last = bs.last_switch_result || {};

  function rows(title, arr) {
    return '<div class="drawer-section"><div class="drawer-section-label">' + title + '</div>' +
      arr.map(r => '<div class="health-row"><span>' + r[0] + '</span><span class="hr-val">' + r[1] + '</span></div>').join('') +
      '</div>';
  }

  const bodyRows = [
    ['活跃槽 (当前替身)', esc(bs.active_slot || '—')],
    ['Shell 槽', esc(bs.shell_slot || '—')],
    ['退役槽', esc(bs.retired_slot || '—')],
    ['累计切换次数', (last.switch_count != null ? last.switch_count : 0)],
    ['上次切换结果', esc(last.status || last.result || '—')],
  ];
  const memRows = [
    ['Tier1 短期记忆条目', (ts.total_entries != null ? ts.total_entries : '—')],
    ['压缩块', (ts.compressed_blocks != null ? ts.compressed_blocks : '—')],
    ['记忆 LLM 健康', ts.llm_healthy ? '✅ 正常' : '⚠️ 异常 / 未知'],
    ['记忆活跃', ts.memory_active ? '✅ 是' : '💤 否'],
  ];

  els.drawerBody.innerHTML =
    '<div class="drawer-sub">替身 (身体) 改进由 API-A agent 执行;监督者只追踪槽位状态。记忆维护属监督者自维护范畴。</div>' +
    rows('🔄 替身 / 身体', bodyRows) +
    rows('💾 记忆 (API-B 侧)', memRows);
}

/* ═══════════════════════════════════════════
   面板渲染函数
   ═══════════════════════════════════════════ */

/* ── 🚦 自主链路总览面板 ── */
function renderChainPanel(state) {
  const body = els.panelChainBody;
  if (!body) return;
  body.replaceChildren();

  const drill = document.createElement('div');
  drill.style.cssText = 'display:flex;justify-content:flex-end;margin-bottom:6px;';
  drill.innerHTML = drillButton('autonomous', '链路总览');
  body.append(drill);

  body.append(buildChainHero(state));

  const loop = (((state || {}).autonomous_observation || {}).loop || {});
  const loopSec = document.createElement('div');
  loopSec.style.cssText = 'margin-top:10px;';
  loopSec.innerHTML = renderAutonomousLoop(loop, {showWritebacks: true});
  if (loopSec.innerHTML) body.append(loopSec.firstElementChild);

  appendLoopStageGrid(
    body,
    ((state || {}).autonomous_observation || {}),
    null,
    '闭环当前落点'
  );
  appendChainSectionGrid(
    body,
    state,
    null,
    {limit: 3}
  );
}

function observationRoleStageLabel(task) {
  const role = String(task.observation_role || '').trim();
  const labels = {
    api_b_judgement: 'API-B 判断阶段',
    api_a_execution: 'API-A 执行回报阶段',
    mem_writeback: 'Mem 写回阶段',
    api_b_reread: 'API-B 再读取阶段',
    candidate: 'API-B 候选判断',
  };
  return labels[role] || '自主链路观察';
}

function buildObservationCard(task, options) {
  const opts = options || {};
  const card = document.createElement('div');
  card.className = 'game-card ' + rarityClass(task);

  const head = document.createElement('div');
  head.className = 'game-card-head';
  const title = document.createElement('div');
  title.className = 'game-card-title';
  title.textContent = (task.title || '未命名').substring(0, 64);
  title.title = task.title || '';
  const badge = document.createElement('span');
  const rawStatus = String(task.status || '').trim().toLowerCase();
  const badgeTone = (
    ['planned', 'approved', 'running', 'completed', 'failed', 'deferred', 'paused'].includes(rawStatus)
      ? rawStatus
      : observationStateBadgeClass(rawStatus)
  );
  const st = task.display_status || task.status || 'queued';
  badge.className = 'game-card-badge ' + badgeTone;
  badge.textContent = statusLabel(st);
  head.append(title, badge);
  card.append(head);

  const sub = document.createElement('div');
  sub.className = 'game-card-sub';
  sub.textContent = String(opts.subtitle || typeLabel(task)).substring(0, 160);
  card.append(sub);

  const meta = document.createElement('div');
  meta.className = 'game-card-meta';

  const tags = document.createElement('div');
  tags.className = 'game-card-tags';
  const lane = taskLane(task);
  const laneTag = document.createElement('span');
  if (lane === 'agent') {
    laneTag.className = 'game-card-tag creativity';
    laneTag.textContent = 'API-A 回报';
  } else if (lane === 'mem') {
    laneTag.className = 'game-card-tag truthfulness';
    laneTag.textContent = 'Mem 写回';
  } else {
    laneTag.className = 'game-card-tag memory';
    laneTag.textContent = 'API-B 治理';
  }
  tags.append(laneTag);
  (Array.isArray(opts.extraTags) ? opts.extraTags : []).forEach(tag => {
    if (!tag || !tag.text) return;
    const extraTag = document.createElement('span');
    extraTag.className = 'game-card-tag ' + String(tag.cls || '').trim();
    extraTag.textContent = String(tag.text);
    tags.append(extraTag);
  });
  if (task.governance_task_type && opts.includeType !== false) {
    const typeTag = document.createElement('span');
    typeTag.className = 'game-card-tag ' + tagClass(task.governance_task_type);
    typeTag.textContent = typeLabel(task);
    tags.append(typeTag);
  }
  const taskMeta = task && task.metadata ? task.metadata : {};
  const candidateTags = Array.isArray(taskMeta.core_values) ? taskMeta.core_values : (Array.isArray(task.value_tags) ? task.value_tags : []);
  if (opts.showCandidateTags && candidateTags.length) {
    candidateTags.forEach(vt => {
      const vtTag = document.createElement('span');
      vtTag.className = 'game-card-tag ' + tagClass(vt);
      vtTag.textContent = vt;
      tags.append(vtTag);
    });
  }
  meta.append(tags);

  if (opts.showUtility && task.utility != null) {
    const scoreWrap = document.createElement('div');
    scoreWrap.style.cssText = 'display:flex;align-items:center;gap:4px;flex-shrink:0;';
    const pct = Math.round((task.utility || 0) * 100);
    const bar = document.createElement('div');
    bar.className = 'game-score-bar';
    bar.style.width = '50px';
    const fill = document.createElement('div');
    fill.className = 'game-score-fill ' + scoreClass(task.utility);
    fill.style.width = pct + '%';
    bar.append(fill);
    const label = document.createElement('span');
    label.style.cssText = 'font-size:9.5px;font-weight:700;color:var(--text-secondary);font-variant-numeric:tabular-nums;';
    label.textContent = pct + '%';
    scoreWrap.append(bar, label);
    meta.append(scoreWrap);
  }

  card.append(meta);
  return card;
}

function buildStageCard(task) {
  const subtitle = [
    observationRoleStageLabel(task),
    task.summary ? String(task.summary).substring(0, 100) : '',
  ].filter(Boolean).join(' · ') || '自主链路阶段观察';
  return buildObservationCard(task, {
    subtitle,
    extraTags: [{text: '闭环阶段', cls: 'truthfulness'}],
    includeType: false,
    showUtility: false,
    showCandidateTags: false,
  });
}

function buildSectionCard(task) {
  const hints = [];
  const ih = taskIdentityHint(task);
  if (ih) hints.push(ih);
  const gh = governanceHint(task);
  if (gh) hints.push(gh);
  if (task.summary) hints.push(String(task.summary).substring(0, 100));
  const subtitle = hints.join(' · ').substring(0, 160) || typeLabel(task);
  return buildObservationCard(task, {
    subtitle,
    extraTags: [{text: '分段观察', cls: 'memory'}],
    includeType: true,
    showUtility: false,
    showCandidateTags: false,
  });
}

function buildCandidateCard(task) {
  const subtitle = [
    '内生驱动候选判断',
    task.summary ? String(task.summary).substring(0, 100) : '',
  ].filter(Boolean).join(' · ') || '等待 API-B 判断';
  return buildObservationCard(task, {
    subtitle,
    extraTags: [{text: '候选判断', cls: 'creativity'}],
    includeType: false,
    showUtility: true,
    showCandidateTags: true,
  });
}

/* ── 🧠 LM 输入面板 ── */
function renderLMInputPanel(state) {
  const body = els.panelLMInputBody;
  if (!body) return;
  body.replaceChildren();

  const lm = state.lm_input || {};
  const mem = state.mem_usage || {};

  // Token 统计
  const tokSec = document.createElement('div');
  tokSec.className = 'lm-section';
  tokSec.innerHTML = '<div class="lm-section-label">📊 Token 用量</div>';
  const stats = [
    {icon:'📨', label:'总消耗', value:(mem.total_tokens||0).toLocaleString(), hl:false},
    {icon:'📥', label:'Prompt', value:(mem.prompt_tokens||0).toLocaleString(), hl:false},
    {icon:'📤', label:'Completion', value:(mem.completion_tokens||0).toLocaleString(), hl:false},
    {icon:'🔢', label:'请求数', value:mem.request_count||0, hl:false},
    {icon:'📐', label:'上下文', value:(mem.context_length||0).toLocaleString() + ' (' + (mem.context_percent||0) + '%)', hl:(mem.context_percent||0) > 80},
  ];
  stats.forEach(s => {
    const row = document.createElement('div');
    row.className = 'lm-stat-row';
    const hlClass = s.hl ? ' highlight' : '';
    row.innerHTML = '<span class="lm-stat-icon">' + s.icon + '</span><span class="lm-stat-label">' + s.label + '</span><span class="lm-stat-value' + hlClass + '">' + s.value + '</span>';
    tokSec.append(row);
  });
  body.append(tokSec);

  // LM 调用信息
  if (lm.last_call_at || lm.prompt_estimate || lm.status || lm.proposal_count != null) {
    const callSec = document.createElement('div');
    callSec.className = 'lm-section';
    callSec.innerHTML = '<div class="lm-section-label">🧠 最近 LM 调用</div>';
    const info = [
      {icon:'🕐', label:'最近调用', value: lm.last_call_at ? new Date(lm.last_call_at).toLocaleTimeString() : '—'},
      {icon:'📝', label:'Prompt 预估', value: lm.prompt_estimate ? (lm.prompt_estimate + ' 字符') : '—'},
      {icon:'🔗', label:'证据节点', value: (lm.evidence_node_count != null) ? lm.evidence_node_count : '—'},
      {icon:'🎯', label:'候选提案', value: (lm.proposal_count != null) ? lm.proposal_count : '—'},
      {icon:'🧭', label:'状态', value: lm.status || '—'},
      {icon:'🧩', label:'模型角色', value: lm.model_role || '—'},
      {icon:'⚙️', label:'生成状态', value: lm.generation_enabled ? '✅ 已启用' : '⏸ 已禁用'},
    ];
    info.forEach(s => {
      const row = document.createElement('div');
      row.className = 'lm-stat-row';
      row.innerHTML = '<span class="lm-stat-icon">' + s.icon + '</span><span class="lm-stat-label">' + s.label + '</span><span class="lm-stat-value">' + s.value + '</span>';
      callSec.append(row);
    });
    body.append(callSec);
  }

  // 证据节点列表
  const evNodes = lm.recent_evidence_nodes || [];
  if (evNodes.length) {
    const evSec = document.createElement('div');
    evSec.className = 'lm-section';
    evSec.innerHTML = '<div class="lm-section-label">📎 最近证据节点 (' + evNodes.length + ')</div>';
    const list = document.createElement('div');
    list.className = 'lm-evidence-list';
    evNodes.slice(0, 20).forEach(ev => {
      const item = document.createElement('div');
      item.className = 'lm-evidence-item';
      const nodeText = typeof ev === 'string' ? ev : (ev.node || ev.title || ev.summary || JSON.stringify(ev).substring(0, 120));
      const nodeLabel = typeof ev === 'object' && ev.node ? String(ev.node) : '证据';
      item.innerHTML = '<span class="ei-node">' + nodeLabel + '</span> ' + String(nodeText).substring(0, 140);
      list.append(item);
    });
    evSec.append(list);
    body.append(evSec);
  }

  // Prompt 预览
  if (lm.prompt_preview) {
    const prevSec = document.createElement('div');
    prevSec.className = 'lm-section';
    prevSec.innerHTML = '<div class="lm-section-label">📄 Prompt 预览</div>';
    const pre = document.createElement('div');
    pre.className = 'lm-prompt-preview';
    pre.textContent = String(lm.prompt_preview).substring(0, 2000);
    prevSec.append(pre);
    body.append(prevSec);
  }

  // 空状态
  if (!mem.total_tokens && !lm.last_call_at && !lm.status && lm.proposal_count == null && !evNodes.length) {
    body.replaceChildren();
    const empty = document.createElement('div');
    empty.className = 'panel-empty';
    empty.innerHTML = '<div class="pe-icon">🧠</div><div class="pe-text">LM 尚未产生调用记录</div><div style="font-size:10px;color:var(--text-muted);">激活 Governor 模式并启用 LM 候选生成后会出现数据</div>';
    body.append(empty);
  }
}

/* ── 📊 认知面板 ── */
function renderCognitionPanel(state) {
  const body = els.panelCognitionBody;
  if (!body) return;
  body.replaceChildren();

  const drill = document.createElement('div');
  drill.style.cssText = 'display:flex;justify-content:flex-end;margin-bottom:6px;';
  drill.innerHTML = drillButton('provenance', '决策溯源');
  body.append(drill);

  const cog = state.cognition || {};
  const perception = cog.perception || {};
  const worldModel = cog.world_model || {};
  const needs = Array.isArray(cog.needs) ? cog.needs : [];
  const intents = Array.isArray(cog.intents) ? cog.intents : [];
  const signals = Array.isArray(cog.signals) ? cog.signals : [];
  const policy = cog.adaptive_policy || {};

  const flow = document.createElement('div');
  flow.className = 'cog-flow';

  // Perception
  const percStep = document.createElement('div');
  percStep.className = 'cog-step';
  percStep.innerHTML = '<div class="cog-step-label">👁 感知</div><div class="cog-step-content"><div class="cog-step-title">' +
    '姿势: ' + (perception.system_posture || '—') + ' · 用户姿态: ' + (perception.user_mode || '—') + ' · 错误: ' + (perception.recent_errors || 0) +
    '</div><div class="cog-step-detail">' +
    '治理在途: ' + (perception.governance_backlog_count || 0) +
    ' · 用户会话: ' + (perception.active_sessions != null ? perception.active_sessions : '—') +
    ' · 学习质量: ' + (perception.learning_quality != null ? Math.round(perception.learning_quality) + '%' : '—') +
    '</div><div class="cog-step-detail">' +
    '修正信号: ' + (perception.correction_signals || 0) +
    ' · 用户静默: ' + ((perception.idle_seconds || {}).user || '—') + 's' +
    ' · 记忆静默: ' + ((perception.idle_seconds || {}).memory || '—') + 's' +
    '</div></div>';
  flow.append(percStep);

  // World Model
  const wmStep = document.createElement('div');
  wmStep.className = 'cog-step';
  wmStep.innerHTML = '<div class="cog-step-label">🌍 世界模型</div><div class="cog-step-content"><div class="cog-step-title">' +
    '治理健康: ' + (worldModel.governance_load_state || '—') + ' · 记忆压力: ' + (worldModel.memory_pressure != null ? Math.round(worldModel.memory_pressure * 100) + '%' : '—') +
    '</div><div class="cog-step-detail">' +
    '真实压力: ' + (worldModel.truthfulness_pressure != null ? Math.round(worldModel.truthfulness_pressure * 100) + '%' : '—') +
    ' · 学习动量: ' + (worldModel.learning_momentum != null ? Math.round(worldModel.learning_momentum * 100) + '%' : '—') +
    ' · 自信: ' + (worldModel.self_confidence != null ? Math.round(worldModel.self_confidence * 100) + '%' : '—') +
    '</div></div>';
  flow.append(wmStep);

  // Needs
  const needStep = document.createElement('div');
  needStep.className = 'cog-step';
  let needHtml = '<div class="cog-step-label">🎯 需求</div><div class="cog-step-content">';
  if (!needs.length) {
    needHtml += '<div class="cog-step-detail">无活跃需求</div>';
  } else {
    needs.forEach(n => {
      const sev = n.severity > 0.7 ? 'severity-high' : n.severity > 0.4 ? 'severity-mid' : 'severity-low';
      needHtml += '<span class="cog-need-tag ' + sev + '">' + (n.need_type || 'unknown') + ' ' + Math.round((n.severity||0)*100) + '%</span>';
    });
  }
  needHtml += '</div>';
  needStep.innerHTML = needHtml;
  flow.append(needStep);

  // Intents
  const intentStep = document.createElement('div');
  intentStep.className = 'cog-step';
  let intentHtml = '<div class="cog-step-label">🧭 意图</div><div class="cog-step-content">';
  if (!intents.length) {
    intentHtml += '<div class="cog-step-detail">无活跃意图</div>';
  } else {
    intents.forEach(i => {
      intentHtml += '<div class="cog-step-title" style="font-size:10.5px;">📌 ' + (i.intent_type || 'intent') + ' → ' + (i.output_channel || '—') + ' (' + (i.target_horizon || '—') + ')</div>';
    });
  }
  intentHtml += '</div>';
  intentStep.innerHTML = intentHtml;
  flow.append(intentStep);

  // Signals
  if (signals.length) {
    const sigStep = document.createElement('div');
    sigStep.className = 'cog-step';
    sigStep.innerHTML = '<div class="cog-step-label">📡 信号</div><div class="cog-step-content">' +
      signals.slice(0, 3).map(s => '<div class="cog-step-detail">' + (s.signal_type || 'signal') + ': ' + String(s.message || '').substring(0, 100) + '</div>').join('') +
      '</div>';
    flow.append(sigStep);
  }

  // Adaptive Policy
  if (Object.keys(policy).length) {
    const polStep = document.createElement('div');
    polStep.className = 'cog-step';
    polStep.innerHTML = '<div class="cog-step-label">🎚 策略</div><div class="cog-step-content"><div class="cog-step-detail">' +
      '学习偏置: ' + (policy.learning_expansion_bias != null ? Math.round(policy.learning_expansion_bias * 100) + '%' : '—') +
      ' · 真实偏置: ' + (policy.truthfulness_bias != null ? Math.round(policy.truthfulness_bias * 100) + '%' : '—') +
      ' · 预算: ' + (policy.candidate_budget || '—') +
      ' · 焦点: ' + (policy.preferred_focus || '—') +
      '</div></div>';
    flow.append(polStep);
  }

  body.append(flow);

  if (!Object.keys(perception).length && !needs.length) {
    body.replaceChildren();
    const empty = document.createElement('div');
    empty.className = 'panel-empty';
    empty.innerHTML = '<div class="pe-icon">📊</div><div class="pe-text">认知状态尚未初始化</div><div style="font-size:10px;color:var(--text-muted);">激活 Governor 模式后内生驱动会填充认知层</div>';
    body.append(empty);
  }
}

/* ── ⚙️ API-B 观测面板 ── */
function renderObservationPanel(state) {
  const body = els.panelObservationBody;
  if (!body) return;
  body.replaceChildren();

  const obs = state.autonomous_observation || {};
  const runtime = observationRuntime(obs);
  const guards = runtime.activity_guards || {};
  const userSignal = guards.user_chain_signal || {};
  const loop = obs.loop || {};

  const drill = document.createElement('div');
  drill.style.cssText = 'display:flex;justify-content:flex-end;margin-bottom:6px;';
  drill.innerHTML = drillButton('autonomous', '链路总览');
  body.append(drill);

  const loopSec = document.createElement('div');
  loopSec.style.cssText = 'margin-bottom:10px;';
  loopSec.innerHTML = renderAutonomousLoop(loop, {showWritebacks: true});
  if (loopSec.innerHTML) body.append(loopSec);

  // 统计摘要
  const summary = document.createElement('div');
  summary.style.cssText = 'display:flex;gap:12px;flex-wrap:wrap;padding:4px 0;margin-bottom:8px;';
  const metricCards = derivedMetricCards(obs);
  const metricColors = {
    'api-b': 'var(--gold)',
    'api-a': 'var(--mint)',
    'candidate': 'var(--plum)',
    'mem': 'var(--coral)',
  };
  metricCards.forEach(s => {
    const chip = document.createElement('div');
    chip.style.cssText = 'text-align:center;padding:6px 10px;border-radius:8px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.05);min-width:50px;';
    chip.innerHTML = '<div style="font-size:18px;font-weight:700;color:' + (metricColors[s.cls] || 'var(--text)') + ';">' + s.value + '</div><div style="font-size:9px;color:var(--text-muted);">' + s.label + '</div>';
    summary.append(chip);
  });
  body.append(summary);

  const observationNotes = derivedObservationNotes(obs);
  observationNotes.forEach(note => {
    const tone = String(note.tone || '').trim();
    const badgeTone = tone === 'warn' ? 'deferred' : (tone === 'good' ? 'approved' : 'running');
    const row = document.createElement('div');
    row.className = 'game-card rarity-common';
    row.innerHTML =
      '<div class="game-card-head"><div class="game-card-title">' + esc(note.title || '观测说明') + '</div>' +
      '<span class="game-card-badge ' + badgeTone + '">' + esc(note.title || '说明') + '</span></div>' +
      '<div class="game-card-sub">' + esc(note.text || '').substring(0, 180) + '</div>';
    body.append(row);
  });
  if (!observationNotes.length) {
    const signalRow = document.createElement('div');
    signalRow.className = 'game-card rarity-common';
    signalRow.innerHTML =
      '<div class="game-card-head"><div class="game-card-title">用户链路软感知输入</div>' +
      '<span class="game-card-badge ' + (userSignal.is_quiet ? 'approved' : 'deferred') + '">' +
      (userSignal.is_quiet ? '安静软信号' : '活跃软信号') + '</span></div>' +
      '<div class="game-card-sub">活跃会话 ' + esc(userSignal.active_sessions != null ? userSignal.active_sessions : 0) +
      ' · 软静默阈值 ' + esc(userSignal.quiet_after_seconds != null ? userSignal.quiet_after_seconds : '—') + 's' +
      ' · 仅影响 API-B 判断让路，不展示用户聊天内容</div>';
    body.append(signalRow);
  }

  appendLoopStageGrid(
    body,
    obs,
    ['api_b_judgement', 'api_b_reread', 'mem_writeback'],
    'API-B 判断 / 写回 / 再读取'
  );

  appendChainSectionGrid(
    body,
    state,
    ['api_b_candidates', 'api_b_backlog', 'mem_recent'],
    {limit: 4}
  );

  const boundaryNote = document.createElement('div');
  boundaryNote.className = 'watch-inline-note';
  boundaryNote.textContent = String(
    loop.boundary
    || 'Web 小屋只观察 API-B 主导的自主链路，不展示用户聊天内容。'
  );
  body.append(boundaryNote);
}

/* ── 📈 统计面板 ── */
function renderStatsPanel(state) {
  const body = els.panelStatsBody;
  if (!body) return;
  body.replaceChildren();

  const bs = state.body_status || {};
  const ts = state.tier1_stats || {};
  const mem = state.mem_usage || {};
  const obs = state.autonomous_observation || {};
  const runtime = obs.runtime || {};
  const userSignal = runtime.user_chain_signal || {};

  const drill = document.createElement('div');
  drill.style.cssText = 'display:flex;justify-content:flex-end;margin-bottom:6px;';
  drill.innerHTML = drillButton('health', '健康度详情');
  body.append(drill);

  // 替身状态
  const bodySec = document.createElement('div');
  bodySec.className = 'lm-section';
  bodySec.innerHTML = '<div class="lm-section-label">🔄 替身状态</div>';
  [
    {icon:'🟢', label:'活跃槽', value: bs.active_slot || '—'},
    {icon:'🐚', label:'Shell 槽', value: bs.shell_slot || '—'},
    {icon:'📦', label:'退役槽', value: bs.retired_slot || '—'},
    {icon:'🔄', label:'切换次数', value: (bs.last_switch_result || {}).switch_count || 0},
  ].forEach(s => {
    const row = document.createElement('div');
    row.className = 'lm-stat-row';
    row.innerHTML = '<span class="lm-stat-icon">' + s.icon + '</span><span class="lm-stat-label">' + s.label + '</span><span class="lm-stat-value">' + s.value + '</span>';
    bodySec.append(row);
  });
  body.append(bodySec);

  // 记忆统计
  const memSec = document.createElement('div');
  memSec.className = 'lm-section';
  memSec.innerHTML = '<div class="lm-section-label">💾 记忆统计</div>';
  [
    {icon:'📊', label:'Tier1 条目', value: ts.total_entries || '—'},
    {icon:'📦', label:'压缩块', value: ts.compressed_blocks || '—'},
    {icon:'✅', label:'LLM 健康', value: ts.llm_healthy ? '✅ 正常' : '⚠️ 异常'},
    {icon:'🧠', label:'记忆活跃', value: ts.memory_active ? '✅ 是' : '💤 否'},
    {icon:'📐', label:'上下文用量', value: (mem.context_percent || 0) + '% (' + (mem.total_tokens || 0).toLocaleString() + ' tokens)'},
  ].forEach(s => {
    const row = document.createElement('div');
    row.className = 'lm-stat-row';
    row.innerHTML = '<span class="lm-stat-icon">' + s.icon + '</span><span class="lm-stat-label">' + s.label + '</span><span class="lm-stat-value">' + s.value + '</span>';
    memSec.append(row);
  });
  body.append(memSec);

  // 治理状态
  const gateSec = document.createElement('div');
  gateSec.className = 'lm-section';
  gateSec.innerHTML = '<div class="lm-section-label">⚙️ 治理状态</div>';
  [
    {icon:'🔮', label:'自主链路', value: runtime.autonomous_chain_gate_active ? '✅ 已激活' : '⏸ 未激活'},
    {icon:'🌐', label:'执行模式', value: '全天候'},
    {icon:'👁', label:'用户链路感知', value: userSignal.active_sessions || 0},
    {icon:'📡', label:'驱动可用', value: runtime.drive_available ? '✅' : '⚠️ 不可用'},
    {icon:'📋', label:'闭环分段', value: loopStages(obs).length || 0},
  ].forEach(s => {
    const row = document.createElement('div');
    row.className = 'lm-stat-row';
    row.innerHTML = '<span class="lm-stat-icon">' + s.icon + '</span><span class="lm-stat-label">' + s.label + '</span><span class="lm-stat-value">' + s.value + '</span>';
    gateSec.append(row);
  });
  body.append(gateSec);
}

/* ── Dock 角色迷你状态更新 ── */
function updateDockCharStrip(state) {
  const bs = state.body_status || {};
  const last = bs.last_switch_result || {};
  const switchCount = (typeof last.switch_count === 'number') ? last.switch_count : 0;
  const lv = Math.max(1, switchCount + 1);
  if (els.dcsName) {
    const slot2 = (state.body_status || {}).active_slot || '';
    const charName = String(slot2).toUpperCase().includes('A') ? '星子' : '西子';
    els.dcsName.textContent = charName + ' Lv.' + lv;
  }
  const hp = hpPercent(state);
  if (els.dcsHpFill) {
    els.dcsHpFill.style.width = hp + '%';
    els.dcsHpFill.className = 'dcs-hp-fill ' + (hp < 30 ? 'danger' : hp < 60 ? 'warn' : 'good');
  }
  if (els.dcsStatus) {
    els.dcsStatus.textContent = (state.summary || '就绪').substring(0, 30);
  }
}

/* ── 场景迷你标题 ── */
function updateSceneMiniTitle(state) {
  const scene = state.scene || 'idle';
  if (els.sceneMiniIcon) els.sceneMiniIcon.textContent = SCENE_ICONS[scene] || '🛋';
  if (els.sceneMiniText) els.sceneMiniText.textContent = state.title || '星子与西子的小屋';
}

/* ── 倒计时(静默) ── */
let countdownTimer = null, nextReviewAt = null;
function renderSchedule(sch) {
  if (countdownTimer) clearInterval(countdownTimer);
  const nxt = sch.next_review_at || sch.next_drive_at;
  if (!nxt) { nextReviewAt = null; return; }
  nextReviewAt = nxt;
  countdownTimer = setInterval(() => {}, 30000);
}

/* ── 应用状态(主入口) ── */
let userPickedAction = null;
let lastState = null;

function applyState(state) {
  lastState = state;
  const scene = state.scene || 'idle';
  const prevScene = els.body.dataset.scene;
  els.body.dataset.scene = scene;
  els.glyph.textContent = GLYPHS[scene] || '·';
  if (els.glyphXingzi) els.glyphXingzi.textContent = GLYPHS[scene] || '·';
  els.body.dataset.hasErrors = ((state.error_count || 0) > 0) ? 'true' : 'false';
  // Whole-day execution (baseline §6): room theme stays in active/day state.
  els.body.dataset.execWindow = 'true';

  // 槽位决定角色: A→星子(男), B→西子(女)
  const slot = (state.body_status || {}).active_slot || '';
  const newChar = String(slot).toUpperCase().includes('A') ? 'xingzi' : 'xizi';
  if (els.activeChar !== newChar) {
    els.activeChar = newChar;
    els.body.dataset.character = newChar;
  }

  // 自动动作
  if (!userPickedAction || userPickedAction.scene !== scene) {
    const action = SCENE_TO_ACTION[scene] || 'rest';
    setAction(action, true);
    userPickedAction = { scene, action };
  }
  updateDockActionButtons();

  updateSceneMiniTitle(state);
  updateDockCharStrip(state);

  // 渲染已打开的面板
  if (panelOpen === 'chain') renderChainPanel(state);
  if (panelOpen === 'lminput') renderLMInputPanel(state);
  if (panelOpen === 'cognition') renderCognitionPanel(state);
  if (panelOpen === 'observation') renderObservationPanel(state);
  if (panelOpen === 'stats') renderStatsPanel(state);

  // 抽屉打开时随状态刷新
  if (drawerOpen) renderDrawer();

  renderSchedule(state.schedule || {});

  if (scene !== prevScene) spawnParticles(scene, 12);
}

function setAction(action, silent) {
  els.body.dataset.action = action;
  if (!silent) spawnParticles(action, 8);
}

function updateDockActionButtons() {
  $$('.dock-btn[data-action-btn]').forEach(b => {
    b.classList.toggle('active', b.dataset.actionBtn === els.body.dataset.action);
  });
}

/* ── 粒子 ── */
let particleTimer = null;
function spawnParticles(scene, count) {
  const colors = {
    idle: 'rgba(255,248,220,.6)', drive: '#e2b04a', learning: '#6fc6a0',
    planning: '#6a9ee8', maintenance: '#e2b04a',
    organize: '#e2b04a', rest: '#6fc6a0', work: '#6a7eb8', write: '#a78ad4',
    execution: '#e8826e', body_switch: '#e8826e', memory: '#a78ad4',
  };
  const color = colors[scene] || 'rgba(255,248,220,.5)';
  for (let i = 0; i < (count || 6); i++) {
    const p = document.createElement('span');
    p.className = 'particle ' + (scene === 'execution' || scene === 'work' ? 'spark' : 'dust');
    p.style.left = (20 + Math.random() * 60) + '%';
    p.style.bottom = (10 + Math.random() * 30) + '%';
    p.style.animationDuration = (4 + Math.random() * 6) + 's';
    p.style.animationDelay = Math.random() * 2 + 's';
    if (scene !== 'idle' && scene !== 'rest') p.style.background = color;
    els.particles.append(p);
    setTimeout(() => p.remove(), 7000);
  }
}
function ambientParticles() {
  const s = els.body.dataset.scene, a = els.body.dataset.action;
  if (s === 'execution' || s === 'body_switch' || a === 'work' || a === 'organize') spawnParticles(a || s, 3);
}
particleTimer = setInterval(ambientParticles, 5000);

/* ── 数据拉取 ── */
async function refresh() {
  try {
    const r = await fetch('/ui/state', {cache: 'no-store'});
    applyState(await r.json());
  } catch (e) {
    els.body.dataset.scene = 'idle';
    if (els.sceneMiniText) els.sceneMiniText.textContent = '义子的小屋 · 等待中';
    els.glyph.textContent = '·';
    if (els.glyphXingzi) els.glyphXingzi.textContent = '·';
  }
}

let fallbackTimer = null;
function startFallback() {
  if (fallbackTimer) return;
  refresh();
  fallbackTimer = setInterval(refresh, 4000);
}

if ('EventSource' in window) {
  const es = new EventSource('/ui/events');
  es.addEventListener('state', ev => {
    if (fallbackTimer) { clearInterval(fallbackTimer); fallbackTimer = null; }
    applyState(JSON.parse(ev.data));
  });
  es.onerror = () => startFallback();
} else {
  startFallback();
}

ambientParticles();
updateDockActionButtons();

/* ── 钟表时/分针同步 ── */
function syncClock() {
  const h = els.wcHour;
  if (!h) return;
  const d = new Date();
  const hh = d.getHours() % 12;
  const mm = d.getMinutes();
  h.style.transform = `rotate(${hh * 30 + mm * .5}deg)`;
  h.style.marginTop = '-28px';
}
syncClock();
setInterval(syncClock, 60000);

/* ── 启动缩放 ── */
updateRoomScale();
window.addEventListener('resize', updateRoomScale);
window.addEventListener('orientationchange', updateRoomScale);
window.addEventListener('load', updateRoomScale);
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
        self._supervisor_ui_activity_guards_cache: Dict[str, Any] = {}
        self._supervisor_ui_memory_stats_cache: Dict[str, Any] = {}

    def _record_supervisor_ui_activity(
        self,
        event_type: str,
        *,
        scene: str = "planning",
        summary: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # Per baseline §3.4/§3.6 the supervisor (API-B) is the
        # governance identity of Mem — it MANAGES the task list and
        # runs endogenous drive; it never executes learning or
        # body-upgrade code.  Reject any scene that implies execution
        # (`learning`, `execution`) which is API-A territory.
        from systems.supervisor.planning_runtime import SUPERVISOR_LEGAL_SCENES

        if scene not in SUPERVISOR_LEGAL_SCENES:
            logger.warning(
                "Refusing illegal supervisor scene=%r for event_type=%r; "
                "falling back to 'planning'. Legal supervisor scenes: %s",
                scene, event_type, sorted(SUPERVISOR_LEGAL_SCENES),
            )
            scene = "planning"

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

    def _latest_drive_candidate_snapshot(self) -> List[Dict[str, Any]]:
        for event in self._recent_supervisor_ui_activity(limit=20):
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or "").strip().lower()
            metadata = dict(event.get("metadata") or {})
            if event_type == "endogenous_drive_evaluated":
                candidates = metadata.get("candidates")
                if isinstance(candidates, list):
                    return [dict(item) for item in candidates if isinstance(item, dict)]
            if event_type == "endogenous_drive_planned":
                tasks = metadata.get("tasks")
                if isinstance(tasks, list):
                    return [dict(item) for item in tasks if isinstance(item, dict)]
        return []

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

    def _clear_supervisor_ui_activity(self) -> None:
        events = getattr(self, "_supervisor_ui_events", None)
        if events is not None:
            events.clear()
        path = getattr(self, "_supervisor_ui_activity_path", None)
        if path is not None:
            payload = {
                "version": 1,
                "updated_at": datetime.utcnow().isoformat(),
                "events": [],
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

    def _default_ui_activity_guard_snapshot(self) -> Dict[str, Any]:
        return {
            "activity": {"active_sessions": 0, "counts": {}},
            "checks": {},
            "decisions": {},
            "thresholds": {},
            "user_chain_signal": {
                "scope": "soft_signal_only",
                "active_sessions": 0,
                "is_quiet": True,
                "quiet_after_seconds": 600,
            },
            "autonomous_chain_gate_active": bool(
                getattr(getattr(self, "_service_runtime", None), "autonomous_chain_gate_active", False)
            ),
            "snapshot_source": "default",
        }

    async def _load_ui_activity_guard_snapshot(
        self,
        *,
        timeout_seconds: float = 0.8,
    ) -> tuple[Dict[str, Any], bool]:
        default_snapshot = self._default_ui_activity_guard_snapshot()
        try:
            payload = await asyncio.wait_for(
                self.evaluate_activity_guards({}),
                timeout=max(float(timeout_seconds), 0.05),
            )
        except Exception:
            cached = dict(getattr(self, "_supervisor_ui_activity_guards_cache", {}) or {})
            if cached:
                cached["snapshot_source"] = "cached"
                cached["autonomous_chain_gate_active"] = bool(
                    cached.get(
                        "autonomous_chain_gate_active",
                        default_snapshot.get("autonomous_chain_gate_active", False),
                    )
                )
                return cached, False
            return default_snapshot, False

        normalized = {
            "activity": dict(payload.get("activity") or {}),
            "checks": dict(payload.get("checks") or {}),
            "decisions": dict(payload.get("decisions") or {}),
            "thresholds": dict(payload.get("thresholds") or {}),
            "user_chain_signal": dict(payload.get("user_chain_signal") or {}),
            "autonomous_chain_gate_active": bool(
                payload.get(
                    "autonomous_chain_gate_active",
                    default_snapshot.get("autonomous_chain_gate_active", False),
                )
            ),
            "snapshot_source": "live",
        }
        if not normalized["user_chain_signal"]:
            normalized["user_chain_signal"] = dict(default_snapshot["user_chain_signal"])
        normalized["user_chain_signal"]["scope"] = str(
            normalized["user_chain_signal"].get("scope") or "soft_signal_only"
        ).strip() or "soft_signal_only"
        self._supervisor_ui_activity_guards_cache = dict(normalized)
        return normalized, True

    async def _load_ui_memory_stats(
        self,
        *,
        timeout_seconds: float = 0.8,
    ) -> Dict[str, Any]:
        try:
            stats = await asyncio.wait_for(
                self._fetch_tier1_stats(),
                timeout=max(float(timeout_seconds), 0.05),
            )
        except Exception:
            cached = dict(getattr(self, "_supervisor_ui_memory_stats_cache", {}) or {})
            if cached:
                cached["snapshot_source"] = "cached"
                return cached
            return {
                "memory_unavailable": True,
                "memory_unavailable_reason": "ui_snapshot_unavailable",
                "memory_active": False,
                "snapshot_source": "default",
            }

        normalized = dict(stats or {})
        normalized["snapshot_source"] = "live"
        self._supervisor_ui_memory_stats_cache = dict(normalized)
        return normalized

    async def _load_ui_observation_timeline(
        self,
        *,
        limit: int = 12,
    ) -> List[Dict[str, Any]]:
        try:
            return self._recent_local_supervisor_observation_timeline(limit=limit)
        except Exception:
            return self._recent_supervisor_ui_activity(limit=limit)

    def _collect_ui_trace_records(
        self,
        *,
        trace_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        records.extend(self._collect_trace_records_from_tasks(trace_id=trace_id))
        records.extend(self._collect_trace_records_from_supervisor_activity(trace_id=trace_id))
        records.extend(
            self._collect_trace_records_from_governor_history(
                trace_id=trace_id,
                limit=max(int(limit), 1),
            )
        )
        return records

    def _recent_local_supervisor_observation_timeline(
        self,
        *,
        limit: int = 12,
    ) -> List[Dict[str, Any]]:
        records = self._collect_ui_trace_records(limit=max(int(limit) * 4, 24))
        timeline = [
            dict(record)
            for record in self._build_trace_timeline(records)
            if str(record.get("trace_id") or "").strip()
        ]
        timeline.reverse()
        return timeline[: max(int(limit), 0)]

    async def get_supervisor_ui_state(self) -> Dict[str, Any]:
        backlog_tasks = list(self._autonomous_chain_store.list_governance_backlog_tasks())
        writeback_history_tasks = list(self._autonomous_chain_store.list_writeback_history())
        all_serialized_tasks = [
            self._serialize_autonomous_chain_task(task)
            for task in [*backlog_tasks, *writeback_history_tasks]
        ]
        all_serialized_tasks.sort(
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )

        visible_tasks = [
            self._serialize_autonomous_chain_task(task)
            for task in backlog_tasks
        ]
        visible_tasks.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)

        drive_candidates: List[Dict[str, Any]] = self._latest_drive_candidate_snapshot()

        # Extract metrics from gateway activity for richer UI expression
        (
            activity_guard_snapshot_with_status,
            tier1_stats,
            observation_timeline,
        ) = await asyncio.gather(
            self._load_ui_activity_guard_snapshot(),
            self._load_ui_memory_stats(),
            self._load_ui_observation_timeline(limit=12),
        )
        activity_guard_snapshot, drive_available = activity_guard_snapshot_with_status
        activity = dict(activity_guard_snapshot.get("activity") or {})
        counts = dict(activity.get("counts") or {})
        error_count = int(counts.get("error_count") or 0)

        # ── Body status (direct from registry snapshot, not governance-task projection) ──
        body_status: Dict[str, Any] = {}
        try:
            registry = self._body_registry.load_registry()
            body_status = {
                "active_slot": getattr(registry, "active_slot", None),
                "retired_slot": getattr(registry, "retired_slot", None),
                "shell_slot": getattr(registry, "shell_slot", None),
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

        # ── Web room observation model ──
        autonomous_observation = self._build_autonomous_observation(
            visible_tasks,
            drive_candidates=drive_candidates,
            drive_available=drive_available,
            autonomous_chain_gate_active=bool(
                activity_guard_snapshot.get("autonomous_chain_gate_active", False)
            ),
            history_tasks=all_serialized_tasks,
            timeline=observation_timeline,
        )
        try:
            autonomous_observation = await asyncio.wait_for(
                self._attach_recent_trace_details_to_observation(autonomous_observation),
                timeout=2.0,
            )
        except Exception:
            pass
        metrics = self._build_ui_metrics(
            all_serialized_tasks,
            visible_tasks=visible_tasks,
            autonomous_observation=autonomous_observation,
            body_status=body_status,
            error_count=error_count,
        )

        scene, title, summary = self._map_supervisor_scene(
            all_tasks=visible_tasks,
            drive_candidates=drive_candidates,
            drive_available=drive_available,
            error_count=error_count,
            memory_active=tier1_stats.get("memory_active", False),
        )

        # ── LM Input info (for 🧠 panel) ──
        lm_input: Dict[str, Any] = {
            "generation_enabled": bool(
                getattr(
                    getattr(self.config, "service_runtime", None),
                    "endogenous_drive_lm_task_generation_enabled",
                    False,
                )
            ),
        }

        def _loaded_cognition_state() -> Dict[str, Any]:
            raw_snapshot = self._load_endogenous_cognition_state()
            if isinstance(raw_snapshot.get("state"), dict):
                return dict(raw_snapshot.get("state") or {})
            return dict(raw_snapshot or {})

        # Extract recent LM call metadata from drive history / cognition state
        try:
            cog_snapshot = _loaded_cognition_state()
            proposal_cog = cog_snapshot.get("proposal_cognition") or {}
            lm_trace = dict(proposal_cog.get("lm_trace") or {})
            if lm_trace.get("status"):
                lm_input["status"] = lm_trace["status"]
            if lm_trace.get("model_role"):
                lm_input["model_role"] = lm_trace["model_role"]
            if lm_trace.get("proposal_count") is not None:
                lm_input["proposal_count"] = lm_trace["proposal_count"]
            # Recent evidence nodes from uncertainty ledger
            ledger = cog_snapshot.get("uncertainty_ledger") or {}
            recent_nodes = ledger.get("recent_nodes") or []
            if recent_nodes:
                lm_input["recent_evidence_nodes"] = [
                    {"node": n.get("node_id", ""), "title": n.get("title", ""), "summary": n.get("summary", "")}
                    for n in recent_nodes[:20]
                ]
            # Prompt preview (if available)
            strategy = cog_snapshot.get("strategy_memory") or {}
            last_prompt = strategy.get("last_prompt_preview") or ""
            if last_prompt:
                lm_input["prompt_preview"] = str(last_prompt)[:2000]
        except Exception:
            pass

        # ── Cognition state (for 📊 panel) ──
        cognition: Dict[str, Any] = {}
        try:
            cog_snapshot = _loaded_cognition_state()
            perception = cog_snapshot.get("perception") or {}
            world_model = cog_snapshot.get("world_model") or {}
            # Build perception summary
            cognition["perception"] = {
                "system_posture": perception.get("system_posture", "balanced"),
                "user_mode": perception.get("user_mode", "unknown"),
                "governance_backlog_count": perception.get("governance_backlog_count", 0),
                "active_sessions": perception.get("active_sessions", 0),
                "recent_errors": perception.get("recent_errors", 0),
                "learning_quality": perception.get("learning_quality", 0),
                "correction_signals": perception.get("correction_signals", 0),
                "idle_seconds": perception.get("idle_seconds", {}),
            }
            # Build world model summary
            cognition["world_model"] = {
                "governance_load_state": world_model.get("governance_load_state", "unknown"),
                "memory_pressure": world_model.get("memory_pressure", 0),
                "truthfulness_pressure": world_model.get("truthfulness_pressure", 0),
                "learning_momentum": world_model.get("learning_momentum", 0),
                "body_upgrade_readiness": world_model.get("body_upgrade_readiness", 0),
                "self_confidence": world_model.get("self_confidence", 0),
            }
            # Needs
            raw_needs = cog_snapshot.get("needs") or []
            cognition["needs"] = [
                {
                    "need_type": n.get("need_type", "unknown"),
                    "severity": n.get("severity", 0),
                    "urgency": n.get("urgency", 0),
                    "confidence": n.get("confidence", 0),
                    "rationale": str(n.get("rationale", ""))[:200],
                }
                for n in raw_needs[:8]
            ]
            # Intents
            raw_intents = cog_snapshot.get("intents") or []
            cognition["intents"] = [
                {
                    "intent_type": i.get("intent_type", "unknown"),
                    "priority": i.get("priority", 0),
                    "output_channel": i.get("output_channel", "task_candidates"),
                    "target_horizon": i.get("target_horizon", "immediate"),
                    "rationale": str(i.get("rationale", ""))[:150],
                }
                for i in raw_intents[:6]
            ]
            # Signals
            raw_signals = cog_snapshot.get("signals") or []
            cognition["signals"] = [
                {
                    "signal_type": s.get("signal_type", "unknown"),
                    "priority": s.get("priority", 0),
                    "message": str(s.get("message", ""))[:200],
                }
                for s in raw_signals[:5]
            ]
            # Adaptive policy
            raw_policy = cog_snapshot.get("adaptive_policy") or {}
            cognition["adaptive_policy"] = {
                "learning_expansion_bias": raw_policy.get("learning_expansion_bias", 0),
                "truthfulness_bias": raw_policy.get("truthfulness_bias", 0),
                "memory_continuity_bias": raw_policy.get("memory_continuity_bias", 0),
                "governance_hygiene_bias": raw_policy.get("governance_hygiene_bias", 0),
                "body_growth_bias": raw_policy.get("body_growth_bias", 0),
                "observation_bias": raw_policy.get("observation_bias", 0),
                "candidate_throttle": raw_policy.get("candidate_throttle", 1.0),
                "candidate_budget": raw_policy.get("candidate_budget", 3),
                "exploratory_learning_quota": raw_policy.get("exploratory_learning_quota", 0),
                "body_growth_quota": raw_policy.get("body_growth_quota", 0),
                "preferred_focus": raw_policy.get("preferred_focus", "balanced"),
            }
        except Exception:
            pass

        ui_activity_guards = {
            "scope": "user_chain_soft_signal_only",
            "checks": dict(activity_guard_snapshot.get("checks") or {}),
            "decisions": dict(activity_guard_snapshot.get("decisions") or {}),
            "thresholds": dict(activity_guard_snapshot.get("thresholds") or {}),
            "user_chain_signal": dict(activity_guard_snapshot.get("user_chain_signal") or {}),
            "autonomous_chain_gate_active": bool(
                activity_guard_snapshot.get("autonomous_chain_gate_active", False)
            ),
            "snapshot_source": str(activity_guard_snapshot.get("snapshot_source") or "default"),
        }
        autonomous_runtime = dict(autonomous_observation.get("runtime") or {})
        autonomous_runtime["activity_guards"] = ui_activity_guards
        autonomous_runtime["user_chain_signal"] = dict(ui_activity_guards.get("user_chain_signal") or {})
        autonomous_runtime["eligibility"] = dict(ui_activity_guards.get("decisions") or {})
        autonomous_observation["runtime"] = autonomous_runtime
        autonomous_observation["metrics"] = metrics

        return {
            "status": "ok",
            "scene": scene,
            "title": title,
            "summary": summary,
            "generated_at": datetime.utcnow().isoformat(),
            "autonomous_observation": autonomous_observation,
            "schedule": schedule,
            "mem_usage": mem_usage,
            "tier1_stats": tier1_stats,
            "body_status": body_status,
            "error_count": error_count,
            "timeline": observation_timeline[:10],
            "lm_input": lm_input,
            "cognition": cognition,
        }

    def _is_creativity_ui_task(self, task: Dict[str, Any]) -> bool:
        governance = str(task.get("governance_task_type") or "").strip().lower()
        execution_kind = str(task.get("execution_kind") or "").strip().lower()
        return governance == "self_learning" or execution_kind == "body_improvement"

    def _observation_status_value(self, task: Dict[str, Any]) -> str:
        return str(task.get("status") or "").strip().lower()

    def _is_api_a_dispatch_task(self, task: Dict[str, Any]) -> bool:
        return self._is_creativity_ui_task(task) and self._observation_status_value(task) in {
            "approved",
            "running",
            "retry",
        }

    def _ui_task_sort_key(self, task: Dict[str, Any]) -> tuple[int, str]:
        status = str(task.get("status") or "").strip().lower()
        order = {
            "running": 0,
            "approved": 1,
            "planned": 2,
            "deferred": 3,
            "paused": 4,
        }
        updated = str(task.get("updated_at") or task.get("created_at") or "")
        return (order.get(status, 9), updated)

    def _observation_sort_key(self, task: Dict[str, Any]) -> tuple[str, str, str]:
        scheduled_for = self._observation_schedule_token(task) or "9999-12-31T23:59:59"
        created = str(task.get("created_at") or "")
        updated = str(task.get("updated_at") or "")
        return (scheduled_for, created or updated, updated or created)

    def _observation_display_status(self, task: Dict[str, Any]) -> str:
        status = str(task.get("status") or "").strip().lower()
        mapping = {
            "planned": "待审核",
            "awaiting_review": "待审查",
            "approved": "待执行",
            "running": "执行中",
            "deferred": "已推迟",
            "paused": "已暂停",
            "retry": "重试",
        }
        return mapping.get(status, status or "待定")

    def _loop_stage_status_label(self, status: str) -> str:
        mapping = {
            "active": "当前在途",
            "ready": "已观察到",
            "idle": "等待中",
        }
        return mapping.get(str(status or "").strip().lower(), "等待中")

    def _observation_role_tag(self, task: Dict[str, Any]) -> str:
        return "agent" if self._is_creativity_ui_task(task) else "supervisor"

    def _build_observation_card(
        self,
        payload: Optional[Dict[str, Any]],
        *,
        lane: str,
        display_status: Optional[str] = None,
        status: Optional[str] = None,
        summary_override: Optional[str] = None,
        observation_role: Optional[str] = None,
        title_override: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None
        card = dict(payload)
        card["lane"] = str(lane or card.get("lane") or "supervisor").strip() or "supervisor"
        if title_override is not None:
            card["title"] = str(title_override).strip() or card.get("title") or "未命名"
        else:
            card["title"] = str(card.get("title") or "未命名").strip() or "未命名"
        if summary_override is not None:
            card["summary"] = str(summary_override).strip()[:160]
        elif card.get("summary") is not None:
            card["summary"] = str(card.get("summary") or "").strip()[:160]
        metadata = dict(card.get("metadata") or {})
        card["metadata"] = metadata
        if observation_role is not None:
            card["observation_role"] = observation_role
        if status is not None:
            card["status"] = str(status).strip().lower()
        else:
            card["status"] = str(card.get("status") or "").strip().lower()
        if display_status is not None:
            card["display_status"] = str(display_status).strip() or "待定"
        elif card.get("display_status") is None:
            card["display_status"] = self._observation_display_status(card)
        return card

    def _build_observation_group(
        self,
        *,
        key: str,
        label: str,
        empty_text: str,
        items: List[Dict[str, Any]],
        emphasis: str = "neutral",
        owner: str = "",
        stage_label: str = "",
        summary: str = "",
        order: int = 0,
        segment_kind: str = "",
    ) -> Dict[str, Any]:
        return {
            "key": key,
            "label": label,
            "empty_text": empty_text,
            "emphasis": emphasis,
            "owner": owner,
            "stage_label": stage_label,
            "summary": summary,
            "order": order,
            "segment_kind": segment_kind,
            "count": len(items),
            "items": list(items),
        }

    def _recent_chain_section_events(
        self,
        *,
        key: str,
        items: List[Dict[str, Any]],
        timeline: List[Dict[str, Any]],
        limit: int = 4,
    ) -> List[Dict[str, Any]]:
        task_ids = {
            str(item.get("task_id") or "").strip()
            for item in items
            if isinstance(item, dict) and str(item.get("task_id") or "").strip()
        }
        trace_ids = {
            str(item.get("trace_id") or "").strip()
            for item in items
            if isinstance(item, dict) and str(item.get("trace_id") or "").strip()
        }

        def _matches(event: Dict[str, Any]) -> bool:
            event_task_id = str(event.get("task_id") or "").strip()
            event_trace_id = str(event.get("trace_id") or "").strip()
            if event_task_id and event_task_id in task_ids:
                return True
            if event_trace_id and event_trace_id in trace_ids:
                return True
            event_type = str(event.get("event_type") or "").strip().lower()
            summary = str(event.get("summary") or "").strip().lower()
            if key == "api_b_candidates":
                return (
                    "endogenous_drive" in event_type
                    or "candidate" in summary
                    or "候选" in summary
                )
            if key == "mem_recent":
                return "writeback" in event_type or "写回" in summary
            return False

        matched: List[Dict[str, Any]] = []
        for event in timeline:
            if not isinstance(event, dict) or not _matches(event):
                continue
            matched.append(
                {
                    "recorded_at": event.get("recorded_at"),
                    "source": str(event.get("source") or "").strip(),
                    "event_type": str(event.get("event_type") or "").strip(),
                    "summary": str(event.get("summary") or "").strip()[:160],
                    "task_id": str(event.get("task_id") or "").strip(),
                    "trace_id": str(event.get("trace_id") or "").strip(),
                }
            )
            if len(matched) >= max(int(limit), 1):
                break
        return matched

    def _recent_chain_section_traces(
        self,
        *,
        items: List[Dict[str, Any]],
        recent_events: List[Dict[str, Any]],
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        titles_by_trace: Dict[str, List[str]] = {}
        task_ids_by_trace: Dict[str, List[str]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            trace_id = str(item.get("trace_id") or "").strip()
            if not trace_id:
                continue
            title = str(item.get("title") or "").strip()
            task_id = str(item.get("task_id") or "").strip()
            if title:
                titles = titles_by_trace.setdefault(trace_id, [])
                if title not in titles:
                    titles.append(title)
            if task_id:
                task_ids = task_ids_by_trace.setdefault(trace_id, [])
                if task_id not in task_ids:
                    task_ids.append(task_id)

        traces: List[Dict[str, Any]] = []
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for event in recent_events:
            trace_id = str((event or {}).get("trace_id") or "").strip()
            if not trace_id:
                continue
            grouped.setdefault(trace_id, []).append(dict(event))

        for trace_id, events in grouped.items():
            if not events:
                continue
            first = dict(events[0])
            sources: List[str] = []
            task_ids = list(task_ids_by_trace.get(trace_id) or [])
            for event in events:
                source = str(event.get("source") or "").strip()
                if source and source not in sources:
                    sources.append(source)
                event_task_id = str(event.get("task_id") or "").strip()
                if event_task_id and event_task_id not in task_ids:
                    task_ids.append(event_task_id)
            traces.append(
                {
                    "trace_id": trace_id,
                    "event_count": len(events),
                    "last_seen_at": first.get("recorded_at"),
                    "last_event_type": str(first.get("event_type") or "").strip(),
                    "latest_summary": str(first.get("summary") or "").strip()[:160],
                    "sources": sources,
                    "task_ids": task_ids,
                    "task_titles": list(titles_by_trace.get(trace_id) or []),
                }
            )
        traces.sort(key=lambda item: str(item.get("last_seen_at") or ""), reverse=True)
        return traces[: max(int(limit), 1)]

    def _resolve_chain_segment_focus_item(
        self,
        *,
        items: List[Dict[str, Any]],
        activity_items: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        candidates = [
            dict(item)
            for item in [*activity_items, *items]
            if isinstance(item, dict)
        ]
        if not candidates:
            return None

        preferred_statuses = (
            "active",
            "running",
            "ready",
            "approved",
            "candidate",
            "retry",
            "planned",
            "awaiting_review",
            "deferred",
            "paused",
            "completed",
            "failed",
        )
        for expected in preferred_statuses:
            for item in candidates:
                status = str(item.get("status") or "").strip().lower()
                if status == expected:
                    return dict(item)
        return dict(candidates[0])

    def _resolve_chain_segment_status(
        self,
        *,
        items: List[Dict[str, Any]],
        activity_items: List[Dict[str, Any]],
        recent_events: List[Dict[str, Any]],
    ) -> tuple[str, str]:
        candidates = [*activity_items, *items]
        normalized_statuses = {
            str(item.get("status") or "").strip().lower()
            for item in candidates
            if isinstance(item, dict)
        }
        if normalized_statuses.intersection({"active", "running"}):
            return "active", "当前有流动"
        if items or recent_events or normalized_statuses.intersection(
            {
                "ready",
                "approved",
                "candidate",
                "retry",
                "planned",
                "awaiting_review",
                "deferred",
                "paused",
                "completed",
                "failed",
            }
        ):
            return "ready", "已有观测"
        return "idle", "暂无信号"

    def _attach_chain_section_activity(
        self,
        *,
        chain_segments: List[Dict[str, Any]],
        timeline: List[Dict[str, Any]],
        activity_items_by_key: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> List[Dict[str, Any]]:
        enriched: List[Dict[str, Any]] = []
        for group in chain_segments:
            section = dict(group or {})
            items = [
                dict(item)
                for item in list(section.get("items") or [])
                if isinstance(item, dict)
            ]
            activity_items = [
                dict(item)
                for item in list((activity_items_by_key or {}).get(str(section.get("key") or "").strip()) or [])
                if isinstance(item, dict)
            ]
            recent_events = self._recent_chain_section_events(
                key=str(section.get("key") or "").strip(),
                items=[*items, *activity_items],
                timeline=timeline,
            )
            recent_traces = self._recent_chain_section_traces(
                items=[*items, *activity_items],
                recent_events=recent_events,
            )
            focus_item = self._resolve_chain_segment_focus_item(
                items=items,
                activity_items=activity_items,
            )
            segment_status, segment_status_label = self._resolve_chain_segment_status(
                items=items,
                activity_items=activity_items,
                recent_events=recent_events,
            )
            section["items"] = items
            section["recent_events"] = recent_events
            section["event_count"] = len(recent_events)
            section["recent_event_count"] = len(recent_events)
            section["latest_trace_id"] = next(
                (
                    str(event.get("trace_id") or "").strip()
                    for event in recent_events
                    if str(event.get("trace_id") or "").strip()
                ),
                "",
            )
            section["recent_traces"] = recent_traces
            section["trace_count"] = len(recent_traces)
            section["payload_count"] = len(items)
            section["segment_status"] = segment_status
            section["segment_status_label"] = segment_status_label
            section["focus_item"] = dict(focus_item) if isinstance(focus_item, dict) else None
            section["latest_item"] = (
                dict(items[0])
                if items
                else (dict(focus_item) if isinstance(focus_item, dict) else None)
            )
            latest_summary = ""
            if recent_events:
                latest_summary = str(recent_events[0].get("summary") or "").strip()
            if not latest_summary and isinstance(focus_item, dict):
                latest_summary = str(focus_item.get("summary") or "").strip()
            if not latest_summary:
                latest_summary = str(section.get("summary") or section.get("empty_text") or "").strip()
            section["latest_summary"] = latest_summary[:160]
            section["projection_scope"] = "chain_segment_projection"
            enriched.append(section)
        return enriched

    async def _load_recent_trace_details(
        self,
        trace_ids: List[str],
        *,
        limit: int = 6,
    ) -> Dict[str, Dict[str, Any]]:
        normalized: List[str] = []
        for trace_id in trace_ids:
            candidate = str(trace_id or "").strip()
            if not candidate or candidate in normalized:
                continue
            normalized.append(candidate)
            if len(normalized) >= max(int(limit), 1):
                break

        async def _load(trace_id: str) -> tuple[str, Dict[str, Any]]:
            records = self._collect_ui_trace_records(trace_id=trace_id, limit=200)
            summary = self._summarize_single_trace(trace_id, records)
            timeline = [
                dict(event)
                for event in self._build_trace_timeline(records)
            ]
            preview = [
                {
                    "recorded_at": event.get("recorded_at"),
                    "source": str(event.get("source") or "").strip(),
                    "event_type": str(event.get("event_type") or "").strip(),
                    "summary": str(event.get("summary") or "").strip()[:160],
                    "task_id": str(event.get("task_id") or "").strip(),
                    "decision_id": str(event.get("decision_id") or "").strip(),
                }
                for event in reversed(timeline[-6:])
            ]
            all_events = [
                {
                    "recorded_at": event.get("recorded_at"),
                    "source": str(event.get("source") or "").strip(),
                    "event_type": str(event.get("event_type") or "").strip(),
                    "summary": str(event.get("summary") or "").strip()[:160],
                    "task_id": str(event.get("task_id") or "").strip(),
                    "decision_id": str(event.get("decision_id") or "").strip(),
                }
                for event in reversed(timeline[-20:])
            ]
            return trace_id, {
                "trace_id": trace_id,
                "found": bool(summary.get("record_count")),
                "record_count": int(summary.get("record_count") or 0),
                "first_seen_at": summary.get("first_seen_at"),
                "last_seen_at": summary.get("last_seen_at"),
                "source_counts": dict(summary.get("sources") or {}),
                "task_ids": list(summary.get("task_ids") or []),
                "decision_ids": list(summary.get("decision_ids") or []),
                "task_families": list(summary.get("task_families") or []),
                "execution_kinds": list(summary.get("execution_kinds") or []),
                "timeline_preview": preview,
                "timeline_events": all_events,
            }

        results = await asyncio.gather(*[_load(trace_id) for trace_id in normalized])
        return {trace_id: detail for trace_id, detail in results}

    async def _attach_recent_trace_details_to_observation(
        self,
        observation: Dict[str, Any],
    ) -> Dict[str, Any]:
        chain = dict(observation.get("chain") or {})
        segments = [
            dict(section)
            for section in list(chain.get("segments") or [])
            if isinstance(section, dict)
        ]
        trace_ids: List[str] = []
        for section in segments:
            for trace in list(section.get("recent_traces") or []):
                if not isinstance(trace, dict):
                    continue
                trace_id = str(trace.get("trace_id") or "").strip()
                if trace_id and trace_id not in trace_ids:
                    trace_ids.append(trace_id)
        if not trace_ids:
            return observation

        details = await self._load_recent_trace_details(trace_ids)
        enriched_segments: List[Dict[str, Any]] = []
        for section in segments:
            traces: List[Dict[str, Any]] = []
            for trace in list(section.get("recent_traces") or []):
                if not isinstance(trace, dict):
                    continue
                trace_payload = dict(trace)
                trace_id = str(trace_payload.get("trace_id") or "").strip()
                if trace_id:
                    trace_payload["detail"] = dict(details.get(trace_id) or {})
                traces.append(trace_payload)
            section["recent_traces"] = traces
            latest_trace_id = str(section.get("latest_trace_id") or "").strip()
            if latest_trace_id and latest_trace_id in details:
                section["latest_trace_detail"] = dict(details.get(latest_trace_id) or {})
            enriched_segments.append(section)

        chain["segments"] = enriched_segments
        observation["chain"] = chain
        return observation

    def _observation_schedule_token(self, payload: Dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""
        nested_sources = [
            payload,
            payload.get("metadata"),
            payload.get("constraints"),
            payload.get("evidence"),
            (payload.get("evidence") or {}).get("endogenous_drive"),
        ]
        for source in nested_sources:
            if not isinstance(source, dict):
                continue
            for key in (
                "scheduled_for",
                "preset_time",
                "scheduled_at",
                "run_at",
                "execute_after",
                "time_slot",
                "window",
            ):
                value = str(source.get(key) or "").strip()
                if value:
                    return value
        return ""

    def _build_autonomous_observation(
        self,
        all_tasks: List[Dict[str, Any]],
        *,
        drive_candidates: List[Dict[str, Any]],
        drive_available: bool,
        autonomous_chain_gate_active: bool,
        history_tasks: Optional[List[Dict[str, Any]]] = None,
        timeline: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        creativity_tasks = [
            task for task in all_tasks if self._is_creativity_ui_task(task)
        ]
        supervisor_tasks = [
            task for task in all_tasks if not self._is_creativity_ui_task(task)
        ]

        creativity_sorted = sorted(creativity_tasks, key=self._observation_sort_key)
        supervisor_sorted = sorted(supervisor_tasks, key=self._observation_sort_key)
        creativity_dispatch_source = [
            task for task in creativity_sorted if self._is_api_a_dispatch_task(task)
        ]
        creativity_running_source = [
            task
            for task in creativity_dispatch_source
            if self._observation_status_value(task) == "running"
        ]
        creativity_governance_source = [
            task for task in creativity_sorted if not self._is_api_a_dispatch_task(task)
        ]
        api_b_governance_source = sorted(
            [*supervisor_sorted, *creativity_governance_source],
            key=self._observation_sort_key,
        )

        def pick_active(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            running = [
                row for row in rows
                if self._observation_status_value(row) == "running"
            ]
            if running:
                return sorted(running, key=self._observation_sort_key)[0]
            approved = [
                row for row in rows
                if self._observation_status_value(row) == "approved"
            ]
            if approved:
                return sorted(approved, key=self._observation_sort_key)[0]
            return None

        supervisor_active = pick_active(supervisor_sorted)
        agent_active = pick_active(creativity_running_source)

        api_b_pending = [
            self._build_observation_card(
                task,
                lane="supervisor",
                observation_role="observed_task",
            )
            for task in api_b_governance_source
        ]
        api_b_pending = [task for task in api_b_pending if isinstance(task, dict)]
        api_a_dispatch_items = [
            self._build_observation_card(
                task,
                lane="agent",
                observation_role="observed_task",
            )
            for task in creativity_dispatch_source
        ]
        api_a_dispatch_items = [
            task for task in api_a_dispatch_items if isinstance(task, dict)
        ]
        api_a_ready = [
            task
            for task in api_a_dispatch_items
            if str(task.get("status") or "").strip().lower() in {"approved", "retry"}
        ]
        creativity_governance_cards = [
            card for card in api_b_pending if self._is_creativity_ui_task(card)
        ]

        seen_keys = {
            str(task.get("metadata", {}).get("endogenous_drive_key") or "").strip()
            for task in [*api_b_pending, *api_a_dispatch_items]
            if isinstance(task, dict)
        }
        seen_titles = {
            str(task.get("title") or "").strip()
            for task in [*api_b_pending, *api_a_dispatch_items]
            if isinstance(task, dict)
        }
        candidates: List[Dict[str, Any]] = []
        for candidate in drive_candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_key = str(
                candidate.get("metadata", {}).get("endogenous_drive_key")
                or candidate.get("stable_key")
                or ""
            ).strip()
            candidate_title = str(candidate.get("title") or "").strip()
            if candidate_key and candidate_key in seen_keys:
                continue
            if candidate_title and candidate_title in seen_titles:
                continue
            candidate_card = self._build_observation_card(
                candidate,
                lane="supervisor",
                display_status="API-B 候选判断",
                status="candidate",
                observation_role="candidate",
            )
            if candidate_card is not None:
                candidates.append(candidate_card)
            if candidate_key:
                seen_keys.add(candidate_key)
            if candidate_title:
                seen_titles.add(candidate_title)

        api_a_ready_focus = api_a_ready[0] if api_a_ready else None
        deferred_api_a_governance = [
            task
            for task in creativity_governance_cards
            if str(task.get("status") or "").strip().lower() == "deferred"
        ]
        completed_tasks = [
            task
            for task in (history_tasks or all_tasks)
            if str(task.get("status") or "").strip().lower() in {"completed", "failed"}
        ]
        recent_writebacks = [
            self._build_autonomous_writeback_summary(task)
            for task in completed_tasks[:3]
        ]
        recent_writeback_cards = [
            self._build_observation_card(
                item,
                lane="mem",
                observation_role="mem_writeback",
            )
            for item in recent_writebacks
        ]
        recent_writeback_cards = [
            item for item in recent_writeback_cards if isinstance(item, dict)
        ]

        if candidates:
            api_b_summary = f"{len(candidates)} 个候选待裁决"
            api_b_status = "active"
        elif api_b_pending:
            api_b_summary = f"{len(api_b_pending)} 个链路项仍在 API-B 治理段"
            api_b_status = "active"
        else:
            api_b_summary = "当前没有新的 API-B 判断信号"
            api_b_status = "idle"

        if agent_active:
            api_a_status = "active"
            api_a_summary = f"{str(agent_active.get('title') or '自主链路项').strip()} 正在由 API-A 自主执行面处理"
        elif api_a_ready:
            api_a_status = "ready"
            api_a_summary = f"{len(api_a_ready)} 个已放行链路项等待 API-A 自主执行面拉取"
        elif creativity_governance_cards:
            api_a_status = "idle"
            api_a_summary = f"{len(creativity_governance_cards)} 个自主链路项仍停留在 API-B 治理段"
        else:
            api_a_status = "idle"
            api_a_summary = "当前没有等待 API-A 自主执行的链路项"

        if recent_writebacks:
            writeback_status = "ready"
            writeback_summary = f"{recent_writebacks[0]['title']} 的结果已形成 Mem 写回记录"
        else:
            writeback_status = "idle"
            writeback_summary = "尚未观察到新的链路结果写回"

        if recent_writebacks and (candidates or api_b_pending):
            reread_status = "active"
            reread_summary = "API-B 正结合最新写回和当前候选继续管理下一轮判断"
        elif recent_writebacks:
            reread_status = "ready"
            reread_summary = "最新写回已可供下一轮 API-B 再读取"
        else:
            reread_status = "idle"
            reread_summary = "等待新的 Mem 写回结果进入下一轮判断"

        api_a_stage_label = "待命拉单"
        api_a_chain_reason = "链路: 当前没有已批准的 API-A 可执行链路项"
        api_a_activity_text = "执行流: 等待监督者放行链路项或等待下一轮拉单"
        api_a_reason_style = "dim"
        if agent_active:
            api_a_stage_label = "他处执行中"
            api_a_chain_reason = "链路: 该链路项已被其他 API-A 自主执行面认领"
            api_a_activity_text = "执行流: 链路项正在其他 API-A 自主执行面中运行"
            api_a_reason_style = "info"
        elif api_a_ready:
            api_a_stage_label = "已放行待认领"
            api_a_chain_reason = "链路: 监督者已放行该链路项，等待 API-A 自主执行面认领"
            api_a_activity_text = "执行流: 监督者已放行链路项，等待 API-A 自主执行面认领"
            api_a_reason_style = "warn"
        elif deferred_api_a_governance:
            api_a_chain_reason = "链路: 当前学习链路项大多仍停留在 API-B 治理段并被延后，尚未进入 API-A 待拉取段"
            api_a_activity_text = "执行流: 等待 API-B 重新放行、重排或补充证据"
            api_a_reason_style = "warn"
        elif creativity_governance_cards:
            api_a_chain_reason = "链路: 当前自主链路项仍停留在 API-B 治理段，尚未进入 API-A 待拉取段"
            api_a_activity_text = "执行流: 等待 API-B 审核、放行或重新排序链路项"
            api_a_reason_style = "info"
        elif recent_writebacks or candidates or api_b_pending:
            api_a_chain_reason = "链路: 当前没有新的 API-A 可执行链路项；API-B 正在判断、回收写回或推进下一轮再读取"
            api_a_reason_style = "info"

        api_b_current = self._build_observation_card(
            supervisor_active
            or (api_b_pending[0] if api_b_pending else None)
            or (candidates[0] if candidates else None)
            or {"title": "API-B 判断"},
            lane="supervisor",
            display_status=self._loop_stage_status_label(api_b_status),
            status=api_b_status,
            summary_override=api_b_summary,
            observation_role="api_b_judgement",
            title_override=(
                str((supervisor_active or {}).get("title") or "").strip()
                or str((api_b_pending[0] if api_b_pending else {}).get("title") or "").strip()
                or str((candidates[0] if candidates else {}).get("title") or "").strip()
                or "API-B 判断"
            ),
        )
        api_a_current = self._build_observation_card(
            agent_active
            or api_a_ready_focus
            or {"title": "API-A 自主执行"},
            lane="agent",
            display_status=self._loop_stage_status_label(api_a_status),
            status=api_a_status,
            summary_override=api_a_summary,
            observation_role="api_a_execution",
            title_override=(
                str((agent_active or {}).get("title") or "").strip()
                or str((api_a_ready_focus or {}).get("title") or "").strip()
                or "API-A 自主执行"
            ),
        )
        mem_current = self._build_observation_card(
            (recent_writeback_cards[0] if recent_writeback_cards else None)
            or {"title": "Mem 写回"},
            lane="mem",
            display_status=self._loop_stage_status_label(writeback_status),
            status=writeback_status,
            summary_override=writeback_summary,
            observation_role="mem_writeback",
            title_override=(
                str((recent_writeback_cards[0] if recent_writeback_cards else {}).get("title") or "").strip()
                or "Mem 写回"
            ),
        )
        reread_card = self._build_observation_card(
            {"title": "API-B 再读取", "summary": reread_summary},
            lane="supervisor",
            display_status=self._loop_stage_status_label(reread_status),
            status=reread_status,
            summary_override=reread_summary,
            observation_role="api_b_reread",
        )
        api_b_active_task = (
            self._build_observation_card(
                supervisor_active,
                lane="supervisor",
                observation_role="api_b_active_task",
            )
            if supervisor_active
            else None
        )
        api_a_active_task = (
            self._build_observation_card(
                agent_active,
                lane="agent",
                observation_role="api_a_active_task",
            )
            if agent_active
            else None
        )

        chain_segments = [
            self._build_observation_group(
                key="api_b_candidates",
                label="API-B 候选判断",
                empty_text="当前没有新的 API-B 候选判断",
                items=candidates[:6],
                emphasis="candidate",
                owner="API-B",
                stage_label="候选形成",
                summary="内生驱动刚形成、尚待 API-B 判断是否进入治理链路的候选。",
                order=0,
                segment_kind="candidate_judgement",
            ),
            self._build_observation_group(
                key="api_b_backlog",
                label="API-B 治理在途",
                empty_text="当前没有新的 API-B 治理在途链路项",
                items=api_b_pending[:6],
                emphasis="supervisor",
                owner="API-B",
                stage_label="判断与治理",
                summary="仍停留在 API-B 审核、重排、延后或内部维护阶段的链路项。",
                order=1,
                segment_kind="governance_backlog",
            ),
            self._build_observation_group(
                key="api_a_ready",
                label="API-A 待拉取",
                empty_text="当前没有进入 API-A 拉取窗口的自主链路项",
                items=api_a_ready[:6],
                emphasis="agent",
                owner="API-A",
                stage_label="自主执行",
                summary="已经放行、处于 API-A 拉取窗口的自主链路项。",
                order=2,
                segment_kind="execution_ready",
            ),
            self._build_observation_group(
                key="mem_recent",
                label="Mem 最近写回",
                empty_text="尚未观察到新的 Mem 写回记录",
                items=recent_writeback_cards[:4],
                emphasis="mem",
                owner="Mem",
                stage_label="写回回流",
                summary="最近完成并已经回流到 Mem 的自主链路结果记录。",
                order=3,
                segment_kind="mem_writeback",
            ),
        ]
        chain_segments = self._attach_chain_section_activity(
            chain_segments=chain_segments,
            timeline=[
                dict(event)
                for event in list(timeline or [])
                if isinstance(event, dict)
            ],
            activity_items_by_key={
                "api_b_backlog": [
                    item
                    for item in (api_b_current, api_b_active_task, *api_b_pending)
                    if isinstance(item, dict)
                ],
                "api_a_ready": [
                    item
                    for item in (api_a_current, api_a_active_task, *api_a_ready)
                    if isinstance(item, dict)
                ],
                "api_b_candidates": [
                    item
                    for item in candidates
                    if isinstance(item, dict)
                ],
                "mem_recent": [
                    item
                    for item in (mem_current, *recent_writeback_cards)
                    if isinstance(item, dict)
                ],
            },
        )
        focus_card = next(
            (
                card
                for card in (api_b_current, api_a_current, mem_current, reread_card)
                if isinstance(card, dict)
                and str(card.get("status") or "").strip().lower() in {"active", "ready"}
            ),
            api_b_current,
        )
        board = {
            "headline": "自主链路闭环观测",
            "summary": (
                "Web 小屋以 API-B 为主视角，只观察判断、治理、Mem 回流与 API-A 执行回报；"
                "用户链路只作为软感知信号。"
            ),
            "primary_focus": {
                "title": str((focus_card or {}).get("title") or "自主链路闭环").strip(),
                "status": str((focus_card or {}).get("display_status") or "等待中").strip(),
                "stage_status": str((focus_card or {}).get("status") or "idle").strip().lower(),
                "summary": str((focus_card or {}).get("summary") or "").strip(),
                "observation_role": str((focus_card or {}).get("observation_role") or "").strip(),
                "stage_key": str((focus_card or {}).get("observation_role") or "").strip(),
                "stage_owner": str((focus_card or {}).get("lane") or "").strip(),
            },
        }

        return {
            "read_model_version": 7,
            "mode": {
                "label": "观测模式",
                "scope": "api_b_autonomous_chain_only",
                "status_text": "只读观测 API-B 与自主链路",
            },
            "runtime": {
                "drive_available": bool(drive_available),
                "autonomous_chain_gate_active": bool(autonomous_chain_gate_active),
            },
            "chain": {
                "headline": "自主链路分段观察",
                "summary": "显式展示 API-B 候选形成、API-B 治理在途、API-A 待拉取与 Mem 最近写回四段链路分段。",
                "segments": chain_segments,
            },
            "board": board,
            "loop": {
                "boundary": (
                    "自主链路闭环只展示 API-B 判断、API-A 自主执行、Mem 写回和 API-B 再读取；"
                    "用户链路只作为软感知信号，不展示聊天内容。"
                ),
                "stages": [
                    {
                        "key": "api_b_judgement",
                        "label": "API-B 判断",
                        "owner": "API-B",
                        "status": api_b_status,
                        "summary": api_b_summary,
                        "focus_task": api_b_active_task or (candidates[0] if candidates else None) or (api_b_pending[0] if api_b_pending else None),
                    },
                    {
                        "key": "api_a_execution",
                        "label": "API-A 自主执行",
                        "owner": "API-A",
                        "status": api_a_status,
                        "summary": api_a_summary,
                        "status_label": api_a_stage_label,
                        "chain_reason": api_a_chain_reason,
                        "activity_text": api_a_activity_text,
                        "reason_style": api_a_reason_style,
                        "focus_task": api_a_active_task or api_a_ready_focus,
                    },
                    {
                        "key": "mem_writeback",
                        "label": "Mem 写回",
                        "owner": "Mem",
                        "status": writeback_status,
                        "summary": writeback_summary,
                        "focus_task": recent_writeback_cards[0] if recent_writeback_cards else None,
                    },
                    {
                        "key": "api_b_reread",
                        "label": "API-B 再读取",
                        "owner": "API-B",
                        "status": reread_status,
                        "summary": reread_summary,
                        "focus_task": recent_writeback_cards[0] if recent_writeback_cards else None,
                    },
                ],
                "recent_writebacks": recent_writebacks,
            },
            "counts": {
                "pending": len(api_b_pending) + len(api_a_ready),
                "active": sum(1 for task in (supervisor_active, agent_active) if task),
                "candidates": len(candidates),
                "writebacks": len(recent_writebacks),
                "api_b_backlog": len(api_b_pending),
                "api_a_ready": len(api_a_ready),
            },
        }

    def _build_autonomous_writeback_summary(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:
        metadata = dict(task.get("metadata") or {})
        execution_result = dict(metadata.get("execution_result") or {})
        summary = (
            execution_result.get("outcome_summary")
            or execution_result.get("summary")
            or execution_result.get("final_response")
            or task.get("decision_reason")
            or task.get("summary")
            or ""
        )
        return {
            "task_id": task.get("task_id"),
            "title": str(task.get("title") or "未命名"),
            "lane": self._observation_role_tag(task),
            "status": str(task.get("status") or "").strip().lower() or "completed",
            "status_label": self._observation_display_status(task),
            "summary": str(summary).strip()[:120],
        }

    async def _fetch_tier1_stats(self) -> Dict[str, Any]:
        """Fetch Tier 1 stats + memory_service rule execution status."""
        try:
            import aiohttp
            gateway_url = "http://127.0.0.1:6000"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{gateway_url}/admin/services", timeout=aiohttp.ClientTimeout(total=3)
                ) as resp:
                    if resp.status != 200:
                        return {
                            "memory_unavailable": True,
                            "memory_unavailable_reason": f"gateway_services_status_{resp.status}",
                            "memory_active": False,
                        }
                    services = (await resp.json()).get("services", {})
                memory_url = None
                for svc in services.values():
                    if svc.get("service_type") == "memory":
                        memory_url = svc.get("address")
                        break
                if not memory_url:
                    return {
                        "memory_unavailable": True,
                        "memory_unavailable_reason": "memory_service_not_registered",
                        "memory_active": False,
                    }
                # Fetch both stats and rules status in parallel
                stats_data = {}
                rules_data = {}
                async with session.get(
                    f"{memory_url}/tier1/stats", timeout=aiohttp.ClientTimeout(total=3)
                ) as resp:
                    if resp.status == 200:
                        stats_data = await resp.json()
                async with session.get(
                    f"{memory_url}/compressed/rules-status", timeout=aiohttp.ClientTimeout(total=3)
                ) as resp:
                    if resp.status == 200:
                        rules_data = await resp.json()
                result = dict(stats_data)
                result["rules"] = rules_data.get("rules", {})
                result["llm_healthy"] = rules_data.get("llm_healthy", False)
                result["effective_activity_at"] = rules_data.get("effective_activity_at")
                result["llm_health_checked_at"] = rules_data.get("llm_health_checked_at")
                # P0-4 健康信号: memory_active reflects REAL write work in the last
                # 2 cycles (effective_activity_at), not merely "a rule ran"
                # (last_run, which advances even on no-op cycles). A degraded /
                # idle / broken pipeline no longer shows "记忆活跃 ✅".
                from datetime import datetime, timedelta, timezone
                recent = datetime.now(timezone.utc) - timedelta(seconds=7200)
                memory_active = False
                eff = rules_data.get("effective_activity_at")
                if eff:
                    try:
                        t = datetime.fromisoformat(eff)
                        if t.tzinfo is None:
                            t = t.replace(tzinfo=timezone.utc)
                        memory_active = t > recent
                    except Exception:
                        memory_active = False
                result["memory_active"] = memory_active
                return result
        except Exception as exc:
            return {
                "memory_unavailable": True,
                "memory_unavailable_reason": type(exc).__name__,
                "memory_active": False,
            }

    def _build_ui_metrics(
        self,
        all_tasks: List[Dict[str, Any]],
        *,
        visible_tasks: List[Dict[str, Any]],
        autonomous_observation: Dict[str, Any],
        body_status: Dict[str, Any],
        error_count: int,
    ) -> Dict[str, Any]:
        """Build metrics for the autonomous-chain observation panels."""
        observed_task_total = len(all_tasks)
        autonomous_task_total = sum(
            1 for t in all_tasks if self._is_creativity_ui_task(t)
        )
        api_b_task_total = sum(
            1 for t in all_tasks if not self._is_creativity_ui_task(t)
        )
        evolution_total = sum(
            1
            for t in all_tasks
            if str(t.get("execution_kind") or "").strip().lower() == "body_improvement"
        )
        learning_completed = sum(
            1 for t in all_tasks if self._is_creativity_ui_task(t) and t.get("status") == "completed"
        )
        learning_failed = sum(
            1 for t in all_tasks if self._is_creativity_ui_task(t) and t.get("status") == "failed"
        )
        shadow_recommendations = 0
        shadow_actions: Dict[str, int] = {}
        direct_lm_actions = 0
        priority_updates = 0
        for task in all_tasks:
            preview = dict(task.get("governance_preview") or {})
            lm_shadow = preview.get("lm_governance_shadow")
            if isinstance(lm_shadow, dict):
                shadow_recommendations += 1
                action = str(lm_shadow.get("action") or "unknown")
                shadow_actions[action] = shadow_actions.get(action, 0) + 1
            lm_review = preview.get("lm_governance_review")
            if isinstance(lm_review, dict):
                direct_lm_actions += 1
            if isinstance(preview.get("lm_governance_priority"), dict):
                priority_updates += 1

        return {
            "observed_task_total": observed_task_total,
            "autonomous_task_total": autonomous_task_total,
            "api_b_task_total": api_b_task_total,
            "evolution_total": evolution_total,
            "by_path": {
                "learning": autonomous_task_total,
                "maintenance": api_b_task_total,
                "evolution": evolution_total,
            },
            "learning_results": {
                "completed": learning_completed,
                "failed": learning_failed,
            },
            "drive_candidates": int(
                dict(autonomous_observation.get("counts") or {}).get("candidates")
                or 0
            ),
            "slot_overview": self._format_slot_overview(body_status),
            "error_count": error_count,
            "running_count": sum(1 for t in visible_tasks if t.get("status") == "running"),
            "governance": {
                "direct_lm_actions": direct_lm_actions,
                "shadow_recommendations": shadow_recommendations,
                "shadow_action_counts": shadow_actions,
                "priority_updates": priority_updates,
            },
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
        all_tasks: List[Dict[str, Any]],
        drive_candidates: List[Dict[str, Any]],
        drive_available: bool,
        error_count: int = 0,
        memory_active: bool = False,
    ) -> tuple[str, str, str]:
        """Map current supervisor activity to one of SUPERVISOR_LEGAL_SCENES.

        Per architectural baseline §3.4/§3.6/§8.1, the supervisor (API-B)
        only MANAGES tasks — it never executes learning or body-upgrade
        code.  Therefore the supervisor's `scene` is restricted to:
          idle, planning, drive, memory, maintenance, dispatch
        The "learning", "code_editing", "executing", "body_switch" scenes
        belong to the Agent (API-A) or Executor, and are not legal
        returns from this method.  When the supervisor is judging a
        body-switch request, it reports `dispatch` (it has decided to
        hand off to the executor) — the executor then reports
        `body_switch` while mechanically executing the switch.
        """
        error_note = f" · {error_count} recent error(s)" if error_count > 0 else ""

        # ── Scene priority: running > memory_active > drive > queued > idle ──

        # 1. Active execution: dispatch if a task is running (we just dispatched it;
        #    the actual execution is the Agent's / Executor's responsibility).
        running = [t for t in all_tasks if t.get("status") == "running"]
        if running:
            r = running[0]
            rtitle = str(r.get("title") or "Running task")
            rfamily = str(r.get("task_family") or "")
            # Memory maintenance is the one case the supervisor is genuinely
            # "doing" the work itself (§3.4 — handled by supervisor's
            # internal memory service, not by API-A autonomous pull).
            if "memory" in rfamily:
                return (
                    "maintenance",
                    f"正在整理记忆{error_note}",
                    f"「{rtitle}」记忆维护任务正在执行。",
                )
            # For learning / body-upgrade running tasks, the supervisor
            # just dispatched them — show `dispatch` (NOT `learning` or
            # `body_switch`, which are Agent / Executor scenes).
            return (
                "dispatch",
                f"已派发任务{error_note}",
                f"「{rtitle}」已交给 API-A 自主执行面或执行器处理，结果将写回 Mem 供下一轮监督者判断。",
            )

        # 2. Supervisor-governed tasks that are ready now.
        supervisor_pending = [
            t
            for t in all_tasks
            if not self._is_creativity_ui_task(t)
            and str(t.get("status") or "").strip().lower() == "approved"
        ]
        if supervisor_pending:
            lp = sorted(supervisor_pending, key=self._observation_sort_key)[0]
            if "memory" in str(lp.get("task_family") or ""):
                return (
                    "maintenance",
                    f"正在整理记忆书架{error_note}",
                    f"「{lp.get('title', '维护任务')}」已进入监督者执行位，等待处理。",
                )
            return (
                "planning",
                f"正在安排治理事务{error_note}",
                f"「{lp.get('title', '监督者任务')}」已进入监督者执行位，等待处理。",
            )

        # 2.5 Memory model actively compressing (detected from memory_service rules_status)
        if memory_active:
            return (
                "memory",
                f"正在整理记忆{error_note}",
                "记忆模型正在执行压缩规则：衰减→桥接→升级→清退。",
            )

        # 3. Endogenous drive active
        if drive_candidates:
            first = drive_candidates[0]
            metadata = dict(first.get("metadata") or {})
            value_tags = ", ".join(metadata.get("core_values") or first.get("value_tags") or [])
            utility_pct = int((metadata.get("utility") or first.get("utility") or 0) * 100)
            return (
                "drive",
                f"发现值得优先处理的事{error_note}",
                f"「{first.get('title', '治理投影')}」从核心价值中浮现 [{value_tags}]，价值度 {utility_pct}%，等待治理审查。",
            )

        # 4. Memory maintenance queued
        maintenance_pending = [t for t in all_tasks if "memory" in str(t.get("task_family", "")) and t.get("status") in ("approved", "planned")]
        if maintenance_pending:
            mp = maintenance_pending[0]
            return (
                "maintenance",
                f"正在整理记忆书架{error_note}",
                f"「{mp.get('title', '维护任务')}」长期连续性正在被守护。",
            )

        # 5. Drive unavailable
        if not drive_available:
            return (
                "idle",
                "望着窗外",
                "网关无法访问，房间显示本地监督者状态——信号恢复后内生驱动将继续。",
            )

        # 6. Truly idle
        window_mood = "全天候自动执行已就绪，系统处于安静状态。"
        return (
            "idle",
            f"在窗边休息{error_note}",
            f"没有待处理的工作。{window_mood}自主链路闭环保持待命，核心价值仍在持续感知。",
        )

    def _format_slot_overview(self, body_status: Dict[str, Any]) -> str:
        active_slot = str(body_status.get("active_slot") or "").strip()
        shell_slot = str(body_status.get("shell_slot") or "").strip()
        if active_slot and shell_slot and active_slot != shell_slot:
            return f"{active_slot} / {shell_slot}"
        return active_slot or shell_slot or ""

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




