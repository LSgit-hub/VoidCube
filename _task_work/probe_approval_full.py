"""探针：长命令 approval 面板的真实 fragments 行数/宽度 vs float 高度限制。"""
import runpy
from types import SimpleNamespace

from voidcube.domain.contracts.interaction import ApprovalRequest
from voidcube.interfaces.cli.interaction_adapter import (
    approval_choices,
    approval_display_fragments,
)
from voidcube.interfaces.cli.terminal_text_layout import display_width

# 长命令：>70 列触发 view 选项
command = "sudo rm -rf /opt/backup/data/" + "x" * 120 + " --force --recursive --no-preserve-root"
print("命令显示宽度:", display_width(command))

for show_full, tag in ((False, "默认(截断70列)"), (True, "show_full=True")):
    state = {
        "request": ApprovalRequest(command=command, description="此操作将永久删除文件，且不可恢复。"),
        "choices": approval_choices(command),
        "selected": 0,
        "show_full": show_full,
    }
    frags = approval_display_fragments(SimpleNamespace(_approval_state=state))
    # 拼出行文本
    text = "".join(t for _, t in frags)
    lines = text.splitlines()
    print(f"\n=== {tag}：choices={state['choices']} 共 {len(lines)} 行，最大行宽="
          f"{max((display_width(l) for l in lines), default=0)} ===")
    for i, line in enumerate(lines):
        print(f"{i:2d}|{line}")
