@echo off
rem Самоперезапуск: если приложение выходит (в т.ч. по /admin/reload),
rem батник поднимает его заново. Так код обновляется без прав администратора.
:loop
"C:\Users\claude\dogovor\venv\Scripts\python.exe" "C:\Users\claude\dogovor\app\run_server.py" >> "C:\Users\claude\dogovor\server.log" 2>&1
timeout /t 2 /nobreak > nul
goto loop
