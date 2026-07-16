from app.routers import insight


def test_warm_concept_tech_cache_uses_dashboard_queries(monkeypatch) -> None:
    calls: list[tuple[str, int, int]] = []

    def fake_resolve(session, *, pool, top_concepts, top_techs):
        calls.append((pool, top_concepts, top_techs))

    monkeypatch.setattr(insight, "resolve_concept_tech", fake_resolve)

    insight.warm_concept_tech_cache(object())

    assert calls == [
        ("domestic", 20, 4),
        ("global", 20, 4),
    ]
