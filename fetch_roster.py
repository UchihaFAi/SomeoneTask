#!/usr/bin/env python3
"""
Выгрузка ростера SWGOH через локально запущенный SWGoH Comlink.

Ally Code НЕ хардкодится: он передаётся аргументом командной строки
или через переменную окружения SWGOH_ALLY_CODE.

Пример:
    ./swgoh-comlink --name my-roster-export --port 3000 &
    python fetch_roster.py 123456789
    # или
    SWGOH_ALLY_CODE=123456789 python fetch_roster.py

Результат:
    roster_raw.json    — сырой ответ /player (сохраняется всегда)
    roster_clean.json  — персонажи (combatType == 1)
    ships_clean.json   — корабли (combatType == 2)

Схема ответа сверена с официальной wiki проекта:
    https://github.com/swgoh-utils/swgoh-comlink/wiki/Player-Data
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("Нужна библиотека requests:  pip install requests")


DEFAULT_URL = "http://localhost:3000"

# combatType из коллекции units в /data: 1 — персонаж, 2 — корабль.
COMBAT_TYPE_CHARACTER = 1
COMBAT_TYPE_SHIP = 2

# relic.currentTier — это enum со смещением: реальный уровень реликвии = currentTier - 2.
# Значения <= 0 после смещения означают, что реликвия ещё не открыта.
RELIC_TIER_OFFSET = 2

# Имена булевых полей, которыми gameData помечает zeta/omicron-тиры способностей.
# Точная схема skill-коллекции не задокументирована в wiki, поэтому имена полей
# определяются во время выполнения по фактическим данным (см. discover_tier_flags).
ZETA_FLAG_CANDIDATES = ("isZetaTier", "isZeta", "zetaTier")
OMICRON_FLAG_CANDIDATES = ("isOmicronTier", "isOmicron", "omicronTier")


class ComlinkError(RuntimeError):
    pass


class Comlink:
    """Тонкий клиент к локальному Comlink."""

    def __init__(self, base_url: str = DEFAULT_URL, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def _post(self, endpoint: str, body: dict[str, Any]) -> Any:
        url = f"{self.base_url}{endpoint}"
        try:
            resp = self.session.post(url, json=body, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ComlinkError(f"Не удалось обратиться к {url}: {exc}") from exc
        if resp.status_code != 200:
            raise ComlinkError(
                f"{endpoint} вернул HTTP {resp.status_code}: {resp.text[:400]}"
            )
        return resp.json()

    def health_check(self) -> dict[str, Any]:
        """Проверяем, что сервис поднялся. /metadata — POST с пустым телом."""
        return self._post("/metadata", {})

    def player(self, ally_code: str) -> dict[str, Any]:
        return self._post("/player", {"payload": {"allyCode": ally_code}})

    def game_data(self, version: str) -> dict[str, Any]:
        """
        Полные игровые данные. includePveUnits=False убирает ~30 МБ NPC-юнитов.
        Некоторые версии Comlink не принимают лишние ключи payload, поэтому
        пробуем варианты от самого экономного к самому простому.
        """
        variants = [
            {"payload": {"version": version, "includePveUnits": False}},
            {"payload": {"version": version, "includePveUnits": False}, "enums": False},
            {"payload": {"version": version}},
        ]
        last_error: Exception | None = None
        for body in variants:
            try:
                return self._post("/data", body)
            except ComlinkError as exc:
                last_error = exc
        raise ComlinkError(f"/data не отдал данные: {last_error}")


# --------------------------------------------------------------------------
# Разбор gameData
# --------------------------------------------------------------------------


def _as_list(container: Any, *keys: str) -> list[dict[str, Any]]:
    """Достаёт список по первому подходящему ключу (схема гуляет между версиями)."""
    if isinstance(container, list):
        return [x for x in container if isinstance(x, dict)]
    if not isinstance(container, dict):
        return []
    for key in keys:
        value = container.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def build_combat_type_map(game_data: dict[str, Any]) -> dict[str, int]:
    """baseId -> combatType. Юнит присутствует на каждой редкости, нам хватит любой."""
    units = _as_list(game_data, "units", "unit")
    mapping: dict[str, int] = {}
    for unit in units:
        base_id = unit.get("baseId")
        combat_type = unit.get("combatType")
        if isinstance(base_id, str) and isinstance(combat_type, int):
            mapping.setdefault(base_id, combat_type)
    return mapping


def discover_tier_flags(skills: Iterable[dict[str, Any]]) -> tuple[str | None, str | None]:
    """
    Находит фактические имена полей zeta/omicron в tier-записях способностей.
    Возвращает (zeta_field, omicron_field); None означает «в данных такого поля нет».
    """
    seen: set[str] = set()
    for skill in skills:
        for tier in _as_list(skill.get("tier")):
            seen.update(k for k, v in tier.items() if isinstance(v, bool))
    zeta = next((c for c in ZETA_FLAG_CANDIDATES if c in seen), None)
    omicron = next((c for c in OMICRON_FLAG_CANDIDATES if c in seen), None)
    return zeta, omicron


def build_skill_flag_map(
    game_data: dict[str, Any],
) -> tuple[dict[str, dict[str, set[int]]], str | None, str | None]:
    """
    skillId -> {"zeta": {индексы тиров}, "omicron": {индексы тиров}}.

    Индекс — позиция в массиве tier определения способности. Считаем, что
    у игрока с skill.tier == N открыты записи с индексами < N (см. README,
    раздел про сверку количества зет).
    """
    skills = _as_list(game_data, "skill")
    zeta_field, omicron_field = discover_tier_flags(skills)
    if not zeta_field and not omicron_field:
        return {}, None, None

    flags: dict[str, dict[str, set[int]]] = {}
    for skill in skills:
        skill_id = skill.get("id")
        if not isinstance(skill_id, str):
            continue
        zetas: set[int] = set()
        omicrons: set[int] = set()
        for index, tier in enumerate(_as_list(skill.get("tier"))):
            if zeta_field and tier.get(zeta_field):
                zetas.add(index)
            if omicron_field and tier.get(omicron_field):
                omicrons.add(index)
        if zetas or omicrons:
            flags[skill_id] = {"zeta": zetas, "omicron": omicrons}
    return flags, zeta_field, omicron_field


# --------------------------------------------------------------------------
# Разбор ростера
# --------------------------------------------------------------------------


def base_id_of(unit: dict[str, Any]) -> str | None:
    """definitionId вида "DARTHREVAN:SEVEN_STAR" -> "DARTHREVAN"."""
    definition_id = unit.get("definitionId") or unit.get("defId")
    if not isinstance(definition_id, str) or not definition_id:
        return None
    return definition_id.split(":")[0]


def relic_tier_of(unit: dict[str, Any]) -> int | None:
    """Реальный уровень реликвии, либо None если реликвий нет/поле отсутствует."""
    relic = unit.get("relic")
    if not isinstance(relic, dict):
        return None
    raw = relic.get("currentTier")
    if not isinstance(raw, int):
        return None
    actual = raw - RELIC_TIER_OFFSET
    return actual if actual > 0 else 0


def mods_summary(unit: dict[str, Any]) -> dict[str, Any] | None:
    """
    Сводка по надетым модам строго из полей, которые реально присутствуют
    в ответе. Кодировка set/slot внутри definitionId мода не документирована,
    поэтому она НЕ расшифровывается — вместо выдуманных названий сетов
    отдаём распределение по фактическим числовым полям.
    """
    equipped = _as_list(unit.get("equippedStatMod"))
    if not equipped:
        return None

    summary: dict[str, Any] = {"count": len(equipped)}
    for field, out_key in (("level", "by_level"), ("tier", "by_tier"), ("rarity", "by_rarity")):
        buckets: dict[str, int] = {}
        for mod in equipped:
            value = mod.get(field)
            if isinstance(value, int):
                buckets[str(value)] = buckets.get(str(value), 0) + 1
        if buckets:
            summary[out_key] = dict(sorted(buckets.items(), key=lambda kv: int(kv[0])))
    return summary


def abilities_of(
    unit: dict[str, Any], skill_flags: dict[str, dict[str, set[int]]]
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Возвращает (zetas, omicrons, все способности с игровым тиром)."""
    zetas: list[str] = []
    omicrons: list[str] = []
    skills: list[dict[str, Any]] = []

    for skill in _as_list(unit.get("skill")):
        skill_id = skill.get("id")
        tier = skill.get("tier")
        if not isinstance(skill_id, str) or not isinstance(tier, int):
            continue
        # Wiki: к значению tier нужно прибавить 2, чтобы получить внутриигровой тир.
        skills.append({"id": skill_id, "tier": tier + 2})

        unlocked = set(range(tier))
        flags = skill_flags.get(skill_id)
        if not flags:
            continue
        if flags["zeta"] & unlocked:
            zetas.append(skill_id)
        if flags["omicron"] & unlocked:
            omicrons.append(skill_id)

    return sorted(zetas), sorted(omicrons), skills


