import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock

PATH = pathlib.Path(__file__).with_name('signed_test.py')


def module():
    if not PATH.is_file():
        raise AssertionError('signed test helper is missing')
    spec = importlib.util.spec_from_file_location('signed_test', PATH)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class SignedTestTests(unittest.TestCase):
    def test_source_metadata_exact_and_unambiguous(self):
        m = module()
        text = '        applicationId = "be.mygod.vpnhotspot"\n        versionCode = 2011004\n        versionName = "3.0.8-p4"\n'
        self.assertEqual(m.source_metadata(text), {'package_name': 'be.mygod.vpnhotspot', 'version_code': '2011004', 'version_name': '3.0.8-p4'})
        with self.assertRaises(ValueError):
            m.source_metadata(text + '        versionCode = 1\n')

    def test_badging_requires_exact_package_and_versions(self):
        m = module()
        info = {'package_name': 'be.mygod.vpnhotspot', 'version_code': '2011004', 'version_name': '3.0.8-p4'}
        good = "package: name='be.mygod.vpnhotspot' versionCode='2011004' versionName='3.0.8-p4' platformBuildVersionName='16'\n"
        m.verify_badging(good, info)
        for bad in (good.replace('2011004', '2011005'), good.replace('3.0.8-p4', '3.0.8-p40'), good.replace('be.mygod.vpnhotspot', 'evil')):
            with self.assertRaises(ValueError):
                m.verify_badging(bad, info)

    def test_exact_trusted_signer_and_single_signer(self):
        m = module()
        good = 'Number of signers: 1\nSigner #1 certificate SHA-256 digest: ' + m.TRUSTED_CERT + '\n'
        m.verify_certificate(good)
        for bad in (good.replace(m.TRUSTED_CERT, '0' * 64), good.replace('signers: 1', 'signers: 2'), good + 'Signer #2 certificate SHA-256 digest: ' + '0' * 64 + '\n'):
            with self.assertRaises(ValueError):
                m.verify_certificate(bad)
        m.verify_certificate('Number of signers: 1\nV1 Signer: certificate SHA-256 digest: ' + m.TRUSTED_CERT.upper() + '\n')

    def test_four_expected_apks_only(self):
        m = module()
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            for name in m.expected_names():
                (root / name).touch()
            self.assertEqual(len(m.validate_apks(root)), 4)
            (root / 'extra.apk').touch()
            with self.assertRaises(ValueError):
                m.validate_apks(root)
            (root / 'extra.apk').unlink()
            (root / next(iter(m.expected_names()))).unlink()
            with self.assertRaises(ValueError):
                m.validate_apks(root)

    def test_command_errors_do_not_include_secret_arguments_or_output(self):
        m = module()
        failed = mock.Mock(returncode=1, stdout='', stderr='sensitive-alias')
        with mock.patch.object(m.subprocess, 'run', return_value=failed):
            with self.assertRaises(RuntimeError) as caught:
                m.run('apksigner', '--ks-key-alias', 'sensitive-alias')
        self.assertNotIn('sensitive-alias', str(caught.exception))

    def test_trusted_source_is_immutable_and_exact(self):
        m = module()
        self.assertEqual(m.TRUSTED_TAG, 'v3.0.8-p4')
        self.assertEqual(m.TRUSTED_SHA, 'ae6f39ed82153b5edf6c797bc1a27e17ab5ee845')
        with mock.patch.object(m, 'api', return_value={'object': {'type': 'commit', 'sha': m.TRUSTED_SHA}}):
            self.assertEqual(m.resolve_trusted_source(), (m.TRUSTED_TAG, m.TRUSTED_SHA))
        with mock.patch.object(m, 'api', side_effect=[
            {'object': {'type': 'tag', 'sha': '1' * 40}},
            {'object': {'type': 'commit', 'sha': m.TRUSTED_SHA}},
        ]) as api:
            self.assertEqual(m.resolve_trusted_source(), (m.TRUSTED_TAG, m.TRUSTED_SHA))
            self.assertEqual(api.call_count, 2)
        with mock.patch.object(m, 'api', return_value={'object': {'type': 'commit', 'sha': '0' * 40}}):
            with self.assertRaises(ValueError):
                m.resolve_trusted_source()

    def test_source_tree_requires_exact_index_and_clean_worktree(self):
        m = module()
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            info = root / 'build-info.json'
            info.write_text('{"patched_tree": "' + 'a' * 40 + '"}\n', encoding='utf-8')
            with mock.patch.object(m, 'run', side_effect=[
                'a' * 40, ' 0162af698935100a590b7359581ac8b1b80693e5 external/aosp/hardware/interfaces'
                , ''
            ]) as run:
                with mock.patch.object(m.subprocess, 'run', return_value=mock.Mock(returncode=0)):
                    m.verify_source_tree(root, info)
            self.assertEqual(run.call_args_list[0].args[:2], ('git', 'write-tree'))

    def test_source_tree_rejects_index_worktree_or_submodule_changes(self):
        m = module()
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            info = root / 'build-info.json'
            info.write_text('{"patched_tree": "' + 'a' * 40 + '"}\n', encoding='utf-8')
            cases = (
                (['b' * 40], 0),
                (['a' * 40, ' 0162af698935100a590b7359581ac8b1b80693e5 external/aosp/hardware/interfaces'], 1),
                (['a' * 40, ' 0162af698935100a590b7359581ac8b1b80693e5 external/aosp/hardware/interfaces', ' M file'], 0),
            )
            for outputs, diff_status in cases:
                with self.subTest(outputs=outputs, diff_status=diff_status):
                    with mock.patch.object(m, 'run', side_effect=outputs):
                        with mock.patch.object(m.subprocess, 'run', return_value=mock.Mock(returncode=diff_status)):
                            with self.assertRaises(ValueError):
                                m.verify_source_tree(root, info)


if __name__ == '__main__':
    unittest.main()
