#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$script_dir/.." && pwd)"
asset_dir="$repo_dir/docs/assets"

for command_name in agg ffmpeg; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "$command_name is required to render the demo" >&2
    exit 2
  }
done

"$script_dir/capture-demo.sh"

agg \
  --theme monokai \
  --font-size 15 \
  --speed 1.25 \
  --idle-time-limit 2 \
  --last-frame-duration 4 \
  --cols 100 \
  --rows 34 \
  "$asset_dir/demo.cast" \
  "$asset_dir/demo.gif"

ffmpeg -hide_banner -loglevel error -y \
  -i "$asset_dir/demo.gif" \
  -vf "fps=24,scale=trunc(iw/2)*2:trunc(ih/2)*2" \
  -movflags +faststart \
  -pix_fmt yuv420p \
  "$asset_dir/demo.mp4"

echo "Rendered $asset_dir/demo.gif and $asset_dir/demo.mp4"

