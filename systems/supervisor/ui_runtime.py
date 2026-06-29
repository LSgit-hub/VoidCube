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
.schedule {
  margin-top: 8px;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid rgba(255,255,255,.06);
  background: rgba(255,255,255,.035);
}
.schedule-label {
  font-size: 10px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: .04em;
  margin-bottom: 3px;
}
.schedule-countdown {
  font-size: 15px;
  font-weight: 700;
  color: var(--accent-blue);
  font-variant-numeric: tabular-nums;
}
.queue-stack {
  display: grid;
  gap: 12px;
}
.queue-section {
  display: grid;
  gap: 8px;
  padding: 10px;
  border-radius: 12px;
  border: 1px solid rgba(255,255,255,.06);
  background: rgba(255,255,255,.035);
}
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.section-label {
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: .04em;
}
.window-pill {
  min-width: 76px;
  padding: 3px 10px;
  border-radius: 999px;
  text-align: center;
  font-size: 10px;
  color: var(--text-secondary);
  background: rgba(255,255,255,.06);
}
.window-pill.open {
  color: var(--accent-green);
  background: rgba(111,198,160,.14);
}
.window-pill.closed {
  color: var(--accent-yellow);
  background: rgba(226,176,74,.14);
}
.queue-slot {
  display: grid;
  gap: 8px;
}
.queue-card {
  display: grid;
  gap: 6px;
  min-height: 56px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid rgba(255,255,255,.06);
  background: rgba(255,255,255,.04);
}
.queue-card.supervisor {
  box-shadow: inset 3px 0 0 rgba(111,198,160,.5);
}
.queue-card.agent {
  box-shadow: inset 3px 0 0 rgba(167,138,212,.6);
}
.queue-card.empty {
  min-height: 44px;
  color: var(--text-muted);
  background: rgba(255,255,255,.025);
}
.queue-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.queue-card-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.35;
}
.queue-card-subtitle {
  font-size: 10px;
  color: rgba(244,228,188,.75);
  line-height: 1.35;
}
.queue-card-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.queue-tag {
  font-size: 10px;
  color: var(--text-muted);
  letter-spacing: .03em;
  text-transform: uppercase;
}
.queue-empty-text {
  font-size: 11px;
  color: var(--text-muted);
}
.timed-list {
  display: grid;
  gap: 7px;
  max-height: 230px;
  overflow-y: auto;
}
.timed-item {
  display: grid;
  grid-template-columns: 10px 1fr auto;
  align-items: center;
  gap: 8px;
  min-height: 36px;
  padding: 7px 10px;
  border-radius: 9px;
  border: 1px solid rgba(255,255,255,.06);
  background: rgba(255,255,255,.035);
}
.timed-item.supervisor {
  background: rgba(111,198,160,.08);
}
.timed-item.agent {
  background: rgba(167,138,212,.08);
}
.timed-item-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.timed-item-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11.5px;
}
.timed-item-subtitle {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10px;
  color: rgba(244,228,188,.72);
}
.candidate-card {
  display: grid;
  gap: 6px;
  min-height: 56px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid rgba(226,176,74,.18);
  background: rgba(226,176,74,.08);
}
.candidate-card.empty {
  border-color: rgba(255,255,255,.06);
  background: rgba(255,255,255,.025);
}
.candidate-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.candidate-utility {
  font-size: 11px;
  font-weight: 700;
  color: var(--gold);
}
.candidate-tags {
  font-size: 10px;
  color: rgba(244,228,188,.72);
}

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

