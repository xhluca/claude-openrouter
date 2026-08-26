#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$script_dir/.." && pwd)"

uv build --clear --directory "$repo_dir"
(
  cd "$repo_dir/dist"
  sha256sum ./* > SHA256SUMS
)

echo "Built release artifacts in $repo_dir/dist"
echo "Copy the wheel digest into install.sh before publishing."

