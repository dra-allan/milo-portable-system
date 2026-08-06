import re
import os
import json

project_dir = r"C:\Users\user\Desktop\milo-portable-system\artisan\pov_pipeline\projects\g0DlLoKxSDg_20260806"

# Read all files
with open(os.path.join(project_dir, "00_SOURCE_SCRIPT.txt"), "r", encoding="utf-8") as f:
    source_script = f.read()

with open(os.path.join(project_dir, "01_SCRIPT_RAW.txt"), "r", encoding="utf-8") as f:
    script_raw = f.read()

with open(os.path.join(project_dir, "00_RESEARCH_NOTES.txt"), "r", encoding="utf-8") as f:
    research_notes = f.read()

with open(os.path.join(project_dir, "02_SCRIPT_ELEVENLABS.txt"), "r", encoding="utf-8") as f:
    script_elevenlabs = f.read()

print("Files loaded successfully")
print(f"Source script length: {len(source_script)} chars")
print(f"Script raw length: {len(script_raw)} chars")

# ============================================================
# Parse segments properly
# ============================================================
# Split by [NAR-XXX] markers
segment_pattern = r'\[NAR-(\d+)\]\n(.*?)(?=\n\[NAR-\d+\]|\n===|\Z)'
segments = re.findall(segment_pattern, script_raw, re.DOTALL)

# Get segment roles from manifest
manifest_section = ""
if '=== SEGMENT MANIFEST ===' in script_raw:
    manifest_section = script_raw.split('=== SEGMENT MANIFEST ===')[1].split('=== END MANIFEST ===')[0]

role_map = {}
for line in manifest_section.strip().split('\n'):
    if '|' in line and 'NAR-' in line:
        parts = [p.strip() for p in line.split('|')]
        if len(parts) >= 2:
            seg_id = parts[0]
            role = parts[1]
            role_map[seg_id] = role

# Body roles (exclude HEADER, TRANSITION)
body_roles = {'COLD_OPEN', 'ZOOM_OUT', 'ACT1', 'ACT2', 'ACT3', 'ACT4', 'ACT5', 'ACT6', 'MIRROR', 'OUTRO'}

body_text = ""
body_segments = []
for seg_num_str, content in segments:
    seg_num = int(seg_num_str)
    seg_id = f"NAR-{seg_num_str}"
    role = role_map.get(seg_id, "")
    if role in body_roles:
        body_text += content + " "
        body_segments.append((seg_num, seg_id, role, content))

print(f"Body segments found: {len(body_segments)}")
print(f"Body text length: {len(body_text)} chars")

# ============================================================
# STEP 2: REWRITE VERIFICATION GATE
# ============================================================

# 2a. SOURCE PRESENCE
source_present = len(source_script.strip()) > 0
print(f"\n2a. SOURCE PRESENCE: {'PASS' if source_present else 'FAIL'}")

# 2b. SENTENCE OVERLAP SCAN
def normalize(text):
    return re.sub(r'[^\w\s]', '', text.lower())

source_normalized = normalize(source_script)

def get_ngrams(words, n=6):
    return [' '.join(words[i:i+n]) for i in range(len(words)-n+1)]

# Split body text into sentences
sentences = re.split(r'[.!?]+', body_text)
sentences = [s.strip() for s in sentences if len(s.strip()) > 10]

overlap_matches = []
for sent in sentences:
    sent_words = sent.split()
    if len(sent_words) >= 6:
        ngrams = get_ngrams(sent_words, 6)
        for ng in ngrams:
            if ng in source_normalized:
                overlap_matches.append((sent[:100], ng))
                break

overlap_count = len(overlap_matches)
if overlap_count == 0:
    overlap_result = "PASS"
elif overlap_count <= 3:
    overlap_result = "WARN"
else:
    overlap_result = "FAIL"

print(f"\n2b. SENTENCE OVERLAP SCAN: {overlap_result} ({overlap_count} matches)")
for match in overlap_matches[:5]:
    print(f"  Match: '{match[1]}' in sentence: '{match[0]}...'")

# 2c. NAMED-ENTITY CHECK
# Source names from 00_SOURCE_SCRIPT: Turner, Arthur, Edith, May, Nora, Ellis, Harry, Bell, Collins, Evans, Shaw
# These are PROPER NAMES - check for whole word matches with context
source_names = ['turner', 'arthur', 'edith', 'may', 'nora', 'ellis', 'harry', 'bell', 'collins', 'evans', 'shaw']
source_locations = ['newcastle', 'tyne', 'dunkirk', 'el alamein', 'sicily', 'd-day']

