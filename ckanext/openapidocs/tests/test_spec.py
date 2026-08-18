"""Tests for the generated OpenAPI spec.

These cover behaviour that holds for any CKAN instance; project-specific
documentation is tested by the project's own extension.
"""
import json

import pytest


@pytest.fixture(autouse=True)
def _clear_spec_cache():
    """The spec is cached per process, so config in one test must not leak."""
    from ckanext.openapidocs import spec as docs_spec

    docs_spec.clear_cache()
    yield
    docs_spec.clear_cache()


@pytest.fixture()
def spec(app):
    response = app.get("/api/openapi.json")
    assert response.status_code == 200
    return json.loads(response.body)


class TestDocstringParsing:
    def test_parses_params_types_and_description(self):
        from ckanext.openapidocs import spec as docs_spec

        doc = (
            "Return the metadata of a dataset.\n"
            "\n"
            ":param id: the id or name of the dataset\n"
            ":type id: string\n"
            ":param include_tracking: add tracking information\n"
            "    (optional, default: ``False``)\n"
            ":type include_tracking: bool\n"
            "\n"
            ":rtype: dictionary\n"
        )
        parsed = docs_spec.parse_docstring(doc)

        assert parsed["description"].startswith("Return the metadata")
        params = {param["name"]: param for param in parsed["params"]}
        assert params["id"]["type"] == "string"
        assert params["include_tracking"]["type"] == "boolean"
        assert "tracking information" in params["include_tracking"]["description"]

    def test_handles_docstring_without_params(self):
        from ckanext.openapidocs import spec as docs_spec

        parsed = docs_spec.parse_docstring("Just a description.")

        assert parsed["description"] == "Just a description."
        assert parsed["params"] == []

    def test_does_not_leak_the_functools_partial_docstring(self, spec):
        """Chained actions wrap the original in a functools.partial.

        Without care its docstring ("partial(func, *args, ...)") surfaces as
        the action's description.
        """
        for path, operations in spec["paths"].items():
            for operation in operations.values():
                assert "partial(func" not in operation.get("description", ""), (
                    f"partial docstring leaked into {path}"
                )


class TestOpenAPISpec:
    def test_returns_valid_openapi3_document(self, spec):
        assert spec["openapi"].startswith("3.")
        assert spec["info"]["title"]
        assert spec["paths"]

    def test_covers_core_actions(self, spec):
        assert "/api/3/action/package_show" in spec["paths"]
        assert "/api/3/action/package_create" in spec["paths"]
        assert "/api/3/action/organization_list" in spec["paths"]

    def test_read_only_actions_are_get_only(self, spec):
        package_show = spec["paths"]["/api/3/action/package_show"]
        assert "get" in package_show
        assert "post" not in package_show

    def test_write_actions_are_post_only(self, spec):
        package_create = spec["paths"]["/api/3/action/package_create"]
        assert "post" in package_create
        assert "get" not in package_create

    def test_description_notes_post_also_works_for_read_actions(self, spec):
        assert "POST" in spec["info"]["description"]

    def test_core_docstring_params_become_query_parameters(self, spec):
        get_op = spec["paths"]["/api/3/action/package_show"]["get"]
        names = [param["name"] for param in get_op.get("parameters", [])]
        assert "id" in names

    def test_request_body_includes_full_schema_fields(self, spec):
        """CKAN docstrings list only some params; the schema has more."""
        post_op = spec["paths"]["/api/3/action/package_create"]["post"]
        properties = post_op["requestBody"]["content"]["application/json"][
            "schema"
        ]["properties"]
        for field in ("author", "maintainer_email", "private", "extras", "tags"):
            assert field in properties, f"missing schema field: {field}"

    def test_api_token_security_scheme_declared(self, spec):
        schemes = spec["components"]["securitySchemes"]
        header_schemes = [
            scheme
            for scheme in schemes.values()
            if scheme.get("in") == "header" and scheme.get("name") == "Authorization"
        ]
        assert header_schemes

    def test_only_one_authorization_is_offered(self, spec):
        """The Authorize dialog should show a single API token field.

        A second, empty security requirement renders as an extra entry with no
        input, which reads as a broken option.
        """
        assert len(spec["components"]["securitySchemes"]) == 1
        assert spec["security"] == [{"ApiToken": []}]

    def test_token_scheme_says_no_bearer_prefix(self, spec):
        scheme = spec["components"]["securitySchemes"]["ApiToken"]

        assert "apiKey" == scheme["type"]
        assert "no `Bearer` prefix" in scheme["description"]


