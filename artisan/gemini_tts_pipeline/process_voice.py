import re

def process_script(input_path, output_path):
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Split by level headers
    parts = re.split(r'(LEVEL \d+: [^\n]+)', content)
    
    # parts[0] might be empty
    levels_data = []
    for i in range(1, len(parts), 2):
        levels_data.append((parts[i].strip(), parts[i+1].strip()))
    
    formatted_output = []
    
    candidates = ["ASSET", "INVISIBLE", "TRAPPED", "SILENT", "RISK", "TERMINAL", "FIXED", "PERMANENT", 
                  "COMPLIANCE", "LEVERAGE", "RESTRICTED", "QUALIFYING", "AUDITED", 
                  "PARTNER", "PRINCIPAL", "MANAGED", "GENERAL", "LIMITED", "SOVEREIGN", "ZERO", "PROTOCOL", "SYSTEM",
                  "VIBRATION", "INFRASTRUCTURE", "PRESSURE", "METRONOME", "STACCATO", "DISCORD", "HAMMER", "VIRTUOSO", "HANDLER", "REQUIEM", "MAESTRO"]

    for i, (header, body) in enumerate(levels_data):
        level_num = i + 1
        level_lines = [f"{header.upper()} [pause]"]
        
        # Split body into paragraphs
        paragraphs = [p.strip() for p in body.split('\n\n') if p.strip()]
        
        # In Level 10, the last few paragraphs are the epilogue.
        # Let's separate the epilogue from Level 10's body.
        if level_num == 10:
            # Epilogue starts after the gut-punch of level 10.
            # Raw Level 10 has:
            # P1: Air smells...
            # P2: Everything...
            # P3: Made it...
            # P4: Real number... (Gut punch here?)
            # Wait, raw Level 10:
            # ...
            # You welcome the void of the daily wipe.
            # You are worth more on paper than you will ever feel in real life. (Gut punch)
            # A young man stands... (Epilogue)
            # ...
            # The cycle continues. (Final line)
            
            gut_punch_idx = -1
            for idx, p in enumerate(paragraphs):
                if "worth more on paper" in p:
                    gut_punch_idx = idx
                    break
            
            level_body_paragraphs = paragraphs[:gut_punch_idx]
            gut_punch = paragraphs[gut_punch_idx]
            epilogue_paragraphs = paragraphs[gut_punch_idx+1:]
        else:
            level_body_paragraphs = paragraphs[:-1]
            gut_punch = paragraphs[-1]
            epilogue_paragraphs = []

        sentence_count = 0
        hook_reset_placed = False
        
        chunk_size = 4 if level_num <= 5 else 5
        
        for p in level_body_paragraphs:
            p_sentences = re.split(r'(?<=[.!?])\s+', p)
            
            # Antithesis check: short paragraph (1-2 sentences, total length < 100 chars)
            if len(p_sentences) <= 2 and len(p) < 100:
                level_lines.append(f"{p} [pause]")
                sentence_count += len(p_sentences)
                continue
            
            # Normal chunking
            for j in range(0, len(p_sentences), chunk_size):
                chunk = p_sentences[j:j+chunk_size]
                
                # Check for "cold calculation" rule: 
                # If this is the last chunk of a paragraph and contains numbers/credits/finality
                is_cold_calc = False
                if j + chunk_size >= len(p_sentences):
                    if any(word in p.upper() for word in ["CREDITS", "NUMBER", "DEBT", "VALUE", "TOTAL"]):
                        is_cold_calc = True
                
                # Format chunk sentences
                chunk_sentences = []
                for s_idx, s in enumerate(chunk):
                    sentence_count += 1
                    
                    # Capitalization micro-emphasis (1-2 words per chunk)
                    # We'll do it once per chunk for simplicity
                    
                    # Pause Hook Reset between 10-15
                    s_suffix = ""
                    if 10 <= sentence_count <= 15 and not hook_reset_placed:
                        s_suffix = " [pause] [pause]"
                        hook_reset_placed = True
                    
                    # Cold calculation pause before final sentence of paragraph
                    if is_cold_calc and s_idx == len(chunk) - 1:
                        chunk_sentences.append(f"[pause] {s}{s_suffix}")
                    else:
                        chunk_sentences.append(f"{s}{s_suffix}")
                
                chunk_text = " ".join(chunk_sentences)
                
                # Capitalization
                words = chunk_text.split()
                found_count = 0
                new_words = []
                for w in words:
                    clean_w = w.strip('.,!?;:()""[]').upper()
                    if clean_w in candidates and found_count < 2 and not "[" in w:
                        new_words.append(w.upper())
                        found_count += 1
                    else:
                        new_words.append(w)
                
                if found_count == 0:
                    # Pick a long word
                    long_words = [w for w in words if len(w) > 6 and "[" not in w]
                    if long_words:
                        longest = sorted(long_words, key=len, reverse=True)[0]
                        chunk_text = " ".join(new_words).replace(longest, longest.upper(), 1)
                    else:
                        chunk_text = " ".join(new_words)
                else:
                    chunk_text = " ".join(new_words)
                
                level_lines.append(chunk_text)

        # Gut Punch
        level_lines.append(f"--{gut_punch}")
        
        formatted_output.append("\n\n".join(level_lines))
        
        # Process Epilogue if exists
        if epilogue_paragraphs:
            epilogue_lines = []
            sentence_count = 0
            # Remove "The cycle continues." if it's the last paragraph
            if epilogue_paragraphs and "The cycle continues." in epilogue_paragraphs[-1]:
                epilogue_paragraphs = epilogue_paragraphs[:-1]
            
            for p in epilogue_paragraphs:
                p_sentences = re.split(r'(?<=[.!?])\s+', p)
                for j in range(0, len(p_sentences), 5):
                    chunk = p_sentences[j:j+5]
                    chunk_text = " ".join(chunk)
                    
                    # Micro emphasis
                    words = chunk_text.split()
                    found_count = 0
                    new_words = []
                    for w in words:
                        clean_w = w.strip('.,!?;:()""').upper()
                        if clean_w in candidates and found_count < 1:
                            new_words.append(w.upper())
                            found_count += 1
                        else:
                            new_words.append(w)
                    chunk_text = " ".join(new_words)
                    epilogue_lines.append(chunk_text)
            
            formatted_output.append("\n\n".join(epilogue_lines))

    final_text = "\n\n".join(formatted_output)
    # Ensure no double periods or triple spaces
    final_text = re.sub(r'\s+', ' ', final_text)
    final_text = final_text.replace(' [pause]', ' [pause]').replace('[pause] ', '[pause] ')
    # Re-insert double newlines for readability in script
    final_text = final_text.replace(' [pause]', ' [pause]\n\n').replace('--', '\n\n--')
    # Wait, the prompt says "pure text for the TTS engine". Double newlines are okay.
    
    # Final check on final line
    if final_text.endswith('.'):
        final_text += "\n\n[pause] The cycle continues."
    else:
        final_text += "\n\n[pause] The cycle continues."
        
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(final_text)

process_script('C:/Users/DRA INVESTMENTS/Desktop/YOUTUBE/CartelHitman_MasterPOV_20260522/01_SCRIPT_RAW.txt', 
               'C:/Users/DRA INVESTMENTS/Desktop/YOUTUBE/CartelHitman_MasterPOV_20260522/02_SCRIPT_ELEVENLABS.txt')
