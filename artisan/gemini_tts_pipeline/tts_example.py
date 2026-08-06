import os
from google import genai
from google.genai import types
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("Error: GEMINI_API_KEY not found in .env file.")
    exit(1)

# Initialize Client
client = genai.Client(api_key=api_key)

def generate_speech(text, output_file="output.wav", voice="Aoede"):
    """
    Generates speech using Gemini 2.0 Flash Lite.
    """
    print(f"Generating audio for: '{text}' using voice '{voice}'...")

    try:
        # Using gemini-2.0-flash-lite which sometimes has different modality support in previews
        response = client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice
                        )
                    )
                )
            )
        )

        # Extract audio bytes from the response
        audio_found = False
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    audio_bytes = part.inline_data.data
                    with open(output_file, "wb") as f:
                        f.write(audio_bytes)
                    print(f"Successfully saved audio to {output_file}")
                    audio_found = True
                    break
        
        if not audio_found:
            print("Notice: No audio data found in the response.")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    text_to_read = "This is a test with Gemini 2.0 Flash Lite."
    output_path = Path("C:/Users/DRA INVESTMENTS/Desktop/Gemini-TTS-Project/output.wav")
    generate_speech(text_to_read, output_path, voice="Aoede")