def clean_unit(
    unit: dict[str, Any],
    skill_flags: dict[str, dict[str, set[int]]],
    include_skills: bool,
    has_gamedata: bool,
) -> dict[str, Any]:
    zetas, omicrons, skills = abilities_of(unit, skill_flags)

    entry: dict[str, Any] = {
        "id": base_id_of(unit),
        "stars": unit.get("currentRarity"),
        "level": unit.get("currentLevel"),
        "gear_tier": unit.get("currentTier"),
        "relic_tier": relic_tier_of(unit),
    }

    # Без gameData нельзя отличить zeta от обычного апгрейда — не выдумываем,
    # а честно отдаём null.
    if has_gamedata:
        entry["zetas"] = zetas
        entry["omicrons"] = omicrons
    else:
        entry["zetas"] = None
        entry["omicrons"] = None

    if include_skills:
        entry["skills"] = skills

    mods = mods_summary(unit)
    if mods:
        entry["mods"] = mods

    return {k: v for k, v in entry.items() if v is not None or k in ("zetas", "omicrons", "relic_tier")}


# --------------------------------------------------------------------------
# Вывод
# --------------------------------------------------------------------------


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def print_stats(characters: list[dict], ships: list[dict], unknown: list[dict]) -> None:
    total = len(characters)
    seven_star = sum(1 for c in characters if c.get("stars") == 7)
    with_relic = sum(1 for c in characters if (c.get("relic_tier") or 0) > 0)
    gear13 = sum(1 for c in characters if (c.get("gear_tier") or 0) >= 13)
    zetas = sum(len(c.get("zetas") or []) for c in characters)
    omicrons = sum(len(c.get("omicrons") or []) for c in characters)

    print("\n=== Статистика ===")
    print(f"Персонажей:            {total}")
    print(f"  7 звёзд:             {seven_star}")
    print(f"  Gear 13+:            {gear13}")
    print(f"  С реликвией (>0):    {with_relic}")
    print(f"  Всего зет:           {zetas}")
    print(f"  Всего омикронов:     {omicrons}")
    print(f"Кораблей:              {len(ships)}")
    if unknown:
        print(f"Не удалось определить тип: {len(unknown)}")

    relic_hist: dict[int, int] = {}
    for c in characters:
        tier = c.get("relic_tier") or 0
        if tier > 0:
            relic_hist[tier] = relic_hist.get(tier, 0) + 1
    if relic_hist:
        breakdown = ", ".join(f"R{t}: {n}" for t, n in sorted(relic_hist.items()))
        print(f"  По реликвиям:        {breakdown}")


