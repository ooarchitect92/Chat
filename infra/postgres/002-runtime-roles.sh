#!/bin/sh
set -eu

: "${POSTGRES_RUNTIME_USER:?POSTGRES_RUNTIME_USER is required}"
: "${POSTGRES_RUNTIME_PASSWORD:?POSTGRES_RUNTIME_PASSWORD is required}"
: "${POSTGRES_SERVICE_USER:?POSTGRES_SERVICE_USER is required}"
: "${POSTGRES_SERVICE_PASSWORD:?POSTGRES_SERVICE_PASSWORD is required}"

case "$POSTGRES_RUNTIME_USER" in
  *[!A-Za-z0-9_]*|'') echo "POSTGRES_RUNTIME_USER contains unsupported characters" >&2; exit 1 ;;
esac
case "$POSTGRES_SERVICE_USER" in
  *[!A-Za-z0-9_]*|'') echo "POSTGRES_SERVICE_USER contains unsupported characters" >&2; exit 1 ;;
esac
if [ "$POSTGRES_RUNTIME_USER" != "northstar_app" ]; then
  echo "POSTGRES_RUNTIME_USER must be northstar_app (the role is part of the migration contract)" >&2
  exit 1
fi
if [ "$POSTGRES_SERVICE_USER" != "northstar_service" ]; then
  echo "POSTGRES_SERVICE_USER must be northstar_service (the role is part of the worker contract)" >&2
  exit 1
fi

# psql variables keep identifiers and password literals correctly quoted. Do not
# enable shell tracing here: these values are credentials.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --no-psqlrc \
  --set=ON_ERROR_STOP=1 \
  --set=runtime_user="$POSTGRES_RUNTIME_USER" \
  --set=runtime_password="$POSTGRES_RUNTIME_PASSWORD" \
  --set=service_user="$POSTGRES_SERVICE_USER" \
  --set=service_password="$POSTGRES_SERVICE_PASSWORD" <<'EOSQL'
SELECT format(
  'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
  :'runtime_user', :'runtime_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'runtime_user')
\gexec

SELECT format(
  'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION BYPASSRLS INHERIT PASSWORD %L',
  :'service_user', :'service_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'service_user')
\gexec

SELECT format('GRANT %I TO %I', :'runtime_user', :'service_user')
\gexec
EOSQL
