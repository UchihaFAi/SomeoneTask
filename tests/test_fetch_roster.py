"""
Тесты разбора ростера.

Фиксируют правила из wiki проекта, на которых держится парсинг:
  * relic.currentTier — enum со смещением -2
  * skill[].tier — смещение +2 до внутриигрового тира
  * combatType: 1 — персонаж, 2 — корабль, берётся из /data

Запуск:  python -m unittest discover -s tests -v
Сети не требует: поднимает мок Comlink на localhost.
"""

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_roster as fr  # noqa: E402


# ---------------------------------------------------------------- фикстуры

GAMEDATA = {
    "units": [
        {"baseId": "DARTHREVAN", "combatType": 1, "rarity": 7},
        {"baseId": "DARTHREVAN", "combatType": 1, "rarity": 1},
        {"baseId": "GRANDMASTERYODA", "combatType": 1},
        {"baseId": "COMMANDERLUKESKYWALKER", "combatType": 1},
        {"baseId": "MILLENNIUMFALCON", "combatType": 2},
        {"baseId": "TIEFIGHTERPILOT", "combatType": 2},
    ],
    "skill": [
        {"id": "skill_revan_unique", "tier": [
            {"isZetaTier": False, "isOmicronTier": False},
            {"isZetaTier": False, "isOmicronTier": False},
            {"isZetaTier": True, "isOmicronTier": False},
        ]},
        {"id": "skill_revan_leader", "tier": [
            {"isZetaTier": False, "isOmicronTier": False},
            {"isZetaTier": True, "isOmicronTier": False},
            {"isZetaTier": False, "isOmicronTier": True},
        ]},
        {"id": "skill_yoda_basic", "tier": [{"isZetaTier": False, "isOmicronTier": False}]},
    ],
}

PLAYER = {
    "name": "TestPlayer",
    "allyCode": "999999999",
    "rosterUnit": [
        {
            "id": "guid-1", "definitionId": "DARTHREVAN:SEVEN_STAR",
            "currentRarity": 7, "currentLevel": 85, "currentTier": 13,
            "relic": {"currentTier": 11},
            "skill": [
                {"id": "skill_revan_unique", "tier": 3},   # открыты 0,1,2 -> зета
                {"id": "skill_revan_leader", "tier": 2},   # открыты 0,1  -> зета, омикрон ещё нет
            ],
            "equippedStatMod": [
                {"id": "m1", "definitionId": "127", "level": 15, "tier": 5, "rarity": 6},
                {"id": "m2", "definitionId": "158", "level": 15, "tier": 5, "rarity": 6},
                {"id": "m3", "definitionId": "112", "level": 12, "tier": 4, "rarity": 5},
            ],
        },
        {
            "id": "guid-2", "definitionId": "GRANDMASTERYODA:SEVEN_STAR",
            "currentRarity": 7, "currentLevel": 85, "currentTier": 12,
            "relic": {"currentTier": 1},                   # реликвия не открыта
            "skill": [{"id": "skill_yoda_basic", "tier": 4}],
        },
        {
            "id": "guid-3", "definitionId": "COMMANDERLUKESKYWALKER:SEVEN_STAR",
            "currentRarity": 6, "currentLevel": 80, "currentTier": 11,
        },
        {
            "id": "guid-4", "definitionId": "MILLENNIUMFALCON:SEVEN_STAR",
            "currentRarity": 7, "currentLevel": 85, "currentTier": 1,
        },
        {
            "id": "guid-5", "definitionId": "TIEFIGHTERPILOT:FIVE_STAR",
            "currentRarity": 5, "currentLevel": 78, "currentTier": 1,
        },
        {
            "id": "guid-6", "definitionId": "UNKNOWNNEWUNIT:SEVEN_STAR",
            "currentRarity": 7, "currentLevel": 85, "currentTier": 13,
            "relic": {"currentTier": 9},
        },
    ],
}

METADATA = {"latestGamedataVersion": "0.99.9:TESTVERSION"}


# ------------------------------------------------------------ юнит-тесты


class TestBaseId(unittest.TestCase):
    def test_strips_rarity_suffix(self):
        self.assertEqual(fr.base_id_of({"definitionId": "DARTHREVAN:SEVEN_STAR"}), "DARTHREVAN")

    def test_accepts_legacy_defid(self):
        self.assertEqual(fr.base_id_of({"defId": "JEDIKNIGHTREVAN"}), "JEDIKNIGHTREVAN")

    def test_missing_id_is_none(self):
        self.assertIsNone(fr.base_id_of({}))
        self.assertIsNone(fr.base_id_of({"definitionId": ""}))