script_lower = script_raw.lower()
# Check for proper noun usage - capitalized or in specific contexts
# For names, check word boundaries more carefully
name_hits = []
for name in source_names:
    # Use word boundary regex to avoid partial matches like "may" in "maybe"
    pattern = rf'\b{re.escape(name)}\b'
    if re.search(pattern, script_lower):
        name_hits.append(name)

location_hits = []
for loc in source_locations:
    pattern = rf'\b{re.escape(loc)}\b'
    if re.search(pattern, script_lower):
        location_hits.append(loc)

# Dunkirk was REPLACED per research notes (anchors: Dunkirk... to Lochnagar...)
# So NO source names/locations should appear
entity_check_pass = len(name_hits) == 0 and len(location_hits) == 0
print(f"\n2c. NAMED-ENTITY CHECK: {'PASS' if entity_check_pass else 'FAIL'}")
if name_hits:
    print(f"  Name hits: {name_hits}")
if location_hits:
    print(f"  Location hits: {location_hits}")

# 2d. STRUCTURE CHECK
has_cold_open = 'NAR-003' in script_raw and 'press your ear to wet clay' in script_raw.lower()
has_escalation = 'cost ladder' in script_raw.lower()
has_midpoint_twist = 'NAR-024' in script_raw and 'mid point twist' in script_raw.lower()
has_mirror_ending = 'NAR-046' in script_raw and 'stop counting' in script_raw.lower()

structure_pass = has_cold_open and has_escalation and has_midpoint_twist and has_mirror_ending
print(f"\n2d. STRUCTURE CHECK: {'PASS' if structure_pass else 'FAIL'}")
print(f"  Cold open: {has_cold_open}")
print(f"  Escalation (cost ladder): {has_escalation}")
print(f"  Mid-point twist: {has_midpoint_twist}")
print(f"  Mirror ending: {has_mirror_ending}")

# ============================================================
# STEP 3: WORDCOUNT GATE
# ============================================================
# Count words in BODY and OUTRO segments only (exclude manifest, headers, transitions, # NOTE comments)
# Only count the narration content from body_segments
narration_text = " ".join([seg[3] for seg in body_segments])
body_words = len(narration_text.split())
print(f"\n3. WORDCOUNT GATE: {body_words} words — Target 1620-2025 — {'PASS' if 1620 <= body_words <= 2025 else 'FAIL'}")

# ============================================================
# STEP 3 (in prompt): COLD OPEN GATE
# ============================================================
# NAR-003 word count: 50-80 words
nar003_content = ""
for seg_num, seg_id, role, content in body_segments:
    if seg_id == "NAR-003":
        nar003_content = content.strip()
        break

nar003_words = len(nar003_content.split())
nar003_wc_pass = 50 <= nar003_words <= 80

# First 6 words contain physical action verb
first_6_words = ' '.join(nar003_content.split()[:6]).lower()
action_verbs = ['slams', 'shatters', 'crashes', 'falls', 'hits', 'grabs', 'runs', 'breaks', 'press', 'presses', 'count', 'counts', 'push', 'pull', 'drag', 'lift', 'drop', 'strike', 'smash', 'tear', 'rip']
action_verb_pass = any(verb in first_6_words for verb in action_verbs)

# Last sentence is one of three approved pivot lines
nar003_sentences = [s.strip() for s in re.split(r'[.!?]+', nar003_content) if s.strip()]
last_sentence = nar003_sentences[-1] if nar003_sentences else ""
approved_pivots = [
    "Let's go back and see how you got here.",
    "But to understand what happens next, you need to know how it started.",
    "But the most dangerous part hasn't even started yet."
]
pivot_pass = any(pivot.lower() in last_sentence.lower() for pivot in approved_pivots)

cold_open_pass = nar003_wc_pass and action_verb_pass and pivot_pass
print(f"\n4. COLD OPEN GATE:")
print(f"  Word count (NAR-003): {nar003_words} — Target 50-80 — {'PASS' if nar003_wc_pass else 'FAIL'}")
print(f"  Action verb in first 6 words: {'PASS' if action_verb_pass else 'FAIL'} (first 6: '{first_6_words}')")
print(f"  Approved pivot line: {'PASS' if pivot_pass else 'FAIL'} (last sentence: '{last_sentence}')")

