import hashlib
import unittest
from unittest.mock import Mock

from app.utils.saldeo_api import SaldeoApiError, SaldeoClient, parse_response, sign_request

COMPANIES = (b"<RESPONSE><STATUS>OK</STATUS><COMPANIES>"
             b"<COMPANY><NAME>Firma A</NAME><COMPANY_PROGRAM_ID>A1</COMPANY_PROGRAM_ID></COMPANY>"
             b"<COMPANY><NAME>Firma B</NAME><COMPANY_PROGRAM_ID>B2</COMPANY_PROGRAM_ID></COMPANY>"
             b"</COMPANIES></RESPONSE>")


class SaldeoApiTests(unittest.TestCase):
    def test_signature_sorts_params_encodes_and_appends_token(self):
        params = {"username": "demo", "req_id": "20140301123056"}
        expected = hashlib.md5(b"req_id%3D20140301123056username%3Ddemosecret").hexdigest()
        self.assertEqual(sign_request(params, "secret"), expected)

    def test_list_companies_signs_request_and_hides_token(self):
        session = Mock()
        session.get.return_value = Mock(content=COMPANIES, raise_for_status=Mock())
        companies = SaldeoClient("demo", "secret", session=session).list_companies()
        self.assertEqual([c["COMPANY_PROGRAM_ID"] for c in companies], ["A1", "B2"])
        url = session.get.call_args.args[0]
        params = session.get.call_args.kwargs["params"]
        self.assertTrue(url.endswith("/api/xml/1.0/company/list"))
        self.assertNotIn("secret", str(params))
        self.assertEqual(params["req_sig"], sign_request({k: v for k, v in params.items() if k != "req_sig"}, "secret"))

    def test_errors_and_dtd_are_rejected(self):
        with self.assertRaises(SaldeoApiError):
            parse_response("<RESPONSE><STATUS>BAD_REQUEST</STATUS></RESPONSE>")
        with self.assertRaises(SaldeoApiError):
            parse_response("<!DOCTYPE x><RESPONSE><STATUS>OK</STATUS></RESPONSE>")
        with self.assertRaises(SaldeoApiError):
            SaldeoClient("", "")


if __name__ == "__main__":
    unittest.main()
