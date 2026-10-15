import unittest

from app.db.dao_companies import (
    delete_partner, get_partners, list_companies, save_partner,
    get_pelnomocnictwa, set_pelnomocnictwa,
)
from tests.base import DbTestCase


class PelUplTests(DbTestCase):
    def test_initials_listed_per_company(self):
        firma = self.make_company("ABC")
        a = self.make_employee("a@x.pl", "Anna", "Zielińska")
        b = self.make_employee("b@x.pl", "Paweł", "Sokołowski")
        set_pelnomocnictwa(firma, "PEL", [a, b])
        set_pelnomocnictwa(firma, "UPL", [b])
        row = next(c for c in list_companies() if c["id"] == firma)
        self.assertEqual(sorted(row["pel"].split()), ["AZ", "PS"])
        self.assertEqual(row["upl"], "PS")

    def test_set_replaces_previous_assignments(self):
        firma = self.make_company()
        a = self.make_employee("a@x.pl", "Anna", "Zielińska")
        b = self.make_employee("b@x.pl", "Paweł", "Sokołowski")
        set_pelnomocnictwa(firma, "PEL", [a, b])
        set_pelnomocnictwa(firma, "PEL", [b])
        self.assertEqual(get_pelnomocnictwa(firma)["PEL"], [b])

    def test_invalid_type_rejected(self):
        firma = self.make_company()
        with self.assertRaises(ValueError):
            set_pelnomocnictwa(firma, "ABC", [])

    def test_company_without_assignments_has_empty_strings(self):
        firma = self.make_company()
        row = next(c for c in list_companies() if c["id"] == firma)
        self.assertEqual((row["pel"], row["upl"]), ("", ""))


class PartnerTests(DbTestCase):
    def test_save_list_update_delete(self):
        firma = self.make_company()
        save_partner(firma, {"imie_pierwsze": "Jan", "nazwisko": "Nowak", "procent": 60})
        save_partner(firma, {"imie_pierwsze": "Ewa", "nazwisko": "Nowak", "procent": 40})
        partners = get_partners(firma)
        self.assertEqual([p["imie_pierwsze"] for p in partners], ["Jan", "Ewa"])

        first = partners[0]
        save_partner(firma, {"id": first["id"], "imie_pierwsze": "Janusz", "nazwisko": "Nowak", "procent": 55})
        self.assertEqual(get_partners(firma)[0]["imie_pierwsze"], "Janusz")

        delete_partner(first["id"])
        remaining = get_partners(firma)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["imie_pierwsze"], "Ewa")


if __name__ == "__main__":
    unittest.main()
