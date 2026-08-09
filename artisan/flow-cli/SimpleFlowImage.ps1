# SimpleFlowImage.ps1
# A simple, customized interface for Google Flow image generation
# Designed for ease of use - no need to remember complex CLI commands
# Save this file and run it in PowerShell: .\SimpleFlowImage.ps1

# ======================
# CONFIGURATION SECTION
# ======================
# Modify these settings to customize the tool for your workflow

# Default model for image generation
$DEFAULT_MODEL = "nano-banana-2"  # Options: nano-banana-2-lite, nano-banana-2, nano-banana-2-pro, imagen-4

# Default number of variations per prompt
$DEFAULT_COUNT = 1

# Default aspect ratio
$DEFAULT_ASPECT = "9:16"  # Options: 1:1, 9:16, 16:9

# Default output directory for batch processing
$DEFAULT_OUTPUT_DIR = "$env:USERPROFILE\Desktop\FlowImages"

# Whether to automatically create output directory
$AUTO_CREATE_OUTPUT_DIR = $true

# Enable verbose output for debugging
$VERBOSE_MODE = $false

# ======================
# HELPER FUNCTIONS
# ======================

function Write-Header {
    param([string]$title)
    Write-Host ""
    Write-Host "=== $title ===" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Success {
    param([string]$message)
    Write-Host "[SUCCESS] $message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$message)
    Write-Host "[WARNING] $message" -ForegroundColor Yellow
}

function Write-Error {
    param([string]$message)
    Write-Host "[ERROR] $message" -ForegroundColor Red
}

function Write-Info {
    param([string]$message)
    Write-Host "[INFO] $message" -ForegroundColor DarkCyan
}

function Confirm-Action {
    param([string]$prompt = "Continue? (y/N)")
    $response = Read-Host $prompt
    return $response -in @("y", "Y", "yes", "Yes")
}

# ======================
# MAIN MENU
# ======================

while ($true) {
    Clear-Host
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  SIMPLE FLOW IMAGE GENERATOR" -ForegroundColor Green
    Write-Host "  Custom tailored interface for easy image generation" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "1.  Check Flow Credits"
    Write-Host "2.  Generate Single Image"
    Write-Host "3.  Generate Batch Images from File"
    Write-Host "4.  Upload Reference Images"
    Write-Host "5.  List Uploaded Media"
    Write-Host "6.  Settings & Help"
    Write-Host "0.  Exit"
    Write-Host ""
    $choice = Read-Host "Select an option (0-6)"

    switch ($choice) {
        "1" {
            # Check Flow Credits
            Write-Header "FLOW CREDITS CHECK"
            Write-Info "Checking your Flow account credits..."
            try {
                & opencli flow credits
                Write-Success "Credit check completed."
            } catch {
                Write-Error "Failed to check credits: $_"
            }
            Write-Host ""
            if (-not (Confirm-Action "Return to main menu? (Y/n)")) { continue }
        }

        "2" {
            # Generate Single Image
            Write-Header "SINGLE IMAGE GENERATION"

            $prompt = Read-Host "Enter your image description (prompt)"
            if (-not $prompt) {
                Write-Warning "Prompt cannot be empty."
                if (-not (Confirm-Action "Try again? (y/N)")) { continue }
                else { continue }
            }

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
                default { $model = $DEFAULT_MODEL }
            }

            Write-Host ""
            $countInput = Read-Host "Number of variations (1-4, default $DEFAULT_COUNT)"
            if ($countInput -and [int]::TryParse($countInput, [ref]$num) -and $num -ge 1 -and $num -le 4) {
                $count = $countInput
            } else {
                $count = $DEFAULT_COUNT
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
                default { $aspect = $DEFAULT_ASPECT }
            }

            Write-Host ""
            $seedInput = Read-Host "Seed for reproducibility (optional, press enter for random)"
            $seedParam = if ($seedInput) { "--seed $seedInput" } else { "" }

            Write-Host ""
            $outInput = Read-Host "Output filename (optional, e.g., myimage.jpg)"
            $outParam = if ($outInput) { "--out $outInput" } else { "" }

            Write-Host ""
            Write-Host "Reference images (optional):" -ForegroundColor Yellow
            Write-Host "Enter comma-separated paths to reference images, or leave blank"
            $refsInput = Read-Host "Reference images (e.g., ./style.jpg,./layout.png)"
            $refsParam = if ($refsInput) { "--refs $refsInput" } else { "" }

            Write-Host ""
            $dryRun = Read-Host "Show cost only? (y/N)"
            $dryRunParam = if ($dryRun -eq "y") { "--dryRun" } else { "" }

            Write-Host ""
            $yesInput = Read-Host "Skip confirmation and generate? (y/N)"
            if ($yesInput -notin @("y", "Y")) {
                Write-Warning "Add `--yes` to actually submit the request."
                if (-not (Confirm-Action "Continue anyway? (y/N)")) { continue }
            }
            $yesParam = "--yes"

            # TODO: Replace this placeholder with actual image generation logic
            # For now, we'll show what the command would be and provide guidance
            Write-Host ""
            Write-Host "PREVIEW OF COMMAND TO RUN:" -ForegroundColor Cyan
            $cmd = "opencli flow image-gen --prompt `"$prompt`" --model $model --count $count --aspect $aspect $seedParam $outParam $dryRunParam $yesParam $refsParam"
            Write-Host $cmd
            Write-Host ""
            Write-Warning "NOTE: The 'flow image-gen' command is not yet available due to plugin compilation issues."
            Write-Host "To enable this functionality:"
            Write-Host "1. Fix the encoding issues in the TypeScript files (image-gen.ts, image-batch.ts)"
            Write-Host "2. Or install esbuild and run: opencli plugin update flow"
            Write-Host "3. Then this menu will automatically use the real command"
            Write-Host ""
            Write-Info "For now, you can manually run the command above once the plugin is fixed."

            if ($outInput) {
                Write-Host ""
                Write-Info "If --out is used, images will be saved as: $outInput (with _N suffix for count>1)"
            }

            Write-Host ""
            if (-not (Confirm-Action "Return to main menu? (Y/n)")) { continue }
        }

        "3" {
            # Generate Batch Images from File
            Write-Header "BATCH IMAGE GENERATION"

            $filePath = Read-Host "Enter path to prompt file (one prompt per line)"
            if (-not (Test-Path $filePath)) {
                Write-Error "File not found: $filePath"
                if (-not (Confirm-Action "Try again? (y/N)")) { continue }
                else { continue }
            }

            Write-Host ""
            $outDir = Read-Host "Output directory (default: $DEFAULT_OUTPUT_DIR)"
            if (-not $outDir) { $outDir = $DEFAULT_OUTPUT_DIR }

            if ($AUTO_CREATE_OUTPUT_DIR -and -(Test-Path $outDir)) {
                try {
                    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
                    Write-Success "Created output directory: $outDir"
                } catch {
                    Write-Error "Failed to create output directory: $_"
                    if (-not (Confirm-Action "Continue without output directory? (y/N)")) { continue }
                }
            }

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
                default { $model = $DEFAULT_MODEL }
            }

            Write-Host ""
            $countInput = Read-Host "Variations per prompt (1-4, default $DEFAULT_COUNT)"
            if ($countInput -and [int]::TryParse($countInput, [ref]$num) -and $num -ge 1 -and $num -le 4) {
                $count = $countInput
            } else {
                $count = $DEFAULT_COUNT
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
                default { $aspect = $DEFAULT_ASPECT }
            }

            Write-Host ""
            $seedInput = Read-Host "Base seed (optional, each prompt increments this)"
            $seedParam = if ($seedInput) { "--seed $seedInput" } else { "" }

            Write-Host ""
            Write-Host "Multi-account setup:" -ForegroundColor Yellow
            Write-Host "Enter comma-separated profile names (e.g., flow-account-1,flow-account-2)"
            Write-Host "Leave blank to use default profile"
            $profilesInput = Read-Host "Profiles for multi-account retry"
            $profilesParam = if ($profilesInput) { "--profiles $profilesInput" } else { "" }

            Write-Host ""
            $retryInput = Read-Host "Enable retry on rate limits? (Y/n)"
            $retryParam = if ($retryInput -notin @("n", "N")) { "--retry" } else { "" }

            Write-Host ""
            $maxRetriesInput = Read-Host "Max retries per account (default 3)"
            $maxRetriesParam = if ($maxRetriesInput) { "--max-retries $maxRetriesInput" } else { "--max-retries 3" }

            Write-Host ""
            $yesInput = Read-Host "Skip confirmation and generate? (y/N)"
            if ($yesInput -notin @("y", "Y")) {
                Write-Warning "Add `--yes` to actually submit the request."
                if (-not (Confirm-Action "Continue anyway? (y/N)")) { continue }
            }
            $yesParam = "--yes"

            # TODO: Replace this placeholder with actual batch image generation logic
            Write-Host ""
            Write-Host "PREVIEW OF COMMAND TO RUN:" -ForegroundColor Cyan
            $cmd = "opencli flow image-batch --file `"$filePath`" --output-dir `"$outDir`" --model $model --count $count --aspect $aspect $seedParam $profilesParam $retryParam $maxRetriesParam $yesParam"
            Write-Host $cmd
            Write-Host ""
            Write-Warning "NOTE: The 'flow image-batch' command is not yet available due to plugin compilation issues."
            Write-Host "To enable this functionality:"
            Write-Host "1. Fix the encoding issues in the TypeScript files (image-gen.ts, image-batch.ts)"
            Write-Host "2. Or install esbuild and run: opencli plugin update flow"
            Write-Host "3. Then this menu will automatically use the real command"
            Write-Host ""
            Write-Info "For now, you can manually run the command above once the plugin is fixed."
            Write-Info "The script will process each line in your file as a separate prompt."

            Write-Host ""
            if (-not (Confirm-Action "Return to main menu? (Y/n)")) { continue }
        }

        "4" {
            # Upload Reference Images
            Write-Header "UPLOAD REFERENCE IMAGES"
            Write-Info "Upload images to Flow for use as references in generation."
            Write-Host ""
            $imagePath = Read-Host "Enter path to image file to upload (or drag & drop file here)"
            # Remove quotes if user dragged & dropped
            $imagePath = $imagePath.Trim('"')
            if (-not (Test-Path $imagePath)) {
                Write-Error "File not found: $imagePath"
                if (-not (Confirm-Action "Try again? (y/N)")) { continue }
                else { continue }
            }

            Write-Host ""
            $aliasInput = Read-Host "Optional alias for this image (e.g., 'style', 'logo')"
            $aliasParam = if ($aliasInput) { "--alias $aliasInput" } else { "" }

            Write-Host ""
            Write-Host "Uploading: $imagePath" -ForegroundColor Yellow
            if ($aliasInput) { Write-Host "With alias: $aliasInput" }

            try {
                & opencli flow media-upload --file `"$imagePath`" $aliasParam
                Write-Success "Image uploaded successfully!"
                Write-Info "You can now use this image as a reference with --refs ./$imagePath or by alias"
            } catch {
                Write-Error "Failed to upload image: $_"
            }

            Write-Host ""
            if (-not (Confirm-Action "Return to main menu? (Y/n)")) { continue }
        }

        "5" {
            # List Uploaded Media
            Write-Header "LIST UPLOADED MEDIA"
            Write-Info "Viewing your uploaded images and videos in Flow..."
            try {
                & opencli flow media-list
                Write-Success "Media list displayed above."
            } catch {
                Write-Error "Failed to list media: $_"
            }
            Write-Host ""
            if (-not (Confirm-Action "Return to main menu? (Y/n)")) { continue }
        }

        "6" {
            # Settings & Help
            while ($true) {
                Clear-Host
                Write-Header "SETTINGS & HELP"
                Write-Host ""
                Write-Host "CURRENT SETTINGS:" -ForegroundColor Yellow
                Write-Host "  Default model: $DEFAULT_MODEL"
                Write-Host "  Default count per prompt: $DEFAULT_COUNT"
                Write-Host "  Default aspect ratio: $DEFAULT_ASPECT"
                Write-Host "  Default output dir: $DEFAULT_OUTPUT_DIR"
                Write-Host "  Auto-create output dir: $AUTO_CREATE_OUTPUT_DIR"
                Write-Host "  Verbose mode: $VERBOSE_MODE"
                Write-Host ""
                Write-Host "OPTIONS:" -ForegroundColor Yellow
                Write-Host "  1. Change default model"
                Write-Host "  2. Change default count"
                Write-Host "  3. Change default aspect ratio"
                Write-Host "  4. Change default output directory"
                Write-Host "  5. Toggle auto-create output directory"
                Write-Host "  6. Toggle verbose mode"
                Write-Host "  7. View help & examples"
                Write-Host "  0. Return to main menu"
                Write-Host ""
                $subChoice = Read-Host "Select an option (0-7)"

                switch ($subChoice) {
                    "1" {
                        Write-Host ""
                        Write-Host "Model options:" -ForegroundColor Yellow
                        Write-Host "  1) nano-banana-2-lite (fastest, cheapest)"
                        Write-Host "  2) nano-banana-2 (balanced) - DEFAULT"
                        Write-Host "  3) nano-banana-2-pro (highest quality)"
                        Write-Host "  4) imagen-4 (latest)"
                        $modelChoice = Read-Host "Select model (1-4)"
                        switch ($modelChoice) {
                            "1" { $DEFAULT_MODEL = "nano-banana-2-lite" }
                            "2" { $DEFAULT_MODEL = "nano-banana-2" }
                            "3" { $DEFAULT_MODEL = "nano-banana-2-pro" }
                            "4" { $DEFAULT_MODEL = "imagen-4" }
                        }
                        Write-Success "Default model updated to: $DEFAULT_MODEL"
                        Start-Sleep -Seconds 1
                    }
                    "2" {
                        Write-Host ""
                        $countInput = Read-Host "Enter default count (1-4)"
                        if ($countInput -and [int]::TryParse($countInput, [ref]$num) -and $num -ge 1 -and $num -le 4) {
                            $DEFAULT_COUNT = [int]$countInput
                            Write-Success "Default count updated to: $DEFAULT_COUNT"
                        } else {
                            Write-Warning "Invalid input. Please enter a number between 1-4."
                        }
                        Start-Sleep -Seconds 1
                    }
                    "3" {
                        Write-Host ""
                        Write-Host "Aspect ratio options:" -ForegroundColor Yellow
                        Write-Host "  1) 1:1 (square)"
                        Write-Host "  2) 9:16 (portrait) - DEFAULT"
                        Write-Host "  3) 16:9 (landscape)"
                        $aspectChoice = Read-Host "Select aspect (1-3)"
                        switch ($aspectChoice) {
                            "1" { $DEFAULT_ASPECT = "1:1" }
                            "2" { $DEFAULT_ASPECT = "9:16" }
                            "3" { $DEFAULT_ASPECT = "16:9" }
                        }
                        Write-Success "Default aspect ratio updated to: $DEFAULT_ASPECT"
                        Start-Sleep -Seconds 1
                    }
                    "4" {
                        Write-Host ""
                        $dirInput = Read-Host "Enter default output directory"
                        if ($dirInput) {
                            $DEFAULT_OUTPUT_DIR = $dirInput
                            Write-Success "Default output directory updated to: $DEFAULT_OUTPUT_DIR"
                        } else {
                            Write-Warning "No change made."
                        }
                        Start-Sleep -Seconds 1
                    }
                    "5" {
                        $AUTO_CREATE_OUTPUT_DIR = -not $AUTO_CREATE_OUTPUT_DIR
                        $status = if ($AUTO_CREATE_OUTPUT_DIR) {"enabled"} else {"disabled"}
                        Write-Success "Auto-create output directory: $status"
                        Start-Sleep -Seconds 1
                    }
                    "6" {
                        $VERBOSE_MODE = -not $VERBOSE_MODE
                        $status = if ($VERBOSE_MODE) {"enabled"} else {"disabled"}
                        Write-Success "Verbose mode: $status"
                        Start-Sleep -Seconds 1
                    }
                    "7" {
                        Clear-Host
                        Write-Host "========================================" -ForegroundColor Cyan
                        Write-Host "  HELP & EXAMPLES" -ForegroundColor Green
                        Write-Host "========================================" -ForegroundColor Cyan
                        Write-Host ""
                        Write-Host "QUICK START:" -ForegroundColor Yellow
                        Write-Host "1. Use option 1 to check your Flow credits"
                        Write-Host "2. Use option 4 to upload any reference images you'll need"
                        Write-Host "3. Use option 2 for single images or option 3 for batch processing"
                        Write-Host ""
                        Write-Host "EXAMPLE PROMPT FILE (create prompts.txt on desktop):" -ForegroundColor Yellow
                        Write-Host "a sunrise over a futuristic city"
                        Write-Host "a close-up of a dewy spiderweb"
                        Write-Host "a vintage coffee cup on a wooden table"
                        Write-Host "a portrait of an elderly woman smiling"
                        Write-Host ""
                        Write-Host "TIPS:" -ForegroundColor Yellow
                        Write-Host "  * Use --dryRun first to check credit cost before generating"
                        Write-Host "  * Use --refs to add reference images for style or composition guidance"
                        Write-Host "  * Use --count 4 to get 4 variations of each prompt (great for exploring ideas)"
                        Write-Host "  * Use --aspect 1:1 for square images (ideal for Instagram posts)"
                        Write-Host "  * Use --seed 12345 for reproducible results (same seed + prompt = same image)"
                        Write-Host ""
                        Write-Host "MULTI-ACCOUNT SETUP:" -ForegroundColor Yellow
                        Write-Host "To use multiple Google Accounts for bypassing rate limits:"
                        Write-Host "1. Each Account needs its own Chrome profile"
                        Write-Host "2. Log into Flow for each account in Chrome"
                        Write-Host "3. Use the --profiles option to specify which accounts to use"
                        Write-Host "   Example: --profiles acc1,acc2,acc3"
                        Write-Host "4. The system will automatically switch accounts when rate limits are hit"
                        Write-Host ""
                        Write-Host "EXAMPLE COMMANDS TO SETUP CHROME PROFILES:" -ForegroundColor Yellow
                        Write-Host "# Close all Chrome windows first"
                        Write-Host "Get-Process chrome | Stop-Process -ErrorAction SilentlyContinue"
                        Write-Host ""
                        Write-Host "# Launch Chrome with profile for Account 1"
                        Write-Host "start chrome --profile-directory=`"flow-account-1`" https://labs.google/fx/tools/flow"
                        Write-Host "# Log into Flow in that window, then close it"
                        Write-Host ""
                        Write-Host "# Launch Chrome with profile for Account 2"
                        Write-Host "start chrome --profile-directory=`"flow-account-2`" https://labs.google/fx/tools/flow"
                        Write-Host "# Log into Flow in that window, then close it"
                        Write-Host ""
                        Write-Host "Then use: --profiles flow-account-1,flow-account-2"
                        Write-Host ""
                        Write-Host "TROUBLESHOOTING:" -ForegroundColor Yellow
                        Write-Host "  * Plugin flow/*ts: no compiled .js found: Run opencli plugin update flow after installing esbuild"
                        Write-Host "  * INSUFFICIENT_CREDITS: Check balance with option 1"
                        Write-Host "  * PUBLIC_ERROR_UNUSUAL_ACTIVITY: Try again or use the --reload flag"
                        Write-Host "  * Command not found: Run opencli daemon restart"
                        Write-Host "  * No images saved: Verify you used --out or check default download location"
                        Write-Host ""
                        Write-Host "Press any key to return to settings..."
                        $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDow")
                    }
                    "0" {
                        break
                    }
                    default {
                        Write-Warning "Invalid option. Please try again."
                        Start-Sleep -Seconds 1
                    }
                }
            }
        }

        "0" {
            Write-Host "Goodbye!" -ForegroundColor Green
            break
        }

        default {
            Write-Warning "Invalid option. Please try again."
            Start-Sleep -Seconds 1
        }
    }
}