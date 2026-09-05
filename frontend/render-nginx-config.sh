#!/bin/sh
set -eu
set -f

template_path=${1:?template path is required}
output_path=${2:?output path is required}
required_upload_origin=${S3_PUBLIC_ENDPOINT_URL:-}
csp_origins=${WEB_CSP_EXTRA_CONNECT_SRC:-}
rendered_origins=
required_origin_found=false

validate_origin() {
  candidate=$1
  variable_name=$2
  if ! printf '%s\n' "$candidate" | grep -Eq '^https?://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$'; then
    printf '%s\n' "$variable_name must contain only HTTP(S) origins without paths, credentials, queries, or fragments." >&2
    exit 1
  fi
  authority=${candidate#*://}
  case "$authority" in
    *:*)
      port=${authority##*:}
      if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        printf '%s\n' "$variable_name contains an invalid port." >&2
        exit 1
      fi
      ;;
  esac
}

if [ -z "$required_upload_origin" ]; then
  printf '%s\n' 'S3_PUBLIC_ENDPOINT_URL is required when rendering the admin CSP.' >&2
  exit 1
fi
validate_origin "$required_upload_origin" S3_PUBLIC_ENDPOINT_URL

for origin in $csp_origins; do
  validate_origin "$origin" WEB_CSP_EXTRA_CONNECT_SRC
  if [ "$origin" = "$required_upload_origin" ]; then
    required_origin_found=true
  fi
  if [ -n "$rendered_origins" ]; then
    rendered_origins="$rendered_origins $origin"
  else
    rendered_origins=$origin
  fi
done

if [ "$required_origin_found" != true ]; then
  printf '%s\n' 'WEB_CSP_EXTRA_CONNECT_SRC must include S3_PUBLIC_ENDPOINT_URL exactly so the admin CSP permits direct browser uploads.' >&2
  exit 1
fi

if ! grep -q '__WEB_CSP_EXTRA_CONNECT_SRC__' "$template_path"; then
  printf '%s\n' 'Nginx template is missing its CSP origin placeholder.' >&2
  exit 1
fi

# The validator excludes sed replacement metacharacters and canonicalizes all
# whitespace, so only CSP source expressions can enter the quoted header.
sed "s|__WEB_CSP_EXTRA_CONNECT_SRC__|$rendered_origins|g" "$template_path" > "$output_path"
if grep -q '__WEB_CSP_EXTRA_CONNECT_SRC__' "$output_path"; then
  printf '%s\n' 'Nginx CSP template rendering was incomplete.' >&2
  exit 1
fi
