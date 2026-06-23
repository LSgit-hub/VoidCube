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
  margin:0; overflow:hidden;
  font-family:"Inter","Segoe UI",system-ui,sans-serif;
  background:#1e2c36;
  color:var(--ink);
  transition:background .8s ease;
}

/* ── Room shell ── */
.room-stage {
  position:fixed; inset:0;
  display:flex; align-items:center; justify-content:center;
  overflow:hidden;
}
.room {
  /* fixed design size — JS will scale this to fit any viewport */
  width:1440px; height:810px;
  position:relative;
  transform:scale(var(--room-scale,1));
  transform-origin:center center;
  display:grid;
  grid-template-columns:minmax(200px,26%) 1fr minmax(200px,26%);
  /* wall 68% / floor 32% — the wall/floor line sits at 32% from bottom */
  grid-template-rows:1fr 32%;
  background:
    /* ceiling gradient */
    linear-gradient(180deg,rgba(255,255,255,.5) 0,rgba(255,255,255,0) 38%),
    /* wallpaper stripes */
    repeating-linear-gradient(90deg,rgba(61,93,107,.08) 0 1px,transparent 1px 64px),
    /* wall base */
    linear-gradient(175deg,var(--wall),var(--wall-dark));
  transition:background .6s ease;
}

/* ── Floor — straight horizontal wall/floor line, no perspective tilt ── */
.room::after {
  content:""; position:absolute; left:0;right:0;bottom:0; height:32%; z-index:0;
  background:
    /* floor planks */
    repeating-linear-gradient(90deg,rgba(60,30,18,.3) 0 2px,transparent 2px 88px),
    repeating-linear-gradient(0deg,rgba(0,0,0,.06) 0 1px,transparent 1px 28px),
    linear-gradient(180deg,var(--floor),var(--floor-dark));
  /* straight horizontal top edge — this is the "wall/floor line" furniture rests on */
  border-top:2px solid rgba(60,30,15,.35);
}

/* ── Ceiling lamp glow ── */
.lamp-glow {
  position:absolute; left:50%; top:0; transform:translateX(-50%);
  width:min(48%,480px); height:18%;
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
  100% { transform:translateY(-60%) translateX(30px); opacity:0; }
}

