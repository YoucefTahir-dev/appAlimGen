import re
from pathlib import Path

from django.test import SimpleTestCase


WORKFLOWS = Path(__file__).resolve().parents[3] / '.github' / 'workflows'
ANY_ACTION = re.compile(r'^\s*uses:\s*([^#\s]+)@([^\s#]+)', re.MULTILINE)
SERVICE_IMAGE = re.compile(r'^\s*image:\s*([^#\s]+)', re.MULTILINE)


class WorkflowSupplyChainTests(SimpleTestCase):
    def test_external_actions_are_pinned_to_full_commit(self):
        failures = []
        for workflow in WORKFLOWS.glob('*.yml'):
            content = workflow.read_text(encoding='utf-8')
            for match in ANY_ACTION.finditer(content):
                if not re.fullmatch(r'[0-9a-f]{40}', match.group(2)):
                    failures.append(f'{workflow.name}: {match.group(1)}@{match.group(2)}')
        self.assertEqual(failures, [], 'Actions non immuables : ' + ', '.join(failures))

    def test_workflows_declare_read_only_default_permissions(self):
        failures = []
        for workflow in WORKFLOWS.glob('*.yml'):
            content = workflow.read_text(encoding='utf-8')
            if not re.search(r'^permissions:\s*\n\s+contents:\s+read\s*$', content, re.MULTILINE):
                failures.append(workflow.name)
        self.assertEqual(failures, [], 'Permissions explicites absentes : ' + ', '.join(failures))

    def test_service_images_are_pinned_to_a_sha256_digest(self):
        failures = []
        for workflow in WORKFLOWS.glob('*.yml'):
            content = workflow.read_text(encoding='utf-8')
            for match in SERVICE_IMAGE.finditer(content):
                if not re.fullmatch(r'[^@\s]+@sha256:[0-9a-f]{64}', match.group(1)):
                    failures.append(f'{workflow.name}: {match.group(1)}')
        self.assertEqual(failures, [], 'Images de service mutables : ' + ', '.join(failures))
