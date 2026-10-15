import os

try:
    utf16_path = r"c:\Users\ITAdmin\.gemini\antigravity\scratch\names.txt"
    if os.path.exists(utf16_path):
        # Using 'utf-16' automatically handles BOM if present!
        with open(utf16_path, "r", encoding="utf-16") as f:
            content = f.read()
        
        # Strip any leading BOM just in case, and strip whitespace
        content_clean = content.strip().lstrip('\ufeff')
        
        with open(r"c:\Users\ITAdmin\.gemini\antigravity\scratch\farmcam\rpi_server\names_utf8.txt", "w", encoding="utf-8") as f:
            f.write(content_clean)
        
        print("SUCCESS_CLEAN_CONTENT:")
        print(repr(content_clean))
    else:
        print("ERROR: names.txt not found")
except Exception as e:
    print("CRITICAL_ERROR:", e)
