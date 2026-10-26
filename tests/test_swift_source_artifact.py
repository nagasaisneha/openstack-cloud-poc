import importlib.util
import io
from pathlib import Path
import tarfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location('swift_source_verification',
    Path(__file__).resolve().parents[1] / 'deployment/swift-maintained/verify_source.py')
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


class SwiftSourceArtifactTests(unittest.TestCase):
    def archives(self, wheel_content):
        source = io.BytesIO()
        with tarfile.open(fileobj=source, mode='w:gz') as tar:
            member = tarfile.TarInfo('swift-' + verify.VERSION + '/swift/module.py')
            member.size = len(b'original code')
            tar.addfile(member, io.BytesIO(b'original code'))
        wheel = io.BytesIO()
        with zipfile.ZipFile(wheel, mode='w') as archive:
            archive.writestr('swift/module.py', wheel_content)
        return source.getvalue(), wheel.getvalue()

    def test_wheel_python_code_must_match_the_signed_source_bytes(self):
        self.assertEqual(verify.module_map(*self.archives(b'original code')), 1)
        with self.assertRaises(RuntimeError):
            verify.module_map(*self.archives(b'altered code'))
