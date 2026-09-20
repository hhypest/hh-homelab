"""
Сверка версий образов с реестром: разбор тегов и правило «что новее».

Теги здесь не semver, и вся ценность скрипта — в том, как он решает,
какой тег новее. Ошибиться можно в обе стороны, и обе дорого:
промолчать про вышедшее обновление или предложить поднять версию
на ночную сборку.

Сеть тесты не трогают: ответ реестра подставляется списком. Проверяется
именно правило, а не доступность ghcr.io.
"""

from __future__ import annotations

import pytest
from conftest import ROOT, load

ci = load(ROOT / "scripts" / "check_image_updates.py")


# --- форма, скелет, числа -----------------------------------------------

@pytest.mark.parametrize(
    ("tag", "shape"),
    [
        ("5.2.3_v2.0.14-ls474", "N.N.N_vN.N.N-lsN"),
        ("10.11.11ubu2604-ls47", "N.N.NubuN-lsN"),
        ("2026.9.0", "N.N.N"),
        ("v3.5.0", "vN.N.N"),
    ],
)
def test_shape_replaces_digit_runs(tag: str, shape: str):
    assert ci.shape(tag) == shape


def test_shape_ignores_version_part_count():
    """
    Ровно тот случай, ради которого скелет и заведён.

    Jellyfin выкинул ведущую «10.» из схемы версий: 10.12.x стало 12.x.
    Форма у тегов разная, линейка одна — и это должно быть видно.
    """
    assert ci.skeleton("10.11.11ubu2604-ls47") == ci.skeleton("12.1ubu2604-ls50")


@pytest.mark.parametrize(
    ("tag", "skeleton"),
    [("sha-8253831", "sha-"), ("3.4-pr-186", ".-pr-"), ("v3.4.1", "v.")],
)
def test_shape_separates_foreign_tags_by_nature(tag: str, skeleton: str):
    """Числа в этих тегах большие, но версией не являются вовсе."""
    assert ci.skeleton(tag) == skeleton
    assert ci.skeleton(tag) != ci.skeleton("2.35.0")


def test_numbers_sort_numerically():
    assert ci.numbers("5.2.3_v2.0.14-ls474") == (5, 2, 3, 2, 0, 14, 474)


# --- что считается выпуском ---------------------------------------------

@pytest.mark.parametrize(
    "tag",
    ["nightly-2.6.5.5620-ls16", "develop-6.4.4.10685-ls267", "2026.9.0b2",
     "sha256-e091b52846a5", "sha-8253831", "1.2.3-rc1", "latest"],
)
def test_non_releases_are_filtered_out(tag: str):
    assert not ci.release(tag)


@pytest.mark.parametrize(
    "tag",
    ["12.1ubu2604-ls50", "2026.9.2", "v3.5.2", "5.2.3_v2.0.14-ls476"],
)
def test_releases_pass(tag: str):
    assert ci.release(tag)


# --- разбор ссылки ------------------------------------------------------

def test_reference_parsing():
    assert ci.parse("lscr.io/linuxserver/jellyfin:10.11.11ubu2604-ls47") == (
        "lscr.io", "linuxserver/jellyfin", "10.11.11ubu2604-ls47"
    )


def test_image_without_tag_is_an_error():
    with pytest.raises(ValueError, match="нет тега"):
        ci.parse("lscr.io/linuxserver/jellyfin")


def test_reference_without_registry_is_an_error():
    """Короткая форма ушла бы в Docker Hub — угадывать реестр скрипт не берётся."""
    with pytest.raises(ValueError, match="не указан реестр"):
        ci.parse("nginx:1.27")


# --- правило «что новее» ------------------------------------------------

def test_finds_newer_of_same_shape():
    newer, others = ci.compare(
        "5.2.3_v2.0.14-ls474",
        ["5.2.3_v2.0.14-ls473", "5.2.3_v2.0.14-ls475", "5.2.3_v2.0.14-ls476"],
    )
    assert newer == ["5.2.3_v2.0.14-ls475", "5.2.3_v2.0.14-ls476"]
    assert others == []


def test_nightly_builds_are_not_offered():
    """
    У них другая форма, поэтому в сравнение они не попадают вовсе —
    отдельного фильтра для этого не нужно.
    """
    newer, others = ci.compare(
        "2.5.2.5491-ls158",
        ["nightly-2.6.5.5620-ls16", "develop-2.6.4.5611-ls274", "libtorrentv1-5.2.3_v1.2.20-ls132"],
    )
    assert newer == []
    assert others == []


def test_scheme_change_is_shown_not_ranked():
    newer, others = ci.compare(
        "10.11.11ubu2604-ls47",
        ["10.10.7ubu2404-ls30", "12.0ubu2604-ls48", "12.1ubu2604-ls50",
         "nightly-2026091410ubu2604-ls102"],
    )
    assert newer == [], "переномерованную линейку машина ранжировать не должна"
    assert others == ["12.0ubu2604-ls48", "12.1ubu2604-ls50"]


def test_old_tag_of_other_scheme_never_wins():
    """10.10.7 старше закреплённого, и показывать его незачем."""
    _, others = ci.compare("10.11.11ubu2604-ls47", ["10.10.7ubu2404-ls30"])
    assert others == []


@pytest.mark.parametrize(
    ("pinned", "available"),
    [
        ("v3.4.1", ["sha-7920970", "sha-8563362", "develop"]),
        ("v0.5.0", ["3.2-pr-186", "3.4-pr-185", "3.4-pr-186"]),
        ("2.35.0", ["sha-8253831", "sha-9913482"]),
    ],
)
def test_commit_tags_and_pr_builds_stay_quiet(pinned: str, available: list[str]):
    """
    Числа в них больше закреплённых, но это не версии. До уточнения
    правила скелетом все три образа попадали в раздел «смотреть».
    """
    newer, others = ci.compare(pinned, available)
    assert newer == []
    assert others == []


def test_fresh_image_stays_silent():
    newer, others = ci.compare("v3.5.2", ["v3.5.0", "v3.5.1", "v3.5.2"])
    assert newer == []
    assert others == []


# --- разбор compose -----------------------------------------------------

def test_finds_all_images_of_both_stacks():
    found = {}
    for path in ci.COMPOSE:
        found.update(ci.images(path))
    assert len(found) == 9, f"ожидалось девять образов, найдено {len(found)}: {found}"
    assert any("DOCKER_MODS" in name for name in found), (
        "образ из DOCKER_MODS потерялся — ровно его Dependabot и не видит"
    )
    for link in found.values():
        ci.parse(link)  # каждая ссылка разбирается без исключений
