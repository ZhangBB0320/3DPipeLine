#!/bin/bash
# 批处理 /Users/zbb/Downloads/Weapon 下的 3 个 .blend 文件
set -e

BLENDER="/Applications/Blender.app/Contents/MacOS/Blender"
SCRIPT="/Users/zbb/3DPipeLine/Scripts/batch_bake.py"
OUT_DIR="/Users/zbb/3DPipeLine/outPut"
WEAPON_DIR="/Users/zbb/Downloads/Weapon"

mkdir -p "$OUT_DIR"

run_one() {
    local pkg="$1"        # 包目录名 (e.g. antique_estoc_1k.blend)
    local obj_name="$2"   # mesh 名 (e.g. antique_estoc)
    local out_name="$3"   # 输出 fbx 文件名 (e.g. antique_estoc_Low.fbx)
    local cage="${4:-1.5}"
    local blend_file="$WEAPON_DIR/$pkg/$pkg"

    echo ""
    echo "===================================================="
    echo "  >>> $obj_name"
    echo "  blend: $blend_file"
    echo "  out:   $OUT_DIR/$out_name"
    echo "===================================================="

    "$BLENDER" "$blend_file" --background --python "$SCRIPT" -- \
        --object "$obj_name" \
        --out "$OUT_DIR/$out_name" \
        --cage "$cage" \
        2>&1 | tail -60

    if [ -f "$OUT_DIR/$out_name" ]; then
        local sz=$(stat -f%z "$OUT_DIR/$out_name")
        echo "  ✓ $out_name : $((sz/1024)) KB"
    else
        echo "  ✗ FAILED: $out_name not generated"
        return 1
    fi
}

# 武器列表：包目录 / mesh 名 / 输出文件名 / cage
run_one "antique_estoc_1k.blend"        "antique_estoc"        "antique_estoc_Low.fbx"        0.3
run_one "wooden_axe_1k.blend"           "wooden_axe"           "wooden_axe_Low.fbx"           0.3
run_one "wooden_handle_saber_1k.blend"  "wooden_handle_saber"  "wooden_handle_saber_Low.fbx"  0.3

echo ""
echo "===================================================="
echo "  All weapons done!"
echo "===================================================="
ls -lh "$OUT_DIR"/*_Low.fbx
