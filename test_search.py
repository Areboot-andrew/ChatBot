import httpx
import urllib.parse
import re

data = urllib.parse.urlencode({'q': 'Biostar A78MD motherboard FX-6300 CPU compatibility'})
resp = httpx.post('https://html.duckduckgo.com/html/', content=data, headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
snippets = [re.sub(r'<[^>]+>', '', s).strip() for s in re.findall(r'class="result__snippet[^"]*"[^>]*>(.*?)</a>', resp.text, re.IGNORECASE | re.DOTALL)]
print(snippets[:3])
