#!/bin/sh
set -e
if [ ! -f config.yaml ] && [ "$1" != "new" ] && [ "$1" != "serve" ]; then
  echo "config.yaml not found in $(pwd) — cd into a project under PEAKY_HOME/projects/ (or run: peaky new SLUG)" >&2
  exit 2
fi
exec peaky "$@"