class TestRelicTier(unittest.TestCase):
    """Wiki: 'you can -2 from it to get the actual Relic Level'."""

    def test_offset_applied(self):
        self.assertEqual(fr.relic_tier_of({"relic": {"currentTier": 11}}), 9)
        self.assertEqual(fr.relic_tier_of({"relic": {"currentTier": 9}}), 7)

    def test_unlocked_floor_is_zero(self):
        # Реликвия ещё не открыта — не уходим в минус.
        self.assertEqual(fr.relic_tier_of({"relic": {"currentTier": 1}}), 0)
        self.assertEqual(fr.relic_tier_of({"relic": {"currentTier": 2}}), 0)

    def test_absent_relic_is_none(self):
        self.assertIsNone(fr.relic_tier_of({}))
        self.assertIsNone(fr.relic_tier_of({"relic": None}))
        self.assertIsNone(fr.relic_tier_of({"relic": {}}))


class TestSkillFlags(unittest.TestCase):
    def test_discovers_flag_names_at_runtime(self):
        zeta, omicron = fr.discover_tier_flags(GAMEDATA["skill"])
        self.assertEqual(zeta, "isZetaTier")
        self.assertEqual(omicron, "isOmicronTier")

    def test_unknown_schema_yields_no_flags(self):
        zeta, omicron = fr.discover_tier_flags([{"id": "x", "tier": [{"somethingElse": True}]}])
        self.assertIsNone(zeta)
        self.assertIsNone(omicron)

    def test_only_unlocked_tiers_count(self):
        flags, _, _ = fr.build_skill_flag_map(GAMEDATA)
        unit = PLAYER["rosterUnit"][0]
        zetas, omicrons, skills = fr.abilities_of(unit, flags)
        # Обе способности прокачаны до зеты...
        self.assertEqual(zetas, ["skill_revan_leader", "skill_revan_unique"])
        # ...а омикрон у лидерки лежит на тире 2, который ещё не открыт (tier == 2).
        self.assertEqual(omicrons, [])
        # Wiki: '+ 2 to the tier value of each skill to get the actual in-game tier'.
        self.assertEqual([s["tier"] for s in skills], [5, 4])

    def test_omicron_detected_once_unlocked(self):
        flags, _, _ = fr.build_skill_flag_map(GAMEDATA)
        unit = {"skill": [{"id": "skill_revan_leader", "tier": 3}]}
        _, omicrons, _ = fr.abilities_of(unit, flags)
        self.assertEqual(omicrons, ["skill_revan_leader"])


class TestCombatType(unittest.TestCase):
    def test_map_collapses_rarity_duplicates(self):
        mapping = fr.build_combat_type_map(GAMEDATA)
        self.assertEqual(mapping["DARTHREVAN"], fr.COMBAT_TYPE_CHARACTER)
        self.assertEqual(mapping["MILLENNIUMFALCON"], fr.COMBAT_TYPE_SHIP)
        self.assertEqual(len(mapping), 5)


class TestCleanUnit(unittest.TestCase):
    def test_without_gamedata_abilities_are_null_not_guessed(self):
        entry = fr.clean_unit(PLAYER["rosterUnit"][0], {}, False, has_gamedata=False)
        self.assertIsNone(entry["zetas"])
        self.assertIsNone(entry["omicrons"])

    def test_mods_summarised_from_present_fields_only(self):
        entry = fr.clean_unit(PLAYER["rosterUnit"][0], {}, False, has_gamedata=False)
        self.assertEqual(entry["mods"]["count"], 3)
        self.assertEqual(entry["mods"]["by_level"], {"12": 1, "15": 2})
        # Сеты не выдумываем: расшифровки definitionId в выводе быть не должно.
        self.assertNotIn("set_counts", entry["mods"])

    def test_no_mods_key_when_unit_has_none(self):
        self.assertNotIn("mods", fr.clean_unit(PLAYER["rosterUnit"][2], {}, False, True))


