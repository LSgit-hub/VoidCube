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
<title>VoidCube · 义子的小屋</title>
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

/* 在写字桌前书写 */
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
  text-align: center; margin-bottom: 12px; padding: 6px 10px;
  border-radius: 8px;
  background: rgba(255,255,255,.04);
}
.schedule-label { font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: .04em; margin-bottom: 2px; }
.schedule-countdown {
  font-size: 16px; font-weight: 700; color: var(--accent-blue);
  font-variant-numeric: tabular-nums;
}
.panels { display: grid; gap: 10px; margin-bottom: 12px; }
.panel-head {
  font-size: 12px; font-weight: 600;
  color: var(--text-secondary);
  padding: 4px 0; margin-bottom: 4px;
  border-bottom: 1px solid rgba(255,255,255,.08);
  cursor: pointer; user-select: none;
  display: flex; align-items: center; gap: 6px;
}
.panel-head::before { content: "\25B8"; font-size: 10px; transition: transform .2s; display: inline-block; }
.panel.open .panel-head::before { transform: rotate(90deg); }
.panel .panel-body { display: none; }
.panel.open .panel-body { display: block; }
.panel.learning .panel-head  { color: var(--mint); }
.panel.maintenance .panel-head { color: var(--gold); }
.panel.evolution .panel-head { color: var(--coral); }

.candidates { margin-bottom: 12px; display: grid; gap: 5px; }
.candidates-label {
  font-size: 11px; color: var(--text-muted);
  letter-spacing: .04em; margin-bottom: 2px;
}
.candidate {
  display: grid; grid-template-columns: 1fr auto auto; gap: 8px;
  align-items: center; min-height: 28px; padding: 4px 8px;
  border-radius: 6px;
  background: rgba(226,176,74,.08);
  font-size: 11px;
}
.candidate-tags { font-size: 10px; color: var(--text-muted); }
.candidate-utility { font-size: 10px; font-weight: 600; color: var(--gold); }

.body-status { margin-bottom: 10px; }
.body-info {
  font-size: 11px; padding: 4px 8px; border-radius: 6px;
  background: rgba(255,255,255,.05);
  color: var(--text-secondary);
}

.executions { margin-bottom: 12px; }
.exec-label {
  font-size: 11px; color: var(--text-muted);
  letter-spacing: .04em; margin-bottom: 2px;
}
.exec-item {
  display: grid; grid-template-columns: 10px 1fr auto;
  align-items: center; gap: 8px;
  min-height: 26px; padding: 4px 8px;
  border-radius: 6px;
  background: rgba(255,255,255,.04);
  font-size: 11px;
}
.exec-type {
  font-size: 10px; color: var(--text-muted);
  letter-spacing: .02em;
}

.queue { display: grid; gap: 7px; margin-bottom: 12px; }
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

