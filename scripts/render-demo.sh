#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$script_dir/.." && pwd)"
asset_dir="$repo_dir/docs/assets"

for command_name in agg ffmpeg identify python3 tmux; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "$command_name is required to render the demo" >&2
    exit 2
  }
done

"$script_dir/capture-demo-docker.sh"
python3 "$script_dir/speed-demo-cast.py" "$asset_dir/demo.cast" --factor 4
python3 "$script_dir/compress-demo-intro.py" \
  "$asset_dir/demo.cast" \
  --through 25 \
  --duration 12

agg \
  --theme monokai \
  --font-size 32 \
  --line-height 1.383 \
  --speed 1 \
  --idle-time-limit 3 \
  --last-frame-duration 4 \
  --cols 75 \
  --rows 24 \
  "$asset_dir/demo.cast" \
  "$asset_dir/demo.gif"

video_width=1480
video_height=1110
terminal_background=0x272822

ffmpeg -hide_banner -loglevel error -y \
  -i "$asset_dir/demo.gif" \
  -vf "fps=24,scale='min(${video_width},iw)':'min(${video_height},ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,pad=${video_width}:${video_height}:(ow-iw)/2:(oh-ih)/2:color=${terminal_background}" \
  -c:v libx264 \
  -crf 20 \
  -profile:v high \
  -level 4.0 \
  -movflags +faststart \
  -pix_fmt yuv420p \
  "$asset_dir/demo.mp4"

echo "Rendered $asset_dir/demo.gif and $asset_dir/demo.mp4"
