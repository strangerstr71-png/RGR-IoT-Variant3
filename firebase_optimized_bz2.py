#!/usr/bin/env python3
"""Verify and upload a bzip2-compressed IoT dataset to Firebase Realtime Database.

Commands:
  python firebase_optimized_bz2.py self-test --input payload.json.bz2
  python firebase_optimized_bz2.py upload --input payload.json.bz2 \
     --key /secure/path/service-account.json \
     --database-url https://PROJECT-default-rtdb.REGION.firebasedatabase.app

WARNING: never commit the Firebase service-account JSON. This script does not
need access to secrets for self-test and does not put any secrets into logs.
"""
import argparse
import base64
import bz2
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_NODE = 'rgr_variant3_weather_2025_optimized'
EXPECTED_SHA256='c4337a2a412b72d31c044963e9e445e855e5daf9c557df52bd9f4453e37808f5'

def parse_dataset(raw:bytes):
    obj=json.loads(raw.decode('utf-8'))
    if not isinstance(obj,dict) or not isinstance(obj.get('records'),list):
        raise ValueError('Expected JSON object with a records array')
    records=obj['records']
    if len(records)!=365:
        raise ValueError(f'Expected 365 weather days; got {len(records)}')
    for i,r in enumerate(records):
        if not isinstance(r,dict) or not all(k in r for k in ('date','wind_speed','temperature','precipitation','solar_radiation')):
            raise ValueError(f'Invalid record structure at index {i}')
    meta=obj.get('metadata',{})
    if meta.get('n_days') not in (None,len(records)):
        raise ValueError('metadata.n_days mismatch')
    if meta.get('n_measurements') not in (None,len(records)*4):
        raise ValueError('metadata.n_measurements mismatch')
    return obj

def envelope_from_bz2(data:bytes):
    raw=bz2.decompress(data)
    parsed=parse_dataset(raw)
    digest=hashlib.sha256(raw).hexdigest()
    return {
        'format':'bzip2+base64',
        'algorithm':'bzip2',
        'encoding':'base64',
        'content_type':'application/json; charset=utf-8',
        'sha256_uncompressed':digest,
        'uncompressed_bytes':len(raw),
        'compressed_bytes':len(data),
        'days':len(parsed['records']),
        'measurements':len(parsed['records'])*4,
        'payload_b64':base64.b64encode(data).decode('ascii'),
    }

def verify_envelope(payload,expected_sha=None):
    if payload.get('algorithm')!='bzip2' or payload.get('encoding')!='base64':
        raise ValueError('Unexpected compression or encoding')
    packed=base64.b64decode(payload['payload_b64'], validate=True)
    raw=bz2.decompress(packed)
    obj=parse_dataset(raw)
    sha=hashlib.sha256(raw).hexdigest()
    if sha!=payload['sha256_uncompressed'] or (expected_sha and sha!=expected_sha):
        raise AssertionError('SHA256 mismatch')
    if len(raw)!=payload['uncompressed_bytes'] or len(packed)!=payload['compressed_bytes']:
        raise AssertionError('Byte count mismatch')
    if len(obj['records'])!=payload['days'] or len(obj['records'])*4!=payload['measurements']:
        raise AssertionError('Measurement count mismatch')
    return obj

def load_verified(path,expected_sha):
    packed=Path(path).read_bytes()
    env=envelope_from_bz2(packed)
    verify_envelope(env,expected_sha)
    return env

def local_report(env):
    return {'status':'LOCAL ROUNDTRIP OK','days':env['days'],
            'measurements':env['measurements'], 'raw_bytes':env['uncompressed_bytes'],
            'bz2_bytes':env['compressed_bytes'],
            'sha256':env['sha256_uncompressed']}

def run():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    sub=parser.add_subparsers(dest='command',required=True)
    for cmd in ('self-test','upload'):
        p=sub.add_parser(cmd)
        p.add_argument('--input',default='payload.json.bz2')
        p.add_argument('--expected-sha256',default=EXPECTED_SHA256)
        if cmd=='upload':
            p.add_argument('--key',required=True,help='Local private Firebase service account JSON')
            p.add_argument('--database-url',required=True)
            p.add_argument('--node',default=DEFAULT_NODE)
            p.add_argument('--proof',default='firebase_bz2_verified.json')
    args=parser.parse_args()
    env=load_verified(args.input,args.expected_sha256)
    report=local_report(env)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if args.command=='self-test':return
    import firebase_admin
    from firebase_admin import credentials,db
    app=firebase_admin.initialize_app(credentials.Certificate(args.key), {'databaseURL':args.database_url})
    try:
        ref=db.reference('/'+args.node.strip('/'),app=app)
        ref.set(env)
        print('FIREBASE BZIP2 UPLOAD OK')
        remote=ref.get()
        verify_envelope(remote,expected_sha=args.expected_sha256)
        if remote!=env:
            raise AssertionError('Remote envelope differs from original payload')
        report.update({'status':'FIREBASE BZIP2 DOWNLOAD VERIFY OK',
                       'node':args.node,'checked_utc':datetime.now(timezone.utc).isoformat(),
                       'roundtrip_verified':True})
        Path(args.proof).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('FIREBASE BZIP2 DOWNLOAD VERIFY OK')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        firebase_admin.delete_app(app)

if __name__=='__main__':run()
