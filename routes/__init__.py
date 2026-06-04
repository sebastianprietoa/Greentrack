def _alias_endpoint(app, source_endpoint, alias_endpoint):
    view_func = app.view_functions.get(source_endpoint)
    rules = app.url_map._rules_by_endpoint.get(source_endpoint)

    if view_func is None or rules is None:
        raise RuntimeError(f"No se pudo registrar el endpoint '{source_endpoint}'.")

    app.view_functions[alias_endpoint] = view_func
    app.url_map._rules_by_endpoint[alias_endpoint] = list(rules)


def register_blueprints(app):
    from .auth_routes import auth_bp

    app.register_blueprint(auth_bp)
    _alias_endpoint(app, "auth.inicio", "inicio")
    _alias_endpoint(app, "auth.logout", "logout")
