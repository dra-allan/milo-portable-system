$content = Get-Content 'C:\Users\user\Desktop\milo-portable-system\artisan\pov_pipeline\projects\g0DlLoKxSDg_20260806\01_SCRIPT_RAW.txt' -Raw
$source = Get-Content 'C:\Users\user\Desktop\milo-portable-system\artisan\pov_pipeline\projects\g0DlLoKxSDg_20260806\00_SOURCE_SCRIPT.txt' -Raw

# Word count
$bodyStart = $content.IndexOf('=== END MANIFEST ===')
if ($bodyStart -ge 0) { $body = $content.Substring($bodyStart) } else { $body = $content }
$bodyWords = ($body -split '\s+' | Where-Object { $_ -match '\w' }).Count
$allWords = ($content -split '\s+' | Where-Object { $_ -match '\w' }).Count
Write-Host "Body word count: $bodyWords"
Write-Host "Total word count: $allWords"

# 6-gram check
$bodyWordsList = $body -split '\s+' | Where-Object { $_ -match '\w' }
$sourceWordsList = $source -split '\s+' | Where-Object { $_ -match '\w' }
$sourceText = ($sourceWordsList -join ' ').ToLower()

$body6grams = @{}
for ($i = 0; $i -le $bodyWordsList.Count - 6; $i++) {
    $gram = ($bodyWordsList[$i..($i+5)] -join ' ').ToLower()
    $body6grams[$gram] = $true
}

$matches = @()
foreach ($gram in $body6grams.Keys) {
    if ($sourceText.Contains($gram)) {
        $matches += $gram
    }
}
Write-Host "6+ word matches found: $($matches.Count)"
foreach ($m in $matches | Select-Object -First 10) {
    Write-Host "  MATCH: $m"
}

# Banned words
$banned = @('furthermore','moreover','additionally','consequently','subsequently','nevertheless','nonetheless','hence','thus','therefore','however','ultimately','crucial','crucially','essentially','fundamentally','significantly','notably','importantly','particularly','tapestry','landscape','realm','journey','navigate','delve','dive into','unpack','unlock','harness','foster','cultivate','embark','in a world where','at its core','what this means is','it is important to note','it is worth mentioning','in essence','in conclusion','picture this','imagine','let us explore','it is not just','more than just','not only','asset','unit','roi','inventory','liquidation','resource','subscription','performance review','synergy','stakeholder','optimize','leverage','pipeline')
$bodyText = $body.ToLower()
$foundBanned = @()
foreach ($w in $banned) {
    if ($bodyText.Contains($w)) { $foundBanned += $w }
}
Write-Host "Banned AI words found: $($foundBanned.Count)"
foreach ($w in $foundBanned) { Write-Host "  BANNED: $w" }

# Punctuation
$emdash = [char]0x2014
$ellipsis = [char]0x2026
Write-Host "Em-dashes: $($content.Split($emdash).Count - 1)"
Write-Host "Ellipses: $($content.Split($ellipsis).Count - 1)"
Write-Host "Semicolons: $($content.Split(';').Count - 1)"

# Segments
$segments = [regex]::Matches($content, '\[NAR-\d+\]').Count
Write-Host "Segments: $segments"