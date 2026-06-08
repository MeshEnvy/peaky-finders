#!/bin/sh
set -e
cd /project
if [ ! -f config.yaml ] && [ "$1" != "new" ]; then
  echo "config.yaml not found in /project — mount your project directory to /project (or run: peaky new SLUG)" >&2
  exit 2
fi
exec peaky "$@"
