# Flow Image Generation Menu - Simple Interactive Interface
# Save this as flow-image-menu.ps1 on your desktop and run it in PowerShell

# Clear screen and show header
clear
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  FLOW IMAGE GENERATION MENU" -ForegroundColor Green
Write-Host "  Easy interface for Google Flow image generation" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if plugin is installed
$pluginInstalled = & opencli plugin list | Select-String "flow"
if (-not $pluginInstalled) {
    Write-Host "ERROR: Flow plugin not installed!" -ForegroundColor Red
    Write-Host "Please install it first:" -ForegroundColor Yellow
    Write-Host "  opencli plugin install file:///C:/Users/user/Desktop/opencli-plugin-flow" -ForegroundColor Green
    Write-Host ""
    Pause
    exit
}

# Main menu loop
while ($true) {
    clear
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  FLOW IMAGE GENERATION MENU" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "1.  Generate Single Image"
    Write-Host "2.  Generate Batch from File"
    Write-Host "3.  Setup Multi-Account Profiles"
    Write-Host "4.  Check Flow Credits"
    Write-Host "5.  View Last Results Folder"
    Write-Host "6.  Help & Examples"
    Write-Host "7.  POV Project Images (automatic, just point at a project)"
    Write-Host "0.  Exit"
    Write-Host ""
    $choice = Read-Host "Select an option (0-6)"

    switch ($choice) {
        "1" {
            # Single Image Generation
            clear
            Write-Host "=== SINGLE IMAGE GENERATION ===" -ForegroundColor Green
            Write-Host ""

            $prompt = Read-Host "Enter your image prompt (e.g., 'a beautiful sunset over mountains')"
            if (-not $prompt) { continue }

            Write-Host ""
            Write-Host "Model options:" -ForegroundColor Yellow
            Write-Host "  1) nano-banana-2-lite (fastest, cheapest)"
            Write-Host "  2) nano-banana-2 (balanced) - DEFAULT"
            Write-Host "  3) nano-banana-2-pro (highest quality)"
            Write-Host "  4) imagen-4 (latest)"
            $modelChoice = Read-Host "Select model (1-4, default 2)"
            switch ($modelChoice) {
                "1" { $model = "nano-banana-2-lite" }
                "3" { $model = "nano-banana-2-pro" }
                "4" { $model = "imagen-4" }
                default { $model = "nano-banana-2" }
            }

            Write-Host ""
            $countInput = Read-Host "Number of variations (1-4, default 1)"
            if ($countInput -and [int]::TryParse($countInput, [ref]$num) -and $num -ge 1 -and $num -le 4) {
                $count = $countInput
            } else {
                $count = "1"
            }

            Write-Host ""
            Write-Host "Aspect ratio options:" -ForegroundColor Yellow
            Write-Host "  1) 1:1 (square)"
            Write-Host "  2) 9:16 (portrait) - DEFAULT"
            Write-Host "  3) 16:9 (landscape)"
            $aspectChoice = Read-Host "Select aspect (1-3, default 2)"
            switch ($aspectChoice) {
                "1" { $aspect = "1:1" }
                "3" { $aspect = "16:9" }
                default { $aspect = "9:16" }
            }

            Write-Host ""
            $seedInput = Read-Host "Seed for reproducibility (optional, press enter for random)"
            if ($seedInput) {
                $seedParam = "--seed $seedInput"
            } else {
                $seedParam = ""
            }

            Write-Host ""
            $outInput = Read-Host "Output filename (optional, e.g., myimage.jpg) - leave blank for mediaId only"
            if ($outInput) {
                $outParam = "--out $outInput"
            } else {
                $outParam = ""
            }

            Write-Host ""
            $dryRun = Read-Host "Show cost only? (y/N)" -caseSensitive:$false
            if ($dryRun -eq "y") {
                $dryRunParam = "--dryRun"
            } else {
                $dryRunParam = ""
            }

            Write-Host ""
            $yesInput = Read-Host "Skip confirmation and generate? (y/N)" -caseSensitive:$false
            if ($yesInput -notin @("y", "Y")) {
                Write-Host "Add `--yes` to actually submit the request." -ForegroundColor Yellow
                Pause
                continue
            }
            $yesParam = "--yes"

            # Build and execute command
            $cmd = "opencli flow image-gen --prompt `"$prompt`" --model $model --count $count --aspect $aspect $seedParam $outParam $dryRunParam $yesParam"
            Write-Host ""
            Write-Host "Running: $cmd" -ForegroundColor Cyan
            Write-Host ""

            try {
                & opencli flow image-gen --prompt "$prompt" --model $model --count $count --aspect $aspect $seedParam $outParam $dryRunParam $yesParam
                # Save last used settings
                $settings = @"
LastPrompt=$prompt
LastModel=$model
LastCount=$count
LastAspect=$aspect
LastSeed=$seedInput
LastOut=$outInput
"@
                $settings | Set-Content -Path "$env:TEMP\flow-image-last.txt" -Encoding UTF8
            } catch {
                Write-Host "Error: $_" -ForegroundColor Red
            }

            Write-Host ""
            Pause
        }

        "2" {
            # Batch Generation
            clear
            Write-Host "=== BATCH IMAGE GENERATION ===" -ForegroundColor Green
            Write-Host ""

            $filePath = Read-Host "Enter path to prompt file (one prompt per line, or JSON array)"
            if (-not (Test-Path $filePath)) {
                Write-Host "File not found: $filePath" -ForegroundColor Red
                Pause
                continue
            }

            Write-Host ""
            $outDir = Read-Host "Output directory (will be created if doesn't exist)"
            if (-not $outDir) { $outDir = "$env:USERPROFILE\Desktop\flow-results" }

            Write-Host ""
            Write-Host "Model options:" -ForegroundColor Yellow
            Write-Host "  1) nano-banana-2-lite (fastest, cheapest)"
            Write-Host "  2) nano-banana-2 (balanced) - DEFAULT"
            Write-Host "  3) nano-banana-2-pro (highest quality)"
            Write-Host "  4) imagen-4 (latest)"
            $modelChoice = Read-Host "Select model (1-4, default 2)"
            switch ($modelChoice) {
                "1" { $model = "nano-banana-2-lite" }
                "3" { $model = "nano-banana-2-pro" }
                "4" { $model = "imagen-4" }
                default { $model = "nano-banana-2" }
            }

            Write-Host ""
            $countInput = Read-Host "Variations per prompt (1-4, default 1)"
            if ($countInput -and [int]::TryParse($countInput, [ref]$num) -and $num -ge 1 -and $num -le 4) {
                $count = $countInput
            } else {
                $count = "1"
            }

            Write-Host ""
            Write-Host "Aspect ratio options:" -ForegroundColor Yellow
            Write-Host "  1) 1:1 (square)"
            Write-Host "  2) 9:16 (portrait) - DEFAULT"
            Write-Host "  3) 16:9 (landscape)"
            $aspectChoice = Read-Host "Select aspect (1-3, default 2)"
            switch ($aspectChoice) {
                "1" { $aspect = "1:1" }
                "3" { $aspect = "16:9" }
                default { $aspect = "9:16" }
            }

            Write-Host ""
            $seedInput = Read-Host "Base seed (optional, each prompt increments this)"
            if ($seedInput) {
                $seedParam = "--seed $seedInput"
            } else {
                $seedParam = ""
            }

            Write-Host ""
            Write-Host "Multi-account setup:" -ForegroundColor Yellow
            Write-Host "Enter comma-separated profile names (e.g., flow-account-1,flow-account-2)"
            Write-Host "Leave blank to use default profile"
            $profilesInput = Read-Host "Profiles"
            if ($profilesInput) {
                $profilesParam = "--profiles $profilesInput"
            } else {
                $profilesParam = ""
            }

            Write-Host ""
            $retryInput = Read-Host "Enable retry on rate limits? (Y/n)" -caseSensitive:$false
            if ($retryInput -notin @("n", "N")) {
                $retryParam = "--retry"
            } else {
                $retryParam = ""
            }

            Write-Host ""
            $maxRetriesInput = Read-Host "Max retries per account (default 3)"
            if ($maxRetriesInput) {
                $maxRetriesParam = "--max-retries $maxRetriesInput"
            } else {
                $maxRetriesParam = "--max-retries 3"
            }

            Write-Host ""
            $yesInput = Read-Host "Skip confirmation and generate? (y/N)" -caseSensitive:$false
            if ($yesInput -notin @("y", "Y")) {
                Write-Host "Add `--yes` to actually submit the request." -ForegroundColor Yellow
                Pause
                continue
            }
            $yesParam = "--yes"

            # Build and execute command
            $cmd = "opencli flow image-batch --file `"$filePath`" --output-dir `"$outDir`" --model $model --count $count --aspect $aspect $seedParam $profilesParam $retryParam $maxRetriesParam $yesParam"
            Write-Host ""
            Write-Host "Running: $cmd" -ForegroundColor Cyan
            Write-Host ""

            try {
                & opencli flow image-batch --file "$filePath" --output-dir "$outDir" --model $model --count $count --aspect $aspect $seedParam $profilesParam $retryParam $maxRetriesParam $yesParam
                # Save last output directory
                "LastOutputDir=$outDir" | Add-Content -Path "$env:TEMP\flow-image-last.txt"
            } catch {
                Write-Host "Error: $_" -ForegroundColor Red
            }

            Write-Host ""
            Pause
        }

        "3" {
            # Profile Setup Helper
            clear
            Write-Host "=== MULTI-ACCOUNT PROFILE SETUP ===" -ForegroundColor Green
            Write-Host ""
            Write-Host "To use multiple Google Accounts for bypassing rate limits:"
            Write-Host ""
            Write-Host "1. Each Google Account needs its own Chrome profile"
            Write-Host "2. opencli can switch between these profiles automatically"
            Write-Host ""
            Write-Host "Current profiles:" -ForegroundColor Yellow
            & opencli profile list
            Write-Host ""
            Write-Host "Recommended setup:" -ForegroundColor Yellow
            Write-Host "  Rename Profile 1 -> flow-account-1"
            Write-Host "  Rename Profile 2 -> flow-account-2"
            Write-Host "  (Add more as needed)"
            Write-Host ""
            $doRename = Read-Host "Rename first two profiles now? (Y/n)" -caseSensitive:$false
            if ($doRename -notin @("n", "N")) {
                & opencli profile rename "Profile 1" "flow-account-1" 2>$null
                & opencli profile rename "Profile 2" "flow-account-2" 2>$null
                Write-Host ""
                Write-Host "Profiles renamed successfully!" -ForegroundColor Green
                Write-Host ""
                Write-Host "NEXT STEPS:" -ForegroundColor Yellow
                Write-Host "For EACH profile, you must:"
                Write-Host "1. Launch Chrome with that profile:"
                Write-Host "   start chrome --profile-directory=`"flow-account-1`" https://labs.google/fx/tools/flow"
                Write-Host "2. Log into Google Flow in that window"
                Write-Host "3. Close the browser (session cookies are saved)"
                Write-Host ""
                Write-Host "Example commands to run in a REGULAR CMD prompt (not PowerShell):" -ForegroundColor Cyan
                Write-Host "  start chrome --profile-directory=`"flow-account-1`" https://labs.google/fx/tools/flow"
                Write-Host "  start chrome --profile-directory=`"flow-account-2`" https://labs.google/fx/tools/flow"
            }
            Write-Host ""
            Pause
        }

        "4" {
            # Check Credits
            clear
            Write-Host "=== FLOW CREDITS CHECK ===" -ForegroundColor Green
            Write-Host ""
            Write-Host "Checking credits for default profile..." -ForegroundColor Yellow
            & opencli flow credits
            Write-Host ""
            Write-Host "To check specific profile:" -ForegroundColor Yellow
            Write-Host "  opencli flow credits --profile flow-account-1"
            Write-Host ""
            Pause
        }

        "5" {
            # View Results
            clear
            Write-Host "=== VIEW LAST RESULTS ===" -ForegroundColor Green
            Write-Host ""
            if (Test-Path "$env:TEMP\flow-image-last.txt") {
                $lastSettings = Get-Content "$env:TEMP\flow-image-last.txt"
                Write-Host "Last used settings:`n$lastSettings" -ForegroundColor Yellow
            } else {
                Write-Host "No previous run recorded." -ForegroundColor Yellow
            }
            Write-Host ""
            $defaultResults = "$env:USERPROFILE\Desktop\flow-results"
            if (Test-Path $defaultResults) {
                Write-Host "Default results folder exists:" -ForegroundColor Green
                Write-Host "  $defaultResults"
                $openFolder = Read-Host "Open it now? (y/N)" -caseSensitive:$false
                if ($openFolder -eq "y") {
                    # Use Invoke-Item to open folder in Explorer
                    Invoke-Item $defaultResults
                }
            } else {
                Write-Host "Default results folder not found." -ForegroundColor Yellow
                $customPath = Read-Host "Enter path to results folder (or press enter to skip)"
                if ($customPath -and (Test-Path $customPath)) {
                    Invoke-Item $customPath
                }
            }
            Write-Host ""
            Pause
        }

        "6" {
            # Help & Examples
            clear
            Write-Host "=== HELP & EXAMPLES ===" -ForegroundColor Green
            Write-Host ""
            Write-Host "QUICK START:" -ForegroundColor Yellow
            Write-Host "1. Set up profiles (option 3)"
            Write-Host "2. Log into Flow for each profile in Chrome"
            Write-Host "3. Use option 1 for single images or option 2 for batches"
            Write-Host ""
            Write-Host "EXAMPLE PROMPT FILE (create prompts.txt on desktop):" -ForegroundColor Yellow
            Write-Host "a sunrise over a futuristic city"
            Write-Host "a close-up of a dewy spiderweb"
            Write-Host "a vintage coffee cup on a wooden table"
            Write-Host "a portrait of an elderly woman smiling"
            Write-Host ""
            Write-Host "TIPS:" -ForegroundColor Yellow
            Write-Host "  * Use `--dryRun` first to check credit cost"
            Write-Host "  * `--refs ./style.jpg` adds reference images"
            Write-Host "  * `--count 4` creates 4 variations per prompt"
            Write-Host "  * `--aspect 1:1` for square images (good for social media)"
            Write-Host "  * `--seed 12345` for reproducible results"
            Write-Host ""
            Write-Host "TROUBLESHOOTING:" -ForegroundColor Yellow
            Write-Host "  * `INSUFFICIENT_CREDITS`: Check balance with option 4"
            Write-Host "  * `PUBLIC_ERROR_UNUSUAL_ACTIVITY`: Try again or use `--reload`"
            Write-Host "  * Command not found: Run `opencli daemon restart`"
            Write-Host "  * No images: Verify you used `--out` flag"
            Write-Host ""
            Pause
        }

        "7" {
            # POV Project Images - the "just works" path
            clear
            Write-Host "=== POV PROJECT IMAGES ===" -ForegroundColor Green
            Write-Host ""
            Write-Host "Point this at a POV project folder. It will read"
            Write-Host "05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt, generate every"
            Write-Host "image, and save them as <SEG_ID>.jpeg - exactly what the"
            Write-Host "assembler needs. Already-generated images are skipped."
            Write-Host ""
            $proj = Read-Host "Path to POV project folder (e.g. C:\Users\user\Desktop\Milo Video Factory\pov\projects\WW1)"
            if (-not (Test-Path "$proj\05_IMAGES\IMAGE_PROMPTS_BATCH_FINAL.txt")) {
                Write-Host "Prompt batch file not found at $proj\05_IMAGES\IMAGE_PROMPTS_BATCH_FINAL.txt" -ForegroundColor Red
                Write-Host "Run the image-director agent first so the batch file exists." -ForegroundColor Yellow
                Pause
                continue
            }
            Write-Host ""
            $forceInput = Read-Host "Re-generate images that already exist? (y/N)" -caseSensitive:$false
            $forceParam = ""
            if ($forceInput -eq "y") { $forceParam = "--force" }
            Write-Host ""
            $profilesInput = Read-Host "Account profiles to rotate (blank = default; e.g. flow-account-1,flow-account-2)"
            $profilesParam = ""
            if ($profilesInput) { $profilesParam = "--profiles $profilesInput" }
            Write-Host ""
            $cmd = "opencli flow images --file `"$proj\05_IMAGES\IMAGE_PROMPTS_BATCH_FINAL.txt`" $forceParam $profilesParam"
            Write-Host "Running: $cmd" -ForegroundColor Cyan
            Write-Host ""
            & opencli flow images --file "$proj\05_IMAGES\IMAGE_PROMPTS_BATCH_FINAL.txt" $forceParam $profilesParam
            Write-Host ""
            Pause
        }

        "0" {
            Write-Host "Goodbye!" -ForegroundColor Green
            break
        }

        default {
            Write-Host "Invalid option. Please try again." -ForegroundColor Red
            Start-Sleep -Seconds 1
        }
    }
}