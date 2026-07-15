#!/bin/sh
set -e

# 컨테이너가 재시작될 때(재생성이 아니라)도 이전 워커들이 남긴 멀티프로세스
# 메트릭 파일이 파일시스템에 그대로 남아 새 워커의 값과 합산되며 카운터가
# 왜곡된다. uvicorn이 워커들을 fork하기 전, 프로세스 시작 시점에 한 번만
# 비운다.
if [ -n "$PROMETHEUS_MULTIPROC_DIR" ]; then
  rm -rf "$PROMETHEUS_MULTIPROC_DIR"
  mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
fi

exec "$@"
