"""探针：纯 fragments 直接 feed pyte，区分 fragments 行宽 vs pyte 解析污染。"""
import pyte
from types import SimpleNamespace

from voidcube.domain.contracts.interaction import ApprovalRequest
from voidcube.interfaces.cli.interaction_adapter import approval_choices, approval_display_fragments

command = "sudo " + "rm -" + "rf /opt/backup/data/" + "x" * 120 + " --force --recursive --no-preserve-root"
state = {
    "request": ApprovalRequest(command=command, description="此操作将永久删除文件，且不可恢复。"),
    "choices": approval_choices(command),
    "selected": 0,
}
frags = approval_display_fragments(SimpleNamespace(_approval_state=state))
text = "".join(t for _, t in frags)
print("=== fragments 原始行(字符len) ===")
for i, l in enumerate(text.splitlines()):
    print(f"{i:2d} len={len(l):3d} |{l}")

print()
screen = pyte.Screen(80, 24)
stream = pyte.ByteStream(screen)
screen.reset()
stream.feed(text.encode("utf-8"))
print("=== pyte 渲染 ===")
for i, l in enumerate(screen.display[:12]):
    print(f"{i:2d} |{l}")
