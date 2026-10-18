# buggy-pr smoke fixture

`./make_repo.sh <dir>` creates a repo with branch `main` (from `base/`) and
`feature` (`change/` on top) and prints both shas. Smoke-test the reviewer:

    python3 -E -s -m review --dry-run --repo <dir> --base main --head feature

Planted defects (the code is deliberately not annotated):

| File | Defect | Expected severity |
|---|---|---|
| `src/scale.cu` | `cudaMemcpyAsync` result is read and the host buffer freed without `cudaStreamSynchronize` | major |
| `scripts/clean.sh` | `rm -rf $BUILD_DIR/` unquoted, no `set -euo pipefail` | critical |
| `tools/run.py` | `subprocess.run(f"convert {name}", shell=True)` with user-supplied input | critical |
| `docs/usage.md` | documents `--threads`, which `tools/run.py` does not accept | minor |

Pass: at least 3 of 4 reported, event `REQUEST_CHANGES`.
