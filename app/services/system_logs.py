"""Read the on-disk log files for the Logs section (Server logs / Error logs).

The app previously wrote exactly one file, ``instance/logs/errorlog.txt``, at
WARNING and above.  Request lines went to stdout, which a WSGI deployment sends
to the host's own log — not something this app can read back.  That is why
"server logs" had nothing to show.

``app/__init__._configure_logging`` now adds two more rotating files:

    instance/logs/request.log   one line per HTTP request
    instance/logs/server.log    INFO+ application chatter (startup, workers)

Everything here is read-only and bounded: files are read from the end in fixed
chunks, never loaded whole into memory, so a 100 MB log cannot take the site
down.
"""
from __future__ import annotations

import io
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

# Hard limits so a giant log file can never exhaust memory or the UI.
MAX_READ_BYTES = 2 * 1024 * 1024
ERROR_FILE = '500-errors.jsonl'
DEFAULT_TAIL = 500
MAX_TAIL = 5000

KNOWN_FILES = {
    'request': ('request.log', 'HTTP requests'),
    'server': ('server.log', 'Application / server'),
    'error': ('errorlog.txt', 'Warnings and errors'),
}

_LINE_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} [\d:,]+)\s+(?P<level>[A-Z]+)\s+\[(?P<logger>[^\]]+)\]:\s*(?P<msg>.*)$'
)


def log_dir(app):
    folder = Path(app.instance_path) / 'logs'
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return folder


def available_files(app):
    """Every log file that exists on disk right now, newest first."""
    folder = log_dir(app)
    out = []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return out
    for entry in entries:
        if not entry.is_file():
            continue
        if entry.suffix not in ('.log', '.txt', '.jsonl') and '.log.' not in entry.name:
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        out.append({
            'name': entry.name,
            'label': _label_for(entry.name),
            'size': stat.st_size,
            'size_human': human_size(stat.st_size),
            'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            'mtime': stat.st_mtime,
        })
    out.sort(key=lambda item: item['mtime'], reverse=True)
    return out


def _label_for(name):
    for key, (filename, label) in KNOWN_FILES.items():
        if name == filename:
            return label
        if name.startswith(filename + '.'):
            return f'{label} (rotated)'
    if name.endswith('.jsonl'):
        return 'Structured error records'
    return 'Log file'


def human_size(num):
    num = float(num or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if num < 1024 or unit == 'GB':
            return f'{num:,.0f} {unit}' if unit == 'B' else f'{num:,.1f} {unit}'
        num /= 1024
    return f'{num:,.1f} GB'


def tail_lines(app, filename, limit=DEFAULT_TAIL):
    """Last ``limit`` lines of one log file, read from the end in chunks."""
    limit = max(1, min(int(limit or DEFAULT_TAIL), MAX_TAIL))
    folder = log_dir(app)
    path = _safe_path(folder, filename)
    if path is None or not path.is_file():
        return [], 0
    size = path.stat().st_size
    chunk = 64 * 1024
    data = b''
    read = 0
    try:
        with path.open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            position = stream.tell()
            while position > 0 and data.count(b'\n') <= limit and read < MAX_READ_BYTES:
                step = min(chunk, position)
                position -= step
                stream.seek(position)
                data = stream.read(step) + data
                read += step
    except OSError:
        return [], size
    lines = data.decode('utf-8', errors='replace').splitlines()
    return lines[-limit:], size


def parse_line(line, index=0):
    """Split a log line into timestamp / level / logger / message."""
    match = _LINE_RE.match(line or '')
    if match:
        return {
            'index': index,
            'ts': match.group('ts'),
            'level': match.group('level'),
            'logger': match.group('logger'),
            'message': match.group('msg'),
            'raw': line,
        }
    return {'index': index, 'ts': '', 'level': '', 'logger': '', 'message': line or '', 'raw': line}


def read_entries(app, filename, *, limit=DEFAULT_TAIL, level=None, contains=None, logger=None):
    lines, size = tail_lines(app, filename, limit)
    entries = [parse_line(line, i) for i, line in enumerate(lines)]
    if level:
        wanted = level.strip().upper()
        entries = [
            e for e in entries
            if e['level'] == wanted or (wanted == 'ERROR' and e['level'] in ('ERROR', 'CRITICAL'))
        ]
    if logger:
        needle = logger.strip().lower()
        entries = [e for e in entries if needle in (e['logger'] or '').lower()]
    if contains:
        needle = contains.strip().lower()
        entries = [e for e in entries if needle in (e['message'] or '').lower()]
    return entries, size


def level_counts(app, filename, limit=MAX_TAIL):
    entries, _size = read_entries(app, filename, limit=limit)
    return Counter(e['level'] or 'RAW' for e in entries)


def download_bytes(app, filename, max_bytes=8 * 1024 * 1024):
    """Whole file (bounded) for the Download button."""
    folder = log_dir(app)
    path = _safe_path(folder, filename)
    if path is None or not path.is_file():
        return None, None
    size = path.stat().st_size
    with path.open('rb') as stream:
        if size > max_bytes:
            stream.seek(size - max_bytes)
        data = stream.read()
    return data, path.name


def _signature(message):
    text = (message or '').strip()
    text = _STRIP_RE.sub('#', text)
    return text[:180]


def _safe_path(folder, filename):
    """Only allow reading files that live directly in the log folder."""
    name = os.path.basename(str(filename or ''))
    if not name or name in ('.', '..'):
        return None
    path = (folder / name).resolve()
    try:
        if path.parent != folder.resolve():
            return None
    except OSError:
        return None
    return path


def dedupe(entries, max_groups=60):
    """Group repeated messages ("same error ×23") so the Error tab is readable.

    The message is normalised by stripping numbers, hex ids and quoted values,
    which collapses "bill 4412 not found" and "bill 4419 not found" into one
    row with a count and the time range.
    """
    groups = {}
    for entry in entries:
        key = _signature(entry.get('message') or '')
        bucket = groups.get(key)
        if bucket is None:
            groups[key] = {
                'signature': key,
                'count': 1,
                'first': entry.get('ts'),
                'last': entry.get('ts'),
                'level': entry.get('level'),
                'logger': entry.get('logger'),
                'sample': entry.get('message'),
                'raw': entry.get('raw'),
            }
        else:
            bucket['count'] += 1
            bucket['last'] = entry.get('ts')
            if len(entry.get('message') or '') > len(bucket['sample'] or ''):
                bucket['sample'] = entry.get('message')
                bucket['raw'] = entry.get('raw')
    ordered = sorted(groups.values(), key=lambda g: g['count'], reverse=True)
    return ordered[:max_groups]


_STRIP_RE = re.compile(r'[0-9a-fA-F]{6,}|\d+')
