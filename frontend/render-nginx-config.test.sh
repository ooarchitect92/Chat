#!/bin/sh
set -eu

renderer=${1:?renderer path is required}
template=${2:?template path is required}
test_output=$(mktemp)
trap 'rm -f "$test_output"' EXIT

S3_PUBLIC_ENDPOINT_URL=https://objects.example.com \
WEB_CSP_EXTRA_CONNECT_SRC='https://objects.example.com https://events.example.com:8443' \
  "$renderer" "$template" "$test_output"
grep -Fq "https://objects.example.com https://events.example.com:8443" "$test_output"

if S3_PUBLIC_ENDPOINT_URL=https://objects.example.com \
  WEB_CSP_EXTRA_CONNECT_SRC=https://different.example.com \
  "$renderer" "$template" "$test_output" >/dev/null 2>&1; then
  printf '%s\n' 'Renderer accepted a CSP that omits the upload origin.' >&2
  exit 1
fi

if S3_PUBLIC_ENDPOINT_URL=https://objects.example.com/path \
  WEB_CSP_EXTRA_CONNECT_SRC=https://objects.example.com/path \
  "$renderer" "$template" "$test_output" >/dev/null 2>&1; then
  printf '%s\n' 'Renderer accepted a non-origin S3 public endpoint.' >&2
  exit 1
fi

if S3_PUBLIC_ENDPOINT_URL=https://objects.example.com \
  WEB_CSP_EXTRA_CONNECT_SRC='https://objects.example.com; script-src *' \
  "$renderer" "$template" "$test_output" >/dev/null 2>&1; then
  printf '%s\n' 'Renderer accepted a CSP source injection.' >&2
  exit 1
fi
