"""Generate an OpenAPI 3 spec for every registered CKAN action.

The spec is built at runtime from the action registry, so it always matches
the running CKAN version and enabled plugins. Core CKAN docstrings (Sphinx
field lists) are parsed into request parameters, fields declared through
ckanext-scheming are layered on top, and the merged YAML configuration
(see ``config.py``) has the final say.
"""
import fnmatch
import functools
import inspect
import logging
import re

import ckan.logic as logic

from ckanext.openapidocs import config as docs_config

log = logging.getLogger(__name__)

_PARAM_RE = re.compile(r"^:param (?P<name>[\w.-]+):\s*(?P<desc>.*)$")
_TYPE_RE = re.compile(r"^:type (?P<name>[\w.-]+):\s*(?P<type>.*)$")
_FIELD_RE = re.compile(r"^:[a-zA-Z]+ ?[\w.-]*:")

# Order matters: first substring found wins.
_TYPE_MAP = (
    ("bool", "boolean"),
    ("int", "integer"),
    ("float", "number"),
    ("number", "number"),
    ("list", "array"),
    ("dict", "object"),
)

# Order matters twice over: the first prefix match wins when tagging an
# action, and this sequence is the order the tags appear in Swagger UI.
_TAG_RULES = (
    ("Datasets", ("package_", "dataset_", "datapackage_",
                  "show_popular_datasets", "current_package_list",
                  "bulk_update_", "scheming_dataset_", "license_list")),
    ("Resources", ("resource_", "datastore_", "format_autocomplete",
                   "aircan_")),
    ("Organizations", ("organization_",)),
    ("Groups", ("group_", "member_")),
    ("Tags & Vocabularies", ("tag_", "vocabulary_")),
    ("Users & Authentication", ("user_", "api_token_", "forgot_password",
                               "follow_", "unfollow_", "am_following_",
                               "followee_", "dashboard_", "get_site_user")),
    ("Activity", ("activity_", "recently_changed_",
                  "send_email_notifications")),
    ("Administration", ("deleted_", "config_option_", "task_status_",
                        "term_translation_", "job_", "status_show",
                        "help_show")),
)
_DEFAULT_TAG = "Other"

# Fields CKAN maintains itself. They appear in the validation schemas (and so
# in the generated request bodies), but a client has no business setting them,
# and Swagger UI leaves readOnly fields out of its sample payload.
_SERVER_MANAGED_FIELDS = frozenset({
    "state",
    "type",
    "plugin_data",
    "created",
    "last_modified",
    "metadata_created",
    "metadata_modified",
    "hash",
    "cache_url",
    "cache_last_updated",
    "mimetype_inner",
    "datastore_active",
    "position",
    "revision_id",
    "relationships_as_object",
    "relationships_as_subject",
})
# Tag display order in Swagger UI: the _TAG_RULES order, then "Other" last.
_TAG_ORDER = [tag for tag, _ in _TAG_RULES] + [_DEFAULT_TAG]



def _openapi_type(rst_type):
    lowered = rst_type.lower()
    for token, openapi_type in _TYPE_MAP:
        if token in lowered:
            return openapi_type
    return "string"


