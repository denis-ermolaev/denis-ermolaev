#!/usr/bin/env python3
"""
build_readme.py — генератор README.md из README-DEV.md.

Что делает:
  1. Читает README-DEV.md (источник истины для контента)
  2. Находит все shield.io и custom-icon-badges.demolab.com URL в markdown
  3. Скачивает недостающие SVG в assets/icons/ (с кэшированием)
  4. Заменяет внешние URL на локальные пути
  5. Записывает результат в README.md

Использование:
    python scripts/build_readme.py

Условные обозначения:
    README-DEV.md  —  исходный файл с внешними ссылками на бейджи
    README.md      —  результат (ссылки заменены на локальные SVG)
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set

# ═══════════════════════════════════════════════════════════════
#  КОНФИГУРАЦИЯ
# ═══════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_FILE = BASE_DIR / "assets" / "icons" / ".cache.json"
DEV_README_PATH = BASE_DIR / "README-DEV.md"
README_PATH = BASE_DIR / "README.md"

# Домены, SVG с которых нужно скачивать локально
ALLOWED_DOMAINS: Set[str] = {
    "img.shields.io",
    "custom-icon-badges.demolab.com",
}

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


def _local_path_for_url(url: str, cache: dict) -> str:
    """
    Определяет локальный путь для URL.

    Если URL уже есть в кэше — возвращает сохранённый путь.
    Иначе генерирует путь на основе хэша URL.
    """
    entries = cache.get("entries", {})
    if url in entries:
        return entries[url]["local_path"]

    # Генерируем короткий хэш (12 символов) для имени файла
    h = _url_sha256(url)[:12]
    return f"assets/icons/badge-{h}.svg"


# ═══════════════════════════════════════════════════════════════
#  ПАРСИНГ README-DEV.md
# ═══════════════════════════════════════════════════════════════

# Регекспы для поиска URL в markdown ![alt](url) и HTML <img src="url">
_IMAGE_RE = re.compile(r"!\[.*?\]\(([^)]+)\)")
_HTML_IMG_RE = re.compile(r'<img\s[^>]*src="([^"]+)"')


def _find_badge_urls(text: str) -> List[str]:
    """
    Находит все URL изображений из разрешённых доменов.

    Ищет конструкцию ![alt](url) (как отдельно, так и внутри
    Markdown-ссылки [![alt](url)](link)), а также <img src="url">.
    """
    found: List[str] = []
    seen: Set[str] = set()

    for pattern in (_IMAGE_RE, _HTML_IMG_RE):
        for match in pattern.finditer(text):
            url = match.group(1)
            if url in seen:
                continue

            # Проверяем, относится ли URL к разрешённому домену
            if any(domain in url for domain in ALLOWED_DOMAINS):
                found.append(url)
                seen.add(url)

    return found


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════


def main() -> None:
    print("=" * 60)
    print("  build_readme.py — генерация README.md из README-DEV.md")
    print("=" * 60)

    # 1. Проверяем, что README-DEV.md существует
    if not DEV_README_PATH.exists():
        print(f"\n❌ Ошибка: {DEV_README_PATH.relative_to(BASE_DIR)} не найден!")
        print("   Создайте README-DEV.md с markdown-контентом и внешними URL бейджей.")
        return

    # 2. Читаем исходный контент
    print(f"\n📖 Чтение {DEV_README_PATH.relative_to(BASE_DIR)}...")
    content = DEV_README_PATH.read_text(encoding="utf-8")
    print(f"   {len(content)} символов")

    # 3. Находим все badge URL
    print(f"\n🔍 Поиск badge URL (домены: {', '.join(ALLOWED_DOMAINS)})...")
    urls = _find_badge_urls(content)
    print(f"   Найдено URL: {len(urls)}")
    for url in urls:
        print(f"   • {url[:80]}...")

    if not urls:
        print("\n⚠️  Badge URL не найдены. README.md будет скопирован как есть.")

    # 4. Загружаем кэш и скачиваем новые SVG
    print(f"\n💾 Загрузка кэша ({CACHE_FILE.relative_to(BASE_DIR)})...")
    cache = _load_cache()
    cached_count = len(cache.get("entries", {}))
    print(f"   Записей в кэше: {cached_count}")

    # Карта: URL -> локальный путь
    url_to_local: Dict[str, str] = {}

    print("\n📥 Синхронизация SVG...")
    new_downloads = 0
    for url in urls:
        local_path = _local_path_for_url(url, cache)
        url_to_local[url] = local_path

        entries = cache.setdefault("entries", {})

        # Пропускаем, если уже в кэше и файл существует
        if url in entries:
            if (BASE_DIR / entries[url]["local_path"]).exists():
                continue
            else:
                print(f"  → {url[:60]}... (файл отсутствует, перекачиваю)")

        # Скачиваем
        print(f"  → {url[:60]}...")
        if _download_svg(url, local_path):
            entries[url] = {
                "local_path": local_path,
                "url_hash": _url_sha256(url),
                "downloaded_at": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            }
            new_downloads += 1

    print(f"   Скачано новых: {new_downloads}, из кэша: {len(urls) - new_downloads}")

    # 5. Заменяем внешние URL на локальные пути
    print("\n🔄 Замена URL на локальные пути...")
    for url, local_path in url_to_local.items():
        content = content.replace(url, local_path)
    print(f"   Заменено URL: {len(url_to_local)}")

    # 6. Записываем README.md
    print(f"\n📄 Запись {README_PATH.relative_to(BASE_DIR)}...")
    README_PATH.write_text(content, encoding="utf-8")
    print(f"   ✓ {len(content)} символов")

    # 7. Сохраняем кэш
    if new_downloads > 0:
        print("\n💾 Сохранение кэша...")
        _save_cache(cache)
        print(f"   ✓ {CACHE_FILE.relative_to(BASE_DIR)}")
    else:
        print("\n💾 Кэш не изменён, пропускаю сохранение.")

    print("\n" + "=" * 60)
    print("  ✅ Готово! README.md сгенерирован.")
    print("=" * 60)


if __name__ == "__main__":
    main()
