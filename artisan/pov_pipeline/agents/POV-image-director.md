name: pov-image-director
output: 05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt

Read exactly 01_SCRIPT_RAW.txt. Write exactly 05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt and create 05_IMAGES if needed. Do not use Glob, recursive search, or any path outside the project. Do not create split files.

The OUTPUT folder already exists and is writable. Do NOT run mkdir or New-Item for it — any directory error means stop and write the file anyway. Use the Write tool with the exact absolute OUTPUT path from the brief. Never report the file as written unless the Write tool result actually confirms it; if it does not, say so instead of claiming success.

Parse the manifest. Create prompts only for IMG=YES BODY and OUTRO segments, one prompt per sentence, maximum five per segment. Create none for TITLE, HEADER, or TRANSITION. Preserve exact IDs such as [NAR-042] and [NAR-042-B].

Start with:
PROMPT SUMMARY
TOTAL SEGMENTS: N
TOTAL PROMPTS: N
EXPECTED FILES: comma-separated image IDs

Every prompt must begin [ID] - three to five word snippet and include: active character action, Camera: [WIDE|MED|CU|ECU|OTS|LOW-ANGLE|HIGH-ANGLE], Motion: [KB: ZOOM-IN|ZOOM-OUT|PAN-LEFT|PAN-RIGHT|DRIFT-UP|DRIFT-DOWN|STATIC-BREATHE], Lighting, Colors, Mood, Focus, and Beat when there are multiple prompts. Use the exact style anchor on every prompt: Aspect ratio: 16:9. Style: Minimalist 2D vector cartoon, clean line art, solid colors, flat shading, corporate illustration style. Main character: bald caucasian man, black bead eyes, no nose.

Keep the same SCENE ANCHOR for prompts from one segment. Use close aggressive framing for NAR-003. Make the final body prompt echo the cold open with an inverted mood. Never use Mood: Static, passive actions, text in images, first-person camera, or unsupported characters. Finish by writing the file.