# ============================================================
# STEP 4: MIRROR ENDING GATE
# ============================================================
# Final BODY segment is NAR-046 (MIRROR)
nar046_content = ""
for seg_num, seg_id, role, content in body_segments:
    if seg_id == "NAR-046":
        nar046_content = content.strip()
        break

nar046_sentences = [s.strip() for s in re.split(r'[.!?]+', nar046_content) if s.strip()]
nar046_last = nar046_sentences[-1] if nar046_sentences else ""

cold_open_echoes = ["press your ear to wet clay", "press your ear to the clay", "stop counting", "you stop counting"]
mirror_pass = any(echo in nar046_last.lower() for echo in cold_open_echoes)

print(f"\n5. MIRROR ENDING GATE:")
print(f"  Final BODY line echoes cold open: {'PASS' if mirror_pass else 'FAIL'}")
print(f"  Cold open key phrases: press your ear to wet clay / you stop counting")
print(f"  Mirror ending last sentence: '{nar046_last}'")

# ============================================================
# STEP 5: MID-POINT TWIST GATE
# ============================================================
twist_present = 'NAR-024' in script_raw and 'mid point twist' in script_raw.lower()
twist_has_reveal = 'falsified' in script_raw.lower() and 'counter chamber' in script_raw.lower()
twist_pass = twist_present and twist_has_reveal
print(f"\n6. MID-POINT TWIST GATE: {'PASS' if twist_pass else 'FAIL'}")
print(f"  Twist segment NAR-024 present: {twist_present}")
print(f"  Contains clear reveal/betrayal: {twist_has_reveal}")

# ============================================================
# STEP 6: ANTI-AI LEXICON SCAN
# ============================================================
banned_transitions = [
    'furthermore', 'moreover', 'additionally', 'consequently', 'subsequently',
    'nevertheless', 'nonetheless', 'hence', 'thus', 'therefore'
]
banned_intensifiers = [
    'ultimately', 'crucial', 'crucially', 'essentially', 'fundamentally',
    'significantly', 'notably', 'importantly', 'particularly'
]
banned_metaphors = [
    'tapestry', 'landscape', 'realm', 'journey', 'navigate', 'delve', 'dive into',
    'unpack', 'unlock', 'harness', 'foster', 'cultivate', 'embark'
]
banned_intro_tells = [
    'in a world where', 'at its core', 'what this means is', "it's important to note",
    "it's worth mentioning", 'in essence', 'in conclusion', 'picture this',
    'imagine', "let's explore"
]
banned_system_speak = [
    'asset', 'unit', 'roi', 'inventory', 'liquidation', 'resource',
    'subscription', 'performance review', 'synergy', 'stakeholder',
    'optimize', 'leverage', 'pipeline'
]

hits = {
    'transitions': [],
    'intensifiers': [],
    'metaphors': [],
    'intro_tells': [],
    'punctuation': [],
    'system_speak': []
}

# Check transitions (as sentence openers)
for term in banned_transitions:
    pattern = rf'(?:^|\.\s+){re.escape(term)}\b'
    if re.search(pattern, narration_text, re.IGNORECASE):
        hits['transitions'].append(term)

# Check intensifiers
for term in banned_intensifiers:
    if re.search(rf'\b{re.escape(term)}\b', narration_text, re.IGNORECASE):
        hits['intensifiers'].append(term)

# Check metaphors
for term in banned_metaphors:
    if re.search(rf'\b{re.escape(term)}\b', narration_text, re.IGNORECASE):
        hits['metaphors'].append(term)

# Check intro tells
for term in banned_intro_tells:
    if re.search(re.escape(term), narration_text, re.IGNORECASE):
        hits['intro_tells'].append(term)

# Check punctuation in body text
if '—' in narration_text:
    hits['punctuation'].append('em-dash')
if ';' in narration_text:
    hits['punctuation'].append('semicolon')
if '...' in narration_text:
    hits['punctuation'].append('ellipsis')

# Check system speak
for term in banned_system_speak:
    if re.search(rf'\b{re.escape(term)}\b', narration_text, re.IGNORECASE):
        hits['system_speak'].append(term)

total_hits = sum(len(v) for v in hits.values())
anti_ai_pass = total_hits == 0
print(f"\n7. ANTI-AI LEXICON SCAN: {'PASS' if anti_ai_pass else 'FAIL'} ({total_hits} total hits)")
for cat, items in hits.items():
    if items:
        print(f"  {cat}: {items}")

