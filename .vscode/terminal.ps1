# Integrated terminal bootstrap: activate .venv and expose `dev`.
$repoRoot = Split-Path -Parent $PSScriptRoot
$activate = Join-Path $repoRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $activate) {
    . $activate
} else {
    Write-Warning ".venv not found. Create it with: python -m venv .venv"
}

function Test-Listen([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $wait = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $wait.AsyncWaitHandle.WaitOne(200, $false)
        if (-not $ok) { return $false }
        $client.EndConnect($wait) | Out-Null
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Stop-Port([int]$Port) {
    $pids = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if (-not $pids) {
        Write-Host "Nothing listening on $Port"
        return
    }
    foreach ($procId in $pids) {
        $name = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
        Write-Host "Killing PID $procId ($name) on port $Port"
        Stop-Process -Id $procId -Force
    }
}

function global:dev {
    $py = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-Error ".venv not found. Run: python -m venv .venv"
        return
    }
    $cmd = if ($args.Count) { [string]$args[0] } else { "ui" }
    $rest = @()
    if ($args.Count -gt 1) { $rest = $args[1..($args.Count - 1)] }

    switch -Regex ($cmd) {
        '^(ui|web)$' {
            if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
                Write-Error "Node.js is required for the desk. Install it, then run dev again."
                return
            }
            if (-not (Test-Listen 8000)) {
                Write-Host "API → http://127.0.0.1:8000/docs"
                Start-Process -FilePath $py -ArgumentList @("-m", "uvicorn", "partnerdesk.app:app", "--host", "127.0.0.1", "--port", "8000") -WorkingDirectory $repoRoot
            }
            $web = Join-Path $repoRoot "web"
            if (-not (Test-Path (Join-Path $web "node_modules"))) {
                Push-Location $web
                npm install
                Pop-Location
            }
            Write-Host "Desk → http://localhost:5173"
            Push-Location $web
            try { npm run dev } finally { Pop-Location }
        }
        '^api$' {
            Write-Host "API → http://127.0.0.1:8000/docs"
            & $py -m uvicorn partnerdesk.app:app --host 127.0.0.1 --port 8000
        }
        '^(kill|stop)$' {
            Stop-Port 5173
            Stop-Port 8000
        }
        '^db$' { & $py (Join-Path $repoRoot "scripts\create_database.py") }
        '^catalog$' { & $py (Join-Path $repoRoot "scripts\build_catalog.py") @rest }
        '^test$' { & $py -m pytest -q @rest }
        '^lint$' { & $py -m ruff check partnerdesk tests evals scripts }
        '^eval$' { & $py (Join-Path $repoRoot "evals\run_po_extract.py") @rest }
        '^py$' { & $py @rest }
        default { Write-Error "Unknown command: $cmd   (use: ui api kill db catalog test lint eval py)" }
    }
}