.timeline { display: grid; gap: 5px; max-height: 120px; overflow-y: auto; }
.event {
  display: grid; grid-template-columns: 18px 58px 1fr;
  gap: 7px; align-items: start;
  min-height: 26px; padding: 5px 8px;
  border-left: 3px solid rgba(106,158,232,.4);
  background: rgba(255,255,255,.03);
  border-radius: 5px;
  font-size: 10.5px;
  animation: event-in .35s ease;
}
@keyframes event-in {
  from { opacity: 0; transform: translateX(6px); }
  to   { opacity: 1; transform: translateX(0); }
}
.event-icon { font-size: 12px; text-align: center; line-height: 1.4; }
.event-time { color: var(--text-muted); white-space: nowrap; font-variant-numeric: tabular-nums; font-size: 10px; }
.event-text { color: var(--text-primary); line-height: 1.4; }

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

    <!-- 角色: 义子 -->
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

    <!-- 环境粒子 -->
    <div class="particles" id="particles" aria-hidden="true"></div>

    <!-- 任务卡片(保留) -->
    <div class="char-card" id="charCard">
      <button class="char-toggle" id="charToggle" aria-label="收起任务卡" title="收起/展开">−</button>
      <div class="char-avatar">
        <div class="av-hair"></div>
        <div class="av-head"></div>
        <div class="av-eyes"><span class="av-eye"></span><span class="av-eye"></span></div>
        <div class="av-mouth"></div>
        <div class="av-body"></div>
      </div>
      <div class="char-info">
        <div class="char-name">义子 <span class="ch-title" id="chTitle">初始替身</span></div>
        <div class="char-lv">等级 <span class="lv-val" id="chLevel">1</span></div>
        <div class="char-exp-wrap"><div class="char-exp-fill" id="chExpBar"></div></div>
        <div class="char-exp-text"><span id="chExpText">0 次替身切换</span></div>
        <div class="char-health"><span class="ch-hp" id="chHP">❤️ 100%</span><span id="chMood">✨ 完美</span></div>
      </div>
    </div>

    <!-- 状态面板开关 -->
    <button class="status-toggle" id="statusToggle" aria-label="收起面板" title="收起/展开">−</button>

    <!-- 状态面板(右上方) -->
    <aside class="status" id="statusPanel" aria-live="polite">
      <h1 id="sceneTitle">义子的小屋</h1>
      <p class="status-summary" id="sceneSummary">正在连接监督者…</p>
      <div class="metrics" id="metrics"></div>
      <div class="schedule" id="schedule" style="display:none;">
        <div class="schedule-label">⏳ 下次自动循环</div>
        <div class="schedule-countdown" id="countdown">—</div>
      </div>
      <div class="panels" id="panels"></div>
      <div class="candidates" id="candidates" style="display:none;">
        <div class="candidates-label">💡 内生驱动候选</div>
        <div id="candidateList"></div>
      </div>
      <div class="executions" id="executions" style="display:none;">
        <div class="exec-label">⚡ 执行中</div>
        <div id="execList"></div>
      </div>
      <div class="body-status" id="bodyStatus"></div>
      <div class="timeline" id="timeline"></div>
    </aside>

    <!-- 动作切换器(底部) -->
    <div class="action-bar" id="actionBar">
      <button class="action-btn" data-action="organize"><span class="ico">📚</span>整理</button>
      <button class="action-btn" data-action="rest"><span class="ico">🛋</span>休息</button>
      <button class="action-btn" data-action="work"><span class="ico">💻</span>工作</button>
      <button class="action-btn" data-action="write"><span class="ico">✍️</span>书写</button>
    </div>

  </main>
</div>
<script>
/*━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  VoidCube 监督者小屋  v4  ——  运行时
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

/* ── DOM ── */
const els = {
  body: document.body,
  title: $('#sceneTitle'),
  summary: $('#sceneSummary'),
  glyph: $('#glyph'),
  panels: $('#panels'),
  candidates: $('#candidates'),
  candidateList: $('#candidateList'),
  executions: $('#executions'),
  execList: $('#execList'),
  bodyStatus: $('#bodyStatus'),
  timeline: $('#timeline'),
  metrics: $('#metrics'),
  particles: $('#particles'),
  actionBar: $('#actionBar'),
  wcHour: $('#wcHour'),
};

/* ── 场景 → 动作自动映射 ── */
const SCENE_TO_ACTION = {
  idle: 'rest',
  planning: 'work',
  drive: 'organize',
  memory: 'organize',
  maintenance: 'organize',
  dispatch: 'write',
};
const GLYPHS = {
  idle: '·', planning: '!', drive: '✦', memory: 'λ',
  maintenance: '¶', dispatch: '⟩',
};
const EVENT_ICONS = {
  endogenous_drive_evaluated: '🧠',
  endogenous_drive_planned: '💡',
  endogenous_drive_idle: '😴',
  task_planned: '📋',
  task_decided: '⚖️',
  tasks_reviewed: '🔍',
  tasks_planned: '📝',
  execution_dispatched: '🚀',
  self_learning_submitted: '📖',
  self_learning_completed: '✅',
  memory_compression: '💾',
  task_decision: '⚖️',
};
const eventIcon = t => EVENT_ICONS[t] || '●';

function taskDotClass(t) {
  const f = String(t.task_family || t.governance_task_type || '');
  if (f.includes('memory'))    return 'memory';
  if (f.includes('learning'))  return 'learning';
  if (f.includes('evolution') || f.includes('body')) return 'evolution';
  return 'planning';
}

