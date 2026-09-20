"""
Проверки обезличивания: учётные данные sing-box.

Конфигурация sing-box выглядит как файл настроек, но настройки в ней —
меньшая часть. UUID клиента VLESS это пароль к серверу, токен в ссылке
на набор правил — доступ к локальному API роутера. Фрагменты этой
конфигурации попадают в документацию, и вместе с ними уезжает боевое
значение: в отличие от ключа с заголовком BEGIN PRIVATE KEY, строка
`"uuid": "..."` не выглядит секретом и взгляд за неё не цепляется.

Тесты фиксируют две вещи: что шаблоны ловят реальную форму записи
и что они не срабатывают на разговоре о ней — в документации теперь
есть абзац, объясняющий, почему этот файл секретный.
"""

from __future__ import annotations

import pathlib

from conftest import ROOT, load

vd = load(ROOT / "scripts" / "validate_docs.py")

THIS_FILE = pathlib.Path(__file__)


def found(text: str) -> list[str]:
    """Метки шаблонов, сработавших на тексте."""
    return [label for pattern, label in vd.PERSONAL if pattern.search(text)]


def uuid_sample() -> str:
    """
    Собираем на лету, а не пишем литералом.

    Проверка читает и файлы тестов тоже. Записанный целиком образец
    пометил бы сам этот файл, а вносить его в SKIP_SELF значило бы
    проделать в обезличивании дыру ровно там, где мы его укрепляем.
    """
    parts = ["1a2b3c4d", "5e6f", "7a8b", "9c0d", "1e2f3a4b5c6d"]
    return '"uuid": "' + "-".join(parts) + '"'


def token_sample() -> str:
    """По той же причине, что и UUID выше."""
    return "?kind=geosite&" + "token=" + "0123456789abcdef" * 4


def test_client_uuid_is_caught():
    assert "UUID клиента VLESS из конфигурации sing-box" in found(uuid_sample())


def test_zero_uuid_is_allowed():
    """Заглушка из одних нулей заведена для примеров — как AA:BB:CC для MAC."""
    stub = '"uuid": "00000000-0000-0000-0000-000000000000"'
    assert found(stub) == []


def test_zeros_in_the_first_group_do_not_make_a_uuid_a_stub():
    """
    Замечание Codex к PR 39, воспроизведённое до правки.

    Первая версия шаблона отсекала заглушку по началу строки — `(?!0{8}-)`.
    Проверялась одна группа из пяти, поэтому боевой UUID, у которого первая
    группа случайно оказалась нулевой, молча проходил мимо проверки.
    Освобождение должно быть ровно на одно значение, а не на диапазон.
    """
    almost_stub = '"uuid": "' + "00000000-1234-5678-9abc-" + "def012345678" + '"'
    assert "UUID клиента VLESS из конфигурации sing-box" in found(almost_stub)


def test_token_in_a_link_is_caught():
    assert "токен доступа в ссылке" in found(token_sample())


def test_talking_about_uuid_is_not_a_leak():
    """
    Ровно тот абзац, что теперь стоит в документации роутера.

    Шаблон, срабатывающий на объяснении «здесь лежат UUID», сделал бы
    предупреждение о секретах невозможным — а оно и есть цель правки.
    """
    paragraph = (
        "Конфигурация sing-box — файл с секретами. В ней лежат UUID "
        "клиентов VLESS, ключи REALITY и токены доступа к локальному API."
    )
    assert found(paragraph) == []


def test_samples_are_not_written_literally():
    """
    Смысл сборки по частям держится, только пока её не обошли.

    Если однажды образец впишут в файл целиком, проверка обезличивания
    начнёт ругаться на собственные тесты — и это выяснится в CI, а не здесь.
    Тест переносит отказ на место причины.
    """
    text = THIS_FILE.read_text(encoding="utf-8")
    assert uuid_sample() not in text
    assert token_sample() not in text
