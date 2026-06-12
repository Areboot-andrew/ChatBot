import json
import sys

log_path = r"C:\Users\user\.gemini\antigravity\brain\0f21a607-9d54-4672-8293-8e06c4105335\.system_generated\logs\transcript.jsonl"
try:
    with open(log_path, 'r', encoding='utf-8') as f, open('user_msgs.txt', 'w', encoding='utf-8') as out:
        for line in f:
            data = json.loads(line)
            if data.get('type') == 'USER_INPUT' or data.get('source') == 'USER_EXPLICIT':
                out.write(f"[{data.get('step_index')}] {data.get('content', '')[:300]}\n")
except Exception as e:
    print(e)
