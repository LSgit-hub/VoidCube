"""子进程 text 模式解码缺陷的回归守卫。

Windows 上 ``subprocess.run(..., text=True)`` 默认按 locale（本机为 cp936）解码。
当子进程输出含该编码无法表示的字节时，解码异常发生在内部读取线程里被吞掉，
``run()`` 仍正常返回，但对应流会变成 ``None``：

* 调用方 ``result.stderr.strip()`` 抛 ``AttributeError``（曾导致 checkpoint
  写入静默失败，见 checkpoint_manager._run_git）；
* 真实子进程错误信息同时丢失，排障信息被替换成 AttributeError。

修复方式：显式声明 ``encoding="utf-8", errors="replace"``，使解码确定且永不失败。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = _ROOT / "src" / "voidcube"

_EMIT_UNDECODABLE_BYTES = (
    "import sys; sys.stderr.buffer.write(bytes([0x80, 0xFF, 0xFE])); "
    "sys.stderr.buffer.flush()"
)


def _child_emitting_undecodable_bytes() -> list[str]:
    return [sys.executable, "-c", _EMIT_UNDECODABLE_BYTES]


def test_narrow_text_decoding_turns_the_stream_into_none():
    """复现缺陷机理：窄编码 + 不可解码字节 → 流变成 None。"""
    result = subprocess.run(
        _child_emitting_undecodable_bytes(),
        capture_output=True,
        text=True,
        encoding="cp936",
    )

    assert result.stderr is None


def test_replacement_decoding_always_yields_text():
    """契约：utf-8 + errors=replace 时流永远是 str，不会变成 None。"""
    result = subprocess.run(
        _child_emitting_undecodable_bytes(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)
    assert result.stderr != ""


def test_checkpoint_git_runner_survives_a_none_stream(tmp_path, monkeypatch):
    """即使底层流为 None，_run_git 也必须不抛异常并返回可用的错误文本。"""
    from voidcube.infrastructure.persistence import checkpoint_manager as cm

    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 128, stdout=None, stderr=None)

    monkeypatch.setattr(cm.subprocess, "run", fake_run)
    workdir = tmp_path / "work"
    workdir.mkdir()

    ok, stdout, stderr = cm._run_git(["add", "-A"], tmp_path / "shadow", str(workdir))

    assert ok is False
    assert (stdout, stderr) == ("", "")
    assert seen.get("encoding") == "utf-8"
    assert seen.get("errors") == "replace"


def _text_mode_run_calls_missing_errors(path: Path) -> list[int]:
    """Return line numbers of ``subprocess.run(..., text=True)`` calls lacking errors.

    崩溃安全不变量是 ``errors=``（让解码永不失败），而不是某个具体 encoding：
    展示路径应保留 locale 编码以免 cp936 中文变成替换符，诊断/git 路径可另选
    utf-8。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "run":
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        text_flag = kwargs.get("text")
        if not (isinstance(text_flag, ast.Constant) and text_flag.value is True):
            continue
        if "errors" not in kwargs:
            offenders.append(node.lineno)
    return offenders


def test_all_text_mode_subprocess_calls_declare_errors_replace():
    """守卫：src/voidcube 下所有 text 模式调用都必须声明 errors，防止流变 None。"""
    offenders: dict[str, list[int]] = {}
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        found = _text_mode_run_calls_missing_errors(path)
        if found:
            offenders[str(path.relative_to(_ROOT))] = found

    assert offenders == {}, (
        "存在未声明 errors 的 text 模式 subprocess.run 调用（解码失败会让对应流"
        f"变成 None 并触发 AttributeError）: {offenders}"
    )
