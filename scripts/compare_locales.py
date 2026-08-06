"""Audit canonical English translations against the Chinese catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE = ROOT / "VoidCube_cli" / "locales" / "en_US.json"
DEFAULT_TRANSLATION = ROOT / "VoidCube_cli" / "locales" / "zh_CN.json"
Issue = tuple[str, Any]


def load_translations(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    translations = payload.get("translations")
    if not isinstance(translations, dict):
        raise ValueError(f"{path} does not contain a translations object")
    return translations


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def compare_catalogs(
    reference: Mapping[str, Any],
    translation: Mapping[str, Any],
    path: str = "",
) -> dict[str, list[Issue]]:
    issues: dict[str, list[Issue]] = {
        "missing": [],
        "empty_reference": [],
        "empty_translation": [],
        "type_mismatch": [],
    }
    for key, reference_value in reference.items():
        current = f"{path}.{key}" if path else key
        if _empty(reference_value):
            issues["empty_reference"].append((current, reference_value))

        if key not in translation:
            issues["missing"].append((current, reference_value))
            continue

        translated_value = translation[key]
        if isinstance(reference_value, dict):
            if not isinstance(translated_value, dict):
                issues["type_mismatch"].append(
                    (current, {"reference": "object", "translation": type(translated_value).__name__})
                )
                continue
            nested = compare_catalogs(reference_value, translated_value, current)
            for category, values in nested.items():
                issues[category].extend(values)
        elif isinstance(translated_value, dict):
            issues["type_mismatch"].append(
                (current, {"reference": type(reference_value).__name__, "translation": "object"})
            )
        elif _empty(translated_value):
            issues["empty_translation"].append((current, reference_value))
    return issues


def audit_files(reference_path: Path, translation_path: Path) -> dict[str, list[Issue]]:
    return compare_catalogs(
        load_translations(reference_path),
        load_translations(translation_path),
    )


def build_report(issues: Mapping[str, Sequence[Issue]]) -> str:
    labels = {
        "missing": "中文缺失的英文翻译键",
        "empty_reference": "英文源文案为空的键",
        "empty_translation": "中文翻译为空的键",
        "type_mismatch": "中英文结构类型不一致的键",
    }
    lines: list[str] = []
    for category, label in labels.items():
        values = list(issues.get(category, ()))
        lines.extend((label, "=" * 80))
        if not values:
            lines.append("(none)")
        else:
            for key, value in sorted(values):
                lines.append(f"{key}: {value}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--translation", type=Path, default=DEFAULT_TRANSLATION)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true", help="return non-zero when issues exist")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    issues = audit_files(args.reference, args.translation)
    report = build_report(issues)
    print(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")
    return 1 if args.check and any(issues.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
