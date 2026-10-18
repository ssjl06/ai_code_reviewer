#!/bin/bash
set -euo pipefail
BUILD_DIR="${BUILD_DIR:-build}"
rm -rf "${BUILD_DIR:?}/"