function governanceHint(t) {
  const preview = t.governance_preview || {};
  const direct = preview.lm_queue_review || null;
  const shadow = preview.lm_queue_shadow || null;
  if (direct && direct.action) {
    return '监督者已裁定: ' + String(direct.action) + ' · ' + String(direct.reason || '').slice(0, 80);
  }
  if (shadow && shadow.action) {
    let extra = '';
    if (shadow.merge_into) extra = ' -> ' + String(shadow.merge_into).slice(0, 16);
    else if (shadow.priority) extra = ' -> ' + String(shadow.priority);
    return '监督者建议: ' + String(shadow.action) + extra + ' · ' + String(shadow.reason || '').slice(0, 80);
  }
  return '';
}

/* ── 任务卡片渲染 ── */
function renderCharCard(state) {
  const bs = state.body_status || {};
  const last = bs.last_switch_result || {};
  const switchCount = (typeof last.switch_count === 'number') ? last.switch_count : 0;
  const lv = Math.max(1, switchCount + 1);
  const progress = switchCount > 0 ? Math.round((switchCount % 1 || 0.5) * 100) : 0;
  $('#chLevel').textContent = lv;
  $('#chExpBar').style.width = progress + '%';
  $('#chExpText').textContent = switchCount + ' 次替身切换';
  const titles = ['初始替身', '觉醒替身', '熟练替身', '精英替身', '传奇替身', '虚空替身', '不朽替身'];
  const ti = Math.min(Math.floor((lv - 1) / 3), titles.length - 1);
  $('#chTitle').textContent = titles[ti];
  const errors = state.error_count || 0;
  const hp = Math.max(0, 100 - errors * 10);
  const hpEl = $('#chHP');
  hpEl.textContent = '❤️ ' + hp + '%';
  hpEl.className = 'ch-hp' + (hp < 30 ? ' danger' : hp < 60 ? ' warn' : '');
  const moods = [
    {min: 0,  label: '疲惫',  emoji: '😣'},
    {min: 30, label: '低落',  emoji: '😕'},
    {min: 50, label: '普通',  emoji: '🙂'},
    {min: 70, label: '愉快',  emoji: '😄'},
    {min: 90, label: '完美',  emoji: '✨'},
  ];
  let mood = moods[0];
  for (let i = moods.length - 1; i >= 0; i--) { if (hp >= moods[i].min) { mood = moods[i]; break; } }
  $('#chMood').textContent = mood.emoji + ' ' + mood.label;
}

/* ── 任务面板渲染 ── */
function renderPanels(panels) {
  els.panels.replaceChildren();
  if (!panels) return;
  const labelMap = {
    planned: '待执行', deferred: '等待中', paused: '暂停',
    approved: '待执行', running: '执行中', completed: '完成',
    failed: '失败', cancelled: '取消',
  };
  ['learning', 'maintenance', 'evolution'].forEach(key => {
    const panel = panels[key];
    if (!panel) return;
    const active = (panel.tasks || []).filter(t =>
      t.status !== 'completed' && t.status !== 'failed' && t.status !== 'cancelled');
    if (!active.length && !panel.count) return;
    const total = panel.count || (panel.tasks || []).length;
    const sec = document.createElement('div');
    sec.className = 'panel ' + key + ' open';
    const head = document.createElement('div');
    head.className = 'panel-head';
    head.textContent = panel.label + ' (' + active.length +
      (active.length !== total ? '/' + total : '') + ')';
    head.onclick = () => sec.classList.toggle('open');
    sec.append(head);
    const body = document.createElement('div');
    body.className = 'panel-body';
    active.slice(0, 8).forEach(t => {
      const row = document.createElement('div');
      row.className = 'task';
      if (t.status === 'completed' || t.status === 'failed') row.classList.add('completed');
      const dot = document.createElement('span');
      dot.className = 'task-dot ' + taskDotClass(t);
      const text = document.createElement('div');
      text.className = 'task-text';
      const title = document.createElement('span');
      title.className = 'task-title';
      title.textContent = (t.title || '未命名').substring(0, 48);
      text.append(title);
      const hint = governanceHint(t);
      if (hint) {
        const gov = document.createElement('span');
        gov.className = 'task-gov';
        gov.textContent = hint;
        text.append(gov);
      }
      const badge = document.createElement('span');
      badge.className = 'task-badge ' + (t.status || 'queued');
      badge.textContent = labelMap[t.status] || t.status || 'queued';
      row.append(dot, text, badge);
      body.append(row);
    });
    sec.append(body);
    els.panels.append(sec);
  });
}