def parse_docstring(doc):
    """Split a CKAN action docstring into a description and its parameters.

    Returns ``{"description": str, "params": [{"name", "description",
    "type"}], "returns": str}``. Sphinx ``:param name:`` / ``:type name:``
    field lists are recognised; unknown fields are dropped.
    """
    description_lines = []
    params = []
    params_by_name = {}
    returns = ""
    # Tracks where indented continuation lines should be appended.
    current = None

    for line in (doc or "").splitlines():
        stripped = line.strip()
        param_match = _PARAM_RE.match(stripped)
        type_match = _TYPE_RE.match(stripped)
        if param_match:
            param = {
                "name": param_match.group("name"),
                "description": param_match.group("desc").strip(),
                "type": "string",
            }
            params.append(param)
            params_by_name[param["name"]] = param
            current = ("param", param)
        elif type_match:
            param = params_by_name.get(type_match.group("name"))
            if param:
                param["type"] = _openapi_type(type_match.group("type"))
            current = None
        elif stripped.startswith((":returns:", ":return:")):
            returns = stripped.split(":", 2)[2].strip()
            current = ("returns", None)
        elif _FIELD_RE.match(stripped):
            current = None
        elif current and stripped:
            kind, param = current
            if kind == "param":
                param["description"] = f"{param['description']} {stripped}".strip()
            else:
                returns = f"{returns} {stripped}".strip()
        elif current is None or not stripped:
            if not params and not returns and current is None:
                description_lines.append(line)
            if not stripped:
                current = None

    description = "\n".join(description_lines).strip()
    # Double backticks are reStructuredText; Swagger UI renders markdown.
    description = description.replace("``", "`")
    for param in params:
        param["description"] = param["description"].replace("``", "`")
    return {
        "description": description,
        "params": params,
        "returns": returns.replace("``", "`"),
    }


def _core_actions():
    """Map action name -> function for CKAN core actions, keeping the core
    docstrings that chained (wrapped) actions lose in the registry."""
    import ckan.logic.action.create
    import ckan.logic.action.delete
    import ckan.logic.action.get
    import ckan.logic.action.patch
    import ckan.logic.action.update

    core = {}
    for module in (
        ckan.logic.action.get,
        ckan.logic.action.create,
        ckan.logic.action.update,
        ckan.logic.action.patch,
        ckan.logic.action.delete,
    ):
        for name, func in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith("_") and func.__module__ == module.__name__:
                core.setdefault(name, func)
    return core


def _registered_actions():
    # get_action() builds the full registry (core + all plugins) on first
    # call; _actions is private but stable across CKAN 2.x.
    logic.get_action("package_show")
    return dict(logic._actions)


def _unwrap(func):
    while isinstance(func, functools.partial):
        func = func.func
    return func


def _action_docstring(func, core_func):
    """The best available docstring for an action.

    Core CKAN's own docstring wins, since a chained action's wrapper rarely
    repeats it. Chained actions that only wrap (no docstring of their own)
    otherwise surface `functools.partial`'s docstring, which is noise.
    """
    if core_func is not None:
        core_doc = inspect.getdoc(core_func)
        if core_doc:
            return core_doc

    unwrapped = _unwrap(func)
    if unwrapped is functools.partial:
        return ""
    doc = inspect.getdoc(unwrapped) or ""
    if doc == (inspect.getdoc(functools.partial) or ""):
        return ""
    return doc


def _is_read_only(func, core_func):
    return bool(
        getattr(func, "side_effect_free", False)
        or getattr(core_func, "side_effect_free", False)
    )


def _tag_order(overrides):
    """Tag display order: the configured order, then any others.

    A `tag_order` list in the configuration wins; tags it does not name keep
    the built-in order, and anything still unaccounted for sorts last.
    """
    configured = [str(tag) for tag in overrides.get("tag_order") or []]
    remaining = [tag for tag in _TAG_ORDER if tag not in configured]
    return configured + remaining


def _action_order_index(overrides):
    """Map action name -> rank from the configuration's `order` list.

    Entries may be exact names or globs, so `package_*` orders a whole family
    without naming each member.
    """
    return [str(entry) for entry in overrides.get("order") or []]


def _operation_sort_key(name, tag, read_only, tag_order, order_patterns):
    """Order actions for display: by tag, then the configured order, then name.

    Swagger UI lists operations in spec order, so this is the order a reader
    sees. Within a tag, actions named by the configuration's `order` list come
    first in that order; the rest follow with reads ahead of writes, so
    browsing an entity starts with how to look it up.
    """
    try:
        tag_rank = tag_order.index(tag)
    except ValueError:
        tag_rank = len(tag_order)

    for rank, pattern in enumerate(order_patterns):
        if name == pattern or fnmatch.fnmatchcase(name, pattern):
            # 0 keeps configured actions ahead of everything else in the tag.
            return (tag_rank, 0, rank, name)
    return (tag_rank, 1, 0 if read_only else 1, name)


