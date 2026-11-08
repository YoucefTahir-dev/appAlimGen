"""Public synthetic fixtures; no real Google request, token or subprocess."""
import hashlib
import json
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from scripts import validate_drive_backup_remote as remote


RUN = '1' * 32
FILE_ID = 'synthetic_test_file_12345678'
PAYLOAD = b'\x8cPUBLIC-SYNTHETIC-PACKET-FOR-UNIT-TEST-ONLY'
CONFIG = SimpleNamespace(client_id='synthetic.apps.googleusercontent.com',
                         client_secret='synthetic-public-client-secret',
                         refresh_token='synthetic-public-refresh-token', expected_permission_id='synthetic_owner')
NATIVE_CHECKS = {name: 'PASS' for name in (
    'real_pg_restore', 'all_table_values_match', 'all_indexes_constraints_sequences_match',
    'temporary_runtime_removed', 'wrong_passphrase_rejected', 'tampered_ciphertext_rejected',
    'remote_download_sha256_match', 'remote_download_used_for_restoration',
)}


class RemoteTransferTests(SimpleTestCase):
    def transfer(self):
        return remote.TestTransfer(CONFIG, RUN, PAYLOAD)

    def metadata(self, transfer):
        return dict(id=FILE_ID, name=transfer.name, mimeType='application/octet-stream',
                    appProperties=transfer.marker, ownedByMe=True, trashed=False,
                    size=str(len(PAYLOAD)), sha256Checksum=hashlib.sha256(PAYLOAD).hexdigest(),
                    permissions=[{'type': 'user', 'role': 'owner'}])

    def test_allowlist_has_no_files_listing_folders_or_arbitrary_id(self):
        transfer = self.transfer()
        for method, host, path in (
            ('GET', 'evil.invalid', '/token'), ('GET', 'www.googleapis.com', '/drive/v3/files'),
            ('DELETE', 'www.googleapis.com', '/drive/v3/files/arbitrary'),
            ('POST', 'www.googleapis.com', '/drive/v3/files'),
            ('GET', 'www.googleapis.com', '/drive/v3/files/synthetic_test_file_12345678?alt=media'),
        ):
            self.assertFalse(transfer._allowed(method, host, path))

    def test_metadata_requires_exact_owner_run_name_size_digest_and_no_sharing(self):
        transfer = self.transfer()
        transfer.file_id = FILE_ID
        expected = self.metadata(transfer)
        transfer.verify(expected)
        for changes in ({'ownedByMe': False}, {'appProperties': {'runId': 'someone_else'}},
                        {'sha256Checksum': '0' * 64}, {'name': 'real-file.txt'}, {'size': '1'},
                        {'permissions': [{'type': 'anyone', 'role': 'reader'}]}, {'trashed': True},
                        {'mimeType': 'application/vnd.google-apps.folder'},
                        {'mimeType': 'application/vnd.google-apps.shortcut'}):
            with self.subTest(fields=list(changes)), self.assertRaises(remote.RemoteTestError):
                transfer.verify(expected | changes)

    def test_missing_optional_server_hash_fails_test_but_allows_pinned_cleanup(self):
        transfer = self.transfer()
        transfer.file_id = FILE_ID
        item = self.metadata(transfer)
        item.pop('sha256Checksum')
        with self.assertRaises(remote.RemoteTestError):
            transfer.verify(item)
        transfer.verify(item, cleanup=True)

    def test_opaque_mime_acceptance_still_requires_exact_ciphertext_digest(self):
        transfer = self.transfer()
        transfer.file_id = FILE_ID
        for mime in ('application/pgp', 'application/pgp-encrypted', 'application/x-gpg', 'text/plain'):
            item = self.metadata(transfer) | {'mimeType': mime}
            transfer.verify(item)
            self.assertFalse(transfer.metadata_flags['mime_match'])
            self.assertTrue(transfer.metadata_flags['stored_blob_type'])
            for digest in (None, '', '0' * 64):
                with self.subTest(mime=mime, digest_missing=not digest), self.assertRaises(remote.RemoteTestError):
                    transfer.verify(item | {'sha256Checksum': digest}, cleanup=True)

    def test_blob_classifier_refuses_native_objects_and_malformed_mime(self):
        for value in (None, '', [], 'invalid', ' application/x-gpg', 'application/x-gpg; charset=utf-8',
                      'application/x-gpg\n', 'application/' + 'x' * 256,
                      'application/vnd.google-apps.document', 'APPLICATION/VND.GOOGLE-APPS.FOLDER',
                      'application/vnd.google-apps.shortcut'):
            with self.subTest(kind=type(value).__name__):
                self.assertIs(remote.stored_blob_type(value), False)

    def test_metadata_diagnostics_are_only_fixed_boolean_comparisons(self):
        transfer = self.transfer()
        transfer.file_id = FILE_ID
        item = self.metadata(transfer) | {
            'name': 'private-filename-not-publishable',
            'appProperties': {'private': 'private-marker-not-publishable'},
            'permissions': [{'type': 'user', 'role': 'reader', 'emailAddress': 'private@example.invalid'}],
        }
        transfer.set_stage('metadata_before')
        transfer.http_status = 200
        with self.assertRaises(remote.RemoteTestError) as error:
            transfer.verify(item)
        diagnostic = transfer.safe_diagnostic(error.exception)
        self.assertEqual(set(diagnostic['metadata_flags']), set(remote.METADATA_FLAG_NAMES))
        self.assertTrue(all(type(value) is bool for value in diagnostic['metadata_flags'].values()))
        self.assertTrue(diagnostic['metadata_flags']['id_match'])
        self.assertFalse(diagnostic['metadata_flags']['name_match'])
        self.assertFalse(diagnostic['metadata_flags']['marker_match'])
        self.assertFalse(diagnostic['metadata_flags']['owner_only'])
        self.assertNotIn('private', json.dumps(diagnostic))
        self.assertNotIn(FILE_ID, json.dumps(diagnostic))
        self.assertEqual(diagnostic['http_status'], 200)

    def test_diagnostics_clamp_untrusted_categories_keys_and_types(self):
        transfer = self.transfer()
        transfer.stage = ['private-stage']
        transfer.failure_nature = 'private-nature'
        transfer.http_status = True
        transfer.metadata_flags = {'id_match': True, 'name_match': 'private-value', 'private-key': True}
        diagnostic = transfer.safe_diagnostic(ValueError('private-exception-message'))
        self.assertEqual(diagnostic, {'code': 'network', 'stage': 'initial', 'nature': 'unexpected',
                                      'metadata_flags': {'id_match': True}})

    def test_size_based_verification_and_diagnostics_do_not_need_payload(self):
        transfer = self.transfer()
        transfer.file_id = FILE_ID
        item = self.metadata(transfer)
        del transfer.payload
        transfer.verify(item, cleanup=True)
        self.assertTrue(transfer.metadata_diagnostics(item)['size_match'])

    def test_refresh_requires_exact_scope_and_selected_identity(self):
        transfer = self.transfer()
        for scope in (None, '', remote.SCOPE + ' email'):
            with patch.object(transfer, '_call', return_value={
                'access_token': 'synthetic_access_token_123456789', 'token_type': 'Bearer', 'scope': scope,
            }), self.assertRaises(remote.RemoteTestError):
                transfer.authenticate()
        with patch.object(transfer, '_call', side_effect=[{
            'access_token': 'synthetic_access_token_123456789', 'token_type': 'Bearer', 'scope': remote.SCOPE,
        }, {'user': {'permissionId': 'not_the_selected_owner'}}]), self.assertRaises(remote.RemoteTestError):
            transfer.authenticate()

    def test_one_preallocated_file_roundtrip_then_exact_id_delete(self):
        transfer = self.transfer()
        meta = self.metadata(transfer)
        responses = [ {'access_token': 'synthetic_access_token_123456789', 'token_type': 'Bearer', 'scope': remote.SCOPE},
                      {'user': {'permissionId': CONFIG.expected_permission_id}}, {'ids': [FILE_ID]},
                      {'id': FILE_ID}, meta, PAYLOAD, meta, meta, {}, None]
        with patch.object(transfer, '_call', side_effect=responses) as calls:
            self.assertEqual(transfer.upload_and_download(), PAYLOAD)
            self.assertEqual(transfer.cleanup(), 'PASS')
        posts = [call for call in calls.call_args_list if call.args[0] == 'POST' and call.args[1] == 'www.googleapis.com']
        self.assertEqual(len(posts), 1)
        self.assertIn('ignoreDefaultVisibility=true', posts[0].args[2])
        self.assertIn(b'.tar.gpg', posts[0].kwargs['body'])
        self.assertIn(PAYLOAD, posts[0].kwargs['body'])
        deletes = [call for call in calls.call_args_list if call.args[0] == 'DELETE']
        self.assertEqual([call.args[2] for call in deletes], ['/drive/v3/files/' + FILE_ID])
        for name in ('refresh_token_scope', 'refresh_and_selected_identity', 'id_preallocated',
                     'create_id_match', 'ciphertext_only_upload', 'metadata_before_download',
                     'real_download_same_sha256', 'metadata_after_download'):
            self.assertEqual(transfer.checks[name], 'PASS')
        self.assertIs(transfer.checks['upload_attempted'], True)

    def test_failed_metadata_keeps_completed_progress_and_precise_stage(self):
        transfer = self.transfer()
        bad_metadata = self.metadata(transfer) | {'mimeType': 'application/vnd.google-apps.shortcut'}
        responses = [{'access_token': 'synthetic_access_token_123456789', 'token_type': 'Bearer', 'scope': remote.SCOPE},
                     {'user': {'permissionId': CONFIG.expected_permission_id}}, {'ids': [FILE_ID]},
                     {'id': FILE_ID}, bad_metadata]
        with patch.object(transfer, '_call', side_effect=responses), self.assertRaises(remote.RemoteTestError) as error:
            transfer.upload_and_download()
        self.assertEqual(transfer.checks['refresh_and_selected_identity'], 'PASS')
        self.assertEqual(transfer.checks['create_id_match'], 'PASS')
        self.assertNotIn('real_download_same_sha256', transfer.checks)
        diagnostic = transfer.safe_diagnostic(error.exception)
        self.assertEqual(diagnostic['stage'], 'metadata_before')
        self.assertFalse(diagnostic['metadata_flags']['mime_match'])

    def test_download_mismatch_never_qualifies(self):
        transfer = self.transfer()
        transfer.file_id = FILE_ID
        with patch.object(transfer, 'authenticate'), patch.object(transfer, '_call', side_effect=[
            {'ids': [FILE_ID]}, {'id': FILE_ID}, self.metadata(transfer), b'corrupt',
        ]), self.assertRaises(remote.RemoteTestError) as error:
            transfer.upload_and_download()
        self.assertEqual(error.exception.code, 'download')

    def test_cleanup_never_deletes_foreign_marker_or_different_content(self):
        transfer = self.transfer()
        transfer.file_id, transfer.attempted = FILE_ID, True
        for change in ({'appProperties': {}}, {'sha256Checksum': '0' * 64}):
            with patch.object(transfer, 'metadata', return_value=self.metadata(transfer) | change), \
                    patch.object(transfer, '_call') as call, self.assertRaises(remote.RemoteTestError):
                transfer.cleanup()
            call.assert_not_called()

    def test_http_rejects_nonallowlisted_operation_before_socket(self):
        with patch.object(remote.http.client, 'HTTPSConnection') as https, self.assertRaises(remote.RemoteTestError):
            self.transfer()._call('GET', 'www.googleapis.com', '/drive/v3/files')
        https.assert_not_called()

    def http_fixture(self, status, content):
        stream = io.BytesIO(content)
        response = SimpleNamespace(status=status, read1=stream.read)
        connection = SimpleNamespace(sock=None, request=lambda *a, **k: None,
                                     getresponse=lambda: response, close=lambda: None)
        return patch.object(remote.http.client, 'HTTPSConnection', return_value=connection)

    def test_binary_download_is_bounded_and_not_parsed_as_json(self):
        transfer = self.transfer()
        transfer.file_id, transfer.token = FILE_ID, 'synthetic-public-access'
        with self.http_fixture(200, PAYLOAD):
            self.assertEqual(transfer._call('GET', 'www.googleapis.com', '/drive/v3/files/' + FILE_ID + '?alt=media',
                                            binary=True), PAYLOAD)
        with self.http_fixture(200, PAYLOAD + b'x'), self.assertRaises(remote.RemoteTestError) as error:
            transfer._call('GET', 'www.googleapis.com', '/drive/v3/files/' + FILE_ID + '?alt=media', binary=True)
        self.assertEqual(error.exception.code, 'download')
        self.assertEqual(transfer.safe_diagnostic(error.exception)['nature'], 'size_limit')

    def test_http_status_and_json_failure_are_sanitized(self):
        transfer = self.transfer()
        transfer.file_id, transfer.token = FILE_ID, 'synthetic-public-access'
        transfer.set_stage('cleanup_metadata')
        with self.http_fixture(403, b'private-provider-body'), self.assertRaises(remote.RemoteTestError) as error:
            transfer.metadata()
        self.assertEqual(transfer.safe_diagnostic(error.exception), {
            'code': 'quota', 'stage': 'cleanup_metadata', 'nature': 'http', 'http_status': 403,
        })
        transfer.set_stage('metadata_before')
        with self.http_fixture(200, b'private-not-json'), self.assertRaises(remote.RemoteTestError) as error:
            transfer.metadata()
        self.assertEqual(transfer.safe_diagnostic(error.exception), {
            'code': 'network', 'stage': 'metadata_before', 'nature': 'json', 'http_status': 200,
        })

    def test_transport_exception_text_never_escapes(self):
        transfer = self.transfer()
        transfer.file_id, transfer.token = FILE_ID, 'synthetic-public-access'
        transfer.set_stage('download')
        for failure, nature in ((TimeoutError('private-timeout-details'), 'timeout'),
                                (remote.ssl.SSLError('private-tls-details'), 'tls'),
                                (OSError('private-host-details'), 'transport')):
            with self.subTest(nature=nature), patch.object(remote.http.client, 'HTTPSConnection') as https:
                https.return_value.request.side_effect = failure
                with self.assertRaises(remote.RemoteTestError) as error:
                    transfer._call('GET', 'www.googleapis.com', '/drive/v3/files/' + FILE_ID + '?alt=media', binary=True)
                diagnostic = transfer.safe_diagnostic(error.exception)
                self.assertEqual(diagnostic, {'code': 'network', 'stage': 'download', 'nature': nature})
                self.assertNotIn('private', json.dumps(diagnostic))

    def test_delete_accepts_empty_success_only(self):
        transfer = self.transfer()
        transfer.file_id, transfer.token = FILE_ID, 'synthetic-public-access'
        for status, content in ((204, b''), (200, b''), (200, b'{}')):
            with self.subTest(status=status), self.http_fixture(status, content):
                self.assertEqual(transfer._call('DELETE', 'www.googleapis.com', '/drive/v3/files/' + FILE_ID), {})
        with self.http_fixture(200, b'{"unexpected":true}'), self.assertRaises(remote.RemoteTestError):
            transfer._call('DELETE', 'www.googleapis.com', '/drive/v3/files/' + FILE_ID)

    def test_redirect_and_provider_diagnostics_never_escape(self):
        transfer = self.transfer()
        transfer.file_id, transfer.token = FILE_ID, 'synthetic-public-access'
        with self.http_fixture(302, b'sensitive-provider-message'), self.assertRaises(remote.RemoteTestError) as error:
            transfer.metadata()
        self.assertEqual(str(error.exception), 'network')
        self.assertNotIn('sensitive', str(error.exception))

    def test_not_found_requires_explicit_cleanup_mode(self):
        transfer = self.transfer()
        transfer.file_id, transfer.token = FILE_ID, 'synthetic-public-access'
        with self.http_fixture(404, b'not-public'), self.assertRaises(remote.RemoteTestError):
            transfer.metadata()
        with self.http_fixture(404, b'not-public'):
            self.assertIsNone(transfer.metadata(missing=True))


