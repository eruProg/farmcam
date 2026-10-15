import json
import re

log_path = r"C:\Users\ITAdmin\.gemini\antigravity\brain\fda94cee-1c08-4df9-a5f9-d5763d6930d0\.system_generated\logs\overview.txt"
out_path = r"C:\Users\ITAdmin\.gemini\antigravity\scratch\farmcam\rpi_server\log_summary.txt"

try:
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Search for model_classes or class mappings
    matches = re.findall(r"(\"model_classes\".*?\])", content)
    with open(out_path, "w") as out:
        out.write(f"Matches count: {len(matches)}\n")
        for m in matches[:10]:
            out.write(m + "\n\n")
        
        # Let's search for the original main.py class names or best.pt names
        for line in content.splitlines():
            if "class" in line.lower() or "names" in line.lower():
                if len(line) < 200:
                    out.write(line + "\n")
except Exception as e:
    with open(out_path, "w") as out:
        out.write(f"Error: {e}")
