"""
Проверки правил перекрытия bind-адресов.

Тестов у check_ports.py не было вовсе, и это дорого обошлось: сравнение
адресов шло по равенству, а в комментарии стояло, что 127.0.0.1:2375
не спорит с 0.0.0.0:2375. На Linux спорит — wildcard занимает порт на всех
адресах своего семейства, и второй bind получает EADDRINUSE независимо
от порядка. Проверка молча пропускала ровно тот случай, ради которого
писалась.

Поведение здесь зафиксировано по факту, а не по документации: каждое
утверждение о конфликте сначала воспроизводилось парой настоящих
socket.bind на этой же машине.

Контрольные случаи не менее важны, чем ловля конфликтов: почини перекрытие
слишком широко — и проверка начнёт падать на 6881/tcp против 6881/udp,
то есть на штатной конфигурации qBittorrent.
"""

from __future__ import annotations

import pytest
from conftest import ROOT, load

check_ports = load(ROOT / "scripts" / "check_ports.py")
overlaps = check_ports.overlaps
find_conflicts = check_ports.find_conflicts


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("0.0.0.0", "127.0.0.1"),
        ("127.0.0.1", "0.0.0.0"),
        ("0.0.0.0", "192.168.3.53"),
        ("", "127.0.0.1"),
        ("0.0.0.0", ""),
        ("127.0.0.1", "127.0.0.1"),
        ("::", "::1"),
        ("::", "127.0.0.1"),
        ("::", "0.0.0.0"),
        ("[::]", "0.0.0.0"),
    ],
)
def test_перекрываются(a, b):
    assert overlaps(a, b), f"{a} и {b} займут один порт, но проверка этого не видит"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("127.0.0.1", "127.0.0.2"),
        ("192.168.3.53", "192.168.3.54"),
        ("127.0.0.1", "192.168.3.53"),
        ("0.0.0.0", "::1"),
    ],
)
def test_не_перекрываются(a, b):
    assert not overlaps(a, b), f"{a} и {b} уживаются, а проверка считает иначе"


def test_wildcard_против_конкретного_это_конфликт():
    entries = [
        ("2375", "tcp", "0.0.0.0", "media:alpha"),
        ("2375", "tcp", "127.0.0.1", "homeassistant:dockerproxy"),
    ]
    assert len(find_conflicts(entries)) == 1


def test_один_номер_в_tcp_и_udp_не_конфликт():
    """Штатная конфигурация qBittorrent: порт раздачи публикуется дважды."""
    entries = [
        ("6881", "tcp", "0.0.0.0", "media:qbittorrent"),
        ("6881", "udp", "0.0.0.0", "media:qbittorrent"),
    ]
    assert find_conflicts(entries) == []


def test_разные_конкретные_адреса_не_конфликт():
    entries = [
        ("8096", "tcp", "127.0.0.1", "a:one"),
        ("8096", "tcp", "192.168.3.53", "b:two"),
    ]
    assert find_conflicts(entries) == []


def test_точный_дубль_остаётся_конфликтом():
    entries = [
        ("9080", "tcp", "0.0.0.0", "a:one"),
        ("9080", "tcp", "0.0.0.0", "b:two"),
    ]
    assert len(find_conflicts(entries)) == 1


def test_wildcard_спорит_с_каждым_а_конкретные_между_собой_нет():
    """
    Три претендента на один порт дают две конфликтующие пары, а не три:
    wildcard перекрывает оба конкретных адреса, но 127.0.0.1 и адрес
    в локальной сети друг другу не мешают.
    """
    entries = [
        ("5055", "tcp", "0.0.0.0", "a:one"),
        ("5055", "tcp", "127.0.0.1", "b:two"),
        ("5055", "tcp", "192.168.3.53", "c:three"),
    ]
    pairs = find_conflicts(entries)
    assert len(pairs) == 2
    assert all("0.0.0.0" in (a[0], b[0]) for _, _, a, b in pairs)


def test_набор_портов_репозитория_не_конфликтует():
    """Регрессия: то, что мы предлагаем чужому человеку, само с собой не спорит."""
    entries = [
        ("9080", "tcp", "0.0.0.0", "media:qbittorrent"),
        ("6881", "tcp", "0.0.0.0", "media:qbittorrent"),
        ("6881", "udp", "0.0.0.0", "media:qbittorrent"),
        ("9696", "tcp", "0.0.0.0", "media:prowlarr"),
        ("7878", "tcp", "0.0.0.0", "media:radarr"),
        ("8191", "tcp", "0.0.0.0", "media:flaresolverr"),
        ("8096", "tcp", "0.0.0.0", "media:jellyfin"),
        ("7359", "udp", "0.0.0.0", "media:jellyfin"),
        ("5055", "tcp", "0.0.0.0", "media:seerr"),
        ("2375", "tcp", "127.0.0.1", "homeassistant:dockerproxy"),
    ]
    assert find_conflicts(entries) == []
