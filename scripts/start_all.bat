@echo off
REM 双击即可一键启动：后端 + 客户前端 + 管理后台
REM 首次运行若要自动安装依赖，请用： start_all.bat -Install
setlocal
cd /d "%~dp0"
if "%~1"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -File ".\start_all.ps1"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File ".\start_all.ps1" %*
)
endlocal
