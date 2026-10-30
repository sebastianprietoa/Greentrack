"""Operaciones de consumo relacionadas; la transacción pertenece al llamador."""


def eliminar_consumo(cursor, empresa, registro_id):
    """Elimina maestro, tracking móvil y suministro únicamente de su empresa."""
    cursor.execute(
        'SELECT id FROM registros WHERE id = %s AND empresa = %s FOR UPDATE',
        (registro_id, empresa),
    )
    if not cursor.fetchone():
        return False
    cursor.execute(
        'DELETE FROM resultados_huella_suministro_agua WHERE registro_id = %s AND empresa = %s',
        (registro_id, empresa),
    )
    cursor.execute(
        'DELETE FROM combustible_movil WHERE registro_id = %s AND empresa = %s',
        (registro_id, empresa),
    )
    cursor.execute(
        'DELETE FROM registros WHERE id = %s AND empresa = %s',
        (registro_id, empresa),
    )
    return True


def eliminar_consumo_movil(cursor, empresa, movil_id):
    cursor.execute(
        'SELECT registro_id FROM combustible_movil WHERE id = %s AND empresa = %s',
        (movil_id, empresa),
    )
    row = cursor.fetchone()
    if not row:
        return False
    if row[0] is not None:
        eliminado = eliminar_consumo(cursor, empresa, row[0])
        if not eliminado:
            # Tracking histórico cuyo maestro ya no existe o pertenece a otra empresa.
            cursor.execute(
                'DELETE FROM resultados_huella_suministro_agua WHERE registro_id = %s AND empresa = %s',
                (row[0], empresa),
            )
    cursor.execute(
        'DELETE FROM combustible_movil WHERE id = %s AND empresa = %s',
        (movil_id, empresa),
    )
    return True
