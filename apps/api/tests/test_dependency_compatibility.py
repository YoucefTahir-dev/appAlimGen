"""API v1 compatibility across the security-only dependency upgrade."""
import json

from django.test import SimpleTestCase
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.renderers import JSONRenderer

from apps.api.exceptions import api_exception_handler
from apps.api.serializers import LoadingOrderSerializer, PurchaseSerializer, SaleSerializer


class QuantitySerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1)


class OrderSerializer(serializers.Serializer):
    items = QuantitySerializer(many=True)


class ListValidationCompatibilityTests(SimpleTestCase):
    def test_nested_errors_preserve_list_positions_and_successful_items(self):
        serializer = OrderSerializer(data={'items': [{'quantity': 1}, {'quantity': 0}]})
        self.assertFalse(serializer.is_valid())
        response = api_exception_handler(ValidationError(serializer.errors), {})
        payload = json.loads(JSONRenderer().render(response.data))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload['success'])
        self.assertEqual(payload['error']['code'], 'VALIDATION_ERROR')
        details = payload['error']['details']['items']
        self.assertIsInstance(details, list)
        self.assertEqual(len(details), 2)
        self.assertEqual(details[0], {})
        self.assertIn('quantity', details[1])

    def test_business_nested_fields_keep_list_error_format(self):
        for serializer_type, name in ((SaleSerializer, 'items'),
                                      (PurchaseSerializer, 'items'),
                                      (LoadingOrderSerializer, 'lines')):
            with self.subTest(serializer=serializer_type.__name__):
                field = serializer_type().fields[name]
                with self.assertRaises(ValidationError) as caught:
                    field.run_validation([{}, {}])
                self.assertIsInstance(caught.exception.detail, list)
                self.assertEqual(len(caught.exception.detail), 2)
                self.assertTrue(all(isinstance(item, dict) and item
                                    for item in caught.exception.detail))

    def test_non_list_input_retains_non_field_error(self):
        serializer = OrderSerializer(data={'items': 'not-a-list'})
        self.assertFalse(serializer.is_valid())
        self.assertIsInstance(serializer.errors['items'], dict)
        self.assertIn('non_field_errors', serializer.errors['items'])
