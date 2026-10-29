from typing import Optional

from app.db.dao_conn import get_conn


def add_company_extra_service(
    firma_id: int,
    miesiac: str,
    usluga_id: int,
    ilosc: float,
    cena_netto: float,
    fakturowac: bool,
    jednostka: str = "szt.",
) -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO uslugi_dodatkowe_firma
                (firma_id, miesiac, usluga_id, ilosc, cena_netto, fakturowac, jednostka)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (firma_id, miesiac, usluga_id, ilosc, cena_netto, 1 if fakturowac else 0, jednostka),
        )
        new_id = int(cur.fetchone()[0])
        conn.commit()
        return new_id
    finally:
        conn.close()
