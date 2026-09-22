import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_NAME = os.path.join(BASE_DIR, "biuro.db")

CEIDG_BASE_URL = "https://dane.biznes.gov.pl/api/ceidg/v3"
CEIDG_API_KEY = "ABCDEF1234567890"

GUS_API_URL = "https://wyszukiwarkaregon.stat.gov.pl/api/regon/v9"
GUS_API_KEY = "ABCDEF1234567890"