class TestSwaggerUIOptions:
    def test_tag_filter_box_is_disabled(self, app):
        """The page is ordered deliberately; the filter box invites reordering."""
        page = app.get("/api/docs").body

        assert "filter: true" not in page

    def test_no_servers_dropdown(self, spec):
        """A single-entry servers list renders as a pointless dropdown.

        Requests are relative to wherever the docs are served, which is what a
        client wants anyway.
        """
        assert "servers" not in spec

    def test_header_shows_the_title_once(self, app):
        """Swagger UI's own info title would repeat the page header."""
        page = app.get("/api/docs").body

        assert page.count("openapidocs-header-title") == 1

    def test_every_operation_is_tagged(self, spec):
        for path, operations in spec["paths"].items():
            for method, operation in operations.items():
                assert operation.get("tags"), f"untagged: {method} {path}"

    def test_tags_are_listed_in_curated_order(self, spec):
        names = [tag["name"] for tag in spec["tags"]]
        expected = [
            "Datasets",
            "Resources",
            "Organizations",
            "Groups",
            "Tags & Vocabularies",
            "Users & Authentication",
            "Activity",
            "Administration",
        ]
        present = [name for name in expected if name in names]
        assert [name for name in names if name in expected] == present
        if "Other" in names:
            assert names[-1] == "Other"

    def test_no_top_level_example_shadows_the_full_schema(self, spec):
        """Swagger UI prints a top-level example verbatim.

        Any field the example omits then disappears from the page, so payload
        samples must come from per-property examples instead.
        """
        for path, operations in spec["paths"].items():
            for method, operation in operations.items():
                content = (operation.get("requestBody") or {}).get(
                    "content", {}
                ).get("application/json", {})
                assert "example" not in content, (
                    f"top-level example shadows schema: {method} {path}"
                )

    def test_documented_fields_carry_example_values(self, spec):
        properties = spec["paths"]["/api/3/action/package_create"]["post"][
            "requestBody"
        ]["content"]["application/json"]["schema"]["properties"]
        for field in ("name", "title", "owner_org", "notes"):
            assert "example" in properties[field], f"{field} has no example"

    def test_server_managed_fields_marked_read_only(self, spec):
        """readOnly keeps CKAN-managed fields out of the sample payload."""
        properties = spec["paths"]["/api/3/action/package_create"]["post"][
            "requestBody"
        ]["content"]["application/json"]["schema"]["properties"]
        for field in ("state", "type"):
            if field in properties:
                assert properties[field].get("readOnly") is True


def _actions_in(spec, tag):
    return [
        path.rsplit("/", 1)[1]
        for path, operations in spec["paths"].items()
        for operation in operations.values()
        if operation["tags"][0] == tag
    ]


