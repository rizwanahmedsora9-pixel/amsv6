"""Match client references on records that predate a client foreign key."""
from sqlalchemy import and_, false, func, or_


def client_reference_filter(code_column, name_column, *, code, name,
                            allow_name_fallback=True):
    """A stored code wins over the display name; only code-less rows use names."""
    code, name = (code or '').strip().lower(), (name or '').strip().lower()
    stored_code = func.lower(func.trim(func.coalesce(code_column, '')))
    clauses = []
    if code:
        clauses.append(stored_code == code)
    if name and allow_name_fallback:
        clauses.append(and_(stored_code == '', func.lower(func.trim(name_column)) == name))
    return or_(*clauses) if clauses else false()
