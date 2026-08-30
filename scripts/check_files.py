#!/usr/bin/env python3
"""
Гигиена текстовых файлов: перевод строки в конце, CRLF, висящие пробелы.

Зачем отдельная проверка, если есть yamllint и ruff
---------------------------------------------------
Оба ловят это, но каждый только у себя: yamllint — в YAML, ruff — в .py.
А в репозитории есть ещё Liquid, Handlebars, JSON, Markdown, HTML и
несколько файлов настроек, и там не смотрит никто.

Поводом послужил настоящий случай. Дерево было выложено на GitHub так,
что **ни один** файл не оканчивался переводом строки: копирование между
машинами съело последний байт. CI встал целиком — десять ошибок yamllint
и двенадцать W292 у ruff, — но в двух задачах из семи, а остальные пять
прошли и создали ощущение, что дело в мелочи.

Что проверяется
---------------
* файл заканчивается ровно одним переводом строки;
* внутри нет CRLF (для этого же заведён .gitattributes, но он действует
  только при коммите через git — файл, добавленный иначе, он не поправит);
* нет пробелов и табуляций в конце строк.

Последнее — не вкусовщина. В Liquid и Markdown хвостовой пробел меняет
вывод: два пробела в конце строки Markdown — это перенос строки, а diff
из-за таких правок становится нечитаемым.

Использование:
    python3 scripts/check_files.py
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".woff", ".woff2", ".pdf"}

# Единственное исключение: в Markdown две концевые пробела — это осознанный
# перенос строки. Одиночный пробел всё равно считается мусором.
MARKDOWN_BREAK = "  "


def problems_in(name: str, text: str) -> list[str]:
    found: list[str] = []

    if not text:
        return found

    if "\r" in text:
        line = text[: text.index("\r")].count("\n") + 1
        found.append(f"строка {line}: перевод строки в стиле Windows (CRLF)")

    if not text.endswith("\n"):
        found.append(
            f"строка {text.count(chr(10)) + 1}: нет перевода строки в конце файла — "
            f"на это ругаются и yamllint, и ruff"
        )
    elif text.endswith("\n\n"):
        found.append("в конце файла больше одного пустого перевода строки")

    markdown = name.endswith(".md")
    for number, line in enumerate(text.split("\n"), start=1):
        stripped = line.rstrip("\r")
        if stripped == stripped.rstrip():
            continue
        if markdown and stripped.endswith(MARKDOWN_BREAK) and stripped.strip():
            continue
        found.append(f"строка {number}: пробелы в конце строки")

    return found


def main() -> int:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False,
    ).stdout.splitlines()

    problems: list[str] = []
    checked = 0

    for name in tracked:
        path = ROOT / name
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIX:
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        checked += 1
        problems.extend(f"{name}: {item}" for item in problems_in(name, text))

    print(f"Проверено файлов: {checked}")

    if problems:
        print(f"Найдено проблем: {len(problems)}\n")
        for item in problems[:60]:
            print(f"  ✗ {item}")
        if len(problems) > 60:
            print(f"  … и ещё {len(problems) - 60}")
        print("\nПочинить всё разом:")
        print("  python3 scripts/check_files.py --fix")
        return 1

    print("Все файлы заканчиваются переводом строки, CRLF и висящих пробелов нет.")
    return 0


def fix() -> int:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False,
    ).stdout.splitlines()

    changed = 0
    for name in tracked:
        path = ROOT / name
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIX:
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        markdown = name.endswith(".md")
        lines = []
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if markdown and line.endswith(MARKDOWN_BREAK) and line.strip():
                lines.append(line.rstrip() + MARKDOWN_BREAK)
            else:
                lines.append(line.rstrip())
        fixed = "\n".join(lines).rstrip("\n") + "\n"

        if fixed != text:
            path.write_text(fixed, encoding="utf-8", newline="\n")
            print(f"  поправлен {name}")
            changed += 1

    print(f"Изменено файлов: {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(fix() if "--fix" in sys.argv[1:] else main())
