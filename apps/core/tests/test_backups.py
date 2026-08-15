import os
import sqlite3
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from apps.core import backup as backup_module
from apps.core.backup import (
    BackupError,
    backup_database_to,
    restore_sqlite_file,
    restore_database_from,
    upload_backup_to_object_storage,
    verify_database_backup,
    write_checksum,
)
from apps.core.models import CompanySettings


def create_sqlite_database(path, value):
    database = sqlite3.connect(str(path))
    try:
        database.execute('CREATE TABLE marker (value TEXT NOT NULL)')
        database.execute('INSERT INTO marker (value) VALUES (?)', (value,))
        database.commit()
    finally:
        database.close()


class BackupUtilityTests(SimpleTestCase):
    def test_sqlite_backup_checksum_and_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / 'valid.sqlite3'
            create_sqlite_database(backup, 'valid')
            write_checksum(backup)

            self.assertEqual(verify_database_backup(backup), backup.resolve())

            with backup.open('ab') as stream:
                stream.write(b'tampered')
            with self.assertRaises(BackupError):
                verify_database_backup(backup)

    def test_malformed_or_mismatched_checksum_is_rejected_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / 'valid.sqlite3'
            checksum = backup.with_name(f'{backup.name}.sha256')
            create_sqlite_database(backup, 'valid')

            checksum.write_text('not-a-checksum\n', encoding='ascii')
            with self.assertRaisesRegex(BackupError, 'mal formé'):
                verify_database_backup(backup)

            checksum.write_text(f'{"0" * 64}  another.sqlite3\n', encoding='ascii')
            with self.assertRaisesRegex(BackupError, 'nom de la sauvegarde'):
                verify_database_backup(backup)

    def test_backup_format_is_detected_from_content_not_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            sqlite_with_dump_suffix = Path(directory) / 'database.dump'
            create_sqlite_database(sqlite_with_dump_suffix, 'valid')
            write_checksum(sqlite_with_dump_suffix)

            self.assertEqual(
                verify_database_backup(sqlite_with_dump_suffix),
                sqlite_with_dump_suffix.resolve(),
            )
            with self.assertRaisesRegex(BackupError, 'moteur actif postgresql'):
                verify_database_backup(sqlite_with_dump_suffix, expected_vendor='postgresql')

    def test_restore_sqlite_file_replaces_target_after_integrity_check(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.sqlite3'
            target = Path(directory) / 'target.sqlite3'
            create_sqlite_database(source, 'restored')
            create_sqlite_database(target, 'old')

            restore_sqlite_file(source, target)

            database = sqlite3.connect(str(target))
            try:
                value = database.execute('SELECT value FROM marker').fetchone()[0]
            finally:
                database.close()
            self.assertEqual(value, 'restored')

    def test_restore_sqlite_is_atomic_when_final_replace_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.sqlite3'
            target = Path(directory) / 'target.sqlite3'
            create_sqlite_database(source, 'restored')
            create_sqlite_database(target, 'old')

            with patch('apps.core.backup.os.replace', side_effect=OSError('locked')):
                with self.assertRaises(OSError):
                    restore_sqlite_file(source, target)

            database = sqlite3.connect(str(target))
            try:
                value = database.execute('SELECT value FROM marker').fetchone()[0]
            finally:
                database.close()
            self.assertEqual(value, 'old')
            self.assertEqual(list(Path(directory).glob('.target.sqlite3.*.tmp')), [])

    def test_restore_sqlite_rejects_source_equal_to_target(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'database.sqlite3'
            create_sqlite_database(database, 'value')
            with self.assertRaisesRegex(BackupError, 'doivent être différentes'):
                restore_sqlite_file(database, database)

    def test_restore_sqlite_refuses_active_transaction_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.sqlite3'
            target = Path(directory) / 'target.sqlite3'
            create_sqlite_database(source, 'restored')
            create_sqlite_database(target, 'old')
            Path(f'{target}-wal').write_bytes(b'active')

            with self.assertRaisesRegex(BackupError, 'fichiers transactionnels'):
                restore_sqlite_file(source, target)

            database = sqlite3.connect(str(target))
            try:
                value = database.execute('SELECT value FROM marker').fetchone()[0]
            finally:
                database.close()
            self.assertEqual(value, 'old')

    def test_restore_command_requires_explicit_confirmation(self):
        with self.assertRaises(CommandError):
            call_command('restore_database', 'missing.sqlite3')

    def test_restore_command_requires_temporary_production_gate(self):
        with (
            self.settings(DEBUG=False),
            patch.dict(os.environ, {'ALLOW_DATABASE_RESTORE': ''}, clear=False),
        ):
            with self.assertRaisesRegex(CommandError, 'ALLOW_DATABASE_RESTORE'):
                call_command('restore_database', 'missing.sqlite3', confirm_restore=True)

    def test_backup_refuses_to_overwrite_active_sqlite_database(self):
        with tempfile.TemporaryDirectory() as directory:
            active_database = Path(directory) / 'active.sqlite3'
            create_sqlite_database(active_database, 'production')
            database_settings = {'default': {'NAME': active_database}}
            with (
                patch.object(backup_module, 'connection') as mocked_connection,
                patch.object(backup_module.settings, 'DATABASES', database_settings),
            ):
                mocked_connection.vendor = 'sqlite'
                with self.assertRaisesRegex(BackupError, 'base active'):
                    backup_database_to(active_database)

            database = sqlite3.connect(str(active_database))
            try:
                value = database.execute('SELECT value FROM marker').fetchone()[0]
            finally:
                database.close()
            self.assertEqual(value, 'production')

    def test_object_storage_rejects_plain_http_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / 'database.sqlite3'
            create_sqlite_database(backup, 'value')
            write_checksum(backup)
            environment = {
                'BACKUP_S3_BUCKET': 'private-backups',
                'BACKUP_S3_ENDPOINT_URL': 'http://storage.example.test',
            }
            with patch.dict(os.environ, environment, clear=False):
                with self.assertRaisesRegex(ImproperlyConfigured, 'HTTPS'):
                    upload_backup_to_object_storage(backup)

    def test_object_storage_uploads_backup_and_checksum_with_scoped_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / 'database.sqlite3'
            create_sqlite_database(backup, 'value')
            checksum = write_checksum(backup)
            environment = {
                'BACKUP_S3_BUCKET': 'private-backups',
                'BACKUP_S3_PREFIX': 'erp/database',
                'BACKUP_S3_ENDPOINT_URL': 'https://storage.example.test',
                'BACKUP_S3_REGION_NAME': 'auto',
                'BACKUP_S3_ACCESS_KEY_ID': 'backup-writer',
                'BACKUP_S3_SECRET_ACCESS_KEY': 'backup-secret',
                'BACKUP_S3_SERVER_SIDE_ENCRYPTION': 'AES256',
            }
            with (
                patch.dict(os.environ, environment, clear=True),
                patch('boto3.client') as client_factory,
            ):
                uri = upload_backup_to_object_storage(backup)

            self.assertEqual(uri, 's3://private-backups/erp/database/database.sqlite3')
            client_factory.assert_called_once_with(
                's3',
                endpoint_url='https://storage.example.test',
                region_name='auto',
                aws_access_key_id='backup-writer',
                aws_secret_access_key='backup-secret',
            )
            client = client_factory.return_value
            self.assertEqual(client.upload_file.call_count, 2)
            client.upload_file.assert_any_call(
                str(backup.resolve()),
                'private-backups',
                'erp/database/database.sqlite3',
                ExtraArgs={'ServerSideEncryption': 'AES256'},
            )
            client.upload_file.assert_any_call(
                str(checksum.resolve()),
                'private-backups',
                'erp/database/database.sqlite3.sha256',
                ExtraArgs={'ServerSideEncryption': 'AES256'},
            )

    def test_object_storage_refuses_tampered_local_backup_before_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / 'database.sqlite3'
            create_sqlite_database(backup, 'value')
            write_checksum(backup)
            with backup.open('ab') as stream:
                stream.write(b'tampered')
            with (
                patch.dict(os.environ, {'BACKUP_S3_BUCKET': 'private-backups'}, clear=True),
                patch('boto3.client') as client_factory,
            ):
                with self.assertRaisesRegex(BackupError, 'SHA-256'):
                    upload_backup_to_object_storage(backup)
            client_factory.assert_not_called()


class PostgreSQLBackupTests(SimpleTestCase):
    database_settings = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': 'erp',
            'USER': 'erp_user',
            'PASSWORD': 'secret-value',
            'HOST': 'db.example.test',
            'PORT': '5432',
            'OPTIONS': {
                'sslmode': 'require',
                'channel_binding': 'require',
            },
        }
    }

    @staticmethod
    def _successful_dump(command, **kwargs):
        if command[0] == 'pg_dump-bin':
            output = Path(command[command.index('--file') + 1])
            output.write_bytes(b'PGDMP-mocked-archive')

    def test_postgres_backup_is_validated_and_published_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'database.dump'
            with (
                patch.object(backup_module, 'connection') as mocked_connection,
                patch.object(backup_module.settings, 'DATABASES', self.database_settings),
                patch('apps.core.backup._postgres_binary', side_effect=lambda name, _: f'{name}-bin'),
                patch('apps.core.backup.subprocess.run', side_effect=self._successful_dump) as run,
            ):
                mocked_connection.vendor = 'postgresql'
                self.assertEqual(backup_database_to(output), output.resolve())

            self.assertTrue(output.read_bytes().startswith(b'PGDMP'))
            self.assertTrue(output.with_name(f'{output.name}.sha256').is_file())
            self.assertEqual(run.call_count, 2)
            dump_call = run.call_args_list[0]
            restore_list_call = run.call_args_list[1]
            self.assertEqual(dump_call.args[0][0], 'pg_dump-bin')
            self.assertIn('--list', restore_list_call.args[0])
            self.assertEqual(dump_call.kwargs['env']['PGPASSWORD'], 'secret-value')
            self.assertEqual(dump_call.kwargs['env']['PGCHANNELBINDING'], 'require')
            self.assertNotIn('secret-value', dump_call.args[0])

    def test_invalid_postgres_dump_does_not_replace_existing_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'database.dump'
            output.write_bytes(b'old-valid-copy')

            def invalid_dump(command, **kwargs):
                if command[0] == 'pg_dump-bin':
                    temporary = Path(command[command.index('--file') + 1])
                    temporary.write_bytes(b'invalid')

            with (
                patch.object(backup_module, 'connection') as mocked_connection,
                patch.object(backup_module.settings, 'DATABASES', self.database_settings),
                patch('apps.core.backup._postgres_binary', side_effect=lambda name, _: f'{name}-bin'),
                patch('apps.core.backup.subprocess.run', side_effect=invalid_dump),
            ):
                mocked_connection.vendor = 'postgresql'
                with self.assertRaisesRegex(BackupError, 'format custom'):
                    backup_database_to(output)

            self.assertEqual(output.read_bytes(), b'old-valid-copy')
            self.assertEqual(list(Path(directory).glob('.database.dump.*.tmp')), [])

    def test_postgres_restore_uses_one_transaction_and_connection_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / 'database.dump'
            backup.write_bytes(b'PGDMP-mocked-archive')
            write_checksum(backup)
            with (
                patch.object(backup_module, 'connection') as mocked_connection,
                patch.object(backup_module.settings, 'DATABASES', self.database_settings),
                patch('apps.core.backup._postgres_binary', side_effect=lambda name, _: f'{name}-bin'),
                patch('apps.core.backup.subprocess.run') as run,
            ):
                mocked_connection.vendor = 'postgresql'
                restore_database_from(backup)

            self.assertEqual(run.call_count, 2)
            restore_call = run.call_args_list[1]
            self.assertIn('--single-transaction', restore_call.args[0])
            self.assertNotIn('--dbname', restore_call.args[0])
            self.assertEqual(restore_call.kwargs['env']['PGDATABASE'], 'erp')
            self.assertEqual(restore_call.kwargs['env']['PGSSLMODE'], 'require')
            mocked_connection.close.assert_called_once_with()


