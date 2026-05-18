#!/bin/bash
# ========================================
#  KB - CodeBuddy 符号链接设置脚本
#  项目: 3DPipeLine
# ========================================

set -e

# 切换到脚本所在目录（即 KB 目录）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
KB_PATH="$SCRIPT_DIR"

echo "========================================"
echo " KB - CodeBuddy 符号链接设置脚本"
echo " 项目: 3DPipeLine"
echo "========================================"
echo ""
echo "当前 KB 路径: $KB_PATH"
echo ""

# ========================================
# 第1步：查找项目根目录（.codebuddy 所在目录）
# ========================================
echo "[1/4] 查找项目根目录..."

PROJECT_PATH=""

# KB 在项目根目录内，.codebuddy 在项目根目录下
if [ -d "$KB_PATH/../.codebuddy" ]; then
    PROJECT_PATH="$(cd "$KB_PATH/.." && pwd)"
elif [ -d "$KB_PATH/.codebuddy" ]; then
    PROJECT_PATH="$KB_PATH"
fi

if [ -z "$PROJECT_PATH" ]; then
    # .codebuddy 尚不存在，假设 KB 直接位于项目根目录下
    PROJECT_PATH="$(cd "$KB_PATH/.." && pwd)"
    echo "  [提示] .codebuddy 尚不存在，将在第2步创建"
fi

echo "  [OK] 项目根目录: $PROJECT_PATH"
echo ""

# ========================================
# 第2步：确保 .codebuddy 目录存在
# ========================================
echo "[2/4] 检查 .codebuddy 目录..."

CODEBUDDY_PATH="$PROJECT_PATH/.codebuddy"

if [ ! -d "$CODEBUDDY_PATH" ]; then
    echo "  .codebuddy 不存在，正在创建..."
    mkdir -p "$CODEBUDDY_PATH"
    echo "  [OK] 已创建 .codebuddy 目录"
else
    echo "  [OK] .codebuddy 目录已存在"
fi
echo ""

# ========================================
# 第3步：遍历 KB 下所有子文件夹，创建符号链接
# ========================================
echo "[3/4] 创建符号链接..."
echo ""

LINK_COUNT=0
FAIL_COUNT=0

for DIR in "$KB_PATH"/*/; do
    # 获取文件夹名
    FOLDER_NAME="$(basename "$DIR")"
    TARGET_FULL="$DIR"
    # 转为小写作为 .codebuddy 下的链接名
    LINK_NAME="$(echo "$FOLDER_NAME" | tr 'A-Z' 'a-z')"
    LINK_FULL="$CODEBUDDY_PATH/$LINK_NAME"

    # 如果 .codebuddy 下已存在该目录
    if [ -e "$LINK_FULL" ]; then
        # 检查是否已经是符号链接
        if [ -L "$LINK_FULL" ]; then
            echo "  [$LINK_NAME] 已是符号链接，移除旧链接..."
            rm -f "$LINK_FULL"
        else
            # 普通目录，备份后再创建链接
            if [ -e "${LINK_FULL}.backup" ]; then
                echo "  [$LINK_NAME] 已存在普通目录，backup 也已存在，移除旧目录..."
                rm -rf "$LINK_FULL"
            else
                echo "  [$LINK_NAME] 已存在普通目录，备份为 ${LINK_NAME}.backup..."
                mv "$LINK_FULL" "${LINK_FULL}.backup"
            fi
        fi
    fi

    # 创建符号链接
    if ln -s "$TARGET_FULL" "$LINK_FULL" 2>/dev/null; then
        echo "  [OK] $LINK_NAME --> $TARGET_FULL"
        LINK_COUNT=$((LINK_COUNT + 1))
    else
        echo "  [失败] $LINK_NAME 链接创建失败"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done

echo ""
echo "  共处理 $LINK_COUNT 个链接"
if [ $FAIL_COUNT -gt 0 ]; then
    echo "  失败 $FAIL_COUNT 个"
fi
echo ""

# ========================================
# 第4步：验证
# ========================================
echo "[4/4] 验证符号链接..."
echo ""

ALL_OK=1
for DIR in "$KB_PATH"/*/; do
    FOLDER_NAME="$(basename "$DIR")"
    LINK_NAME="$(echo "$FOLDER_NAME" | tr 'A-Z' 'a-z')"
    LINK_FULL="$CODEBUDDY_PATH/$LINK_NAME"

    if [ -e "$LINK_FULL" ]; then
        echo "  [OK] $LINK_NAME"
    else
        echo "  [失败] $LINK_NAME"
        ALL_OK=0
    fi
done

echo ""
if [ "$ALL_OK" -eq 1 ]; then
    echo "========================================"
    echo " 全部设置成功!"
    echo "========================================"
    echo ""
    echo "现在可以用 CodeBuddy 打开 3DPipeLine 项目，"
    echo "它将自动识别 KB 中的资源。"
else
    echo "========================================"
    echo " 部分链接设置失败，请检查上方错误信息"
    echo "========================================"
fi
