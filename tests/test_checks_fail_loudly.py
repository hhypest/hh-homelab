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

SCRIPTS_WITH_GIT = ["check_files.py", "validate_config.py", "validate_docs.py"]


def test_zero_samples_is_a_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rp, "SAMPLES", tmp_path)
    monkeypatch.setattr(sys, "argv", ["render_pachca.py", "--check"])
    assert rp.main() == 1


def test_missing_samples_of_one_service_are_noticed(tmp_path, monkeypatch, capsys) -> None:
    """Общий счётчик остаётся ненулевым — поэтому считать надо по сервисам."""
    for name in ("radarr-test.json", "prowlarr-health.json", "jellyfin-4k.json"):
        (tmp_path / name).write_text(
            (rp.SAMPLES / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    monkeypatch.setattr(rp, "SAMPLES", tmp_path)
    monkeypatch.setattr(sys, "argv", ["render_pachca.py", "--check"])
    assert rp.main() == 1
    output = capsys.readouterr().out
    assert "seerr" in output
    assert "Отрендерено: 0" not in output, "отрендерено не ноль — ловится именно пропажа сервиса"


def test_broken_sample_does_not_crash_the_script(tmp_path, monkeypatch, capsys) -> None:
    """Раньше JSONDecodeError вылетал трассировкой мимо списка проблем."""
    (tmp_path / "radarr-broken.json").write_text("{битый", encoding="utf-8")
    monkeypatch.setattr(rp, "SAMPLES", tmp_path)
    monkeypatch.setattr(sys, "argv", ["render_pachca.py", "--check"])
    assert rp.main() == 1
    assert "не разбирается" in capsys.readouterr().out


def test_correct_samples_still_pass(monkeypatch) -> None:
    """Контроль: на настоящем каталоге примеров проверка остаётся зелёной."""
    monkeypatch.setattr(sys, "argv", ["render_pachca.py", "--check"])
    assert rp.main() == 0


@pytest.mark.parametrize("script", SCRIPTS_WITH_GIT)
def test_without_git_the_check_prints_no_success(script: str) -> None:
    """
    PATH без git — то же, что git, вернувший ошибку: списка файлов нет.

    python3 берётся по абсолютному пути, поэтому пустой PATH ломает
    ровно то, что нужно сломать.
    """
    rendered = subprocess.run(
        [sys.executable, f"scripts/{script}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={"PATH": "/несуществующий-каталог", "LANG": "C.UTF-8"},
        check=False,
    )
    assert rendered.returncode != 0, f"{script} отчитался об успехе без git"
    assert "git" in (rendered.stdout + rendered.stderr).lower(), (
        f"{script} упал, но не сказал, что виноват git"
    )


def test_secret_patterns_stay_in_sync_between_scripts() -> None:
    """
    validate_config ищет секреты своим коротким списком, validate_docs —
    длинным. Пока первый список вложен во второй, дыра в одном закрыта
    другим — и именно поэтому снятое исключение для secrets.yaml.example
    ничего не ломает. Зависимость молчаливая, поэтому записана тестом:
    выражение, добавленное только в FORBIDDEN, остальные файлы не увидят.
    """
    vc = load(ROOT / "scripts" / "validate_config.py")
    vd = load(ROOT / "scripts" / "validate_docs.py")
    config_only = {tpl.pattern for tpl, _ in vc.FORBIDDEN} - {tpl.pattern for tpl, _ in vd.PERSONAL}
    assert not config_only, (
        f"эти выражения знает только validate_config.py: {config_only}. "
        f"Перенесите их в PERSONAL, иначе файлы вне YAML_GLOBS останутся непроверенными"
    )


def test_secret_sample_is_no_longer_exempt() -> None:
    """Исключение освобождало ровно тот файл, который правят руками."""
    source = (ROOT / "scripts" / "validate_config.py").read_text(encoding="utf-8")
    assert "ALLOWED_IN_EXAMPLES = {" not in source


def test_real_mac_in_a_sample_is_found(tmp_path) -> None:
    """Сценарий утечки целиком: копия образца с боевым MAC-адресом."""
    vc = load(ROOT / "scripts" / "validate_config.py")
    text = (ROOT / "homeassistant" / "config" / "secrets.yaml.example").read_text(encoding="utf-8")
    # Адрес собирается по частям: в виде литерала он выглядел бы настоящим
    # MAC-адресом, и на этом файле срабатывал бы поиск личных данных
    # в validate_docs.py — тот самый, который тест и проверяет.
    mac = ":".join(["3c", "22", "fb", "9a", "11", "07"])
    fake = text + f'\ntv_mac: "{mac}"\n'
    found = [label for template, label in vc.FORBIDDEN if template.search(fake)]
    assert found == ["похоже на реальный MAC-адрес"]
    assert not [label for template, label in vc.FORBIDDEN if template.search(text)], (
        "в самом образце срабатываний быть не должно — заглушки на то и заглушки"
    )


def test_line_length_is_really_checked(tmp_path) -> None:
    """
    Комментарий в ruff.toml утверждал, что длину строк сторожит line-length,
    и на этом основании E501 стояло в исключениях. Но line-length влияет
    ровно на E501 и на ruff format, а формат в CI не запускается: строка
    в триста символов проходила проверку молча. Исключение снято, предел —
    те же 120, что у yamllint.
    """
    long_side = tmp_path / "длинная.py"
    long_side.write_text('x = "' + "я" * 130 + '"\n', encoding="utf-8")
    rendered = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--config", "ruff.toml", str(long_side)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert rendered.returncode != 0, "длинная строка прошла проверку"
    assert "E501" in rendered.stdout

    short_side = tmp_path / "короткая.py"
    short_side.write_text('x = "' + "я" * 100 + '"\n', encoding="utf-8")
    rendered = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--config", "ruff.toml", str(short_side)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert rendered.returncode == 0, f"строка в пределах лимита отвергнута: {rendered.stdout}"


def test_recorder_exclusions_match_real_sensors() -> None:
    """
    В списке исключений recorder стояли маски sensor.docker_*_image
    и sensor.docker_*_status, не совпадающие ни с чем: в monitored_conditions
    нет ни image, ни status. Мёртвая строка в списке исключений опаснее
    отсутствующей — она выглядит как работающая защита.
    """
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    Loader.add_multi_constructor("!", lambda loader, suffix, node: None)

    config = yaml.load(
        (ROOT / "homeassistant" / "config" / "configuration.yaml").read_text(encoding="utf-8"),
        Loader=Loader,
    )
    masks = [m for m in config["recorder"]["exclude"]["entity_globs"] if m.startswith("sensor.docker_")]
    package = yaml.load(
        (ROOT / "homeassistant" / "config" / "packages" / "docker.yaml").read_text(encoding="utf-8"),
        Loader=Loader,
    )
    conditions = set(package["monitor_docker"][0]["monitored_conditions"])

    for mask in masks:
        tail = mask.removeprefix("sensor.docker_*_")
        assert tail in conditions, (
            f"маска {mask} не совпадает ни с одним сенсором: в monitored_conditions "
            f"есть {sorted(conditions)}"
        )
