import json

from flask import Blueprint, Response

import ckan.plugins.toolkit as tk

from ckanext.openapidocs import config as docs_config
from ckanext.openapidocs import spec as docs_spec


def openapi_spec():
    # Serialise directly rather than with jsonify: Flask sorts JSON keys by
    # default, and Swagger UI renders paths and tags in document order, so
    # sorting would silently undo the configured `order` / `tag_order`.
    return Response(
        json.dumps(docs_spec.get_spec(), sort_keys=False),
        mimetype="application/json",
    )


def api_docs():
    info = docs_spec.get_spec().get("info") or {}
    title = info.get("title", "API Documentation")
    theme = docs_config.theme()
    return tk.render(
        "openapidocs/api_docs.html",
        extra_vars={
            "spec_url": docs_config.spec_path(),
            "page_title": title,
            # The header shows the configured site title, falling back to the
            # spec's own title so the page is never unlabelled.
            "site_title": theme["site_title"] or title,
            "api_version": info.get("version", ""),
            "primary_color": theme["primary_color"],
            "header_color": theme["header_color"],
            "logo_url": theme["logo_url"],
        },
    )


def get_blueprints():
    # A fresh Blueprint per call: Flask forbids adding routes to a blueprint
    # that has already been registered, which happens whenever the app is
    # rebuilt in the same process (tests, config reloads).
    #
    # Paths are configurable so a portal can mount the docs elsewhere (say
    # under /docs) without the routes clashing with its own views.
    blueprint = Blueprint("openapidocs", __name__)
    blueprint.add_url_rule(
        docs_config.spec_path(), "openapi_spec", openapi_spec
    )
    blueprint.add_url_rule(docs_config.docs_path(), "api_docs", api_docs)
    return [blueprint]
