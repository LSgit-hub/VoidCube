#!/usr/bin/env python3
"""
对比英文和中文翻译的结构，找出中文中缺失的翻译键
"""

import argparse
import json
from pathlib import Path


def load_json(file_path: Path) -> dict:
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def compare_dicts(en_dict, zh_dict, path=""):
    missing = []
    empty = []
    
    for key, en_value in en_dict.items():
        current_path = f"{path}.{key}" if path else key
        
        if key not in zh_dict:
            missing.append((current_path, en_value))
        else:
            zh_value = zh_dict[key]
            if isinstance(en_value, dict) and isinstance(zh_value, dict):
                # 递归对比子字典
                sub_missing, sub_empty = compare_dicts(en_value, zh_value, current_path)
                missing.extend(sub_missing)
                empty.extend(sub_empty)
            else:
                # 检查中文值是否为空
                if zh_value is None or (isinstance(zh_value, str) and zh_value.strip() == ""):
                    empty.append((current_path, en_value))
    
    return missing, empty


def build_report(missing, empty):
    lines = [
        "英文翻译文件中存在但中文缺失的翻译:",
        "=" * 80,
        "",
    ]
    for path, value in sorted(missing):
        lines.append(f"{path}")
        lines.append(f"  英文: {value}")
        lines.append("")

    lines.extend([
        "",
        "中文翻译为空的键:",
        "=" * 80,
        "",
    ])
    for path, value in sorted(empty):
        lines.append(f"{path}")
        lines.append(f"  英文: {value}")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="对比英文和中文翻译结构")
    parser.add_argument(
        "--output",
        type=Path,
        help="可选：把报告写入指定文件；默认只输出到控制台",
    )
    args = parser.parse_args()

    locales_dir = Path(__file__).parent / 'VoidCube_cli' / 'locales'
    en_file = locales_dir / 'en_US.json'
    zh_file = locales_dir / 'zh_CN.json'
    
    en_data = load_json(en_file)
    zh_data = load_json(zh_file)
    
    en_trans = en_data.get('translations', {})
    zh_trans = zh_data.get('translations', {})
    
    missing, empty = compare_dicts(en_trans, zh_trans)
    report = build_report(missing, empty)

    print(report)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")


if __name__ == '__main__':
    main()
