from django.contrib.auth.models import Group, Permission
from django.test import TestCase

from apps.accounts.models import User
from apps.accounts.permissions import ALL_MANAGED_PERMISSION_NAMES, has_permission
from apps.accounts.views import _may_manage_target


class LegacyDebtPermissionTests(TestCase):
    def test_new_financial_permission_does_not_accidentally_block_legacy_admin_management(self):
        actor = User.objects.create_user(username='delegated-security-admin', password='unused')
        group = Group.objects.create(name='Existing explicit administration permissions')
        actor.groups.add(group)
        for name in ALL_MANAGED_PERMISSION_NAMES - {'inventory.adjust_client_balance'}:
            app_label, codename = name.split('.', 1)
            group.permissions.add(Permission.objects.get(content_type__app_label=app_label, codename=codename))
        target = User.objects.create_user(username='legacy-admin-target', password='unused', role=User.ADMIN)
        self.assertFalse(has_permission(target, 'inventory.adjust_client_balance'))
        self.assertTrue(_may_manage_target(actor, target))
        target.user_permissions.add(Permission.objects.get(
            content_type__app_label='inventory', codename='adjust_client_balance',
        ))
        self.assertFalse(_may_manage_target(actor, User.objects.get(pk=target.pk)))
