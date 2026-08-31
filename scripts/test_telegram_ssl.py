import urllib.request, json, os

# Set SSL cert file
os.environ['SSL_CERT_FILE'] = r'C:\milo-portable-system\artisan\youtube-shorts-pipeline\venv\Lib\site-packages\certifi\cacert.pem'

token = '8844481759:AAExAkAIOl_m_JBQ3_RxTf9tM7Afn32Y3nM'
chat = '8101147332'
msg = 'Test with SSL_CERT_FILE set'
url = f'https://api.telegram.org/bot{token}/sendMessage'
data = json.dumps({'chat_id': chat, 'text': msg, 'disable_web_page_preview': True}).encode()
req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req)
print(resp.read().decode())