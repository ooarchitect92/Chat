#!/usr/bin/env bash
set -Eeuo pipefail

required_paths=(
  "frontend/package.json"
  "frontend/Dockerfile"
  "backend/pyproject.toml"
  "backend/Dockerfile"
  "backend/src/northstar_api"
  "backend/migrations"
  "database/postgres"
  "docker/README.md"
  "infrastructure/redis/README.md"
  "infrastructure/rabbitmq/README.md"
  "infrastructure/kafka/README.md"
  "infrastructure/minio/README.md"
  "kubernetes/base/kustomization.yaml"
  "kubernetes/overlays/production/kustomization.yaml"
  "compose.yaml"
)

for path in "${required_paths[@]}"; do
  if [[ ! -e "$path" ]]; then
    echo "Architecture contract violation: required path is missing: $path" >&2
    exit 1
  fi
done

# These obsolete umbrella roots duplicate canonical application and
# infrastructure boundaries and previously produced confusing empty folders.
legacy_roots=(apps deploy infra services)
for path in "${legacy_roots[@]}"; do
  if [[ -e "$path" ]]; then
    echo "Architecture contract violation: obsolete root exists: $path" >&2
    echo "Use frontend/, backend/, kubernetes/, or infrastructure/ instead." >&2
    exit 1
  fi
done

echo "Repository architecture contract passed."
