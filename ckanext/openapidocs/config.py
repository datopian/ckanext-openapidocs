"""Layered configuration for the generated API docs.

Configuration arrives in three layers, each overriding the one before:

1. ``openapi.yaml`` in this extension — generic CKAN documentation that
   applies to any instance.
2. Project YAML files listed in ``ckanext.openapidocs.spec_files`` — a portal's
   own wording, examples and extra fields.
3. Individual CKAN config options (``ckanext.openapidocs.title`` and friends),
   which are settable per deployment through environment variables.

Layer 3 exists so the same image can be deployed to several environments and
relabelled without editing YAML. CKAN maps ``CKAN___CKANEXT__OPENAPIDOCS__TITLE``
onto ``ckanext.openapidocs.title`` via ckanext-envvars.
"""
import copy
import logging
import os
import re

import yaml

import ckan.plugins.toolkit as tk

log = logging.getLogger(__name__)

CONFIG_PREFIX = "ckanext.openapidocs"

# Where the docs and the raw spec are served.
CONFIG_DOCS_PATH = f"{CONFIG_PREFIX}.docs_path"
CONFIG_SPEC_PATH = f"{CONFIG_PREFIX}.spec_path"
# Project YAML layers, whitespace-separated, each `module:file.yaml` or a path.
CONFIG_SPEC_FILES = f"{CONFIG_PREFIX}.spec_files"
# Individual scalar overrides.
CONFIG_TITLE = f"{CONFIG_PREFIX}.title"
CONFIG_VERSION = f"{CONFIG_PREFIX}.version"
CONFIG_DESCRIPTION = f"{CONFIG_PREFIX}.description"
# Action selection, whitespace-separated names or globs.
CONFIG_EXCLUDE = f"{CONFIG_PREFIX}.exclude"
CONFIG_INCLUDE_ONLY = f"{CONFIG_PREFIX}.include_only"

# Branding for the docs page. The stylesheet reads the colour from a custom
# property, so a portal can rebrand without shipping CSS.
CONFIG_PRIMARY_COLOR = f"{CONFIG_PREFIX}.primary_color"
CONFIG_HEADER_COLOR = f"{CONFIG_PREFIX}.header_color"
CONFIG_SITE_TITLE = f"{CONFIG_PREFIX}.site_title"
CONFIG_LOGO_URL = f"{CONFIG_PREFIX}.logo_url"

DEFAULT_DOCS_PATH = "/api/docs"
DEFAULT_SPEC_PATH = "/api/openapi.json"
BASE_SPEC_FILE = "ckanext.openapidocs:openapi.yaml"


class ConfigError(Exception):
    """Raised when a configured spec file is missing or malformed."""


def deep_merge(*layers):
    """Merge mapping layers left to right, with later layers winning.

    Mappings merge key by key so a project layer can add a single field
    without restating the rest. Lists are replaced outright, so a project can
    shorten an inherited list rather than only ever appending to it.
    """
    result = {}
    for layer in layers:
        if not layer:
            continue
        for key, value in layer.items():
            existing = result.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                result[key] = deep_merge(existing, value)
            else:
                result[key] = copy.deepcopy(value)
    return result


def resolve_path(location):
    """Turn ``module:file.yaml`` into an absolute path, as scheming does.

    A plain path is returned unchanged, so deployments can mount a spec file
    outside any Python package.
    """
    if ":" not in location:
        return location

    module_name, _, filename = location.partition(":")
    try:
        module = __import__(module_name, fromlist=[""])
    except ImportError as error:
        raise ConfigError(
            f"Could not import '{module_name}' for spec file '{location}'"
        ) from error

    module_dir = os.path.dirname(os.path.abspath(module.__file__))
    return os.path.join(module_dir, filename)


def load_yaml(path):
    """Read a YAML file, returning {} when it is absent or empty."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as stream:
            return yaml.safe_load(stream) or {}
    except yaml.YAMLError as error:
        raise ConfigError(f"Could not parse '{path}': {error}") from error


def _config_get(key, default=None):
    """Read a CKAN config option, tolerating an unconfigured CKAN."""
    try:
        return tk.config.get(key, default)
    except (AttributeError, RuntimeError, TypeError):
        return default


def _as_list(value):
    """Split a whitespace- or comma-separated config value into a list."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    text = str(value).replace(",", " ").strip()
    if not text:
        return []
    return text.split()


def _ckan_config_layer():
    """Build an override layer from the individual CKAN config options."""
    layer = {}
    info = {}

    title = _config_get(CONFIG_TITLE)
    if title:
        info["title"] = title
    version = _config_get(CONFIG_VERSION)
    if version:
        info["version"] = str(version)
    description = _config_get(CONFIG_DESCRIPTION)
    if description:
        info["description"] = description
    if info:
        layer["info"] = info

    for key, option in (
        ("exclude", CONFIG_EXCLUDE),
        ("include_only", CONFIG_INCLUDE_ONLY),
    ):
        # A configured-but-empty value clears an inherited list, so `exclude =`
        # publishes everything the base YAML hides.
        raw = _config_get(option)
        if raw is not None:
            layer[key] = _as_list(raw)

    return layer


def spec_file_locations():
    """The YAML layers to load: this extension's base, then the project's."""
    configured = _as_list(_config_get(CONFIG_SPEC_FILES)) or []
    return [BASE_SPEC_FILE] + configured


def load_config():
    """Load and merge every configuration layer."""
    layers = []
    for location in spec_file_locations():
        path = resolve_path(location)
        if not os.path.exists(path):
            raise ConfigError(f"Spec file not found: '{location}' ({path})")
        layers.append(load_yaml(path))
    layers.append(_ckan_config_layer())
    return deep_merge(*layers)


def docs_path():
    return _config_get(CONFIG_DOCS_PATH) or DEFAULT_DOCS_PATH


def spec_path():
    return _config_get(CONFIG_SPEC_PATH) or DEFAULT_SPEC_PATH


_COLOR_RE = re.compile(
    r"^(#[0-9a-fA-F]{3,8}"
    r"|rgba?\([\d\s.,%/]+\)"
    r"|hsla?\([\d\s.,%/deg]+\)"
    r"|[a-zA-Z]+)$"
)


def _color_option(option):
    """Read a colour config option, dropping anything that is not one.

    The value lands inside a `<style>` block on the docs page, so it is
    validated rather than injected as given.
    """
    color = (_config_get(option) or "").strip()
    if color and not _COLOR_RE.match(color):
        log.warning("Ignoring %s: %r is not a CSS colour", option, color)
        return ""
    return color


def theme():
    """Branding for the docs page.

    The colours are written into CSS custom properties, so the shipped
    stylesheet restyles itself around a portal's brand. Empty values keep the
    stylesheet's own defaults: a brand-neutral header, and HTTP method badges
    in their conventional colours.
    """
    return {
        "primary_color": _color_option(CONFIG_PRIMARY_COLOR),
        "header_color": _color_option(CONFIG_HEADER_COLOR),
        "site_title": (_config_get(CONFIG_SITE_TITLE) or "").strip(),
        "logo_url": (_config_get(CONFIG_LOGO_URL) or "").strip(),
    }
