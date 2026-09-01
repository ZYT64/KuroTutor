import json
import urllib.request

cfg = json.load(open("/app/kuro.json"))
m = cfg["models"]["llm"]
data = json.dumps({
    "model": m["model"],
    "messages": [{"role": "user", "content": "say ok"}],
    "max_tokens": 10,
}).encode()
req = urllib.request.Request(
    m["base_url"] + "/chat/completions",
    data=data,
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + m["api_key"],
    },
)
try:
    r = urllib.request.urlopen(req, timeout=30)
    body = r.read().decode()
    print("STATUS:", r.status)
    print("BODY:", body[:200])
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
