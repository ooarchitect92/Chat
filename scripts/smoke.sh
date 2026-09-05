#!/usr/bin/env bash
set -Eeuo pipefail

WEB_URL="${WEB_URL:-http://localhost:3000}"
API_URL="${API_URL:-http://localhost:8000}"
OBJECT_STORE_URL="${OBJECT_STORE_URL:-http://localhost:9000}"
SMOKE_WEB_ORIGIN="${SMOKE_WEB_ORIGIN:-http://localhost:3000}"
SMOKE_TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-180}"
SMOKE_ADMIN_EMAIL="${SMOKE_ADMIN_EMAIL:-}"
SMOKE_ADMIN_PASSWORD="${SMOKE_ADMIN_PASSWORD:-}"

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Required command not found: $1" >&2
    exit 2
  }
}

wait_for_http() {
  local name="$1"
  local url="$2"
  local deadline=$((SECONDS + SMOKE_TIMEOUT_SECONDS))

  while (( SECONDS < deadline )); do
    if curl --fail --silent --show-error --max-time 5 "$url" >/dev/null 2>&1; then
      printf '%-18s %s\n' "$name" "ready"
      return 0
    fi
    sleep 2
  done

  echo "$name did not become ready within ${SMOKE_TIMEOUT_SECONDS}s ($url)" >&2
  docker compose ps >&2 || true
  return 1
}

wait_for_container_health() {
  local service="$1"
  local deadline=$((SECONDS + SMOKE_TIMEOUT_SECONDS))

  while (( SECONDS < deadline )); do
    local container_id
    container_id="$(docker compose ps --all --quiet "$service")"
    if [[ -n "$container_id" ]]; then
      local health
      health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
      if [[ "$health" == "healthy" ]]; then
        printf '%-18s %s\n' "$service" "healthy"
        return 0
      fi
      if [[ "$health" == "unhealthy" || "$health" == "exited" || "$health" == "dead" ]]; then
        echo "$service entered terminal state: $health" >&2
        docker compose logs --tail=80 "$service" >&2 || true
        return 1
      fi
    fi
    sleep 2
  done

  echo "$service did not become healthy within ${SMOKE_TIMEOUT_SECONDS}s" >&2
  docker compose ps >&2 || true
  return 1
}

wait_for_one_shot() {
  local service="$1"
  local deadline=$((SECONDS + SMOKE_TIMEOUT_SECONDS))

  while (( SECONDS < deadline )); do
    local container_id
    container_id="$(docker compose ps --all --quiet "$service")"
    if [[ -n "$container_id" ]]; then
      local status
      local exit_code
      status="$(docker inspect --format '{{.State.Status}}' "$container_id" 2>/dev/null || true)"
      exit_code="$(docker inspect --format '{{.State.ExitCode}}' "$container_id" 2>/dev/null || true)"
      if [[ "$status" == "exited" && "$exit_code" == "0" ]]; then
        printf '%-18s %s\n' "$service" "completed"
        return 0
      fi
      if [[ "$status" == "exited" || "$status" == "dead" ]]; then
        echo "$service did not complete successfully: $status $exit_code" >&2
        docker compose logs --tail=80 "$service" >&2 || true
        return 1
      fi
    fi
    sleep 2
  done

  echo "$service did not complete within ${SMOKE_TIMEOUT_SECONDS}s" >&2
  docker compose logs --tail=80 "$service" >&2 || true
  return 1
}

assert_container_running() {
  local service="$1"
  local container_id
  container_id="$(docker compose ps --all --quiet "$service")"
  [[ -n "$container_id" ]] || { echo "$service container was not created" >&2; return 1; }
  local status
  status="$(docker inspect --format '{{.State.Status}}' "$container_id")"
  [[ "$status" == "running" ]] || {
    echo "$service is not running: $status" >&2
    docker compose logs --tail=80 "$service" >&2 || true
    return 1
  }
  printf '%-18s %s\n' "$service" "running"
}

