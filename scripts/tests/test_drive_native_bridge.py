"""Filesystem-only bridge tests: public fixtures, no Google or native processes."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import validate_drive_backup_local as worker


class NativeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='public-synthetic-native-bridge-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.root_patch = patch.object(worker, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        (self.root / 'tmp').mkdir()
        self.run_id = '0123456789abcdef' * 2
        self.bridge = worker.prepare_remote_bridge(self.root / 'tmp' / (worker.BRIDGE_PREFIX + self.run_id))
        self.runtime = self.root / 'tmp' / 'p0a-drive-local-public-fixture'
        self.sink = self.runtime / 'fake-drive'
        self.sink.mkdir(parents=True)
        self.ciphertext = self.sink / 'public-synthetic.tar.gpg'
        self.content = b'\x8cPUBLIC SYNTHETIC CIPHERTEXT FIXTURE ONLY'
        self.ciphertext.write_bytes(self.content)

    def request(self):
        return worker.publish_bridge_request(self.bridge, self.runtime, self.ciphertext)

    def ready(self, request, *, content=None, overrides=None):
        (self.bridge / 'downloaded.gpg').write_bytes(self.content if content is None else content)
        value = {'run_id': request['run_id'], 'sha256': request['cipher_sha256'],
                 'size': request['cipher_size'], 'source': 'google_drive'}
        value.update(overrides or {})
        (self.bridge / 'download.ready.json').write_text(json.dumps(value), encoding='utf-8')

    def assert_bridge_failure(self, function, code):
        with self.assertRaises(worker.RemoteBridgeFailure) as caught:
            function()
        self.assertEqual(str(caught.exception), code)

    def test_new_direct_child_directory_and_existing_paths_are_not_adopted(self):
        self.assertEqual(self.bridge.parent, self.root / 'tmp')
        self.assertEqual(self.bridge.resolve(), self.bridge)
        self.assert_bridge_failure(lambda: worker.prepare_remote_bridge(self.bridge),
                                   'remote_bridge_already_exists')
        occupied = self.root / 'tmp' / (worker.BRIDGE_PREFIX + 'a' * 32)
        occupied.write_text('public fixture', encoding='utf-8')
        self.assert_bridge_failure(lambda: worker.prepare_remote_bridge(occupied),
                                   'remote_bridge_already_exists')

    def test_wrong_names_nesting_and_outside_paths_are_refused(self):
        for path in (self.root / (worker.BRIDGE_PREFIX + 'a' * 32),
                     self.root / 'tmp' / 'arbitrary-existing-folder',
                     self.root / 'tmp' / (worker.BRIDGE_PREFIX + 'A' * 32),
                     self.bridge / (worker.BRIDGE_PREFIX + 'b' * 32),
                     self.root / 'tmp' / '..' / 'tmp' / (worker.BRIDGE_PREFIX + 'c' * 32)):
            with self.subTest(path=path.name):
                self.assert_bridge_failure(lambda: worker.prepare_remote_bridge(path),
                                           'invalid_remote_bridge_directory')

    def test_linked_bridge_or_tmp_directory_is_refused(self):
        original = worker._is_link
        for linked in (self.bridge, self.root / 'tmp'):
            with self.subTest(linked=linked.name), patch.object(
                    worker, '_is_link', side_effect=lambda path: Path(path) == linked or original(Path(path))):
                self.assert_bridge_failure(lambda: worker.wait_for_remote_download(self.bridge, {}),
                                           'invalid_remote_bridge_directory')

    def test_request_schema_is_exact_canonical_and_contains_no_credentials(self):
        request = self.request()
        self.assertEqual(set(request), {'run_id', 'scope', 'cipher_path', 'cipher_sha256', 'cipher_size'})
        self.assertEqual(request['run_id'], self.run_id)
        self.assertEqual(request['scope'], 'synthetic_database_only')
        self.assertEqual(request['cipher_path'], str(self.ciphertext.resolve()))
        self.assertEqual(request['cipher_sha256'], worker.digest(self.ciphertext))
        self.assertEqual(request['cipher_size'], len(self.content))
        self.assertEqual(json.loads((self.bridge / 'request.json').read_text(encoding='utf-8')), request)
        self.assertEqual(list(self.bridge.glob('*.pending')), [])
        self.assertNotIn('token', json.dumps(request))
        self.assertNotIn('passphrase', json.dumps(request))

    def test_request_output_cannot_be_overwritten(self):
        request = self.request()
        self.assert_bridge_failure(lambda: worker.write_bridge_json(self.bridge, 'request.json', request),
                                   'remote_bridge_output_already_exists')
        self.assert_bridge_failure(lambda: worker.write_bridge_json(self.bridge, '../other.json', request),
                                   'invalid_remote_bridge_output')

    def test_json_is_fsynced_then_atomically_published_from_unique_temporary(self):
        events, temporaries = [], []
        original_fsync, original_replace = worker.os.fsync, worker.os.replace

        def synced(descriptor):
            events.append('fsync')
            return original_fsync(descriptor)

        def published(source, destination):
            source, destination = Path(source), Path(destination)
            self.assertEqual(events[-1], 'fsync')
            self.assertEqual(source.parent, self.bridge)
            self.assertFalse(destination.exists())
            self.assertEqual(json.loads(source.read_text(encoding='utf-8')), {'public': destination.name})
            temporaries.append(source)
            events.append('publish')
            return original_replace(source, destination)

        with patch.object(worker.os, 'fsync', side_effect=synced), \
                patch.object(worker.os, 'replace', side_effect=published):
            for name in ('request.json', 'native-result.json'):
                worker.write_bridge_json(self.bridge, name, {'public': name})
        self.assertEqual(events, ['fsync', 'publish', 'fsync', 'publish'])
        self.assertEqual(len(set(temporaries)), 2)
        self.assertTrue(all(not path.exists() for path in temporaries))
        self.assertEqual(list(self.bridge.glob('*.pending')), [])

    def test_failed_atomic_publication_leaves_no_partial_final_json(self):
        with patch.object(worker.os, 'replace', side_effect=OSError('synthetic publication failure')):
            with self.assertRaises(OSError):
                worker.write_bridge_json(self.bridge, 'request.json', {'public': 'fixture'})
        self.assertFalse((self.bridge / 'request.json').exists())
        self.assertEqual(list(self.bridge.glob('*.pending')), [])

    def test_ciphertext_must_be_in_this_runtime_fake_drive(self):
        outside = self.root / 'tmp' / 'other.gpg'
        outside.write_bytes(self.content)
        for source in (outside, self.sink / '..' / 'fake-drive' / self.ciphertext.name):
            with self.subTest(source=str(source)):
                self.assert_bridge_failure(lambda: worker.publish_bridge_request(self.bridge, self.runtime, source),
                                           'invalid_remote_bridge_ciphertext')

    def test_linked_source_file_or_parent_is_refused(self):
        original = worker._is_link
        for linked in (self.runtime, self.sink, self.ciphertext):
            with self.subTest(linked=linked.name), patch.object(
                    worker, '_is_link', side_effect=lambda path: Path(path) == linked or original(Path(path))):
                with self.assertRaises(worker.RemoteBridgeFailure):
                    worker.publish_bridge_request(self.bridge, self.runtime, self.ciphertext)

    def test_hardlinked_source_file_is_refused(self):
        os.link(self.ciphertext, self.sink / 'other-public-fixture.gpg')
        self.assert_bridge_failure(self.request, 'invalid_remote_bridge_file')

    def test_exact_ready_returns_download_not_original_source(self):
        request = self.request()
        self.ready(request)
        with patch.object(worker.time, 'sleep') as sleep:
            downloaded = worker.wait_for_remote_download(self.bridge, request)
        self.assertEqual(downloaded, self.bridge / 'downloaded.gpg')
        self.assertNotEqual(downloaded, self.ciphertext)
        self.assertEqual(downloaded.read_bytes(), self.content)
        sleep.assert_not_called()

    def test_ready_schema_run_hash_size_and_source_are_strict(self):
        request = self.request()
        for changes in ({'run_id': 'f' * 32}, {'sha256': '0' * 64}, {'size': True},
                        {'size': request['cipher_size'] + 1}, {'source': 'local_copy'},
                        {'unexpected': 'public fixture'}):
            with self.subTest(changes=changes):
                self.ready(request, overrides=changes)
                self.assert_bridge_failure(lambda: worker.wait_for_remote_download(self.bridge, request),
                                           'invalid_remote_bridge_ready')

    def test_source_is_never_used_when_download_is_missing(self):
        request = self.request()
        self.ready(request)
        (self.bridge / 'downloaded.gpg').unlink()
        self.assertTrue(self.ciphertext.is_file())
        self.assert_bridge_failure(lambda: worker.wait_for_remote_download(self.bridge, request),
                                   'invalid_remote_bridge_file')

    def test_altered_download_and_oversize_download_are_refused(self):
        request = self.request()
        self.ready(request, content=b'x' * len(self.content))
        self.assert_bridge_failure(lambda: worker.wait_for_remote_download(self.bridge, request),
                                   'remote_bridge_ciphertext_mismatch')
        with (self.bridge / 'downloaded.gpg').open('wb') as stream:
            stream.truncate(worker.BRIDGE_MAX_CIPHER_BYTES + 1)
        self.assert_bridge_failure(lambda: worker.wait_for_remote_download(self.bridge, request),
                                   'invalid_remote_bridge_file')

    def test_download_link_and_hardlink_are_refused(self):
        request = self.request()
        self.ready(request)
        downloaded = self.bridge / 'downloaded.gpg'
        original = worker._is_link
        with patch.object(worker, '_is_link', side_effect=lambda path: Path(path) == downloaded or original(Path(path))):
            self.assert_bridge_failure(lambda: worker.wait_for_remote_download(self.bridge, request),
                                       'invalid_remote_bridge_file')
        downloaded.unlink()
        os.link(self.ciphertext, downloaded)
        self.assert_bridge_failure(lambda: worker.wait_for_remote_download(self.bridge, request),
                                   'invalid_remote_bridge_file')

    def test_abort_schema_is_fixed_and_takes_precedence(self):
        request = self.request()
        self.ready(request)
        abort = self.bridge / 'abort.json'
        abort.write_text(json.dumps({'run_id': self.run_id, 'status': 'abort'}), encoding='utf-8')
        self.assert_bridge_failure(lambda: worker.wait_for_remote_download(self.bridge, request),
                                   'remote_bridge_aborted')
        abort.write_text(json.dumps({'run_id': self.run_id, 'status': 'abort',
                                     'error': 'not permitted as free-form output'}), encoding='utf-8')
        self.assert_bridge_failure(lambda: worker.wait_for_remote_download(self.bridge, request),
                                   'invalid_remote_bridge_abort')

    def test_duplicate_json_keys_and_non_object_ready_are_refused(self):
        request = self.request()
        ready = self.bridge / 'download.ready.json'
        for text in ('{"run_id":"public","run_id":"duplicate"}', '[]', '{incomplete'):
            with self.subTest(text=text):
                ready.write_text(text, encoding='utf-8')
                self.assert_bridge_failure(lambda: worker.wait_for_remote_download(self.bridge, request),
                                           'invalid_remote_bridge_json')

    def test_linked_ready_or_abort_is_refused_before_reading(self):
        request = self.request()
        original = worker._is_link
        for name in ('download.ready.json', 'abort.json'):
            linked = self.bridge / name
            with self.subTest(name=name), patch.object(
                    worker, '_is_link', side_effect=lambda path: Path(path) == linked or original(Path(path))):
                self.assert_bridge_failure(lambda: worker.wait_for_remote_download(self.bridge, request),
                                           'invalid_remote_bridge_file')

    def test_invalid_request_metadata_is_refused(self):
        request = self.request()
        for changes in ({'cipher_size': True}, {'cipher_size': worker.BRIDGE_MAX_CIPHER_BYTES + 1},
                        {'cipher_sha256': 'bad'}, {'run_id': '0' * 32}, {'scope': 'anything_else'},
                        {'extra': 'public fixture'}):
            with self.subTest(changes=changes):
                self.assert_bridge_failure(
                    lambda: worker.wait_for_remote_download(self.bridge, request | changes),
                    'invalid_remote_bridge_request')

    def test_wait_timeout_is_bounded_and_does_not_contact_anything(self):
        request = self.request()
        for timeout in (0, -1, 1201, True, float('inf'), float('nan'), '1200'):
            with self.subTest(timeout=timeout):
                self.assert_bridge_failure(lambda: worker.wait_for_remote_download(self.bridge, request, timeout=timeout),
                                           'invalid_remote_bridge_timeout')
        with patch.object(worker.time, 'monotonic', side_effect=[0, 1201]), patch.object(worker.time, 'sleep') as sleep:
            self.assert_bridge_failure(lambda: worker.wait_for_remote_download(self.bridge, request),
                                       'remote_bridge_timeout')
        sleep.assert_not_called()

    def test_native_result_is_published_only_after_validation_cleanup(self):
        bridge = self.root / 'tmp' / (worker.BRIDGE_PREFIX + 'c' * 32)
        events = []
        result = {'status': 'PASS', 'checks': {'temporary_runtime_removed': 'PASS'}}

        def synthetic_run(*args, **kwargs):
            worker.prepare_remote_bridge(kwargs['remote_bridge'])
            owned_runtime = self.root / 'tmp' / 'p0a-drive-local-main-fixture'
            owned_runtime.mkdir()
            owned_runtime.rmdir()
            events.append('runtime_cleaned')
            return result

        original_write = worker.write_bridge_json

        def checked_write(directory, name, value):
            self.assertEqual(events, ['runtime_cleaned'])
            self.assertEqual(name, 'native-result.json')
            return original_write(directory, name, value)

        argv = ['validate_drive_backup_local.py', '--postgres-bin', 'public-placeholder',
                '--gpg-bin', 'public-placeholder', '--openssl-bin', 'public-placeholder',
                '--remote-bridge', str(bridge)]
        with patch.object(worker.sys, 'argv', argv), patch.object(worker, 'run_validation', side_effect=synthetic_run), \
                patch.object(worker, 'write_bridge_json', side_effect=checked_write), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(worker.main(), 0)
        final = json.loads((bridge / 'native-result.json').read_text(encoding='utf-8'))
        self.assertEqual(set(final), {'run_id', 'result'})
        self.assertEqual(final, {'run_id': 'c' * 32, 'result': result})
        self.assertTrue(bridge.is_dir())


if __name__ == '__main__':
    unittest.main()
