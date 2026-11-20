from ._common import *  # noqa

@bp.route('/delivery_persons')
@login_required
def delivery_persons_page():
    if not _user_can('can_manage_delivery_persons'):
        flash('Unauthorized', 'danger')
        return redirect(url_for('index'))
    rows = DeliveryPerson.query.order_by(DeliveryPerson.name.asc()).all()

    # Use the same read-only rollup as the Delivery Person Rent page so the
    # per-person totals here stay aligned with each person's ledger.  Keep every
    # master row visible, including people with no rent activity yet.
    from app.services.delivery_rent_groups import build_delivery_rent_groups

    rollup = build_delivery_rent_groups(group_by='person', sort='name')
    totals_by_person = {
        group['delivery_person_id']: {
            'earned': group['rent'],
            'paid': group['paid'],
            'remaining': group['due'],
            'waived': group['waived'],
        }
        for group in rollup['groups']
        if group.get('delivery_person_id') is not None
    }
    person_totals = {
        person.id: totals_by_person.get(person.id, {
            'earned': 0.0, 'paid': 0.0, 'remaining': 0.0, 'waived': 0.0,
        })
        for person in rows
    }
    return render_template(
        'delivery_persons.html', rows=rows, person_totals=person_totals
    )

