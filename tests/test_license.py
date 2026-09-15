"""
Лицензирование: чистота LICENSE, подписи на страницах, полнота NOTICE.

Три вещи, каждая из которых уже ломалась или могла сломаться молча.

**LICENSE должен оставаться голым текстом MIT.** К нему было дописано
примечание по-русски — полезное, но детектор лицензий GitHub (licensee)
перестал узнавать в файле MIT, и API отдавал по репозиторию
`"license": {"key": "other", "spdx_id": "NOASSERTION"}`. Бейдж в README
обещал MIT, страница репозитория показывала «Other». Примечание переехало
в NOTICE.md, и вернуться в LICENSE не должно.

**Страница должна называть свою лицензию.** Текст лежит под CC BY 4.0,
а эта лицензия держится на указании авторства: страницу сохраняют,
пересылают и цитируют в отрыве от репозитория, поэтому подпись обязана
ехать вместе с ней, а не только лежать в корне.

**NOTICE должен знать про каждый образ.** Добавить сервис в compose и
забыть про его лицензию — ровно та ошибка, которая уже случилась:
VueTorrent стоял в DOCKER_MODS, а в перечне лицензий его не было.
"""

from __future__ import annotations

import re

from conftest import ROOT

LICENSE = ROOT / "LICENSE"
NOTICE = ROOT / "NOTICE.md"
DOCS = sorted((ROOT / "docs").glob("*.html"))
COMPOSE = [ROOT / "media" / "compose.yaml", ROOT / "homeassistant" / "compose.yaml"]

# Ссылка на текст CC BY — то, что превращает страницу в самодостаточную
# с точки зрения лицензии.
CC_BY = "creativecommons.org/licenses/by/4.0"


def упростить(text: str) -> str:
    """Только буквы и цифры в нижнем регистре: «docker-socket-proxy» → «dockersocketproxy»."""
    return re.sub(r"[^0-9a-zа-яё]+", "", text.lower())


def образы() -> list[str]:
    """Имена образов из обоих compose — и из image:, и из DOCKER_MODS."""
    найдено = []
    for path in COMPOSE:
        text = path.read_text(encoding="utf-8")
        найдено += re.findall(r"^\s*image:\s*(\S+)", text, re.M)
        найдено += re.findall(r"DOCKER_MODS=(\S+)", text)
    return найдено


def test_license_это_ровно_текст_mit():
    """
    Побайтовое совпадение с эталоном в LICENSES/. Любая приписка — хоть
    примечание, хоть лишняя пустая строка — здесь и остановится.
    """
    assert LICENSE.read_text(encoding="utf-8") == (
        ROOT / "LICENSES" / "MIT.txt"
    ).read_text(encoding="utf-8"), (
        "LICENSE разошёлся с LICENSES/MIT.txt — GitHub перестанет "
        "определять лицензию и покажет «Other»"
    )


def test_в_license_нет_приписок_по_русски():
    """
    Отдельно от проверки выше, потому что говорит о причине.

    Именно русское примечание сбило детектор в прошлый раз. Пояснения
    нужны — но их место в NOTICE.md, а не в файле лицензии.
    """
    assert not re.search(r"[а-яё]", LICENSE.read_text(encoding="utf-8"), re.I), (
        "в LICENSE появился текст по-русски — пояснения место в NOTICE.md"
    )


def test_текст_cc_by_лежит_в_репозитории():
    """Ссылки на creativecommons.org мало: текст лицензии должен быть под версией."""
    текст = (ROOT / "LICENSES" / "CC-BY-4.0.txt").read_text(encoding="utf-8")
    assert "Creative Commons Attribution 4.0 International" in текст
    assert "Section 3 -- License Conditions" in текст or "Attribution" in текст


def test_каждая_страница_называет_свою_лицензию():
    for path in DOCS:
        подвал = path.read_text(encoding="utf-8").split("<footer>")[-1]
        assert CC_BY in подвал, (
            f"{path.relative_to(ROOT)}: в подвале нет ссылки на CC BY — "
            f"страница, сохранённая отдельно, окажется без указания лицензии"
        )


