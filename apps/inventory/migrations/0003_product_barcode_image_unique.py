from django.core.files.base import ContentFile
from django.db import migrations, models


def build_barcode_svg(barcode_value):
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode import createBarcodeDrawing

    drawing = createBarcodeDrawing(
        'Code128',
        value=barcode_value,
        barHeight=44,
        humanReadable=True,
    )
    return renderSVG.drawToString(drawing).encode('utf-8')


def populate_barcodes(apps, schema_editor):
    Product = apps.get_model('inventory', 'Product')
    seen = set()

    for product in Product.objects.order_by('pk'):
        base_barcode = product.barcode or f'BC-{product.reference or product.pk}'
        candidate = base_barcode
        suffix = 1
        while candidate in seen or Product.objects.exclude(pk=product.pk).filter(barcode=candidate).exists():
            suffix += 1
            candidate = f'{base_barcode}-{suffix}'

        product.barcode = candidate
        seen.add(candidate)

        if not product.barcode_image:
            filename = f'{product.reference or product.pk}_barcode.svg'
            product.barcode_image.save(filename, ContentFile(build_barcode_svg(candidate)), save=False)

        product.save(update_fields=['barcode', 'barcode_image'])


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0002_product_qr_code_alter_product_reference'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='barcode_image',
            field=models.FileField(blank=True, null=True, upload_to='barcodes/', verbose_name='Image code-barres'),
        ),
        migrations.RunPython(populate_barcodes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='product',
            name='barcode',
            field=models.CharField(blank=True, max_length=100, unique=True, verbose_name='Code-barres'),
        ),
    ]
