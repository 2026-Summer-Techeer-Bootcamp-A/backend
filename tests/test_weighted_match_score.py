from app.services.match import calculate_match_score, select_target_skills


SKILLS = [
    {"skill_id": 1, "canonical": "Java", "posting_count": 900, "freq": .5},
    {"skill_id": 2, "canonical": "Spring", "posting_count": 800, "freq": .44},
    {"skill_id": 3, "canonical": "Docker", "posting_count": 100, "freq": .06},
    {"skill_id": 4, "canonical": "Redis", "posting_count": 50, "freq": .03},
    {"skill_id": 5, "canonical": "Swagger", "posting_count": 20, "freq": .01},
]


def test_weighted_score_boundaries_and_monotonicity():
    assert calculate_match_score(set(), SKILLS)["score"] == 0
    assert calculate_match_score({1, 2, 3, 4, 5}, SKILLS)["score"] == 100
    assert calculate_match_score({1}, SKILLS)["score"] <= calculate_match_score({1, 3}, SKILLS)["score"]


def test_core_skill_is_more_valuable_than_low_frequency_skill():
    assert calculate_match_score({1}, SKILLS)["score"] > calculate_match_score({5}, SKILLS)["score"]


def test_score_is_deterministic_and_explained():
    first = calculate_match_score({1, 3}, SKILLS)
    assert first == calculate_match_score({1, 3}, SKILLS)
    assert first["base_score"] >= first["score"]
    assert first["skills"][0]["tier"] == "core"


def test_coverage_and_gap_share_the_same_default_target_universe():
    market = [
        {"skill_id": index, "canonical": f"skill-{index}", "posting_count": 100 - index}
        for index in range(25)
    ]
    coverage_targets = select_target_skills(market)
    gap_targets = select_target_skills(market)

    assert coverage_targets == gap_targets == market[:20]
    assert calculate_match_score({0, 3}, coverage_targets)["score"] == calculate_match_score(
        {0, 3}, gap_targets
    )["score"]
