import importlib.util
import sys
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "custom_components" / "mealie_atelier" / "studio.py"
spec = importlib.util.spec_from_file_location("studio_pure", root)
studio = importlib.util.module_from_spec(spec)
sys.modules["studio_pure"] = studio
spec.loader.exec_module(studio)


class StudioTests(unittest.TestCase):
    def test_pantry_profile_and_family(self):
        s = studio.initial()
        s = studio.apply(
            s,
            "pantry_put",
            {"name": "Tomaten", "amount": 300, "unit": "g", "expires": "2026-10-31"},
        )
        s = studio.apply(
            s, "profile_set", {"people": 3, "maxMinutes": 30, "diets": ["vegan"], "exclude": []}
        )
        s = studio.apply(s, "family_member", {"name": "Kind", "child": True})
        s = studio.apply(s, "session_start", {"slug": "suppe", "name": "Suppe"})
        with self.assertRaisesRegex(ValueError, "adult approval"):
            studio.apply(
                s,
                "session_task",
                {
                    "session_id": s["family"]["session"]["id"],
                    "memberId": s["family"]["members"][0]["id"],
                    "text": "Teller stellen",
                },
            )
        s = studio.apply(
            s,
            "session_task",
            {
                "session_id": s["family"]["session"]["id"],
                "memberId": s["family"]["members"][0]["id"],
                "text": "Teller stellen",
                "adultApproved": True,
            },
        )
        self.assertEqual(len(s["family"]["session"]["tasks"]), 1)
        self.assertEqual(s["profile"]["people"], 3)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            studio.apply(studio.initial(), "pantry_put", {"name": "X", "amount": -1, "unit": "g"})
        with self.assertRaises(ValueError):
            studio.apply(studio.initial(), "profile_set", {"people": 1, "maxMinutes": 2})


if __name__ == "__main__":
    unittest.main()


class StudioCompletionTests(unittest.TestCase):
    def test_python_logic_task_toggle(self):
        s = studio.initial()
        s = studio.apply(s, "family_member", {"name": "Kid", "child": True})
        s = studio.apply(s, "session_start", {"slug": "dish", "name": "Dish"})
        sess = s["family"]["session"]["id"]
        member = s["family"]["members"][0]["id"]
        s = studio.apply(
            s,
            "session_task",
            {
                "session_id": sess,
                "memberId": member,
                "text": "Decorate plate",
                "adultApproved": True,
            },
        )
        task = s["family"]["session"]["tasks"][0]
        s = studio.apply(s, "session_task", {"session_id": sess, "id": task["id"], "done": True})
        assert s["family"]["session"]["tasks"][0]["done"] is True


class StudioQualityTests(unittest.TestCase):
    def test_active_session_protected_from_implicit_overwrite(self):
        s = studio.apply(studio.initial(), "session_start", {"slug": "one", "name": "Recipe A"})
        with self.assertRaisesRegex(ValueError, "already active"):
            studio.apply(s, "session_start", {"slug": "two", "name": "Recipe B"})
        replacement = studio.apply(
            s, "session_start", {"slug": "two", "name": "Recipe B", "replace_existing": True}
        )
        self.assertEqual(replacement["family"]["session"]["slug"], "two")

    def test_family_member_with_assigned_tasks_cannot_be_orphaned(self):
        s = studio.apply(studio.initial(), "family_member", {"name": "Helper", "child": False})
        member = s["family"]["members"][0]["id"]
        s = studio.apply(s, "session_start", {"slug": "meal", "name": "Meal"})
        session = s["family"]["session"]["id"]
        s = studio.apply(
            s, "session_task", {"session_id": session, "memberId": member, "text": "Set table"}
        )
        with self.assertRaisesRegex(ValueError, "still has cooking tasks"):
            studio.apply(s, "family_member", {"id": member, "remove": True})
