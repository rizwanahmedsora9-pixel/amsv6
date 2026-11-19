from ._common import *  # noqa
from sqlalchemy.exc import IntegrityError

@bp.route('/add_client', methods=['POST'])
@login_required
def add_client():
    name = request.form.get('name', '').strip()
    code = request.form.get('code', '').strip()
    if not name:
        flash('Client name is required', 'danger')
        return redirect(url_for('clients'))
    if not code:
        code = generate_client_code()
    if Client.query.filter_by(code=code).first():
        flash(f'Client code "{code}" already exists', 'danger')
        return redirect(url_for('clients'))
    category = (request.form.get('category') or '').strip()
    if not category:
        flash('Please select a client category', 'danger')
        return redirect(url_for('clients'))
    category = resolve_client_category(category)
    if not category:
        flash('Select a valid category from the client ledger categories', 'danger')
        return redirect(url_for('clients'))
    # An opening balance is money.  It used to be coerced with a helper that
    # silently turns unparseable text into 0.0, so typing "abc" or "5,000" cost
    # the client's whole opening balance with a green "Client Registered" flash.
    raw_opening = (request.form.get('opening_balance') or '').strip()
    opening_balance = 0.0
    if raw_opening:
        cleaned = raw_opening.replace(',', '').replace('\u20b9', '').strip()
        try:
            opening_balance = float(cleaned)
        except (TypeError, ValueError):
            flash(
                f'Opening balance "{raw_opening}" is not a number. Enter a figure such as 5000 or '
                '0, or leave the box empty — the client was NOT saved.',
                'danger',
            )
            return redirect(url_for('clients'))
    opening_balance_date = _resolve_opening_balance_date(request.form.get('opening_balance_date'))
    page_entries_raw = request.form.getlist('page_entry')
    page_entries_clean = [str(x).strip() for x in page_entries_raw if str(x).strip()]
    page_notes_value = ' | '.join(page_entries_clean) if page_entries_clean else (request.form.get('page_notes', '') or '').strip()

    new_c = Client(name=name,
                   code=code,
                   name_ur=(request.form.get('name_ur') or '').strip(),
                   phone=request.form.get('phone', ''),
                   address=request.form.get('address', ''),
                   category=category,
                   book_no='',
                   financial_book_no='',
                   financial_page='',
                   cement_book_no='',
                   cement_page='',
                   steel_book_no='',
                   steel_page=page_notes_value,
                   location_url=(request.form.get('location_url', '') or '').strip(),
                   page_notes=page_notes_value,
                   opening_balance=opening_balance,
                   opening_balance_date=opening_balance_date)
    db.session.add(new_c)
    try:
        from app.services.client_qr import ensure_client_ledger_qr
        # Note: ensure_client_ledger_qr flushes (the INSERT runs there,
        # before commit), so the IntegrityError can surface on either side —
        # the try must wrap both.
        ensure_client_ledger_qr(new_c)
        db.session.commit()
    except IntegrityError as exc:
        # uq_client_code (or a legacy inline constraint) rejected a
        # concurrent duplicate commit.  Roll back so the session is clean
        # and tell the user instead of returning a 500.
        db.session.rollback()
        if 'client.code' in str(exc.orig if hasattr(exc, 'orig') else exc):
            flash(f'Client code "{code}" already exists', 'danger')
        else:
            flash('Client could not be saved because it violates a uniqueness rule.', 'danger')
        return redirect(url_for('clients'))
    flash('Client Registered', 'success')
    return redirect(url_for('clients'))

