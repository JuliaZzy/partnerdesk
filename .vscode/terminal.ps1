# Integrated terminal bootstrap: activate .venv and expose `dev`.
$repoRoot = Split-Path -Parent $PSScriptRoot
$activate = Join-Path $repoRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $activate) {
    . $activate
} else {
    Write-Warning ".venv not found. Create it with: python -m venv .venv"
}

function global:dev {
    # Call venv python in this shell so Cursor shows Streamlit's output.
    # Going through dev.bat from PowerShell swallows stdout and looks hung.
    $py = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-Error ".venv not found. Run: python -m venv .venv"
        return
    }
    $cmd = if ($args.Count) { [string]$args[0] } else { "ui" }
    $rest = @()
    if ($args.Count -gt 1) { $rest = $args[1..($args.Count - 1)] }

    switch -Regex ($cmd) {
        '^ui$' {
            Write-Host "Desk → http://localhost:8501"
            $env:PYTHONUNBUFFERED = "1"
            & $py -u -m streamlit run (Join-Path $repoRoot "partnerdesk\ui.py") --server.headless true --server.port 8501
        }
        '^api$' {
            Write-Host "API → http://127.0.0.1:8000/docs"
            & $py -m uvicorn partnerdesk.app:app --port 8000
        }
        '^(kill|stop)$' {
            $pids = @(Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty OwningProcess -Unique)
            if (-not $pids) {
                Write-Host "Nothing listening on 8501"
                return
            }
            foreach ($procId in $pids) {
                $name = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
                Write-Host "Killing PID $procId ($name) on port 8501"
                Stop-Process -Id $procId -Force
            }
        }
        '^test$' { & $py -m pytest -q @rest }
        '^lint$' { & $py -m ruff check partnerdesk tests evals }
        '^eval$' { & $py (Join-Path $repoRoot "evals\run_po_extract.py") @rest }
        '^py$' { & $py @rest }
        default { Write-Error "Unknown command: $cmd   (use: ui api kill test lint eval py)" }
    }
}
