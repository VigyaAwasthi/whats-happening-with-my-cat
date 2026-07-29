"""HTTP wiring for zero-cat onboarding and a no-paid-call corner exercise."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def cat_payload(cat_id: object, name: str = "Mochi") -> dict[str, object]:
    return {
        "cat_id": str(cat_id),
        "name": name,
        "age": {"value": 3, "unit": "years"},
        "breed": None,
        "weight": {"value": 9, "unit": "lb"},
        "energy_level": 3,
        "common_patterns": "Knocks pens off the desk.",
        "known_conditions": [],
        "photo_references": [],
        "theme": {
            "primary_color": "#112233",
            "accent_color": "#AABBCC",
        },
    }


def test_browser_cors_preflight(monkeypatch: object) -> None:
    monkeypatch.setenv("RUNTIME_MODE", "development")
    with TestClient(app) as client:
        response = client.options(
            "/chat/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": (
                    "authorization,content-type,x-active-cat-id"
                ),
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_first_cat_bootstrap_and_emergency_route(monkeypatch: object) -> None:
    monkeypatch.setenv("RUNTIME_MODE", "development")
    cat_id = uuid4()
    session_id = uuid4()
    with TestClient(app) as client:
        no_cat = client.post(
            "/chat/health",
            json={
                "cat_id": str(cat_id),
                "message": "My cat can't pee",
                "intake": None,
                "session_id": str(session_id),
            },
        )
        assert no_cat.status_code == 409
        assert no_cat.json()["code"] == "NO_ACTIVE_CAT"

        created = client.post(
            "/cats",
            json=cat_payload(cat_id),
        )
        assert created.status_code == 201

        roster = client.get("/cats")
        assert roster.status_code == 200
        assert [cat["id"] for cat in roster.json()["cats"]] == [str(cat_id)]

        emergency = client.post(
            "/chat/health",
            headers={"X-Active-Cat-ID": str(cat_id)},
            json={
                "cat_id": str(cat_id),
                "message": "My cat can't pee and keeps going to the litter box",
                "intake": None,
                "session_id": str(session_id),
            },
        )
        assert emergency.status_code == 200
        assert emergency.json()["result"]["severity"] == "emergency"


def test_cat_cap_fact_detail_and_full_account_delete(monkeypatch: object) -> None:
    monkeypatch.setenv("RUNTIME_MODE", "development")
    cat_ids = [uuid4() for _ in range(11)]
    with TestClient(app) as client:
        for index, cat_id in enumerate(cat_ids[:10]):
            response = client.post(
                "/cats", json=cat_payload(cat_id, f"Cat {index}")
            )
            assert response.status_code == 201

        capped = client.post(
            "/cats", json=cat_payload(cat_ids[10], "Eleventh")
        )
        assert capped.status_code == 409
        assert capped.json()["code"] == "CAT_LIMIT_REACHED"

        facts = client.get(
            "/facts",
            headers={"X-Active-Cat-ID": str(cat_ids[0])},
            params={"cat_id": str(cat_ids[0]), "tags": "breed:bengal"},
        )
        assert facts.status_code == 200
        cards = facts.json()["facts"]
        assert cards
        detail = client.get(
            f"/facts/{cards[0]['id']}",
            headers={"X-Active-Cat-ID": str(cat_ids[0])},
            params={"cat_id": str(cat_ids[0])},
        )
        assert detail.status_code == 200
        assert detail.json()["detail"].strip()

        deleted = client.delete("/account")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert client.get("/account/export").status_code == 404