class TestOperationOrder:
    """Display order comes from the configuration, not from the code.

    Swagger UI renders operations in the order the spec lists them, so the
    spec's path order is what a reader sees.
    """

    def _build(self, overrides):
        from ckanext.openapidocs import spec as docs_spec

        return docs_spec.build_spec(overrides)

    def test_order_list_sets_the_order_within_a_tag(self, app):
        spec = self._build(
            {"order": ["package_show", "package_search", "package_create"]}
        )
        actions = _actions_in(spec, "Datasets")

        assert actions[:3] == [
            "package_show",
            "package_search",
            "package_create",
        ]

    def test_order_accepts_globs(self, app):
        spec = self._build({"order": ["package_s*", "package_create"]})
        actions = _actions_in(spec, "Datasets")

        # package_search and package_show both match, alphabetically within
        # the glob, and both precede the explicitly named create.
        assert actions.index("package_search") < actions.index("package_create")
        assert actions.index("package_show") < actions.index("package_create")

    def test_unordered_actions_put_reads_before_writes(self, app):
        spec = self._build({"order": []})
        methods = [
            method
            for operations in spec["paths"].values()
            for method, operation in operations.items()
            if operation["tags"][0] == "Datasets"
        ]
        first_post = next(
            (i for i, m in enumerate(methods) if m == "post"), len(methods)
        )
        last_get = max(
            (i for i, m in enumerate(methods) if m == "get"), default=-1
        )

        assert last_get < first_post, "a GET follows a POST"

    def test_unordered_actions_stay_alphabetical(self, app):
        spec = self._build({"order": []})
        actions = _actions_in(spec, "Datasets")
        reads = [
            name
            for name in actions
            if "get" in spec["paths"][f"/api/3/action/{name}"]
        ]

        assert reads == sorted(reads)

    def test_tag_order_sets_the_order_of_tags(self, app):
        spec = self._build({"tag_order": ["Organizations", "Datasets"]})
        names = [tag["name"] for tag in spec["tags"]]

        assert names[:2] == ["Organizations", "Datasets"]

    def test_tags_left_out_of_tag_order_keep_the_builtin_order(self, app):
        """Naming one tag promotes it; the rest keep their relative order."""
        spec = self._build({"tag_order": ["Groups"]})
        names = [tag["name"] for tag in spec["tags"]]

        assert names[0] == "Groups"
        assert names.index("Datasets") < names.index("Resources")
        assert names.index("Resources") < names.index("Organizations")


class TestConfiguredOrderDefaults:
    """The shipped configuration orders the common actions sensibly."""

    def test_dataset_reads_lead_the_datasets_tag(self, spec):
        actions = _actions_in(spec, "Datasets")

        assert actions[:3] == ["package_search", "package_show", "package_list"]

    def test_dataset_writes_follow_the_reads(self, spec):
        actions = _actions_in(spec, "Datasets")

        assert actions.index("package_show") < actions.index("package_create")
        assert actions.index("package_create") < actions.index("package_update")

    def test_core_actions_precede_niche_ones(self, spec):
        actions = _actions_in(spec, "Datasets")

        assert actions.index("package_create") < actions.index("package_revise")
        assert actions.index("package_update") < actions.index(
            "bulk_update_delete"
        )

    def test_resource_reads_lead_their_tag(self, spec):
        actions = _actions_in(spec, "Resources")

        assert actions[0] == "resource_show"
        assert actions.index("resource_create") < actions.index(
            "resource_view_create"
        )


