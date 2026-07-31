"""Tests for the Jinja2 component macro library (macros/components.html).

Each test renders a macro and asserts required a11y attributes are present.
"""

import os

import pytest
from jinja2 import Environment, FileSystemLoader


@pytest.fixture()
def jinja_env():
    """Create a Jinja2 environment pointing at the templates directory."""
    templates_dir = os.path.join(
        os.path.dirname(__file__), "..", "..", "src", "templates"
    )
    return Environment(
        loader=FileSystemLoader(os.path.abspath(templates_dir)),
        autoescape=True,
        extensions=["jinja2.ext.do"],
    )


@pytest.fixture()
def render(jinja_env):
    """Helper that renders a template string with components imported."""

    def _render(source, **ctx):
        tpl = jinja_env.from_string(
            "{% from 'macros/components.html' import modal, status_chip %}\n" + source
        )
        return tpl.render(**ctx)

    return _render


# -- modal macro --


class TestModal:
    def test_role_dialog(self, render):
        html = render("{% call modal('testModal', 'Test Title') %}body{% endcall %}")
        assert 'role="dialog"' in html

    def test_aria_modal(self, render):
        html = render("{% call modal('testModal', 'Test Title') %}body{% endcall %}")
        assert 'aria-modal="true"' in html

    def test_aria_labelledby(self, render):
        html = render("{% call modal('myModal', 'My Title') %}content{% endcall %}")
        assert 'aria-labelledby="myModalTitle"' in html
        assert 'id="myModalTitle"' in html
        assert "My Title" in html

    def test_close_button(self, render):
        html = render("{% call modal('m1', 'T') %}b{% endcall %}")
        assert 'aria-label="Close"' in html

    def test_body_rendered(self, render):
        html = render("{% call modal('m2', 'T') %}<p>Hello</p>{% endcall %}")
        assert "<p>Hello</p>" in html


# -- status_chip macro --


class TestStatusChip:
    def test_default_variant(self, render):
        html = render("{{ status_chip('Online') }}")
        assert "status-chip" in html
        assert "info" in html
        assert "Online" in html

    def test_custom_variant(self, render):
        html = render("{{ status_chip('Error', 'danger') }}")
        assert "danger" in html

    def test_success_variant(self, render):
        html = render("{{ status_chip('Active', 'success') }}")
        assert "success" in html