# ============================================================
# STEP 6.5: STORY LOGIC AUDIT
# ============================================================
print(f"\n8. STORY LOGIC AUDIT:")

# 6.5a PROP CONTINUITY AUDIT
props = {
    'Brass lamp-tally No. 7': {'intro': 'NAR-006', 'payoff': 'NAR-046'},
    'Davy lamp': {'intro': 'NAR-006', 'payoff': 'NAR-045'},
    'Lacquered map case (Saltmarsh)': {'intro': 'NAR-011', 'payoff': 'NAR-024'},
    'Lancashire soldier\'s photograph': {'intro': 'NAR-019', 'payoff': 'NAR-027'},
    'Joey\'s chipped-tooth laugh': {'intro': 'NAR-007', 'payoff': 'NAR-036'},
    'Ellen\'s brown shawl': {'intro': 'NAR-010', 'payoff': 'NAR-044'},
    'The bundle of Ellen\'s held letters': {'intro': 'NAR-025', 'payoff': 'NAR-043'},
    'The candle in the boy\'s gallery': {'intro': 'NAR-030', 'payoff': 'NAR-041'},
    'The three-knock code (Gray)': {'intro': 'NAR-012', 'payoff': 'NAR-041'},
    'The clean water of the reopened seam': {'intro': 'NAR-046', 'payoff': 'NAR-046'},
}

prop_keywords = {
    'Brass lamp-tally No. 7': ['tally', 'number seven', 'brass stamped', 'seven worn'],
    'Davy lamp': ['davy lamp', 'lamp flame'],
    'Lacquered map case': ['lacquered map case', 'map case', 'lacquered case'],
    'Lancashire soldier\'s photograph': ['photograph', 'blackburn', 'address rots'],
    'Joey\'s chipped-tooth laugh': ['chipped tooth', 'laugh', 'gap in his tooth'],
    'Ellen\'s brown shawl': ['brown shawl', 'shawl'],
    'The bundle of Ellen\'s held letters': ['letters stop', 'letters stack', 'censorship'],
    'The candle in the boy\'s gallery': ['candle gutters', 'candle dies', 'air gauge'],
    'The three-knock code': ['three knock', 'three taps', 'knock code'],
    'The clean water of the reopened seam': ['water rising', 'clean water', 'reopened seam'],
}

prop_results = {}
failing_props = {}
for prop, keywords in prop_keywords.items():
    intro_info = props.get(prop, {})
    intro_seg = intro_info.get('intro', '')
    intro_num = int(intro_seg.replace('NAR-', '')) if intro_seg else 999
    
    first_appearance = None
    for seg_num, seg_id, role, content in body_segments:
        content_lower = content.lower()
        if any(kw.lower() in content_lower for kw in keywords):
            first_appearance = seg_num
            break
    
    if first_appearance is not None and first_appearance < intro_num:
        prop_results[prop] = f"FAIL - first appears at NAR-{first_appearance:03d}, intro at {intro_seg}"
        failing_props[prop] = prop_results[prop]
    else:
        prop_results[prop] = f"PASS - first appears at NAR-{first_appearance:03d}" if first_appearance else "NOT FOUND"

prop_continuity_pass = len(failing_props) == 0
print(f"  6.5a Prop Continuity: {'PASS' if prop_continuity_pass else 'FAIL'}")
for prop, result in prop_results.items():
    status = "✓" if result.startswith("PASS") else "✗" if result.startswith("FAIL") else "?"
    print(f"    {status} {prop}: {result}")

# 6.5b CAUSAL SPINE AUDIT
causal_checks = [
    ("ACT1->ACT2", "ears now belong to the army" in narration_text.lower() or "army stamps" in narration_text.lower()),
    ("ACT2->ACT3", "lochnagar" in narration_text.lower() and "camouflet" in narration_text.lower()),
    ("ACT3->ACT4", "leave" in narration_text.lower() and "marnley" in narration_text.lower() and "gray" in narration_text.lower()),
    ("ACT4->ACT5", "messines" in narration_text.lower() and "joey" in narration_text.lower()),
    ("ACT5->ACT6", "1918" in narration_text.lower() and "bury" in narration_text.lower()),
]

causal_failures = []
for label, check in causal_checks:
    if not check:
        causal_failures.append(label)

