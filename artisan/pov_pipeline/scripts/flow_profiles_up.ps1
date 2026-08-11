# ---------------------------------------------------------------------------
# flow_profiles_up.ps1 - open the Chrome Browser Bridge profiles (Windows dev)
#
#   powershell -ExecutionPolicy Bypass -File scripts\flow_profiles_up.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\flow_profiles_up.ps1 -Profiles flow-1,flow-2
#
# Google Flow only generates images while its Chrome profiles are OPEN. A
# closed profile fails deep inside the images stage with BROWSER_CONNECT.
# This script opens them; it does NOT log in. Google login and reCAPTCHA are
# a one-time human step, on purpose, and are never automated.
#
# Verify afterwards:
#   python run_pov_pipeline.py --check-profiles --flow-profiles flow-1,flow-2
# ---------------------------------------------------------------------------

param(
    [string[]]$Profiles = @('flow-1','flow-2','flow-3','flow-4','flow-5','flow-6'),
    [int]$DelaySeconds = 3
)

$opencli = (Get-Command opencli -ErrorAction SilentlyContinue).Source
if (-not $opencli) {
    Write-Error "opencli is not on PATH. Install it: npm i -g opencli"
    exit 1
}

Write-Host "Opening $($Profiles.Count) Flow profile(s) via $opencli"
foreach ($p in $Profiles) {
    Write-Host "  -> $p"
    & $opencli profile open $p
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "  $p did not open cleanly (exit $LASTEXITCODE)"
    }
    Start-Sleep -Seconds $DelaySeconds
}

Write-Host ""
Write-Host "Connected profiles:"
& $opencli profile list
Write-Host ""
Write-Host "If a profile shows as logged out, sign in to it by hand once."