/* ── 队列面板专用 ── */
.queue-filter-row {
  display: flex; gap: 6px; flex-wrap: wrap;
}
.queue-filter-chip {
  padding: 4px 12px;
  border-radius: 99px;
  font-size: 10px; font-weight: 600;
  border: 1px solid rgba(255,255,255,.08);
  background: rgba(255,255,255,.03);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all .2s;
  font-family: inherit;
}
.queue-filter-chip:hover {
  background: rgba(255,255,255,.07);
  color: var(--text-primary);
}
.queue-filter-chip.active {
  background: rgba(226,176,74,.15);
  border-color: rgba(226,176,74,.3);
  color: var(--gold);
}

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
/* 双泳道 */
.lane-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 560px) { .lane-grid { grid-template-columns: 1fr; } }
.lane-col {
  border-radius: 12px; padding: 12px;
  background: rgba(255,255,255,.03);
  border: 1px solid rgba(255,255,255,.06);
}
.lane-col.supervisor { border-top: 2px solid var(--gold); }
.lane-col.agent { border-top: 2px solid var(--mint); }
.lane-col-head {
  font-size: 11px; font-weight: 700; color: var(--text-primary);
  display: flex; align-items: center; gap: 6px; margin-bottom: 4px;
}
.lane-col-tag { font-size: 8px; color: var(--text-muted); font-weight: 600; }
.lane-metric {
  display: flex; justify-content: space-between; align-items: baseline;
  font-size: 10.5px; color: var(--text-secondary); padding: 3px 0;
}
.lane-metric b { color: var(--text-primary); font-size: 13px; font-variant-numeric: tabular-nums; }
.lane-active {
  margin-top: 8px; padding: 8px; border-radius: 8px;
  background: rgba(0,0,0,.2); font-size: 10px; color: var(--text-secondary);
}
.lane-active .la-title { color: var(--text-primary); font-weight: 600; font-size: 11px; }
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

      <!-- 📋 任务面板 -->
      <div class="dock-panel" id="panelTasks">
        <div class="panel-header">
          <div class="panel-title"><span class="pt-icon">📋</span>任务看板</div>
          <button class="panel-close" data-panel="tasks">×</button>
        </div>
        <div class="panel-body" id="panelTasksBody">
        </div>
      </div>

      <!-- 🧠 LM 输入面板 -->
      <div class="dock-panel" id="panelLMInput">
        <div class="panel-header">
          <div class="panel-title"><span class="pt-icon">🧠</span>LM 输入监视器</div>
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

      <!-- ⚙️ 队列面板 -->
      <div class="dock-panel" id="panelQueue">
        <div class="panel-header">
          <div class="panel-title"><span class="pt-icon">⚙️</span>队列治理</div>
          <button class="panel-close" data-panel="queue">×</button>
        </div>
        <div class="panel-body" id="panelQueueBody">
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

      <button class="dock-btn" data-panel="tasks" title="任务看板">
        <span class="db-icon">📋</span>
        <span class="db-label">任务</span>
      </button>
      <span class="dock-sep"></span>
      <button class="dock-btn" data-panel="lminput" title="LM 输入">
        <span class="db-icon">🧠</span>
        <span class="db-label">LM输入</span>
      </button>
      <span class="dock-sep"></span>
      <button class="dock-btn" data-panel="cognition" title="认知状态">
        <span class="db-icon">📊</span>
        <span class="db-label">认知</span>
      </button>
      <span class="dock-sep"></span>
      <button class="dock-btn" data-panel="queue" title="队列治理">
        <span class="db-icon">⚙️</span>
        <span class="db-label">队列</span>
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
  panelTasksBody: $('#panelTasksBody'),
  panelLMInputBody: $('#panelLMInputBody'),
  panelCognitionBody: $('#panelCognitionBody'),
  panelQueueBody: $('#panelQueueBody'),
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
  const identity = t.task_identity || {};
  const displayKind = String(identity.display_kind || t.execution_kind || '').trim();
  const governance = String(t.governance_task_type || '').trim();
  const primary = displayKind || governance || String(t.task_family || '').trim();
  const typeMap = {
    self_learning: '自主学习', body_improvement: '替身改进',
    memory_maintenance: '记忆维护', self_evolution: '通用演化',
    general_self_evolution: '通用演化',
  };
  return typeMap[primary] || primary.replace(/_/g, ' ') || '任务';
}
function taskIdentityHint(t) {
  const identity = t.task_identity || {};
  const family = String(identity.task_family || t.task_family || '').trim();
  const displayKind = String(identity.display_kind || identity.execution_kind || '').trim();
  if (family && displayKind && family !== displayKind) return '任务家族: ' + family + ' · 执行动作: ' + displayKind;
  if (displayKind) return '任务类型: ' + displayKind;
  if (family) return '任务家族: ' + family;
  return '';
}
function governanceHint(t) {
  const preview = t.governance_preview || {};
  const direct = preview.lm_queue_review || null;
  const shadow = preview.lm_queue_shadow || null;
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
  const map = { planned:'待审核', approved:'待执行', running:'执行中', deferred:'已推迟', paused:'已暂停', completed:'已完成', failed:'失败', cancelled:'已取消', awaiting_review:'待审查', retry:'重试' };
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
    if (name === 'tasks') renderTasksPanel(lastState);
    if (name === 'lminput') renderLMInputPanel(lastState);
    if (name === 'cognition') renderCognitionPanel(lastState);
    if (name === 'queue') renderQueuePanel(lastState);
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
let drawerOpen = null;  // 当前抽屉类型: 'lanes' | 'provenance' | 'health' | null

const DRAWER_META = {
  lanes:      { icon: '🚦', title: '双泳道治理总览' },
  provenance: { icon: '🔎', title: '内生驱动决策溯源' },
  health:     { icon: '💗', title: '身体 / 记忆健康度' },
};

function openDrawer(type) {
  if (!els.drawer || !DRAWER_META[type]) return;
  drawerOpen = type;
  const meta = DRAWER_META[type];
  if (els.drawerTitle) els.drawerTitle.innerHTML = '<span>' + meta.icon + '</span>' + meta.title;
  renderDrawer();
  els.drawer.classList.add('open');
}

function closeDrawer() {
  drawerOpen = null;
  if (els.drawer) els.drawer.classList.remove('open');
}

function renderDrawer() {
  if (!drawerOpen || !els.drawerBody) return;
  const state = lastState || {};
  if (drawerOpen === 'lanes') renderLanesDrawer(state);
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
  openDrawer(trigger.dataset.drill);
});

