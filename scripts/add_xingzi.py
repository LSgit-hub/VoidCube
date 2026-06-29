"""Add 星子 (male, short hair) character alongside 西子 (female, long hair).
Switch based on body slot: A→星子, B→西子."""
from pathlib import Path

target = Path("f:/My_code/Traecode/VoidCube/systems/supervisor/ui_runtime.py")
content = target.read_text(encoding="utf-8")

# ── 1. Add 星子 CSS (insert before "/* ── 状态面板" section) ──
xingzi_css = '''
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

'''
# Insert before the status panel section
anchor = "/* ── 状态面板(右下方) ── */"
content = content.replace(anchor, xingzi_css + "\n" + anchor)
print("1. Added 星子 CSS")

# ── 2. Update action state selectors for 西子 to keep working ──
# (The existing selectors target .xizi which is now 西子 - they work fine)

# ── 3. Rename 义子 → 西子 in the HTML comment ──
content = content.replace("<!-- 角色: 义子 -->", "<!-- 角色: 西子(长发女) -->")
print("2. Renamed character comment")

# ── 4. Add 星子 HTML section after the 西子 section ──
old_xizi_end = """      </div>
    </section>

    <!-- 环境粒子 -->"""

xingzi_html = """      </div>
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

    <!-- 环境粒子 -->"""

content = content.replace(old_xizi_end, xingzi_html)
print("3. Added 星子 HTML")

# ── 5. Fix thoughts visibility for 星子 ──
# The thoughts bubble shows on action states. Make the 星子 glyph work.
# Update JS: both glyphs need updating

# ── 6. Update JS ──
# a) Add character switching logic based on body slot
# b) Update glyph element references

old_js_glyph = "  glyph: $('#glyph'),"
new_js_glyph = """  glyph: $('#glyph'),
  glyphXingzi: $('#glyphXingzi'),
  /* character switching */
  activeChar: 'xizi',"""

content = content.replace(old_js_glyph, new_js_glyph)
print("4. Updated JS els with dual glyph refs")

# Add character switch logic to applyState
old_apply_body = """  els.body.dataset.hasErrors = ((state.error_count || 0) > 0) ? 'true' : 'false';
  els.body.dataset.execWindow = (state.in_execution_window !== false) ? 'true' : 'false';"""

new_apply_body = """  els.body.dataset.hasErrors = ((state.error_count || 0) > 0) ? 'true' : 'false';
  els.body.dataset.execWindow = (state.in_execution_window !== false) ? 'true' : 'false';

  // 槽位决定角色: A→星子(男), B→西子(女)
  const slot = (state.body_status || {}).active_slot || '';
  const newChar = String(slot).toUpperCase().includes('A') ? 'xingzi' : 'xizi';
  if (els.activeChar !== newChar) {
    els.activeChar = newChar;
    els.body.dataset.character = newChar;
  }"""

content = content.replace(old_apply_body, new_apply_body)
print("5. Added character switching logic")

# Update glyph both characters
old_glyph_update = "  els.glyph.textContent = GLYPHS[scene] || '·';"
new_glyph_update = """  els.glyph.textContent = GLYPHS[scene] || '·';
  if (els.glyphXingzi) els.glyphXingzi.textContent = GLYPHS[scene] || '·';"""

content = content.replace(old_glyph_update, new_glyph_update)
print("6. Updated dual glyph rendering")

# Also update the error/refresh fallback glyph
old_glyph_fallback = "    els.glyph.textContent = '·';"
new_glyph_fallback = """    els.glyph.textContent = '·';
    if (els.glyphXingzi) els.glyphXingzi.textContent = '·';"""

content = content.replace(old_glyph_fallback, new_glyph_fallback)
print("7. Updated fallback glyph")

# ── 7. Update Dock char strip to show correct name ──
old_dcs_name = "  if (els.dcsName) els.dcsName.textContent = '义子 Lv.' + lv;"
new_dcs_name = """  if (els.dcsName) {
    const slot2 = (state.body_status || {}).active_slot || '';
    const charName = String(slot2).toUpperCase().includes('A') ? '星子' : '西子';
    els.dcsName.textContent = charName + ' Lv.' + lv;
  }"""

content = content.replace(old_dcs_name, new_dcs_name)
print("8. Updated dock char strip name")

# ── 8. Update scene mini title default ──
old_scene_title_default = "  if (els.sceneMiniText) els.sceneMiniText.textContent = state.title || '义子的小屋';"
new_scene_title_default = """  if (els.sceneMiniText) els.sceneMiniText.textContent = state.title || '星子与西子的小屋';"""

content = content.replace(old_scene_title_default, new_scene_title_default)
print("9. Updated scene title default")

# ── 9. Update default scene title text in HTML ──
old_default_title = '<span id="sceneMiniText">义子的小屋</span>'
new_default_title = '<span id="sceneMiniText">星子与西子的小屋</span>'
content = content.replace(old_default_title, new_default_title)
print("10. Updated HTML default title")

# ── 10. Update dock char strip defaults in HTML ──
old_dcs_default = '<div class="dcs-name" id="dcsName">义子</div>'
new_dcs_default = '<div class="dcs-name" id="dcsName">西子</div>'
content = content.replace(old_dcs_default, new_dcs_default)
print("11. Updated dock default name")

# ── Wait, the default should stay "西子" since that's the default character. ──
# The JS will update it based on slot.

# ── 12. Update page title ──
old_page_title = "<title>VoidCube · 义子的小屋</title>"
new_page_title = "<title>VoidCube · 星子与西子的小屋</title>"
content = content.replace(old_page_title, new_page_title)
print("12. Updated page title")

# ── 13. Update Python backend scene mapping text ──
# Replace "义子" references in _map_supervisor_scene
replacements = [
    ('义子正在整理记忆', '正在整理记忆'),
    ('义子已派发任务', '已派发任务'),
    ('义子正在安排治理事务', '正在安排治理事务'),
    ('义子正在整理记忆书架', '正在整理记忆书架'),
    ('义子发现值得优先处理的事', '发现值得优先处理的事'),
    ('义子望着窗外', '望着窗外'),
    ('义子在窗边休息', '在窗边休息'),
]
for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        print(f"  Replaced: {old} → {new}")

# ── 14. Add a CSS rule for dock char strip avatar to reflect character ──
# The mini avatar body color should change with character
# 星子 = blue shirt, 西子 = dress color varies with action
dock_avatar_css = """
/* ── Dock 迷你头像跟随角色 ── */
body[data-character="xingzi"] .dcs-body-mini {
  background: linear-gradient(140deg, #4a6088, #2d4060) !important;
  border-radius: 6px 6px 4px 4px;
}
"""

# Insert before scene-mini-title CSS
anchor2 = "/* ── 场景迷你标题(轻量) ── */"
content = content.replace(anchor2, dock_avatar_css + "\n" + anchor2)
print("13. Added dock avatar character CSS")

# ── Save ──
target.write_text(content, encoding="utf-8")
print(f"\nDone! File: {len(content):,} chars")
