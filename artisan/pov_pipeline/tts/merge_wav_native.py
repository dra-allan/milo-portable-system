import wave
import os
from pathlib import Path

def merge_wavs_native(target_dir, filenames, output_name="FINAL_COMBINED"):
    """
    Merges multiple WAV files into one using only Python's built-in wave module.
    No FFmpeg or Pydub required.
    """
    output_path = target_dir / f"_{output_name}.wav"
    
    # Filter for existing files
    valid_files = [target_dir / f for f in filenames if (target_dir / f).exists() and f.lower().endswith(".wav")]
    
    if not valid_files:
        print("No valid WAV files found to merge.")
        return None

    print(f"Native Merging {len(valid_files)} WAV files...")
    
    try:
        # Read the first file to get parameters
        with wave.open(str(valid_files[0]), 'rb') as first_wav:
            params = first_wav.getparams()
            
            with wave.open(str(output_path), 'wb') as output_wav:
                output_wav.setparams(params)
                
                for wav_path in valid_files:
                    with wave.open(str(wav_path), 'rb') as input_wav:
                        # Ensure consistency (optional check)
                        if input_wav.getparams() != params:
                            print(f"Warning: {wav_path.name} has different parameters. Skipping to avoid corruption.")
                            continue
                        output_wav.writeframes(input_wav.readframes(input_wav.getnframes()))
        
        print(f"✅ Successfully merged into: {output_path.name}")
        return str(output_path)
    except Exception as e:
        print(f"❌ Native merge failed: {e}")
        return None

if __name__ == "__main__":
    print("--- Native WAV Merger ---")
    folder_input = input("Enter path to project folder (inside 'output/'): ").strip()
    
    # Try to find the folder in output if it's just a name
    target = Path(folder_input)
    if not target.exists():
        target = Path("output") / folder_input
        
    if target.exists() and target.is_dir():
        # Find all chunks (001_, 002_, etc)
        wav_files = sorted([f.name for f in target.glob("*.wav") if not f.name.startswith("_")])
        if wav_files:
            merge_wavs_native(target, wav_files)
        else:
            print(f"No WAV files found in {target}")
    else:
        print(f"Folder not found: {target}")
