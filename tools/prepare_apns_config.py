"""Encrypt APNs configuration to the operator's pinned transfer certificate.

Private key material is only passed through a pipe; artifacts contain CMS ciphertext.
The recipient private key is held on the server, never in GitHub.
"""
import json
import os
from pathlib import Path
import re
import subprocess

key_id = os.environ.get('APNS_KEY_ID', '')
private_key = os.environ.get('APNS_PRIVATE_KEY', '')
if not re.fullmatch(r'[A-Z0-9]{10}', key_id) or '-----BEGIN PRIVATE KEY-----' not in private_key:
    raise SystemExit('Add APNS_KEY_ID and APNS_PRIVATE_KEY to Actions secrets first.')
subprocess.run(['openssl', 'pkey', '-check', '-noout'], input=private_key.encode(), capture_output=True, check=True)
Path('build').mkdir(exist_ok=True)
payload = json.dumps({'team_id':'VW3VH2HPTV', 'key_id':key_id, 'environment':'production', 'private_key':private_key}).encode()
subprocess.run(['openssl', 'cms', '-encrypt', '-binary', '-aes-256-cbc', '-outform', 'DER', '-out', 'build/apns-config.cms', 'tools/apns-transfer-recipient.crt'],
               input=payload, capture_output=True, check=True)
print('Encrypted APNs configuration prepared. No key material was logged.')
