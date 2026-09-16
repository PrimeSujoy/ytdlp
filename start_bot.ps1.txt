$venvPython = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "python" }
Start-Process -WindowStyle Hidden -FilePath $python -ArgumentList "main.py" -WorkingDirectory $PSScriptRoot
Write-Host "Bot started in background!" -ForegroundColor Green
