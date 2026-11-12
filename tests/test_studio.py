import unittest

from custom_components.mealie_atelier import studio


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


class SessionServingsTests(unittest.TestCase):
    def start(self, **extra):
        return studio.apply(
            studio.initial(), "session_start", {"slug": "dish", "name": "Dish", **extra}
        )

    def test_start_stores_the_pair(self):
        session = self.start(servings=4, servings_base=2.5)["family"]["session"]
        self.assertEqual((session["servings"], session["servingsBase"]), (4, 2.5))

    def test_start_without_pair_has_neither_key(self):
        session = self.start()["family"]["session"]
        self.assertNotIn("servings", session)
        self.assertNotIn("servingsBase", session)

    def test_start_rejects_half_pairs_and_bad_numbers(self):
        for extra in (
            {"servings": 4},
            {"servings_base": 2},
            {"servings": 0, "servings_base": 2},
            {"servings": 4, "servings_base": 1000.5},
            {"servings": 0.005, "servings_base": 2},
            {"servings": True, "servings_base": 2},
            {"servings": "4", "servings_base": 2},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.start(**extra)
        self.start(servings=0.01, servings_base=1000)

    def test_session_servings_updates_and_bumps_revision(self):
        s = self.start()
        sid = s["family"]["session"]["id"]
        s2 = studio.apply(
            s, "session_servings", {"session_id": sid, "servings": 6, "servings_base": 2}
        )
        session = s2["family"]["session"]
        self.assertEqual((session["servings"], session["servingsBase"]), (6, 2))
        self.assertEqual(session["revision"], 1)
        self.assertEqual(s2["revision"], s["revision"] + 1)

    def test_session_servings_rejects_wrong_session_none_and_bad_values(self):
        s = self.start()
        sid = s["family"]["session"]["id"]
        with self.assertRaises(ValueError):
            studio.apply(
                s, "session_servings", {"session_id": "x", "servings": 4, "servings_base": 2}
            )
        with self.assertRaises(ValueError):
            studio.apply(
                studio.initial(),
                "session_servings",
                {"session_id": sid, "servings": 4, "servings_base": 2},
            )
        for extra in ({}, {"servings": 4}, {"servings": 0, "servings_base": 2}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                studio.apply(s, "session_servings", {"session_id": sid, **extra})

    def test_step_task_and_timer_keep_the_pair(self):
        s = self.start(servings=4, servings_base=2)
        sid = s["family"]["session"]["id"]
        s = studio.apply(s, "session_step", {"session_id": sid, "step": 2})
        s = studio.apply(s, "session_timer", {"session_id": sid, "name": "Eggs", "minutes": 3})
        s = studio.apply(s, "family_member", {"name": "Ann"})
        member = s["family"]["members"][0]["id"]
        s = studio.apply(s, "session_task", {"session_id": sid, "memberId": member, "text": "Stir"})
        session = s["family"]["session"]
        self.assertEqual((session["servings"], session["servingsBase"]), (4, 2))
