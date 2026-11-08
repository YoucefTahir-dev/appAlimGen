"""One known synthetic test object only. No upload, listing or file download.

This recovery tool is pinned to the failed, explicitly authorized synthetic run.
It reads only its public proof, not the removed ciphertext, OAuth vault or ERP.
A fresh browser consent in the isolated launcher is required before any request.
"""
import argparse
from pathlib import Path
import sys
import time
from urllib.parse import urlencode
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.dont_write_bytecode = True
from scripts import validate_drive_backup_remote as remote


RUN = '9213e3e4232c4608ab148910e51d2735'
FILE_ID = '1JAo5IsVK2V1IAx4tlJiG616BblyjYxrh'
SHA256 = 'b48370d2520338e5652e4fb3ffec8cf23da06b67d1ff29edc1ee0ca9964e0b34'
SIZE = 20570


class CleanupTransfer(remote.TestTransfer):
    """No payload and no caller-selected target; allowlist is narrower than test."""
    def __init__(self, config):
        self.config, self.run_id = config, RUN
        self.size, self.sha = SIZE, SHA256
        self.name = 'p0-a-SYNTHETIC-' + RUN + '.tar.gpg'
        self.marker = {'p0aSyntheticRun': RUN, 'scope': 'synthetic-database-only'}
        self.file_id, self.token = FILE_ID, ''
        self.attempted = True
        self.until = time.monotonic() + 60
        self.checks = {}
        self.set_stage('refresh')

    def _allowed(self, method, host, path):
        if host == 'oauth2.googleapis.com':
            return method == 'POST' and path == '/token'
        if host != 'www.googleapis.com':
            return False
        base = '/drive/v3/files/' + FILE_ID
        return ((method == 'GET' and path in {
            '/drive/v3/about?' + urlencode({'fields': 'user(permissionId)'}),
            base + '?' + urlencode({'fields': remote.FIELDS}),
        }) or (method == 'DELETE' and path == base))

    def upload_and_download(self):
        raise remote.RemoteTestError('used')

    def verify(self, value, *, cleanup=False):
        # Deleting our exact old object is not a privacy/integrity qualification.
        # Permissions/trashed flags remain diagnostic, not proof of provenance.
        flags = self.metadata_diagnostics(value)
        self.metadata_flags = flags
        self.provenance_flags = flags.copy()
        # The real diagnostic proved a non-default MIME with the exact SHA.
        # Do not guess a second short MIME allowlist; require a stored blob and
        # the complete expected ciphertext digest on the fresh GET before DELETE.
        if (not cleanup or not all(flags[name] for name in (
                'object_valid', 'id_match', 'name_match', 'marker_match', 'owned_by_me', 'size_match',
                'stored_blob_type', 'hash_present', 'hash_format_valid', 'hash_match'))):
            raise remote.RemoteTestError('metadata')
        self.checks['pinned_synthetic_provenance'] = 'PASS'
        self.checks['server_sha256'] = 'PASS'


class CleanupAttempt:
    cleanup_only = True

    def __init__(self):
        directory = remote.ROOT / 'tmp' / ('p0a-drive-remote-bridge-' + RUN)
        if not remote.unlinked(directory) or directory.resolve().parent != (remote.ROOT / 'tmp').resolve():
            raise remote.RemoteTestError('bridge')
        request = remote.read_json(directory / 'request.json', 8192)
        previous = remote.read_json(directory / 'remote-result.json')
        native = remote.read_json(directory / 'native-result.json')
        # Source path is checked as a string only, NEVER opened or followed.
        if (set(request) != {'run_id', 'scope', 'cipher_path', 'cipher_sha256', 'cipher_size'} or
                request['run_id'] != RUN or request['scope'] != 'synthetic_database_only' or
                request['cipher_sha256'] != SHA256 or type(request['cipher_size']) is not int or
                request['cipher_size'] != SIZE or not isinstance(request['cipher_path'], str) or
                previous.get('test_file_id') != FILE_ID or previous.get('cipher_sha256') != SHA256 or
                type(previous.get('cipher_size')) is not int or previous['cipher_size'] != SIZE or
                previous.get('status') != 'FAIL' or previous.get('production_access') is not False or
                previous.get('media_included') is not False or previous.get('scope') != 'synthetic_database_only' or
                set(native) != {'run_id', 'result'} or native['run_id'] != RUN or
                not isinstance(native['result'], dict) or
                native['result'].get('ciphertext_sha256') != SHA256 or
                not isinstance(native['result'].get('checks'), dict) or
                native['result']['checks'].get('temporary_runtime_removed') != 'PASS'):
            raise remote.RemoteTestError('bridge')
        self.directory, self.used = directory, False
        self.result = self._base('WAITING')

    def _base(self, status):
        return dict(status=status, scope='synthetic_cleanup_only', production_access=False,
                    media_included=False, upload_operations=0, download_operations=0,
                    test_file_id=FILE_ID)

    def public_status(self):
        return self.result['status']

    def public_result(self):
        return dict(self.result)

    def cancel_if_incomplete(self):
        pass  # No worker or remote operation is started without the POST.

    def run(self, config):
        if self.used:
            raise remote.RemoteTestError('used')
        self.used = True
        transfer = CleanupTransfer(config)
        self.result = self._base('RUNNING')
        outcome = 'FAIL'
        try:
            transfer.authenticate()
            outcome = transfer.cleanup()
        except Exception as error:
            self.result['cleanup_error'] = transfer.safe_diagnostic(error)
        finally:
            self.result.update(status='PASS' if outcome == 'PASS' else 'FAIL',
                               checks=dict(transfer.checks, remote_file_cleanup=outcome))
            flags = getattr(transfer, 'provenance_flags', None)
            if isinstance(flags, dict):
                # Constructed solely by the fixed-key, boolean-only helper.
                self.result['metadata_flags'] = {name: flags[name] for name in remote.METADATA_FLAG_NAMES
                                                if type(flags.get(name)) is bool}
            remote.write_json(self.directory / ('cleanup-result-' + uuid.uuid4().hex + '.json'), self.result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-file', required=True)
    args = parser.parse_args(argv)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(remote.ROOT))
    optional = remote.ROOT / 'tmp' / 'p0a-drive-remote-dependencies-20261108'
    if optional.is_dir():
        sys.path.insert(0, str(optional))
    from scripts import launch_drive_oauth_local as launcher
    launcher.ORIGIN = 'http://127.0.0.1:8001'
    try:
        attempt = CleanupAttempt()
        client = launcher.read_client(args.client_file)
        launcher.serve(client, synthetic_test=attempt)
        return 0 if attempt.public_status() == 'PASS' else 1
    except KeyboardInterrupt:
        return 1
    except Exception:
        print('SYNTHETIC_CLEANUP_FAILED: no cleanup confirmed.', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
