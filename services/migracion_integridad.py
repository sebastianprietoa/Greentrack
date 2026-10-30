"""Reconciliación recuperable y claves foráneas de consumos relacionados."""


def reconciliar_integridad(connection):
    """Archiva vínculos inválidos y protege los nuevos; el llamador confirma."""
    cursor = connection.cursor()
    cursor.execute('LOCK TABLE registros, combustible_movil, resultados_huella_suministro_agua IN SHARE ROW EXCLUSIVE MODE')
    cursor.execute('''CREATE TABLE IF NOT EXISTS integridad_suministro_cuarentena (
        source_id INTEGER PRIMARY KEY,
        fila_original JSONB NOT NULL,
        motivo TEXT NOT NULL,
        fecha_archivo TIMESTAMP NOT NULL DEFAULT NOW()
    )''')
    cursor.execute('''INSERT INTO integridad_suministro_cuarentena (source_id, fila_original, motivo)
        SELECT h.id, to_jsonb(h), 'Consumo maestro inexistente o empresa distinta'
        FROM resultados_huella_suministro_agua h
        WHERE NOT EXISTS (SELECT 1 FROM registros r WHERE r.id=h.registro_id AND r.empresa=h.empresa)
        ON CONFLICT (source_id) DO UPDATE SET fila_original=EXCLUDED.fila_original,
            motivo=EXCLUDED.motivo, fecha_archivo=NOW()''')
    archived = cursor.rowcount
    cursor.execute('''DELETE FROM resultados_huella_suministro_agua h
        USING integridad_suministro_cuarentena q
        WHERE h.id=q.source_id AND to_jsonb(h)=q.fila_original
        AND NOT EXISTS (SELECT 1 FROM registros r WHERE r.id=h.registro_id AND r.empresa=h.empresa)''')
    quarantined = cursor.rowcount
    # Las filas móviles históricas sin maestro se conservan, sin un vínculo inválido.
    cursor.execute('''UPDATE combustible_movil cm SET registro_id=NULL
        WHERE cm.registro_id IS NOT NULL AND NOT EXISTS
        (SELECT 1 FROM registros r WHERE r.id=cm.registro_id AND r.empresa=cm.empresa)''')
    detached = cursor.rowcount
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS registros_id_empresa_integridad_key ON registros (id, empresa)')
    for table, name in [
        ('resultados_huella_suministro_agua','suministro_consumo_empresa_fk'),
        ('combustible_movil','movil_consumo_empresa_fk'),
    ]:
        cursor.execute('SELECT confupdtype FROM pg_constraint WHERE conrelid=%s::regclass AND conname=%s', (table,name))
        existing=cursor.fetchone()
        if existing and existing[0]!='c':
            cursor.execute(f'ALTER TABLE {table} DROP CONSTRAINT {name}')
        if not existing or existing[0]!='c':
            # Identificadores constantes, nunca datos de una solicitud.
            cursor.execute(f'''ALTER TABLE {table} ADD CONSTRAINT {name}
                FOREIGN KEY (registro_id, empresa) REFERENCES registros (id, empresa)
                ON UPDATE CASCADE ON DELETE CASCADE NOT VALID''')
        cursor.execute(f'ALTER TABLE {table} VALIDATE CONSTRAINT {name}')
    return {'archivados':archived,'retirados_de_calculos':quarantined,'moviles_desvinculados':detached}
