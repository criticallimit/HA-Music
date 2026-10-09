"""Evaluate the actual HA discovery template against registry/source differences."""
import json
from pathlib import Path
import sys
import unittest
from jinja2.sandbox import ImmutableSandboxedEnvironment

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ha_music"))
import app


class RegistryTemplateTests(unittest.TestCase):
    def render(self, sources, registry, entries, names=None):
        # HA's domain lookup uses loaded entity_sources; title lookup uses the
        # entity registry, which also contains disabled/unloaded media players.
        calls = []
        def integration_entities(key):
            calls.append(key)
            return sources.get(key, registry.get(key, []))

        def config_entry_attr(entry, key):
            return entries[entry][key]

        entity_entries = {entity: entry for entry, data in entries.items() for entity in data["entities"]}
        environment = ImmutableSandboxedEnvironment()
        environment.filters["to_json"] = json.dumps
        environment.globals.update(integration_entities=integration_entities,
                                   config_entry_id=entity_entries.get,
                                   config_entry_attr=config_entry_attr,
                                   device_attr=lambda entity, key: (names or {}).get(entity) if key == "name" else None)
        result = json.loads(environment.from_string(app.INVENTORY_TEMPLATE).render())
        return result, calls

    def test_all_eight_devices_even_when_only_four_players_are_loaded(self):
        entities = ["media_player." + key for key in ("bad", "fire_tv", "fire_tv_2", "kueche", "mo", "this_device", "wohnung", "wohnzimmer")]
        loaded = [entity for entity in entities if entity.rsplit(".", 1)[-1] in ("bad", "kueche", "wohnung", "wohnzimmer")]
        entries = {"account": {"domain": "alexa_media", "title": "Amazon account", "entities": entities}}
        names = dict(zip(entities, ["Bad", "Dirks Fire TV", "Dirks Fire TV", "Küche", "Mo", "This Device", "Wohnung", "Wohnzimmer"]))
        result, calls = self.render({"alexa_media": loaded}, {"Amazon account": entities}, entries, names)
        self.assertEqual(set(result["alexa_media"]), set(entities))
        self.assertEqual(result["device_names"], names)
        self.assertEqual(calls.count("Amazon account"), 1)

    def test_account_registry_is_found_through_sensor_without_loaded_player(self):
        entities = ["sensor.mo_next_alarm", "media_player.mo"]
        entries = {"account": {"domain": "alexa_media", "title": "Amazon account", "entities": entities}}
        result, _ = self.render({"alexa_media": [entities[0]]}, {"Amazon account": entities}, entries)
        self.assertIn("media_player.mo", result["alexa_media"])

    def test_two_accounts_and_official_alexa_integration_are_combined(self):
        entries = {key: {"domain": domain, "title": key, "entities": ["media_player." + key]}
                   for key, domain in (("first", "alexa_media"), ("second", "alexa_media"), ("official", "alexa_devices"))}
        result, _ = self.render({"alexa_media": ["media_player.first", "media_player.second"], "alexa_devices": ["media_player.official"]},
                                {key: data["entities"] for key, data in entries.items()}, entries)
        self.assertEqual(set(result["alexa_media"]), {"media_player.first", "media_player.second"})
        self.assertEqual(result["alexa_devices"], ["media_player.official"])

    def test_same_title_from_another_integration_does_not_admit_its_players(self):
        entries = {"amazon": {"domain": "alexa_media", "title": "Shared title", "entities": ["media_player.mo"]},
                   "other": {"domain": "cast", "title": "Shared title", "entities": ["media_player.unrelated"]}}
        result, _ = self.render({"alexa_media": ["media_player.mo"]}, {"Shared title": ["media_player.mo", "media_player.unrelated"]}, entries)
        self.assertEqual(result["alexa_media"], ["media_player.mo"])

    def test_legacy_sources_without_config_entry_remain_visible(self):
        result, _ = self.render({"alexa_media": ["media_player.legacy"]}, {}, {})
        self.assertEqual(result["alexa_media"], ["media_player.legacy"])


if __name__ == "__main__":
    unittest.main()
