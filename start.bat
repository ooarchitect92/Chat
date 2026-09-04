@echo off
setlocal
title Northstar AI - Local Stack
cd /d "%~dp0"

echo ==========================================================
echo   NORTHSTAR AI - ONE CLICK START
echo ==========================================================
echo.

REM ---------- 1. Docker must be running ----------
docker info >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Docker Desktop is not running.
  echo         Start Docker Desktop, wait for the whale icon to go steady, then run this again.
  goto :fail
)
echo [1/6] Docker is running.

REM ---------- 2. Ensure .env exists ----------
if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  echo [2/6] Created .env from .env.example.
) else (
  echo [2/6] Using existing .env.
)

REM ---------- 3. Clear this project's ports ----------
echo [3/6] Clearing ports (stopping any previous Northstar containers)...
docker compose down --remove-orphans >nul 2>&1

set "PORTS=3100 8100 55432 6399 5673 15673 29093 9010 9011"
set "BLOCKED="
for %%P in (%PORTS%) do call :checkport %%P
if defined BLOCKED (
  echo.
  echo [ERROR] These host ports are still in use:%BLOCKED%
  echo         Stop whatever owns them ^(see the owner listed above^), or change the
  echo         matching *_PORT value in .env, then run this script again.
  goto :fail
)
echo       All required ports are free.

REM ---------- 4. Build and start ----------
echo [4/6] Building images and starting all services ^(first run can take several minutes^)...
docker compose up --build -d
if errorlevel 1 (
  echo.
  echo [ERROR] docker compose failed to start. Full logs:
  echo         docker compose logs --tail=200
  goto :fail
)

REM ---------- 5. Wait for the API to become ready ----------
echo [5/6] Waiting for the API to report ready...
set /a TRIES=0
:waitapi
set /a TRIES+=1
curl.exe -fsS -o nul http://127.0.0.1:8100/health/ready >nul 2>&1
if not errorlevel 1 goto :apiready
if %TRIES% GEQ 100 (
  echo.
  echo [ERROR] The API did not become ready in time.
  echo         Inspect it with: docker compose logs --tail=200 api
  goto :fail
)
ping -n 4 127.0.0.1 >nul
goto :waitapi
:apiready
echo       API is ready.

REM ---------- 6. Wait for the web app ----------
echo [6/6] Waiting for the web app...
set /a TRIES=0
:waitweb
set /a TRIES+=1
curl.exe -fsS -o nul http://127.0.0.1:3100/healthz >nul 2>&1
if not errorlevel 1 goto :webready
if %TRIES% GEQ 40 (
  echo       [WARN] Web app is slow to answer; the API below is already usable.
  goto :webready
)
ping -n 4 127.0.0.1 >nul
goto :waitweb
:webready

REM ---------- Read the seeded credentials straight out of .env ----------
set "ADMIN_EMAIL="
set "ADMIN_PASSWORD="
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
  if /i "%%A"=="SEED_ADMIN_EMAIL" set "ADMIN_EMAIL=%%B"
  if /i "%%A"=="SEED_ADMIN_PASSWORD" set "ADMIN_PASSWORD=%%B"
)

echo.
echo ==========================================================
echo   NORTHSTAR AI IS UP
echo ==========================================================
echo.
echo   Web app          http://localhost:3100
echo   Swagger UI       http://localhost:8100/docs
echo   ReDoc            http://localhost:8100/redoc
echo   OpenAPI JSON     http://localhost:8100/openapi.json
echo   API base path    http://localhost:8100/api/v1
echo.
echo   --- Test login ---
echo   Email            %ADMIN_EMAIL%
echo   Password         %ADMIN_PASSWORD%
echo.
echo   To authorize Swagger:
echo     1. POST /api/v1/auth/login with the email and password above
echo     2. Copy "accessToken" out of the response
echo     3. Click "Authorize" at the top right and paste the token
echo.
echo   --- Infrastructure consoles ---
echo   RabbitMQ         http://localhost:15673   northstar / replace-local-rabbitmq-password
echo   MinIO console    http://localhost:9011    northstar / replace-local-minio-password
echo   Postgres         localhost:55432          northstar / replace-local-postgres-password
echo   Redis            localhost:6399           password: replace-local-redis-password
echo   Kafka bootstrap  localhost:29093
echo.
echo   Live logs        docker compose logs -f
echo   Shut down        docker compose down
echo ==========================================================
echo.

start "" http://localhost:8100/docs
start "" http://localhost:3100

echo Opened Swagger and the web app in your browser.
pause
endlocal
exit /b 0

REM ---------- helper: report a port that is still occupied ----------
:checkport
powershell -NoProfile -Command "exit (@(Get-NetTCPConnection -State Listen -LocalPort %1 -ErrorAction SilentlyContinue).Count -gt 0)"
if errorlevel 1 (
  set "BLOCKED=%BLOCKED% %1"
  echo       [BUSY] port %1 is taken by:
  docker ps --format "                 docker container {{.Names}} ({{.Ports}})" | findstr /C:":%1->"
)
exit /b 0

:fail
echo.
pause
endlocal
exit /b 1
