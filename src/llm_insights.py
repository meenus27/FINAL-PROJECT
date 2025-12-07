import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    genai = None

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
CACHE_PATH = Path("data/cached_advisories.json")
LOCAL_CACHE = {}
GEMINI_DISABLED = False

if CACHE_PATH.exists():
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            LOCAL_CACHE = json.load(f)
    except Exception:
        LOCAL_CACHE = {}

def generate_advisory(severity, drivers, role="Authority"):
    global GEMINI_DISABLED
    prompt = f"Provide a concise advisory for severity={severity}. Drivers: {', '.join(drivers)}. Role: {role}."
    if GEMINI_DISABLED:
        return LOCAL_CACHE.get(severity, f"[Cached Advisory] {severity}: follow local instructions.")

    try:
        if not GEMINI_AVAILABLE:
            print("Warning: google-generativeai package not installed. Install with: pip install google-generativeai")
            return LOCAL_CACHE.get(severity, f"[Mock Advisory] Severity: {severity}. Drivers: {', '.join(drivers)}")
        
        if not GEMINI_KEY:
            print("Warning: GEMINI_API_KEY not set. Set it in .env file or environment variable.")
            return LOCAL_CACHE.get(severity, f"[Mock Advisory] Severity: {severity}. Drivers: {', '.join(drivers)}")

        # Configure Gemini API
        genai.configure(api_key=GEMINI_KEY)
        
        # Use ONLY Gemini 2.0 Flash
        model_name = None
        # Try both formats: with and without models/ prefix
        gemini_2_flash_models = ['models/gemini-2.5-flash-lite', 'gemini-2.5-flash-lite']
        
        for model_candidate in gemini_2_flash_models:
            try:
                model = genai.GenerativeModel(model_candidate)
                model_name = model_candidate
                print(f"✓ Using Gemini 2.5 Flash Lite: {model_name}")
                break
            except Exception as e:
                continue
        
        if model_name is None:
            raise ValueError("Gemini 2.5 Flash Lite model not available. Please ensure you have access to gemini-2.5-flash-lite model.")
        
        # Create a more neutral, clinical prompt that's less likely to trigger safety filters
        # Focus on public safety information rather than disaster scenarios
        neutral_prompt = f"""Generate a brief public safety advisory message.
Risk level: {severity}
Factors: {', '.join(drivers)}
Audience: {role}

Provide 2-3 sentences of clear, factual safety guidance. Use neutral, professional language focused on preparedness and response protocols."""
        
        # Configure safety settings to be less restrictive for public safety content
        try:
            # Use proper enum values if available
            from google.generativeai.types import HarmCategory, HarmBlockThreshold
            safety_settings = {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,  # Most permissive for safety info
            }
        except (ImportError, AttributeError):
            # Fallback to string format
            safety_settings = [
                {
                    "category": "HARM_CATEGORY_HARASSMENT",
                    "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                },
                {
                    "category": "HARM_CATEGORY_HATE_SPEECH",
                    "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                },
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_ONLY_HIGH"  # Most permissive for safety content
                }
            ]
        
        # Try with neutral prompt and adjusted safety settings
        try:
            response = model.generate_content(
                neutral_prompt,
                generation_config={
                    "max_output_tokens": 150,
                    "temperature": 0.7,
                },
                safety_settings=safety_settings
            )
        except Exception as e1:
            # Fallback 1: Try with safety settings only
            try:
                response = model.generate_content(
                    neutral_prompt,
                    safety_settings=safety_settings
                )
            except Exception as e2:
                # Fallback 2: Try with even more neutral prompt
                try:
                    simple_prompt = f"Public safety advisory: {severity} risk level due to {', '.join(drivers[:2])}. Provide brief safety guidance."
                    response = model.generate_content(
                        simple_prompt,
                        safety_settings=safety_settings
                    )
                except Exception as e3:
                    # Final fallback: Simple generation without safety settings
                    try:
                        response = model.generate_content(simple_prompt)
                    except Exception:
                        # Last resort: very simple prompt
                        response = model.generate_content(f"Safety advisory for {severity} conditions.")
        
        if not response:
            raise ValueError("Empty response from Gemini API")
        
        # Check for blocked content (finish_reason 2 = SAFETY)
        if hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, 'finish_reason'):
                finish_reason = candidate.finish_reason
                # finish_reason 2 = SAFETY (blocked by safety filters)
                # finish_reason values: 0=STOP, 1=MAX_TOKENS, 2=SAFETY, 3=RECITATION, 4=OTHER
                if finish_reason == 2 or (isinstance(finish_reason, int) and finish_reason == 2):
                    # Try one more time with an even simpler prompt
                    try:
                        # Only show retry message in verbose mode (can be controlled via env var)
                        verbose = os.getenv("GEMINI_VERBOSE", "false").lower() == "true"
                        if verbose:
                            print("⚠️ Content blocked, retrying with simpler prompt...")
                        simple_retry = f"Brief safety message: {severity} risk. Factors: {', '.join(drivers[:2]) if len(drivers) >= 2 else drivers[0] if drivers else 'weather conditions'}. Provide guidance."
                        retry_response = model.generate_content(simple_retry)
                        
                        if hasattr(retry_response, 'candidates') and retry_response.candidates:
                            retry_candidate = retry_response.candidates[0]
                            if hasattr(retry_candidate, 'finish_reason') and retry_candidate.finish_reason != 2:
                                # Retry succeeded!
                                if hasattr(retry_response, 'text'):
                                    text = retry_response.text.strip()
                                    if text:
                                        if verbose:
                                            print("✓ Retry successful!")
                                        LOCAL_CACHE[severity] = text
                                        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                                        with open(CACHE_PATH, "w", encoding="utf-8") as f:
                                            json.dump(LOCAL_CACHE, f, indent=2, ensure_ascii=False)
                                        return text
                    except Exception:
                        pass
                    
                    # If retry failed, use fallback
                    print("⚠️ Content blocked by safety filters. Using fallback advisory.")
                    fallback_msg = f"{severity} risk level detected. Factors: {', '.join(drivers)}. Follow local emergency protocols and official instructions."
                    return LOCAL_CACHE.get(severity, fallback_msg)
                elif finish_reason == 3:  # RECITATION (repetitive content)
                    print("⚠️ Content flagged as recitation. Using fallback advisory.")
                    return LOCAL_CACHE.get(severity, f"[Advisory] {severity} risk level. Drivers: {', '.join(drivers)}. Follow local emergency instructions.")
        
        # Try to get text from response
        try:
            if hasattr(response, 'text'):
                text = response.text.strip()
            elif hasattr(response, 'candidates') and response.candidates:
                # Try to extract text from parts
                candidate = response.candidates[0]
                if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                    text_parts = []
                    for part in candidate.content.parts:
                        if hasattr(part, 'text'):
                            text_parts.append(part.text)
                    text = " ".join(text_parts).strip()
                else:
                    raise ValueError("No text content in response")
            else:
                raise ValueError("Cannot extract text from response")
        except Exception as extract_error:
            print(f"⚠️ Could not extract text: {extract_error}")
            return LOCAL_CACHE.get(severity, f"[Advisory] {severity} risk level. Drivers: {', '.join(drivers)}. Follow local emergency instructions.")
        
        if not text:
            raise ValueError("Empty text in response")

        LOCAL_CACHE[severity] = text
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(LOCAL_CACHE, f, indent=2, ensure_ascii=False)

        print(f"✓ Gemini API success using {model_name}")
        return text

    except Exception as e:
        error_msg = str(e)
        msg = error_msg.lower()
        
        # Better error reporting
        if "api_key" in msg or "invalid" in msg or "401" in error_msg or "403" in error_msg:
            print(f"❌ Gemini API Key Error: {error_msg}")
            print("   → Check your GEMINI_API_KEY in .env file or environment variables")
            print("   → Get API key from: https://aistudio.google.com/app/apikey")
        elif "finish_reason" in msg or "safety" in msg or "blocked" in msg:
            print(f"⚠️ Content blocked by safety filters: {error_msg}")
            print("   → This is normal - disaster content may trigger safety filters")
            print("   → Using fallback advisory instead")
            # Don't disable - this is expected behavior
        elif "quota" in msg or "insufficient_quota" in msg or "429" in error_msg:
            GEMINI_DISABLED = True
            print(f"⚠️ Gemini API Quota Exceeded: {error_msg}")
        else:
            print(f"❌ Gemini API Error: {error_msg}")
            print(f"   Error type: {type(e).__name__}")
        
        return LOCAL_CACHE.get(severity, f"[Mock Advisory] Severity: {severity}. Drivers: {', '.join(drivers)}")