class RemoteBridgeTests(SimpleTestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory(prefix='synthetic-bridge-unit-'))
        self.root = Path(self.tmp).resolve()
        self.enterContext(patch.object(remote, 'ROOT', self.root))
        self.directory = self.root / 'tmp' / ('p0a-drive-remote-bridge-' + RUN)
        self.directory.mkdir(parents=True)
        source = self.root / 'tmp' / 'p0a-drive-local-unit' / 'fake-drive' / 'synthetic.tar.gpg'
        source.parent.mkdir(parents=True)
        source.write_bytes(PAYLOAD)
        self.request = dict(run_id=RUN, scope='synthetic_database_only', cipher_path=str(source),
                            cipher_sha256=hashlib.sha256(PAYLOAD).hexdigest(), cipher_size=len(PAYLOAD))
        remote.write_json(self.directory / 'request.json', self.request)

    def native(self, **changes):
        return dict(status='PASS', checks=NATIVE_CHECKS.copy(), ciphertext_sha256=self.request['cipher_sha256'],
                    native_postgresql_major=17, synthetic_row_count=10,
                    database_objects_compared={'tables': 6, 'sequences': 6, 'indexes': 12, 'constraints': 20}) | changes

    def clear_test_outputs(self):
        # Only exact files owned by this test's disposable bridge are removed.
        for name in ('downloaded.gpg', 'download.ready.json', 'native-result.json',
                     'remote-result.json', 'abort.json'):
            (self.directory / name).unlink(missing_ok=True)

    def test_loads_only_owned_pinned_ciphertext_and_never_a_phrase(self):
        bridge = remote.SyntheticBridge(self.directory)
        self.assertEqual(bridge.payload, PAYLOAD)
        self.assertNotIn('passphrase', json.dumps(bridge.public_result()))

    def test_rejects_wrong_schema_run_hash_plaintext_and_preexisting_outputs(self):
        original = self.directory / 'request.json'
        for change in ({'run_id': '2' * 32}, {'cipher_sha256': '0' * 64}, {'unexpected': 'field'}):
            original.write_text(json.dumps(self.request | change))
            with self.assertRaises(remote.RemoteTestError):
                remote.SyntheticBridge(self.directory)
        original.write_text(json.dumps(self.request))
        (self.directory / 'abort.json').write_text('{}')
        with self.assertRaises(remote.RemoteTestError):
            remote.SyntheticBridge(self.directory)

    def test_json_publication_is_atomic_no_overwrite_and_no_temporary_residue(self):
        path = self.directory / 'test.json'
        actual_replace = remote.os.replace
        def observe(source, target):
            self.assertFalse(path.exists())
            self.assertEqual(json.loads(Path(source).read_text()), {'ok': True})
            return actual_replace(source, target)
        with patch.object(remote.os, 'replace', side_effect=observe) as replace:
            remote.write_json(path, {'ok': True})
        replace.assert_called_once()
        with self.assertRaises(remote.RemoteTestError):
            remote.write_json(path, {'ok': False})
        self.assertFalse(list(self.directory.glob('*.tmp')))

    def run_bridge(self, *, native=None, interrupt=False, cleanup_failure=False):
        bridge = remote.SyntheticBridge(self.directory)
        self.last_bridge = bridge
        transfer = remote.TestTransfer(CONFIG, RUN, PAYLOAD)
        transfer.file_id, transfer.attempted = FILE_ID, True
        def download():
            if interrupt:
                raise KeyboardInterrupt()
            remote.write_json(self.directory / 'native-result.json', native or {'run_id': RUN, 'result': self.native()})
            return PAYLOAD
        def cleanup():
            transfer.set_stage('delete')
            if cleanup_failure:
                transfer.http_status, transfer.failure_nature = 403, 'http'
                raise remote.RemoteTestError('quota')
            return 'PASS'
        with patch.object(transfer, 'upload_and_download', side_effect=download), \
                patch.object(transfer, 'cleanup', side_effect=cleanup), \
                patch.object(remote, 'TestTransfer', return_value=transfer):
            if interrupt:
                with self.assertRaises(KeyboardInterrupt):
                    bridge.run(CONFIG)
            else:
                bridge.run(CONFIG)
        return bridge

    def test_success_requires_native_restore_and_remote_cleanup(self):
        bridge = self.run_bridge()
        self.assertEqual(bridge.public_status(), 'PASS')
        self.assertEqual(bridge.public_result()['postgresql_major'], 17)
        self.assertFalse((self.directory / 'downloaded.gpg').exists())
        with self.assertRaises(remote.RemoteTestError):
            bridge.run(CONFIG)

    def test_postgresql_18_accepts_only_its_44_constraint_fixture(self):
        value = self.native(native_postgresql_major=18, database_objects_compared={
            'tables': 6, 'sequences': 6, 'indexes': 12, 'constraints': 44,
        })
        bridge = self.run_bridge(native={'run_id': RUN, 'result': value})
        self.assertEqual(bridge.public_status(), 'PASS')
        self.assertEqual(bridge.public_result()['postgresql_major'], 18)
        self.assertEqual(bridge.public_result()['checks']['indexes_constraints_sequences_match'], 'PASS')

    def test_constraint_counts_cannot_be_swapped_between_postgresql_17_and_18(self):
        for major, constraints in ((18, 20), (17, 44)):
            with self.subTest(major=major, constraints=constraints):
                self.clear_test_outputs()
                value = self.native(native_postgresql_major=major, database_objects_compared={
                    'tables': 6, 'sequences': 6, 'indexes': 12, 'constraints': constraints,
                })
                bridge = self.run_bridge(native={'run_id': RUN, 'result': value})
                self.assertEqual(bridge.public_status(), 'FAIL')
                self.assertEqual(bridge.public_result()['primary_error']['code'], 'native_restore')
                self.assertNotIn('postgresql_major', bridge.public_result())

    def test_major_is_an_exact_supported_integer_before_count_mapping_lookup(self):
        for major in (True, False, 18.0, '18', None, [18], {'untrusted': 18}, 19):
            with self.subTest(value_type=type(major).__name__):
                self.clear_test_outputs()
                value = self.native(native_postgresql_major=major)
                bridge = self.run_bridge(native={'run_id': RUN, 'result': value})
                result = bridge.public_result()
                self.assertEqual(result['status'], 'FAIL')
                self.assertEqual(result['primary_error']['code'], 'native_restore')
                self.assertEqual(result['primary_error']['stage'], 'native_restore')
                self.assertNotIn('postgresql_major', result)
                self.assertNotIn('untrusted', json.dumps(result))

    def test_object_count_metadata_requires_exact_keys_integer_types_and_fixed_counts(self):
        expected = {'tables': 6, 'sequences': 6, 'indexes': 12, 'constraints': 44}
        invalid = [None, [], 'untrusted-object-counts', expected | {'untrusted': 1}]
        for name, count in expected.items():
            invalid.extend(expected | {name: value} for value in (True, float(count), str(count), None, count + 1))
        for compared in invalid:
            with self.subTest(value_type=type(compared).__name__):
                self.clear_test_outputs()
                value = self.native(native_postgresql_major=18, database_objects_compared=compared)
                bridge = self.run_bridge(native={'run_id': RUN, 'result': value})
                result = bridge.public_result()
                self.assertEqual(result['status'], 'FAIL')
                self.assertEqual(result['primary_error']['code'], 'native_restore')
                self.assertNotIn('postgresql_major', result)
                self.assertNotIn('untrusted', json.dumps(result))

    def test_native_proof_and_row_metadata_reject_untrusted_types(self):
        values = [None, [], 'untrusted-proof', self.native(synthetic_row_count=True),
                  self.native(synthetic_row_count=10.0), self.native(checks=['untrusted-checks'])]
        for value in values:
            with self.subTest(value_type=type(value).__name__):
                self.clear_test_outputs()
                bridge = self.run_bridge(native={'run_id': RUN, 'result': value})
                result = bridge.public_result()
                self.assertEqual(result['status'], 'FAIL')
                self.assertEqual(result['primary_error']['code'], 'native_restore')
                self.assertNotIn('postgresql_major', result)
                self.assertNotIn('synthetic_row_count', result)
                self.assertNotIn('untrusted', json.dumps(result))

    def test_wrong_native_run_cannot_qualify_or_copy_untrusted_public_fields(self):
        bridge = self.run_bridge(native={'run_id': '2' * 32, 'result': self.native()})
        self.assertEqual(bridge.public_status(), 'FAIL')
        self.assertNotIn('postgresql_major', bridge.public_result())

    def test_native_public_fields_require_exact_types(self):
        value = self.native()
        value['native_postgresql_major'] = 'sensitive-native-value-not-publishable'
        bridge = self.run_bridge(native={'run_id': RUN, 'result': value})
        self.assertEqual(bridge.public_status(), 'FAIL')
        self.assertNotIn('sensitive-native-value', json.dumps(bridge.public_result()))

    def test_native_restore_must_explicitly_use_the_downloaded_ciphertext(self):
        value = self.native()
        value['checks'].pop('remote_download_used_for_restoration')
        bridge = self.run_bridge(native={'run_id': RUN, 'result': value})
        self.assertEqual(bridge.public_status(), 'FAIL')

    def test_interruption_never_produces_false_pass_and_signals_worker_abort(self):
        bridge = self.run_bridge(interrupt=True)
        self.assertEqual(bridge.public_status(), 'FAIL')
        self.assertEqual(remote.read_json(self.directory / 'abort.json'), {'run_id': RUN, 'status': 'abort'})
        self.assertEqual(bridge.public_result()['primary_error']['code'], 'aborted')
        self.assertEqual(bridge.public_result()['primary_error']['nature'], 'interrupted')

    def test_transfer_and_cleanup_failures_are_both_preserved_without_secret_values(self):
        bridge = remote.SyntheticBridge(self.directory)
        transfer = remote.TestTransfer(CONFIG, RUN, PAYLOAD)
        transfer.file_id, transfer.attempted = FILE_ID, True
        def download():
            transfer.checks['id_preallocated'] = 'PASS'
            transfer.set_stage('metadata_before')
            transfer.http_status = 200
            transfer.verify(dict(id=FILE_ID, name='private-unexpected-file', appProperties={},
                                 permissions=[{'emailAddress': 'private@example.invalid'}]))
        def cleanup():
            transfer.set_stage('cleanup_metadata')
            transfer.http_status, transfer.failure_nature = 403, 'http'
            raise remote.RemoteTestError('quota')
        with patch.object(transfer, 'upload_and_download', side_effect=download), \
                patch.object(transfer, 'cleanup', side_effect=cleanup), \
                patch.object(remote, 'TestTransfer', return_value=transfer):
            bridge.run(CONFIG)
        result = bridge.public_result()
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['error_code'], 'metadata')
        self.assertEqual(result['primary_error']['code'], 'metadata')
        self.assertEqual(result['primary_error']['stage'], 'metadata_before')
        self.assertEqual(result['primary_error']['http_status'], 200)
        self.assertFalse(result['primary_error']['metadata_flags']['name_match'])
        self.assertEqual(result['cleanup_error'], {
            'code': 'quota', 'stage': 'cleanup_metadata', 'nature': 'http', 'http_status': 403,
        })
        self.assertEqual(result['checks']['id_preallocated'], 'PASS')
        self.assertEqual(result['checks']['remote_file_cleanup'], 'FAIL')
        self.assertNotIn('private', json.dumps(result))
        self.assertEqual(remote.read_json(self.directory / 'remote-result.json'), result)

    def test_cleanup_failure_alone_is_not_reported_as_primary_failure(self):
        bridge = self.run_bridge(cleanup_failure=True)
        result = bridge.public_result()
        self.assertEqual(result['status'], 'FAIL')
        self.assertNotIn('primary_error', result)
        self.assertEqual(result['error_code'], 'quota')
        self.assertEqual(result['cleanup_error']['stage'], 'delete')

    def test_local_cleanup_failure_cannot_replace_the_native_failure(self):
        unlink = Path.unlink
        def fail_download_only(path, *args, **kwargs):
            if path.name == 'downloaded.gpg':
                raise OSError('private-local-path-diagnostic')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', new=fail_download_only):
            bridge = self.run_bridge(native={'run_id': '2' * 32, 'result': self.native()})
        result = bridge.public_result()
        self.assertEqual(result['error_code'], 'native_restore')
        self.assertEqual(result['primary_error']['stage'], 'native_restore')
        self.assertEqual(result['cleanup_error']['code'], 'cleanup')
        self.assertEqual(result['cleanup_error']['stage'], 'local_cleanup')
        self.assertNotIn('private-local-path-diagnostic', json.dumps(result))

    def test_report_write_failure_cannot_replace_existing_primary_and_cleanup_errors(self):
        write_json = remote.write_json
        def fail_report_only(path, value):
            if path.name == 'remote-result.json':
                raise OSError('private-report-path-diagnostic')
            return write_json(path, value)
        with patch.object(remote, 'write_json', side_effect=fail_report_only), \
                self.assertRaises(remote.RemoteTestError):
            self.run_bridge(native={'run_id': '2' * 32, 'result': self.native()}, cleanup_failure=True)
        # The report cannot be written, but the only public exception is a code.
        self.assertFalse((self.directory / 'remote-result.json').exists())
        result = self.last_bridge.public_result()
        self.assertEqual(result['error_code'], 'native_restore')
        self.assertEqual(result['primary_error']['code'], 'native_restore')
        self.assertEqual(result['cleanup_error']['code'], 'quota')
        self.assertEqual(result['report_error']['code'], 'bridge')
        self.assertNotIn('private-report-path-diagnostic', json.dumps(result))

    def test_cancel_waiting_test_only_writes_fixed_abort_marker(self):
        bridge = remote.SyntheticBridge(self.directory)
        bridge.cancel_if_incomplete()
        self.assertEqual(remote.read_json(self.directory / 'abort.json'), {'run_id': RUN, 'status': 'abort'})
