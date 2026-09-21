<#
.SYNOPSIS
  Runs the TrendCast load-test scenarios one after another and writes a report for each.

.DESCRIPTION
  Prerequisites (see README.md):
    * LOAD_TEST_DB_URL points at a dedicated database that has been seeded (seed_load_data.py seed)
    * the stubbed backend is running:  uvicorn stubbed_app:app --app-dir backend/tests/load --port 8100

  For every scenario it starts monitor.py (CPU, memory, DB connections), runs Locust headless, then
  analyze_results.py to produce report.md with pass/fail verdicts. Output goes to
  results/<timestamp>/<scenario>/ .

.PARAMETER Scenarios
  baseline, normal, ramp, peaks, spike, soak, auth, predict.  Default: everything except soak (30 min).

.PARAMETER Quick
  Shrinks every duration so the whole suite finishes in a few minutes. For checking the kit works;
  the numbers are not meaningful.

.EXAMPLE
  ./run_load_tests.ps1
  ./run_load_tests.ps1 -Scenarios spike,soak
  ./run_load_tests.ps1 -Quick
  $env:LOAD_NORMAL_MINUTES=2; ./run_load_tests.ps1 -Scenarios normal   # any LOAD_* variable you set overrides the plan
#>
param(
    [string[]]$Scenarios = @("baseline", "normal", "ramp", "peaks", "spike", "auth", "predict"),
    [string]$HostUrl = "http://127.0.0.1:8100",
    [int]$Port = 8100,
    [switch]$Quick
)

$ErrorActionPreference = "Continue"   # Locust writes its progress to stderr; explicit `throw`s below still stop the script
Set-Location $PSScriptRoot

if (-not $env:LOAD_TEST_DB_URL) { throw "LOAD_TEST_DB_URL is not set." }
if (-not (Test-Path "seed_manifest.json")) { throw "No seed_manifest.json: run 'python seed_load_data.py seed' first." }

# Refuse to run unless the target is the stubbed app (it serves /__loadtest__).
python -c "import load_config as lc; lc.assert_stubbed_target('$HostUrl')"
if ($LASTEXITCODE -ne 0) { throw "$HostUrl is not the load-test app. Aborting." }

