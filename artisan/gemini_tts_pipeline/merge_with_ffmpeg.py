import subprocess
from pathlib import Path

FFMPEG = r"C:\Users\user\Desktop\AGENTIC WORK\ffmpeg-2026-05-18-git-b4d11dffbf-full_build\ffmpeg-2026-05-18-git-b4d11dffbf-full_build\bin\ffmpeg.exe"


def merge_audio_files(seg_dir: Path, output_path: Path) -> bool:
    wav_files = sorted(
        [f for f in seg_dir.glob("*.wav") if not f.name.startswith("_")]
    )
    if not wav_files:
        print(f"No WAV files found in {seg_dir}")
        return False

    print(f"Merging {len(wav_files)} WAV files into {output_path.name}...")

    concat_file = seg_dir / "_concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{f.name}'" for f in wav_files),
        encoding="utf-8"
    )

    cmd = [
        FFMPEG, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        str(output_path)
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        print(f"Merged -> {output_path}")
        concat_file.unlink()
        return True
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg merge failed, trying resample merge: {e.stderr.decode()[:200]}")
        try:
            cmd2 = [
                FFMPEG, "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-c:a", "pcm_s16le",
                "-ar", "44100",
                "-ac", "1",
                str(output_path)
            ]
            subprocess.run(cmd2, check=True, capture_output=True, timeout=300)
            print(f"Merged (resampled) -> {output_path}")
            concat_file.unlink()
            return True
        except subprocess.CalledProcessError as e2:
            print(f"Resample merge failed: {e2.stderr.decode()[:300]}")
        except Exception as e2:
            print(f"Resample merge error: {e2}")
        concat_file.unlink()
        return False
    except Exception as e:
        print(f"Merge error: {e}")
        return False