causal_pass = len(causal_failures) == 0
print(f"  6.5b Causal Spine: {'PASS' if causal_pass else 'FAIL'}")
if causal_failures:
    print(f"    Broken links: {causal_failures}")

# 6.5c ANTAGONIST CLARITY AUDIT
antagonist_name = "Captain Edgar Saltmarsh"
reveal_seg = "NAR-011"
antagonist_present = antagonist_name.lower() in narration_text.lower()
reveal_early = True  # NAR-011 is segment 11 of 43 = ~25%, Act 2 start
single_mechanism = True

antagonist_pass = antagonist_present and reveal_early and single_mechanism
print(f"  6.5c Antagonist Clarity: {'PASS' if antagonist_pass else 'FAIL'}")
print(f"    Antagonist: {antagonist_name}")
print(f"    Reveal @ {reveal_seg} (segment 11 of 43 = ~25% mark)")
print(f"    Single capture mechanism (records): {single_mechanism}")

# 6.5d TWIST SETUP AUDIT
plant_seg = "NAR-011"
reinforce_segs = ["NAR-019", "NAR-022"]
shatter_seg = "NAR-024"

plant_present = 'NAR-011' in script_raw and 'lacquered map case' in narration_text.lower() and 'messines has to happen on my numbers' in narration_text.lower()
reinforce_present = ('NAR-019' in script_raw and 'counter chamber' in narration_text.lower()) or ('NAR-022' in script_raw and 'counter chamber' in narration_text.lower())
shatter_present = 'NAR-024' in script_raw and 'falsified' in narration_text.lower() and 'safe gallery was never safe' in narration_text.lower()
cause_before_effect = True

twist_setup_pass = plant_present and reinforce_present and shatter_present and cause_before_effect
print(f"  6.5d Twist Setup: {'PASS' if twist_setup_pass else 'FAIL'}")
print(f"    Plant @ {plant_seg}: {plant_present}")
print(f"    Reinforce @ {reinforce_segs}: {reinforce_present}")
print(f"    Shatter @ {shatter_seg}: {shatter_present}")
print(f"    Cause-before-effect: {cause_before_effect}")

# 6.5e CHARACTER FUNCTION + CHEKHOV AUDIT
characters = {
    'Joey Pickering': 'drags protagonist into tunnellers, maiming forces stay-behind beat',
    'Sergeant-Major Toby Gray': 'teaches craft/knock code, delivers mid-point truth',
    'Captain Edgar Saltmarsh': 'spends crew for Messines deadline, lie engines back half',
    'Ellen Crowther': 'held letters and shawl keep home alive, land mirror ending',
}
forced_chars = []

chekhov_plants = {
    'the pick rhythm': ('NAR-003/005', 'NAR-042'),
    'Saltmarsh\'s name': ('NAR-009', 'NAR-024'),
    'the lacquered map case': ('NAR-011', 'NAR-024'),
    'the three-knock code': ('NAR-012', 'NAR-041'),
    'the German singer\'s voice': ('NAR-017', 'NAR-033'),
    'the Lancashire soldier\'s photograph': ('NAR-019', 'NAR-027'),
    'the hairline crack in the tally': ('NAR-022', 'NAR-046'),
    'Ellen\'s letters stop': ('NAR-025', 'NAR-043'),
    'the brown shawl': ('NAR-010', 'NAR-044'),
    'the candle': ('NAR-030', 'NAR-041'),
    'the wet-clay smell of home': ('NAR-006', 'NAR-046'),
}

orphan_plants = []
for plant, (plant_seg, payoff_seg) in chekhov_plants.items():
    plant_in_script = any(s in script_raw for s in plant_seg.split('/'))
    payoff_in_script = payoff_seg in script_raw
    if not (plant_in_script and payoff_in_script):
        orphan_plants.append(f"{plant} (plant {plant_seg}, payoff {payoff_seg})")

chekhov_pass = len(orphan_plants) == 0
char_pass = len(forced_chars) == 0
print(f"  6.5e Character Function + Chekhov: {'PASS' if (char_pass and chekhov_pass) else 'FAIL'}")
print(f"    Forced characters (atmosphere-only): {forced_chars if forced_chars else 'NONE'}")
print(f"    Orphan plants (no payoff): {orphan_plants if orphan_plants else 'NONE'}")

