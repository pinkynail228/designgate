"""AC-5/AC-6: loading, validation and lookups of the ``people.json`` directory."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from figmapot_bot.people import PeopleError, Person, load


ENTRY = {
    "key": "ivan",
    "name": "Ivan Petrov",
    "jira_account_id": "jira-ivan",
    "penpot_profile_id": "penpot-ivan",
    "telegram_chat_id": "111",
    "owner": True,
}


class PeopleTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="figmapot-people-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "people.json")

    def write(self, data):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return self.path

    def write_people(self, *entries):
        return self.write({"people": list(entries)})


class LoadTests(PeopleTestCase):
    def test_loads_person_fields(self):
        directory = load(self.write_people(ENTRY))
        self.assertEqual(len(directory), 1)
        self.assertEqual(
            directory.resolve("ivan"),
            Person(
                key="ivan",
                name="Ivan Petrov",
                jira_account_id="jira-ivan",
                penpot_profile_id="penpot-ivan",
                telegram_chat_id="111",
                owner=True,
            ),
        )

    def test_missing_file_is_an_empty_directory(self):
        directory = load(os.path.join(self.tmp, "absent.json"))
        self.assertEqual(len(directory), 0)
        self.assertIsNone(directory.resolve("ivan"))
        self.assertIsNone(directory.owner())

    def test_missing_key_names_the_entry(self):
        entry = dict(ENTRY)
        entry.pop("key")
        with self.assertRaises(PeopleError) as caught:
            load(self.write_people(entry))
        self.assertIn("missing key", str(caught.exception))
        self.assertIn("person 1", str(caught.exception))

    def test_missing_name_names_the_entry(self):
        entry = dict(ENTRY)
        entry.pop("name")
        with self.assertRaises(PeopleError) as caught:
            load(self.write_people(entry))
        self.assertIn("ivan", str(caught.exception))
        self.assertIn("missing name", str(caught.exception))

    def test_missing_jira_account_id_names_the_entry(self):
        entry = dict(ENTRY)
        entry.pop("jira_account_id")
        with self.assertRaises(PeopleError) as caught:
            load(self.write_people(entry))
        self.assertIn("ivan", str(caught.exception))
        self.assertIn("missing jira_account_id", str(caught.exception))

    def test_duplicate_key_is_rejected(self):
        second = dict(ENTRY, name="Ivan Again", jira_account_id="jira-other")
        with self.assertRaises(PeopleError) as caught:
            load(self.write_people(ENTRY, second))
        self.assertIn("duplicate key", str(caught.exception))
        self.assertIn("ivan", str(caught.exception))

    def test_duplicate_jira_account_id_is_rejected_without_echoing_it(self):
        second = dict(ENTRY, key="petr", name="Petr", penpot_profile_id=None)
        with self.assertRaises(PeopleError) as caught:
            load(self.write_people(ENTRY, second))
        message = str(caught.exception)
        self.assertIn("duplicate jira_account_id", message)
        self.assertIn("petr", message)
        self.assertNotIn("jira-ivan", message)

    def test_duplicate_penpot_profile_id_is_rejected_without_echoing_it(self):
        second = dict(ENTRY, key="petr", name="Petr", jira_account_id="jira-petr")
        with self.assertRaises(PeopleError) as caught:
            load(self.write_people(ENTRY, second))
        message = str(caught.exception)
        self.assertIn("duplicate penpot_profile_id", message)
        self.assertIn("petr", message)
        self.assertNotIn("penpot-ivan", message)

    def test_more_than_one_owner_is_rejected(self):
        second = dict(
            ENTRY, key="petr", name="Petr", jira_account_id="jira-petr", penpot_profile_id="penpot-petr"
        )
        with self.assertRaises(PeopleError) as caught:
            load(self.write_people(ENTRY, second))
        message = str(caught.exception)
        self.assertIn("owner", message)
        self.assertIn("ivan", message)
        self.assertIn("petr", message)


class ResolveTests(PeopleTestCase):
    def setUp(self):
        super().setUp()
        self.directory = load(
            self.write_people(
                ENTRY,
                {"key": "petr", "name": "Petr", "jira_account_id": "jira-petr", "penpot_profile_id": "penpot-petr"},
            )
        )

    def test_resolve_by_key_is_case_insensitive(self):
        self.assertEqual(self.directory.resolve("Petr").name, "Petr")

    def test_resolve_by_penpot_id(self):
        self.assertEqual(self.directory.resolve("penpot:penpot-petr").key, "petr")

    def test_unknown_ref_is_none(self):
        self.assertIsNone(self.directory.resolve("nobody"))
        self.assertIsNone(self.directory.resolve("penpot:missing"))
        self.assertIsNone(self.directory.resolve("jira:penpot-petr"))
        self.assertIsNone(self.directory.resolve(None))

    def test_owner_returns_the_owner_person(self):
        self.assertEqual(self.directory.owner().key, "ivan")

    def test_owner_is_none_without_an_owner(self):
        directory = load(
            self.write_people({"key": "petr", "name": "Petr", "jira_account_id": "jira-petr"})
        )
        self.assertIsNone(directory.owner())


if __name__ == "__main__":
    unittest.main()
