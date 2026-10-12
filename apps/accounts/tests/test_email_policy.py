from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Barrier

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase

from apps.accounts.forms import ProfileForm, UserCreateForm, UserUpdateForm


class UserEmailPolicyTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.existing = self.User.objects.create_user(
            username='existing-email',
            email='owner@example.com',
            password='Strong-password-2040!',
        )

    def test_manager_normalizes_case_and_surrounding_spaces(self):
        user = self.User.objects.create_user(
            username='normalized-email',
            email=' Person@Example.COM ',
        )
        self.assertEqual(user.email, 'person@example.com')

    def test_creation_form_rejects_normalized_duplicate(self):
        role = Group.objects.create(name='Unique email test role')
        form = UserCreateForm(data={
            'username': 'duplicate-create',
            'email': ' OWNER@EXAMPLE.COM ',
            'assigned_role': role.pk,
            'password1': 'Strong-password-2041!',
            'password2': 'Strong-password-2041!',
            'is_active': 'on',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_profile_update_rejects_normalized_duplicate(self):
        target = self.User.objects.create_user(
            username='profile-email-target',
            email='target@example.com',
        )
        form = ProfileForm(
            instance=target,
            data={
                'first_name': '',
                'last_name': '',
                'email': ' Owner@Example.com ',
                'phone': '',
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_administration_update_rejects_normalized_duplicate(self):
        role = Group.objects.create(name='Unique email update role')
        target = self.User.objects.create_user(
            username='admin-email-target',
            email='admin-target@example.com',
        )
        target.groups.add(role)
        form = UserUpdateForm(
            instance=target,
            data={
                'username': target.username,
                'first_name': '',
                'last_name': '',
                'email': ' OWNER@EXAMPLE.COM ',
                'phone': '',
                'assigned_role': role.pk,
                'is_active': 'on',
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_database_constraint_rejects_case_and_space_duplicate(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.User.objects.create_user(
                username='constraint-duplicate',
                email=' OWNER@EXAMPLE.COM ',
            )

    def test_inactive_account_reserves_its_email(self):
        self.existing.is_active = False
        self.existing.save(update_fields=['is_active'])
        target = self.User(username='inactive-conflict', email='OWNER@example.com')
        target.set_unusable_password()
        with self.assertRaisesMessage(ValidationError, 'déjà utilisée'):
            target.full_clean()

    def test_audit_command_masks_addresses_and_never_changes_data(self):
        self.User.objects.filter(pk=self.existing.pk).update(
            email=' Owner@Example.com ',
        )
        invalid = self.User.objects.create_user(username='invalid-email-audit')
        self.User.objects.filter(pk=invalid.pk).update(email='not-an-email')
        output = StringIO()

        call_command('audit_user_emails', stdout=output)

        report = output.getvalue()
        self.assertIn('E-mails non normalisés : 1', report)
        self.assertIn('E-mails invalides : 1', report)
        self.assertIn('État migration : BLOQUÉE', report)
        self.assertNotIn('owner@example.com', report.lower())
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.email, ' Owner@Example.com ')


class UserEmailPostgreSQLConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_two_concurrent_creations_cannot_share_an_email(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Ce test exige une base PostgreSQL isolée.')

        barrier = Barrier(2)

        def create_user(index):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                with transaction.atomic():
                    get_user_model().objects.create_user(
                        username=f'concurrent-email-{index}',
                        email=(
                            'Concurrent@Example.com'
                            if index == 1
                            else ' concurrent@example.COM '
                        ),
                    )
                return 'created'
            except IntegrityError:
                return 'conflict'
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(create_user, (1, 2)))

        self.assertCountEqual(results, ['created', 'conflict'])
