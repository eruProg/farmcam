import os

log_path = r"C:\Users\ITAdmin\.gemini\antigravity\brain\fda94cee-1c08-4df9-a5f9-d5763d6930d0\.system_generated\logs\overview.txt"
out_path = r"C:\Users\ITAdmin\.gemini\antigravity\scratch\farmcam\rpi_server\model_metrics.txt"

try:
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    
    lines = content.splitlines()
    matches = []
    for idx, l in enumerate(lines):
        if "Min:" in l or "Max:" in l or "Mean:" in l or "Range:" in l or "Max Conf:" in l:
            # Grab surrounding context
            start = max(0, idx - 5)
            end = min(len(lines), idx + 6)
            matches.append(f"--- MATCH AT LINE {idx} ---")
            matches.extend(lines[start:end])
            matches.append("\n")
            
    with open(out_path, "w") as out:
        out.write("\n".join(matches))
        print("Done! Found", len(matches), "matches")
except Exception as e:
    with open(out_path, "w") as out:
        out.write(f"Error: {e}")
