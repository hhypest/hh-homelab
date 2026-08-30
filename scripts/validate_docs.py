#!/usr/bin/env python3
"""
Проверка документации: целостность страниц, ссылок и обезличивания.

Три вещи, которые тихо портятся со временем:

1. **HTML чек-листов.** Страницы собираются скриптом и правятся руками.
   Незакрытый тег не помешает браузеру их показать, но сломает вёрстку
   в неочевидном месте, и заметить это можно через месяц.

2. **Ссылки в markdown.** Файлы переименовываются, каталоги переезжают,
   ссылки остаются. На GitHub битая относительная ссылка выглядит как
   страница 404 — и только.

3. **Обезличивание.** Репозиторий публичный. MAC-адреса, имена устройств
   и имена Wi-Fi сетей вычищены один раз, но следующая правка легко
   вернёт их обратно — особенно если копировать куски из личных заметок.
   Имя точки доступа есть в открытых базах геолокации по SSID: рядом
   с именем аккаунта GitHub оно указывает на домашний адрес.

Использование:
    python3 scripts/validate_docs.py
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parent.parent

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

# Что не должно вернуться в публичные файлы.
# Заглушки вида AA:BB:CC пропускаем — они для того и заведены.
PERSONAL = [
    (re.compile(r"\b(?!AA:BB:CC)[0-9a-f]{2}(:[0-9a-f]{2}){5}\b", re.I), "похоже на реальный MAC-адрес"),
    (re.compile(r"\bhh(NAS|TV|PC|Phone|Home|IoT)\b"), "имя устройства или Wi-Fi сети"),
    (re.compile(r"\bRed Shield\b"), "название VPN-провайдера"),
    (re.compile(r"\b10\.254\.254\.254\b"), "адрес DNS-сервера туннеля"),
    (re.compile(r"api\.pachca\.com/webhooks/[A-Za-z0-9_-]{8,}"), "боевой вебхук Пачки"),
    (re.compile(r'MediaBrowser Token="[0-9a-f]{16,}"'), "боевой ключ Jellyfin"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "приватный ключ"),
]

SKIP_BINARY = {".png", ".jpg", ".jpeg", ".gif", ".zip", ".ico", ".woff", ".woff2"}


class Checker(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.errors: list[str] = []
        self.seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs) -> None:
        self.seen.add(tag)
        if tag in VOID_TAGS:
            return
        raw = self.get_starttag_text() or ""
        if raw.endswith("/>"):
            return
        self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()
        else:
            near = ", ".join(t for t, _ in self.stack[-3:])
            self.errors.append(f"строка {self.getpos()[0]}: закрывается </{tag}>, а открыт был [{near}]")


def check_html(path: pathlib.Path, problems: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(ROOT)

    checker = Checker()
    checker.feed(text)
    for err in checker.errors:
        problems.append(f"{rel}: {err}")
    for tag, line in checker.stack:
        problems.append(f"{rel}: тег <{tag}> со строки {line} так и не закрыт")

    if not text.lstrip().lower().startswith("<!doctype html>"):
        problems.append(f"{rel}: нет <!doctype html> — страница откроется в режиме совместимости")
    for required in ("html", "head", "body", "title"):
        if required not in checker.seen:
            problems.append(f"{rel}: нет тега <{required}>")
    if 'lang="ru"' not in text[:400]:
        problems.append(f"{rel}: не указан язык страницы (lang)")

    keys = re.findall(r'data-key="([^"]+)"', text)
    duplicates = {k for k in keys if keys.count(k) > 1}
    if duplicates:
        problems.append(f"{rel}: повторяются data-key: {', '.join(sorted(duplicates))} — "
                        f"такие отметки будут переключаться вместе")
    if keys:
        print(f"  HTML   ok  {rel}  ({len(keys)} шагов)")
    else:
        print(f"  HTML   ok  {rel}")


def check_markdown_links(path: pathlib.Path, problems: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(ROOT)
    for label, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", text):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        target = target.split("#", 1)[0]
        if not target:
            continue
        if not (path.parent / target).exists():
            problems.append(f"{rel}: ссылка «{label}» ведёт в никуда — нет {target}")
    print(f"  ССЫЛКИ ok  {rel}")


def main() -> int:
    problems: list[str] = []

    for path in sorted((ROOT / "docs").glob("*.html")):
        check_html(path, problems)

    for path in sorted(ROOT.rglob("*.md")):
        if ".git" in path.parts:
            continue
        check_markdown_links(path, problems)

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False,
    ).stdout.splitlines()

    scanned = 0
    for name in tracked:
        path = ROOT / name
        if not path.is_file() or path.suffix.lower() in SKIP_BINARY:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        for pattern, label in PERSONAL:
            found = pattern.search(content)
            if found:
                problems.append(f"{name}: {label} — «{found.group(0)[:48]}»")

    print(f"\nПроверено файлов на обезличивание: {scanned}")

    if problems:
        print(f"Найдено проблем: {len(problems)}\n")
        for item in problems:
            print(f"  ✗ {item}")
        return 1

    print("Страницы целы, ссылки ведут куда надо, личных данных не найдено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
