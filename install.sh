#!/usr/bin/env bash
# Install (or update) the AI review kit into a target repository.
#   ./install.sh [--force-workflow] <target-repo-dir>
# Copies review/, agent/, config/ into <target>/.github/ai-review/ (replacing it) and
# templates/ai-review.yml to <target>/.github/workflows/ai-review.yml if that file does
# not exist yet (or always with --force-workflow). Nothing else in the target is touched.
set -euo pipefail

usage() {
  echo "usage: $0 [--force-workflow] <target-repo-dir>" >&2
  exit 2
}

force_workflow=0
target=""
for arg in "$@"; do
  case "$arg" in
    --force-workflow) force_workflow=1 ;;
    -h|--help) usage ;;
    -*) echo "unknown option: $arg" >&2; usage ;;
    *) [[ -z "$target" ]] || usage; target="$arg" ;;
  esac
done
[[ -n "$target" ]] || usage
[[ -d "$target" ]] || { echo "error: not a directory: $target" >&2; exit 1; }

kit_src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="$(cd "$target" && pwd)"
[[ "$target" != "$kit_src" ]] || { echo "error: target is the kit repository itself" >&2; exit 1; }
kit_dst="$target/.github/ai-review"
workflow_dst="$target/.github/workflows/ai-review.yml"

version="$(git -C "$kit_src" rev-parse HEAD 2>/dev/null || echo unknown)"
if [[ "$version" != unknown ]] && [[ -n "$(git -C "$kit_src" status --porcelain -- review agent config templates 2>/dev/null)" ]]; then
  version="$version-dirty"
fi

# Build the new copy next to the destination, then swap it in.
mkdir -p "$target/.github"
staging="$(mktemp -d "$target/.github/.ai-review.tmp.XXXXXX")"
trap 'rm -rf "$staging"' EXIT
for d in review agent config; do
  cp -R "$kit_src/$d" "$staging/$d"
done
find "$staging" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$staging" -name '*.pyc' -type f -delete
echo "$version" > "$staging/VERSION"
printf '__pycache__/\n.ai-review-out/\n' > "$staging/.gitignore"
cat > "$staging/README.md" <<README
# AI review kit (installed copy)

This directory is a copy of the AI PR review kit, installed by \`install.sh\`
from the kit repository (version: $version, see \`VERSION\`).
It is run by \`.github/workflows/ai-review.yml\` and excluded from reviews.

Do not edit it by hand: change the kit repository and re-run
\`install.sh <this-repo>\`, then commit the result.
README

rm -rf "$kit_dst"
mv "$staging" "$kit_dst"
trap - EXIT
echo "installed kit $version -> $kit_dst"

if [[ -e "$workflow_dst" && "$force_workflow" -eq 0 ]]; then
  echo "kept existing $workflow_dst (use --force-workflow to overwrite)"
else
  mkdir -p "$(dirname "$workflow_dst")"
  cp "$kit_src/templates/ai-review.yml" "$workflow_dst"
  echo "wrote $workflow_dst"
fi

cat <<NEXT

Next steps:
  1. Set the secrets (repository or organization level):
       AI_REVIEW_BOT_PAT, AI_REVIEW_BOT_NAME, AI_REVIEW_LLM_API_KEY, AI_REVIEW_LLM_BASE_URL
  2. Check runs-on and the env values at the top of .github/workflows/ai-review.yml.
  3. Commit and open a pull request:
       cd "$target"
       git add .github/ai-review .github/workflows/ai-review.yml
       git commit -m "ci: install AI review kit $version"
NEXT