function drillButton(type, label) {
  return '<span class="drill-link" data-drill="' + type + '">🔬 ' + label + '</span>';
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function pct(v) { return (v != null && !isNaN(v)) ? Math.round(v * 100) + '%' : '—'; }

/* ── 🚦 双泳道治理总览 ── */
function renderLanesDrawer(state) {
  const layout = state.queue_layout || {};
  const win = layout.window || {};
  const timed = Array.isArray(layout.timed_queue) ? layout.timed_queue : [];
  const cands = Array.isArray(layout.candidate_list) ? layout.candidate_list : [];

  // 按 lane 归类定时队列
  function isAgentLane(t) {
    const lane = String((t || {}).lane || '').trim();
    if (lane) return lane === 'agent';
    const gov = String((t || {}).governance_task_type || '').trim();
    const ek = String((t || {}).execution_kind || '').trim();
    return gov === 'self_learning' || ek === 'body_improvement';
  }
  const supQueued = timed.filter(t => !isAgentLane(t));
  const agtQueued = timed.filter(isAgentLane);
  const supCand = cands.filter(c => !isAgentLane(c));
  const agtCand = cands.filter(isAgentLane);

  function activeBlock(task) {
    if (!task) return '<div class="lane-active" style="color:var(--text-muted);">空闲 · 无活跃任务</div>';
    return '<div class="lane-active"><div class="la-title">' + esc(String(task.title || '未命名').substring(0, 48)) +
      '</div><div style="margin-top:3px;">状态: ' + esc(task.display_status || task.status || '—') + '</div></div>';
  }
  function laneCol(cls, icon, name, tag, active, queuedN, candN) {
    return '<div class="lane-col ' + cls + '">' +
      '<div class="lane-col-head">' + icon + ' ' + name + ' <span class="lane-col-tag">' + tag + '</span></div>' +
      '<div class="lane-metric"><span>定时队列</span><b>' + queuedN + '</b></div>' +
      '<div class="lane-metric"><span>待治理候选</span><b>' + candN + '</b></div>' +
      activeBlock(active) + '</div>';
  }

  const winColor = win.open ? 'var(--mint)' : 'var(--gold)';
  let html = '<div class="drawer-sub">监督者本体只处理自身维护(记忆 / 演化)。学习与替身改进委派给 API-A agent 执行。' +
    '执行窗口: <span style="color:' + winColor + ';">' + esc(win.range || '00:00-06:00') + ' · ' + esc(win.status_text || '') + '</span></div>';
  html += '<div class="lane-grid">' +
    laneCol('supervisor', '🧠', '监督者自维护', 'API-B · 记忆/演化',
      layout.supervisor_active, supQueued.length, supCand.length) +
    laneCol('agent', '🤖', 'Agent 执行', 'API-A · 学习/替身改进',
      layout.agent_active, agtQueued.length, agtCand.length) +
    '</div>';
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
  const cands = Array.isArray((state.queue_layout || {}).candidate_list)
    ? state.queue_layout.candidate_list : [];

  if (!Object.keys(p).length && !needs.length) {
    els.drawerBody.innerHTML = '<div class="drawer-sub">认知状态尚未初始化。激活 Governor 模式后内生驱动会填充感知→意图链。</div>';
    return;
  }

  let chain = '<div class="prov-chain">';
  chain += '<div class="prov-node"><div class="prov-node-label">👁 感知 PERCEPTION</div><div class="prov-node-body">' +
    '系统姿态 ' + esc(p.system_posture || '—') + ' · 活跃队列 ' + (p.active_queue_count || 0) +
    ' · 近期错误 ' + (p.recent_errors || 0) + ' · 修正信号 ' + (p.correction_signals || 0) + '</div></div>';
  chain += '<div class="prov-node"><div class="prov-node-label">🌍 世界模型 WORLD MODEL</div><div class="prov-node-body">' +
    '队列健康 ' + esc(wm.queue_health || '—') + ' · 记忆压力 ' + pct(wm.memory_pressure) +
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
      const tags = Array.isArray(c.value_tags) ? c.value_tags.map(esc).join(' · ') : '';
      return '<div class="lane-active" style="margin-top:6px;"><div class="la-title">' + esc(String(c.title || '未命名').substring(0, 52)) + '</div>' +
        (c.rationale ? '<div style="margin-top:3px;">理由: ' + esc(String(c.rationale).substring(0, 120)) + '</div>' : '') +
        (tags ? '<div style="margin-top:3px;color:var(--text-muted);">价值标签: ' + tags + '</div>' : '') + '</div>';
    }).join('');
  }

  els.drawerBody.innerHTML =
    '<div class="drawer-sub">回答"当前为什么这样判断 / 为什么产出(或不产出)这些任务"。链路: 感知 → 世界模型 → 需求 → 意图 → 策略 → 候选。</div>' +
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

