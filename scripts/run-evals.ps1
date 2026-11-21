<#
Run evaluation suites live through the gateway and import the results as evidence.
  AURAI_GATEWAY_URL   gateway base URL (without /v1)
  AURAI_EVAL_KEY      virtual key for a gateway team at tier T1 or above (so contact details are masked)
  AURAI_PROMPTFOO_CMD override the Promptfoo command (default: npx --yes promptfoo@0.126.0)
Usage: .\scripts\run-evals.ps1 [suite-id ...]     (default: every suite except retrieval suites)
#>
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Suites)
$ErrorActionPreference = "Stop"
if (-not $env:AURAI_GATEWAY_URL) { throw "set AURAI_GATEWAY_URL" }
if (-not $env:AURAI_EVAL_KEY) { throw "set AURAI_EVAL_KEY" }
$pf = if ($env:AURAI_PROMPTFOO_CMD) { $env:AURAI_PROMPTFOO_CMD } else { "npx --yes promptfoo@0.126.0" }
$env:PROMPTFOO_DISABLE_TELEMETRY = "1"
$env:PROMPTFOO_DISABLE_UPDATE = "1"

if (-not $Suites -or $Suites.Count -eq 0) {
  $Suites = aurai-governance evals list | ForEach-Object { $f = $_ -split '\s+'; if ($f[1] -ne "retrieval") { $f[0] } }
}

$failed = $false
foreach ($id in $Suites) {
  $work = Join-Path ([IO.Path]::GetTempPath()) ("aurai-eval-" + [guid]::NewGuid())
  New-Item -ItemType Directory -Path $work | Out-Null
  Write-Host "== $id"
  aurai-governance evals export $id --out $work | Out-Null
  # Promptfoo exits non-zero when cases fail; the import records those failures, so keep going.
  Invoke-Expression "$pf eval -c `"$work\promptfooconfig.yaml`" -o `"$work\results.json`" --no-cache --no-write"
  aurai-governance evals import "$work\results.json" --suite $id
  if ($LASTEXITCODE -ne 0) { $failed = $true }
  Remove-Item -Recurse -Force $work
}
if ($failed) { exit 1 }
