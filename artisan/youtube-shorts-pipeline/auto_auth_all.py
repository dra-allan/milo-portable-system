#!/usr/bin/env python3
"""Auto-authenticate channels and write channel_id to channels.yaml automatically."""

import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "PyYAML"], check=True)
    import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANNELS_YAML = REPO_ROOT / "yt-secrets" / "channels.yaml"
AUTH_SCRIPT = REPO_ROOT / "yt_secrets" / "auth.py"

def load_channels():
    data = yaml.safe_load(CHANNELS_YAML.read_text(encoding="utf-8")) or {}
    return data.get("channels", {})

def save_channels(channels):
    data = {"channels": channels}
    CHANNELS_YAML.write_text(yaml.dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")

def extract_channel_id(output: str) -> str:
    """Extract channel_id from yt_secrets auth output."""
    # Matches: "OK  key: Title (UCxxxxxx), refreshed successfully"
    match = re.search(r'OK\s+\w+:\s+.*?\((UC[^)]+)\)', output)
    if match:
        return match.group(1)
    # Matches: "bound key -> UCxxxxxx"
    match = re.search(r'bound\s+\w+\s+->\s+(UC\w+)', output)
    if match:
        return match.group(1)
    return None

def auth_channel(channel_key: str, python_exe: str) -> bool:
    """Authenticate one channel and auto-write channel_id to channels.yaml."""
    print(f"\n{'='*70}")
    print(f"  AUTHENTICATING: {channel_key}")
    print(f"{'='*70}")
    
    channels = load_channels()
    if channel_key not in channels:
        print(f"ERROR: Unknown channel key: {channel_key}")
        return False
    
    info = channels[channel_key]
    email = info.get("email", "unknown")
    print(f"  Sign in as: {email}")
    
    # Run the auth command from artisan directory (where yt_secrets module lives)
    artisan_dir = REPO_ROOT
    cmd = [python_exe, "-m", "yt_secrets", "auth", "--channel", channel_key]
    print(f"  Running from {artisan_dir}: {' '.join(cmd)}")
    print()
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(artisan_dir))
        output = result.stdout + result.stderr
        print(output)
        
        if result.returncode != 0:
            print(f"  FAILED (exit code {result.returncode})")
            return False
        
        # Extract channel_id from output
        channel_id = extract_channel_id(output)
        if not channel_id:
            print(f"  WARNING: Could not extract channel_id from output")
            return False
        
        # Auto-write to channels.yaml
        channels[channel_key]["channel_id"] = channel_id
        save_channels(channels)
        print(f"  SUCCESS: Auto-wrote channel_id '{channel_id}' to channels.yaml")
        return True
        
    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT: Authentication took too long")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False

def main():
    python_exe = sys.executable
    
    # All 7 channels across both pipelines
    channels_to_auth = [
        ("capital_mindset", "draallan0@gmail.com", "SHORTS"),
        ("wealth_mindset", "adrasaltsxxx@gmail.com", "SHORTS"),
        ("flick_shorts", "draallan0@gmail.com", "SHORTS (borrows wealth_mindset client)"),
        ("chop_ug", "daadaallan0@gmail.com", "SHORTS"),
        ("NXS", "draallan12@gmail.com", "SHORTS - MUST CONSENT AS draallan12"),
        ("rankdrop", "daadaallan0@gmail.com", "RANKING (normal variant)"),
        ("the_other_guys", "allandaada@gmail.com", "RANKING (contrast variant)"),
    ]
    
    print("="*70)
    print("  AUTO-AUTHENTICATE ALL CHANNELS (SHORTS + RANKING)")
    print("="*70)
    print("This will authenticate each channel and AUTOMATICALLY write")
    print("the channel_id to artisan/yt-secrets/channels.yaml")
    print()
    
    for key, email, note in channels_to_auth:
        print(f"  {key:20s} -> {email:30s} [{note}]")
    
    print()
    confirm = input("Proceed with ALL 7 channels? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Cancelled.")
        return 1
    
    results = {}
    for key, email, note in channels_to_auth:
        print(f"\n>>> {key} ({note})")
        success = auth_channel(key, python_exe)
        results[key] = success
    
    print("\n" + "="*70)
    print("  SUMMARY")
    print("="*70)
    for key, success in results.items():
        status = "OK" if success else "FAILED"
        print(f"  {key:20s} : {status}")
    
    failed = [k for k, v in results.items() if not v]
    if failed:
        print(f"\nFailed channels: {', '.join(failed)}")
        return 1
    
    print("\nAll channels authenticated and channels.yaml updated!")
    return 0

if __name__ == "__main__":
    sys.exit(main())