/* ── 📋 任务面板 ── */
function renderTasksPanel(state) {
  const body = els.panelTasksBody;
  if (!body) return;
  body.replaceChildren();
  const layout = state.queue_layout || {};

  function addSection(label, task, emptyText) {
    const sec = document.createElement('div');
    sec.style.cssText = 'display:grid;gap:6px;';
    const hdr = document.createElement('div');
    hdr.style.cssText = 'font-size:10px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;padding:0 2px;';
    hdr.textContent = label;
    sec.append(hdr);
    if (!task) {
      const empty = document.createElement('div');
      empty.className = 'game-card rarity-common';
      empty.innerHTML = '<div class="game-card-sub" style="text-align:center;color:var(--text-muted);">' + emptyText + '</div>';
      sec.append(empty);
    } else {
      sec.append(buildGameCard(task));
    }
    body.append(sec);
  }

  addSection('⚡ 监督者执行', layout.supervisor_active || null, '当前没有监督者任务');
  addSection('🎨 Agent 执行', layout.agent_active || null, '当前没有创造类任务');

  // 定时队列
  const timedSec = document.createElement('div');
  timedSec.style.cssText = 'display:grid;gap:6px;';
  const timedHdr = document.createElement('div');
  timedHdr.style.cssText = 'font-size:10px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;padding:0 2px;display:flex;justify-content:space-between;';
  const winInfo = layout.window || {};
  const winColor = winInfo.open ? 'var(--mint)' : 'var(--gold)';
  timedHdr.innerHTML = '⏳ 定时队列 <span style="font-size:9px;color:' + winColor + ';">' + (winInfo.range || '00:00-06:00') + ' · ' + (winInfo.status_text || '') + '</span>';
  timedSec.append(timedHdr);
  const timed = Array.isArray(layout.timed_queue) ? layout.timed_queue : [];
  if (!timed.length) {
    const empty = document.createElement('div');
    empty.className = 'game-card rarity-common';
    empty.innerHTML = '<div class="game-card-sub" style="text-align:center;color:var(--text-muted);">定时队列为空</div>';
    timedSec.append(empty);
  } else {
    timed.slice(0, 8).forEach(t => timedSec.append(buildGameCard(t)));
  }
  body.append(timedSec);

  // 内生驱动候选
  const candSec = document.createElement('div');
  candSec.style.cssText = 'display:grid;gap:6px;';
  const candHdr = document.createElement('div');
  candHdr.style.cssText = 'font-size:10px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;padding:0 2px;';
  candHdr.textContent = '💡 内生驱动候选';
  candSec.append(candHdr);
  const candidates = Array.isArray(layout.candidate_list) ? layout.candidate_list : [];
  if (!candidates.length) {
    const empty = document.createElement('div');
    empty.className = 'game-card rarity-common';
    empty.innerHTML = '<div class="game-card-sub" style="text-align:center;color:var(--text-muted);">当前没有待治理投影</div>';
    candSec.append(empty);
  } else {
    candidates.forEach(c => candSec.append(buildGameCard(c, true)));
  }
  body.append(candSec);
}

