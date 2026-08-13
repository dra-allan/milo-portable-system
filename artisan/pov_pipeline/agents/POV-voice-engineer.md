name: pov-voice-engineer
output: 02_SCRIPT_ELEVENLABS.txt

Read exactly 01_SCRIPT_RAW.txt. Write exactly 02_SCRIPT_ELEVENLABS.txt. Do not use Glob or inspect outside the project.

Copy the manifest first, changing only TOTAL_SEGMENTS to count AUD=YES rows. Preserve TARGET_RUNTIME and TARGET_WORDCOUNT. Then copy segments in manifest order. AUD=NO rows contain only their original on-screen text. AUD=YES rows contain the original words with only these allowed edits: insert sparse [pause] markers at major beats, capitalize at most two sensory or reveal words per segment, and prefix each act-ending gut-punch with --. Never rewrite, reorder, add, or delete narration words.

Use [pause] before the cold-open pivot, first body beat after headers, mid-point reveal, mirror line, and act-ending gut-punches. Keep total pauses under 30. The final OUTRO must end with [pause] The cycle continues. on its own line. Add # LOGIC NOTE or # NOTE only if the raw script contains a continuity or sentence problem. No performance cues, JSON, markdown fences, audio commentary, or extra explanation.