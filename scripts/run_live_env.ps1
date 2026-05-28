param(
    [string]$Mode = "rule_regime",
    [string]$EnvFile = ""
)

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $EnvFile = Join-Path $ProjectRoot ".env"
}

if (-not (Test-Path $EnvFile)) {
    throw "Missing env file: $EnvFile"
}

Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
        return
    }

    $parts = $line -split "=", 2
    if ($parts.Count -ne 2) {
        return
    }

    $key = $parts[0].Trim()
    $value = $parts[1].Trim().Trim('"').Trim("'")
    if (-not [string]::IsNullOrWhiteSpace($key)) {
        Set-Item -Path "Env:$key" -Value $value
    }
}

python main.py --mode $Mode