/* ── 候选 ── */
function renderCandidates(c) {
  if (!c || !c.length) { els.candidates.style.display = 'none'; return; }
  els.candidates.style.display = 'block';
  els.candidateList.replaceChildren();
  c.slice(0, 4).forEach(x => {
    const row = document.createElement('div');
    row.className = 'candidate';
    const t = document.createElement('span');
    t.textContent = (x.title || '候选').substring(0, 40);
    const tags = document.createElement('span');
    tags.className = 'candidate-tags';
    tags.textContent = (x.value_tags || []).join(', ');
    const u = document.createElement('span');
    u.className = 'candidate-utility';
    u.textContent = Math.round((x.utility || 0) * 100) + '%';
    row.append(t, tags, u);
    els.candidateList.append(row);
  });
}

/* ── 身体状态 ── */
function renderBodyStatus(s) {
  els.bodyStatus.replaceChildren();
  if (!s || !s.active_slot) return;
  const row = document.createElement('div');
  row.className = 'body-info';
  const label = document.createElement('span');
  label.textContent = '🖥 替身: ' + s.active_slot;
  if (s.candidate_slot) {
    label.textContent += ' → 候选 ' + s.candidate_slot;
    row.style.color = 'var(--coral)';
  }
  els.bodyStatus.append(row);
}

/* ── 时间线 ── */
function renderTimeline(events) {
  els.timeline.replaceChildren();
  (events || []).slice(0, 6).forEach(ev => {
    const row = document.createElement('div');
    row.className = 'event';
    const ic = document.createElement('span');
    ic.className = 'event-icon';
    ic.textContent = eventIcon(ev.event_type || '');
    const tm = document.createElement('span');
    tm.className = 'event-time';
    const d = ev.recorded_at ? new Date(ev.recorded_at) : null;
    tm.textContent = (d && !isNaN(d.getTime()))
      ? d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'})
      : '--:--:--';
    const tx = document.createElement('span');
    tx.className = 'event-text';
    tx.textContent = ev.summary || ev.event_type || '活动';
    row.append(ic, tm, tx);
    els.timeline.append(row);
  });
}

/* ── 执行中 ── */
function renderExecutions(tasks) {
  els.execList.replaceChildren();
  if (!tasks || !tasks.length) { els.executions.style.display = 'none'; return; }
  els.executions.style.display = 'block';
  tasks.slice(0, 3).forEach(t => {
    const row = document.createElement('div');
    row.className = 'exec-item';
    const dot = document.createElement('span');
    dot.className = 'task-dot ' + taskDotClass(t);
    const ti = document.createElement('span');
    ti.textContent = (t.title || '未命名').substring(0, 40);
    const ty = document.createElement('span');
    ty.className = 'exec-type';
    const typeMap = {
      'self_learning': '自我学习', 'learning': '学习',
      'memory_compression': '记忆压缩', 'memory': '记忆',
      'body_switch': '替身切换', 'evolution': '进化',
      'planning': '规划', 'task_decision': '任务决策',
      'maintenance': '维护', 'drive': '驱动',
    };
    const rawType = (t.governance_task_type || t.task_family || '').replace(/_/g, ' ');
    ty.textContent = typeMap[t.governance_task_type || t.task_family || ''] || rawType;
    row.append(dot, ti, ty);
    els.execList.append(row);
  });
}

