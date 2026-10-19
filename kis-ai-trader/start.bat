@echo off
chcp 65001 > nul
cd /d "%~dp0"
where python > nul 2>&1
if errorlevel 1 (
    echo Python 이 설치되어 있지 않습니다. https://www.python.org/downloads/ 에서 설치하세요.
    echo 설치 화면에서 "Add python.exe to PATH" 를 꼭 체크하세요.
    pause
    exit /b 1
)
set KIS_FROM_BAT=1
python start.py %*
pause