class TestActionSelection:
    """Configuration controls which actions the spec documents."""

    def _build(self, monkeypatch, overrides):
        from ckanext.openapidocs import spec as docs_spec

        return docs_spec.build_spec(overrides)

    def test_exclude_removes_an_action(self, monkeypatch, app):
        spec = self._build(monkeypatch, {"exclude": ["status_show"]})

        assert "/api/3/action/status_show" not in spec["paths"]
        assert "/api/3/action/package_show" in spec["paths"]

    def test_exclude_supports_glob_patterns(self, monkeypatch, app):
        spec = self._build(monkeypatch, {"exclude": ["*_patch", "follow_*"]})

        assert "/api/3/action/package_patch" not in spec["paths"]
        assert "/api/3/action/follow_dataset" not in spec["paths"]
        assert "/api/3/action/package_show" in spec["paths"]

    def test_hidden_flag_on_an_action_removes_it(self, monkeypatch, app):
        spec = self._build(
            monkeypatch, {"actions": {"status_show": {"hidden": True}}}
        )

        assert "/api/3/action/status_show" not in spec["paths"]

    def test_include_only_restricts_to_listed_actions(self, monkeypatch, app):
        spec = self._build(
            monkeypatch, {"include_only": ["package_show", "*_create"]}
        )

        assert "/api/3/action/package_show" in spec["paths"]
        assert "/api/3/action/package_create" in spec["paths"]
        assert "/api/3/action/status_show" not in spec["paths"]

    def test_exclude_wins_over_include_only(self, monkeypatch, app):
        spec = self._build(
            monkeypatch,
            {"include_only": ["package_*"], "exclude": ["package_patch"]},
        )

        assert "/api/3/action/package_show" in spec["paths"]
        assert "/api/3/action/package_patch" not in spec["paths"]

    def test_documenting_an_unregistered_action_is_skipped(
        self, monkeypatch, app
    ):
        """A stale override must not publish a path that would 404."""
        spec = self._build(
            monkeypatch,
            {"actions": {"no_such_action_xyz": {"summary": "Nope"}}},
        )

        assert "/api/3/action/no_such_action_xyz" not in spec["paths"]

    def test_excluded_actions_do_not_leave_empty_tags(self, monkeypatch, app):
        spec = self._build(
            monkeypatch,
            {"exclude": ["config_option_*", "task_status_*",
                         "term_translation_*", "job_*", "status_show",
                         "help_show"]},
        )

        used = {
            op["tags"][0]
            for ops in spec["paths"].values()
            for op in ops.values()
        }
        for tag in spec["tags"]:
            assert tag["name"] in used, f"tag with no operations: {tag['name']}"


class TestConfiguredContent:
    """Values from the merged configuration reach the spec."""

    def test_info_comes_from_configuration(self, monkeypatch, app):
        from ckanext.openapidocs import spec as docs_spec

        spec = docs_spec.build_spec(
            {"info": {"title": "Portal API", "version": "9"}}
        )

        assert spec["info"]["title"] == "Portal API"
        assert spec["info"]["version"] == "9"

    def test_action_properties_and_summary_are_applied(self, monkeypatch, app):
        from ckanext.openapidocs import spec as docs_spec

        spec = docs_spec.build_spec(
            {
                "actions": {
                    "package_create": {
                        "summary": "Make a dataset",
                        "properties": {
                            "custom_field": {
                                "type": "string",
                                "example": "hello",
                            }
                        },
                    }
                }
            }
        )

        operation = spec["paths"]["/api/3/action/package_create"]["post"]
        assert operation["summary"] == "Make a dataset"
        properties = operation["requestBody"]["content"]["application/json"][
            "schema"
        ]["properties"]
        assert properties["custom_field"]["example"] == "hello"

    def test_sysadmin_flag_is_noted_in_the_description(self, monkeypatch, app):
        from ckanext.openapidocs import spec as docs_spec

        spec = docs_spec.build_spec(
            {"actions": {"package_create": {"sysadmin": True}}}
        )

        description = spec["paths"]["/api/3/action/package_create"]["post"][
            "description"
        ]
        assert "sysadmin" in description.lower()

    def test_tag_descriptions_are_applied(self, monkeypatch, app):
        from ckanext.openapidocs import spec as docs_spec

        spec = docs_spec.build_spec(
            {"tags": {"Datasets": "All about datasets."}}
        )

        datasets = [
            tag for tag in spec["tags"] if tag["name"] == "Datasets"
        ][0]
        assert datasets["description"] == "All about datasets."


