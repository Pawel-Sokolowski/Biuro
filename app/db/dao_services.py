from typing import Optional

from app.db.dao_conn import get_conn


def add_company_extra_service(
    firma_id: int,
    miesiac: str,
    usluga_id: int,
    ilosc: float,
    cena_netto: float,
    fakturowac: bool,
) -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO uslugi_dodatkowe_firma
                (firma_id, miesiac, usluga_id, ilosc, cena_netto, fakturowac)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (firma_id, miesiac, usluga_id, ilosc, cena_netto, 1 if fakturowac else 0),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()
