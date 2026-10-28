"""Persistent household cooking state; pure business logic, no network dependency."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime
from uuid import uuid4


def initial():
    return {
        "revision": 0,
        "profile": {"people": 2, "maxMinutes": 40, "diets": [], "exclude": [], "quietHours": False},
        "pantry": [],
        "journal": {},
        "family": {"members": [], "session": None},
        "savedAt": None,
    }


def text(s, limit=120):
    if not isinstance(s, str) or not s.strip() or len(s) > limit:
        raise ValueError("Invalid text")
    return s.strip()


def date_value(value):
    if not value:
        return ""
    if not isinstance(value, str):
        raise ValueError("Invalid date")  # noqa: TRY004
    try:
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        raise ValueError("Invalid date") from None


def number(v, minimum, maximum, integer=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not minimum <= v <= maximum:
        raise ValueError("Invalid number")
    if integer and (not isinstance(v, int) or isinstance(v, bool)):
        raise ValueError("Expected integer")
    return v


def apply(state: dict, action: str, payload: dict) -> dict:
    if not isinstance(payload, dict):
        # The transport maps ValueError to the public invalid_request code.
        raise ValueError("Invalid studio payload")  # noqa: TRY004
    result = deepcopy(state)
    pantry = result.setdefault("pantry", [])
    family = result.setdefault("family", {"members": [], "session": None})
    members = family.setdefault("members", [])
    if action == "profile_set":
        people = number(payload.get("people"), 1, 20, True)
        minutes = number(payload.get("maxMinutes"), 5, 360, True)
        diets = payload.get("diets", [])
        exclude = payload.get("exclude", [])
        if (
            not isinstance(diets, list)
            or len(diets) > 2
            or any(x not in ("vegan", "vegetarian") for x in diets)
        ):
            raise ValueError("Unsupported diet tags")
        if (
            not isinstance(exclude, list)
            or len(exclude) > 30
            or any(not isinstance(x, str) or not x.strip() or len(x) > 80 for x in exclude)
        ):
            raise ValueError("Invalid ingredients exclusion")
        result["profile"] = {
            "people": people,
            "maxMinutes": minutes,
            "diets": diets,
            "exclude": [x.strip() for x in exclude],
            "quietHours": payload.get("quietHours") is True,
        }
    elif action == "pantry_put":
        unit = text(payload.get("unit"), 12)
        if unit not in ("g", "ml", "stück"):
            raise ValueError("Invalid pantry unit")
        amount = number(payload.get("amount"), 0, 10_000_000)
        row = {
            "id": text(payload["id"], 80) if payload.get("id") else str(uuid4()),
            "name": text(payload.get("name")),
            "amount": amount,
            "unit": unit,
            "expires": date_value(payload.get("expires")),
            "staple": payload.get("staple") is True,
        }
        for i, old in enumerate(pantry):
            if old["id"] == row["id"]:
                pantry[i] = row
                break
        else:
            if len(pantry) >= 300:
                raise ValueError("Pantry limit reached")
            pantry.append(row)
    elif action == "pantry_delete":
        result["pantry"] = [r for r in pantry if r["id"] != text(payload.get("id"), 80)]
    elif action == "journal_set":
        slug = text(payload.get("slug"), 150)
        note = payload.get("note") or ""
        rating = payload.get("rating") or 0
        if not isinstance(note, str) or len(note) > 1200:
            raise ValueError("Invalid recipe note")
        number(rating, 0, 5, True)
        prev = result.setdefault("journal", {}).get(slug, {})
        result["journal"][slug] = {
            "rating": rating,
            "note": note,
            "lastCooked": datetime.now(UTC).isoformat()
            if payload.get("cooked") is True
            else prev.get("lastCooked"),
            "favorite": payload.get("favorite") is True,
            "updated": datetime.now(UTC).isoformat(),
        }
        if len(result["journal"]) > 1500:
            raise ValueError("Journal limit reached")
    elif action == "family_member":
        if payload.get("remove"):
            member_id = text(payload.get("id"), 80)
            if any(
                task.get("memberId") == member_id
                for task in (family.get("session") or {}).get("tasks", [])
            ):
                raise ValueError(
                    "This person still has cooking tasks. Remove or finish the tasks first."
                )
            family["members"] = [x for x in members if x["id"] != member_id]
        else:
            row = {
                "id": text(payload["id"], 80) if payload.get("id") else str(uuid4()),
                "name": text(payload.get("name"), 60),
                "child": payload.get("child") is True,
            }
            for i, old in enumerate(members):
                if old["id"] == row["id"]:
                    members[i] = row
                    break
            else:
                if len(members) >= 20:
                    raise ValueError("Family member limit reached")
                members.append(row)
    elif action == "session_start":
        if family.get("session") is not None and payload.get("replace_existing") is not True:
            raise ValueError("A cooking session is already active. Confirm replacing it first.")
        family["session"] = {
            "id": str(uuid4()),
            "slug": text(payload.get("slug"), 150),
            "name": text(payload.get("name"), 180),
            "step": 0,
            "started": datetime.now(UTC).isoformat(),
            "tasks": [],
            "timers": [],
            "revision": 0,
        }
    elif action in ("session_step", "session_task", "session_timer", "session_end"):
        session = family.get("session")
        if not session or session["id"] != payload.get("session_id"):
            raise ValueError("Cooking session changed")
        if action == "session_step":
            session["step"] = number(payload.get("step"), 0, 500, True)
            session["revision"] += 1
        elif action == "session_task":
            tasks = session["tasks"]
            if payload.get("remove"):
                session["tasks"] = [r for r in tasks if r["id"] != text(payload.get("id"), 80)]
            else:
                old = next((row for row in tasks if row["id"] == payload.get("id")), None)
                if old and set(payload) == {"session_id", "id", "done"}:
                    payload = {**old, **payload}
                member_id = text(payload.get("memberId"), 80)
                member = next((m for m in members if m["id"] == member_id), None)
                if not member:
                    raise ValueError("Unknown family member")
                if member["child"] and payload.get("adultApproved") is not True:
                    raise ValueError("Child task needs adult approval")
                row = {
                    "id": text(payload["id"], 80) if payload.get("id") else str(uuid4()),
                    "text": text(payload.get("text"), 300),
                    "memberId": member_id,
                    "done": payload.get("done") is True,
                    "adultApproved": payload.get("adultApproved") is True,
                }
                for i, old in enumerate(tasks):
                    if old["id"] == row["id"]:
                        tasks[i] = row
                        break
                else:
                    if len(tasks) >= 40:
                        raise ValueError("Task limit reached")
                    tasks.append(row)
            session["revision"] += 1
        elif action == "session_timer":
            if payload.get("remove"):
                session["timers"] = [
                    r for r in session["timers"] if r["id"] != text(payload.get("id"), 80)
                ]
            else:
                minutes = number(payload.get("minutes"), 1, 240, True)
                if len(session["timers"]) >= 16:
                    raise ValueError("Timer limit reached")
                session["timers"].append(
                    {
                        "id": str(uuid4()),
                        "name": text(payload.get("name"), 70),
                        "deadline": int(datetime.now(UTC).timestamp() * 1000) + minutes * 60000,
                    }
                )
            session["revision"] += 1
        else:
            family["session"] = None
    else:
        raise ValueError("Unknown studio action")
    result["revision"] = state.get("revision", 0) + 1
    result["savedAt"] = datetime.now(UTC).isoformat()
    return result
