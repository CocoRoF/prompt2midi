#!/usr/bin/env bash
# Docker 이미지 점검: 컨테이너를 띄워 HTTP 응답·비루트·데이터 디렉터리·이미지 안 비밀 문자열을 단언한다.
# 사용: tools/docker_check.sh <이미지 이름>   (이미지는 미리 `docker build` 해 둔다. push·레지스트리 로그인 없음)
set -euo pipefail

IMAGE="${1:?사용법: tools/docker_check.sh <이미지>}"
NAME="p2m-ci-$$"
PORT="${P2M_CHECK_PORT:-8000}"
BASE="http://127.0.0.1:${PORT}"
FAILED=0

fail() { echo "FAIL: $*" >&2; FAILED=1; }
ok() { echo "ok: $*"; }
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

code() { curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1" || echo 000; }
expect_code() {
  local path="$1" want="$2" got
  got="$(code "${BASE}${path}")"
  if [ "$got" = "$want" ]; then ok "GET ${path} -> ${got}"; else fail "GET ${path} -> ${got} (기대 ${want})"; fi
}

docker run -d --name "$NAME" -p "127.0.0.1:${PORT}:8000" "$IMAGE" >/dev/null

# 기동 대기 (최대 40초)
up=0
for _ in $(seq 1 40); do
  if [ "$(code "${BASE}/healthz")" = "200" ]; then up=1; break; fi
  sleep 1
done
if [ "$up" != 1 ]; then
  echo "--- 컨테이너 로그 ---" >&2
  docker logs "$NAME" >&2 || true
  fail "40초 안에 /healthz 200 이 되지 않음"
  exit 1
fi

# 1) HTTP 응답
expect_code /healthz 200
expect_code / 200
expect_code /admin 200
expect_code /README.md 404

# 2) 비루트 실행
uid="$(docker exec "$NAME" id -u)"
if [ "$uid" != "0" ]; then ok "컨테이너 프로세스 uid=${uid} (비루트)"; else fail "컨테이너가 root(uid 0)로 실행됨"; fi
cfg_user="$(docker inspect --format '{{.Config.User}}' "$IMAGE")"
if [ -n "$cfg_user" ] && [ "$cfg_user" != "root" ] && [ "$cfg_user" != "0" ]; then ok "이미지 USER=${cfg_user}"; else fail "이미지 USER 가 비어 있거나 root (${cfg_user:-없음})"; fi

# 3) 데이터 디렉터리(/data)가 비루트에게 쓰이는지: 최초 설정을 실제로 해 본다
token="$(docker logs "$NAME" 2>&1 | grep -o '이 줄은 한 번만 출력됩니다): .*' | sed 's/^[^)]*): //' | head -n1)"
if [ -n "$token" ]; then
  ok "설정 토큰이 서버 로그에 출력됨 (${#token}자)"
  setup_pw="$(head -c 18 /dev/urandom | od -An -tx1 | tr -d ' \n')"  # 실행마다 무작위(소스에 비밀번호 리터럴을 두지 않음)
  setup_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -X POST "${BASE}/api/admin/setup" \
    -H 'Content-Type: application/json' -d "{\"setup_token\":\"${token}\",\"password\":\"${setup_pw}\"}")"
  if [ "$setup_code" = "200" ]; then ok "POST /api/admin/setup -> 200 (/data 쓰기 가능)"; else fail "POST /api/admin/setup -> ${setup_code}"; fi
  perms="$(docker exec "$NAME" stat -c '%a' /data/admin.json 2>/dev/null || echo none)"
  if [ "$perms" = "600" ]; then ok "/data/admin.json 권한 600"; else fail "/data/admin.json 권한 ${perms} (기대 600)"; fi
else
  fail "서버 로그에서 설정 토큰 줄을 찾지 못함"
fi
data_owner="$(docker exec "$NAME" stat -c '%u' /data)"
if [ "$data_owner" != "0" ]; then ok "/data 소유자 uid=${data_owner}"; else fail "/data 소유자가 root"; fi

# 4) 이미지에 키·비밀이 없는지 (패턴은 tools/scan_secrets.py 와 같은 계열)
PATTERNS=('sk-'"ant-[A-Za-z0-9_-]{20,}" 'sk-'"(proj-)?[A-Za-z0-9_-]{32,}" '(AKIA|ASIA)[0-9A-Z]{16}' 'gh[pousr]_[A-Za-z0-9]{36,}' 'AIza[0-9A-Za-z_-]{35}' '-----BEGIN ([A-Z]+ )?PRIVATE KEY-----')
RX="$(IFS='|'; echo "${PATTERNS[*]}")"

# 4a) 이미지 빌드 이력·환경변수·설정
meta="$(docker history --no-trunc "$IMAGE"; docker inspect "$IMAGE")"
if printf '%s' "$meta" | grep -E -q -- "$RX"; then fail "docker history/inspect 에 비밀 문자열 의심"; else ok "docker history/inspect 에 비밀 문자열 패턴 0건"; fi

# 4b) 파일 시스템 (앱·데이터·홈·설정 디렉터리. 서드파티 site-packages 는 제외)
#     /data 는 위에서 최초 설정을 했으므로 admin.json(해시)만 있고 API 키는 없어야 한다.
hits="$(docker exec "$NAME" sh -c "grep -rEIl -- '$RX' /app /data /etc /root /home /opt /srv 2>/dev/null || true")"
if [ -z "$hits" ]; then ok "파일 시스템(/app /data /etc /root /home /opt /srv)에 비밀 문자열 패턴 0건"; else fail "비밀 문자열 의심 파일: ${hits}"; fi

# 4c) 키 파일이 이미지에 들어 있지 않은지 (.env, *.key, secrets.json, instance/)
leftover="$(docker exec "$NAME" sh -c "find /app -name '.env' -o -name '*.key' -o -name 'secrets.json' -o -name instance 2>/dev/null")"
if [ -z "$leftover" ]; then ok "/app 에 .env·*.key·secrets.json·instance 없음"; else fail "이미지에 남은 파일: ${leftover}"; fi

if [ "$FAILED" != 0 ]; then
  echo "--- 컨테이너 로그(설정 토큰 줄은 가림) ---" >&2
  docker logs "$NAME" 2>&1 | sed 's/): .*/): <가림>/' >&2 || true
  exit 1
fi
echo "Docker 이미지 점검 통과"
