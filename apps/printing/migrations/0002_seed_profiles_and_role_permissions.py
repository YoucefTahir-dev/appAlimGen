from django.db import migrations


PERMISSION_NAMES = {
    'printerprofile': (
        ('add_printerprofile', 'Can add printer profile'),
        ('change_printerprofile', 'Can change printer profile'),
        ('delete_printerprofile', 'Can delete printer profile'),
        ('view_printerprofile', 'Can view printer profile'),
        ('test_printerprofile', 'Can generate a printer test'),
    ),
    'printprofile': (
        ('add_printprofile', 'Can add print profile'),
        ('change_printprofile', 'Can change print profile'),
        ('delete_printprofile', 'Can delete print profile'),
        ('view_printprofile', 'Can view print profile'),
    ),
    'userprinterpreference': (
        ('add_userprinterpreference', 'Can add user printer preference'),
        ('change_userprinterpreference', 'Can change user printer preference'),
        ('delete_userprinterpreference', 'Can delete user printer preference'),
        ('view_userprinterpreference', 'Can view user printer preference'),
    ),
}


def seed_profiles_and_permissions(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    Group = apps.get_model('auth', 'Group')
    PrintProfile = apps.get_model('printing', 'PrintProfile')

    PrintProfile.objects.get_or_create(
        name='Ticket 58 mm',
        defaults={'document_type': 'ticket_58', 'paper_width': 58, 'copies': 1, 'language': 'bilingual'},
    )
    PrintProfile.objects.get_or_create(
        name='Ticket 80 mm',
        defaults={'document_type': 'ticket_80', 'paper_width': 80, 'copies': 1, 'language': 'bilingual'},
    )
    PrintProfile.objects.get_or_create(
        name='Facture A4',
        defaults={'document_type': 'invoice_a4', 'paper_width': None, 'copies': 1, 'language': 'bilingual'},
    )
    PrintProfile.objects.get_or_create(name="Bon d'achat", defaults={'document_type': 'purchase_order', 'copies': 1, 'language': 'fr'})
    PrintProfile.objects.get_or_create(name='Bon de livraison', defaults={'document_type': 'delivery_note', 'copies': 1, 'language': 'fr'})
    PrintProfile.objects.get_or_create(name='Étiquette produit', defaults={'document_type': 'product_label', 'copies': 1, 'language': 'fr'})

    permissions = {}
    for model, definitions in PERMISSION_NAMES.items():
        content_type, _ = ContentType.objects.get_or_create(app_label='printing', model=model)
        for codename, name in definitions:
            permission, _ = Permission.objects.get_or_create(
                content_type=content_type, codename=codename, defaults={'name': name},
            )
            permissions[codename] = permission

    role_permissions = {
        'Administrateur': set(permissions),
        'Gestionnaire': set(permissions) - {'delete_printerprofile', 'delete_printprofile'},
        'Vendeur': {'view_printerprofile', 'test_printerprofile', 'view_printprofile'},
    }
    for group_name, codenames in role_permissions.items():
        group = Group.objects.filter(name=group_name).first()
        if group:
            group.permissions.add(*(permissions[codename] for codename in codenames))


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0004_user_denied_permissions'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('printing', '0001_initial'),
    ]

    operations = [migrations.RunPython(seed_profiles_and_permissions, migrations.RunPython.noop)]