# Per-scenario settings. Environment variables read by shapes.py, locustfile.py and load_config.py.
$plans = @{
    baseline = @{ LOAD_SHAPE = "baseline"; LOAD_BASELINE_SECONDS = "120"; QUICK_LOAD_BASELINE_SECONDS = "15" }
    normal   = @{ LOAD_SHAPE = "normal"; LOAD_NORMAL_USERS = "20"; LOAD_NORMAL_MINUTES = "10"
                  QUICK_LOAD_NORMAL_MINUTES = "0.4" }
    ramp     = @{ LOAD_SHAPE = "ramp"; LOAD_RAMP_STEP = "10"; LOAD_RAMP_STEP_SECONDS = "120"; LOAD_RAMP_MAX = "150"
                  QUICK_LOAD_RAMP_STEP_SECONDS = "8"; QUICK_LOAD_RAMP_MAX = "40"
                  INFORMATIONAL = "1" }   # exploratory: the report's capacity table is the result, not pass/fail
    peaks    = @{ LOAD_SHAPE = "peaks"; LOAD_PEAK_CYCLES = "3"; LOAD_PEAK_LOW = "10"; LOAD_PEAK_HIGH = "80"
                  LOAD_MAX_ERROR_PCT = "2"; LOAD_READ_P95_MS = "1000"
                  QUICK_LOAD_PEAK_CYCLES = "1"; QUICK_LOAD_PEAK_PHASE_SCALE = "0.15"; QUICK_LOAD_PEAK_HIGH = "30" }
    spike    = @{ LOAD_SHAPE = "spike"; LOAD_SPIKE_LOW = "10"; LOAD_SPIKE_HIGH = "100"
                  LOAD_MAX_ERROR_PCT = "5"; LOAD_READ_P95_MS = "2000"; LOAD_READ_P99_MS = "5000"
                  QUICK_LOAD_SPIKE_WARMUP = "8"; QUICK_LOAD_SPIKE_HOLD = "15"; QUICK_LOAD_SPIKE_RECOVERY = "15"; QUICK_LOAD_SPIKE_HIGH = "30" }
    soak     = @{ LOAD_SHAPE = "soak"; LOAD_SOAK_USERS = "30"; LOAD_SOAK_MINUTES = "30"; QUICK_LOAD_SOAK_MINUTES = "0.5" }
    auth     = @{ LOAD_SHAPE = "normal"; LOAD_PROFILE = "auth"; LOAD_NORMAL_USERS = "20"; LOAD_NORMAL_MINUTES = "3"
                  LOAD_AUTH_P95_MS = "5000"; QUICK_LOAD_NORMAL_MINUTES = "0.3" }
    predict  = @{ LOAD_SHAPE = "normal"; LOAD_PROFILE = "predict"; LOAD_NORMAL_USERS = "20"; LOAD_NORMAL_MINUTES = "3"
                  QUICK_LOAD_NORMAL_MINUTES = "0.3" }
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$root = Join-Path "results" $stamp
$summary = @()

foreach ($name in $Scenarios) {
    if (-not $plans.ContainsKey($name)) { throw "Unknown scenario '$name'. Choose from: $($plans.Keys -join ', ')" }
    $plan = $plans[$name]
    $dir = Join-Path $root $name
    New-Item -ItemType Directory -Force $dir | Out-Null
    $stopFile = Join-Path $dir "monitor.stop"

    # apply the scenario's variables (QUICK_ ones override in -Quick mode), remembering the old values
    $saved = @{}
    $informational = $false
    foreach ($key in $plan.Keys) {
        if ($key -eq "INFORMATIONAL") { $informational = $true; continue }
        if ($key.StartsWith("QUICK_")) { continue }
        if ([Environment]::GetEnvironmentVariable($key)) { continue }   # a value you set yourself wins over the plan
        $value = $plan[$key]
        if ($Quick -and $plan.ContainsKey("QUICK_$key")) { $value = $plan["QUICK_$key"] }
        $saved[$key] = [Environment]::GetEnvironmentVariable($key)
        [Environment]::SetEnvironmentVariable($key, $value)
    }
    if ($Quick) {
        foreach ($key in $plan.Keys) {
            if ($key.StartsWith("QUICK_") -and -not $plan.ContainsKey($key.Substring(6))) {
                $real = $key.Substring(6)
                $saved[$real] = [Environment]::GetEnvironmentVariable($real)
                [Environment]::SetEnvironmentVariable($real, $plan[$key])
            }
        }
    }

    Write-Host ""
    Write-Host "=== $name ===" -ForegroundColor Cyan
    $monitor = Start-Process python -ArgumentList "monitor.py", "--port", $Port, "--out", "$dir/monitor.csv", "--stop-file", $stopFile `
        -PassThru -NoNewWindow -RedirectStandardOutput "$dir/monitor.log"
    Start-Sleep -Seconds 2   # let it take its first sample before the load starts

    try {
        python -m locust -f locustfile.py --host $HostUrl --headless --only-summary `
            --csv "$dir/$name" --csv-full-history --html "$dir/$name.html" --loglevel WARNING
        $locustExit = $LASTEXITCODE
    }
    finally {
        New-Item -ItemType File -Force $stopFile | Out-Null   # always stop the monitor, even on Ctrl+C
    }
    $monitor.WaitForExit(30000) | Out-Null
    if (-not $monitor.HasExited) { $monitor.Kill() }

    python analyze_results.py "$dir/$name" --monitor "$dir/monitor.csv" --out "$dir/report.md" --title "Load test: $name" | Out-Null

    foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key]) }

    $verdict = if ($locustExit -eq 0) { "PASS" } elseif ($informational) { "INFO" } else { "FAIL" }
    $summary += [pscustomobject]@{ Scenario = $name; Result = $verdict; Report = "$dir/report.md" }
    Start-Sleep -Seconds 5   # let the API and database settle between scenarios
}

Write-Host ""
Write-Host "=== Summary ===" -ForegroundColor Cyan
$summary | Format-Table -AutoSize
Write-Host "Reports and Locust HTML charts are in $root"
if ($summary | Where-Object { $_.Result -eq "FAIL" }) { exit 1 }
