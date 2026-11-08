import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('builder', Path(__file__).resolve().parents[1] / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BuilderTests(unittest.TestCase):
    def test_rejects_unsafe_target_profile_packages_and_wrong_release(self):
        good = {'version': builder.VERSION, 'target': 'x86/64', 'profile': 'generic', 'packages': ['luci-ssl', '-ppp']}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'
            path.write_text(json.dumps(good))
            self.assertEqual(builder.read_config(path), good)
            for changes in [{'target': '../x86'}, {'profile': 'generic; id'},
                    {'packages': ['a$(id)']}, {'packages': ['abc def']}, {'version': '24.10.8'}]:
                path.write_text(json.dumps({**good, **changes}))
                with self.assertRaises(ValueError):
                    builder.read_config(path)

    def test_overlay_cannot_reference_host_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'outside').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):
                builder.validate_overlay(root)

    def test_untrusted_download_rejected_before_network(self):
        for url in ['http://downloads.openwrt.org/image', 'https://elsewhere.invalid/',
                    'https://downloads.openwrt.org/releases/24.10.8/image']:
            with self.assertRaises(ValueError):
                builder.download(url, Path('/not-written'))

    def test_packages_only_requested_native_image_and_keeps_installation_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            products = root / 'bin/targets/x86/64'
            products.mkdir(parents=True)
            image = products / 'exact-sysupgrade.bin'
            image.write_bytes(b'firmware')
            loader = products / 'preloader.bin'
            loader.write_bytes(b'bootloader')
            (products / 'other-router.bin').write_bytes(b'not the requested board')
            images = [{'name': image.name, 'type': 'sysupgrade', 'filesystem': 'squashfs', 'sha256': builder.digest(image)},
                      {'name': loader.name, 'type': 'preloader.bin', 'sha256': builder.digest(loader)}]
            (products / 'profiles.json').write_text(json.dumps({'version_number': builder.VERSION,
                'version_code': 'r123-test', 'target': 'x86/64', 'profiles': {'generic': {'images': images}}}))
            (root / 'LICENSE').write_text('MIT')
            config = {'target': 'x86/64', 'profile': 'generic', 'architecture': 'x86_64',
                'arch_packages': 'x86_64', 'packages': ['luci-ssl']}
            with patch.object(builder, 'ROOT', root), patch.object(builder.subprocess, 'check_output', return_value=b'a' * 40):
                manifest = builder.package(root, config, {}, {'url': 'upstream'}, imagebuilder_sha256='b' * 64)
            with zipfile.ZipFile(root / 'output/openwrt-firmware.zip') as archive:
                self.assertIn('images/exact-sysupgrade.bin', archive.namelist())
                self.assertNotIn('images/other-router.bin', archive.namelist())
                self.assertNotIn('images/preloader.bin', archive.namelist())
                self.assertEqual(manifest['images'][0]['type'], 'sysupgrade')
                self.assertFalse(manifest['hardware_tested'])
                self.assertFalse(manifest['runtime_tested'])
                for name, sha256 in manifest['files'].items():
                    self.assertEqual(__import__('hashlib').sha256(archive.read(name)).hexdigest(), sha256)
