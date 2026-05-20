#!/bin/bash
# NoBake 减面批量处理脚本
# 用法:
#   单个模型:   bash batch_nobake.sh /path/to/model.fbx 3000
#   批量目录:   bash batch_nobake.sh /path/to/fbx_dir/ 3000
#   仅指定目录: bash batch_nobake.sh  (默认处理 ~/Downloads/Building/ 下所有 FBX)

BLENDER="/Applications/Blender.app/Contents/MacOS/Blender"
SCRIPT="$(dirname "$0")/nobake_decimate.py"

INPUT="${1:-$HOME/Downloads/Building/}"
TARGET="${2:-3000}"
OUTPUT="$(dirname "$0")/../outPut"

echo "=== NoBake Decimation Pipeline ==="
echo "Input:  $INPUT"
echo "Target: $TARGET faces"
echo "Output: $OUTPUT"
echo "=================================="

if [ ! -f "$BLENDER" ]; then
    echo "[ERROR] Blender not found at $BLENDER"
    exit 1
fi

if [ ! -f "$SCRIPT" ]; then
    echo "[ERROR] Script not found at $SCRIPT"
    exit 1
fi

"$BLENDER" --background --python "$SCRIPT" -- \
    --input "$INPUT" \
    --output "$OUTPUT" \
    --target "$TARGET" \
    --render