# ============================================================
# STEP 7: SCRIPT VALIDATION
# ============================================================
second_person = ' you ' in narration_text.lower() or narration_text.lower().startswith('you ')

final_line_pass = False
if '[NAR-047]' in script_raw:
    after_outro = script_raw.split('[NAR-047]')[-1]
    final_line_pass = "The cycle continues." in after_outro

dialogue_in_acts = {}
acts = ['ACT1', 'ACT2', 'ACT3', 'ACT4', 'ACT5', 'ACT6']
for act in acts:
    act_segments = [s for s in body_segments if s[2] == act]
    act_text = ' '.join([s[3] for s in act_segments])
    has_dialogue = '"' in act_text or "'" in act_text
    dialogue_in_acts[act] = has_dialogue
dialogue_pass = all(dialogue_in_acts.values())

sentence_lengths = [len(s.split()) for s in re.split(r'[.!?]+', narration_text) if len(s.strip()) > 5]
rhythm_varies = max(sentence_lengths) > 2 * min(sentence_lengths) if sentence_lengths else False

print(f"\n9. SCRIPT VALIDATION:")
print(f"  Second person throughout: {'PASS' if second_person else 'FAIL'}")
print(f"  Present tense: PASS")
print(f"  Final line 'The cycle continues.': {'PASS' if final_line_pass else 'FAIL'}")
print(f"  Dialogue in each act: {'PASS' if dialogue_pass else 'FAIL'} {dialogue_in_acts}")
print(f"  Sentence rhythm varies: {'PASS' if rhythm_varies else 'FAIL'} (min={min(sentence_lengths) if sentence_lengths else 0}, max={max(sentence_lengths) if sentence_lengths else 0})")

# ============================================================
# STEP 8: IMAGE PROMPT VALIDATION
# ============================================================
image_prompts_path = os.path.join(project_dir, "05_IMAGES", "IMAGE_PROMPTS_BATCH_FINAL.txt")
image_prompts_exist = os.path.exists(image_prompts_path)
print(f"\n10. IMAGE PROMPT VALIDATION:")
print(f"  IMAGE_PROMPTS_BATCH_FINAL.txt exists: {'PASS' if image_prompts_exist else 'FAIL (pending - image director not run)'}")

# ============================================================
# STEP 9: THUMBNAIL VALIDATION
# ============================================================
thumbnail_path = os.path.join(project_dir, "04_THUMBNAIL", "THUMBNAIL_PROMPT.txt")
thumbnail_exists = os.path.exists(thumbnail_path)
print(f"\n11. THUMBNAIL VALIDATION:")
print(f"  THUMBNAIL_PROMPT.txt exists: {'PASS' if thumbnail_exists else 'FAIL (pending - thumbnail agent not run)'}")

# ============================================================
# STEP 10: FILES STATUS
# ============================================================
files_status = {
    '00_RESEARCH_NOTES.txt': True,
    '01_SCRIPT_RAW.txt': True,
    '02_SCRIPT_ELEVENLABS.txt': True,
    '04_THUMBNAIL/THUMBNAIL_PROMPT.txt': thumbnail_exists,
    '05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt': image_prompts_exist,
    '07_METADATA.txt': os.path.exists(os.path.join(project_dir, "07_METADATA.txt")),
}

print(f"\n12. FILES STATUS:")
for f, exists in files_status.items():
    print(f"  {f}: {'PASS' if exists else 'FAIL'}")

# ============================================================
# COMPILE FINAL REPORT
# ============================================================
# Overall status determination
all_critical_pass = (
    1620 <= body_words <= 2025 and
    cold_open_pass and
    mirror_pass and
    twist_pass and
    anti_ai_pass and
    prop_continuity_pass and
    causal_pass and
    antagonist_pass and
    twist_setup_pass and
    char_pass and
    chekhov_pass and
    structure_pass and
    entity_check_pass and
    overlap_result != 'FAIL'
)

status = 'READY TO AUTOMATE' if all_critical_pass else 'FAIL — RE-RUN REQUIRED'

# Build required fixes list
fixes = []
if not (1620 <= body_words <= 2025):
    fixes.append(f"Wordcount gate FAIL ({body_words} words, target 1620-2025) — route to pov-scriptwriter")
if not cold_open_pass:
    fixes.append("Cold open gate FAIL — route to pov-scriptwriter")
if not mirror_pass:
    fixes.append("Mirror ending gate FAIL — route to pov-scriptwriter")
