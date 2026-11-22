"""Keeps the ERD honest.

The ERD had drifted a long way from the schema — it still described Payment as
a OneToOne, a `time` and `duration` on Booking that no longer exist, prices in
USD, and no TutorSchedule at all. A diagram nobody checks is worse than no
diagram, because it is believed.

These tests read the .puml and compare it against the live models, so the next
schema change either updates the diagram or fails the suite.
"""
import re
from pathlib import Path

from django.apps import apps
from django.test import SimpleTestCase

ERD = Path(__file__).resolve().parent / 'online_tutor_management_erd.puml'

# The apps whose models the diagram documents.
DOCUMENTED_APPS = ['accounts', 'tutors', 'bookings', 'reviews', 'payments',
                   'chatbot']

# Fields inherited from Django's own machinery that the diagram deliberately
# omits — they are framework plumbing, not part of this application's design.
OMITTED = {'groups', 'user_permissions', 'logentry'}


def documented_models():
    for label in DOCUMENTED_APPS:
        for model in apps.get_app_config(label).get_models():
            yield model


def concrete_fields(model):
    """Real columns on this table — not reverse relations or M2M."""
    for field in model._meta.get_fields():
        if not hasattr(field, 'get_internal_type'):
            continue
        if field.auto_created and not field.concrete:
            continue
        if field.many_to_many or field.one_to_many:
            continue
        if field.name in OMITTED:
            continue
        yield field


class ErdMatchesTheSchemaTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.text = ERD.read_text(encoding='utf-8')

    def test_every_model_appears(self):
        for model in documented_models():
            with self.subTest(model=model.__name__):
                self.assertIn(model.__name__, self.text,
                              f'{model.__name__} is missing from the ERD')

    def test_every_column_appears(self):
        """A field added to a model must be added to the diagram too."""
        missing = []
        for model in documented_models():
            for field in concrete_fields(model):
                name = field.name
                # FKs are drawn with their column name, e.g. student_id.
                candidates = [name, f'{name}_id']
                if not any(re.search(rf'\b{re.escape(c)}\b', self.text)
                           for c in candidates):
                    missing.append(f'{model.__name__}.{name}')
        self.assertEqual(missing, [],
                         'these columns exist but are not in the ERD: '
                         + ', '.join(missing))

    def test_no_columns_that_no_longer_exist(self):
        """The failure mode that actually happened: stale fields left behind."""
        live = set()
        for model in documented_models():
            for field in concrete_fields(model):
                live.add(field.name)

        # Fields the old diagram carried that were removed from the schema.
        # If one of these ever reappears in the ERD it is a regression, unless
        # it has genuinely been added back to a model.
        retired = ['duration', 'is_verified', 'currency', 'notes']
        for name in retired:
            if name in live:
                continue
            with self.subTest(field=name):
                # Allow the word inside prose in a note, but not as a field row.
                field_row = re.search(rf'^\s*{name}\s*:', self.text, re.MULTILINE)
                self.assertIsNone(
                    field_row,
                    f'the ERD lists a "{name}" column that no model has',
                )

    def test_the_relationship_cardinalities_are_right(self):
        """The two that matter, and the one that was wrong."""
        # Payments are instalments — many per booking. The old diagram said one.
        self.assertRegex(
            self.text, r'Booking\s+\|\|--o\{\s+Payment',
            'Payment must be drawn one-to-MANY from Booking — a course is paid '
            'in instalments, and the model is a ForeignKey.',
        )
        self.assertRegex(
            self.text, r'Booking\s+\|\|--o\|\s+Review',
            'Review is one-per-booking (OneToOneField).',
        )

    def test_the_payment_model_is_a_foreign_key(self):
        """Guards the claim the diagram makes, against the model itself."""
        Payment = apps.get_model('payments', 'Payment')
        field = Payment._meta.get_field('booking')
        self.assertTrue(field.many_to_one,
                        'Payment.booking must stay a ForeignKey for instalments')

    def test_status_choices_are_listed(self):
        """Status vocabularies drift quietly; 'cancelled' was missing before."""
        Booking = apps.get_model('bookings', 'Booking')
        for value, _label in Booking._meta.get_field('status').choices:
            with self.subTest(status=value):
                self.assertIn(value, self.text,
                              f'booking status {value!r} is not in the ERD')

        Payment = apps.get_model('payments', 'Payment')
        for value, _label in Payment._meta.get_field('status').choices:
            with self.subTest(status=value):
                self.assertIn(value, self.text,
                              f'payment status {value!r} is not in the ERD')

    def test_it_is_a_well_formed_plantuml_document(self):
        self.assertTrue(self.text.lstrip().startswith('@startuml'))
        self.assertTrue(self.text.rstrip().endswith('@enduml'))
        self.assertEqual(self.text.count('@startuml'), 1)
        self.assertEqual(self.text.count('@enduml'), 1)
        # Every block that opens closes — entities and skinparam alike.
        opens = len(re.findall(r'\{\s*$', self.text, re.MULTILINE))
        closes = len(re.findall(r'^\}\s*$', self.text, re.MULTILINE))
        self.assertEqual(opens, closes, 'a block in the diagram is unbalanced')

        # Every entity declared is also drawn in a relationship, or it is
        # floating unconnected on the page.
        entities = set(re.findall(r'entity\s+"(\w+)"', self.text))
        relationships = self.text.split('RELATIONSHIPS')[-1]
        for name in entities:
            with self.subTest(entity=name):
                self.assertIn(name, relationships,
                              f'{name} is drawn but connected to nothing')
