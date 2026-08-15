from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import AuditLog


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class DynamicRoleTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.admin = self.User.objects.create_superuser(
            username='rbac-admin',
            email='admin@example.test',
            password='StrongPass123!',
        )

    def permission(self, app_label, codename):
        return Permission.objects.get(
            content_type__app_label=app_label,
            codename=codename,
        )

    def test_legacy_roles_are_seeded_as_dynamic_groups(self):
        self.assertTrue(
            Group.objects.filter(name='Administrateur').exists()
        )

    def test_management_pages_render_for_superuser(self):
        role = Group.objects.create(name='Rôle rendu')
        target = self.User.objects.create_user(
            username='render-target', password='StrongPass123!'
        )
        target.groups.add(role)
        self.client.force_login(self.admin)

        urls = [
            reverse('user_list'),
            reverse('user_create'),
            reverse('user_update', args=[target.pk]),
            reverse('user_password_reset_admin', args=[target.pk]),
            reverse('role_list'),
            reverse('role_create'),
            reverse('role_update', args=[role.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        user_form = self.client.get(reverse('user_update', args=[target.pk]))
        self.assertContains(user_form, 'Permissions individuelles refusées')
        self.assertContains(user_form, 'name="denied_permissions"')
        self.assertTrue(Group.objects.filter(name='Gestionnaire').exists())
        self.assertTrue(Group.objects.filter(name='Vendeur').exists())
        self.assertTrue(
            Group.objects.get(name='Administrateur').permissions.filter(
                codename='view_dashboard',
                content_type__app_label='accounts',
            ).exists()
        )

    def test_custom_role_controls_direct_url_and_menu(self):
        role = Group.objects.create(name='Lecteur produits')
        role.permissions.add(self.permission('inventory', 'view_product'))
        user = self.User.objects.create_user(
            username='product-reader', password='StrongPass123!'
        )
        user.groups.add(role)
        self.client.force_login(user)

        product_response = self.client.get(reverse('product_list'))
        self.assertEqual(product_response.status_code, 200)
        self.assertContains(product_response, 'Produits')
        self.assertNotContains(product_response, 'Ajouter produit')
        self.assertNotContains(product_response, reverse('dashboard'))
        self.assertEqual(self.client.get(reverse('product_create')).status_code, 403)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 403)

    def test_direct_user_permission_is_an_additional_exception(self):
        role = Group.objects.create(name='Sans accès dashboard')
        user = self.User.objects.create_user(
            username='direct-permission', password='StrongPass123!'
        )
        user.groups.add(role)
        dashboard_permission = self.permission('accounts', 'view_dashboard')
        user.user_permissions.add(dashboard_permission)
        self.client.force_login(user)

        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)
        user.user_permissions.remove(dashboard_permission)
        user = self.User.objects.get(pk=user.pk)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 403)

    def test_individual_refusal_overrides_role_and_can_be_removed(self):
        role = Group.objects.create(name='Dashboard avec exception')
        dashboard_permission = self.permission('accounts', 'view_dashboard')
        role.permissions.add(dashboard_permission)
        user = self.User.objects.create_user(
            username='dashboard-denied', password='StrongPass123!'
        )
        user.groups.add(role)
        user.denied_permissions.add(dashboard_permission)
        self.client.force_login(user)

        profile_response = self.client.get(reverse('profile'))
        self.assertEqual(profile_response.status_code, 200)
        self.assertNotContains(profile_response, reverse('dashboard'))
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 403)

        user.denied_permissions.remove(dashboard_permission)
        user = self.User.objects.get(pk=user.pk)
        self.client.force_login(user)
        self.assertContains(self.client.get(reverse('profile')), reverse('dashboard'))
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_same_permission_cannot_be_granted_and_denied(self):
        role = Group.objects.create(name='Collision test')
        dashboard_permission = self.permission('accounts', 'view_dashboard')
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('user_create'),
            {
                'username': 'invalid-overrides',
                'first_name': 'Test',
                'last_name': 'Collision',
                'email': 'collision@example.test',
                'assigned_role': role.pk,
                'password1': 'SafePassword987!',
                'password2': 'SafePassword987!',
                'is_active': 'on',
                'individual_permissions': [dashboard_permission.pk],
                'denied_permissions': [dashboard_permission.pk],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'à la fois accordée et refusée',
            html=False,
        )
        self.assertFalse(
            self.User.objects.filter(username='invalid-overrides').exists()
        )

    def test_user_editor_can_add_then_remove_a_refusal(self):
        role = Group.objects.create(name='Édition refus')
        dashboard_permission = self.permission('accounts', 'view_dashboard')
        role.permissions.add(dashboard_permission)
        target = self.User.objects.create_user(
            username='edited-refusal',
            email='edited@example.test',
            password='StrongPass123!',
        )
        target.groups.add(role)
        self.client.force_login(self.admin)
        base_data = {
            'username': target.username,
            'first_name': '',
            'last_name': '',
            'email': target.email,
            'phone': '',
            'assigned_role': role.pk,
            'is_active': 'on',
        }

        response = self.client.post(
            reverse('user_update', args=[target.pk]),
            {**base_data, 'denied_permissions': [dashboard_permission.pk]},
        )
        self.assertRedirects(response, reverse('user_list'))
        target.refresh_from_db()
        self.assertTrue(target.denied_permissions.filter(pk=dashboard_permission.pk).exists())

        response = self.client.post(
            reverse('user_update', args=[target.pk]),
            base_data,
        )
        self.assertRedirects(response, reverse('user_list'))
        target.refresh_from_db()
        self.assertFalse(target.denied_permissions.exists())
        self.assertTrue(target.has_perm('accounts.view_dashboard'))

    def test_superuser_is_not_restricted_by_individual_refusal(self):
        dashboard_permission = self.permission('accounts', 'view_dashboard')
        self.admin.denied_permissions.add(dashboard_permission)
        self.client.force_login(self.admin)

        self.assertTrue(self.admin.has_perm('accounts.view_dashboard'))
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_admin_can_create_user_with_hashed_password_role_and_audit(self):
        role = Group.objects.create(name='Caissier test')
        role.permissions.add(self.permission('commerce', 'add_sale'))
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('user_create'),
            {
                'username': 'new-cashier',
                'first_name': 'Nadia',
                'last_name': 'Test',
                'email': 'nadia@example.test',
                'phone': '0550000000',
                'assigned_role': role.pk,
                'password1': 'SafePassword987!',
                'password2': 'SafePassword987!',
                'is_active': 'on',
                'force_password_change': 'on',
                'individual_permissions': [
                    self.permission('accounts', 'view_dashboard').pk
                ],
            },
        )

        self.assertRedirects(response, reverse('user_list'))
        user = self.User.objects.get(username='new-cashier')
        self.assertTrue(user.check_password('SafePassword987!'))
        self.assertTrue(user.force_password_change)
        self.assertEqual(list(user.groups.all()), [role])
        self.assertTrue(user.has_perm('accounts.view_dashboard'))
        self.assertTrue(
            AuditLog.objects.filter(
                user=self.admin,
                action__startswith='Création utilisateur:',
            ).exists()
        )

    def test_admin_reset_password_hashes_value_and_can_force_change(self):
        target = self.User.objects.create_user(
            username='reset-target', password='OldPassword123!'
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('user_password_reset_admin', args=[target.pk]),
            {
                'password1': 'NewPassword456!',
                'password2': 'NewPassword456!',
                'force_password_change': 'on',
            },
        )

        self.assertRedirects(response, reverse('user_list'))
        target.refresh_from_db()
        self.assertTrue(target.check_password('NewPassword456!'))
        self.assertFalse(target.check_password('OldPassword123!'))
        self.assertTrue(target.force_password_change)

    def test_admin_can_disable_reactivate_and_delete_safe_account(self):
        role = Group.objects.create(name='Compte temporaire')
        target = self.User.objects.create_user(
            username='temporary-account', password='StrongPass123!'
        )
        target.groups.add(role)
        self.client.force_login(self.admin)

        self.client.post(reverse('user_toggle_active', args=[target.pk]))
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.client.post(reverse('user_toggle_active', args=[target.pk]))
        target.refresh_from_db()
        self.assertTrue(target.is_active)
        self.assertEqual(
            self.client.get(reverse('user_delete', args=[target.pk])).status_code,
            200,
        )
        response = self.client.post(reverse('user_delete', args=[target.pk]))
        self.assertRedirects(response, reverse('user_list'))
        self.assertFalse(self.User.objects.filter(pk=target.pk).exists())

    def test_admin_can_create_role_with_permission_matrix(self):
        dashboard_permission = self.permission('accounts', 'view_dashboard')
        product_permission = self.permission('inventory', 'view_product')
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('role_create'),
            {
                'name': 'Directeur test',
                'permissions': [dashboard_permission.pk, product_permission.pk],
            },
        )

        self.assertRedirects(response, reverse('role_list'))
        role = Group.objects.get(name='Directeur test')
        self.assertSetEqual(
            set(role.permissions.values_list('pk', flat=True)),
            {dashboard_permission.pk, product_permission.pk},
        )
        self.assertTrue(
            AuditLog.objects.filter(
                user=self.admin,
                action='Création rôle: Directeur test',
            ).exists()
        )

    def test_non_superuser_cannot_modify_superuser(self):
        role = Group.objects.create(name='Gestion utilisateurs limitée')
        role.permissions.add(self.permission('accounts', 'change_user'))
        operator = self.User.objects.create_user(
            username='operator', password='StrongPass123!'
        )
        operator.groups.add(role)
        self.client.force_login(operator)

        response = self.client.get(reverse('user_update', args=[self.admin.pk]))

        self.assertEqual(response.status_code, 403)

    def test_limited_operator_cannot_reset_a_more_privileged_account(self):
        operator_role = Group.objects.create(name='Gestion utilisateurs limitée 2')
        operator_role.permissions.add(self.permission('accounts', 'change_user'))
        operator = self.User.objects.create_user(
            username='limited-operator', password='StrongPass123!'
        )
        operator.groups.add(operator_role)
        privileged = self.User.objects.create_user(
            username='privileged-user', password='StrongPass123!'
        )
        administrator = Group.objects.get(name='Administrateur')
        privileged.groups.add(administrator)
        privileged.denied_permissions.add(*administrator.permissions.all())
        self.client.force_login(operator)

        response = self.client.get(
            reverse('user_password_reset_admin', args=[privileged.pk])
        )

        self.assertEqual(response.status_code, 403)

    def test_role_cannot_be_deleted_while_assigned(self):
        role = Group.objects.create(name='Rôle utilisé')
        user = self.User.objects.create_user(
            username='assigned-user', password='StrongPass123!'
        )
        user.groups.add(role)
        self.client.force_login(self.admin)

        response = self.client.post(reverse('role_delete', args=[role.pk]))

        self.assertRedirects(response, reverse('role_list'))
        self.assertTrue(Group.objects.filter(pk=role.pk).exists())

    def test_user_cannot_deactivate_own_account(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('user_toggle_active', args=[self.admin.pk])
        )

        self.assertRedirects(response, reverse('user_list'))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
