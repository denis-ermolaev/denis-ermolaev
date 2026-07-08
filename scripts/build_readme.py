#!/usr/bin/env python3
"""
build_readme.py — генератор README.md из DSL-описания бейджей.

Этот скрипт является ЕДИНСТВЕННЫМ источником истины для содержимого README.md.
Он заменяет собой README-DEV.md и AGENTS.md.

Что делает:
  1. Определяет бейджи через удобный DSL (contact_badge / stack_badge)
  2. Генерирует shield.io URL и определяет локальные пути для SVG
  3. Скачивает недостающие SVG (с кэшированием в assets/icons/.cache.json)
  4. Собирает markdown-текст с локальными путями к SVG
  5. Переносит waka-секцию из старого README.md (если есть)
  6. Записывает README.md

Использование:
    python scripts/build_readme.py
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════
#  КОНФИГУРАЦИЯ
# ═══════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_FILE = BASE_DIR / "assets" / "icons" / ".cache.json"
README_PATH = BASE_DIR / "README.md"

WAKA_START = "<!--START_SECTION:waka-->"
WAKA_END = "<!--END_SECTION:waka-->"

# Цвета shield.io по умолчанию
CONTACT_COLOR = "2b2b2b"  # цвет сообщения (правая часть)
CONTACT_LABEL_COLOR = "000000"  # цвет label (левая часть)
STACK_COLOR = "2b2b2b"
STACK_LABEL_COLOR = "000000"

# Маппинг label -> локальный путь для контактных бейджей
CONTACT_PATHS: Dict[str, str] = {
    "TELEGRAM": "assets/icons/telegram-badge.svg",
    "GITHUB": "assets/icons/github-badge.svg",
    "EMAIL": "assets/icons/email-badge.svg",
    "RESUME": "assets/icons/resume-badge.svg",
}

# Маппинг label -> локальный путь для стековых бейджей
STACK_PATHS: Dict[str, str] = {
    "Backend": "assets/icons/stack/backend.svg",
    "Data science": "assets/icons/stack/datascience.svg",
    "Tools": "assets/icons/stack/tools.svg",
    "Frontend": "assets/icons/stack/frontend.svg",
}


# ═══════════════════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════════════


def _shield_escape(text: str) -> str:
    """
    Экранирует текст для вставки в URL shield.io.

    Shield.io использует '-' как разделитель между label/message/color,
    а '_' как указатель пробела. Поэтому:
      - литеральный '-' -> '--'
      - литеральный '_' -> '__'
    Остальное — стандартный URL-encode.
    """
    text = text.replace("-", "--")
    text = text.replace("_", "__")
    # quote по умолчанию НЕ трогает '-', '_', '.', '~'
    return urllib.parse.quote(text)


def _make_shield_url(
    label: str,
    message: str,
    color: str,
    *,
    style: str = "flat-square",
    logo: Optional[str] = None,
    logo_color: Optional[str] = None,
    label_color: Optional[str] = None,
) -> str:
    """Собирает полный URL для shield.io бейджа."""
    el = _shield_escape(label)
    em = _shield_escape(message)
    url = f"https://img.shields.io/badge/{el}-{em}-{color}"
    params: List[str] = [f"style={style}"]
    if logo:
        params.append(f"logo={logo}")
    if logo_color:
        params.append(f"logoColor={logo_color}")
    if label_color:
        params.append(f"labelColor={label_color}")
    return url + "?" + "&".join(params)


def _contact_local_path(label: str) -> str:
    """Определяет локальный путь для контактного бейджа."""
    if label in CONTACT_PATHS:
        return CONTACT_PATHS[label]
    safe = label.lower().replace(" ", "-")
    return f"assets/icons/{safe}-badge.svg"


def _stack_local_path(label: str) -> str:
    """Определяет локальный путь для стекового бейджа."""
    if label in STACK_PATHS:
        return STACK_PATHS[label]
    safe = label.lower().replace(" ", "-")
    return f"assets/icons/stack/{safe}.svg"


# ═══════════════════════════════════════════════════════════════
#  DSL — БЕЙДЖИ
# ═══════════════════════════════════════════════════════════════


@dataclass
class Badge:
    """Один бейдж: URL для скачивания + локальный путь + markdown."""

    shield_url: str  # полный shield.io URL
    local_path: str  # локальный путь к SVG (относительно корня)
    markdown: str  # готовая markdown-строка (с локальным путём)
    label: str = field(repr=False)  # для отладки


def contact_badge(
    label: str,
    message: str,
    logo: str,
    url: str,
    *,
    color: str = CONTACT_COLOR,
    label_color: str = CONTACT_LABEL_COLOR,
) -> Badge:
    """Контактный бейдж (с логотипом, for-the-badge, ссылка)."""
    shield_url = _make_shield_url(
        label,
        message,
        color,
        style="for-the-badge",
        logo=logo,
        logo_color="white",
        label_color=label_color,
    )
    local_path = _contact_local_path(label)
    md = f"[![{label}]({local_path})]({url})"
    return Badge(shield_url=shield_url, local_path=local_path, markdown=md, label=label)


def stack_badge(
    label: str,
    message: str,
    *,
    color: str = STACK_COLOR,
    label_color: str = STACK_LABEL_COLOR,
    url: str = "#",
) -> Badge:
    """Стековый бейдж (без логотипа, flat-square, без ссылки)."""
    shield_url = _make_shield_url(
        label,
        message,
        color,
        style="flat-square",
        label_color=label_color,
    )
    local_path = _stack_local_path(label)
    md = f"[![{label}]({local_path})]({url})"
    return Badge(shield_url=shield_url, local_path=local_path, markdown=md, label=label)


# ═══════════════════════════════════════════════════════════════
#  КЭШ И СКАЧИВАНИЕ SVG
# ═══════════════════════════════════════════════════════════════

_CACHE_VERSION = 1


def _load_cache() -> dict:
    """Загружает кэш из файла."""
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if data.get("version") == _CACHE_VERSION:
                return data
        except (json.JSONDecodeError, KeyError):
            pass
    return {"version": _CACHE_VERSION, "entries": {}}


def _save_cache(cache: dict) -> None:
    """Сохраняет кэш в файл."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _url_sha256(url: str) -> str:
    """SHA256 хеш URL."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _download_svg(url: str, local_path: str) -> bool:
    """
    Скачивает SVG по URL и сохраняет в local_path (относительно корня).
    Возвращает True при успехе.
    """
    full_path = BASE_DIR / local_path
    full_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "build_readme.py/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        full_path.write_bytes(data)
        print(f"  ✓ {local_path}  ({len(data)} bytes)")
        return True
    except Exception as exc:
        print(f"  ✗ {local_path}  — {exc}")
        return False


def _sync_cache(badges: List[Badge], cache: dict) -> None:
    """
    Сверяет URL бейджей с кэшем.
    Для новых и изменившихся URL — скачивает SVG и добавляет запись в кэш.
    """
    entries = cache.setdefault("entries", {})

    for badge in badges:
        if badge.shield_url in entries:
            # Уже в кэше — пропускаем
            continue

        print(f"  → {badge.label}: {badge.shield_url[:70]}...")
        if _download_svg(badge.shield_url, badge.local_path):
            entries[badge.shield_url] = {
                "local_path": badge.local_path,
                "url_hash": _url_sha256(badge.shield_url),
                "downloaded_at": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            }


# ═══════════════════════════════════════════════════════════════
#  WAKA-СЕКЦИЯ
# ═══════════════════════════════════════════════════════════════


def _extract_waka(text: str) -> Optional[str]:
    """Извлекает waka-секцию с маркерами. Возвращает None, если секции нет."""
    pattern = rf"{re.escape(WAKA_START)}.*?{re.escape(WAKA_END)}"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(0) if m else None


def _inject_waka(text: str, waka_section: Optional[str]) -> str:
    """
    Вставляет waka-секцию в текст.
    Если в тексте уже есть маркеры — заменяет содержимое.
    Если маркеров нет — добавляет секцию в конец.
    """
    if waka_section is None:
        return text

    if WAKA_START in text and WAKA_END in text:
        return re.sub(
            rf"{re.escape(WAKA_START)}.*?{re.escape(WAKA_END)}",
            waka_section,
            text,
            flags=re.DOTALL,
        )
    else:
        return text.rstrip() + "\n\n" + waka_section + "\n"


# ═══════════════════════════════════════════════════════════════
#  ФОРМИРОВАНИЕ СОДЕРЖИМОГО README
# ═══════════════════════════════════════════════════════════════


def _define_badges() -> Tuple[List[Badge], str]:
    """
    ЕДИНСТВЕННОЕ МЕСТО, ГДЕ ОПРЕДЕЛЯЮТСЯ БЕЙДЖИ.
    Меняйте содержимое README здесь.

    Возвращает:
      (all_badges, markdown_text)
    """
    # ── Контактные бейджи ──────────────────────────────────
    tg = contact_badge(
        "TELEGRAM",
        "@first_millennium",
        "telegram",
        "https://t.me/millenniumxx",
    )
    gh = contact_badge(
        "GITHUB",
        "github.com",
        "github",
        "https://github.com/denis-ermolaev",
    )
    em = contact_badge(
        "EMAIL",
        "denis-ermolaev@internet.ru",
        "gmail",
        "mailto:denis-ermolaev@internet.ru",
    )
    rs = contact_badge(
        "RESUME",
        "rxresu.me",
        "googledocs",
        "https://rxresu.me/denis-ermolaev/resume-python-developer",
    )

    # ── Стековые бейджи ────────────────────────────────────
    be = stack_badge(
        "Backend",
        "Python | FastAPI | Django REST Framework | Flask | PostgreSQL",
    )
    ds = stack_badge(
        "Data science",
        "Pandas | NumPy | Scikit-Learn | Visualization",
    )
    tl = stack_badge(
        "Tools",
        "Linux | Git | Docker | PyTest | pre-commit | Ruff | ty | uv",
    )
    fe = stack_badge(
        "Frontend",
        "TypeScript | NextJS | React | Redux | Tailwind | shadcn/ui",
    )

    all_badges = [tg, gh, em, rs, be, ds, tl, fe]

    # ═══ СБОРКА MARKDOWN ═══
    md = f"""{tg.markdown}{gh.markdown}

