# Integrated terminal bootstrap: put `dev` on the PATH. Everything runs in Docker.
$repoRoot = Split-Path -Parent $PSScriptRoot
$env:Path = "$repoRoot;" + $env:Path

function global:dev {
    $bat = Join-Path $repoRoot "dev.bat"
    if (Test-Path $bat) {
        & $bat @args
        return
    }
    Write-Error "dev.bat not found in $repoRoot"
}
