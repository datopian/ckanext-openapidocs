# ckanext-openapidocs

Serves an OpenAPI 3 spec and a Swagger UI page for any CKAN instance.

The spec is **generated from the running instance**: it walks the action
registry, so it always matches the deployed CKAN version and the plugins that
are actually enabled. Request fields come from CKAN's own docstrings and, when
ckanext-scheming is in use, from the dataset schema — so no hand-written action
list to drift out of date. YAML layers on top supply wording, examples and
whatever the code cannot infer.

- `GET /api/docs` — Swagger UI (configurable path)
- `GET /api/openapi.json` — the raw spec (configurable path)

Swagger UI is vendored, so the page works with no external network access and
no CDN.

## Requirements

CKAN 2.10 or 2.11. `ckanext-scheming` is optional: when enabled, dataset and
resource fields are read from its schema, including their allowed values.

## Installation

```sh
pip install -e .
```

Then add the plugin to `ckan.plugins`:

```ini
ckan.plugins = ... openapidocs
```

## Configuration

Configuration arrives in three layers, each overriding the one before:

1. **`ckanext/openapidocs/openapi.yaml`** in this extension — generic CKAN
   documentation that applies to any instance.
2. **Project YAML** listed in `ckanext.openapidocs.spec_files` — your portal's
   own title, wording, examples and extra fields.
3. **Individual CKAN config options** — set per deployment, including from the
   environment.

Mappings merge key by key, so a project layer can add one field without
restating the rest. Lists are replaced outright, so a project can shorten an
inherited list rather than only ever appending to it.

### Config options

| Option | Default | Description |
|---|---|---|
| `ckanext.openapidocs.spec_files` | — | Whitespace-separated YAML layers, each `module:file.yaml` or an absolute path. Loaded after this extension's base file. |
| `ckanext.openapidocs.title` | from YAML | Overrides `info.title`. |
| `ckanext.openapidocs.version` | from YAML | Overrides `info.version`. |
| `ckanext.openapidocs.description` | from YAML | Overrides `info.description`. |
| `ckanext.openapidocs.exclude` | from YAML | Replaces the exclude list. Names or globs, whitespace-separated. Set it empty to publish everything. |
| `ckanext.openapidocs.include_only` | — | When set, documents only matching actions. |
| `ckanext.openapidocs.docs_path` | `/api/docs` | Where Swagger UI is mounted. |
| `ckanext.openapidocs.spec_path` | `/api/openapi.json` | Where the raw spec is served. |
| `ckanext.openapidocs.primary_color` | slate blue | Brand colour for links, inline code and the Authorize/Execute buttons. Any CSS colour; anything else is ignored with a warning, since the value is written into a `<style>` block. |
| `ckanext.openapidocs.header_color` | `#1f2937` | Background of the page header, separate from the brand colour so the bar can be branded without recolouring every accent. |
| `ckanext.openapidocs.site_title` | the spec title | Title shown in the page header. |
| `ckanext.openapidocs.logo_url` | — | Logo shown in the page header. |

Example, pointing at a project layer and relabelling one environment:

```ini
ckanext.openapidocs.spec_files = ckanext.myportal:openapi_myportal.yaml
ckanext.openapidocs.title = My Portal API (staging)
```

With `ckanext-envvars`, the same options come from the environment:

```sh
CKAN___CKANEXT__OPENAPIDOCS__SPEC_FILES="ckanext.myportal:openapi_myportal.yaml"
CKAN___CKANEXT__OPENAPIDOCS__TITLE="My Portal API (staging)"
```

### Choosing which actions appear

Every registered action is documented by default. Two top-level YAML keys
change that, both accepting exact names or globs:

```yaml
exclude:
  - job_*
  - config_option_*

include_only:      # when set, only these are documented
  - package_*
  - resource_*
```

`exclude` wins over `include_only`, so a family can be allowed broadly and
individual members dropped. An action can also be hidden on its own:

```yaml
actions:
  status_show:
    hidden: true
```

Listing an action the running instance does not have is harmless — it is
ignored rather than published as a path that would 404.

### Styling

The page ships a light-only stylesheet driven by CSS custom properties, so a
portal rebrands by setting one colour rather than shipping CSS:

```ini
ckanext.openapidocs.primary_color = #7A3864
ckanext.openapidocs.header_color = #1f2937
ckanext.openapidocs.site_title = My Portal API
ckanext.openapidocs.logo_url = /base/images/logo.png
```

`primary_color` drives links, inline code and the Authorize/Execute buttons.
HTTP method badges keep their conventional colours instead — blue `GET`, green
`POST`, amber `PUT`/`PATCH`, red `DELETE` — so reads and writes stay
distinguishable whatever a portal brands with, and the palette matches what
readers know from other API docs. The header is neutral by default rather than
brand-coloured, since a full-width block of saturated colour competes with the
content; set `header_color` to brand it.

Swagger UI's own topbar, servers dropdown, tag filter and duplicate title block
are hidden, and its Authorize dialog is restyled (the raw `ApiToken (apiKey)`
heading and the `Name:` / empty `In:` rows are dropped).

### Ordering the page

Swagger UI lists tags and operations in document order, so the order is set in
YAML:

```yaml
tag_order:                # the order tags appear
  - Datasets
  - Resources

order:                    # the order actions appear within their tag
  - package_search        # exact names, matched in the order written
  - package_show
  - package_create
  - resource_view_*       # or globs, to place a whole family
```

Anything `order` does not name follows its tag's listed actions, with reads
(`GET`) before writes (`POST`) and then alphabetically — so a newly enabled
plugin's actions land in a sensible place without any config change.

### Documenting an action

```yaml
actions:
  package_create:
    summary: Create a dataset            # replaces the generated summary
    prepend_description: |               # added above CKAN's own description
      Datasets are private until published.
    properties:                          # merged into the request body
      my_field:
        type: string
        example: some value
        description: What this field is for.
    required: [name]
    tags: [Datasets]
    sysadmin: true                       # notes the restriction in the docs
```

Keys: `summary`, `description` (replaces entirely), `prepend_description`,
`properties`, `required`, `tags`, `sysadmin`, `hidden`.

**Give properties an `example` rather than adding a top-level request
example.** Swagger UI prints a top-level example verbatim and hides every
field it leaves out; per-property examples let it build a sample payload
covering all documented fields.

## How the spec is generated

- **Actions** come from CKAN's action registry, so enabled plugins are
  included automatically.
- **Request fields** come from the `:param:` / `:type:` field lists in each
  action's docstring, then from the ckanext-scheming dataset schema for
  dataset and resource writes, then from your YAML.
- **Read-only actions** are documented as `GET` with query parameters; write
  actions as `POST` with a JSON body. `POST` also works for read-only actions,
  which the page explains, but listing both would double its length.
- **CKAN-managed fields** (`state`, `metadata_created`, `hash`, …) are marked
  `readOnly`, keeping them out of the sample payload.
- **Tags** group actions by entity (Datasets, Resources, Organizations, …) in
  a fixed order; anything unrecognised lands in `Other`.

The spec is built once per process and cached. Call
`ckanext.openapidocs.spec.clear_cache()` after changing configuration in a
live process.

## Tests

```sh
pytest --ckan-ini=test.ini ckanext/openapidocs/tests
```
