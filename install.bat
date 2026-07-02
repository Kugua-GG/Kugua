@echo off
chcp 65001 >nul
echo ========================================
echo   kugua core v0.2.1 — 一键安装
echo ========================================
echo.
cd /d "%~dp0"

:: ── [1/5] 语言选择 ──────────────────────────
echo [1/5] 语言选择 / Language Selection
echo.
echo    [1] 简体中文 (zh-CN)
echo    [2] 繁體中文 (zh-TW)
echo    [3] English (en)
echo    [4] 日本語 (ja)
echo.
set /p LANG_CHOICE="  请选择 / Select (1-4) [默认/default 1]: "
if "%LANG_CHOICE%"=="" set LANG_CHOICE=1

if "%LANG_CHOICE%"=="1" set KUGUA_LANG=zh-CN
if "%LANG_CHOICE%"=="2" set KUGUA_LANG=zh-TW
if "%LANG_CHOICE%"=="3" set KUGUA_LANG=en
if "%LANG_CHOICE%"=="4" set KUGUA_LANG=ja

setx KUGUA_LANG %KUGUA_LANG% >nul 2>&1
echo   语言已设置为: %KUGUA_LANG%
echo.

:: ── [2/5] pip install ──────────────────────
echo [2/5] pip install -e .
pip install -e . --quiet

if %errorlevel% neq 0 (
    echo 安装失败，请检查 Python 和 pip 是否已安装
    pause
    exit /b 1
)

:: ── [3/5] 验证安装 ─────────────────────────
echo.
echo [3/5] 验证安装
python -c "from kugua import KuguaConfig, get_dashboard_summary; from kugua.i18n import t, SUPPORTED_LANGS; print('  导入成功 · 已加载', len(SUPPORTED_LANGS), '种语言')" 2>nul

if %errorlevel% neq 0 (
    echo 验证失败
    pause
    exit /b 1
)

:: ── [4/5] 配置 MCP ─────────────────────────
echo.
echo [4/5] MCP 配置
echo   .mcp.json 中应包含 kugua 条目:
echo   {
echo     "kugua": {
echo       "command": "python",
echo       "args": ["%cd%\mcp\server.py"],
echo       "env": {
echo         "KUGUA_LANG": "%KUGUA_LANG%",
echo         "KUGUA_ARTIFACTS_DIR": "C:/Users/Administrator/.claude/.codex/artifacts",
echo         "KUGUA_CODE_DIR": "%cd%\kugua"
echo       }
echo     }
echo   }

:: ── [5/5] 配置 LLM ─────────────────────────
echo [5/5] 配置 LLM 模型
echo.
echo   kugua core 需要配置 LLM 模型才能运行。
echo   至少需要 1 个，强烈建议提供 2 个（主力和观察者各一）。
echo.
echo   +---------------------------------------------+
echo   ^| 主力模型 (Worker/Checker) — 处理复杂任务     ^|
echo   ^| 观察者 (FreshObserver)   — 检测幻觉，需轻量  ^|
echo   ^|                              (建议不同模型)  ^|
echo   +---------------------------------------------+
echo.
set /p RUN_SETUP="  是否现在运行配置向导？(Y/n): "
if /i "%RUN_SETUP%" neq "n" (
    python -m kugua.setup_wizard
)

echo.
echo ========================================
echo   安装完成！
echo.
echo   支持的语言: 简体中文 ^| 繁體中文 ^| English ^| 日本語
echo   切换语言: set KUGUA_LANG=en  (当前: %KUGUA_LANG%)
echo.
echo   7 个 MCP 工具:
echo     status_all · kb_query · kb_snapshot
echo     double_loop_check · observer_gate
echo     negentropy_dash · csd_status
echo.
echo   快速测试:
echo     python -c "from kugua import get_dashboard_summary; print(get_dashboard_summary())"
echo ========================================

pause