class BackupCommandTests(TransactionTestCase):
    def test_backup_command_creates_a_verified_snapshot_and_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'test.sqlite3'
            stdout = StringIO()

            call_command('backup_database', output=str(output), stdout=stdout)

            self.assertTrue(output.is_file())
            self.assertTrue(output.with_name(f'{output.name}.sha256').is_file())
            self.assertEqual(verify_database_backup(output), output.resolve())
            self.assertIn('Sauvegarde validée', stdout.getvalue())


class MediaMigrationCommandTests(TestCase):
    @staticmethod
    def _storage_settings(target_root):
        return {
            'default': {
                'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {
                    'location': str(target_root),
                    'base_url': '/test-media/',
                },
            },
        }

    def test_media_migration_dry_run_then_apply_and_verify_existing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / 'source'
            target_root = root / 'target'
            relative_name = Path('company') / 'logo.png'
            source = source_root / relative_name
            source.parent.mkdir(parents=True)
            source.write_bytes(b'official-logo')
            company = CompanySettings.objects.create(logo=relative_name.as_posix())

            with self.settings(STORAGES=self._storage_settings(target_root)):
                dry_run_output = StringIO()
                call_command(
                    'migrate_media_storage',
                    str(source_root),
                    stdout=dry_run_output,
                )
                self.assertFalse((target_root / relative_name).exists())
                self.assertIn('à copier=1', dry_run_output.getvalue())

                apply_output = StringIO()
                call_command(
                    'migrate_media_storage',
                    str(source_root),
                    apply=True,
                    stdout=apply_output,
                )
                self.assertEqual((target_root / relative_name).read_bytes(), b'official-logo')
                self.assertIn('copiés=1', apply_output.getvalue())

                second_output = StringIO()
                call_command(
                    'migrate_media_storage',
                    str(source_root),
                    apply=True,
                    stdout=second_output,
                )
                self.assertIn('déjà présents=1', second_output.getvalue())

            company.refresh_from_db()
            self.assertEqual(company.logo.name, relative_name.as_posix())

    def test_media_migration_rejects_existing_content_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / 'source'
            target_root = root / 'target'
            relative_name = Path('company') / 'logo.png'
            source = source_root / relative_name
            target = target_root / relative_name
            source.parent.mkdir(parents=True)
            target.parent.mkdir(parents=True)
            source.write_bytes(b'new-logo')
            target.write_bytes(b'different-logo')
            CompanySettings.objects.create(logo=relative_name.as_posix())

            with self.settings(STORAGES=self._storage_settings(target_root)):
                with self.assertRaisesRegex(CommandError, 'contenu différent'):
                    call_command('migrate_media_storage', str(source_root))

            self.assertEqual(target.read_bytes(), b'different-logo')

    def test_media_migration_missing_file_fails_unless_explicitly_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / 'source'
            target_root = root / 'target'
            source_root.mkdir()
            CompanySettings.objects.create(logo='company/missing.png')

            with self.settings(STORAGES=self._storage_settings(target_root)):
                with self.assertRaisesRegex(CommandError, 'Migration incomplète'):
                    call_command(
                        'migrate_media_storage',
                        str(source_root),
                        stdout=StringIO(),
                        stderr=StringIO(),
                    )
                call_command(
                    'migrate_media_storage',
                    str(source_root),
                    allow_missing=True,
                    stdout=StringIO(),
                    stderr=StringIO(),
                )

    def test_media_migration_rejects_path_outside_source_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / 'source'
            source_root.mkdir()
            (root / 'escape.png').write_bytes(b'outside')
            CompanySettings.objects.create(logo='../escape.png')

            with self.settings(STORAGES=self._storage_settings(root / 'target')):
                with self.assertRaisesRegex(CommandError, 'hors du dossier autorisé'):
                    call_command('migrate_media_storage', str(source_root))
