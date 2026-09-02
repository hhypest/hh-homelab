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

# Файл, в котором эти образцы записаны, проверять на них же бессмысленно.
# Сейчас он и не срабатывает — по случайности, из-за \b в самих выражениях, —
# но полагаться на такое нельзя: стоит переписать один шаблон, и проверка
# начнёт ругаться на собственный исходник.
SKIP_SELF = {"scripts/validate_docs.py", "scripts/validate_config.py"}


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


STEP_COUNTS: dict[str, int] = {}


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

    # Ссылки вида «см. шаг 2.3» ломаются ровно тогда, когда в раздел
    # вставляют новый шаг и сдвигают нумерацию: текст остаётся прежним,
    # а шага с таким номером больше нет.
    numbers = set(re.findall(r'<span class="num">([\d.]+)</span>', text))
    if numbers:
        other_page = re.compile(r'href="[\w.-]+\.html')
        missing = set()
        for match in re.finditer(r"шаг[а-яё]*\s+(\d+\.\d+)", text):
            if match.group(1) in numbers:
                continue
            # Ссылка может вести на шаг соседнего документа — тогда рядом
            # стоит ссылка на него, и сверять номер с этой страницей нечего.
            near = text[max(0, match.start() - 400):match.end()]
            if other_page.search(near):
                continue
            missing.add(match.group(1))
        for number in sorted(missing, key=lambda v: [int(x) for x in v.split(".")]):
            problems.append(f"{rel}: ссылка на шаг {number}, а такого шага на странице нет")

    keys = re.findall(r'data-key="([^"]+)"', text)
    duplicates = {k for k in keys if keys.count(k) > 1}
    if duplicates:
        problems.append(f"{rel}: повторяются data-key: {', '.join(sorted(duplicates))} — "
                        f"такие отметки будут переключаться вместе")
    if keys:
        STEP_COUNTS[path.name] = len(keys)
        print(f"  HTML   ok  {rel}  ({len(keys)} шагов)")
    else:
        print(f"  HTML   ok  {rel}")


def check_declared_step_counts(path: pathlib.Path, problems: list[str]) -> None:
    """
    README обещает «43 шага с отметками» рядом со ссылкой на чек-лист.
    Число легко разъезжается со страницей: шаг добавили, README не тронули,
    и документ начинает врать в первой же таблице. Сверяем.
    """
    rel = path.relative_to(ROOT)
    page = re.compile(r"docs/([\w.-]+\.html)")
    count = re.compile(r"(\d+)\s+(?:шаг|шага|шагов|пункт\w*)")
    # Число и ссылка стоят в одной строке таблицы, но порядок бывает любым,
    # поэтому смотрим строку целиком, а не последовательность внутри неё.
    for line in path.read_text(encoding="utf-8").splitlines():
        names = page.findall(line)
        declared = count.findall(line)
        if not names or not declared:
            continue
        actual = STEP_COUNTS.get(names[0])
        if actual is None:
            continue
        if int(declared[0]) != actual:
            problems.append(
                f"{rel}: обещано {declared[0]} шагов для {names[0]}, "
                f"а на странице {actual}"
            )


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
        check_declared_step_counts(path, problems)

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False,
    ).stdout.splitlines()

    scanned = 0
    for name in tracked:
        path = ROOT / name
        if not path.is_file() or path.suffix.lower() in SKIP_BINARY or name in SKIP_SELF:
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