def validate(clean_total: int, raw_total: int, entries: list[dict]) -> bool:
    ok = True
    if clean_total != raw_total:
        print(f"[!] Расхождение: в сыром ответе {raw_total}, в чистых файлах {clean_total}")
        ok = False

    ids = [e.get("id") for e in entries]
    duplicates = {i for i in ids if i and ids.count(i) > 1}
    if duplicates:
        print(f"[!] Дубликаты id: {sorted(duplicates)}")
        ok = False

    missing = sum(1 for i in ids if not i)
    if missing:
        print(f"[!] Записей без id: {missing}")
        ok = False

    if ok:
        print("[ok] Проверка пройдена: количество совпадает, дублей нет.")
    return ok


# --------------------------------------------------------------------------


def resolve_ally_code(cli_value: str | None) -> str:
    ally_code = cli_value or os.environ.get("SWGOH_ALLY_CODE") or ""
    ally_code = ally_code.replace("-", "").strip()
    if not ally_code.isdigit():
        sys.exit(
            "Ally Code не задан или задан неверно.\n"
            "Передайте его аргументом:   python fetch_roster.py 123456789\n"
            "или переменной окружения:   SWGOH_ALLY_CODE=123456789 python fetch_roster.py"
        )
    return ally_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Выгрузка ростера SWGOH через Comlink.")
    parser.add_argument("ally_code", nargs="?", help="Ally Code без дефисов (или SWGOH_ALLY_CODE)")
    parser.add_argument("--url", default=os.environ.get("COMLINK_URL", DEFAULT_URL),
                        help=f"адрес Comlink (по умолчанию {DEFAULT_URL})")
    parser.add_argument("--out-dir", default=".", help="куда складывать файлы")
    parser.add_argument("--no-gamedata", action="store_true",
                        help="не запрашивать /data (быстрее, но без зет/омикронов и без деления на корабли)")
    parser.add_argument("--include-skills", action="store_true",
                        help="включить полный список способностей с тирами")
    parser.add_argument("--timeout", type=int, default=120, help="таймаут запросов, сек")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ally_code = resolve_ally_code(args.ally_code)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    client = Comlink(args.url, timeout=args.timeout)

    print(f"Проверяю Comlink на {args.url} ...")
    try:
        metadata = client.health_check()
    except ComlinkError as exc:
        print(f"[!] Comlink недоступен: {exc}", file=sys.stderr)
        print("    Запустите его:  ./swgoh-comlink --name my-roster-export --port 3000", file=sys.stderr)
        return 1
    print("[ok] Comlink отвечает.")

    # --- /player: сырой ответ сохраняем в первую очередь -------------------
    print(f"Запрашиваю данные игрока (ally code ***{ally_code[-3:]}) ...")
    try:
        player = client.player(ally_code)
    except ComlinkError as exc:
        print(f"[!] Не удалось получить игрока: {exc}", file=sys.stderr)
        return 1

    raw_path = out_dir / "roster_raw.json"
    write_json(raw_path, player)
    print(f"[ok] Сырой ответ сохранён: {raw_path}")

    roster = _as_list(player.get("rosterUnit") or player.get("roster") or [])
    if not roster:
        print("[!] В ответе нет rosterUnit/roster — посмотрите roster_raw.json вручную.", file=sys.stderr)
        return 1
    print(f"    Юнитов в ростере: {len(roster)}")

    # --- /data: combatType + флаги zeta/omicron ----------------------------
    combat_types: dict[str, int] = {}
    skill_flags: dict[str, dict[str, set[int]]] = {}
    has_gamedata = False

    if not args.no_gamedata:
        version = (metadata or {}).get("latestGamedataVersion")
        if not version:
            print("[!] В /metadata нет latestGamedataVersion — пропускаю /data.")
        else:
            print(f"Загружаю игровые данные (version {version}) — это может занять минуту ...")
            try:
                game_data = client.game_data(version)
            except ComlinkError as exc:
                print(f"[!] /data недоступен ({exc}). Продолжаю без зет/омикронов.")
            else:
                combat_types = build_combat_type_map(game_data)
                skill_flags, zeta_field, omicron_field = build_skill_flag_map(game_data)
                has_gamedata = bool(skill_flags)
                del game_data
                print(f"[ok] combatType известен для {len(combat_types)} юнитов.")
                if has_gamedata:
                    print(f"[ok] Флаги способностей: zeta='{zeta_field}', omicron='{omicron_field}' "
                          f"({len(skill_flags)} способностей).")
                else:
                    print("[!] В gameData не нашлись поля zeta/omicron — эти поля будут null.")

    # --- Сборка ------------------------------------------------------------
    characters: list[dict] = []
    ships: list[dict] = []
    unknown: list[dict] = []

    for unit in roster:
        entry = clean_unit(unit, skill_flags, args.include_skills, has_gamedata)
        combat_type = combat_types.get(entry.get("id") or "")
        if combat_type == COMBAT_TYPE_SHIP:
            ships.append(entry)
        elif combat_type == COMBAT_TYPE_CHARACTER:
            characters.append(entry)
        else:
            unknown.append(entry)

    # Без gameData тип неизвестен — кладём всё в один файл, ничего не выдумывая.
    if not combat_types:
        characters, unknown = unknown, []
        print("[!] combatType неизвестен — корабли не отделены, всё в roster_clean.json.")
    elif unknown:
        characters.extend(unknown)
        print(f"[!] {len(unknown)} юнитов без combatType добавлены в roster_clean.json.")

    characters.sort(key=lambda e: e.get("id") or "")
    ships.sort(key=lambda e: e.get("id") or "")

    clean_path = out_dir / "roster_clean.json"
    write_json(clean_path, characters)
    print(f"[ok] Персонажи сохранены: {clean_path} ({len(characters)})")

    if ships:
        ships_path = out_dir / "ships_clean.json"
        write_json(ships_path, ships)
        print(f"[ok] Корабли сохранены: {ships_path} ({len(ships)})")

    print_stats(characters, ships, unknown)
    ok = validate(len(characters) + len(ships), len(roster), characters + ships)

    if characters:
        print("\nПример записи:")
        print(json.dumps(characters[0], ensure_ascii=False, indent=2))

    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
