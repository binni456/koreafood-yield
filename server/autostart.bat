@echo off
cd /d "%~dp0"
set "TARGET=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\yield-server.bat"
> "%TARGET%" echo @echo off
>> "%TARGET%" echo start "수율 일지 서버" /min /d "%~dp0." "%~dp0start.bat"
echo.
echo  다음부터 이 PC를 켜면 수율 일지 서버가 자동으로 켜집니다.
echo  끄려면 이 파일을 지우세요: %TARGET%
echo.
pause
