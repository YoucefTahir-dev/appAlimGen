import apps.accounts.models
import apps.core.security
from django.db import migrations, models


PERMISSIONS = {
    'accounts': {
        'user': {
            'view_user': 'Peut voir les utilisateurs',
            'add_user': 'Peut ajouter un utilisateur',
            'change_user': 'Peut modifier un utilisateur',
            'delete_user': 'Peut supprimer un utilisateur',
            'view_dashboard': 'Peut voir le tableau de bord',
            'view_stock': "Peut voir l'état du stock",
            'manage_stock': 'Peut enregistrer des mouvements de stock',
            'view_invoices': 'Peut voir les factures',
            'download_invoice_pdf': 'Peut télécharger les factures PDF',
            'print_invoice': 'Peut imprimer les factures et tickets',
            'view_reports': 'Peut voir les rapports',
            'export_reports_pdf': 'Peut exporter les rapports PDF',
            'export_reports_excel': 'Peut exporter les rapports Excel',
            'view_backups': 'Peut voir les sauvegardes',
            'create_backups': 'Peut créer des sauvegardes',
            'restore_backups': 'Peut restaurer des sauvegardes',
        }
    },
    'auth': {
        'group': {
            'view_group': 'Peut voir les rôles',
            'add_group': 'Peut ajouter un rôle',
            'change_group': 'Peut modifier un rôle',
            'delete_group': 'Peut supprimer un rôle',
        }
    },
    'inventory': {
        'product': {
            'view_product': 'Peut voir les produits',
            'add_product': 'Peut ajouter un produit',
            'change_product': 'Peut modifier un produit',
            'delete_product': 'Peut supprimer un produit',
        },
        'client': {
            'view_client': 'Peut voir les clients',
            'add_client': 'Peut ajouter un client',
            'change_client': 'Peut modifier un client',
            'delete_client': 'Peut supprimer un client',
        },
        'supplier': {
            'view_supplier': 'Peut voir les fournisseurs',
            'add_supplier': 'Peut ajouter un fournisseur',
            'change_supplier': 'Peut modifier un fournisseur',
            'delete_supplier': 'Peut supprimer un fournisseur',
        },
    },
    'commerce': {
        'purchase': {
            'view_purchase': 'Peut voir les achats',
            'add_purchase': 'Peut ajouter un achat',
            'change_purchase': 'Peut modifier un achat',
            'delete_purchase': 'Peut supprimer un achat',
        },
        'sale': {
            'view_sale': 'Peut voir les ventes',
            'add_sale': 'Peut ajouter une vente',
            'change_sale': 'Peut modifier une vente',
            'delete_sale': 'Peut supprimer une vente',
        },
    },
    'expenses': {
        'expense': {
            'view_expense': 'Peut voir les charges',
            'add_expense': 'Peut ajouter une charge',
            'change_expense': 'Peut modifier une charge',
            'delete_expense': 'Peut supprimer une charge',
        },
        'expensecategory': {
            'add_expensecategory': 'Peut gérer les catégories de charges',
        },
    },
    'core': {
        'companysettings': {
            'view_companysettings': 'Peut voir les paramètres société',
            'change_companysettings': 'Peut modifier les paramètres société',
        }
    },
}


SELLER_CODES = {
    'view_dashboard',
    'view_stock',
    'view_invoices',
    'download_invoice_pdf',
    'print_invoice',
    'view_product',
    'add_client',
    'view_sale',
    'add_sale',
}

MANAGER_EXCLUDED_CODES = {
    'view_user',
    'add_user',
    'change_user',
    'delete_user',
    'view_group',
    'add_group',
    'change_group',
    'delete_group',
    'view_backups',
    'create_backups',
    'restore_backups',
    'change_companysettings',
}


def seed_roles_and_permissions(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    Group = apps.get_model('auth', 'Group')
    User = apps.get_model('accounts', 'User')

    managed_permissions = []
    for app_label, models_map in PERMISSIONS.items():
        for model_name, permission_map in models_map.items():
            content_type, _created = ContentType.objects.get_or_create(
                app_label=app_label,
                model=model_name,
            )
            for codename, name in permission_map.items():
                permission, _created = Permission.objects.get_or_create(
                    content_type=content_type,
                    codename=codename,
                    defaults={'name': name},
                )
                managed_permissions.append(permission)

    administrator, _created = Group.objects.get_or_create(name='Administrateur')
    manager, _created = Group.objects.get_or_create(name='Gestionnaire')
    seller, _created = Group.objects.get_or_create(name='Vendeur')

    administrator.permissions.add(*managed_permissions)
    manager.permissions.add(
        *[
            permission
            for permission in managed_permissions
            if permission.codename not in MANAGER_EXCLUDED_CODES
        ]
    )
    seller.permissions.add(
        *[
            permission
            for permission in managed_permissions
            if permission.codename in SELLER_CODES
        ]
    )

    role_groups = {
        'admin': administrator,
        'manager': manager,
        'seller': seller,
    }
    for user in User.objects.all().iterator():
        group = administrator if user.is_superuser else role_groups.get(user.role, seller)
        user.groups.add(group)


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0002_user_force_password_change'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('commerce', '0006_ticketsequence_sale_payment_type_sale_ticket_number'),
        ('core', '0003_auditlog_audit_action_ip_date_idx_and_more'),
        ('expenses', '0001_initial'),
        ('inventory', '0008_stockmovement_ledger_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='phone',
            field=models.CharField(blank=True, max_length=30, verbose_name='Téléphone'),
        ),
        migrations.AddField(
            model_name='user',
            name='photo',
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to=apps.accounts.models.profile_photo_upload_to,
                validators=[apps.core.security.validate_image_upload],
                verbose_name='Photo de profil',
            ),
        ),
        migrations.AlterModelOptions(
            name='user',
            options={
                'permissions': [
                    ('view_dashboard', 'Peut voir le tableau de bord'),
                    ('view_stock', "Peut voir l'état du stock"),
                    ('manage_stock', 'Peut enregistrer des mouvements de stock'),
                    ('view_invoices', 'Peut voir les factures'),
                    ('download_invoice_pdf', 'Peut télécharger les factures PDF'),
                    ('print_invoice', 'Peut imprimer les factures et tickets'),
                    ('view_reports', 'Peut voir les rapports'),
                    ('export_reports_pdf', 'Peut exporter les rapports PDF'),
                    ('export_reports_excel', 'Peut exporter les rapports Excel'),
                    ('view_backups', 'Peut voir les sauvegardes'),
                    ('create_backups', 'Peut créer des sauvegardes'),
                    ('restore_backups', 'Peut restaurer des sauvegardes'),
                ],
                'verbose_name': 'Utilisateur',
                'verbose_name_plural': 'Utilisateurs',
            },
        ),
        migrations.RunPython(seed_roles_and_permissions, migrations.RunPython.noop),
    ]
