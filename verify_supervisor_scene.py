"""验证监督者 scene 上报符合基线 §3.4/§3.6 边界.

监督者 (API-B) 只能上报以下 scene:
  idle, planning, memory, drive, dispatch, maintenance, body_switch

禁止上报（API-A 专属）:
  learning, execution
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(r'f:\My_code\Traecode\VoidCube')

LEGAL = {
    'idle', 'planning', 'memory', 'drive', 'dispatch', 'maintenance',
}
FORBIDDEN = {'learning', 'execution', 'code_editing', 'body_switch'}


def _check(name: str, ok: bool, detail: str = '') -> bool:
    mark = 'PASS' if ok else 'FAIL'
    print(f'  [{mark}] {name}' + (f' — {detail}' if detail else ''))
    return ok


def test_supervisor_runtime_guard():
    """_record_supervisor_ui_activity 必须拒绝非法 scene."""
    print('TEST 1: _record_supervisor_ui_activity 运行时守卫')
    src = (ROOT / 'systems/supervisor/ui_runtime.py').read_text(encoding='utf-8')
    # 找到 _record_supervisor_ui_activity 定义行
    start = src.find('def _record_supervisor_ui_activity(')
    if start < 0:
        return _check('函数定义未找到', False)
    # 找到下一个 def 或 class
    end = src.find('\n    def ', start + 1)
    if end < 0:
        end = src.find('\nclass ', start + 1)
    if end < 0:
        end = len(src)
    body = src[start:end]
    return _check(
        '引用 SUPERVISOR_LEGAL_SCENES 并 fallback 到 planning',
        'SUPERVISOR_LEGAL_SCENES' in body
        and 'scene = "planning"' in body
        and 'logger.warning' in body,
    )


def test_supervisor_legal_scenes():
    """SUPERVISOR_LEGAL_SCENES 集合正确."""
    print()
    print('TEST 2: SUPERVISOR_LEGAL_SCENES 定义正确')
    src = (ROOT / 'systems/supervisor/planning_runtime.py').read_text(encoding='utf-8')
    m = re.search(
        r'SUPERVISOR_LEGAL_SCENES:\s*frozenset\[str\]\s*=\s*frozenset\(\s*\{([^}]+)\}\s*\)',
        src,
    )
    if not m:
        return _check('SUPERVISOR_LEGAL_SCENES 定义未找到', False)
    defined = {s.strip().strip('"\'') for s in m.group(1).split(',')}
    return _check(
        f'frozenset = {sorted(defined)}',
        defined == LEGAL,
        detail=f'expected {sorted(LEGAL)}' if defined != LEGAL else '',
    )


def test_supervisor_scene_calls():
    """所有 _record_supervisor_ui_activity 调用点的 scene 都在合法集内."""
    print()
    print('TEST 3: 监督者所有 _record_supervisor_ui_activity 调用')
    bad_calls: list[tuple[str, int, str]] = []
    call_count = 0
    for path in [
        ROOT / 'systems/supervisor/planning_runtime.py',
        ROOT / 'systems/supervisor/service_runtime.py',
        ROOT / 'systems/supervisor/ui_runtime.py',
    ]:
        src = path.read_text(encoding='utf-8')
        for i, line in enumerate(src.splitlines(), 1):
            # 排除 CSS 行（body[data-scene="..."] 是 CSS, 不是 Python scene=）
            if 'data-scene=' in line:
                continue
            # 只看 Python `scene="..."` 或 `scene='...'` 字面量
            m = re.search(r'scene\s*=\s*([\'"])([a-z_]+)\1', line)
            if not m:
                continue
            val = m.group(2)
            if val in FORBIDDEN:
                bad_calls.append((str(path), i, val))
            if val in LEGAL or val in FORBIDDEN:
                call_count += 1
    all_ok = True
    all_ok &= _check(
        f'发现 {call_count} 处显式 scene= 字面量',
        call_count > 0,
    )
    all_ok &= _check(
        '无 learning/execution 字面量',
        len(bad_calls) == 0,
        detail=f'违规: {bad_calls}' if bad_calls else '',
    )
    return all_ok


def test_cli_status_dispatch_added():
    """CLI 状态栏必须有 dispatch 场景."""
    print()
    print('TEST 4: CLI 状态栏 scene 映射含 dispatch')
    src = (ROOT / 'cli.py').read_text(encoding='utf-8')
    # 找到 supervisor scene 渲染段
    start = src.find('# ── Supervisor scene')
    end = src.find('# error indicator', start)
    block = src[start:end] if start > 0 and end > 0 else ''
    return _check(
        'block 含 dispatch (icon/color/label 三处)',
        block.count('"dispatch"') >= 3,
        detail=f'"dispatch" 出现 {block.count(chr(34) + "dispatch" + chr(34))} 次'
        if '"dispatch"' not in block else '',
    )


def test_cli_status_no_forbidden():
    """CLI 状态栏 scene 映射不应再有 learning/execution 场景."""
    print()
    print('TEST 5: CLI 状态栏 scene 映射无 learning/execution')
    src = (ROOT / 'cli.py').read_text(encoding='utf-8')
    start = src.find('# ── Supervisor scene')
    end = src.find('# error indicator', start)
    block = src[start:end] if start > 0 and end > 0 else ''
    return _check(
        'block 内无 learning/execution 字面量',
        '"learning"' not in block and '"execution"' not in block,
    )


def test_drive_scene_for_candidate():
    """内生驱动器产物上报 scene=drive（不是 learning）."""
    print()
    print('TEST 6: self_learning_submitted → scene=drive')
    src = (ROOT / 'systems/supervisor/planning_runtime.py').read_text(encoding='utf-8')
    m = re.search(
        r'_record_supervisor_ui_activity\(\s*\n\s*"self_learning_submitted",\s*\n\s*scene="([^"]+)"',
        src,
    )
    return _check(
        f'self_learning_submitted scene={m.group(1) if m else "?"}（期望 drive）',
        bool(m and m.group(1) == 'drive'),
    )


def test_dispatch_scene_for_executor():
    """下发到执行器时 scene=dispatch（不是 execution）."""
    print()
    print('TEST 7: execution_dispatched / auto_dispatched → scene=dispatch')
    src = (ROOT / 'systems/supervisor/planning_runtime.py').read_text(encoding='utf-8')
    m1 = re.search(
        r'_record_supervisor_ui_activity\(\s*\n\s*"execution_dispatched",\s*\n\s*scene="([^"]+)"',
        src,
    )
    src2 = (ROOT / 'systems/supervisor/service_runtime.py').read_text(encoding='utf-8')
    m2 = re.search(
        r'_record_supervisor_ui_activity\(\s*\n\s*"auto_dispatched",\s*\n\s*scene="([^"]+)"',
        src2,
    )
    ok1 = bool(m1 and m1.group(1) == 'dispatch')
    ok2 = bool(m2 and m2.group(1) == 'dispatch')
    return _check(
        f'execution_dispatched={m1.group(1) if m1 else "?"}, '
        f'auto_dispatched={m2.group(1) if m2 else "?"} (both expected dispatch)',
        ok1 and ok2,
    )


def test_planning_scene_for_decisions():
    """任务决策/审查上报 scene=planning（不是 execution）."""
    print()
    print('TEST 8: task_decided / tasks_reviewed → scene=planning')
    src = (ROOT / 'systems/supervisor/planning_runtime.py').read_text(encoding='utf-8')
    m1 = re.search(
        r'_record_supervisor_ui_activity\(\s*\n\s*"task_decided",\s*\n\s*scene="([^"]+)"',
        src,
    )
    m2 = re.search(
        r'_record_supervisor_ui_activity\(\s*\n\s*"tasks_reviewed",\s*\n\s*scene="([^"]+)"',
        src,
    )
    ok = bool(m1 and m1.group(1) == 'planning') and bool(m2 and m2.group(1) == 'planning')
    return _check(
        f'task_decided={m1.group(1) if m1 else "?"}, '
        f'tasks_reviewed={m2.group(1) if m2 else "?"} (both expected planning)',
        ok,
    )


def test_syntax():
    """所有改过的文件 Python 3.14 语法通过."""
    print()
    print('TEST 9: 3 个改过的文件 Python 3.14 语法检查')
    files = [
        ROOT / 'systems/supervisor/planning_runtime.py',
        ROOT / 'systems/supervisor/service_runtime.py',
        ROOT / 'systems/supervisor/ui_runtime.py',
        ROOT / 'cli.py',
    ]
    all_ok = True
    for f in files:
        try:
            ast.parse(f.read_text(encoding='utf-8'))
            all_ok &= _check(f.name + ' OK', True)
        except SyntaxError as e:
            all_ok &= _check(f.name + f' -- {e}', False)
    return all_ok


def main() -> int:
    results = [
        test_supervisor_runtime_guard(),
        test_supervisor_legal_scenes(),
        test_supervisor_scene_calls(),
        test_cli_status_dispatch_added(),
        test_cli_status_no_forbidden(),
        test_drive_scene_for_candidate(),
        test_dispatch_scene_for_executor(),
        test_planning_scene_for_decisions(),
        test_syntax(),
    ]
    print()
    print('=' * 60)
    if all(results):
        print('所有验证通过！')
        return 0
    print(f'失败: {sum(1 for r in results if not r)}/{len(results)} 项')
    return 1


if __name__ == '__main__':
    sys.exit(main())
