"""Merge duplicate private conversations without discarding their histories."""


def apply(conn):
    # The migration is transactional; block writers while moving message FKs.
    conn.execute("LOCK TABLE konwersacje, uczestnicy_chat, wiadomosci_chat IN SHARE ROW EXCLUSIVE MODE")
    conn.execute("""DO $$ DECLARE group_row record; duplicate record;
        BEGIN
            FOR group_row IN SELECT nazwa, MIN(id) AS keep_id FROM konwersacje
                WHERE typ='prywatna' GROUP BY nazwa HAVING COUNT(*)>1
            LOOP
                FOR duplicate IN SELECT id FROM konwersacje
                    WHERE typ='prywatna' AND nazwa=group_row.nazwa AND id<>group_row.keep_id
                LOOP
                    INSERT INTO uczestnicy_chat(konwersacja_id,pracownik_id,ostatnie_przeczytane_id)
                        SELECT group_row.keep_id,pracownik_id,ostatnie_przeczytane_id
                        FROM uczestnicy_chat WHERE konwersacja_id=duplicate.id
                        ON CONFLICT(konwersacja_id,pracownik_id) DO UPDATE
                        SET ostatnie_przeczytane_id=LEAST(COALESCE(uczestnicy_chat.ostatnie_przeczytane_id,0),
                                                        COALESCE(EXCLUDED.ostatnie_przeczytane_id,0));
                    UPDATE wiadomosci_chat SET konwersacja_id=group_row.keep_id WHERE konwersacja_id=duplicate.id;
                    UPDATE konwersacje SET ostatnia_aktualizacja=GREATEST(ostatnia_aktualizacja,
                        (SELECT ostatnia_aktualizacja FROM konwersacje WHERE id=duplicate.id))
                        WHERE id=group_row.keep_id;
                    DELETE FROM konwersacje WHERE id=duplicate.id;
                END LOOP;
            END LOOP;
        END $$""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_private_name ON konwersacje(nazwa) WHERE typ='prywatna'")
