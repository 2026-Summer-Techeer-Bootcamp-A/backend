"""GET /stats/group-share, /stats/concept-tech, /stats/skill-count-dist, /stats/global-domestic-lag 테스트.

feat/market-stats-v2 — 시장 상황 탭 재구성(design v8) §5 신규 백엔드 4종.
"""

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.crud.insight import get_global_domestic_lag
from app.main import app
from app.models import Concept, Posting, PostingConcept, PostingTech, Skill


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        react = Skill(canonical="React", category="framework")
        vue = Skill(canonical="Vue", category="framework")
        docker = Skill(canonical="Docker", category="devops")
        kubernetes = Skill(canonical="Kubernetes", category="devops")
        terraform = Skill(canonical="Terraform", category="devops")
        seed.add_all([react, vue, docker, kubernetes, terraform])
        seed.flush()

        # ---- group-share fixtures ----
        gs_p1 = Posting(source="jumpit", source_uid="gs-1", pool="domestic", company="A", title="X")
        gs_p2 = Posting(source="jumpit", source_uid="gs-2", pool="domestic", company="B", title="X")
        gs_p3 = Posting(source="jumpit", source_uid="gs-3", pool="domestic", company="C", title="X")
        gs_global = Posting(source="wwr", source_uid="gs-g1", pool="global", company="D", title="X")
        seed.add_all([gs_p1, gs_p2, gs_p3, gs_global])
        seed.commit()
        seed.add_all(
            [
                PostingTech(posting_id=gs_p1.id, skill_id=react.id),
                PostingTech(posting_id=gs_p2.id, skill_id=react.id),
                PostingTech(posting_id=gs_p3.id, skill_id=vue.id),
                PostingTech(posting_id=gs_global.id, skill_id=react.id),
            ]
        )
        seed.commit()

        # ---- concept-tech fixtures ----
        cicd = Concept(name="CI/CD", category="devops")
        msa = Concept(name="MSA", category="devops")
        seed.add_all([cicd, msa])
        seed.flush()

        ct_c1 = Posting(source="jumpit", source_uid="ct-1", pool="domestic", company="A", title="X")
        ct_c2 = Posting(source="jumpit", source_uid="ct-2", pool="domestic", company="B", title="X")
        ct_c3 = Posting(source="jumpit", source_uid="ct-3", pool="domestic", company="C", title="X")
        ct_m1 = Posting(source="jumpit", source_uid="ct-4", pool="domestic", company="D", title="X")
        ct_m2 = Posting(source="jumpit", source_uid="ct-5", pool="domestic", company="E", title="X")
        ct_global = Posting(source="wwr", source_uid="ct-g1", pool="global", company="F", title="X")
        seed.add_all([ct_c1, ct_c2, ct_c3, ct_m1, ct_m2, ct_global])
        seed.commit()
        seed.add_all(
            [
                PostingConcept(posting_id=ct_c1.id, concept_id=cicd.id),
                PostingConcept(posting_id=ct_c2.id, concept_id=cicd.id),
                PostingConcept(posting_id=ct_c3.id, concept_id=cicd.id),
                PostingConcept(posting_id=ct_m1.id, concept_id=msa.id),
                PostingConcept(posting_id=ct_m2.id, concept_id=msa.id),
                PostingConcept(posting_id=ct_global.id, concept_id=cicd.id),
            ]
        )
        seed.add_all(
            [
                PostingTech(posting_id=ct_c1.id, skill_id=docker.id),
                PostingTech(posting_id=ct_c2.id, skill_id=docker.id),
                PostingTech(posting_id=ct_c2.id, skill_id=kubernetes.id),
                PostingTech(posting_id=ct_c3.id, skill_id=terraform.id),
                PostingTech(posting_id=ct_m1.id, skill_id=kubernetes.id),
                PostingTech(posting_id=ct_m2.id, skill_id=kubernetes.id),
                PostingTech(posting_id=ct_global.id, skill_id=terraform.id),
            ]
        )
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def dist_client() -> Iterator[TestClient]:
    """skill-count-dist는 pool 전체를 집계하므로 다른 위젯 fixture와 섞이지 않도록 별도 DB를 쓴다."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        m1 = Skill(canonical="M1", category="misc")
        m2 = Skill(canonical="M2", category="misc")
        m3 = Skill(canonical="M3", category="misc")
        m4 = Skill(canonical="M4", category="misc")
        seed.add_all([m1, m2, m3, m4])
        seed.flush()

        sc_1tech = Posting(source="jumpit", source_uid="sc-1", pool="domestic", company="A", title="X")
        sc_3tech = Posting(source="jumpit", source_uid="sc-2", pool="domestic", company="B", title="X")
        sc_4tech_a = Posting(source="jumpit", source_uid="sc-3", pool="domestic", company="C", title="X")
        sc_4tech_b = Posting(source="jumpit", source_uid="sc-4", pool="domestic", company="D", title="X")
        sc_no_tech = Posting(source="jumpit", source_uid="sc-5", pool="domestic", company="E", title="X")
        seed.add_all([sc_1tech, sc_3tech, sc_4tech_a, sc_4tech_b, sc_no_tech])
        seed.commit()
        seed.add_all(
            [
                PostingTech(posting_id=sc_1tech.id, skill_id=m1.id),
                PostingTech(posting_id=sc_3tech.id, skill_id=m1.id),
                PostingTech(posting_id=sc_3tech.id, skill_id=m2.id),
                PostingTech(posting_id=sc_3tech.id, skill_id=m3.id),
                PostingTech(posting_id=sc_4tech_a.id, skill_id=m1.id),
                PostingTech(posting_id=sc_4tech_a.id, skill_id=m2.id),
                PostingTech(posting_id=sc_4tech_a.id, skill_id=m3.id),
                PostingTech(posting_id=sc_4tech_a.id, skill_id=m4.id),
                PostingTech(posting_id=sc_4tech_b.id, skill_id=m1.id),
                PostingTech(posting_id=sc_4tech_b.id, skill_id=m2.id),
                PostingTech(posting_id=sc_4tech_b.id, skill_id=m3.id),
                PostingTech(posting_id=sc_4tech_b.id, skill_id=m4.id),
            ]
        )
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---- group-share ----


def test_group_share_requires_group(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/group-share")
    assert resp.status_code == 422


def test_group_share_rejects_unknown_group(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/group-share", params={"group": "not_a_group"})
    assert resp.status_code == 422


def test_group_share_defaults_to_domestic(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/group-share", params={"group": "frontend_fw"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["pool"] == "domestic"
    assert body["union_count"] == 3
    items = {i["canonical"]: i for i in body["items"]}
    assert items["React"]["count"] == 2
    assert items["React"]["share"] == round(100 * 2 / 3, 1)
    assert items["Vue"]["count"] == 1
    assert items["Vue"]["share"] == round(100 * 1 / 3, 1)
    # sorted desc by share
    assert body["items"][0]["canonical"] == "React"


def test_group_share_global_pool_isolated_from_domestic(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/group-share", params={"group": "frontend_fw", "pool": "global"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["union_count"] == 1
    assert body["items"] == [{"canonical": "React", "count": 1, "share": 100.0}]


def test_group_share_empty_group_returns_zero_union(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/group-share", params={"group": "database", "pool": "domestic"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["union_count"] == 0
    assert body["items"] == []


# ---- concept-tech ----


def test_concept_tech_defaults_to_domestic(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/concept-tech")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pool"] == "domestic"

    node_names = {n["name"]: n["type"] for n in body["nodes"]}
    assert node_names["CI/CD"] == "concept"
    assert node_names["MSA"] == "concept"
    assert node_names["Docker"] == "tech"
    assert node_names["Kubernetes"] == "tech"
    assert node_names["Terraform"] == "tech"

    links = {(link["source"], link["target"]): link["value"] for link in body["links"]}
    assert links[("CI/CD", "Docker")] == 2
    assert links[("CI/CD", "Kubernetes")] == 1
    assert links[("CI/CD", "Terraform")] == 1
    assert links[("MSA", "Kubernetes")] == 2


def test_concept_tech_top_techs_limits_per_concept(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/stats/concept-tech",
        params={"pool": "domestic", "top_concepts": 2, "top_techs": 1},
    )
    assert resp.status_code == 200
    body = resp.json()

    links_by_concept: dict[str, list[dict]] = {}
    for link in body["links"]:
        links_by_concept.setdefault(link["source"], []).append(link)

    # CI/CD has 3 postings (Docker x2, Kubernetes x1, Terraform x1) -> top1 = Docker(2)
    assert len(links_by_concept["CI/CD"]) == 1
    assert links_by_concept["CI/CD"][0]["target"] == "Docker"
    assert links_by_concept["CI/CD"][0]["value"] == 2

    # MSA has 2 postings, both Kubernetes -> top1 = Kubernetes(2)
    assert len(links_by_concept["MSA"]) == 1
    assert links_by_concept["MSA"][0]["target"] == "Kubernetes"
    assert links_by_concept["MSA"][0]["value"] == 2

    tech_nodes = {n["name"] for n in body["nodes"] if n["type"] == "tech"}
    assert "Terraform" not in tech_nodes  # pruned by top_techs=1


def test_concept_tech_respects_top_concepts(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/stats/concept-tech",
        params={"pool": "domestic", "top_concepts": 1, "top_techs": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    concept_nodes = [n["name"] for n in body["nodes"] if n["type"] == "concept"]
    assert concept_nodes == ["CI/CD"]  # CI/CD(3) outranks MSA(2)


def test_concept_tech_empty_pool_returns_empty(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/concept-tech", params={"pool": "global", "top_concepts": 6})
    assert resp.status_code == 200
    body = resp.json()
    # only ct_global posting exists (CI/CD + Terraform)
    concept_nodes = [n["name"] for n in body["nodes"] if n["type"] == "concept"]
    assert concept_nodes == ["CI/CD"]
    tech_nodes = [n["name"] for n in body["nodes"] if n["type"] == "tech"]
    assert tech_nodes == ["Terraform"]


# ---- skill-count-dist ----


def test_skill_count_dist_defaults_to_domestic(dist_client: TestClient) -> None:
    resp = dist_client.get("/api/v1/stats/skill-count-dist")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pool"] == "domestic"
    histogram = {h["k"]: h["count"] for h in body["histogram"]}
    # postings with 0 techs are excluded entirely
    assert histogram == {1: 1, 3: 1, 4: 2}
    assert body["avg"] == 3.0  # (1+3+4+4)/4
    assert body["median"] == 3.5  # median([1,3,4,4])


def test_skill_count_dist_empty_pool_returns_zeroed_response(dist_client: TestClient) -> None:
    resp = dist_client.get("/api/v1/stats/skill-count-dist", params={"pool": "global"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["histogram"] == []
    assert body["avg"] == 0.0
    assert body["median"] == 0.0


# ---- global-domestic-lag (router smoke test: below-threshold sample -> empty, no error) ----


def test_global_domestic_lag_router_returns_empty_items_below_sample_threshold(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/global-domestic-lag")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert "근사" in body["note"]


def test_global_domestic_lag_rejects_invalid_limit(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/global-domestic-lag", params={"limit": 0})
    assert resp.status_code == 422


# ---- global-domestic-lag (crud-level: verify cross-correlation lag estimation) ----


def test_global_domestic_lag_estimates_shift_via_cross_correlation() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as session:
        skill_x = Skill(canonical="LagSkill", category="framework")
        session.add(skill_x)
        session.flush()

        years = [2018, 2019, 2020, 2021, 2022, 2023, 2024]
        # global adopts from 2021 onward; domestic adopts the same pattern shifted 2 years later (2023).
        global_on_years = {2021, 2022, 2023, 2024}
        domestic_on_years = {2023, 2024}

        uid = 0
        for year in years:
            for pool, on_years in (("global", global_on_years), ("domestic", domestic_on_years)):
                for i in range(5):
                    uid += 1
                    posting = Posting(
                        source="wwr" if pool == "global" else "jumpit",
                        source_uid=f"lag-{uid}",
                        pool=pool,
                        company="C",
                        title="X",
                        post_date=date(year, 6, 15),
                    )
                    session.add(posting)
                    session.flush()
                    if year in on_years and i < 4:  # 4 of 5 postings that year carry the skill (80% share)
                        session.add(PostingTech(posting_id=posting.id, skill_id=skill_x.id))
        session.commit()

        result = get_global_domestic_lag(session, limit=10, min_postings=1, min_years=5, max_lag=3)

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["canonical"] == "LagSkill"
    assert item["lag_years"] == 2
    assert len(item["global_series"]) == 7
    assert len(item["domestic_series"]) == 7