class TestValidate(unittest.TestCase):
    def test_passes_on_clean_input(self):
        self.assertTrue(fr.validate(2, 2, [{"id": "A"}, {"id": "B"}]))

    def test_flags_duplicates(self):
        self.assertFalse(fr.validate(2, 2, [{"id": "A"}, {"id": "A"}]))

    def test_flags_count_mismatch(self):
        self.assertFalse(fr.validate(1, 2, [{"id": "A"}]))

    def test_flags_missing_id(self):
        self.assertFalse(fr.validate(2, 2, [{"id": "A"}, {"id": None}]))


class TestAllyCode(unittest.TestCase):
    def test_strips_dashes(self):
        self.assertEqual(fr.resolve_ally_code("123-456-789"), "123456789")

    def test_rejects_non_numeric(self):
        with self.assertRaises(SystemExit):
            fr.resolve_ally_code("not-a-code")

    def test_falls_back_to_environment(self):
        with mock.patch.dict("os.environ", {"SWGOH_ALLY_CODE": "987654321"}):
            self.assertEqual(fr.resolve_ally_code(None), "987654321")


# --------------------------------------------------------- сквозной тест


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/metadata":
            payload = METADATA
        elif self.path == "/player":
            if not body["payload"]["allyCode"].isdigit():
                self.send_error(400, "bad ally code")
                return
            payload = PLAYER
        elif self.path == "/data":
            # Старые сборки не принимают лишние ключи — проверяем перебор вариантов.
            if "includePveUnits" in body.get("payload", {}):
                self.send_error(400, "unsupported key")
                return
            payload = GAMEDATA
        else:
            self.send_error(404)
            return
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _run(self, *extra):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        argv = ["fetch_roster.py", "999999999", "--url", self.url, "--out-dir", tmp.name, *extra]
        with mock.patch.object(sys, "argv", argv):
            code = fr.main()
        return code, Path(tmp.name)

    def _read(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_full_run_splits_and_validates(self):
        code, out = self._run()
        self.assertEqual(code, 0)

        self.assertEqual(self._read(out / "roster_raw.json"), PLAYER)

        characters = self._read(out / "roster_clean.json")
        ships = self._read(out / "ships_clean.json")

        # Корабли отделены, юнит без combatType не потерян и ушёл к персонажам.
        self.assertEqual([s["id"] for s in ships], ["MILLENNIUMFALCON", "TIEFIGHTERPILOT"])
        self.assertIn("UNKNOWNNEWUNIT", [c["id"] for c in characters])
        self.assertEqual(len(characters) + len(ships), len(PLAYER["rosterUnit"]))

        revan = next(c for c in characters if c["id"] == "DARTHREVAN")
        self.assertEqual(revan["relic_tier"], 9)
        self.assertEqual(revan["zetas"], ["skill_revan_leader", "skill_revan_unique"])
        self.assertEqual(revan["omicrons"], [])

        yoda = next(c for c in characters if c["id"] == "GRANDMASTERYODA")
        self.assertEqual(yoda["relic_tier"], 0)

    def test_no_gamedata_keeps_everything_and_nulls_abilities(self):
        code, out = self._run("--no-gamedata")
        self.assertEqual(code, 0)
        characters = self._read(out / "roster_clean.json")
        # Тип неизвестен — корабли не выдумываем, всё в одном файле.
        self.assertEqual(len(characters), len(PLAYER["rosterUnit"]))
        self.assertFalse((out / "ships_clean.json").exists())
        self.assertTrue(all(c["zetas"] is None for c in characters))

    def test_include_skills_adds_in_game_tiers(self):
        _, out = self._run("--include-skills")
        revan = next(c for c in self._read(out / "roster_clean.json") if c["id"] == "DARTHREVAN")
        self.assertEqual([s["tier"] for s in revan["skills"]], [5, 4])

    def test_raw_is_saved_before_processing(self):
        """Сырой ответ должен лежать на диске даже если разбор упадёт."""
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        argv = ["fetch_roster.py", "999999999", "--url", self.url, "--out-dir", tmp.name]
        with mock.patch.object(fr, "clean_unit", side_effect=RuntimeError("boom")):
            with mock.patch.object(sys, "argv", argv):
                with self.assertRaises(RuntimeError):
                    fr.main()
        self.assertEqual(self._read(Path(tmp.name) / "roster_raw.json"), PLAYER)

    def test_comlink_down_exits_nonzero(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        argv = ["fetch_roster.py", "999999999", "--url", "http://127.0.0.1:1", "--out-dir", tmp.name]
        with mock.patch.object(sys, "argv", argv):
            self.assertEqual(fr.main(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
