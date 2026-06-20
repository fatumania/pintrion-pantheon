@echo off
title Phone Checker v4.0 - ОКНО НЕ ЗАКРЫВАЙТЕ!
cd /d C:\Users\three\pintrion-pantheon\whatsapp-checker
echo.
echo ========================================
echo   Phone Checker v4.0
echo   Откройте: http://localhost:5559
echo   ОКНО НЕ ЗАКРЫВАЙТЕ!
echo ========================================
echo.
node server.js
echo.
echo Сервис остановлен. Нажмите любую клавишу для перезапуска...
pause >nul
goto :eof
