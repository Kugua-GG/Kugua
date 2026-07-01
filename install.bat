@echo off
chcp 65001 >nul
echo ========================================
echo   苦瓜code v0.2.0 — 一键安装
echo ========================================
echo.

cd /d "%~dp0"

echo [1/3] pip install kugua_code-0.1.0-py3-none-any.whl
pip install kugua_code-0.1.0-py3-none-any.whl --force-reinstall --quiet

if %errorlevel% neq 0 (
    echo 安装失败，请检查 Python 和 pip 是否已安装
    pause
    exit /b 1
)

echo.
echo [2/3] 验证安装
python -c "from kugua import KuguaConfig, ContextManager, GraphKB, TaskExecutor, FreshObserver; print('  导入成功')" 2>nul

if %errorlevel% neq 0 (
    echo 验证失败
    pause
    exit /b 1
)

echo.
echo [3/3] 配置 LLM 模型
echo.
echo   苦瓜code 需要配置 LLM 模型才能运行。
echo   至少需要 1 个，强烈建议提供 2 个（主力和观察者各一）。
echo.
echo   ┌─────────────────────────────────────────────┐
echo   │ 主力模型 (Worker/Checker) — 处理复杂任务     │
echo   │ 观察者 (FreshObserver)   — 检测幻觉，需轻量  │
echo   │                              (建议不同模型)  │
echo   └─────────────────────────────────────────────┘
echo.
set /p RUN_SETUP="  是否现在运行配置向导？(Y/n): "
if /i "%RUN_SETUP%" neq "n" (
    python -m kugua.setup_wizard
)

echo.
echo ========================================
echo   安装完成！
echo.
echo   使用方法:
echo     kugua-worker --subtask-id t1 --task "分析"
echo     kugua-checker --subtask-id t1 --worker-output "结果"
echo     kugua-setup             (重新配置)
echo     kugua-setup --check     (检查配置)
echo.
echo   快速测试:
echo     python -m kugua.self_test
echo ========================================

pause