class TestDocsPageStyling:
    def test_stylesheet_is_served(self, app):
        response = app.get("/vendor/openapidocs/theme.css")

        assert response.status_code == 200
        assert "--openapidocs-primary" in response.body

    def test_page_links_the_stylesheet(self, app):
        assert "/vendor/openapidocs/theme.css" in app.get("/api/docs").body

    def test_theme_is_light_only(self, app):
        """The page commits to a light theme, whatever the visitor's OS says."""
        css = app.get("/vendor/openapidocs/theme.css").body

        assert "prefers-color-scheme" not in css
        assert "color-scheme: light" in css

    def test_each_method_has_its_own_badge_colour(self, app):
        """Reads and writes must be distinguishable at a glance.

        Each method's colour is its own property, so one can be adjusted for a
        brand colour that sits too close to it without touching the rest.
        """
        css = app.get("/vendor/openapidocs/theme.css").body

        assert "--openapidocs-get" in css
        assert "--openapidocs-post" in css
        assert "opblock-get .opblock-summary-method" in css
        assert "opblock-post .opblock-summary-method" in css

    def test_header_colour_can_be_set_separately(self, app):
        """The bar can be rebranded without recolouring every accent."""
        css = app.get("/vendor/openapidocs/theme.css").body

        assert "--openapidocs-header-bg" in css

    def test_authorize_dialog_is_styled(self, app):
        """Swagger UI's stock Authorize dialog needs tidying.

        It heads the form with the raw scheme key ("ApiToken (apiKey)") and
        lists the scheme's `name` / `in`, the latter with an empty value.
        """
        css = app.get("/vendor/openapidocs/theme.css").body

        assert ".swagger-ui .auth-container h4" in css
        assert ".swagger-ui .auth-container form p" in css
        assert ".swagger-ui .auth-btn-wrapper" in css
        assert ".swagger-ui .dialog-ux .modal-ux" in css

    def test_dialog_keeps_the_scheme_description_visible(self, app):
        """Hiding the metadata rows must not hide the instructions."""
        css = app.get("/vendor/openapidocs/theme.css").body

        assert ".swagger-ui .auth-container form .renderedMarkdown p" in css

    def test_no_colour_override_when_none_is_configured(self, app):
        """Unconfigured, the page leaves the stylesheet's default in place."""
        page = app.get("/api/docs").body

        assert "--openapidocs-primary:" not in page

    @pytest.mark.ckan_config("ckanext.openapidocs.primary_color", "#7A3864")
    def test_configured_primary_colour_reaches_the_page(self, app):
        assert "#7A3864" in app.get("/api/docs").body

    @pytest.mark.ckan_config("ckanext.openapidocs.site_title", "NESO Docs")
    def test_configured_header_title_is_shown(self, app):
        page = app.get("/api/docs").body

        assert "NESO Docs" in page

    def test_page_has_a_header_above_the_spec(self, app):
        page = app.get("/api/docs").body

        assert 'class="openapidocs-header"' in page

    @pytest.mark.ckan_config("ckanext.openapidocs.logo_url", "/base/img/x.png")
    def test_configured_logo_is_rendered(self, app):
        page = app.get("/api/docs").body

        assert "/base/img/x.png" in page

    def test_no_logo_markup_without_a_configured_logo(self, app):
        page = app.get("/api/docs").body

        assert "openapidocs-logo" not in page


class TestDocsPage:
    def test_docs_page_renders_swagger_ui(self, app):
        response = app.get("/api/docs")

        assert response.status_code == 200
        assert "swagger-ui" in response.body
        assert "/api/openapi.json" in response.body

    def test_swagger_assets_are_served_locally(self, app):
        response = app.get("/api/docs")

        assert "unpkg.com" not in response.body
        assert "cdn." not in response.body

    def test_vendored_assets_are_served(self, app):
        for asset in ("swagger-ui.css", "swagger-ui-bundle.js"):
            response = app.get(f"/vendor/swagger-ui/{asset}")
            assert response.status_code == 200, f"{asset} not served"

    @pytest.mark.ckan_config("ckanext.openapidocs.docs_path", "/docs/api")
    @pytest.mark.ckan_config(
        "ckanext.openapidocs.spec_path", "/docs/openapi.json"
    )
    def test_paths_are_configurable(self, app):
        assert app.get("/docs/api").status_code == 200
        assert app.get("/docs/openapi.json").status_code == 200