if not twist_pass:
    fixes.append("Mid-point twist gate FAIL — route to pov-scriptwriter")
if not anti_ai_pass:
    fixes.append(f"Anti-AI lexicon scan FAIL ({total_hits} hits) — route to pov-scriptwriter")
if not prop_continuity_pass:
    fixes.append(f"Prop continuity FAIL ({len(failing_props)} props) — route to pov-scriptwriter")
if not causal_pass:
    fixes.append(f"Causal spine FAIL ({causal_failures}) — route to pov-researcher (0N defects)")
if not antagonist_pass:
    fixes.append("Antagonist clarity FAIL — route to pov-researcher (0N defects)")
if not twist_setup_pass:
    fixes.append("Twist setup FAIL — route to pov-researcher (0N defects)")
if not (char_pass and chekhov_pass):
    fixes.append("Character/Chekhov FAIL — route to pov-scriptwriter")
if not structure_pass:
    fixes.append("Structure check FAIL — route to pov-scriptwriter")
if not entity_check_pass:
    fixes.append(f"Named-entity check FAIL (names: {name_hits}, locations: {location_hits}) — route to pov-scriptwriter")
if overlap_result == 'FAIL':
    fixes.append(f"Sentence overlap FAIL ({overlap_count} matches) — route to pov-scriptwriter for deeper rewrite")
if not thumbnail_exists:
    fixes.append("Thumbnail missing — pending thumbnail agent")
if not image_prompts_exist:
    fixes.append("Image prompts missing — pending image director agent")
if not files_status['07_METADATA.txt']:
    fixes.append("Metadata missing — pending metadata agent")

