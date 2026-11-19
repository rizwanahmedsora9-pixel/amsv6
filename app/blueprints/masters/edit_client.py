from ._common import *  # noqa
from app.services.client_identity import client_reference_filter

@bp.route('/edit_client/<int:id>', methods=['POST'])
@login_required
def edit_client(id):
    if not _user_can('can_manage_clients'):
        flash('Permission denied', 'danger')
        return redirect(url_for('clients'))
    c = db.session.get(Client, id)
    if c:
        before = {'id': c.id, 'code': c.code, 'name': c.name, 'phone': c.phone,
                  'address': c.address, 'opening_balance': c.opening_balance,
                  'is_active': bool(c.is_active)}
        old_code = c.code
        old_name = c.name
        new_code = request.form.get('code', '').strip()
        new_name = request.form.get('name', '').strip()
        category_input = (request.form.get('category') or '').strip()
        category_name = resolve_client_category(category_input)

        if not category_name:
            flash('Please select a valid client category', 'danger')
            return redirect(url_for('clients'))

        if not new_code:
            flash('Client code is required', 'danger')
            return redirect(url_for('clients'))

        if not new_name:
            flash('Client name is required', 'danger')
            return redirect(url_for('clients'))

        others = Client.query.filter(Client.id != id)

        def another_client_has(column, value):
            return others.filter(func.lower(func.trim(column)) == (value or '').strip().lower()).first() is not None

        if another_client_has(Client.code, new_code):
            flash(f'Client code "{new_code}" already exists', 'danger')
            return redirect(url_for('clients'))

        if old_code != new_code or old_name != new_name:
            ambiguous_name = another_client_has(Client.name, old_name)
            if another_client_has(Client.code, old_code) or (
                old_name != new_name and (ambiguous_name or another_client_has(Client.name, new_name))
            ):
                flash('Client identity is ambiguous. No records were changed; ask an administrator to review duplicate names or codes.', 'danger')
                return redirect(url_for('clients'))

            for model, name_col in (
                (PendingBill, PendingBill.client_name), (Entry, Entry.client),
                (DirectSale, DirectSale.client_name), (WaiveOff, WaiveOff.client_name),
                (Invoice, Invoice.client_name), (DirectSaleDraft, DirectSaleDraft.client_name),
            ):
                model.query.filter(client_reference_filter(
                    model.client_code, name_col, code=old_code, name=old_name,
                    allow_name_fallback=not ambiguous_name,
                )).update({model.client_code: new_code, name_col: new_name}, synchronize_session=False)

            if old_name != new_name:
                for model in (Booking, MaterialReturn, Delivery):
                    model.query.filter(func.lower(func.trim(model.client_name)) == old_name.strip().lower()).update(
                        {model.client_name: new_name}, synchronize_session=False,
                    )
                # Keep the payment's historic printed name. Claim only legacy
                # unlinked payments with the old, now-proven unique name.
                Payment.query.filter(
                    Payment.client_id.is_(None), func.lower(func.trim(Payment.client_name)) == old_name.strip().lower(),
                ).update({Payment.client_id: c.id}, synchronize_session=False)

        page_entries_raw = request.form.getlist('page_entry')
        page_entries_clean = [str(x).strip() for x in page_entries_raw if str(x).strip()]
        page_notes_value = ' | '.join(page_entries_clean) if page_entries_clean else (request.form.get('page_notes', '') or '').strip()

        c.name = new_name
        c.code = new_code
        c.name_ur = (request.form.get('name_ur') or '').strip()
        c.phone = request.form.get('phone', '')
        c.address = request.form.get('address', '')
        c.category = category_name
        c.book_no = ''
        c.financial_book_no = ''
        c.financial_page = ''
        c.cement_book_no = ''
        c.cement_page = ''
        c.steel_book_no = ''
        c.steel_page = page_notes_value
        c.location_url = (request.form.get('location_url', '') or '').strip()
        c.page_notes = page_notes_value
        c.opening_balance = _to_float_or_zero(request.form.get('opening_balance', c.opening_balance))
        c.opening_balance_date = _resolve_opening_balance_date(
            request.form.get('opening_balance_date'),
            fallback_dt=(c.opening_balance_date or c.created_at)
        )

        from utils.accounting_audit import record_accounting_audit
        after = {'id': c.id, 'code': c.code, 'name': c.name, 'phone': c.phone,
                 'address': c.address, 'opening_balance': c.opening_balance,
                 'is_active': bool(c.is_active)}
        record_accounting_audit(
            current_user, action='Edit', entity_type='Client', entity_id=c.id,
            before=before, after=after, party_before_id=c.id, party_after_id=c.id,
            reason='Client master updated', module='clients',
        )
        db.session.commit()
        flash('Client updated', 'success')
    return redirect(url_for('clients'))