assert_object_store_cors() {
  local path="$OBJECT_STORE_URL/northstar-cors-check/probe"
  local method
  for method in GET HEAD PUT POST; do
    local allowed_headers
    allowed_headers="$(curl --fail --silent --show-error --max-time 10 --dump-header - --output /dev/null \
      --request OPTIONS \
      --header "Origin: $SMOKE_WEB_ORIGIN" \
      --header "Access-Control-Request-Method: $method" \
      --header "Access-Control-Request-Headers: content-type,x-amz-meta-sha256" \
      "$path")"
    grep -Fqi "Access-Control-Allow-Origin: $SMOKE_WEB_ORIGIN" <<<"$allowed_headers" || {
      echo "Object storage did not allow $method from the configured smoke-test web origin" >&2
      return 1
    }
  done

  local denied_headers
  denied_headers="$(curl --fail --silent --show-error --max-time 10 --dump-header - --output /dev/null \
    --request OPTIONS \
    --header "Origin: https://cors-deny-check.invalid" \
    --header "Access-Control-Request-Method: PUT" \
    "$path")"
  if grep -Fqi "Access-Control-Allow-Origin:" <<<"$denied_headers"; then
    echo "Object storage unexpectedly allowed an untrusted smoke-test origin" >&2
    return 1
  fi
  printf '%-18s %s\n' "Object CORS" "restricted"
}

