"""Cleanup recovery tests: public proof fixtures, Google entirely simulated."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase

from scripts import cleanup_drive_synthetic_local as cleanup
from scripts import validate_drive_backup_remote as remote
from scripts.tests.test_drive_remote_transfer import CONFIG


class PinnedCleanupTests(SimpleTestCase):
    def metadata(self):
        transfer = cleanup.CleanupTransfer(CONFIG)
        return dict(id=cleanup.FILE_ID, name=transfer.name, appProperties=transfer.marker,
                    size=str(cleanup.SIZE), sha256Checksum=cleanup.SHA256,
                    mimeType='application/octet-stream', ownedByMe=True, trashed=False,
                    permissions=[{'type': 'user', 'role': 'owner'}])

    def test_no_payload_and_no_upload_download_listing_or_other_target(self):
        transfer = cleanup.CleanupTransfer(CONFIG)
        self.assertFalse(hasattr(transfer, 'payload'))
        for method, host, path in (
            ('GET', 'www.googleapis.com', '/drive/v3/files'),
            ('GET', 'www.googleapis.com', '/drive/v3/files/generateIds?count=1&space=drive&type=files'),
            ('GET', 'www.googleapis.com', '/drive/v3/files/' + cleanup.FILE_ID + '?alt=media'),
            ('DELETE', 'www.googleapis.com', '/drive/v3/files/another-file-id'),
            ('POST', 'www.googleapis.com', '/upload/drive/v3/files?uploadType=multipart'),
            ('POST', 'oauth2.googleapis.com', '/revoke'),
        ):
            self.assertFalse(transfer._allowed(method, host, path))
        with self.assertRaises(remote.RemoteTestError):
            transfer.upload_and_download()

    def test_exact_provenance_required_before_delete(self):
        transfer = cleanup.CleanupTransfer(CONFIG)
        for change in ({'id': 'another-file-id'}, {'name': 'personal-file.gpg'},
                       {'appProperties': {}}, {'ownedByMe': False}, {'size': '1'},
                       {'sha256Checksum': '0' * 64}, {'sha256Checksum': ''},
                       {'mimeType': 'application/vnd.google-apps.folder'},
                       {'mimeType': 'application/vnd.google-apps.shortcut'},
                       {'mimeType': 'APPLICATION/VND.GOOGLE-APPS.FOLDER'},
                       {'mimeType': None}, {'mimeType': ''}, {'mimeType': 'invalid'},
                       {'mimeType': 'application/x-gpg\r\nInjected: true'}):
            with self.subTest(fields=list(change)), \
                    patch.object(transfer, 'metadata', return_value=self.metadata() | change), \
                    patch.object(transfer, '_call') as call, self.assertRaises(remote.RemoteTestError):
                transfer.cleanup()
            call.assert_not_called()

    def test_absent_sha_now_blocks_cleanup_instead_of_weakening_provenance(self):
        transfer = cleanup.CleanupTransfer(CONFIG)
        item = self.metadata()
        item.pop('sha256Checksum')
        with self.assertRaises(remote.RemoteTestError):
            transfer.verify(item, cleanup=True)
        self.assertNotIn('server_sha256', transfer.checks)
        self.assertFalse(transfer.metadata_flags['hash_present'])

    def test_opaque_mime_is_accepted_only_with_all_pins_and_exact_cipher_hash(self):
        transfer = cleanup.CleanupTransfer(CONFIG)
        for mime in ('application/pgp', 'application/x-gpg', 'application/x-opaque-fixture', 'text/plain'):
            with self.subTest(mime=mime):
                item = self.metadata() | {'mimeType': mime}
                transfer.verify(item, cleanup=True)
                self.assertFalse(transfer.metadata_flags['mime_match'])
                self.assertTrue(transfer.metadata_flags['stored_blob_type'])
                self.assertEqual(transfer.checks['server_sha256'], 'PASS')
                item.pop('sha256Checksum')
                with self.assertRaises(remote.RemoteTestError):
                    transfer.verify(item, cleanup=True)

    def test_sharing_trashed_and_mime_diagnostics_do_not_mask_exact_provenance(self):
        transfer = cleanup.CleanupTransfer(CONFIG)
        item = self.metadata() | dict(mimeType='application/pgp-encrypted', trashed=True,
                                     permissions=[{'type': 'anyone', 'role': 'reader'}])
        transfer.verify(item, cleanup=True)
        self.assertFalse(transfer.metadata_flags['mime_match'])
        self.assertFalse(transfer.metadata_flags['not_trashed'])
        self.assertFalse(transfer.metadata_flags['owner_only'])
        self.assertEqual(transfer.checks['pinned_synthetic_provenance'], 'PASS')

    def test_success_deletes_only_pinned_id_and_confirms_absence(self):
        transfer = cleanup.CleanupTransfer(CONFIG)
        with patch.object(transfer, 'metadata', side_effect=[self.metadata(), None]), \
                patch.object(transfer, '_call', return_value={}) as call:
            self.assertEqual(transfer.cleanup(), 'PASS')
        call.assert_called_once_with('DELETE', 'www.googleapis.com', '/drive/v3/files/' + cleanup.FILE_ID)

    def test_initial_404_is_not_deletion_success(self):
        transfer = cleanup.CleanupTransfer(CONFIG)
        with patch.object(transfer, 'metadata', return_value=None), \
                patch.object(transfer, '_call') as call, patch.object(remote.time, 'sleep'):
            self.assertEqual(transfer.cleanup(), 'NOT FOUND AFTER ATTEMPT')
        call.assert_not_called()


class CleanupProofTests(SimpleTestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory(prefix='synthetic-cleanup-proof-'))
        self.root = Path(self.tmp).resolve()
        self.enterContext(patch.object(remote, 'ROOT', self.root))
        self.directory = self.root / 'tmp' / ('p0a-drive-remote-bridge-' + cleanup.RUN)
        self.directory.mkdir(parents=True)
        self.request = dict(run_id=cleanup.RUN, scope='synthetic_database_only',
                            cipher_path='nonexistent-never-opened-original.gpg',
                            cipher_sha256=cleanup.SHA256, cipher_size=cleanup.SIZE)
        self.previous = dict(status='FAIL', test_file_id=cleanup.FILE_ID,
                             cipher_sha256=cleanup.SHA256, cipher_size=cleanup.SIZE,
                             scope='synthetic_database_only', production_access=False, media_included=False)
        self.native = dict(run_id=cleanup.RUN, result=dict(ciphertext_sha256=cleanup.SHA256,
                                                         checks={'temporary_runtime_removed': 'PASS'}))
        for name, value in (('request.json', self.request), ('remote-result.json', self.previous),
                            ('native-result.json', self.native)):
            remote.write_json(self.directory / name, value)

    def test_loads_only_pinned_public_proof_without_original_ciphertext(self):
        attempt = cleanup.CleanupAttempt()
        self.assertTrue(attempt.cleanup_only)
        self.assertFalse(attempt.used)
        self.assertEqual(attempt.public_status(), 'WAITING')
        self.assertEqual(attempt.public_result()['upload_operations'], 0)
        attempt.cancel_if_incomplete()
        self.assertFalse((self.directory / 'abort.json').exists())

    def test_modified_proof_refuses_all_remote_work(self):
        for name, original, change in (
            ('request.json', self.request, {'run_id': '0' * 32}),
            ('request.json', self.request, {'cipher_sha256': '0' * 64}),
            ('remote-result.json', self.previous, {'test_file_id': 'other-file-id'}),
            ('remote-result.json', self.previous, {'cipher_size': True}),
            ('remote-result.json', self.previous, {'production_access': True}),
            ('native-result.json', self.native, {'run_id': '0' * 32}),
            ('native-result.json', self.native, {'result': {'checks': {}}}),
        ):
            path = self.directory / name
            path.write_text(json.dumps(original | change))
            with self.subTest(name=name, fields=list(change)), self.assertRaises(remote.RemoteTestError):
                cleanup.CleanupAttempt()
            path.write_text(json.dumps(original))

    def test_success_is_one_shot_and_never_changes_original_report(self):
        attempt = cleanup.CleanupAttempt()
        original = (self.directory / 'remote-result.json').read_bytes()
        with patch.object(cleanup.CleanupTransfer, 'authenticate'), \
                patch.object(cleanup.CleanupTransfer, 'cleanup', return_value='PASS'):
            attempt.run(CONFIG)
        self.assertEqual(attempt.public_status(), 'PASS')
        self.assertEqual((self.directory / 'remote-result.json').read_bytes(), original)
        self.assertEqual(len(list(self.directory.glob('cleanup-result-*.json'))), 1)
        with self.assertRaises(remote.RemoteTestError):
            attempt.run(CONFIG)

    def test_failure_only_exports_fixed_diagnostics_not_exception_text(self):
        attempt = cleanup.CleanupAttempt()
        with patch.object(cleanup.CleanupTransfer, 'authenticate', side_effect=ValueError('private-provider-message')):
            attempt.run(CONFIG)
        result = attempt.public_result()
        self.assertEqual(result['status'], 'FAIL')
        self.assertIn('cleanup_error', result)
        self.assertNotIn('private-provider-message', json.dumps(result))
        self.assertEqual(result['upload_operations'], 0)
        self.assertEqual(result['download_operations'], 0)

    def test_ambiguous_absence_is_not_cleanup_pass(self):
        attempt = cleanup.CleanupAttempt()
        with patch.object(cleanup.CleanupTransfer, 'authenticate'), \
                patch.object(cleanup.CleanupTransfer, 'cleanup', return_value='NOT FOUND AFTER ATTEMPT'):
            attempt.run(CONFIG)
        self.assertEqual(attempt.public_status(), 'FAIL')
