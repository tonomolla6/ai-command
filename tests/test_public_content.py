import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('public_content', Path(__file__).resolve().parent.parent / 'scripts/check_public_content.py')
public_content = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public_content)


class PublicContentTests(unittest.TestCase):
    def test_rejects_private_files_and_values_without_returning_values(self):
        fake = b'sk-' + b'A' * 32
        self.assertIn('private-file', public_content.inspect('profiles/auth.json', b'{}'))
        self.assertIn('private-file', public_content.inspect('.env.local', b''))
        self.assertEqual(public_content.inspect('README.md', fake), ['credential-shaped-value'])
        self.assertIn('non-example-email', public_content.inspect('README.md', b'user@' + b'private.provider'))
        self.assertIn('local-private-term', public_content.inspect('README.md', b'private-project', (b'private-project',)))

    def test_allows_fictional_examples_and_secret_detection_source(self):
        self.assertEqual(public_content.inspect('README.md', b'ana@example.org user@example.invalid'), [])
        self.assertEqual(public_content.inspect('core.py', b"name = 'auth.json'; token = os.environ.get('API_KEY')"), [])