def test_notice_знает_про_каждый_образ():
    """
    Сверяем по имени образа, а не по списку руками: список разъедется,
    имя в compose — нет.

    Ищем имя целиком и его начала по дефисам: образ
    `vuetorrent-lsio-mod` — это по-прежнему VueTorrent, и в перечне
    лицензий он назван своим именем, без обвязки linuxserver.
    """
    notice = упростить(NOTICE.read_text(encoding="utf-8"))
    assert notice, "NOTICE.md пуст"

    for образ in образы():
        имя = образ.split("/")[-1].split(":")[0]
        части = имя.split("-")
        варианты = ["-".join(части[:i]) for i in range(len(части), 0, -1)]
        assert any(упростить(v) in notice for v in варианты), (
            f"образ {образ} есть в compose, но в NOTICE.md о его лицензии "
            f"ничего не сказано"
        )


def test_readme_ведёт_на_оба_текста_лицензий():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "(LICENSE)" in readme, "README не ссылается на MIT"
    assert "LICENSES/CC-BY-4.0.txt" in readme, "README не ссылается на CC BY"
    assert "NOTICE.md" in readme, "README не ссылается на NOTICE.md"


def test_перечень_лицензий_не_потерял_разделы():
    """
    Защита от вычищенного NOTICE: если файл ужмут до списка ссылок,
    пропадут ровно те оговорки, ради которых он и заведён.
    """
    notice = NOTICE.read_text(encoding="utf-8")
    for заголовок in ("Что под какой лицензией", "Стороннее ПО", "Оговорки"):
        assert заголовок in notice, f"в NOTICE.md пропал раздел «{заголовок}»"


def test_каждый_файл_лицензии_на_месте():
    for имя in ("LICENSE", "NOTICE.md", "CONTRIBUTING.md",
                "LICENSES/MIT.txt", "LICENSES/CC-BY-4.0.txt"):
        assert (ROOT / имя).is_file(), f"нет файла {имя}"


# Карта лицензий обязана покрывать каждый файл под версией. Замечание
# Codex к PR 44: NOTICE перечислял `docs/` и README как текст, а каталоги
# с кодом — как код, и сам NOTICE вместе с CONTRIBUTING не попадал никуда.
# Список ниже — та же карта, записанная так, чтобы её можно было сверить.
КАРТА = {
    "текст": ("docs/", "README.md", "NOTICE.md", "CONTRIBUTING.md"),
    "код": ("scripts/", "tests/", "media/", "homeassistant/", "pachca/", ".github/"),
    "тексты лицензий": ("LICENSE", "LICENSES/"),
    # Про эти NOTICE говорит общими словами — «файлы настроек в корне», —
    # поэтому поимённо в нём их нет, а здесь есть: новый файл в корне
    # должен потребовать решения, а не попасть в код молча.
    "настройки в корне": (
        ".gitattributes", ".gitignore", ".yamllint", "constraints.txt",
        "pytest.ini", "requirements-dev.txt", "ruff.toml",
    ),
}


def под_версией() -> list[str]:
    import subprocess
    return subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False,
    ).stdout.splitlines()


def test_каждый_файл_под_версией_попал_в_карту_лицензий():
    префиксы = [p for группа in КАРТА.values() for p in группа]
    бездомные = [
        f for f in под_версией()
        if not any(f == p or f.startswith(p) for p in префиксы)
    ]
    assert not бездомные, (
        "эти файлы не попадают ни в одну категорию NOTICE.md — "
        f"им не назначена лицензия: {', '.join(sorted(бездомные))}"
    )


def test_notice_называет_каждую_категорию_карты():
    """
    Обратная сторона проверки выше: карта в тесте не должна разойтись
    с картой в NOTICE. Каталоги и файлы, названные там поимённо, ищем
    в тексте; про настройки в корне NOTICE говорит общими словами.
    """
    notice = NOTICE.read_text(encoding="utf-8")
    for группа in ("текст", "код", "тексты лицензий"):
        for путь in КАРТА[группа]:
            искомое = путь.rstrip("/") if путь.endswith("/") else путь
            assert искомое in notice, (
                f"«{путь}» есть в карте теста, но в NOTICE.md не упомянут"
            )
    assert "файлы настроек в корне" in notice
