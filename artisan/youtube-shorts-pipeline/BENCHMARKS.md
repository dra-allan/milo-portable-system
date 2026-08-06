# Measured Render Benchmarks

Hardware: 2 cores, CPU-only. Source: 20 s, 1280x720, 30 fps.
Output: 1080x1920, libx264 `veryfast`, CRF 23.
Reproduce with the scripts in this file's history; numbers are wall-clock.

## Where render time actually goes

| Configuration | Time | Note |
|---|---|---|
| OLD filter chain only (`-f null`) | **19.87 s** | filters alone ≈ 1x realtime |
| NEW `cheap` filter chain only (`-f null`) | **6.47 s** | **3.07x faster filters** |
| encode only, 720p, no filters | 4.85 s | encode floor |
| scale-only to 1080x1920 + encode | 8.92 s | |
| **OLD full render (filters + encode + audio)** | **18.87 s** | |
| **NEW `cheap` full render** | **13.02 s** | **1.45x end-to-end** |
| `black` bars full render | 9.37 s | 2.01x, but changes the look |
| `crop` (fill frame) full render | 12.24 s | 1.54x, loses frame edges |

**Conclusion:** `gblur=sigma=28` at full 1080x1920 was the single most
expensive operation in the pipeline — it cost more than the H.264 encode
itself. Removing it makes the *filter* stage 3.07x faster. End-to-end gain is
1.45x because the encode is then the floor, not 10x.

## Getting the same look for less: blur scale-invariance

A Gaussian blur of sigma S on an image downscaled by factor k looks like a
blur of sigma S/k on the original. The backdrop is out-of-focus by design, so
it can be blurred small and scaled up. SSIM measured against the original
full-resolution `gblur=sigma=28` output:

| Backdrop resolution | sigma | Time | SSIM vs original |
|---|---|---|---|
| 136x240 (k=8) | 3.5 | 13.11 s | **0.9749** |
| 270x480 (k=4) | 7 | 13.49 s | 0.9767 |
| 270x480 (k=4) | 8 | 13.54 s | 0.9624 |
| 360x640 (k=3) | 9.3 | 14.11 s | 0.9762 |
| 540x960 (k=2) | 14 | 14.57 s | 0.9791 |

Chosen: **136x240 @ sigma 3.5** — fastest, and SSIM 0.975 is visually
indistinguishable for a blurred backdrop. Note sigma must be 28/k; using
sigma=6 at k=8 (an early guess) scored only 0.870 and looked visibly
different. The sigma is now derived in code, not hardcoded.

Scaler flags: `flags=fast_bilinear` saves a further ~10% (6.61 s -> 5.97 s
filters-only) with no visible difference on a blurred backdrop.

## Parallel rendering: the claim that did not survive measurement

Every plan (and the original report) asserted `ThreadPoolExecutor(2)` gives
"2x render". Measured on 2 cores, rendering 4 clips:

| Strategy | Total | Per clip | Speedup |
|---|---|---|---|
| sequential | 26.46 s | 6.62 s | 1.00x |
| parallel, 2 workers, ffmpeg default threads | 25.99 s | 6.50 s | **1.02x** |
| parallel, 2 workers, `-threads 1` each | 24.94 s | 6.24 s | **1.06x** |

**There is no 2x.** libx264 already parallelises across all available cores,
so a single render already saturates the CPU; running two at once just splits
the same cores and adds memory pressure. Parallel rendering only pays when
there are idle cores (more cores than one encode can use) or when workers are
blocked on I/O.

Therefore:
- `RENDER_WORKERS` defaults to `min(2, max(1, cpu_count // 2))`, i.e. **1 on a
  2-core box**, and only rises on machines that can actually use it.
- The real overlap win is **network vs CPU**: fetching the next clip's footage
  while the current clip encodes. That is genuinely close to free, and it is
  what the pipeline now does.
