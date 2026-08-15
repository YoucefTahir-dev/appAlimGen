import tempfile

from django.test import TestCase

from apps.inventory.models import Product, ProductReferenceSequence


class ProductReferenceSequenceTests(TestCase):
    def product(self, name):
        return Product(
            name=name,
            purchase_price='10.00',
            sale_price='15.00',
            quantity=0,
            minimum_stock=0,
        )

    def test_generated_reference_is_sequential_and_not_reused(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            first = self.product('Premier')
            first.save()
            first_number = int(first.reference.rsplit('-', 1)[1])
            first.delete()

            second = self.product('Deuxième')
            second.save()

        self.assertEqual(int(second.reference.rsplit('-', 1)[1]), first_number + 1)
        self.assertEqual(
            ProductReferenceSequence.objects.get(
                year=int(second.reference.split('-')[1])
            ).last_number,
            first_number + 1,
        )

    def test_explicit_import_reference_does_not_reset_sequence(self):
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            generated = self.product('Généré')
            generated.save()
            imported = self.product('Importé')
            imported.reference = 'FOURNISSEUR-ABC'
            imported.save()
            following = self.product('Suivant')
            following.save()

        self.assertEqual(
            int(following.reference.rsplit('-', 1)[1]),
            int(generated.reference.rsplit('-', 1)[1]) + 1,
        )
