@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'bot.py|rent_watchdog.py|funpay_command_watchdog.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
start "rent-watchdog" /min python rent_watchdog.py
start "funpay-command-watchdog" /min python funpay_command_watchdog.py
python bot.py
pause
