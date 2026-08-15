import hashlib
from pathlib import Path

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


MEDIA_FIELDS = (
    ('inventory', 'Product', 'photo'),
    ('inventory', 'Product', 'qr_code'),
    ('inventory', 'Product', 'barcode_image'),
    ('core', 'CompanySettings', 'logo'),
    ('expenses', 'Expense', 'receipt'),
    ('accounts', 'User', 'photo'),
)


def _stream_sha256(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()


def _source_sha256(path):
    with Path(path).open('rb') as stream:
        return _stream_sha256(stream)


def _storage_sha256(name):
    with default_storage.open(name, 'rb') as stream:
        return _stream_sha256(stream)


class Command(BaseCommand):
    help = 'Copie les médias existants vers le stockage Django configuré, sans supprimer les originaux.'

    def add_arguments(self, parser):
        parser.add_argument('source_root')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument(
            '--allow-missing',
            action='store_true',
            help='Termine avec succès même si des fichiers référencés en base sont absents.',
        )

    def handle(self, *args, **options):
        source_root = Path(options['source_root']).resolve()
        if not source_root.is_dir():
            raise CommandError(f'Dossier source introuvable : {source_root}')

        counters = {'found': 0, 'planned': 0, 'copied': 0, 'existing': 0, 'missing': 0}
        for app_label, model_name, field_name in MEDIA_FIELDS:
            try:
                model = apps.get_model(app_label, model_name)
                model._meta.get_field(field_name)
            except (LookupError, FieldDoesNotExist):
                continue

            for instance in model._default_manager.exclude(**{field_name: ''}).iterator(chunk_size=200):
                field_file = getattr(instance, field_name)
                if not field_file or not field_file.name:
                    continue
                counters['found'] += 1
                relative_name = Path(field_file.name)
                source = (source_root / relative_name).resolve()
                try:
                    source.relative_to(source_root)
                except ValueError:
                    raise CommandError(f'Chemin média hors du dossier autorisé : {field_file.name}')
                if not source.is_file():
                    counters['missing'] += 1
                    self.stderr.write(f'Absent : {field_file.name}')
                    continue
                try:
                    already_exists = default_storage.exists(field_file.name)
                except Exception as exc:
                    raise CommandError(f'Stockage cible inaccessible : {exc}') from exc
                if already_exists:
                    try:
                        source_checksum = _source_sha256(source)
                        target_checksum = _storage_sha256(field_file.name)
                    except Exception as exc:
                        raise CommandError(
                            f'Impossible de vérifier le média existant {field_file.name} : {exc}'
                        ) from exc
                    if source_checksum != target_checksum:
                        raise CommandError(
                            f'Conflit : le média cible {field_file.name} existe avec un contenu différent.'
                        )
                    counters['existing'] += 1
                    continue
                if not options['apply']:
                    counters['planned'] += 1
                    self.stdout.write(f'À copier : {model._meta.label}#{instance.pk} {field_file.name}')
                    continue

                saved_name = None
                try:
                    source_checksum = _source_sha256(source)
                    with source.open('rb') as stream:
                        saved_name = default_storage.save(
                            field_file.name,
                            File(stream, name=relative_name.name),
                        )
                    target_checksum = _storage_sha256(saved_name)
                    if source_checksum != target_checksum:
                        try:
                            default_storage.delete(saved_name)
                        except Exception:
                            pass
                        raise CommandError(
                            f'La vérification SHA-256 a échoué après copie de {field_file.name}.'
                        )
                except CommandError:
                    raise
                except Exception as exc:
                    raise CommandError(f'Échec de copie de {field_file.name} : {exc}') from exc
                if saved_name != field_file.name:
                    try:
                        model._default_manager.filter(pk=instance.pk).update(**{field_name: saved_name})
                    except Exception as exc:
                        raise CommandError(
                            f'Copie créée sous {saved_name}, mais mise à jour en base impossible : {exc}'
                        ) from exc
                counters['copied'] += 1

        mode = 'APPLIQUÉ' if options['apply'] else 'SIMULATION'
        self.stdout.write(
            self.style.SUCCESS(
                f'{mode} : trouvés={counters["found"]}, à copier={counters["planned"]}, '
                f'copiés={counters["copied"]}, '
                f'déjà présents={counters["existing"]}, fichiers absents={counters["missing"]}'
            )
        )
        if counters['missing'] and not options['allow_missing']:
            raise CommandError(
                'Migration incomplète : des fichiers référencés en base sont absents. '
                'Corrigez les sources ou utilisez --allow-missing après vérification manuelle.'
            )
