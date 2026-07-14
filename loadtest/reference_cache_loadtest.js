// 참조 데이터 캐싱(동건) 부하 테스트.
//
// 목적(체크리스트 #9): 캐싱한 네 엔드포인트(/skills, /api/v1/job-categories,
// /api/v1/certs, /api/v1/company/by-skill)의 p95가 좋아지는지, 그리고 더
// 중요하게는 DB 왕복이 줄면서 "다른 엔드포인트들의 p95도 같이 좋아지는지"
// (커넥션 경합 완화 효과)를 함께 본다.
//
// 실행: (Redis/DB/서버가 떠 있는 스택에 대해)
//   k6 run -e BASE_URL=http://localhost:8000 loadtest/reference_cache_loadtest.js
//
// 캐시 전/후 비교: 캐싱 브랜치 배포 전(main)과 후(feat/redis-cache-reference-data)
// 각각에 대해 동일 조건으로 돌려 per-endpoint p95(아래 커스텀 Trend)와
// 경합 대상인 무거운 엔드포인트들의 p95를 비교한다.

import http from 'k6/http'
import { check, sleep } from 'k6'
import { Trend } from 'k6/metrics'

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000'

// 엔드포인트별 p95를 개별적으로 보기 위한 커스텀 Trend.
const latency = {
  skills: new Trend('lat_skills', true),
  job_categories: new Trend('lat_job_categories', true),
  certs: new Trend('lat_certs', true),
  company_by_skill: new Trend('lat_company_by_skill', true),
  // 경합 상대 — 캐싱이 이들의 p95도 낮춰주는지 관찰(이 작업의 진짜 목표).
  search: new Trend('lat_search', true),
  postings: new Trend('lat_postings', true),
}

export const options = {
  scenarios: {
    ramp: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 20 },
        { duration: '1m', target: 50 },
        { duration: '30s', target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    // 캐싱된 참조 엔드포인트는 히트 시 매우 빨라야 한다.
    lat_skills: ['p(95)<150'],
    lat_job_categories: ['p(95)<150'],
    lat_certs: ['p(95)<150'],
    lat_company_by_skill: ['p(95)<200'],
  },
}

// 캐시 키가 파라미터별로 갈리므로, 소수의 반복되는 값을 섞어 히트/미스를 모두 만든다.
const SKILL_QUERIES = ['py', 'java', 'react', 'kotlin', 'go']
const CERT_QUERIES = ['aws', 'sql', 'linux', '']
const POOLS = ['global', 'domestic', '']
const COMPANY_SKILLS = ['Python', 'Kotlin', 'React', 'Go']

function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)]
}

function timed(trend, res) {
  trend.add(res.timings.duration)
  check(res, { 'status is 200': (r) => r.status === 200 })
}

export default function () {
  const q = pick(SKILL_QUERIES)
  timed(latency.skills, http.get(`${BASE_URL}/skills?q=${q}&limit=20`))

  const pool = pick(POOLS)
  const poolParam = pool ? `?pool=${pool}` : ''
  timed(latency.job_categories, http.get(`${BASE_URL}/api/v1/job-categories${poolParam}`))

  const cq = pick(CERT_QUERIES)
  timed(latency.certs, http.get(`${BASE_URL}/api/v1/certs${cq ? `?q=${cq}` : ''}`))

  const skill = pick(COMPANY_SKILLS)
  timed(
    latency.company_by_skill,
    http.get(`${BASE_URL}/api/v1/company/by-skill?skill=${skill}${pool ? `&pool=${pool}` : ''}`),
  )

  // 경합 상대 관찰 — 캐싱 도입으로 이들 p95가 함께 내려가는지 본다.
  timed(latency.search, http.get(`${BASE_URL}/api/v1/search?q=${q}`))
  timed(latency.postings, http.get(`${BASE_URL}/api/v1/postings?limit=20`))

  sleep(1)
}
