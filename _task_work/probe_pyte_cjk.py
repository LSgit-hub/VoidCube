"""探针：pyte 对中文/边框混合行的最小化测试。"""
import pyte

def test_line(label, line):
    screen = pyte.Screen(80, 24)
    stream = pyte.ByteStream(screen)
    screen.reset()
    stream.feed((line + "\n").encode("utf-8"))
    print(f"--- {label} ---")
    for i, l in enumerate(screen.display[:3]):
        print(f"{i}|{l!r}")

test_line("纯中文行", "│ 此操作将永久删除文件，且不可恢复。                               │")
test_line("边框+中文", "│ 此操作│")
test_line("连续两行", "│ 此操作将永久删除文件，且不可恢复。                               │\n│ 第二行                                                                │")
test_line("命令(ASCII)", "│ sudo rm -rf /opt/backup/data/xxxx │")
