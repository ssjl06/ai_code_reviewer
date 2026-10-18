#!/bin/bash
# Usage: make_repo.sh <dir>  -- creates a git repo with branches main and feature.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <dir>" >&2
  exit 2
fi
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest="$1"
mkdir -p "$dest"
git -C "$dest" init -q -b main
git -C "$dest" config user.name "fixture"
git -C "$dest" config user.email "fixture@example.invalid"
git -C "$dest" config commit.gpgsign false

cp -R "$here/base/." "$dest/"
git -C "$dest" add -A
git -C "$dest" commit -q -m "base"
git -C "$dest" checkout -q -b feature
cp -R "$here/change/." "$dest/"
git -C "$dest" add -A
git -C "$dest" commit -q -m "feature: resize option and host copy"

echo "main=$(git -C "$dest" rev-parse main)"
echo "feature=$(git -C "$dest" rev-parse feature)"
