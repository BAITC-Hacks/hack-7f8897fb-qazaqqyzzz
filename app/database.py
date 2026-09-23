import csv
import json
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .security import password_hash

class Store:
    def __init__(self, settings):
        self.settings = settings
        db_path = Path(settings.database)
        if not db_path.is_absolute():
            db_path = settings.root / db_path
        self.path = str(db_path.resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self, write=False):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        if write:
            conn.execute('BEGIN IMMEDIATE')
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
                CREATE TABLE IF NOT EXISTS documents (name TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history (record_id TEXT PRIMARY KEY, employee_id TEXT NOT NULL,
                    event_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS history_employee ON history(employee_id);
                CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('hr','employee')), employee_id TEXT);
                CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL,
                    csrf TEXT NOT NULL, expires REAL NOT NULL, FOREIGN KEY(username) REFERENCES users(username));
                CREATE TABLE IF NOT EXISTS ai_cache (fingerprint TEXT PRIMARY KEY, payload TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS personal_events (event_id TEXT PRIMARY KEY, employee_id TEXT NOT NULL,
                    payload TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS personal_events_employee ON personal_events(employee_id);
                CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, actor TEXT, action TEXT, subject TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
            ''')
        with self.connect(write=True) as c:
            if not c.execute('SELECT 1 FROM documents LIMIT 1').fetchone():
                base = self.settings.root / 'data'
                for name in ('employees', 'events', 'skills'):
                    self.put(c, name, json.loads((base / f'{name}.json').read_text()))
                with (base / 'activity_history.csv').open(newline='') as f:
                    for row in csv.DictReader(f):
                        self.put_record(c, row)
            if not c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                admin = self.settings.admin_password
                employee = self.settings.employee_password
                if self.settings.production and (len(admin) < 12 or len(employee) < 12):
                    raise RuntimeError('Set ADMIN_PASSWORD and EMPLOYEE_PASSWORD (at least 12 characters) for production setup.')
                admin = admin or secrets.token_urlsafe(18)
                employee = employee or secrets.token_urlsafe(18)
                for username, password, role, eid in [('admin', admin, 'hr', None), ('employee', employee, 'employee', 'E0001')]:
                    c.execute('INSERT INTO users VALUES (?,?,?,?)', (username, password_hash(password), role, eid))
                if not self.settings.production:
                    credentials = Path(self.path).parent / 'credentials.txt'
                    credentials.write_text(f'Local demo sign-in\n\nHR\nUsername: admin\nPassword: {admin}\n\nEmployee (E0001)\nUsername: employee\nPassword: {employee}\n')
                    credentials.chmod(0o600)

    @staticmethod
    def put(c, name, value):
        c.execute('INSERT INTO documents VALUES (?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload', (name, json.dumps(value)))

    @staticmethod
    def put_record(c, row):
        c.execute('INSERT INTO history VALUES (?,?,?,?) ON CONFLICT(record_id) DO UPDATE SET employee_id=excluded.employee_id,event_id=excluded.event_id,payload=excluded.payload',
                  (row['record_id'], row['employee_id'], row['event_id'], json.dumps(row)))

    def snapshot(self, conn=None):
        if conn is None:
            with self.connect() as c:
                return self.snapshot(c)
        docs = {r['name']: json.loads(r['payload']) for r in conn.execute('SELECT * FROM documents')}
        return {'employees': docs['employees']['employees'], 'events': docs['events']['events'],
                'skills': docs['skills']['skills'], 'role_profiles': docs['skills']['role_profiles'],
                'as_of_date': docs['employees'].get('meta', {}).get('as_of_date', '2026-10-01'),
                'history': [json.loads(r['payload']) for r in conn.execute('SELECT payload FROM history')],
                'personal_events': [json.loads(r['payload']) for r in conn.execute('SELECT payload FROM personal_events ORDER BY created DESC')]}