/* ── 指标 ── */
function renderMetrics(state) {
  els.metrics.replaceChildren();
  const m = state.metrics || {};
  const by = m.by_path || {};
  const lr = m.learning_results || {};
  function add(cls, v, l) {
    const d = document.createElement('div');
    d.className = 'metric ' + cls;
    const a = document.createElement('div');
    a.className = 'metric-value';
    a.textContent = v;
    const b = document.createElement('div');
    b.className = 'metric-label';
    b.textContent = l;
    d.append(a, b);
    els.metrics.append(d);
  }
  add('ok',   m.queue_total || 0,     '总数');
  add('ok',   by.learning || 0,       '学习');
  add('ok',   by.maintenance || 0,    '维护');
  add((m.error_count || 0) > 0 ? 'error' : 'ok', m.error_count || 0, '错误');
  if (lr.completed || lr.failed) add('ok', (lr.completed || 0) + '/' + (lr.failed || 0), '完成/失败');
  add(m.body_switch_active ? 'warn' : 'ok', m.active_slot || '—', '替身');
  add(state.in_execution_window !== false ? 'ok' : '', state.in_execution_window !== false ? '开放' : '关闭', '窗口');
}

/* ── 倒计时 ── */
let countdownTimer = null, nextReviewAt = null;
function formatCountdown(s) {
  if (s <= 0) return '即将';
  const m = Math.floor(s / 60), r = Math.floor(s % 60);
  if (m > 0) return m + '分' + (r < 10 ? '0' : '') + r + '秒';
  return r + '秒';
}
function renderSchedule(sch) {
  const el = $('#schedule'), cd = $('#countdown');
  if (!el || !cd) return;
  const nxt = sch.next_review_at || sch.next_drive_at;
  if (!nxt) { el.style.display = 'none'; nextReviewAt = null; return; }
  nextReviewAt = nxt; el.style.display = 'block';
  function tick() {
    if (!nextReviewAt) { cd.textContent = '—'; return; }
    const d = new Date(nextReviewAt);
    if (isNaN(d.getTime())) { cd.textContent = '—'; return; }
    const rem = Math.max(0, (d.getTime() - Date.now()) / 1000);
    cd.textContent = formatCountdown(rem);
    cd.style.color = rem <= 10 ? 'var(--coral)' : '';
  }
  tick();
  if (countdownTimer) clearInterval(countdownTimer);
  countdownTimer = setInterval(tick, 1000);
}

/* ── 应用状态 ── */
let userPickedAction = null;  // 用户手动选择的 action(锁定直到下一轮场景变化可解锁)

function applyState(state) {
  const scene = state.scene || 'idle';
  const prevScene = els.body.dataset.scene;
  els.body.dataset.scene = scene;
  els.glyph.textContent = GLYPHS[scene] || '·';
  els.title.textContent = state.title || '义子的小屋';
  els.summary.textContent = state.summary || '';
  els.body.dataset.hasErrors = ((state.error_count || 0) > 0) ? 'true' : 'false';
  els.body.dataset.execWindow = (state.in_execution_window !== false) ? 'true' : 'false';

  // 自动动作: 仅当用户未手动选择时, 跟随场景
  if (!userPickedAction || userPickedAction.scene !== scene) {
    const action = SCENE_TO_ACTION[scene] || 'rest';
    setAction(action, /*silent*/ true);
    userPickedAction = { scene, action };
  }
  updateActionButtons();

  renderCharCard(state);
  renderPanels(state.panels || {});
  renderCandidates(state.drive_candidates || []);
  renderExecutions(state.active_executions || []);
  renderBodyStatus(state.body_status || {});
  renderTimeline(state.timeline || []);
  renderMetrics(state);
  if (state.schedule) renderSchedule(state.schedule);

  if (scene !== prevScene) spawnParticles(scene, 12);
}

function setAction(action, silent) {
  els.body.dataset.action = action;
  if (!silent) spawnParticles(action, 8);
}
function updateActionButtons() {
  $$('.action-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.action === els.body.dataset.action);
  });
}

/* ── 动作按钮绑定 ── */
els.actionBar.addEventListener('click', e => {
  const btn = e.target.closest('.action-btn');
  if (!btn) return;
  const a = btn.dataset.action;
  setAction(a, false);
  // 解锁"用户选择", 之后会一直跟随用户直到下次 applyState 触发
  userPickedAction = { scene: '__manual__', action: a };
  updateActionButtons();
});

/* ── 任务卡片收起/展开 ── */
const charCard = $('#charCard');
const charToggleBtn = $('#charToggle');
if (charCard && charToggleBtn) {
  charToggleBtn.addEventListener('click', e => {
    e.stopPropagation();
    const collapsed = charCard.classList.toggle('collapsed');
    charToggleBtn.textContent = collapsed ? '+' : '−';
    charToggleBtn.setAttribute('aria-label', collapsed ? '展开任务卡' : '收起任务卡');
    charToggleBtn.setAttribute('title', collapsed ? '展开任务卡' : '收起任务卡');
  });
}