{em.markdown}{rs.markdown}

### Core Stack

{be.markdown}

{ds.markdown}

{tl.markdown}

{fe.markdown}

{WAKA_START}
{WAKA_END}
"""
    return all_badges, md


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════


def main() -> None:
    print("=" * 60)
    print("  build_readme.py — генерация README.md")
    print("=" * 60)

    # 1. Определяем бейджи и markdown
    print("\n📝 Определение бейджей...")
    badges, new_markdown = _define_badges()
    print(f"   Всего бейджей: {len(badges)}")
    for b in badges:
        print(f"   • {b.label:20s} → {b.local_path}")

    # 2. Загружаем кэш
    print("\n💾 Загрузка кэша...")
    cache = _load_cache()
    print(f"   Записей в кэше: {len(cache.get('entries', {}))}")

    # 3. Скачиваем новые SVG
    print("\n📥 Синхронизация SVG...")
    _sync_cache(badges, cache)
    print("   Готово.")

    # 4. Перенос waka-секции из старого README.md
    print("\n🔄 Перенос waka-секции...")
    old_waka = None
    if README_PATH.exists():
        old_text = README_PATH.read_text(encoding="utf-8")
        old_waka = _extract_waka(old_text)
        if old_waka:
            print("   ✓ waka-секция найдена, переношу...")
        else:
            print("   • waka-секция не найдена")
    else:
        print("   • README.md ещё не существует")
    new_markdown = _inject_waka(new_markdown, old_waka)

    # 5. Запись README.md
    print(f"\n📄 Запись {README_PATH.relative_to(BASE_DIR)}...")
    README_PATH.write_text(new_markdown, encoding="utf-8")
    print(f"   ✓ {len(new_markdown)} символов")

    # 6. Сохранение кэша
    print("\n💾 Сохранение кэша...")
    _save_cache(cache)
    print(f"   ✓ {CACHE_FILE.relative_to(BASE_DIR)}")

    print("\n" + "=" * 60)
    print("  ✅ Готово! README.md сгенерирован.")
    print("=" * 60)


if __name__ == "__main__":
    main()
