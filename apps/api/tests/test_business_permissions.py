"""Fail-closed policy tests without a database or real user credentials."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.api.permissions import BusinessPermission


class BusinessPermissionTests(SimpleTestCase):
    def setUp(self):
        self.user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            is_superuser=False,
            force_password_change=False,
        )

    def assert_decision(self, expected, request, view):
        """Object checks must not reopen a route denied by the action policy."""
        for object_check in (False, True):
            with self.subTest(object_check=object_check):
                permission = BusinessPermission()
                if object_check:
                    actual = permission.has_object_permission(request, view, object())
                else:
                    actual = permission.has_permission(request, view)
                self.assertIs(actual, expected)

    def request(self, user=None, method='GET'):
        return SimpleNamespace(user=self.user if user is None else user, method=method)

    def test_missing_required_permissions_denies_even_a_superuser(self):
        for is_superuser in (False, True):
            self.user.is_superuser = is_superuser
            with self.subTest(is_superuser=is_superuser):
                with patch('apps.api.permissions.has_permissions') as check:
                    self.assert_decision(False, self.request(), SimpleNamespace(action='list'))
                    check.assert_not_called()

    def test_empty_or_malformed_mapping_denies_without_business_lookup(self):
        for mapping in ({}, None, False, 1, 'inventory.view_product', [], (), set()):
            with self.subTest(mapping=mapping):
                with patch('apps.api.permissions.has_permissions') as check:
                    self.assert_decision(
                        False, self.request(),
                        SimpleNamespace(action='list', required_permissions=mapping),
                    )
                    check.assert_not_called()

    def test_undeclared_action_denies_even_a_superuser(self):
        for is_superuser in (False, True):
            self.user.is_superuser = is_superuser
            with self.subTest(is_superuser=is_superuser):
                with patch('apps.api.permissions.has_permissions') as check:
                    self.assert_decision(
                        False, self.request(),
                        SimpleNamespace(
                            action='destroy',
                            required_permissions={'list': 'inventory.view_product'},
                        ),
                    )
                    check.assert_not_called()

    def test_wildcard_is_not_a_fallback_for_an_undeclared_action(self):
        for wildcard in ((), 'inventory.view_product'):
            with self.subTest(wildcard=wildcard):
                with patch('apps.api.permissions.has_permissions') as check:
                    self.assert_decision(
                        False, self.request(),
                        SimpleNamespace(
                            action='future_action', required_permissions={'*': wildcard},
                        ),
                    )
                    check.assert_not_called()

    def test_exact_action_does_not_use_a_wildcard_to_bypass_its_permission(self):
        view = SimpleNamespace(
            action='destroy',
            required_permissions={'*': (), 'destroy': 'inventory.delete_product'},
        )
        with patch('apps.api.permissions.has_permissions', return_value=False) as check:
            self.assert_decision(False, self.request(), view)
            self.assertEqual(check.call_count, 2)
            for call in check.call_args_list:
                self.assertEqual(call.args, (self.user, ('inventory.delete_product',)))
                self.assertFalse(call.kwargs.get('any_permission', False))

    def test_string_permission_is_normalized_and_checked(self):
        view = SimpleNamespace(
            action='list', required_permissions={'list': 'inventory.view_product'},
        )
        for granted in (False, True):
            with self.subTest(granted=granted):
                with patch('apps.api.permissions.has_permissions', return_value=granted) as check:
                    self.assert_decision(granted, self.request(), view)
                    self.assertEqual(check.call_count, 2)
                    for call in check.call_args_list:
                        self.assertEqual(call.args, (self.user, ('inventory.view_product',)))
                        self.assertFalse(call.kwargs.get('any_permission', False))

    def test_list_and_tuple_require_all_declared_permissions(self):
        names = ('commerce.view_sale', 'accounts.download_invoice_pdf')
        for permission_names in (names, list(names)):
            view = SimpleNamespace(action='invoice', required_permissions={'invoice': permission_names})
            for granted in (False, True):
                with self.subTest(permission_names=permission_names, granted=granted):
                    with patch('apps.api.permissions.has_permissions', return_value=granted) as check:
                        self.assert_decision(granted, self.request(), view)
                        self.assertEqual(check.call_count, 2)
                        for call in check.call_args_list:
                            self.assertIs(call.args[0], self.user)
                            self.assertEqual(tuple(call.args[1]), names)
                            self.assertFalse(call.kwargs.get('any_permission', False))

    def test_any_permission_is_opt_in_for_the_exact_declared_action(self):
        names = ('commerce.view_sale', 'commerce.view_purchase')
        for action, any_permission in (('list', True), ('retrieve', False)):
            view = SimpleNamespace(
                action=action,
                required_permissions={'list': names, 'retrieve': names},
                any_permission_actions=('list',),
            )
            for granted in (False, True):
                with self.subTest(action=action, granted=granted):
                    with patch('apps.api.permissions.has_permissions', return_value=granted) as check:
                        self.assert_decision(granted, self.request(), view)
                        self.assertEqual(check.call_count, 2)
                        for call in check.call_args_list:
                            self.assertIs(call.args[0], self.user)
                            self.assertEqual(tuple(call.args[1]), names)
                            self.assertIs(call.kwargs.get('any_permission', False), any_permission)

    def test_any_permission_action_with_no_permissions_denies(self):
        view = SimpleNamespace(
            action='list', required_permissions={'list': ()},
            any_permission_actions=('list',),
        )
        with patch('apps.api.permissions.has_permissions') as check:
            self.assert_decision(False, self.request(), view)
            check.assert_not_called()

    def test_malformed_any_permission_actions_denies_without_business_lookup(self):
        for actions in (None, False, True, 0, 'list', {'list': True}, {'list'}, [], ['list'],
                        (None,), (False,), (1,), ('',), (' ',), ('list action',),
                        ('list\t',), ('list-action',), ('*',)):
            with self.subTest(actions=actions):
                view = SimpleNamespace(
                    action='list', required_permissions={'list': 'commerce.view_sale'},
                    any_permission_actions=actions,
                )
                with patch('apps.api.permissions.has_permissions') as check:
                    self.assert_decision(False, self.request(), view)
                    check.assert_not_called()

    def test_malformed_permission_values_deny_even_a_superuser(self):
        invalid_values = (
            None, False, True, 0, 1, {}, {'inventory.view_product'},
            '', ' ', 'inventory', 'inventory.', '.view_product',
            'inventory.view_product.extra',
            ('',), (' ',), ('inventory',), (None,), (False,), (1,),
            ('inventory.view_product', None),
            ['inventory.view_product', 'inventory.'],
        )
        for is_superuser in (False, True):
            self.user.is_superuser = is_superuser
            for permission_names in invalid_values:
                with self.subTest(is_superuser=is_superuser, permission_names=permission_names):
                    with patch('apps.api.permissions.has_permissions') as check:
                        self.assert_decision(
                            False, self.request(),
                            SimpleNamespace(
                                action='list', required_permissions={'list': permission_names},
                            ),
                        )
                        check.assert_not_called()

    def test_explicit_empty_tuple_allows_an_authenticated_active_user(self):
        with patch('apps.api.permissions.has_permissions') as check:
            self.assert_decision(
                True, self.request(),
                SimpleNamespace(action='me', required_permissions={'me': ()}),
            )
            check.assert_not_called()

    def test_missing_user_denies_even_an_explicit_authentication_only_action(self):
        view = SimpleNamespace(action='me', required_permissions={'me': ()})
        for request in (SimpleNamespace(method='GET'), SimpleNamespace(user=None, method='GET')):
            with self.subTest(request=request):
                with patch('apps.api.permissions.has_permissions') as check:
                    self.assert_decision(False, request, view)
                    check.assert_not_called()

    def test_anonymous_inactive_or_password_change_required_user_denies(self):
        view = SimpleNamespace(action='me', required_permissions={'me': ()})
        for changes in (
            {'is_authenticated': False},
            {'is_active': False},
            {'force_password_change': True},
            {'is_superuser': True, 'is_active': False},
            {'is_superuser': True, 'force_password_change': True},
        ):
            user = SimpleNamespace(**(vars(self.user) | changes))
            with self.subTest(changes=changes):
                with patch('apps.api.permissions.has_permissions') as check:
                    self.assert_decision(False, self.request(user=user), view)
                    check.assert_not_called()

    def test_plain_api_view_uses_its_explicit_http_method_declaration(self):
        view = SimpleNamespace(required_permissions={'get': 'accounts.view_dashboard'})
        with patch('apps.api.permissions.has_permissions', return_value=True) as check:
            self.assert_decision(True, self.request(method='GET'), view)
            self.assertEqual(check.call_count, 2)
            for call in check.call_args_list:
                self.assertEqual(call.args, (self.user, ('accounts.view_dashboard',)))
                self.assertFalse(call.kwargs.get('any_permission', False))

    def test_plain_api_view_denies_an_undeclared_http_method(self):
        view = SimpleNamespace(required_permissions={'get': 'accounts.view_dashboard'})
        with patch('apps.api.permissions.has_permissions') as check:
            self.assert_decision(False, self.request(method='POST'), view)
            check.assert_not_called()

    def test_plain_api_view_head_reuses_the_explicit_get_permission(self):
        view = SimpleNamespace(required_permissions={'get': 'accounts.view_dashboard'})
        for granted in (False, True):
            with self.subTest(granted=granted):
                with patch('apps.api.permissions.has_permissions', return_value=granted) as check:
                    self.assert_decision(granted, self.request(method='HEAD'), view)
                    self.assertEqual(check.call_count, 2)
                    for call in check.call_args_list:
                        self.assertEqual(call.args, (self.user, ('accounts.view_dashboard',)))
                        self.assertFalse(call.kwargs.get('any_permission', False))

    def test_plain_api_view_head_denies_when_get_is_undeclared(self):
        view = SimpleNamespace(required_permissions={'post': 'commerce.add_sale'})
        with patch('apps.api.permissions.has_permissions') as check:
            self.assert_decision(False, self.request(method='HEAD'), view)
            check.assert_not_called()

    def test_plain_api_view_explicit_head_permission_takes_precedence_over_get(self):
        view = SimpleNamespace(required_permissions={
            'get': (), 'head': 'accounts.view_dashboard',
        })
        for granted in (False, True):
            with self.subTest(granted=granted):
                with patch('apps.api.permissions.has_permissions', return_value=granted) as check:
                    self.assert_decision(granted, self.request(method='HEAD'), view)
                    self.assertEqual(check.call_count, 2)
                    for call in check.call_args_list:
                        self.assertEqual(call.args, (self.user, ('accounts.view_dashboard',)))
                        self.assertFalse(call.kwargs.get('any_permission', False))

    def test_plain_api_view_malformed_head_does_not_fall_back_to_get(self):
        for declaration in (None, False, '', 'accounts.', ('accounts.view_dashboard', None)):
            view = SimpleNamespace(required_permissions={'get': (), 'head': declaration})
            with self.subTest(declaration=declaration):
                with patch('apps.api.permissions.has_permissions') as check:
                    self.assert_decision(False, self.request(method='HEAD'), view)
                    check.assert_not_called()

    def test_options_and_metadata_allow_only_their_exact_explicit_declaration(self):
        for action, method in ((None, 'OPTIONS'), ('metadata', 'OPTIONS')):
            declaration = 'options' if action is None else action
            view_data = {'required_permissions': {declaration: ()}}
            if action is not None:
                view_data['action'] = action
            with self.subTest(action=action):
                with patch('apps.api.permissions.has_permissions') as check:
                    self.assert_decision(True, self.request(method=method), SimpleNamespace(**view_data))
                    check.assert_not_called()

    def test_options_and_metadata_have_no_implicit_authentication_only_fallback(self):
        cases = (
            SimpleNamespace(required_permissions={'get': 'inventory.view_product'}),
            SimpleNamespace(action='metadata', required_permissions={'list': 'inventory.view_product'}),
            SimpleNamespace(action='metadata', required_permissions={'options': ()}),
        )
        for view in cases:
            with self.subTest(view=view):
                with patch('apps.api.permissions.has_permissions') as check:
                    self.assert_decision(False, self.request(method='OPTIONS'), view)
                    check.assert_not_called()

    def test_metadata_cloned_write_request_checks_the_resolved_action_permission(self):
        for method, action, name in (
            ('POST', 'create', 'inventory.add_product'),
            ('PUT', 'update', 'inventory.change_product'),
        ):
            view = SimpleNamespace(
                action='metadata',
                action_map={method.lower(): action},
                required_permissions={'metadata': (), action: name},
            )
            for granted in (False, True):
                with self.subTest(method=method, granted=granted):
                    with patch('apps.api.permissions.has_permissions', return_value=granted) as check:
                        self.assert_decision(granted, self.request(method=method), view)
                        self.assertEqual(check.call_count, 2)
                        for call in check.call_args_list:
                            self.assertEqual(call.args, (self.user, (name,)))
                            self.assertFalse(call.kwargs.get('any_permission', False))

    def test_metadata_cloned_write_request_requires_a_well_formed_action_map(self):
        invalid_maps = (None, False, 1, 'create', [], (), {'post'}, {})
        views = [SimpleNamespace(
            action='metadata',
            required_permissions={'metadata': (), 'create': 'inventory.add_product'},
        )]
        views.extend(SimpleNamespace(
            action='metadata', action_map=action_map,
            required_permissions={'metadata': (), 'create': 'inventory.add_product'},
        ) for action_map in invalid_maps)
        for view in views:
            with self.subTest(view=view):
                with patch('apps.api.permissions.has_permissions') as check:
                    self.assert_decision(False, self.request(method='POST'), view)
                    check.assert_not_called()

    def test_metadata_cloned_write_request_denies_malformed_resolved_action(self):
        for action in (None, False, 1, '', ' ', 'create action', '*', 'metadata', [], {}):
            view = SimpleNamespace(
                action='metadata', action_map={'post': action},
                required_permissions={'metadata': (), '*': (), 'create': 'inventory.add_product'},
            )
            with self.subTest(action=action):
                with patch('apps.api.permissions.has_permissions') as check:
                    self.assert_decision(False, self.request(method='POST'), view)
                    check.assert_not_called()

    def test_metadata_cloned_write_request_denies_an_undeclared_resolved_action(self):
        for method, action in (('POST', 'create'), ('PUT', 'update')):
            view = SimpleNamespace(
                action='metadata', action_map={method.lower(): action},
                required_permissions={'metadata': (), '*': ()},
            )
            with self.subTest(method=method):
                with patch('apps.api.permissions.has_permissions') as check:
                    self.assert_decision(False, self.request(method=method), view)
                    check.assert_not_called()