/* ── Window ── */
.window {
  grid-column:2; grid-row:1;
  align-self:start; justify-self:center;
  width:min(60%,280px); height:170px;
  margin:6% 0 0 0;
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

/* ── Bookshelf — sits on the left wall, bottom edge aligned to the wall/floor line ── */
.shelf {
  grid-column:1; grid-row:1;
  align-self:end; justify-self:start;
  width:min(88%,300px); height:55%;
  margin:0 0 0 4%;
  border:11px solid #5c3d2a;
  border-radius:8px;
  background:
    repeating-linear-gradient(90deg,rgba(60,30,18,.18) 0 2px,transparent 2px 18px),
    linear-gradient(#8b6345,#73502f);
  box-shadow:0 22px 36px var(--shadow), inset 0 0 30px rgba(60,30,15,.25);
  display:grid;
  grid-template-rows:repeat(4,1fr);
  padding:14px; gap:12px;
  position:relative; z-index:1;
  transition:box-shadow .5s ease;
}
.shelf::before, .shelf::after {
  content:""; position:absolute; left:14px; right:14px; height:6px;
  background:linear-gradient(180deg,#4a2f1f,#3a2210);
  border-radius:3px;
}
.shelf::before { top:-4px; }
.shelf::after  { bottom:-4px; }
.shelf-row {
  border-bottom:none;
  display:flex; align-items:flex-end; gap:5px;
  padding:0 4px;
  position:relative;
}
.shelf-row::after {
  content:""; position:absolute; left:0; right:0; bottom:-2px; height:6px;
  background:linear-gradient(180deg,#4a2f1f,#3a2210);
  border-radius:2px;
  box-shadow:0 2px 4px rgba(0,0,0,.25);
}
.book {
  width:18px; border-radius:2px 2px 0 0;
  box-shadow:inset -4px 0 rgba(255,255,255,.18), inset 2px 0 rgba(0,0,0,.12);
  transition:height .4s ease,background .4s ease,box-shadow .5s ease;
  position:relative;
}
.book::after {
  content:""; position:absolute;
  left:50%; top:18%;
  width:2px; height:60%;
  transform:translateX(-50%);
  background:rgba(255,255,255,.25);
  border-radius:1px;
}
.book.b1  { height:72%; background:linear-gradient(180deg,#7ba8c8,#4a7a9a); }
.book.b2  { height:58%; background:linear-gradient(180deg,#e89a8a,#c66858); }
.book.b3  { height:84%; background:linear-gradient(180deg,#e8c878,#b08830); }
.book.b4  { height:64%; background:linear-gradient(180deg,#a8c89a,#689070); }
.book.b5  { height:78%; background:linear-gradient(180deg,#c8a0c8,#906098); }
.book.b6  { height:54%; background:linear-gradient(180deg,#e8a878,#b87848); }
.book.b7  { height:68%; background:linear-gradient(180deg,#88b0c0,#588098); }
.book.b8  { height:80%; background:linear-gradient(180deg,#d4a878,#a47840); }
.book.b9  { height:60%; background:linear-gradient(180deg,#b8c8d0,#7890a0); }
.book.b10 { height:74%; background:linear-gradient(180deg,#e89898,#b06868); }
.book.lean-l { transform:rotate(-6deg) translateY(2px); }
.book.lean-r { transform:rotate(5deg) translateY(1px); }
.book.short  { height:36% !important; }
.book.tall   { height:92% !important; }
.book.flat {
  height:14%; width:54px;
  border-radius:1px;
  align-self:flex-start;
  margin-top:auto;
  box-shadow:inset 0 -3px rgba(0,0,0,.18);
}
.book.flat::after { display:none; }

/* bookshelf glow per scene */
body[data-scene="memory"]   .shelf { box-shadow:0 22px 36px var(--shadow),0 0 40px var(--gold-glow); }
body[data-scene="learning"] .shelf { box-shadow:0 22px 36px var(--shadow),0 0 40px var(--mint-glow); }

/* ── Desk + lamp — main desk, sits on the floor against the right wall ── */
.desk {
  grid-column:3; grid-row:2;
  align-self:end; justify-self:start;
  width:min(94%,360px); height:108px;
  border-radius:10px;
  background:linear-gradient(#704a35,#553320);
  box-shadow:0 24px 30px var(--shadow);
  position:relative; z-index:2;
  margin:0 0 4% 2%;
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

/* papers on the main desk — appropriately sized, realistic detail */
.papers {
  position:absolute; right:8%; top:-30px;
  width:120px; height:80px;
  z-index:3;
}
.paper {
  position:absolute; border-radius:2px;
  background:linear-gradient(180deg,#fdf6e3 0%,#f0e2c0 100%);
  border:1px solid rgba(120,80,40,.18);
  box-shadow:0 3px 6px rgba(60,30,15,.25), inset 0 -1px 0 rgba(120,80,40,.08);
  transform:rotate(var(--r,0deg));
  transition:transform .4s ease,background .4s ease;
}
/* paper content — subtle text lines */
.paper::before, .paper::after {
  content:""; position:absolute; left:8px; right:8px; height:1px;
  background:rgba(80,60,40,.18); border-radius:1px;
}
.paper::before { top:14px; box-shadow:0 6px 0 rgba(80,60,40,.18), 0 12px 0 rgba(80,60,40,.18), 0 18px 0 rgba(80,60,40,.12), 0 24px 0 rgba(80,60,40,.18); }
.paper::after  { top:36px; width:60%; box-shadow:0 6px 0 rgba(80,60,40,.18), 0 12px 0 rgba(80,60,40,.12); }
.paper.p1 { left:0;  top:6px;  width:74px; height:60px; --r:-7deg; }
.paper.p2 { left:24px; top:0;  width:74px; height:60px; --r:4deg; }
.paper.p3 { left:50px; top:10px; width:70px; height:56px; --r:-3deg; }
/* coffee stain on a paper for life */
.paper.p2::before { box-shadow:0 6px 0 rgba(80,60,40,.18), 0 12px 0 rgba(80,60,40,.18), 0 18px 0 rgba(80,60,40,.12), 0 24px 0 rgba(80,60,40,.18), 38px 36px 0 -2px rgba(140,80,40,.22); }

/* ── Computer desk (separate small desk) + laptop — lined up to the right of the sofa ── */
.comp-desk {
  position:absolute;
  /* bottom edge sits exactly on the wall/floor line (32% from bottom) */
  left:44%;
  bottom:32%;
  width:200px; height:118px;
  z-index:2;
  transform:perspective(800px) rotateY(0deg);
  transform-origin:center bottom;
  filter:drop-shadow(0 10px 8px rgba(40,20,10,.18));
}
/* wooden top */
.cd-top {
  position:absolute; left:0; right:0; top:0;
  height:14px;
  background:linear-gradient(180deg,#8c5f44,#5c3820);
  border-radius:4px 4px 2px 2px;
  box-shadow:0 2px 3px rgba(0,0,0,.25);
}
/* legs */
.cd-leg {
  position:absolute; bottom:-22px; width:8px; height:24px;
  background:linear-gradient(180deg,#4a2c18,#2a1608);
  border-radius:1px;
}
.cd-leg.l { left:8px; }
.cd-leg.r { right:8px; }
/* laptop base */
.cd-laptop {
  position:absolute;
  left:30px; bottom:18px;
  width:140px; height:78px;
  z-index:2;
}
/* laptop screen (the lid, tilted up) */
.cd-screen-wrap {
  position:absolute; left:6px; top:0;
  width:128px; height:60px;
  transform-origin:bottom left;
  transform:perspective(280px) rotateX(-22deg);
}
.cd-screen {
  position:absolute; inset:0;
  border:4px solid #1a1f28;
  border-radius:4px 4px 1px 1px;
  background:#02060c; /* off-state */
  overflow:hidden;
  box-shadow:0 2px 6px rgba(0,0,0,.4);
  animation:cd-boot 5s ease-in-out infinite;
}
@keyframes cd-boot {
  0%   { background:#02060c; box-shadow:0 2px 6px rgba(0,0,0,.4); }
  18%  { background:#04101c; box-shadow:0 2px 6px rgba(0,0,0,.4), inset 0 0 12px rgba(80,180,220,.15); }
  35%  { background:#082030; box-shadow:0 2px 6px rgba(0,0,0,.4), inset 0 0 18px rgba(80,180,220,.4); }
  60%  { background:#0c3055; box-shadow:0 2px 8px rgba(0,0,0,.4), inset 0 0 22px rgba(120,210,255,.6); }
  85%,100% { background:linear-gradient(180deg,#0a2540 0%,#0e3a66 100%);
             box-shadow:0 2px 8px rgba(0,0,0,.4), inset 0 0 28px rgba(120,210,255,.55); }
}
/* faint scan lines on the screen */
.cd-screen::before {
  content:""; position:absolute; inset:0;
  background:repeating-linear-gradient(0deg,rgba(255,255,255,.04) 0 1px,transparent 1px 3px);
  pointer-events:none;
}
/* glowing power LED */
.cd-led {
  position:absolute; right:14px; bottom:8px;
  width:3px; height:3px; border-radius:50%;
  background:var(--mint);
  box-shadow:0 0 6px var(--mint-glow);
  animation:cd-led 1.6s ease-in-out infinite;
}
@keyframes cd-led {
  0%,100% { opacity:.4; }
  50% { opacity:1; }
}
/* tiny blinking cursor on screen */
.cd-screen::after {
  content:"_"; position:absolute;
  left:18%; top:62%;
  color:#7ee0c0; font:700 8px/1 "Courier New",monospace;
  animation:cd-cursor 1s steps(2) infinite;
}
@keyframes cd-cursor {
  50% { opacity:0; }
}
/* laptop base (keyboard deck) */
.cd-keyboard {
  position:absolute; left:0; bottom:0;
  width:140px; height:18px;
  background:linear-gradient(180deg,#3a3f48,#1e2228);
  border:3px solid #1a1f28;
  border-radius:3px 3px 5px 5px;
  box-shadow:0 3px 4px rgba(0,0,0,.35);
  z-index:3;
}
/* trackpad groove */
.cd-keyboard::before {
  content:""; position:absolute;
  left:50%; top:50%; transform:translate(-50%,-50%);
  width:36px; height:8px;
  border:1px solid rgba(255,255,255,.08);
  border-radius:2px;
}
/* coffee mug on the desk */
.cd-mug {
  position:absolute;
  right:10px; top:-4px;
  width:22px; height:26px;
  z-index:3;
}
.cd-mug-body {
  position:absolute; inset:0;
  background:linear-gradient(180deg,#e6d7b8,#c8a878);
  border-radius:3px 3px 6px 6px;
  box-shadow:inset -3px 0 rgba(120,80,40,.18), inset 0 -3px rgba(120,80,40,.2);
}
.cd-mug-handle {
  position:absolute; right:-7px; top:5px;
  width:8px; height:12px;
  border:2px solid #c8a878;
  border-left:none;
  border-radius:0 6px 6px 0;
}
/* coffee surface */
.cd-mug::after {
  content:""; position:absolute;
  left:3px; right:3px; top:3px; height:4px;
  background:radial-gradient(ellipse,#5a3a20,#3a2210);
  border-radius:50%;
}
/* steam from coffee */
.cd-steam {
  position:absolute;
  left:50%; top:-10px;
  width:2px; height:8px;
  background:rgba(255,255,255,.5);
  border-radius:2px;
  transform:translateX(-50%);
  animation:cd-steam 2.4s ease-in-out infinite;
}
.cd-steam.s2 { left:60%; animation-delay:.8s; height:6px; }
.cd-steam.s3 { left:40%; animation-delay:1.4s; height:7px; }
@keyframes cd-steam {
  0%   { transform:translate(-50%, 0) scaleY(1); opacity:0; }
  30%  { opacity:.7; }
  100% { transform:translate(-50%, -18px) scaleY(1.4); opacity:0; }
}

/* ── Sofa — bottom edge aligned to the wall/floor line, lined up between shelf and comp-desk ── */
.sofa {
  position:absolute;
  /* bottom edge sits exactly on the wall/floor line (32% from bottom) */
  left:24%;
  bottom:32%;
  width:200px; height:88px;
  z-index:3;
  transform:perspective(900px) rotateY(-3deg) rotateX(1deg);
  transform-origin:center bottom;
  filter:drop-shadow(0 14px 12px rgba(40,20,10,.18));
}
/* back of the sofa (vertical, behind the cushions) */
.sofa-back {
  position:absolute;
  left:0; right:0; top:0;
  height:60px;
  background:linear-gradient(180deg,#c98c6e 0%,#a8694f 60%,#8a4f38 100%);
  border-radius:14px 14px 4px 4px;
  box-shadow:inset 0 -8px rgba(80,40,20,.25), inset 0 2px 0 rgba(255,255,255,.18);
}
/* base / seat platform */
.sofa-base {
  position:absolute;
  left:0; right:0; bottom:6px;
  height:38px;
  background:linear-gradient(180deg,#b07a5e 0%,#8c5a44 70%,#6a4030 100%);
  border-radius:6px;
  box-shadow:inset 0 -2px rgba(0,0,0,.2), 0 4px 8px rgba(0,0,0,.2);
}
/* back cushions */
.sofa-cushion {
  position:absolute;
  border-radius:8px 8px 4px 4px;
  box-shadow:inset 0 -3px rgba(80,40,20,.18), inset 0 1px 0 rgba(255,255,255,.2), 0 2px 4px rgba(0,0,0,.18);
}
.sofa-cushion.back-l { left:8px;  top:6px;  width:104px; height:38px; background:linear-gradient(180deg,#d49a78,#a8694f); }
.sofa-cushion.back-r { right:8px; top:6px;  width:104px; height:38px; background:linear-gradient(180deg,#d49a78,#a8694f); }
/* seat cushions */
.sofa-cushion.seat-l { left:8px;  bottom:14px; width:108px; height:32px; background:linear-gradient(180deg,#dc9e7c,#b07558); }
.sofa-cushion.seat-r { right:8px; bottom:14px; width:108px; height:32px; background:linear-gradient(180deg,#dc9e7c,#b07558); }
/* armrests */
.sofa-arm {
  position:absolute;
  bottom:6px; width:24px; height:64px;
  background:linear-gradient(180deg,#c98c6e 0%,#8a4f38 100%);
  border-radius:6px 6px 4px 4px;
  box-shadow:inset -2px 0 rgba(80,40,20,.2), inset 0 1px 0 rgba(255,255,255,.2);
}
.sofa-arm.l { left:-4px; }
.sofa-arm.r { right:-4px; }
/* legs (small wooden feet) */
.sofa-leg {
  position:absolute; bottom:-4px;
  width:10px; height:8px;
  background:linear-gradient(180deg,#5a3520,#3a2010);
  border-radius:1px;
  box-shadow:0 2px 3px rgba(0,0,0,.3);
}
.sofa-leg.l { left:6px; }
.sofa-leg.m { left:50%; transform:translateX(-50%); }
.sofa-leg.r { right:6px; }
/* decorative pillow on the left seat */
.sofa-pillow {
  position:absolute;
  left:14px; bottom:18px;
  width:32px; height:24px;
  background:linear-gradient(135deg,#7cc9a0,#45997e);
  border-radius:5px;
  box-shadow:inset 0 -2px rgba(0,0,0,.15), inset 0 1px 0 rgba(255,255,255,.25), 0 2px 4px rgba(0,0,0,.2);
  transform:rotate(-6deg);
}
.sofa-pillow::before {
  content:""; position:absolute;
  left:50%; top:50%;
  width:6px; height:6px;
  transform:translate(-50%,-50%) rotate(45deg);
  background:rgba(255,255,255,.18);
  border-radius:1px;
}
/* a folded throw blanket draped on the right armrest */
.sofa-throw {
  position:absolute;
  right:2px; top:18px;
  width:60px; height:36px;
  background:
    linear-gradient(90deg,rgba(255,255,255,.1) 0 2px,transparent 2px 6px),
    linear-gradient(135deg,#e2b04a 0%,#c8983a 50%,#a87820 100%);
  border-radius:3px;
  box-shadow:inset 0 -3px rgba(80,40,10,.25), 0 2px 4px rgba(0,0,0,.2);
  transform:rotate(8deg);
  opacity:.95;
}

/* ── Console terminal — floats on the back wall next to the window ── */
.console {
  grid-column:2; grid-row:1;
  align-self:start; justify-self:start;
  width:156px; height:96px;
  margin:6% 0 0 4%;
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

/* ── Character: 兮子 — stands on the wall/floor line, moves to the right furniture per scene ── */
.xizi {
  position:absolute;
  bottom:32%; /* feet on the wall/floor line */
  left:50%;
  width:170px; height:260px;
  margin:0 0 0 -85px; /* center horizontally */
  z-index:4;
  transition:left .8s cubic-bezier(.4,0,.2,1), transform .8s cubic-bezier(.4,0,.2,1);
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
  0%,100% { margin-bottom:14%; }
  50% { margin-bottom:calc(14% + 6px); }
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

/* ── Status dashboard — upper right wall, above the desk ── */
.status {
  grid-column:3; grid-row:1;
  align-self:start; justify-self:start;
  width:min(94%,360px); margin:6% 0 0 4%;
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
/* panels — grouped task display by execution path */
.panels { display:grid; gap:10px; margin-bottom:12px; }
.panel { margin-bottom:4px; }
.panel-head {
  font-size:10.5px; font-weight:600; text-transform:uppercase;
  letter-spacing:.04em; color:#5a6b7a; padding:2px 0; margin-bottom:4px;
  border-bottom:1px solid rgba(61,93,107,.12);
}
.panel.learning .panel-head { color:var(--mint); }
.panel.maintenance .panel-head { color:var(--gold); }
.panel.evolution .panel-head { color:var(--coral); }
/* candidates */
.candidates { margin-bottom:12px; display:grid; gap:5px; }
.candidates-label {
  font-size:10px; color:#5a6b7a; text-transform:uppercase;
  letter-spacing:.04em; margin-bottom:2px;
}
.candidate {
  display:grid; grid-template-columns:1fr auto auto; gap:8px;
  align-items:center; min-height:28px; padding:4px 8px;
  border-radius:6px; background:rgba(226,176,74,.08);
  font-size:11px;
}
.candidate-tags { font-size:10px; color:#7a6b4a; }
.candidate-utility { font-size:10px; font-weight:600; color:var(--gold); }
/* body status */
.body-status { margin-bottom:10px; }
.body-info {
  font-size:11px; padding:4px 8px; border-radius:6px;
  background:rgba(61,93,107,.06); color:#3a5260;
}
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

/* ── Scene states — task/character moves to the matching furniture ── */
/* Furniture lineup along the back wall: shelf(0-22%) sofa(24-38%) comp-desk(44-58%) desk(74-94%) */

/* idle: character relaxes on the sofa (left-of-center) */
body[data-scene="idle"] .xizi { left:30%; margin-left:-85px; transform:scale(.96); }
body[data-scene="idle"] .desk-lamp { box-shadow:0 0 22px var(--lamp); }
body[data-scene="idle"] .thoughts { opacity:.65; }
body[data-scene="idle"] .glyph { color:var(--trim); }

/* memory: character at the bookshelf (left), gold theme, books highlighted */
body[data-scene="memory"] .xizi { left:8%; margin-left:-60px; }
body[data-scene="memory"] .xz-body { background:linear-gradient(140deg,#d4af6a,#b08830); }
body[data-scene="memory"] .xz-prop { transform:rotate(6deg) scale(1.12); background:#f4dc82; }
body[data-scene="memory"] .xz-arm.r { animation:arm-reach .9s ease-in-out infinite; }
body[data-scene="memory"] .glyph { color:var(--gold); }
body[data-scene="memory"] .status { border-color:rgba(226,176,74,.35); }
body[data-scene="memory"] .bubble { background:rgba(255,245,210,.92); }
body[data-scene="memory"] .shelf { box-shadow:0 22px 36px var(--shadow),0 0 40px var(--gold-glow); }

/* learning: character at the bookshelf (left), reading a card, mint glow */
body[data-scene="learning"] .xizi { left:8%; margin-left:-60px; }
body[data-scene="learning"] .xz-body { background:linear-gradient(140deg,#5cc497,#3a8e6e); }
body[data-scene="learning"] .xz-prop { background:#c0efd4; animation:card-flip 1.6s ease-in-out infinite; }
body[data-scene="learning"] .glyph { color:var(--mint); }
body[data-scene="learning"] .status { border-color:rgba(124,201,160,.35); }
body[data-scene="learning"] .bubble { background:rgba(225,250,238,.92); }
body[data-scene="learning"] .shelf { box-shadow:0 22px 36px var(--shadow),0 0 40px var(--mint-glow); }
@keyframes card-flip {
  0%,100% { transform:rotate(-6deg) scale(1); }
  50% { transform:rotate(10deg) scale(1.1); box-shadow:0 0 24px var(--mint-glow); }
}

/* planning: character at the main desk (right), thinking pose, coral accent */
body[data-scene="planning"] .xizi { left:80%; margin-left:-100px; }
body[data-scene="planning"] .xz-head { transform:translateY(-4px); }
body[data-scene="planning"] .glyph { color:var(--coral); animation-duration:1.2s; }
body[data-scene="planning"] .xz-arm.l { animation:arm-think .8s ease-in-out infinite; }
body[data-scene="planning"] .bubble { animation-duration:1.8s; }
body[data-scene="planning"] .status { border-color:rgba(224,115,98,.3); }
@keyframes arm-think {
  0%,100% { transform:rotate(12deg) translateY(0); }
  50% { transform:rotate(28deg) translateY(-8px); }
}

/* execution: character at the comp-desk (center), typing, console active, coral glow */
body[data-scene="execution"] .xizi { left:50%; margin-left:-60px; transform:translateY(4px); }
body[data-scene="execution"] .xz-body { background:linear-gradient(140deg,var(--coral),#c55a48); }
body[data-scene="execution"] .xz-arm.l { animation:arm-type .5s ease-in-out infinite; }
body[data-scene="execution"] .xz-arm.r { animation:arm-type .5s ease-in-out .25s infinite; }
body[data-scene="execution"] .glyph { color:var(--coral); }
body[data-scene="execution"] .status { border-color:rgba(224,115,98,.4); }
body[data-scene="execution"] .console { box-shadow:0 16px 22px var(--shadow),0 0 28px var(--coral-glow); }
body[data-scene="execution"] .bubble { opacity:.55; }
body[data-scene="execution"] .comp-desk { box-shadow:0 10px 8px rgba(40,20,10,.18), 0 0 24px var(--coral-glow); }
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

/* ── Responsive — small screens stack the room vertically (single column) ── */
@media (max-width:820px) {
  .room { grid-template-columns:1fr; grid-template-rows:36vh 32vh 32vh; }
  .window { grid-column:1; grid-row:1; width:160px; height:110px; margin:4vh auto 0; justify-self:center; }
  .shelf { grid-column:1; grid-row:2; align-self:end; justify-self:start;
    width:140px; height:180px; margin:0 0 2vh 4vw; }
  .xizi { left:50%; margin-left:-70px; bottom:32%; width:140px; height:220px; transform:scale(.85); transform-origin:center bottom; }
  body[data-scene="idle"] .xizi { left:34%; margin-left:0; }
  body[data-scene="memory"] .xizi,
  body[data-scene="learning"] .xizi { left:20%; margin-left:0; }
  body[data-scene="execution"] .xizi { left:54%; margin-left:0; }
  body[data-scene="planning"] .xizi { left:78%; margin-left:0; }
  .desk { grid-column:1; grid-row:3; align-self:end; justify-self:start;
    width:80vw; max-width:340px; height:84px; margin:0 0 2vh 4vw; }
  .console { grid-column:1; grid-row:1; justify-self:start; width:118px; height:76px; margin:4vh 0 0 4vw; }
  .thoughts { left:50%; margin-left:-30px; bottom:55%; transform:none; }
  .status { grid-column:1; grid-row:3; align-self:start; justify-self:center; width:92vw; max-width:340px; margin:2vh 0 0; }
  .desk-lamp { left:8%; top:-58px; width:38px; height:50px; }
  .papers { right:6%; top:-22px; width:90px; height:60px; }
  .sofa { left:32%; bottom:32%; width:160px; height:72px; transform:perspective(800px) rotateY(-3deg) scale(.85); transform-origin:center bottom; }
  .comp-desk { left:54%; bottom:32%; width:160px; height:96px; }
  .comp-desk .cd-laptop { left:18px; bottom:14px; width:110px; height:62px; }
  .comp-desk .cd-screen-wrap { width:100px; height:48px; }
  .comp-desk .cd-keyboard { width:110px; height:14px; }
  .comp-desk .cd-mug { right:6px; top:-2px; width:18px; height:22px; }
  .char-card { max-width:200px; padding:8px 10px; }
}

@media (max-width:480px) {
  .shelf { display:none; }
  .window { width:120px; height:90px; }
  .console { display:none; }
  .desk-lamp { display:none; }
  .papers { display:none; }
  .thoughts { transform:translate(60px,-20px) scale(.8); }
  .comp-desk { display:none; }
}

/* Character card — floats top-left, above the bookshelf */
.char-card {
  position:absolute; left:14px; top:14px; z-index:20;
  background:rgba(20,30,50,0.88); border:1px solid rgba(100,150,255,0.25);
  border-radius:var(--radius-md); padding:10px 14px;
  display:grid; grid-template-columns:44px 1fr; gap:8px; align-items:center;
  backdrop-filter:blur(8px);
  min-width:200px; max-width:240px;
}
.char-avatar { width:52px; height:64px; position:relative; margin:0 auto; }
.char-avatar .av-head { position:absolute; top:0; left:8px; width:36px; height:32px; background:linear-gradient(155deg,#ffe4c0,#f0cfa0); border-radius:44% 44% 40% 42%; }
.char-avatar .av-eyes { position:absolute; top:14px; left:12px; display:flex; gap:10px; }
.char-avatar .av-eye { width:5px; height:7px; background:#1e2835; border-radius:50%; }
.char-avatar .av-mouth { position:absolute; top:26px; left:50%; transform:translateX(-50%); width:8px; height:3px; border-bottom:2px solid #a06858; border-radius:50%; }
.char-avatar .av-hair { position:absolute; top:-2px; left:2px; width:48px; height:22px; background:#1a2028; border-radius:20px 20px 6px 6px; z-index:5; }
.char-avatar .av-body { position:absolute; bottom:0; left:10px; width:32px; height:28px; background:linear-gradient(140deg,#4dd0e1,#26a69a); border-radius:8px 8px 6px 6px; }
.char-info { display:flex; flex-direction:column; gap:2px; }
.char-name { font-size:13px; font-weight:700; color:var(--text-primary); }
.char-name .ch-title { font-size:10px; color:var(--accent-purple); margin-left:4px; }
.char-lv { font-size:11px; color:var(--text-secondary); }
.char-lv .lv-val { color:var(--accent-blue); font-weight:700; }
.char-exp-wrap { height:4px; background:rgba(255,255,255,0.08); border-radius:2px; overflow:hidden; }
.char-exp-fill { height:100%; width:0; background:linear-gradient(90deg,var(--accent-purple),var(--accent-blue)); border-radius:2px; transition:width 0.5s ease; }
.char-exp-text { font-size:9px; color:var(--text-muted); }
.char-health { font-size:10px; color:var(--text-secondary); display:flex; gap:8px; }
.ch-hp { color:var(--accent-green); } .ch-hp.warn { color:var(--accent-yellow); } .ch-hp.danger { color:var(--accent-red); }
/* Collapsible panels */
.panel-head { cursor:pointer; user-select:none; display:flex; align-items:center; gap:6px; }
.panel-head::before { content:"\25B8"; font-size:10px; transition:transform 0.2s; display:inline-block; }
.panel.open .panel-head::before { transform:rotate(90deg); }
.panel .panel-body { display:none; }
.panel.open .panel-body { display:block; }
.task { transition:opacity 0.5s ease, max-height 0.5s ease; }
.task.completed { opacity:0; max-height:0; overflow:hidden; margin:0; padding:0; border:none; }
.task-badge.approved { background:rgba(102,187,106,0.15); color:var(--mint); }
.task-badge.running { background:rgba(100,181,246,0.15); color:var(--accent-blue); }
.task-badge.completed,.task-badge.failed,.task-badge.cancelled { background:rgba(255,255,255,0.06); color:var(--text-muted); }
.task-badge.planned,.task-badge.queued,.task-badge.deferred { background:rgba(255,183,77,0.12); color:var(--gold); }


/* Monitor (existing, on the main desk) — REMOVED, see sofa */

</style>
</head>
<body data-scene="idle" data-has-errors="false" data-exec-window="true">
<div class="room-stage">
<main class="room" aria-label="VoidCube supervisor room">

  <!-- ambient particles -->
  <div class="particles" id="particles" aria-hidden="true"></div>

  <!-- ceiling lamp -->
  <div class="lamp-glow" aria-hidden="true"></div>

  <!-- bookshelf -->
  <section class="shelf" aria-hidden="true">
    <div class="shelf-row"><span class="book b1"></span><span class="book b2 lean-r"></span><span class="book b3"></span><span class="book b4 short"></span><span class="book b5"></span><span class="book b6"></span><span class="book b7 lean-l"></span><span class="book b8"></span><span class="book b9 short"></span><span class="book b10"></span></div>
    <div class="shelf-row"><span class="book b2"></span><span class="book b5 lean-l"></span><span class="book b8"></span><span class="book b1 short"></span><span class="book b3"></span><span class="book b6 lean-r"></span><span class="book b9"></span><span class="book b4"></span></div>
    <div class="shelf-row"><span class="book b7"></span><span class="book b10"></span><span class="book b2 lean-r"></span><span class="book b5"></span><span class="book b8 lean-l"></span><span class="book flat b3"></span><span class="book flat b4"></span><span class="book b1 short"></span><span class="book b6"></span></div>
    <div class="shelf-row"><span class="book b3"></span><span class="book b9 lean-r"></span><span class="book b1 tall"></span><span class="book b4"></span><span class="book b7 lean-l"></span><span class="book b2"></span><span class="book b5"></span><span class="book b10 short"></span><span class="book b8"></span></div>
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

  <!-- character info card (floating in scene) -->
  <div class="char-card" id="charCard">
    <div class="char-avatar">
      <div class="av-head"></div><div class="av-eyes"><span class="av-eye"></span><span class="av-eye"></span></div>
      <div class="av-mouth"></div><div class="av-hair"></div><div class="av-body"></div>
    </div>
    <div class="char-info">
      <div class="char-name">义子 <span class="ch-title" id="chTitle">初始替身</span></div>
      <div class="char-lv">Lv.<span class="lv-val" id="chLevel">1</span></div>
      <div class="char-exp-wrap"><div class="char-exp-fill" id="chExpBar"></div></div>
      <div class="char-exp-text"><span id="chExpText">0 body switch</span></div>
      <div class="char-health"><span class="ch-hp" id="chHP">❤️ 100%</span><span id="chMood">😊 普通</span></div>
    </div>
  </div>


  <!-- desk + lamp + papers (monitors removed) -->
  <div class="desk" aria-hidden="true">
    <div class="desk-lamp"></div>
    <!-- Stack of papers / documents on the desk -->
    <div class="papers">
      <span class="paper p1"></span>
      <span class="paper p2"></span>
      <span class="paper p3"></span>
    </div>
  </div>

  <!-- computer desk + laptop (with boot animation) — small secondary desk -->
  <div class="comp-desk" aria-hidden="true">
    <div class="cd-top"></div>
    <div class="cd-leg l"></div>
    <div class="cd-leg r"></div>
    <div class="cd-laptop">
      <div class="cd-screen-wrap">
        <div class="cd-screen"></div>
        <div class="cd-led"></div>
      </div>
      <div class="cd-keyboard"></div>
    </div>
    <div class="cd-mug">
      <div class="cd-mug-body"></div>
      <div class="cd-mug-handle"></div>
      <span class="cd-steam"></span>
      <span class="cd-steam s2"></span>
      <span class="cd-steam s3"></span>
    </div>
  </div>

  <!-- sofa — tucked in the left wall corner on the floor -->
  <div class="sofa" aria-hidden="true">
    <div class="sofa-back"></div>
    <div class="sofa-cushion back-l"></div>
    <div class="sofa-cushion back-r"></div>
    <div class="sofa-cushion seat-l"></div>
    <div class="sofa-cushion seat-r"></div>
    <div class="sofa-arm l"></div>
    <div class="sofa-arm r"></div>
    <div class="sofa-base"></div>
    <div class="sofa-leg l"></div>
    <div class="sofa-leg r"></div>
    <div class="sofa-leg m"></div>
    <div class="sofa-pillow"></div>
    <div class="sofa-throw"></div>
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

</main>
</div><!-- /room-stage -->
<script>
/*━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  VoidCube Supervisor Room  v2  — JS runtime
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━*/
const $ = (sel, el) => (el||document).querySelector(sel);
const $$ = (sel, el) => [...(el||document).querySelectorAll(sel)];

/* ── Responsive room scaling ──
   The .room is a fixed 1440x810 design canvas.
   We scale it uniformly to fit the viewport so all items
   (desk, sofa, books, character, etc.) resize together. */
const ROOM_DESIGN_W = 1440;
const ROOM_DESIGN_H = 810;

function updateRoomScale() {
  const stage = document.querySelector('.room-stage');
  const room  = document.querySelector('.room');
  if (!stage || !room) return;
  const sw = stage.clientWidth  || window.innerWidth;
  const sh = stage.clientHeight || window.innerHeight;
  if (sw <= 0 || sh <= 0) return;
  const scale = Math.min(sw / ROOM_DESIGN_W, sh / ROOM_DESIGN_H);
  document.documentElement.style.setProperty('--room-scale', scale);
}

/* ── DOM refs ── */
const els = {
  body: document.body,
  title: document.getElementById("sceneTitle"),
  summary: document.getElementById("sceneSummary"),
  glyph: document.getElementById("glyph"),
  panels: document.getElementById("panels"),
  candidates: document.getElementById("candidates"),
  candidateList: document.getElementById("candidateList"),
  executions: document.getElementById("executions"),
  execList: document.getElementById("execList"),
  bodyStatus: document.getElementById("bodyStatus"),
  timeline: document.getElementById("timeline"),
  metrics: document.getElementById("metrics"),
  particles: document.getElementById("particles"),
};

/* ── Glyphs per scene ── */
const GLYPHS = {
  idle:"?", drive:"✦", planning:"!", maintenance:"¶", learning:"λ", body_switch:"⟩", execution:"⟩"
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

/* Render character card */
function renderCharCard(state) {
  var bs = state.body_status || {};
  var lastSwitch = bs.last_switch_result || {};
  var switchCount = (typeof lastSwitch.switch_count === 'number') ? lastSwitch.switch_count : 0;
  var lv = Math.max(1, switchCount + 1);
  var progress = switchCount > 0 ? Math.round((switchCount % 1 || 0.5) * 100) : 0;
  document.getElementById('chLevel').textContent = lv;
  document.getElementById('chExpBar').style.width = progress + '%';
  document.getElementById('chExpText').textContent = switchCount + ' body switch' + (switchCount !== 1 ? 'es' : '');
  var titles = ['\u521d\u59cb\u66ff\u8eab','\u89c9\u9192\u66ff\u8eab','\u719f\u7ec3\u66ff\u8eab','\u7cbe\u82f1\u66ff\u8eab','\u4f20\u5947\u66ff\u8eab','\u865a\u7a7a\u66ff\u8eab','\u4e0d\u673d\u66ff\u8eab'];
  var ti = Math.min(Math.floor((lv-1) / 3), titles.length-1);
  document.getElementById('chTitle').textContent = titles[ti];
  var errors = state.error_count || 0;
  var hp = Math.max(0, 100 - errors * 10);
  var hpEl = document.getElementById('chHP');
  hpEl.textContent = '\u2764\ufe0f ' + hp + '%';
  hpEl.className = 'ch-hp' + (hp < 30 ? ' danger' : hp < 60 ? ' warn' : '');
  var moods = [{min:0,label:'\u75b2\u60eb',emoji:'\ud83d\ude2b'},{min:30,label:'\u4f4e\u843d',emoji:'\ud83d\ude14'},{min:50,label:'\u666e\u901a',emoji:'\ud83d\ude0a'},{min:70,label:'\u6109\u5feb',emoji:'\ud83d\ude04'},{min:90,label:'\u5b8c\u7f8e',emoji:'\u2728'}];
  var mood = moods[0];
  for (var i=moods.length-1; i>=0; i--) { if (hp >= moods[i].min) { mood = moods[i]; break; } }
  document.getElementById('chMood').textContent = mood.emoji + ' ' + mood.label;
}

/* ── Render grouped task panels ── */
function renderPanels(panels) {
  els.panels.replaceChildren();
  if (!panels) return;
  var statusLabel = {planned:'\u5f85\u6267\u884c',deferred:'\u7b49\u5f85\u4e2d',paused:'\u6682\u505c',approved:'\u5f85\u6267\u884c',running:'\u6267\u884c\u4e2d',completed:'\u5b8c\u6210',failed:'\u5931\u8d25',cancelled:'\u53d6\u6d88'};
  var groups = ["learning","maintenance","evolution"];
  groups.forEach(function(key) {
    var panel = panels[key];
    if (!panel) return;
    var activeTasks = (panel.tasks||[]).filter(function(t){
      return t.status !== 'completed' && t.status !== 'failed' && t.status !== 'cancelled';
    });
    if (!activeTasks.length && !panel.count) return;
    var totalCount = panel.count || (panel.tasks||[]).length;
    var section = document.createElement("div");
    section.className = "panel " + key + " open";
    var head = document.createElement("div");
    head.className = "panel-head";
    head.textContent = panel.label + " (" + activeTasks.length + (activeTasks.length !== totalCount ? "/" + totalCount : "") + ")";
    head.onclick = function(){ section.classList.toggle("open"); };
    section.append(head);
    var body = document.createElement("div");
    body.className = "panel-body";
    activeTasks.slice(0,8).forEach(function(t) {
      var row = document.createElement("div");
      row.className = "task";
      if (t.status === 'completed' || t.status === 'failed') row.classList.add('completed');
      var dot = document.createElement("span");
      dot.className = "task-dot " + taskDotClass(t);
      var title = document.createElement("span");
      title.textContent = (t.title||"Untitled").substring(0,48);
      var badge = document.createElement("span");
      badge.className = "task-badge " + (t.status||'queued');
      badge.textContent = statusLabel[t.status] || t.status || 'queued';
      row.append(dot,title,badge);
      body.append(row);
    });
    section.append(body);
    els.panels.append(section);
  });
}

/* ── Render drive candidates ── */
function renderCandidates(candidates) {
  if (!candidates || !candidates.length) {
    els.candidates.style.display = "none";
    return;
  }
  els.candidates.style.display = "block";
  els.candidateList.replaceChildren();
  candidates.slice(0,4).forEach(function(c) {
    var row = document.createElement("div");
    row.className = "candidate";
    var title = document.createElement("span");
    title.textContent = (c.title||"Candidate").substring(0,40);
    var tags = document.createElement("span");
    tags.className = "candidate-tags";
    tags.textContent = (c.value_tags||[]).join(", ");
    var util = document.createElement("span");
    util.className = "candidate-utility";
    util.textContent = Math.round((c.utility||0)*100) + "%";
    row.append(title,tags,util);
    els.candidateList.append(row);
  });
}

/* ── Render body status ── */
function renderBodyStatus(status) {
  els.bodyStatus.replaceChildren();
  if (!status || !status.active_slot) return;
  var row = document.createElement("div");
  row.className = "body-info";
  var label = document.createElement("span");
  label.textContent = "🖥 Body: " + status.active_slot;
  if (status.candidate_slot) {
    label.textContent += " → candidate " + status.candidate_slot;
    row.style.color = "var(--coral)";
  }
  els.bodyStatus.append(row);
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
  var m = state.metrics||{};
  var byPath = m.by_path||{};
  var lr = m.learning_results||{};

  function addMetric(cls,value,label) {
    var d = document.createElement("div");
    d.className = "metric "+cls;
    var v = document.createElement("div");
    v.className = "metric-value";
    v.textContent = value;
    var l = document.createElement("div");
    l.className = "metric-label";
    l.textContent = label;
    d.append(v,l);
    els.metrics.append(d);
  }
  addMetric("ok",m.queue_total||0,"Total");
  addMetric("ok",byPath.learning||0,"Learning");
  addMetric("ok",byPath.maintenance||0,"Maint");
  addMetric((m.error_count||0)>0?"error":"ok",m.error_count||0,"Errors");
  if (lr.completed||lr.failed) {
    addMetric("ok",lr.completed+"/"+(lr.failed||0),"Done/Fail");
  }
  addMetric(m.body_switch_active?"warn":"ok",m.active_slot||"—","Body");
  addMetric(state.in_execution_window!==false?"ok":"",state.in_execution_window!==false?"open":"closed","Window");
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

  renderCharCard(state);
  renderPanels(state.panels||{});
  renderCandidates(state.drive_candidates||[]);
  renderExecutions(state.active_executions||[]);
  renderBodyStatus(state.body_status||{});
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
  var colors = {idle:"rgba(255,248,220,.6)",drive:"#e2b04a",learning:"#7cc9a0",planning:"#e07362",maintenance:"#e2b04a",body_switch:"#e07362",execution:"#e07362"};
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
  var s = els.body.dataset.scene;
  if (s==="execution"||s==="body_switch") spawnParticles(s,3);
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
    els.panels.replaceChildren();
    els.candidates.style.display = "none";
    els.executions.style.display = "none";
    els.bodyStatus.replaceChildren();
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

/* ── Boot responsive scaling ── */
function bootRoomScale() {
  updateRoomScale();
}
bootRoomScale();
window.addEventListener('resize', updateRoomScale);
window.addEventListener('orientationchange', updateRoomScale);
/* run again after fonts/layout settle to avoid 1-frame flicker */
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
