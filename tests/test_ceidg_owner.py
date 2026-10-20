import unittest
from types import SimpleNamespace

from app.companies.company_logic import ceidg_apply


class Field:
    def __init__(self):
        self.value = ""

    def setText(self, value):
        self.value = value


class CeidgOwnerTests(unittest.TestCase):
    def test_owner_name_is_shown_in_contact_fields(self):
        first = Field()
        surname = Field()
        short_name = Field()
        form = SimpleNamespace(inputs={
            "nazwa": Field(),
            "nazwa_skrocona": short_name,
            "kont_imie": first,
            "kont_nazwisko": surname,
        })

        ceidg_apply(form, {
            "nazwa": "Jan Kowalski Usługi",
            "imie": "Jan",
            "nazwisko": "Kowalski",
        })

        self.assertEqual(first.value, "Jan")
        self.assertEqual(surname.value, "Kowalski")
        self.assertEqual(short_name.value, "Kowalski Jan")


if __name__ == "__main__":
    unittest.main()