/* ── 状态面板收起/展开 ── */
const statusPanel = $('#statusPanel');
const statusToggleBtn = $('#statusToggle');
if (statusPanel && statusToggleBtn) {
  statusToggleBtn.addEventListener('click', e => {
    e.stopPropagation();
    const collapsed = statusPanel.classList.toggle('collapsed');
    statusToggleBtn.textContent = collapsed ? '+' : '−';
    statusToggleBtn.setAttribute('aria-label', collapsed ? '展开面板' : '收起面板');
    statusToggleBtn.setAttribute('title', collapsed ? '展开面板' : '收起面板');
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
    els.title.textContent = '义子的小屋 · 等待中';
    els.summary.textContent = '尚未连接到监督者。';
    els.glyph.textContent = '·';
    els.metrics.replaceChildren();
    els.panels.replaceChildren();
    els.candidates.style.display = 'none';
    els.executions.style.display = 'none';
    els.bodyStatus.replaceChildren();
    els.timeline.replaceChildren();
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
updateActionButtons();

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

        # ── Tier 1 short-term memory stats ──
        tier1_stats: Dict[str, Any] = {}
        try:
            tier1_stats = await self._fetch_tier1_stats()
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
            memory_active=tier1_stats.get("memory_active", False),
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
            "tier1_stats": tier1_stats,
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
                    f"义子正在整理记忆{error_note}",
                    f"「{rtitle}」记忆维护任务正在执行。",
                )
            # For learning / body-upgrade running tasks, the supervisor
            # just dispatched them — show `dispatch` (NOT `learning` or
            # `body_switch`, which are Agent / Executor scenes).
            return (
                "dispatch",
                f"义子已派发任务{error_note}",
                f"「{rtitle}」已派发，代理或执行器正在运行，监督者等待结果。",
            )

        # 2. Learning / body-upgrade tasks awaiting Agent pull — supervisor
        #    is managing the list, not learning.
        learning_pending = [t for t in all_tasks if "learning" in str(t.get("task_family", "")) and t.get("status") == "approved"]
        if learning_pending:
            lp = learning_pending[0]
            return (
                "planning",
                f"义子已批准学习任务{error_note}",
                f"「{lp.get('title', '学习任务')}」已就绪，等待代理拉取执行。",
            )

        # 2.5 Memory model actively compressing (detected from memory_service rules_status)
        if memory_active:
            return (
                "memory",
                f"Xizi is organizing memory{error_note}",
                "记忆模型正在执行压缩规则：衰减→桥接→升级→清退。",
            )

        # 3. Endogenous drive active
        if drive_candidates:
            first = drive_candidates[0]
            value_tags = ", ".join(first.get("value_tags") or [])
            utility_pct = int((first.get("utility") or 0) * 100)
            return (
                "drive",
                f"义子发现值得做的事{error_note}",
                f"「{first.get('title', '候选任务')}」从核心价值中浮现 [{value_tags}]，价值度 {utility_pct}%，等待治理审查。",
            )

        # 4. Memory maintenance queued
        maintenance_pending = [t for t in all_tasks if "memory" in str(t.get("task_family", "")) and t.get("status") in ("approved", "planned")]
        if maintenance_pending:
            mp = maintenance_pending[0]
            return (
                "maintenance",
                f"义子正在整理记忆书架{error_note}",
                f"「{mp.get('title', '维护任务')}」长期连续性正在被守护。",
            )

        # 5. Drive unavailable
        if not drive_available:
            return (
                "idle",
                "义子望着窗外",
                "网关无法访问，房间显示本地监督者状态——信号恢复后内生驱动将继续。",
            )

        # 6. Truly idle
        window_mood = "执行窗口已开启，系统处于安静状态。" if in_execution_window else "执行窗口外，系统正在休息。"
        return (
            "idle",
            f"义子在窗边休息{error_note}",
            f"没有待处理的工作。{window_mood}核心价值保持警觉但平静。",
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
