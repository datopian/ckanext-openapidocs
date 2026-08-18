"""Tests for layered configuration: base YAML, project YAML, then CKAN config."""
import pytest

from ckanext.openapidocs import config as docs_config


class TestDeepMerge:
    def test_later_layers_win_on_scalars(self):
        merged = docs_config.deep_merge(
            {"info": {"title": "Base", "version": "3"}},
            {"info": {"title": "Project"}},
        )

        assert merged["info"]["title"] == "Project"
        assert merged["info"]["version"] == "3"

    def test_nested_action_entries_merge_field_by_field(self):
        base = {
            "actions": {
                "package_create": {
                    "summary": "Create a dataset",
                    "properties": {"name": {"type": "string"}},
                }
            }
        }
        project = {
            "actions": {
                "package_create": {
                    "properties": {"dq_score": {"type": "string"}},
                }
            }
        }

        merged = docs_config.deep_merge(base, project)

        action = merged["actions"]["package_create"]
        assert action["summary"] == "Create a dataset"
        assert set(action["properties"]) == {"name", "dq_score"}

    def test_lists_are_replaced_not_concatenated(self):
        """A project must be able to shrink a list, not only grow it."""
        merged = docs_config.deep_merge(
            {"exclude": ["job_*", "task_status_*"]},
            {"exclude": ["job_*"]},
        )

        assert merged["exclude"] == ["job_*"]

    def test_merging_does_not_mutate_the_inputs(self):
        base = {"actions": {"package_create": {"summary": "Base"}}}
        project = {"actions": {"package_create": {"summary": "Project"}}}

        docs_config.deep_merge(base, project)

        assert base["actions"]["package_create"]["summary"] == "Base"
        assert project["actions"]["package_create"]["summary"] == "Project"

    def test_none_layers_are_skipped(self):
        merged = docs_config.deep_merge(None, {"a": 1}, None)

        assert merged == {"a": 1}


class TestLoadYaml:
    def test_reads_a_yaml_file(self, tmp_path):
        path = tmp_path / "spec.yaml"
        path.write_text("info:\n  title: From file\n")

        assert docs_config.load_yaml(str(path))["info"]["title"] == "From file"

    def test_missing_file_returns_empty(self, tmp_path):
        assert docs_config.load_yaml(str(tmp_path / "nope.yaml")) == {}

    def test_empty_file_returns_empty(self, tmp_path):
        path = tmp_path / "empty.yaml"
        path.write_text("")

        assert docs_config.load_yaml(str(path)) == {}

    def test_malformed_yaml_raises_with_the_path(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("info:\n  title: [unclosed\n")

        with pytest.raises(docs_config.ConfigError) as excinfo:
            docs_config.load_yaml(str(path))

        assert "bad.yaml" in str(excinfo.value)


class TestResolveModulePath:
    def test_resolves_module_relative_paths(self):
        """`module:file.yaml` is the same form scheming uses for schemas."""
        resolved = docs_config.resolve_path(
            "ckanext.openapidocs:openapi.yaml"
        )

        assert resolved.endswith("openapi.yaml")
        assert docs_config.load_yaml(resolved)

    def test_absolute_paths_pass_through(self, tmp_path):
        path = tmp_path / "spec.yaml"
        path.write_text("a: 1")

        assert docs_config.resolve_path(str(path)) == str(path)

    def test_unknown_module_raises_config_error(self):
        with pytest.raises(docs_config.ConfigError):
            docs_config.resolve_path("ckanext.no_such_module:file.yaml")


@pytest.mark.usefixtures("with_plugins")
class TestLoadConfigFromCkan:
    def test_base_config_is_loaded_by_default(self):
        loaded = docs_config.load_config()

        assert loaded["info"]["title"]
        assert loaded["exclude"]

    @pytest.mark.ckan_config(
        "ckanext.openapidocs.title", "My Portal API"
    )
    def test_ckan_config_overrides_the_yaml_title(self):
        loaded = docs_config.load_config()

        assert loaded["info"]["title"] == "My Portal API"

    @pytest.mark.ckan_config("ckanext.openapidocs.version", "2.5")
    def test_ckan_config_overrides_the_version(self):
        assert docs_config.load_config()["info"]["version"] == "2.5"

    @pytest.mark.ckan_config(
        "ckanext.openapidocs.exclude", "job_* my_action"
    )
    def test_ckan_config_replaces_the_exclude_list(self):
        loaded = docs_config.load_config()

        assert loaded["exclude"] == ["job_*", "my_action"]

    @pytest.mark.ckan_config(
        "ckanext.openapidocs.include_only", "package_* resource_*"
    )
    def test_ckan_config_sets_include_only(self):
        loaded = docs_config.load_config()

        assert loaded["include_only"] == ["package_*", "resource_*"]

    @pytest.mark.ckan_config("ckanext.openapidocs.spec_files", "")
    def test_empty_spec_files_still_loads_the_base(self):
        assert docs_config.load_config()["info"]["title"]


@pytest.mark.usefixtures("with_plugins")
class TestTheme:
    @pytest.mark.ckan_config("ckanext.openapidocs.primary_color", "#7A3864")
    def test_hex_colour_is_accepted(self):
        assert docs_config.theme()["primary_color"] == "#7A3864"

    @pytest.mark.ckan_config(
        "ckanext.openapidocs.primary_color", "rgb(122, 56, 100)"
    )
    def test_rgb_colour_is_accepted(self):
        assert docs_config.theme()["primary_color"] == "rgb(122, 56, 100)"

    @pytest.mark.ckan_config("ckanext.openapidocs.primary_color", "rebeccapurple")
    def test_named_colour_is_accepted(self):
        assert docs_config.theme()["primary_color"] == "rebeccapurple"

    @pytest.mark.ckan_config(
        "ckanext.openapidocs.primary_color", "red; } </style><script>x()</script>"
    )
    def test_a_value_that_is_not_a_colour_is_dropped(self):
        """The value lands inside a <style> block, so it must be validated."""
        assert docs_config.theme()["primary_color"] == ""

    def test_unset_colour_is_empty(self):
        assert docs_config.theme()["primary_color"] == ""

    @pytest.mark.ckan_config("ckanext.openapidocs.header_color", "#1f2937")
    def test_header_colour_is_separate_from_the_brand_colour(self):
        """The header can be branded without recolouring links and buttons."""
        theme = docs_config.theme()

        assert theme["header_color"] == "#1f2937"
        assert theme["primary_color"] == ""

    @pytest.mark.ckan_config("ckanext.openapidocs.header_color", "javascript:x")
    def test_invalid_header_colour_is_dropped(self):
        assert docs_config.theme()["header_color"] == ""
