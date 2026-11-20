"""The delivery-person table footer sums the same totals shown in its rows."""
from types import SimpleNamespace

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader


def render_page(rows, person_totals):
    env = Environment(loader=ChoiceLoader([
        DictLoader({'layout.html': '{% block content %}{% endblock %}'}),
        FileSystemLoader('templates'),
    ]), autoescape=True)
    env.globals['url_for'] = lambda endpoint, **kwargs: '/' + endpoint
    return env.get_template('delivery_persons.html').render(
        rows=rows, person_totals=person_totals,
    )


def test_footer_sums_active_inactive_and_zero_activity_people():
    rows = [SimpleNamespace(id=1, name='Alice', phone=None, is_active=True, name_ur=None),
            SimpleNamespace(id=2, name='Bob', phone=None, is_active=False, name_ur=None),
            SimpleNamespace(id=3, name='Charlie', phone=None, is_active=True, name_ur=None)]
    totals = {1: {'earned': 1000, 'paid': 300, 'remaining': 650},
              2: {'earned': 250, 'paid': 100, 'remaining': 150},
              3: {'earned': 0, 'paid': 0, 'remaining': 0}}
    html = render_page(rows, totals)
    footer = html.split('<tfoot>')[1].split('</tfoot>')[0]
    assert 'Grand total' in footer
    assert 'Rs. 1,250.00' in footer
    assert 'Rs. 400.00' in footer
    assert 'Rs. 800.00' in footer  # remaining accounts for waive-offs


def test_empty_table_has_zero_totals():
    html = render_page([], {})
    assert 'No delivery persons found.' in html
    footer = html.split('<tfoot>')[1].split('</tfoot>')[0]
    assert footer.count('Rs. 0.00') == 3