function buildGameCard(task, isCandidate) {
  const card = document.createElement('div');
  card.className = 'game-card ' + (isCandidate ? rarityClass(task) : rarityClass(task));

  const head = document.createElement('div');
  head.className = 'game-card-head';
  const title = document.createElement('div');
  title.className = 'game-card-title';
  title.textContent = (task.title || '未命名').substring(0, 64);
  title.title = task.title || '';
  const badge = document.createElement('span');
  const st = task.display_status || task.status || 'queued';
  badge.className = 'game-card-badge ' + (task.status || 'queued');
  badge.textContent = statusLabel(st);
  head.append(title, badge);
  card.append(head);

  // subtitle
  const sub = document.createElement('div');
  sub.className = 'game-card-sub';
  const hints = [];
  if (!isCandidate) {
    const ih = taskIdentityHint(task);
    if (ih) hints.push(ih);
    const gh = governanceHint(task);
    if (gh) hints.push(gh);
  }
  if (task.summary) hints.push(String(task.summary).substring(0, 100));
  if (!hints.length) hints.push(isCandidate ? '等待监督者治理' : typeLabel(task));
  sub.textContent = hints.join(' · ').substring(0, 160);
  card.append(sub);

  // meta row
  const meta = document.createElement('div');
  meta.className = 'game-card-meta';

  const tags = document.createElement('div');
  tags.className = 'game-card-tags';
  const lane = taskLane(task);
  const laneTag = document.createElement('span');
  laneTag.className = 'game-card-tag ' + (lane === 'agent' ? 'creativity' : 'memory');
  laneTag.textContent = lane === 'agent' ? 'API-A 创造' : '监督者治理';
  tags.append(laneTag);
  if (task.governance_task_type) {
    const typeTag = document.createElement('span');
    typeTag.className = 'game-card-tag ' + tagClass(task.governance_task_type);
    typeTag.textContent = typeLabel(task);
    tags.append(typeTag);
  }
  if (isCandidate && Array.isArray(task.value_tags)) {
    task.value_tags.forEach(vt => {
      const vtTag = document.createElement('span');
      vtTag.className = 'game-card-tag ' + tagClass(vt);
      vtTag.textContent = vt;
      tags.append(vtTag);
    });
  }
  meta.append(tags);

  // utility score bar
  if (task.utility != null) {
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
  if (lm.last_call_at || lm.prompt_estimate) {
    const callSec = document.createElement('div');
    callSec.className = 'lm-section';
    callSec.innerHTML = '<div class="lm-section-label">🧠 最近 LM 调用</div>';
    const info = [
      {icon:'🕐', label:'最近调用', value: lm.last_call_at ? new Date(lm.last_call_at).toLocaleTimeString() : '—'},
      {icon:'📝', label:'Prompt 预估', value: lm.prompt_estimate ? (lm.prompt_estimate + ' 字符') : '—'},
      {icon:'🔗', label:'证据节点', value: (lm.evidence_node_count != null) ? lm.evidence_node_count : '—'},
      {icon:'🎯', label:'任务提案', value: (lm.proposal_count != null) ? lm.proposal_count : '—'},
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
      const nodeLabel = typeof ev === 'object' && ev.node ? String(ev.node) : 'evidence';
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
  if (!mem.total_tokens && !lm.last_call_at && !evNodes.length) {
    body.replaceChildren();
    const empty = document.createElement('div');
    empty.className = 'panel-empty';
    empty.innerHTML = '<div class="pe-icon">🧠</div><div class="pe-text">LM 尚未产生调用记录</div><div style="font-size:10px;color:var(--text-muted);">激活 Governor 模式并启用 LM 任务生成后会出现数据</div>';
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
    '姿势: ' + (perception.system_posture || '—') + ' · 队列: ' + (perception.active_queue_count || 0) + ' · 错误: ' + (perception.recent_errors || 0) +
    '</div><div class="cog-step-detail">' +
    '学习质量: ' + (perception.learning_quality != null ? Math.round(perception.learning_quality) + '%' : '—') +
    ' · 修正信号: ' + (perception.correction_signals || 0) +
    ' · 闲置: ' + ((perception.idle_seconds || {}).user_idle || '—') + 's' +
    '</div></div>';
  flow.append(percStep);

  // World Model
  const wmStep = document.createElement('div');
  wmStep.className = 'cog-step';
  wmStep.innerHTML = '<div class="cog-step-label">🌍 世界模型</div><div class="cog-step-content"><div class="cog-step-title">' +
    '队列健康: ' + (worldModel.queue_health || '—') + ' · 记忆压力: ' + (worldModel.memory_pressure != null ? Math.round(worldModel.memory_pressure * 100) + '%' : '—') +
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

/* ── ⚙️ 队列面板 ── */
function renderQueuePanel(state) {
  const body = els.panelQueueBody;
  if (!body) return;
  body.replaceChildren();

  const layout = state.queue_layout || {};
  const m = state.metrics || {};

  const drill = document.createElement('div');
  drill.style.cssText = 'display:flex;justify-content:flex-end;margin-bottom:6px;';
  drill.innerHTML = drillButton('lanes', '双泳道总览');
  body.append(drill);

  // 统计摘要
  const summary = document.createElement('div');
  summary.style.cssText = 'display:flex;gap:12px;flex-wrap:wrap;padding:4px 0;margin-bottom:8px;';
  [
    {label:'总数', value: m.queue_total || 0, color:'var(--text-primary)'},
    {label:'学习中', value: m.learning_total || 0, color:'var(--mint)'},
    {label:'维护中', value: m.maintenance_total || 0, color:'var(--gold)'},
    {label:'运行中', value: m.running_count || 0, color:'var(--accent-blue)'},
    {label:'候选', value: m.drive_candidates || 0, color:'var(--plum)'},
    {label:'错误', value: m.error_count || 0, color:(m.error_count||0) > 0 ? 'var(--coral)' : 'var(--text-muted)'},
  ].forEach(s => {
    const chip = document.createElement('div');
    chip.style.cssText = 'text-align:center;padding:6px 10px;border-radius:8px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.05);min-width:50px;';
    chip.innerHTML = '<div style="font-size:18px;font-weight:700;color:' + s.color + ';">' + s.value + '</div><div style="font-size:9px;color:var(--text-muted);">' + s.label + '</div>';
    summary.append(chip);
  });
  body.append(summary);

  // 定时队列
  const timedSec = document.createElement('div');
  timedSec.style.cssText = 'display:grid;gap:6px;';
  timedSec.innerHTML = '<div class="lm-section-label">⏳ 定时队列</div>';
  const timed = Array.isArray(layout.timed_queue) ? layout.timed_queue : [];
  if (!timed.length) {
    timedSec.innerHTML += '<div class="game-card-sub" style="text-align:center;color:var(--text-muted);padding:12px;">队列为空</div>';
  } else {
    timed.forEach(t => timedSec.append(buildGameCard(t)));
  }
  body.append(timedSec);

  // 全部候选
  const candidates = Array.isArray(layout.candidate_list) ? layout.candidate_list : [];
  if (candidates.length) {
    const candSec = document.createElement('div');
    candSec.style.cssText = 'display:grid;gap:6px;';
    candSec.innerHTML = '<div class="lm-section-label">💡 内生驱动候选 (' + candidates.length + ')</div>';
    candidates.forEach(c => candSec.append(buildGameCard(c, true)));
    body.append(candSec);
  }
}

/* ── 📈 统计面板 ── */
function renderStatsPanel(state) {
  const body = els.panelStatsBody;
  if (!body) return;
  body.replaceChildren();

  const bs = state.body_status || {};
  const ts = state.tier1_stats || {};
  const mem = state.mem_usage || {};
  const gov = state.governor_mode || {};

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
  const govSec = document.createElement('div');
  govSec.className = 'lm-section';
  govSec.innerHTML = '<div class="lm-section-label">⚙️ 治理状态</div>';
  [
    {icon:'🔮', label:'Governor', value: gov.active ? '✅ 已激活' : '⏸ 未激活'},
    {icon:'🪟', label:'执行窗口', value: state.in_execution_window ? '✅ 开启' : '🌙 关闭'},
    {icon:'👥', label:'活跃会话', value: state.active_sessions || 0},
    {icon:'📡', label:'驱动可用', value: state.drive_available ? '✅' : '⚠️ 不可用'},
    {icon:'📋', label:'活跃执行', value: (state.active_executions || []).length || 0},
  ].forEach(s => {
    const row = document.createElement('div');
    row.className = 'lm-stat-row';
    row.innerHTML = '<span class="lm-stat-icon">' + s.icon + '</span><span class="lm-stat-label">' + s.label + '</span><span class="lm-stat-value">' + s.value + '</span>';
    govSec.append(row);
  });
  body.append(govSec);
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
  els.body.dataset.execWindow = (state.in_execution_window !== false) ? 'true' : 'false';

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
  if (panelOpen === 'tasks') renderTasksPanel(state);
  if (panelOpen === 'lminput') renderLMInputPanel(state);
  if (panelOpen === 'cognition') renderCognitionPanel(state);
  if (panelOpen === 'queue') renderQueuePanel(state);
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

    async def get_supervisor_ui_state(self) -> Dict[str, Any]:
        all_serialized_tasks = [
            self._serialize_self_evolution_task(task)
            for task in self._self_evolution_queue.list_tasks()
        ]
        all_serialized_tasks.sort(
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )

        visible_tasks = [
            self._serialize_self_evolution_task(task)
            for task in self._self_evolution_queue.list_tasks()
            if task.status in {"planned", "deferred", "paused", "approved", "running"}
        ]
        visible_tasks.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)

        # ── Grouped task panels by execution path ──
        panels = self._build_task_panels(visible_tasks)

        drive_candidates: List[Dict[str, Any]] = self._latest_drive_candidate_snapshot()
        drive_available = True
        idle_snapshot: Dict[str, Any] = {}
        try:
            idle_snapshot = await self.evaluate_idle_window({})
        except Exception:
            drive_available = False
        if (
            drive_available
            and not drive_candidates
            and not bool(getattr(getattr(self, "_service_runtime", None), "suppress_candidate_refresh", False))
        ):
            try:
                evaluation = await self.evaluate_endogenous_drive(
                    {"record_activity": False}
                )
                fallback_candidates = evaluation.get("candidates") if isinstance(evaluation, dict) else None
                if isinstance(fallback_candidates, list):
                    drive_candidates = [
                        dict(item) for item in fallback_candidates if isinstance(item, dict)
                    ]
            except Exception:
                pass

        # Extract metrics from gateway activity for richer UI expression
        activity = dict(idle_snapshot.get("activity") or {})
        counts = dict(activity.get("counts") or {})
        checks = dict(idle_snapshot.get("checks") or {})
        error_count = int(counts.get("error_count") or 0)
        in_execution_window = bool(checks.get("in_execution_window", True))

        # ── Body status (direct from registry snapshot, not task queue) ──
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

        # ── Tier 1 short-term memory stats ──
        tier1_stats: Dict[str, Any] = {}
        try:
            tier1_stats = await self._fetch_tier1_stats()
        except Exception:
            pass

        # ── Metrics panel (upgraded with per-path stats) ──
        queue_layout = self._build_governance_queue_layout(
            visible_tasks,
            drive_candidates=drive_candidates,
            in_execution_window=in_execution_window,
        )
        metrics = self._build_ui_metrics(
            all_serialized_tasks,
            visible_tasks=visible_tasks,
            queue_layout=queue_layout,
            body_status=body_status,
            error_count=error_count,
        )

        scene, title, summary = self._map_supervisor_scene(
            panels=panels,
            all_tasks=visible_tasks,
            drive_candidates=drive_candidates,
            drive_available=drive_available,
            error_count=error_count,
            in_execution_window=in_execution_window,
            memory_active=tier1_stats.get("memory_active", False),
        )

        # ── LM Input info (for 🧠 panel) ──
        lm_input: Dict[str, Any] = {
            "generation_enabled": bool(
                getattr(self.config, "endogenous_drive_lm_task_generation_enabled", False)
            ),
        }
        # Extract recent LM call metadata from drive history / cognition state
        try:
            cog_snapshot = self._load_endogenous_cognition_state()
            proposal_cog = cog_snapshot.get("proposal_cognition") or {}
            lm_state = proposal_cog.get("lm_reasoning_state") or {}
            if lm_state.get("last_call_at"):
                lm_input["last_call_at"] = lm_state["last_call_at"]
            if lm_state.get("prompt_chars"):
                lm_input["prompt_estimate"] = lm_state["prompt_chars"]
            if lm_state.get("evidence_node_count"):
                lm_input["evidence_node_count"] = lm_state["evidence_node_count"]
            if lm_state.get("proposal_count") is not None:
                lm_input["proposal_count"] = lm_state["proposal_count"]
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
            cog_snapshot = self._load_endogenous_cognition_state()
            perception = cog_snapshot.get("perception") or {}
            world_model = cog_snapshot.get("world_model") or {}
            # Build perception summary
            cognition["perception"] = {
                "system_posture": perception.get("system_posture", "balanced"),
                "active_queue_count": perception.get("active_queue_count", 0),
                "recent_errors": perception.get("recent_errors", 0),
                "learning_quality": perception.get("learning_quality", 0),
                "correction_signals": perception.get("correction_signals", 0),
                "idle_seconds": perception.get("idle_seconds", {}),
            }
            # Build world model summary
            cognition["world_model"] = {
                "queue_health": world_model.get("queue_health", "unknown"),
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
                "queue_hygiene_bias": raw_policy.get("queue_hygiene_bias", 0),
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

        return {
            "status": "ok",
            "scene": scene,
            "title": title,
            "summary": summary,
            "generated_at": datetime.utcnow().isoformat(),
            "panels": panels,
            "tasks": visible_tasks[:12],
            "queue_layout": queue_layout,
            "schedule": schedule,
            "metrics": metrics,
            "mem_usage": mem_usage,
            "tier1_stats": tier1_stats,
            "body_status": body_status,
            "drive_candidates": drive_candidates,
            "drive_available": drive_available,
            "error_count": error_count,
            "in_execution_window": in_execution_window,
            "active_sessions": int(activity.get("active_sessions") or 0),
            "timeline": await self._recent_supervisor_observation_timeline(limit=10),
            "lm_input": lm_input,
            "cognition": cognition,
            "governor_mode": self._governor_mode_status(),
            "active_executions": [
                self._serialize_self_evolution_task(task)
                for task in self._self_evolution_queue.list_tasks()
                if task.status == "running"
                and not task.metadata.get("execution_failed")
            ],
        }

    def _is_creativity_ui_task(self, task: Dict[str, Any]) -> bool:
        governance = str(task.get("governance_task_type") or "").strip().lower()
        execution_kind = str(task.get("execution_kind") or "").strip().lower()
        return governance == "self_learning" or execution_kind == "body_improvement"

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

    def _queue_fifo_sort_key(self, task: Dict[str, Any]) -> tuple[str, str, str]:
        scheduled_for = self._queue_schedule_token(task) or "9999-12-31T23:59:59"
        created = str(task.get("created_at") or "")
        updated = str(task.get("updated_at") or "")
        return (scheduled_for, created or updated, updated or created)

    def _queue_display_status(self, task: Dict[str, Any]) -> str:
        status = str(task.get("status") or "").strip().lower()
        mapping = {
            "planned": "待审核",
            "approved": "待执行",
            "running": "执行中",
            "deferred": "已推迟",
            "paused": "已暂停",
        }
        return mapping.get(status, status or "待定")

    def _timed_queue_display_status(self, task: Dict[str, Any]) -> str:
        status = str(task.get("status") or "").strip().lower()
        if status == "paused":
            return "已挂起"
        if status == "deferred":
            return "已顺延"
        return "预设时间"

    def _queue_role_tag(self, task: Dict[str, Any]) -> str:
        return "agent" if self._is_creativity_ui_task(task) else "supervisor"

    def _queue_schedule_token(self, payload: Dict[str, Any]) -> str:
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

    def _build_governance_queue_layout(
        self,
        all_tasks: List[Dict[str, Any]],
        *,
        drive_candidates: List[Dict[str, Any]],
        in_execution_window: bool,
    ) -> Dict[str, Any]:
        creativity_tasks = [task for task in all_tasks if self._is_creativity_ui_task(task)]
        supervisor_tasks = [task for task in all_tasks if not self._is_creativity_ui_task(task)]

        creativity_sorted = sorted(creativity_tasks, key=self._queue_fifo_sort_key)
        supervisor_sorted = sorted(supervisor_tasks, key=self._queue_fifo_sort_key)

        def pick_active(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            running = [
                row for row in rows
                if str(row.get("status") or "").strip().lower() == "running"
            ]
            if running:
                return sorted(running, key=self._queue_fifo_sort_key)[0]
            approved = [
                row for row in rows
                if str(row.get("status") or "").strip().lower() == "approved"
            ]
            if approved:
                return sorted(approved, key=self._queue_fifo_sort_key)[0]
            return None

        supervisor_active = pick_active(supervisor_sorted)
        agent_active = pick_active(creativity_sorted)

        active_ids = {
            task.get("task_id")
            for task in (supervisor_active, agent_active)
            if isinstance(task, dict) and task.get("task_id")
        }
        timed_queue = [
            {
                **task,
                "lane": self._queue_role_tag(task),
                "display_status": self._timed_queue_display_status(task),
            }
            for task in sorted(all_tasks, key=self._queue_fifo_sort_key)
            if task.get("task_id") not in active_ids
        ]

        seen_keys = {
            str(task.get("metadata", {}).get("endogenous_drive_key") or "").strip()
            for task in timed_queue
            if isinstance(task, dict)
        }
        seen_titles = {
            str(task.get("title") or "").strip()
            for task in timed_queue
            if isinstance(task, dict)
        }
        seen_schedule_tokens = {
            self._queue_schedule_token(task)
            for task in timed_queue
            if isinstance(task, dict)
        }
        seen_schedule_tokens.discard("")
        candidate_list: List[Dict[str, Any]] = []
        for candidate in drive_candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_key = str(
                candidate.get("metadata", {}).get("endogenous_drive_key")
                or candidate.get("stable_key")
                or ""
            ).strip()
            candidate_title = str(candidate.get("title") or "").strip()
            candidate_schedule = self._queue_schedule_token(candidate)
            if candidate_key and candidate_key in seen_keys:
                continue
            if candidate_title and candidate_title in seen_titles:
                continue
            if candidate_schedule and candidate_schedule in seen_schedule_tokens:
                continue
            candidate_list.append(
                {
                    **candidate,
                    "display_status": "等待监督者治理",
                }
            )
            if candidate_key:
                seen_keys.add(candidate_key)
            if candidate_title:
                seen_titles.add(candidate_title)
            if candidate_schedule:
                seen_schedule_tokens.add(candidate_schedule)

        return {
            "window": {
                "label": "预设时间",
                "range": "00:00-06:00",
                "open": bool(in_execution_window),
                "status_text": (
                    "限时自动执行中"
                    if in_execution_window
                    else "等待窗口或手动触发"
                ),
            },
            "supervisor_active": (
                {
                    **supervisor_active,
                    "lane": "supervisor",
                    "display_status": self._queue_display_status(supervisor_active),
                }
                if supervisor_active
                else None
            ),
            "agent_active": (
                {
                    **agent_active,
                    "lane": "agent",
                    "display_status": self._queue_display_status(agent_active),
                }
                if agent_active
                else None
            ),
            "timed_queue": timed_queue,
            "candidate_list": candidate_list,
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
            "learning": {"label": "学习", "count": len(learning), "tasks": learning},
            "maintenance": {"label": "维护", "count": len(maintenance), "tasks": maintenance},
            "evolution": {"label": "进化", "count": len(evolution), "tasks": evolution},
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
                        return {}
                    services = (await resp.json()).get("services", {})
                memory_url = None
                for svc in services.values():
                    if svc.get("service_type") == "memory":
                        memory_url = svc.get("address")
                        break
                if not memory_url:
                    return {}
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
                # Compute memory_active: any rule ran in the last 2 cycles
                from datetime import datetime, timedelta, timezone
                recent = datetime.now(timezone.utc) - timedelta(seconds=7200)
                memory_active = False
                for rule_name, rule_info in result.get("rules", {}).items():
                    last_run = rule_info.get("last_run")
                    if last_run:
                        try:
                            t = datetime.fromisoformat(last_run)
                            if t.tzinfo is None:
                                t = t.replace(tzinfo=timezone.utc)
                            if t > recent:
                                memory_active = True
                                break
                        except Exception:
                            pass
                result["memory_active"] = memory_active
                return result
        except Exception:
            pass
        return {}

    def _build_ui_metrics(
        self,
        all_tasks: List[Dict[str, Any]],
        *,
        visible_tasks: List[Dict[str, Any]],
        queue_layout: Dict[str, Any],
        body_status: Dict[str, Any],
        error_count: int,
    ) -> Dict[str, Any]:
        """Build top-card metrics for the 5-section governance layout."""
        queue_total = len(all_tasks)
        learning_total = sum(1 for t in all_tasks if self._is_creativity_ui_task(t))
        maintenance_total = sum(1 for t in all_tasks if not self._is_creativity_ui_task(t))
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
            lm_shadow = preview.get("lm_queue_shadow")
            if isinstance(lm_shadow, dict):
                shadow_recommendations += 1
                action = str(lm_shadow.get("action") or "unknown")
                shadow_actions[action] = shadow_actions.get(action, 0) + 1
            lm_review = preview.get("lm_queue_review")
            if isinstance(lm_review, dict):
                direct_lm_actions += 1
            if isinstance(preview.get("lm_queue_priority"), dict):
                priority_updates += 1

        return {
            "queue_total": queue_total,
            "learning_total": learning_total,
            "maintenance_total": maintenance_total,
            "evolution_total": evolution_total,
            "by_path": {
                "learning": learning_total,
                "maintenance": maintenance_total,
                "evolution": evolution_total,
            },
            "learning_results": {
                "completed": learning_completed,
                "failed": learning_failed,
            },
            "drive_candidates": len(queue_layout.get("candidate_list") or []),
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
        panels: Dict[str, Any],
        all_tasks: List[Dict[str, Any]],
        drive_candidates: List[Dict[str, Any]],
        drive_available: bool,
        error_count: int = 0,
        in_execution_window: bool = True,
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
            # internal memory service, not by Agent pull).
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
                f"「{rtitle}」已派发，代理或执行器正在运行，监督者等待结果。",
            )

        # 2. Supervisor-governed tasks that are ready now.
        supervisor_pending = [
            t
            for t in all_tasks
            if not self._is_creativity_ui_task(t)
            and str(t.get("status") or "").strip().lower() == "approved"
        ]
        if supervisor_pending:
            lp = sorted(supervisor_pending, key=self._queue_fifo_sort_key)[0]
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
            value_tags = ", ".join(first.get("value_tags") or [])
            utility_pct = int((first.get("utility") or 0) * 100)
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
        window_mood = "执行窗口已开启，系统处于安静状态。" if in_execution_window else "执行窗口外，系统正在休息。"
        return (
            "idle",
            f"在窗边休息{error_note}",
            f"没有待处理的工作。{window_mood}核心价值保持警觉但平静。",
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