assert_presigned_upload_flow() {
  if [[ -z "$SMOKE_ADMIN_EMAIL" && -z "$SMOKE_ADMIN_PASSWORD" ]]; then
    printf '%-18s %s\n' "Upload flow" "skipped (set SMOKE_ADMIN_EMAIL and SMOKE_ADMIN_PASSWORD)"
    return 0
  fi
  if [[ -z "$SMOKE_ADMIN_EMAIL" || -z "$SMOKE_ADMIN_PASSWORD" ]]; then
    echo "Set both SMOKE_ADMIN_EMAIL and SMOKE_ADMIN_PASSWORD, or neither" >&2
    return 1
  fi

  local api_base="${WEB_URL%/}/api/v1"
  local payload_file
  local response_headers
  payload_file="$(mktemp)"
  response_headers="$(mktemp)"
  local source_id=""
  local access_token=""

  cleanup_upload_probe() {
    trap - RETURN EXIT
    if [[ -n "$source_id" && -n "$access_token" ]]; then
      curl --silent --show-error --max-time 30 --output /dev/null --request DELETE \
        --header "Authorization: Bearer $access_token" \
        "$api_base/knowledge/$source_id" || true
    fi
    rm -f -- "$payload_file" "$response_headers"
  }
  trap cleanup_upload_probe RETURN EXIT

  printf '%s' 'Northstar presigned POST integration smoke.' >"$payload_file"
  local size_bytes
  local checksum_sha256
  size_bytes="$(wc -c <"$payload_file")"
  size_bytes="${size_bytes//[[:space:]]/}"
  checksum_sha256="$(docker compose exec -T api python -c \
    'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())' <"$payload_file")"

  local login_json
  login_json="$(printf '{"email":"%s","password":"%s"}' "$SMOKE_ADMIN_EMAIL" "$SMOKE_ADMIN_PASSWORD" | \
    curl --fail-with-body --silent --show-error --max-time 30 \
      --header 'Content-Type: application/json' \
      --data-binary @- \
      "$api_base/auth/login")"
  access_token="$(printf '%s' "$login_json" | docker compose exec -T api python -c \
    'import json, sys; print(json.load(sys.stdin)["accessToken"])')"

  local agents_json
  local agent_id
  agents_json="$(curl --fail-with-body --silent --show-error --max-time 30 \
    --header "Authorization: Bearer $access_token" \
    "$api_base/agents")"
  agent_id="$(printf '%s' "$agents_json" | docker compose exec -T api python -c \
    'import json, sys; data=json.load(sys.stdin); assert data, "no agent available for upload smoke"; print(data[0]["id"])')"

  local integrations_json
  integrations_json="$(curl --fail-with-body --silent --show-error --max-time 30 \
    --header "Authorization: Bearer $access_token" \
    "$api_base/integrations")"
  printf '%s' "$integrations_json" | docker compose exec -T api python -c \
    'import json, sys; data=json.load(sys.stdin); required={"website", "slack", "whatsapp", "zapier", "notion", "api", "teams"}; ids={item["id"] for item in data}; missing=sorted(required-ids); assert not missing, f"integration catalog is incomplete: {missing}"'
  printf '%-18s %s\n' "App catalog" "populated"

  local presign_json
  presign_json="$(printf '{"filename":"infra-smoke.txt","contentType":"text/plain","sizeBytes":%s,"checksumSha256":"%s"}' "$size_bytes" "$checksum_sha256" | \
    curl --fail-with-body --silent --show-error --max-time 30 \
      --header "Authorization: Bearer $access_token" \
      --header 'Content-Type: application/json' \
      --data-binary @- \
      "$api_base/uploads/presign")"

  local upload_url
  local object_key
  upload_url="$(printf '%s' "$presign_json" | docker compose exec -T api python -c \
    'import json, sys; data=json.load(sys.stdin); assert data["method"] == "POST"; print(data["url"])')"
  object_key="$(printf '%s' "$presign_json" | docker compose exec -T api python -c \
    'import json, sys; data=json.load(sys.stdin); key=data["objectKey"]; assert key.startswith("staging/"); print(key)')"

  local -a form_arguments=()
  while IFS=$'\t' read -r field_name field_value; do
    [[ -n "$field_name" ]] || continue
    form_arguments+=(--form-string "$field_name=$field_value")
  done < <(printf '%s' "$presign_json" | docker compose exec -T api python -c \
    'import json, sys; data=json.load(sys.stdin); [print(f"{key}\t{value}") for key, value in data["fields"].items()]')
  # S3-compatible POST handlers require the file part after all signed fields.
  form_arguments+=(--form "file=@$payload_file;type=text/plain")

  local upload_status
  upload_status="$(curl --fail --silent --show-error --max-time 30 \
    --dump-header "$response_headers" \
    --output /dev/null \
    --write-out '%{http_code}' \
    --request POST \
    --header "Origin: $SMOKE_WEB_ORIGIN" \
    "${form_arguments[@]}" \
    "$upload_url")"
  [[ "$upload_status" == "200" || "$upload_status" == "204" ]] || {
    echo "Presigned object upload returned HTTP $upload_status" >&2
    return 1
  }
  grep -Fqi "Access-Control-Allow-Origin: $SMOKE_WEB_ORIGIN" "$response_headers" || {
    echo "Presigned object upload response omitted the configured CORS origin" >&2
    return 1
  }

  local source_json
  source_json="$(printf '{"name":"Infrastructure upload smoke","kind":"file","objectKey":"%s"}' "$object_key" | \
    curl --fail-with-body --silent --show-error --max-time 30 \
      --header "Authorization: Bearer $access_token" \
      --header 'Content-Type: application/json' \
      --data-binary @- \
      "$api_base/agents/$agent_id/knowledge")"
  source_id="$(printf '%s' "$source_json" | docker compose exec -T api python -c \
    'import json, sys; print(json.load(sys.stdin)["id"])')"

  local deadline=$((SECONDS + SMOKE_TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    local sources_json
    local source_status
    sources_json="$(curl --fail-with-body --silent --show-error --max-time 30 \
      --header "Authorization: Bearer $access_token" \
      "$api_base/agents/$agent_id/knowledge")"
    source_status="$(printf '%s' "$sources_json" | docker compose exec -T api python -c \
      'import json, sys; source_id=sys.argv[1]; data=json.load(sys.stdin); print(next(item["status"] for item in data if item["id"] == source_id))' \
      "$source_id")"
    if [[ "$source_status" == "ready" ]]; then
      printf '%-18s %s\n' "Upload flow" "promoted and ingested"
      return 0
    fi
    if [[ "$source_status" == "failed" ]]; then
      echo "Uploaded smoke source entered failed state" >&2
      return 1
    fi
    sleep 2
  done

  echo "Uploaded smoke source did not finish within ${SMOKE_TIMEOUT_SECONDS}s" >&2
  return 1
}

require_command curl
require_command docker
require_command grep
require_command mktemp
require_command wc

for service in postgres redis rabbitmq kafka minio; do
  wait_for_container_health "$service"
done
wait_for_one_shot migrate
wait_for_one_shot minio-init
assert_object_store_cors

for service in api web worker; do
  wait_for_container_health "$service"
done
for service in job-dispatcher outbox-relay analytics-consumer object-cleaner; do
  assert_container_running "$service"
done

wait_for_http "API liveness" "$API_URL/health/live"
wait_for_http "API readiness" "$API_URL/health/ready"
wait_for_http "Web application" "$WEB_URL/healthz"
assert_presigned_upload_flow

docker compose ps --status running
echo "Smoke checks passed."
