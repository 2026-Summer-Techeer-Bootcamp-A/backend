"""job_category.group_name 백필 (일회성 스크립트).

feed의 직군 필터 탭이 22개 세부 job_category(is_tech=true)를 그대로 노출해 가로 탭
UI가 넘치는 문제가 있었다. 해결책은 group_name을 추가해 ~15개 상위 그룹 탭으로
보여주고, 기존 22개 세부 카테고리는 "상세 필터" 패널의 pill로 내리는 것이다. 이건
순수 additive 변경이다 — resume.position 등과 공유하는 통제 어휘인 name 자체는
건드리지 않는다(app/models/job_category.py 참고), 아래 한 가지 데이터 품질 버그
정리만 예외.

Step A에서, 과거 휴리스틱 백필(enrich_categories.py)의 대소문자 아티팩트로 생긴
"iOS 개발자"/"IOS 개발자" 중복을 먼저 정리한다 — 후자를 전자로 병합하고 소프트
삭제한다. Step B는 group_name 컬럼을 추가하고, Step C는 하드코딩된 GROUP_MAP으로
값을 채운다.

이 스크립트가 다루는 데이터는 정적 하드코딩 매핑뿐이라(파싱할 원본 파일이 없음),
scripts/enrich_categories.py와 달리 emit 단계 없이 apply만 있다. 로컬/프로덕션
어디서든 재실행해도 안전하도록(멱등) 작성했다.

사용:
    python -m scripts.set_job_category_groups apply
"""

from __future__ import annotations

import os
import sys

import psycopg

# name -> group_name. "IOS 개발자"는 Step A에서 "iOS 개발자"로 병합/소프트삭제되므로
# 의도적으로 이 맵에 없다.
GROUP_MAP: dict[str, str] = {
    "서버/백엔드 개발자": "백엔드",
    "SW/솔루션": "SW/솔루션",
    "프론트엔드 개발자": "프론트엔드",
    "웹퍼블리셔": "프론트엔드",
    "devops/시스템 엔지니어": "DevOps/인프라",
    "인공지능/머신러닝": "AI/ML",
    "HW/임베디드": "HW/임베디드",
    "빅데이터 엔지니어": "데이터",
    "DBA": "데이터",
    "웹 풀스택 개발자": "풀스택",
    "기술지원": "기술지원",
    "안드로이드 개발자": "모바일",
    "iOS 개발자": "모바일",
    "크로스플랫폼 앱개발자": "모바일",
    "정보보안 담당자": "보안",
    "블록체인": "블록체인",
    "QA 엔지니어": "QA",
    "개발 PM": "PM/기획",
    "게임 클라이언트 개발자": "게임",
    "게임 서버 개발자": "게임",
    "VR/AR/3D": "게임",
}


def cmd_apply() -> None:
    database_url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            # --- Step A: "IOS 개발자" -> "iOS 개발자" 중복 병합 ---
            cur.execute(
                """
                DELETE FROM posting_category pc
                WHERE pc.category = 'IOS 개발자'
                AND EXISTS (
                    SELECT 1 FROM posting_category pc2
                    WHERE pc2.posting_id = pc.posting_id AND pc2.category = 'iOS 개발자'
                )
                """
            )
            print(
                f"posting_category: deleted {cur.rowcount} duplicate 'IOS 개발자' rows "
                "(canonical 'iOS 개발자' already present on the same posting)",
                file=sys.stderr,
            )

            cur.execute(
                "UPDATE posting_category SET category = 'iOS 개발자' "
                "WHERE category = 'IOS 개발자'"
            )
            print(
                f"posting_category: retargeted {cur.rowcount} remaining rows "
                "'IOS 개발자' -> 'iOS 개발자'",
                file=sys.stderr,
            )

            cur.execute(
                "UPDATE job_category SET is_deleted = true, deleted_at = now() "
                "WHERE name = 'IOS 개발자'"
            )
            print(
                f"job_category: soft-deleted {cur.rowcount} row(s) named 'IOS 개발자'",
                file=sys.stderr,
            )
            conn.commit()

            # --- Step B: group_name 컬럼 추가 (idempotent) ---
            cur.execute(
                "ALTER TABLE job_category ADD COLUMN IF NOT EXISTS group_name VARCHAR(64)"
            )
            conn.commit()
            print("job_category: ensured group_name column exists", file=sys.stderr)

            # --- Step C: GROUP_MAP 적용 ---
            cur.execute("SELECT name FROM job_category WHERE is_deleted = false")
            existing_names = {row[0] for row in cur.fetchall()}

            unmatched_keys = sorted(set(GROUP_MAP) - existing_names)
            for name in unmatched_keys:
                print(
                    f"warning: GROUP_MAP key {name!r} does not match any "
                    "(non-deleted) job_category row",
                    file=sys.stderr,
                )

            cur.executemany(
                "UPDATE job_category SET group_name = %s WHERE name = %s",
                [(v, k) for k, v in GROUP_MAP.items()],
            )
            updated = cur.rowcount
            conn.commit()

            cur.execute(
                "SELECT count(*) FROM job_category "
                "WHERE is_deleted = false AND group_name IS NOT NULL"
            )
            grouped_count = cur.fetchone()[0]
            print(
                f"job_category: group_name applied for {len(GROUP_MAP)} GROUP_MAP entries "
                f"(last executemany batch rowcount={updated}), "
                f"{grouped_count} non-deleted rows now have a non-null group_name, "
                f"{len(unmatched_keys)} GROUP_MAP keys had no matching row",
                file=sys.stderr,
            )


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "apply":
        print(__doc__)
        sys.exit(1)
    cmd_apply()
