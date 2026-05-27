#!/bin/sh
set -e
cd /project
if [ ! -f config.yaml ]; then
  echo "config.yaml not found in /project — mount your project directory to /project" >&2
  exit 2
fi
exec peaky "$@"
