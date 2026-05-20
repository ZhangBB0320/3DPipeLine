#!/bin/bash
# 批处理 /Users/zbb/Downloads/Building 下的 14 个 FBX 文件
# 流程：高模FBX → pymeshlab减面到3000面 → Blender烘焙(albedo/normal/roughness) → 内嵌纹理低模FBX

BLENDER="/Applications/Blender.app/Contents/MacOS/Blender"
SCRIPT="/Users/zbb/3DPipeLine/Scripts/batch_bake_fbx.py"
OUT_DIR="/Users/zbb/3DPipeLine/outPut"
BUILDING_DIR="/Users/zbb/Downloads/Building"

mkdir -p "$OUT_DIR"

# 收集所有 FBX
fbx_files=()
for f in "$BUILDING_DIR"/*.fbx; do
    [ -f "$f" ] && fbx_files+=("$f")
done
total=${#fbx_files[@]}

if [ "$total" -eq 0 ]; then
    echo "ERROR: No FBX files found in $BUILDING_DIR"
    exit 1
fi

echo "===================================================="
echo "  Building Batch Bake: $total files"
echo "  Target: 3000 faces"
echo "  Output: $OUT_DIR"
echo "===================================================="

success=0
failed=0
failed_list=""

for fbx in "${fbx_files[@]}"; do
    idx=$((success + failed + 1))
    name=$(basename "$fbx" .fbx)
    out_name="${name}_Low.fbx"

    echo ""
    echo "===================================================="
    echo "  >>> [$idx/$total] $name"
    echo "===================================================="

    if "$BLENDER" --background --python "$SCRIPT" -- \
        --fbx "$fbx" \
        --out "$OUT_DIR/$out_name" \
        --cage 0.2 \
        --target 3000 \
        2>&1 | tail -80; then

        if [ -f "$OUT_DIR/$out_name" ]; then
            sz=$(stat -f%z "$OUT_DIR/$out_name" 2>/dev/null || stat -c%s "$OUT_DIR/$out_name" 2>/dev/null)
            echo "  ✓ $out_name : $((sz/1024)) KB"
            ((success++))
        else
            echo "  ✗ FAILED: $out_name not generated"
            ((failed++))
            failed_list="$failed_list $name"
        fi
    else
        echo "  ✗ FAILED: Blender error"
        ((failed++))
        failed_list="$failed_list $name"
    fi
done

echo ""
echo "===================================================="
echo "  Batch complete: $success/$total success"
if [ "$failed" -gt 0 ]; then
    echo "  Failed:$failed_list"
fi
echo "===================================================="
ls -lh "$OUT_DIR"/*_Low.fbx 2>/dev/null || echo "No output files"