def _tag_for(name):
    for tag, prefixes in _TAG_RULES:
        if name.startswith(prefixes):
            return tag
    return _DEFAULT_TAG


def _summary_from(description, name):
    first_line = description.strip().split("\n", 1)[0].strip()
    if not first_line:
        return f"Call the {name} action"
    if len(first_line) > 120:
        first_line = first_line[:117].rstrip() + "..."
    return first_line.rstrip(".")


def _merge_properties(base, extra):
    """Merge property definitions field by field, with `extra` winning.

    Merging per field rather than replacing wholesale keeps a description
    written in one layer when a later layer only adds an enum or an example.
    """
    merged = {name: dict(prop) for name, prop in base.items()}
    for name, prop in extra.items():
        merged.setdefault(name, {}).update(prop)
    return merged


def _scheming_type(field):
    """Infer an OpenAPI type from a scheming field's preset and validators."""
    preset = field.get("preset") or ""
    validators = field.get("validators") or ""
    if preset in ("json_object", "dataset_organization"):
        return "object" if preset == "json_object" else "string"
    if preset == "multiple_checkbox" or "list_of_strings" in validators:
        return "array"
    if "boolean_validator" in validators:
        return "boolean"
    if "int_validator" in validators:
        return "integer"
    return "string"


def _scheming_description(field):
    """Build a field description from its scheming metadata."""
    parts = []
    help_text = (field.get("help_text") or "").strip()
    if help_text:
        parts.append(help_text)
    elif field.get("label"):
        parts.append(str(field["label"]).strip())
    if field.get("form_placeholder"):
        parts.append(f"Example: {field['form_placeholder']}.")
    if field.get("default") is not None:
        parts.append(f"Defaults to {field['default']}.")
    return " ".join(parts)


def _scheming_properties(fields):
    """Turn scheming field definitions into OpenAPI request properties."""
    properties = {}
    required = []
    for field in fields or []:
        name = field.get("field_name")
        if not name:
            continue
        prop = {"type": _scheming_type(field)}
        description = _scheming_description(field)
        if description:
            prop["description"] = description
        choices = [
            choice["value"]
            for choice in field.get("choices") or []
            if isinstance(choice, dict) and "value" in choice
        ]
        if choices:
            prop["enum"] = choices
        if field.get("default") is not None:
            prop["example"] = field["default"]
        properties[name] = prop

        validators = field.get("validators") or ""
        if "not_empty" in validators and "ignore_missing" not in validators:
            required.append(name)
    return properties, required


def _scheming_schemas():
    """Return (dataset_properties, dataset_required, resource_properties).

    Empty when ckanext-scheming is not enabled, so the spec still builds.
    """
    try:
        show = logic.get_action("scheming_dataset_schema_show")
    except KeyError:
        return {}, [], {}, []

    try:
        schema = show({"ignore_auth": True}, {"type": "dataset"})
    except Exception:
        log.exception("Could not read the scheming dataset schema")
        return {}, [], {}, []

    dataset_props, dataset_required = _scheming_properties(
        schema.get("dataset_fields")
    )
    resource_props, resource_required = _scheming_properties(
        schema.get("resource_fields")
    )
    return dataset_props, dataset_required, resource_props, resource_required


# Actions whose request body describes a dataset / a resource, and so should
# carry the scheming fields the portal actually accepts.
_DATASET_WRITE_ACTIONS = frozenset({
    "package_create", "package_update", "package_patch",
})
_RESOURCE_WRITE_ACTIONS = frozenset({
    "resource_create", "resource_update", "resource_patch",
})


