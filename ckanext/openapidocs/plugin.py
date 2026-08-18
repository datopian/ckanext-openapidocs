import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit

from ckanext.openapidocs import views


class OpenAPIDocsPlugin(plugins.SingletonPlugin):
    """Serve a generated OpenAPI 3 spec and a Swagger UI page."""

    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IBlueprint)

    # IConfigurer
    def update_config(self, config_):
        toolkit.add_template_directory(config_, "templates")
        toolkit.add_public_directory(config_, "public")

    # IBlueprint
    def get_blueprint(self):
        return views.get_blueprints()
