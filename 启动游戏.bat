@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title AI 叙事 RPG
cd /d "%~dp0"

REM ===== 1. 找出真正的 Python =====
REM 不能用 where python 直接判断：Win10/11 自带 Microsoft Store 的 python.exe
REM 别名（%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe），没装 Python 时
REM 它也存在，where 会误报成功，随后执行它会弹出应用商店。
set "PYEXE="
for /f "delims=" %%p in ('where python 2^>nul') do (
    if not defined PYEXE (
        set "CAND=%%p"
        if "!CAND:WindowsApps=!"=="!CAND!" set "PYEXE=%%p"
    )
)

REM 退回 Windows 自带的 py 启动器
if not defined PYEXE (
    where py >nul 2>nul
    if not errorlevel 1 set "PYEXE=py"
)

if not defined PYEXE goto :no_python

REM 认输出、不认退出码：冒牌货（应用商店别名、py 空壳）对陌生参数常常返回 0，
REM 只有真的 Python 才会打印 "Python 3.x"
set "PYVER="
for /f "delims=" %%v in ('"%PYEXE%" --version 2^>^&1') do set "PYVER=%%v"
set "PYCHK=!PYVER:Python 3=!"
if "!PYCHK!"=="!PYVER!" goto :no_python

REM 版本必须 >= 3.10
"%PYEXE%" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto :bad_python

echo [Python] !PYVER!  (!PYEXE!)

REM ===== 2. 检查依赖是否已安装 =====
"%PYEXE%" -c "import ttkbootstrap, requests" >nul 2>nul
if not errorlevel 1 goto :launch

echo.
echo 首次运行：正在安装依赖，请稍候...

REM 先试内置离线包（适配 Python 3.10-3.13 64位，纯离线、无需联网）
if exist vendor_packages (
    echo [1/2] 尝试内置离线依赖包（vendor_packages）...
    "%PYEXE%" -m pip install --no-index --find-links vendor_packages -r requirements.txt >nul 2>nul
    if not errorlevel 1 goto :deps_ok
    echo       系统目录装不上，改装到当前用户目录...
    "%PYEXE%" -m pip install --user --no-index --find-links vendor_packages -r requirements.txt >nul 2>nul
    if not errorlevel 1 goto :deps_ok
    echo       离线包安装未成功（离线包适配 Python 3.10-3.13 64位），
    echo       会自动转联网安装。
    echo.
)

REM 联网安装（适配任意 Python 版本，需要网络）
echo [2/2] 尝试联网安装（需要网络连接）...
"%PYEXE%" -m pip install -r requirements.txt
if not errorlevel 1 goto :deps_ok

REM 联网装系统目录失败（常因权限）→ 改装到当前用户目录
echo       系统目录安装受限，改装到当前用户目录...
"%PYEXE%" -m pip install --user -r requirements.txt
if not errorlevel 1 goto :deps_ok

goto :install_failed

:deps_ok
echo.
echo 依赖安装完成。
goto :launch

:launch
echo.
echo 正在启动游戏...
"%PYEXE%" main.py
if errorlevel 1 (
    echo.
    echo [错误] 游戏异常退出，错误码 !errorlevel!
    echo 请把上方报错信息发给开发者。
)
pause
exit /b 0

:bad_python
echo.
echo ============================================================
echo [错误] Python 版本过低：!PYVER!
echo.
echo   本程序需要 Python 3.10 或更高版本，你当前的版本太旧。
echo   请到官网下载新版安装（可与旧版共存）：
echo     https://www.python.org/downloads/
echo   安装时务必勾选 "Add Python to PATH"。
echo ============================================================
echo.
pause
exit /b 1

:no_python
echo.
echo ============================================================
echo [错误] 未检测到可用的 Python 3。
echo.
echo   注意：Windows 应用商店里的 "python.exe" 不算，装了它也不管用。
echo.
echo   请到官网下载安装 Python 3.10 或更高版本：
echo     https://www.python.org/downloads/
echo   安装时务必勾选 "Add Python to PATH"。
echo ============================================================
echo.
pause
exit /b 1

:install_failed
echo.
echo ============================================================
echo [错误] 依赖安装失败，错误码 !errorlevel!
echo.
echo 可能原因与解决办法：
echo   1. Python 版本与离线包不匹配
echo      - 离线包支持 Python 3.10-3.13 的 64 位版本，若你装的是
echo        32 位或更老的 Python，需联网安装（上面已自动尝试过）。
echo      - 可手动联网安装：python -m pip install -r requirements.txt
echo      - 或换国内源：python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo   2. 网络不通或 pip 源不可用
echo      - 请检查网络，或使用上方国内源命令重试。
echo   3. 系统目录无写入权限
echo      - 已在上面自动尝试 --user 安装；若仍失败请以管理员身份运行本文件。
echo ============================================================
pause
exit /b 1
