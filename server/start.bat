@echo off
cd /d "%~dp0"
title 코리아식품 수율 일지 서버
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 server.py
  goto done
)
where python >nul 2>nul
if %errorlevel%==0 (
  python server.py
  goto done
)
echo.
echo  파이썬이 설치되어 있지 않습니다.
echo  INSTALL-GUIDE.html 을 열어 1단계(파이썬 설치)를 따라 해 주세요.
echo.
:done
pause
