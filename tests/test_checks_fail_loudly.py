"""
Проверка, которая не может провалиться, — это не проверка.

Два случая нашлись в репозитории сразу.

Первый: render_pachca.py --check печатал «Отрендерено: 0. Проблем: 0»
и возвращал ноль, если каталог примеров переименовали, потеряли или
опустошили. В журнале CI это выглядело как пройденная задача, а на деле
снимало единственную проверку, которая прогоняет шаблоны на данных.

Второй: check_files.py, validate_config.py и validate_docs.py звали
git ls-files с check=False и брали stdout как есть. Недоступный git давал
пустой список, цикл не выполнялся ни разу — и все три печатали успех,
не открыв ни одного файла.

Здесь обе двери заперты снаружи: скрипты запускаются в условиях, которые
раньше давали зелёный код возврата.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from conftest import ROOT, load

rp = load(ROOT / "scripts" / "render_pachca.py")

СКРИПТЫ_С_GIT = ["check_files.py", "validate_config.py", "validate_docs.py"]


def test_ноль_примеров_это_провал(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rp, "SAMPLES", tmp_path)
    monkeypatch.setattr(sys, "argv", ["render_pachca.py", "--check"])
    assert rp.main() == 1


def test_пропажа_примеров_одного_сервиса_видна(tmp_path, monkeypatch, capsys) -> None:
    """Общий счётчик остаётся ненулевым — поэтому считать надо по сервисам."""
    for имя in ("radarr-test.json", "prowlarr-health.json", "jellyfin-4k.json"):
        (tmp_path / имя).write_text(
            (rp.SAMPLES / имя).read_text(encoding="utf-8"), encoding="utf-8"
        )
    monkeypatch.setattr(rp, "SAMPLES", tmp_path)
    monkeypatch.setattr(sys, "argv", ["render_pachca.py", "--check"])
    assert rp.main() == 1
    вывод = capsys.readouterr().out
    assert "seerr" in вывод
    assert "Отрендерено: 0" not in вывод, "отрендерено не ноль — ловится именно пропажа сервиса"


def test_битый_пример_не_роняет_скрипт(tmp_path, monkeypatch, capsys) -> None:
    """Раньше JSONDecodeError вылетал трассировкой мимо списка проблем."""
    (tmp_path / "radarr-broken.json").write_text("{битый", encoding="utf-8")
    monkeypatch.setattr(rp, "SAMPLES", tmp_path)
    monkeypatch.setattr(sys, "argv", ["render_pachca.py", "--check"])
    assert rp.main() == 1
    assert "не разбирается" in capsys.readouterr().out


def test_правильные_примеры_по_прежнему_проходят(monkeypatch) -> None:
    """Контроль: на настоящем каталоге примеров проверка остаётся зелёной."""
    monkeypatch.setattr(sys, "argv", ["render_pachca.py", "--check"])
    assert rp.main() == 0


@pytest.mark.parametrize("скрипт", СКРИПТЫ_С_GIT)
def test_без_git_проверка_не_печатает_успех(скрипт: str) -> None:
    """
    PATH без git — то же, что git, вернувший ошибку: списка файлов нет.

    python3 берётся по абсолютному пути, поэтому пустой PATH ломает
    ровно то, что нужно сломать.
    """
    готово = subprocess.run(
        [sys.executable, f"scripts/{скрипт}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={"PATH": "/несуществующий-каталог", "LANG": "C.UTF-8"},
        check=False,
    )
    assert готово.returncode != 0, f"{скрипт} отчитался об успехе без git"
    assert "git" in (готово.stdout + готово.stderr).lower(), (
        f"{скрипт} упал, но не сказал, что виноват git"
    )


def test_поиск_секретов_в_двух_скриптах_не_разъедется() -> None:
    """
    validate_config ищет секреты своим коротким списком, validate_docs —
    длинным. Пока первый список вложен во второй, дыра в одном закрыта
    другим — и именно поэтому снятое исключение для secrets.yaml.example
    ничего не ломает. Зависимость молчаливая, поэтому записана тестом:
    выражение, добавленное только в FORBIDDEN, остальные файлы не увидят.
    """
    vc = load(ROOT / "scripts" / "validate_config.py")
    vd = load(ROOT / "scripts" / "validate_docs.py")
    только_в_config = {ш.pattern for ш, _ in vc.FORBIDDEN} - {ш.pattern for ш, _ in vd.PERSONAL}
    assert not только_в_config, (
        f"эти выражения знает только validate_config.py: {только_в_config}. "
        f"Перенесите их в PERSONAL, иначе файлы вне YAML_GLOBS останутся непроверенными"
    )


def test_образец_секретов_больше_не_освобождён() -> None:
    """Исключение освобождало ровно тот файл, который правят руками."""
    исходник = (ROOT / "scripts" / "validate_config.py").read_text(encoding="utf-8")
    assert "ALLOWED_IN_EXAMPLES = {" not in исходник


def test_настоящий_mac_в_образце_находится(tmp_path) -> None:
    """Сценарий утечки целиком: копия образца с боевым MAC-адресом."""
    vc = load(ROOT / "scripts" / "validate_config.py")
    текст = (ROOT / "homeassistant" / "config" / "secrets.yaml.example").read_text(encoding="utf-8")
    подделка = текст + '\ntv_mac: "3c:22:fb:9a:11:07"\n'
    найдено = [метка for шаблон, метка in vc.FORBIDDEN if шаблон.search(подделка)]
    assert найдено == ["похоже на реальный MAC-адрес"]
    assert not [метка for шаблон, метка in vc.FORBIDDEN if шаблон.search(текст)], (
        "в самом образце срабатываний быть не должно — заглушки на то и заглушки"
    )