def _matches_any(name, patterns):
    """True when `name` equals or glob-matches any of `patterns`."""
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _selected_actions(actions, overrides):
    """Filter the action registry through the overrides file.

    `include_only` (when given) restricts the spec to matching actions, and
    `exclude` removes them; both accept exact names or globs like `*_patch`.
    An action marked `hidden: true` is dropped too. Exclusions win, so an
    action can be allowed broadly and hidden individually.
    """
    include_only = overrides.get("include_only") or []
    exclude = list(overrides.get("exclude") or [])
    exclude += [
        name
        for name, override in (overrides.get("actions") or {}).items()
        if (override or {}).get("hidden")
    ]

    selected = {}
    for name, func in actions.items():
        if include_only and not _matches_any(name, include_only):
            continue
        if _matches_any(name, exclude):
            continue
        selected[name] = func
    return selected


def _responses():
    success = {
        "description": "Success. The action result is in the `result` key.",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ActionResponse"}
            }
        },
    }
    return {
        "200": success,
        "403": {"$ref": "#/components/responses/NotAuthorized"},
        "404": {"$ref": "#/components/responses/NotFound"},
        "409": {"$ref": "#/components/responses/ValidationError"},
    }


def _error_response(description):
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
            }
        },
    }


def _build_operation(name, description, tag, properties, required,
                     summary=None, deprecated=False):
    operation = {
        "operationId": name,
        "tags": [tag],
        "summary": summary or _summary_from(description, name),
        "description": description,
        "responses": _responses(),
    }
    if deprecated:
        operation["deprecated"] = True
    if properties:
        schema = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        operation["requestBody"] = {
            "required": bool(required),
            "content": {"application/json": {"schema": schema}},
        }
    return operation


def _get_operation(post_operation, properties, required):
    operation = dict(post_operation)
    operation.pop("requestBody", None)
    if properties:
        operation["parameters"] = [
            {
                "name": param_name,
                "in": "query",
                "required": param_name in (required or []),
                "description": prop.get("description", ""),
                # Everything except the description is a JSON Schema keyword
                # (type, enum, format, ...) and belongs in the param schema.
                # readOnly is meaningless on a query parameter, which is
                # always an input.
                "schema": {
                    key: value
                    for key, value in prop.items()
                    if key not in ("description", "readOnly")
                } or {"type": "string"},
            }
            for param_name, prop in properties.items()
        ]
    return operation


