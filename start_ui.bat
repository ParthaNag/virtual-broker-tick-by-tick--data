@echo off
if not exist node_modules (
  echo Installing UI dependencies...
  npm install
  if errorlevel 1 exit /b %errorlevel%
)
npm run dev
