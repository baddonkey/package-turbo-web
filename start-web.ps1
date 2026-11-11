param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$repositoryRoot = $PSScriptRoot
$loginConfig = Join-Path $HOME ".config\package-turbo-web\login.env"

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory)]
        [string]$Command,
        [Parameter()]
        [string[]]$Arguments = @()
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command exited with code $LASTEXITCODE."
    }
}

if (
    (Test-Path -LiteralPath $loginConfig -PathType Leaf) -and
    [string]::IsNullOrEmpty($env:PACKAGE_TURBO_LOGIN_USERNAME) -and
    [string]::IsNullOrEmpty($env:PACKAGE_TURBO_LOGIN_PASSWORD_HASH)
) {
    foreach ($line in Get-Content -LiteralPath $loginConfig) {
        if ($line -match '^\s*(?:export\s+)?(PACKAGE_TURBO_LOGIN_USERNAME|PACKAGE_TURBO_LOGIN_PASSWORD_HASH)=(.*)$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim()
            if ($value.Length -ge 2 -and
                (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
        elseif ($line -match '^\s*(?:export\s+)?PACKAGE_TURBO_LOGIN_(?:USERNAME|PASSWORD_HASH)\b') {
            throw "Invalid login configuration line: $line"
        }
    }
}

Push-Location $repositoryRoot
try {
    Invoke-CheckedCommand "uv" @("sync", "--locked", "--extra", "web")
    Invoke-CheckedCommand "npm" @("ci", "--prefix", "web")
    Invoke-CheckedCommand "npm" @("run", "build", "--prefix", "web")

    $env:PACKAGE_TURBO_STATIC_DIR = Join-Path $repositoryRoot "web\dist\package-turbo-web\browser"
    Invoke-CheckedCommand "uv" @(
        "run", "--locked", "--extra", "web", "python", "-m", "uvicorn",
        "ph_web.app:app", "--host", "127.0.0.1", "--port", "$Port"
    )
}
finally {
    Pop-Location
}
