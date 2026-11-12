#!/usr/bin/env python3
"""Upload and verify RGR variant 3 simulated weather data in Firebase RTDB.

Requires a private service-account JSON file stored locally (never share it).
Example Windows PowerShell, from extracted RGR project folder:
  python -m pip install firebase-admin
  python firebase_admin_upload_rgr3.py --key "C:\\private\\serviceAccountKey.json"
"""
import argparse
import hashlib
import json
from pathlib import Path

DATABASE_URL = 'https://rgr-iot-variant3-default-rtdb.europe-west1.firebasedatabase.app'
NODE = 'rgr_variant3_weather_2025'

def main():
    parser = argparse.ArgumentParser(description='Upload local synthetic weather data to Firebase RTDB')
    parser.add_argument('--key', required=True, help='Local path to private Firebase service account JSON')
    parser.add_argument('--payload', default='rgr3_assets/payload.json', help='Local payload from variant3_iot.py')
    args = parser.parse_args()
    import firebase_admin
    from firebase_admin import credentials, db
    key_path = Path(args.key)
    data_path = Path(args.payload)
    if not key_path.is_file():
        parser.error('Private key file not found: ' + str(key_path))
    if not data_path.is_file():
        parser.error('Payload file not found. Run python variant3_iot.py first.')
    payload_bytes = data_path.read_bytes()
    payload = json.loads(payload_bytes)
    if payload.get('metadata', {}).get('variant') != 3 or len(payload.get('records', [])) != 365:
        parser.error('Unexpected payload: expected variant 3 and 365 daily records')
    app = firebase_admin.initialize_app(credentials.Certificate(str(key_path)), {'databaseURL': DATABASE_URL})
    try:
        reference = db.reference(NODE, app=app)
        reference.set(payload)
        check = reference.get()
        if check != payload:
            raise RuntimeError('Round-trip mismatch: uploaded and downloaded values differ')
        print('FIREBASE UPLOAD OK')
        print('FIREBASE DOWNLOAD VERIFY OK')
        print('Firebase path:', NODE)
        print('Days:', len(check['records']))
        print('Measurements:', len(check['records']) * 4)
        print('Local payload SHA256:', hashlib.sha256(payload_bytes).hexdigest())
        results = data_path.parent / 'firebase_verified.json'
        results.write_text(json.dumps({'firebase_roundtrip_ok':True, 'database_url': DATABASE_URL,
                                       'node': NODE, 'days':365, 'measurements':1460},
                                      indent=2,ensure_ascii=False),encoding='utf-8')
        print('Verification file:', results)
    finally:
        firebase_admin.delete_app(app)

if __name__ == '__main__':
    main()
