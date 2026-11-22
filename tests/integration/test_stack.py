"""Integration: the running stack (tables + services + GraphQL) answers, and connector holds are exclusive.
Last-unit race ported from Taller-GraphQL backend/test/commands.test.js."""
from concurrent.futures import ThreadPoolExecutor

from conftest import connector, gql, start, stop, wait


def test_every_service_is_healthy():
    health = gql("{ health { env services { service status version } } }")["health"]
    assert {s["status"] for s in health["services"]} == {"ok"}, health
    assert len(health["services"]) == 4


def test_seed_is_loaded():
    stations = gql("{ stations { id connectors { id type powerKw } } }")["stations"]
    assert [s["id"] for s in stations] == ["BOG-01", "CDMX-01", "MDE-01"]
    assert all([c["type"] for c in s["connectors"]] == ["FAST", "FAST", "ULTRA"] for s in stations)
    assert len(gql("{ flags { name } }")["flags"]) == 3


def test_three_parallel_charges_on_one_connector_exactly_one_wins():
    with ThreadPoolExecutor(3) as pool:
        ids = [s["id"] for s in pool.map(lambda _: start("FLEET-ACME", "CDMX-01", "CDMX-01-C2", 1), range(3))]
    final = [wait(i) for i in ids]
    assert sorted(s["status"] for s in final) == ["COMPLETED", "FAILED", "FAILED"], final
    winner = next(s for s in final if s["status"] == "COMPLETED")
    assert connector("CDMX-01", "CDMX-01-C2")["sagaId"] == winner["id"]
    stop(winner["id"])
    assert connector("CDMX-01", "CDMX-01-C2")["status"] == "AVAILABLE"
