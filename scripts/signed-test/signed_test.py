#!/usr/bin/env python3
"""Prepare, check and sign a test build, without publishing a release."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile

UPSTREAM = 'lovitus/VPNHotspot'
PACKAGE = 'be.mygod.vpnhotspot'
TRUSTED_TAG = 'v3.0.8-p4'
TRUSTED_SHA = 'ae6f39ed82153b5edf6c797bc1a27e17ab5ee845'
TRUSTED_CERT = '37990b9e22dd4ee8cf77d78e922af16f7ef329107f15062ecc8e2886e2d3438c'
ABIS = ('arm64-v8a', 'armeabi-v7a', 'x86', 'x86_64')


def run(*args, cwd=None, env=None):
    result = subprocess.run(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        # Never include arguments or tool output: they can contain the signing alias.
        raise RuntimeError(f'{Path(args[0]).name} failed with exit status {result.returncode}')
    return result.stdout.strip()


def api(path):
    request = urllib.request.Request('https://api.github.com/repos/' + UPSTREAM + '/' + path,
        headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'vpn-signed-test-ci',
                 'Authorization': 'Bearer ' + os.environ['GH_TOKEN']})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def source_metadata(text):
    result = {}
    for key, pattern in (
        ('package_name', r'(?m)^\s*applicationId\s*=\s*"([^"\n]+)"\s*$'),
        ('version_code', r'(?m)^\s*versionCode\s*=\s*([0-9]+)\s*$'),
        ('version_name', r'(?m)^\s*versionName\s*=\s*"([^"\n]+)"\s*$'),
    ):
        values = re.findall(pattern, text)
        if len(values) != 1:
            raise ValueError('Expected exactly one literal ' + key)
        result[key] = values[0]
    if result['package_name'] != PACKAGE:
        raise ValueError('Unexpected source package')
    return result


def verify_badging(badging, info):
    lines = badging.splitlines()
    if not lines or not lines[0].startswith('package: '):
        raise ValueError('Missing APK package metadata')
    fields = dict(re.findall(r"([A-Za-z][A-Za-z0-9]*)='([^']*)'", lines[0]))
    if any(fields.get(field) != info[key] for field, key in (
        ('name', 'package_name'), ('versionCode', 'version_code'), ('versionName', 'version_name'))):
        raise ValueError('APK package or version differs from source')


def verify_certificate(text):
    if not re.search(r'(?m)^Number of signers: 1\r?$', text):
        raise ValueError('Expected exactly one signer')
    certs = re.findall(r'(?m)^(?:Signer #[0-9]+|V[0-9]+(?:\.[0-9]+)* Signer:) certificate SHA-256 digest: ([0-9a-fA-F]{64})\r?$', text)
    if not certs or any(cert.lower() != TRUSTED_CERT for cert in certs):
        raise ValueError('APK certificate is not the trusted p4 certificate')
    if re.search(r'(?m)^Signer #(?!1\b)[0-9]+ certificate', text):
        raise ValueError('Unexpected additional signer')


def expected_names():
    return {f'mobile-{abi}-release-unsigned.apk' for abi in ABIS}


def validate_apks(directory):
    paths = list(directory.glob('*.apk'))
    if {p.name for p in paths} != expected_names():
        raise ValueError('Expected exactly the four unsigned ABI APKs')
    return sorted(paths)


def verify_apk(apk, abi, info, tools, env):
    with zipfile.ZipFile(apk) as archive:
        if f'lib/{abi}/libvpnhotspotd.so' not in archive.namelist():
            raise ValueError('Missing daemon for ' + abi)
    verify_badging(run(str(tools / 'aapt2'), 'dump', 'badging', str(apk), env=env), info)


def resolve_trusted_source():
    obj = api('git/ref/tags/' + urllib.parse.quote(TRUSTED_TAG, safe=''))['object']
    for _ in range(10):
        if obj['type'] != 'tag':
            break
        obj = api('git/tags/' + obj['sha'])['object']
    if obj.get('type') != 'commit' or obj.get('sha') != TRUSTED_SHA:
        raise ValueError('Trusted release tag does not resolve to the pinned commit')
    return TRUSTED_TAG, TRUSTED_SHA


def prepare(source, patch, info_path):
    tag, sha = resolve_trusted_source()
    if source.exists():
        raise ValueError('Source destination already exists')
    run('git', 'init', '--quiet', str(source))
    run('git', 'fetch', '--no-tags', 'https://github.com/' + UPSTREAM + '.git', 'refs/tags/' + tag, cwd=source)
    if run('git', 'rev-parse', 'FETCH_HEAD^{commit}', cwd=source) != sha:
        raise ValueError('Release tag moved during fetch')
    run('git', 'checkout', '--quiet', '--detach', sha, cwd=source)
    gradle = source / 'mobile/build.gradle.kts'
    metadata = source_metadata(gradle.read_text(encoding='utf-8'))
    run('git', 'apply', '--check', str(patch.resolve()), cwd=source)
    run('git', 'apply', '--index', str(patch.resolve()), cwd=source)
    if source_metadata(gradle.read_text(encoding='utf-8')) != metadata:
        raise ValueError('Patch changed source version or package')
    info = metadata | {'upstream_repository': UPSTREAM, 'upstream_tag': tag, 'upstream_sha': sha,
                       'patch_sha256': hashlib.sha256(patch.read_bytes()).hexdigest(),
                       'patched_tree': run('git', 'write-tree', cwd=source)}
    info_path.parent.mkdir(parents=True, exist_ok=True)
    info_path.write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')
    print('Prepared', tag, sha, 'with web patch only; version', metadata['version_name'], metadata['version_code'])


def verify_unsigned(apks, info_path, tools):
    info = json.loads(info_path.read_text(encoding='utf-8'))
    validate_apks(apks)
    for abi in ABIS:
        verify_apk(apks / f'mobile-{abi}-release-unsigned.apk', abi, info, tools, os.environ.copy())
    print('Verified four unsigned APKs against source metadata and ABIs')


def sign(apks, info_path, tools, output):
    info = json.loads(info_path.read_text(encoding='utf-8'))
    validate_apks(apks)
    key_data = os.environ.pop('APK_SIGNING_KEY', '')
    password = os.environ.pop('APK_SIGNING_PASSWORD', '')
    alias = os.environ.pop('APK_SIGNING_ALIAS', '')
    if not key_data or not password or not alias:
        raise ValueError('All three signing secrets are required')
    env = os.environ.copy()
    sign_env = env | {'APK_TEST_KEY_PASSWORD': password}
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(dir=os.environ.get('RUNNER_TEMP')) as tmp:
        key = Path(tmp) / 'key.p12'
        fd = os.open(key, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(base64.b64decode(''.join(key_data.split()), validate=True))
        for abi in ABIS:
            apk = apks / f'mobile-{abi}-release-unsigned.apk'
            verify_apk(apk, abi, info, tools, env)
            aligned = Path(tmp) / 'aligned.apk'
            signed = output / f'mobile-{abi}-test.apk'
            run(str(tools / 'zipalign'), '-f', '-P', '16', '4', str(apk), str(aligned), env=env)
            run(str(tools / 'apksigner'), 'sign', '--ks', str(key), '--ks-type', 'PKCS12',
                '--ks-key-alias', alias, '--ks-pass', 'env:APK_TEST_KEY_PASSWORD',
                '--key-pass', 'env:APK_TEST_KEY_PASSWORD', '--out', str(signed), str(aligned), env=sign_env)
            verify_certificate(run(str(tools / 'apksigner'), 'verify', '--verbose', '--print-certs', str(signed), env=env))
            run(str(tools / 'zipalign'), '-c', '-P', '16', '4', str(signed), env=env)
            verify_apk(signed, abi, info, tools, env)
    info['signing_certificate_sha256'] = TRUSTED_CERT
    info['apk_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob('*.apk'))}
    (output / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')
    print('Verified four signed test APKs against trusted p4 certificate')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare', 'verify', 'sign'))
    parser.add_argument('--source', type=Path, default=Path('source'))
    parser.add_argument('--patch', type=Path, default=Path('maintenance/patches/0005-web-admin.patch'))
    parser.add_argument('--info', type=Path, default=Path('verified/build-info.json'))
    parser.add_argument('--apks', type=Path, default=Path('verified/apks'))
    parser.add_argument('--tools', type=Path, default=Path(os.environ.get('ANDROID_HOME', '.')) / 'build-tools/37.0.0')
    parser.add_argument('--output', type=Path, default=Path('signed-test'))
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.source, args.patch, args.info)
    elif args.action == 'verify':
        verify_unsigned(args.apks, args.info, args.tools)
    else:
        sign(args.apks, args.info, args.tools, args.output)


if __name__ == '__main__':
    main()