def build_spec(overrides=None):
    """Build the OpenAPI document.

    `overrides` is the merged YAML configuration; it is loaded from the
    configured layers when not supplied.
    """
    if overrides is None:
        overrides = docs_config.load_config()
    action_overrides = overrides.get("actions") or {}
    core = _core_actions()
    (
        scheming_dataset_props,
        scheming_dataset_required,
        scheming_resource_props,
        scheming_resource_required,
    ) = _scheming_schemas()
    paths = {}

    actions = _selected_actions(_registered_actions(), overrides)

    # Emit paths in display order: Swagger UI renders them as the spec lists
    # them, so the ordering has to happen here rather than in the template.
    tag_order = _tag_order(overrides)
    order_patterns = _action_order_index(overrides)

    def sort_key(item):
        name, func = item
        action_override = action_overrides.get(name) or {}
        tag = (action_override.get("tags") or [_tag_for(name)])[0]
        return _operation_sort_key(
            name,
            tag,
            _is_read_only(func, core.get(name)),
            tag_order,
            order_patterns,
        )

    for name, func in sorted(actions.items(), key=sort_key):
        core_func = core.get(name)
        doc = _action_docstring(func, core_func)
        parsed = parse_docstring(doc)
        override = action_overrides.get(name) or {}

        description = override.get("description") or parsed["description"]
        prepend = override.get("prepend_description")
        if prepend:
            description = f"{prepend.strip()}\n\n{description}".strip()
        if override.get("sysadmin"):
            description = f"**Sysadmin only.**\n\n{description}".strip()
        if parsed["returns"] and not override.get("description"):
            description = f"{description}\n\n**Returns:** {parsed['returns']}"

        properties = {
            param["name"]: {
                "type": param["type"],
                "description": param["description"],
            }
            for param in parsed["params"]
        }
        # Layer the fields the portal's dataset schema actually accepts over
        # the docstring-derived ones, then let the curated overrides win.
        schema_required = []
        if name in _DATASET_WRITE_ACTIONS:
            properties = _merge_properties(properties, scheming_dataset_props)
            schema_required = scheming_dataset_required
        elif name in _RESOURCE_WRITE_ACTIONS:
            properties = _merge_properties(properties, scheming_resource_props)
            schema_required = scheming_resource_required

        properties = _merge_properties(
            properties, override.get("properties") or {}
        )
        for field in _SERVER_MANAGED_FIELDS & set(properties):
            properties[field].setdefault("readOnly", True)

        required = override.get("required")
        if required is None:
            # Only *_create enforces the schema's required fields; update and
            # patch accept a partial payload.
            required = schema_required if name.endswith("_create") else []

        tag = (override.get("tags") or [_tag_for(name)])[0]
        post_operation = _build_operation(
            name,
            description,
            tag,
            properties,
            required,
            summary=override.get("summary"),
            deprecated=override.get("deprecated", False),
        )
        # Read-only actions are documented as GET only. POST works too, but
        # showing both doubles the page for no benefit; the note in the spec
        # description covers it.
        if _is_read_only(func, core_func):
            operations = {
                "get": _get_operation(post_operation, properties, required)
            }
        else:
            operations = {"post": post_operation}
        paths[f"/api/3/action/{name}"] = operations

    info = {
        "title": "CKAN Action API",
        "version": "3",
    }
    info.update(overrides.get("info") or {})

    tag_descriptions = overrides.get("tags") or {}
    used_tags = {
        operation["tags"][0]
        for operations in paths.values()
        for operation in operations.values()
    }
    # Swagger UI renders tags in the order the spec lists them, so emit the
    # curated order first and append anything unexpected alphabetically.
    ordered_tags = [tag for tag in tag_order if tag in used_tags]
    ordered_tags += sorted(used_tags - set(tag_order))
    tags = [
        {"name": tag, **({"description": tag_descriptions[tag]}
                         if tag in tag_descriptions else {})}
        for tag in ordered_tags
    ]

    return {
        "openapi": "3.0.3",
        "info": info,
        # No `servers` list: with a single entry Swagger UI still renders a
        # dropdown, and omitting it makes requests relative to wherever the
        # docs are served, which is the same host a client wants to call.
        "tags": tags,
        "paths": paths,
        # One requirement only: a second, empty entry shows up in the
        # Authorize dialog as an option with no input to fill in.
        "security": [{"ApiToken": []}],
        "components": {
            "securitySchemes": {
                "ApiToken": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "Authorization",
                    "description": (
                        "Paste your API token below. It is sent as-is in the "
                        "`Authorization` header, with no `Bearer` prefix."
                    ),
                }
            },
            "schemas": {
                "ActionResponse": {
                    "type": "object",
                    "properties": {
                        "help": {"type": "string"},
                        "success": {"type": "boolean", "example": True},
                        "result": {
                            "description": "The action result; its shape "
                            "depends on the action."
                        },
                    },
                },
                "ErrorResponse": {
                    "type": "object",
                    "properties": {
                        "help": {"type": "string"},
                        "success": {"type": "boolean", "example": False},
                        "error": {"type": "object"},
                    },
                },
            },
            "responses": {
                "NotAuthorized": _error_response(
                    "Not authorized to call this action."
                ),
                "NotFound": _error_response("The object was not found."),
                "ValidationError": _error_response(
                    "Validation error; details are in the `error` object."
                ),
            },
        },
    }


_spec_cache = None


def get_spec():
    """The generated spec, built once per process.

    Building walks every action's docstring, so the result is cached. Call
    `clear_cache()` after changing configuration in a running process.
    """
    global _spec_cache
    if _spec_cache is None:
        _spec_cache = build_spec()
    return _spec_cache


def clear_cache():
    global _spec_cache
    _spec_cache = None
