from scripts.enrich_categories import jobkorea_categories, jumpit_categories


def test_jumpit_categories_extracts_names():
    rec = {"jobCategories": [{"id": 1, "name": "서버/백엔드 개발자"}]}
    assert jumpit_categories(rec) == ["서버/백엔드 개발자"]


def test_jumpit_categories_multiple_preserve_order():
    rec = {
        "jobCategories": [
            {"id": 19, "name": "빅데이터 엔지니어"},
            {"id": 9, "name": "devops/시스템 엔지니어"},
        ]
    }
    assert jumpit_categories(rec) == ["빅데이터 엔지니어", "devops/시스템 엔지니어"]


def test_jumpit_categories_dedup():
    rec = {
        "jobCategories": [
            {"id": 1, "name": "서버/백엔드 개발자"},
            {"id": 1, "name": "서버/백엔드 개발자"},
        ]
    }
    assert jumpit_categories(rec) == ["서버/백엔드 개발자"]


def test_jumpit_categories_missing_field():
    assert jumpit_categories({}) == []


def test_jumpit_categories_empty_list():
    assert jumpit_categories({"jobCategories": []}) == []


def test_jumpit_categories_ignores_non_dict_entries():
    assert jumpit_categories({"jobCategories": ["not-a-dict", None]}) == []


def test_jumpit_categories_ignores_entries_without_name():
    assert jumpit_categories({"jobCategories": [{"id": 1}]}) == []


def test_jobkorea_categories_strips_noise_suffix():
    rec = {
        "categories": (
            "화학·에너지·환경, 제품영업, 해외영업, 영업관리, 화학, 국내영업, "
            "자동차영업, 장비영업, 영어, 거래선개발관리, 거래처관리, 고객관리, "
            "공채, 채용, 구인, 공고, 입사 지원, 잡코리아"
        )
    }
    assert jobkorea_categories(rec) == ["화학·에너지·환경", "제품영업"]


def test_jobkorea_categories_short_list_without_noise():
    # 노이즈 태그가 아예 없는(태그 3~8개짜리) 케이스도 표본에서 다수 관찰됨.
    rec = {"categories": "소프트웨어·솔루션·ASP, 웹프로그래머, php"}
    assert jobkorea_categories(rec) == ["소프트웨어·솔루션·ASP", "웹프로그래머"]


def test_jobkorea_categories_single_tag_after_noise_removed():
    rec = {"categories": "게임·애니메이션, 공채, 채용, 구인, 공고, 입사 지원, 잡코리아"}
    assert jobkorea_categories(rec) == ["게임·애니메이션"]


def test_jobkorea_categories_all_noise_yields_empty():
    rec = {"categories": "공채, 채용, 구인, 공고, 입사 지원, 잡코리아"}
    assert jobkorea_categories(rec) == []


def test_jobkorea_categories_missing_field():
    assert jobkorea_categories({}) == []


def test_jobkorea_categories_none_value():
    assert jobkorea_categories({"categories": None}) == []


def test_jobkorea_categories_empty_string():
    assert jobkorea_categories({"categories": ""}) == []


def test_jobkorea_categories_dedup_preserves_order():
    rec = {"categories": "백엔드개발자, 백엔드개발자, AI/ML엔지니어"}
    assert jobkorea_categories(rec) == ["백엔드개발자", "AI/ML엔지니어"]