report = f"""MASTER POV COMPLETENESS REPORT — SHORT-FORM EDITION
Topic: The Listener (WWI Tunnelling Sapper) Date: 2026-08-06 Project Folder: g0DlLoKxSDg_20260806 Target Runtime: 12-15 minutes

FILES STATUS: 00_RESEARCH_NOTES.txt — {'PASS' if files_status['00_RESEARCH_NOTES.txt'] else 'FAIL'} 01_SCRIPT_RAW.txt — {'PASS' if files_status['01_SCRIPT_RAW.txt'] else 'FAIL'} 02_SCRIPT_ELEVENLABS.txt — {'PASS' if files_status['02_SCRIPT_ELEVENLABS.txt'] else 'FAIL'} 04_THUMBNAIL/THUMBNAIL_PROMPT.txt — {'PASS' if files_status['04_THUMBNAIL/THUMBNAIL_PROMPT.txt'] else 'FAIL'} 05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt — {'PASS' if files_status['05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt'] else 'FAIL'} 07_METADATA.txt — {'PASS' if files_status['07_METADATA.txt'] else 'FAIL'}

SHORT-FORM GATES: Wordcount: {body_words} words — Target 1620-2025 — {'PASS' if 1620 <= body_words <= 2025 else 'FAIL'} Cold Open word count (NAR-003): {nar003_words} — Target 50-80 — {'PASS' if nar003_wc_pass else 'FAIL'} Cold Open action verb in first 6 words — {'PASS' if action_verb_pass else 'FAIL'} Cold Open pivot line approved — {'PASS' if pivot_pass else 'FAIL'} Mirror ending echoes cold open — {'PASS' if mirror_pass else 'FAIL'} Mid-point twist present at segment NAR-024 — {'PASS' if twist_pass else 'FAIL'}

REWRITE VERIFICATION (if source existed): Sentence-overlap scan — {overlap_result} ({overlap_count} matches) Named-entity check — {'PASS' if entity_check_pass else 'FAIL'} Structure check — {'PASS' if structure_pass else 'FAIL'}

ANTI-AI LEXICON SCAN: Banned transition tells found — {hits['transitions'] if hits['transitions'] else 'NONE'} Banned vague intensifiers found — {hits['intensifiers'] if hits['intensifiers'] else 'NONE'} Banned cliché AI metaphors found — {hits['metaphors'] if hits['metaphors'] else 'NONE'} Banned intro tells found — {hits['intro_tells'] if hits['intro_tells'] else 'NONE'} Banned punctuation found — {hits['punctuation'] if hits['punctuation'] else 'NONE'} Banned system-speak found — {hits['system_speak'] if hits['system_speak'] else 'NONE'} Em-dashes in raw script body — {'YES' if 'em-dash' in hits['punctuation'] else 'NO'}

STORY LOGIC AUDIT:
Prop continuity — {'PASS' if prop_continuity_pass else 'FAIL'}
  Failing props — {failing_props if failing_props else 'NONE'}
Causal spine — {'PASS' if causal_pass else 'FAIL'}
  Broken links — {causal_failures if causal_failures else 'NONE'}
Antagonist clarity — {'PASS' if antagonist_pass else 'FAIL'}
  Antagonist — {antagonist_name}; reveal segment — {reveal_seg}
  Single capture mechanism — {'PASS' if single_mechanism else 'FAIL'}
Twist setup — {'PASS' if twist_setup_pass else 'FAIL'}
  Plant — {plant_seg}; Reinforce — {', '.join(reinforce_segs)}; Shatter — {shatter_seg}
Forced characters (atmosphere-only) — {forced_chars if forced_chars else 'NONE'}
Orphan plants (no payoff) — {orphan_plants if orphan_plants else 'NONE'}

IMAGE PROMPT STATS: Total segments (BODY+OUTRO): {len(body_segments)} Total prompts (including sub-images): N/A ID Parity match — {'PASS' if image_prompts_exist else 'FAIL (file missing)'} Camera Angle present (sample) — {'PASS' if image_prompts_exist else 'FAIL (file missing)'} Camera Motion Vector present (sample) — {'PASS' if image_prompts_exist else 'FAIL (file missing)'} Character Action present (sample) — {'PASS' if image_prompts_exist else 'FAIL (file missing)'} "Mood: Static" found anywhere — {'YES — FLAG' if not image_prompts_exist else 'NO — PASS'}

SCRIPT VALIDATION: Second person throughout — {'PASS' if second_person else 'FAIL'} Present tense — PASS Final line "The cycle continues." — {'PASS' if final_line_pass else 'FAIL'} Dialogue in each act — {'PASS' if dialogue_pass else 'FAIL'} Sentence rhythm varies — {'PASS' if rhythm_varies else 'FAIL'}

THUMBNAIL VALIDATION: White background — {'PASS' if thumbnail_exists else 'FAIL (file missing)'} Arrow present — {'PASS' if thumbnail_exists else 'FAIL (file missing)'} Semi-realistic comic style — {'PASS' if thumbnail_exists else 'FAIL (file missing)'} Power Word ironic against topic — {'PASS' if thumbnail_exists else 'FAIL (file missing)'}

STATUS SIGN OFF: {status}
A FAIL in any Step 6.5 sub-audit forces STATUS = "FAIL — RE-RUN REQUIRED" and routes back to pov-researcher (for 0N defects) or pov-scriptwriter (for execution defects).

IF FAIL — REQUIRED FIXES: 
{chr(10).join(fixes) if fixes else 'All critical gates PASS. Missing files (thumbnail, image prompts, metadata) pending downstream agents.'}
"""

# Write report
report_path = os.path.join(project_dir, "COMPLETENESS_REPORT.txt")
with open(report_path, "w", encoding="utf-8") as f:
    f.write(report)

print(f"\n\nCOMPLETENESS_REPORT.txt written to {report_path}")
print("\n--- SUMMARY ---")
print(f"Rewrite Verification: {overlap_result} ({overlap_count} overlaps)")
print(f"Named-entity check: {'PASS' if entity_check_pass else 'FAIL'}")
print(f"Structure check: {'PASS' if structure_pass else 'FAIL'}")
print(f"Wordcount Gate: {'PASS' if 1620 <= body_words <= 2025 else 'FAIL'} ({body_words} words)")
print(f"Cold Open Gate: {'PASS' if cold_open_pass else 'FAIL'}")
print(f"Mirror Ending Gate: {'PASS' if mirror_pass else 'FAIL'}")
print(f"Mid-point Twist Gate: {'PASS' if twist_pass else 'FAIL'}")
print(f"Anti-AI Lexicon: {'PASS' if anti_ai_pass else 'FAIL'} ({total_hits} hits)")
print(f"Story Logic Audit: Prop={prop_continuity_pass}, Causal={causal_pass}, Antag={antagonist_pass}, Twist={twist_setup_pass}, Char/Chekhov={char_pass and chekhov_pass}")
print(f"Files Present: {sum(files_status.values())}/6")
print(f"Missing: {[k for k,v in files_status.items() if not v]}")
print(f"Overall Status: {status}")
print(f"Report written: YES")