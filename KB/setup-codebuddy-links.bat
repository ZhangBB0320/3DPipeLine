@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ========================================
echo  KB - CodeBuddy 符号链接设置脚本
echo  项目: 3DPipeLine
echo ========================================
echo.

REM 切换到脚本所在目录（即 KB 目录）
cd /d "%~dp0"
set "KB_PATH=%cd%"

echo 当前 KB 路径: %KB_PATH%
echo.

REM ========================================
REM 第1步：查找项目根目录（.codebuddy 所在目录）
REM ========================================
echo [1/4] 查找项目根目录...

set "PROJECT_PATH="

REM KB 和项目根目录在同一父目录下（KB 在项目根目录内）
if exist "%KB_PATH%\..\.codebuddy" (
    pushd "%KB_PATH%\.."
    set "PROJECT_PATH=!cd!"
    popd
) else if exist "%KB_PATH%\.codebuddy" (
    set "PROJECT_PATH=!KB_PATH!"
)

if "!PROJECT_PATH!"=="" (
    echo [错误] 找不到包含 .codebuddy 的项目根目录
    echo.
    echo 请确保 KB 和 .codebuddy 在同一项目目录下:
    echo   3DPipeLine\
    echo     +-- KB\          ^(当前位置^)
    echo     +-- .codebuddy\
    pause
    exit /b 1
)

echo   [OK] 项目根目录: !PROJECT_PATH!
echo.

REM ========================================
REM 第2步：确保 .codebuddy 目录存在
REM ========================================
echo [2/4] 检查 .codebuddy 目录...

set "CODEBUDDY_PATH=!PROJECT_PATH!\.codebuddy"

if not exist "!CODEBUDDY_PATH!" (
    echo   .codebuddy 不存在，正在创建...
    mkdir "!CODEBUDDY_PATH!"
    echo   [OK] 已创建 .codebuddy 目录
) else (
    echo   [OK] .codebuddy 目录已存在
)
echo.

REM ========================================
REM 第3步：遍历 KB 下所有子文件夹，创建符号链接
REM ========================================
echo [3/4] 创建符号链接...
echo.

set "LINK_COUNT=0"
set "FAIL_COUNT=0"

for /d %%D in ("%KB_PATH%\*") do (
    set "FOLDER_NAME=%%~nxD"
    set "TARGET_FULL=%%D"

    REM 将文件夹名转为小写作为 .codebuddy 下的链接名
    set "LINK_NAME=!FOLDER_NAME!"
    call :to_lower LINK_NAME

    set "LINK_FULL=!CODEBUDDY_PATH!\!LINK_NAME!"

    REM 如果 .codebuddy 下已存在该目录
    if exist "!LINK_FULL!" (
        REM 检查是否已经是 Junction 符号链接
        set "IS_JUNCTION=0"
        for /f "tokens=*" %%L in ('dir "!CODEBUDDY_PATH!" 2^>nul ^| findstr /C:"<JUNCTION>" ^| findstr /C:"!LINK_NAME!"') do (
            set "IS_JUNCTION=1"
        )

        if "!IS_JUNCTION!"=="1" (
            echo   [!LINK_NAME!] 已是符号链接，移除旧链接...
            rmdir "!LINK_FULL!" 2>nul
        ) else (
            REM 普通目录，备份后再创建链接
            if exist "!LINK_FULL!.backup" (
                echo   [!LINK_NAME!] 已存在普通目录，backup 也已存在，移除旧目录...
                rmdir /s /q "!LINK_FULL!" 2>nul
            ) else (
                echo   [!LINK_NAME!] 已存在普通目录，备份为 !LINK_NAME!.backup...
                rename "!LINK_FULL!" "!LINK_NAME!.backup" 2>nul
            )
        )
    )

    REM 创建符号链接
    mklink /J "!LINK_FULL!" "!TARGET_FULL!" >nul 2>&1
    if !errorlevel! equ 0 (
        echo   [OK] !LINK_NAME! --^> !TARGET_FULL!
        set /a LINK_COUNT+=1
    ) else (
        echo   [失败] !LINK_NAME! 链接创建失败
        set /a FAIL_COUNT+=1
    )
)

echo.
echo   共处理 !LINK_COUNT! 个链接
if !FAIL_COUNT! gtr 0 (
    echo   失败 !FAIL_COUNT! 个
)
echo.

REM ========================================
REM 第4步：验证
REM ========================================
echo [4/4] 验证符号链接...
echo.

set "ALL_OK=1"
for /d %%D in ("%KB_PATH%\*") do (
    set "FOLDER_NAME=%%~nxD"
    set "LINK_NAME=!FOLDER_NAME!"
    call :to_lower LINK_NAME

    set "LINK_FULL=!CODEBUDDY_PATH!\!LINK_NAME!"

    if exist "!LINK_FULL!" (
        echo   [OK] !LINK_NAME!
    ) else (
        echo   [失败] !LINK_NAME!
        set "ALL_OK=0"
    )
)

echo.
if "!ALL_OK!"=="1" (
    echo ========================================
    echo  全部设置成功!
    echo ========================================
    echo.
    echo 现在可以用 CodeBuddy 打开 3DPipeLine 项目，
    echo 它将自动识别 KB 中的资源。
) else (
    echo ========================================
    echo  部分链接设置失败，请检查上方错误信息
    echo ========================================
)

echo.
pause
exit /b 0

REM ========================================
REM 函数：将变量值转为小写
REM 参数1：变量名（不带感叹号）
REM ========================================
::to_lower
set "_val=!%~1!"
set "_val=!_val:A=a!"
set "_val=!_val:B=b!"
set "_val=!_val:C=c!"
set "_val=!_val:D=d!"
set "_val=!_val:E=e!"
set "_val=!_val:F=f!"
set "_val=!_val:G=g!"
set "_val=!_val:H=h!"
set "_val=!_val:I=i!"
set "_val=!_val:J=j!"
set "_val=!_val:K=k!"
set "_val=!_val:L=l!"
set "_val=!_val:M=m!"
set "_val=!_val:N=n!"
set "_val=!_val:O=o!"
set "_val=!_val:P=p!"
set "_val=!_val:Q=q!"
set "_val=!_val:R=r!"
set "_val=!_val:S=s!"
set "_val=!_val:T=t!"
set "_val=!_val:U=u!"
set "_val=!_val:V=v!"
set "_val=!_val:W=w!"
set "_val=!_val:X=x!"
set "_val=!_val:Y=y!"
set "_val=!_val:Z=z!"
set "%~1=!_val!"
goto :eof
