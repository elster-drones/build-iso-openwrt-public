#!/usr/bin/env python3
"""Wire a pinned upstream ImageBuilder/SDK to exact-profile firmware output."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile

VERSION = "25.12.6"
ROOT = Path(__file__).resolve().parent


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def read_config(path):
    config = json.loads(Path(path).read_text())
    if (config.get('version') != VERSION
            or not re.fullmatch(r'[a-z0-9_-]+/[a-z0-9_-]+', config.get('target', ''))
            or not re.fullmatch(r'[a-zA-Z0-9_.+-]+', config.get('profile', ''))
            or not isinstance(config.get('packages'), list)
            or not all(isinstance(p, str) and re.fullmatch(r'-?[a-zA-Z0-9][a-zA-Z0-9_.+-]*', p) for p in config['packages'])):
        raise ValueError('Invalid pinned OpenWrt target or package selection')
    return config


def download(url, path, *, sha256=None, max_bytes=2 * 1024**3):
    if not url.startswith('https://downloads.openwrt.org/releases/' + VERSION + '/'):
        raise ValueError('Only pinned OpenWrt release inputs are supported')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if sha256 and path.is_file() and digest(path) == sha256:
        return path
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.download-')
    try:
        with os.fdopen(fd, 'wb') as out, urllib.request.urlopen(url, timeout=60) as response:
            count = 0
            while block := response.read(1024**2):
                count += len(block)
                if count > max_bytes:
                    raise ValueError('OpenWrt download exceeds its size bound')
                out.write(block)
        if sha256 and digest(name) != sha256:
            raise ValueError('Pinned OpenWrt input checksum mismatch')
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
    return path


def run(*args, cwd=ROOT):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run([str(a) for a in args], cwd=cwd, check=True)


def authenticated_sources(config, cache):
    base = 'https://downloads.openwrt.org/releases/' + VERSION + '/targets/' + config['target'] + '/'
    sums = download(base + 'sha256sums', cache / 'sha256sums',
        sha256=config['sources']['checksums_sha256'], max_bytes=8 * 1024**2)
    asc = download(base + 'sha256sums.asc', cache / 'sha256sums.asc', max_bytes=65536)
    key = cache / 'release-key.gpg'
    run('gpg', '--batch', '--yes', '--dearmor', '--output', key, ROOT / 'keys/openwrt-release.asc')
    run('gpgv', '--keyring', key, asc, sums)
    verified = {}
    for line in sums.read_text().splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and re.fullmatch('[0-9a-f]{64}', parts[0]):
            verified[parts[1].lstrip('*')] = parts[0]
    for kind in ('imagebuilder', 'sdk'):
        source = config['sources'].get(kind)
        if source is not None and (source['url'] != base + source['filename']
                or verified.get(source['filename']) != source['sha256']):
            raise ValueError('ImageBuilder or SDK does not match signed release checksums')
    profiles_path = download(base + 'profiles.json', cache / 'profiles.json',
        sha256=verified['profiles.json'], max_bytes=16 * 1024**2)
    profiles = json.loads(profiles_path.read_text())
    if profiles['version_number'] != VERSION or config['profile'] not in profiles['profiles']:
        raise ValueError('Requested profile is not in the signed release')
    if profiles['arch_packages'] != config['arch_packages']:
        raise ValueError('Requested architecture conflicts with the signed profile')
    return profiles


def extract(source, destination):
    destination.mkdir(parents=True)
    # tarfile's data filter checks archive paths, links and device members.
    with subprocess.Popen(['zstd', '-dc', str(source)], stdout=subprocess.PIPE) as proc:
        try:
            with tarfile.open(fileobj=proc.stdout, mode='r|') as archive:
                archive.extractall(destination, filter='data')
        except BaseException:
            proc.terminate()
            raise
        if proc.wait():
            raise RuntimeError('OpenWrt archive decompression failed')
    dirs = list(destination.iterdir())
    if len(dirs) != 1 or not dirs[0].is_dir():
        raise ValueError('Unexpected upstream archive root')
    return dirs[0]


def validate_overlay(path):
    if path.exists():
        root = path.resolve()
        for entry in path.rglob('*'):
            if not entry.resolve().is_relative_to(root):
                raise ValueError('Overlay symlink escapes its workspace')
            if not (entry.is_file() or entry.is_dir()):
                raise ValueError('Overlay contains a special file')


def package(root, config, profile, source, *, imagebuilder_sha256):
    output = ROOT / 'output'
    output.mkdir(exist_ok=True)
    products = root / 'bin/targets' / config['target']
    definitions = json.loads((products / 'profiles.json').read_text())
    if definitions['version_number'] != VERSION or definitions['target'] != config['target']:
        raise ValueError('Builder output target changed')
    actual = definitions['profiles'][config['profile']]
    files, images = {}, []
    # The upstream output manifest names the requested profile's outputs. Do not
    # collect a different board by filename prefix or include bootloader writes.
    for item in actual.get('images', []):
        name = item['name']
        if PurePosixPath(name).name != name or '\\' in name:
            raise ValueError('Unsafe image name')
        if item.get('type') in {'preloader.bin', 'bl31-uboot.fip'}:
            continue
        path = products / name
        if not path.is_file() or path.stat().st_size == 0 or digest(path) != item['sha256']:
            raise ValueError('Native image output failed integrity validation')
        member = 'images/' + name
        files[member] = path
        images.append({'filename': member, 'type': item['type'], 'filesystem': item.get('filesystem'), 'sha256': item['sha256']})
    if not images:
        raise ValueError('ImageBuilder produced no images for the requested profile')
    # Retain reproducibility metadata and image-specific package manifest.
    for path in products.iterdir():
        if path.is_file() and (path.name.endswith('.buildinfo') or path.name == 'profiles.json'
                or path.name.endswith('.manifest')):
            files['metadata/' + path.name] = path
    notes = output / 'INSTALL.txt'
    notes.write_text('OpenWrt ' + VERSION + '\nExact target: ' + config['target'] + ':' + config['profile']
        + '\n\nThese are upstream-packaged images, not hardware-tested by OpenFactory.\n'
        'Factory and sysupgrade images are different installation types. A sysupgrade image is not automatically accepted by a stock firmware uploader.\n'
        'Use the exact model/revision installation and recovery instructions in the OpenWrt Table of Hardware: https://openwrt.org/toh/start\n'
        'Preserve the router factory calibration and bootloader. No bootloader-write images are included.\n'
        'Compilation and checksum validation do not establish boot, Ethernet or Wi-Fi operation on hardware.\n')
    files['INSTALL.txt'] = notes
    files['LICENSE'] = ROOT / 'LICENSE'
    for directory in ['config/sdk-packages', 'config/files']:
        inputs = ROOT / directory
        if inputs.is_dir():
            for path in inputs.rglob('*'):
                if path.is_file() and not path.is_symlink():
                    files['sources/' + str(path.relative_to(ROOT))] = path
    manifest = {'schema_version': 1, 'distribution': 'openwrt', 'version': VERSION,
        'target': config['target'], 'profile': config['profile'],
        'board': config['target'] + ':' + config['profile'], 'architecture': config['architecture'],
        'arch_packages': config['arch_packages'], 'packages': config['packages'],
        'hardware_tested': False, 'runtime_tested': False, 'images': images,
        'imagebuilder_url': source['url'], 'imagebuilder_sha256': imagebuilder_sha256,
        'source_url': 'https://git.openwrt.org/openwrt/openwrt.git',
        'source_revision': definitions['version_code'],
        'source_archive_url': 'https://github.com/openwrt/openwrt/archive/refs/tags/v' + VERSION + '.tar.gz',
        'builder_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
        'files': {name: digest(path) for name, path in sorted(files.items())}}
    temporary = output / 'openwrt-firmware.zip.tmp'
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(files.items()):
            archive.write(path, name)
        archive.writestr('manifest.json', json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    temporary.replace(output / 'openwrt-firmware.zip')
    return manifest


def main():
    config = read_config(ROOT / 'config/openwrt.json')
    cache = Path(os.environ.get('OPENWRT_CACHE_DIR', ROOT / '.cache')).resolve() / VERSION / config['target']
    cache.mkdir(parents=True, exist_ok=True)
    profiles = authenticated_sources(config, cache)
    overlay = ROOT / 'config/files'
    validate_overlay(overlay)
    sources = config['sources']
    source = sources['imagebuilder']
    archive = download(source['url'], cache / source['filename'], sha256=source['sha256'])
    with tempfile.TemporaryDirectory(prefix='.openwrt-', dir=ROOT) as work:
        imagebuilder = extract(archive, Path(work) / 'imagebuilder')
        custom = ROOT / 'config/sdk-packages'
        if custom.is_dir() and any(custom.iterdir()):
            sdk_source = sources['sdk']
            sdk_archive = download(sdk_source['url'], cache / sdk_source['filename'], sha256=sdk_source['sha256'])
            sdk = extract(sdk_archive, Path(work) / 'sdk')
            run('./scripts/feeds', 'update', '-a', cwd=sdk)
            run('./scripts/feeds', 'install', '-a', cwd=sdk)
            packages = []
            for path in sorted(custom.iterdir()):
                if not path.is_dir() or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]*', path.name):
                    raise ValueError('Invalid SDK package source directory')
                validate_overlay(path)
                shutil.copytree(path, sdk / 'package' / path.name)
                packages.append(path.name)
            run('make', 'defconfig', cwd=sdk)
            for name in packages:
                run('make', 'package/' + name + '/compile', 'V=s', cwd=sdk)
            local = imagebuilder / 'packages'
            local.mkdir(exist_ok=True)
            built = list((sdk / 'bin/packages').rglob('*.apk'))
            if not built:
                raise ValueError('SDK produced no custom packages')
            for path in built:
                shutil.copy2(path, local / path.name)
        args = ['make', 'image', 'PROFILE=' + config['profile'], 'PACKAGES=' + ' '.join(config['packages'])]
        if overlay.is_dir():
            args.append('FILES=' + str(overlay))
        run(*args, cwd=imagebuilder)
        manifest = package(imagebuilder, config, profiles['profiles'][config['profile']], source,
            imagebuilder_sha256=source['sha256'])
        print(json.dumps({'artifact': 'output/openwrt-firmware.zip', 'board': manifest['board'],
            'images': len(manifest['images']), 'hardware_tested': False}), flush=True)


if __name__ == '__main__':
    main()
