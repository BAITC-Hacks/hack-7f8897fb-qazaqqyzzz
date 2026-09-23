"""Create/reset a login without putting a password on the command line."""
import argparse
import getpass
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import settings
from app.database import Store
from app.security import password_hash

parser=argparse.ArgumentParser()
parser.add_argument('username')
parser.add_argument('--role',choices=['hr','employee'],required=True)
parser.add_argument('--employee-id')
args=parser.parse_args()
store=Store(settings)
store.initialize()
if args.role=='employee' and args.employee_id not in {e['employee_id'] for e in store.snapshot()['employees']}:
    parser.error('An employee login must reference an existing --employee-id.')
password=getpass.getpass('New password (12+ characters): ')
if len(password)<12 or password!=getpass.getpass('Repeat password: '):
    raise SystemExit('Passwords must match and contain at least 12 characters.')
with store.connect(write=True) as c:
    c.execute('INSERT INTO users VALUES (?,?,?,?) ON CONFLICT(username) DO UPDATE SET password=excluded.password,role=excluded.role,employee_id=excluded.employee_id',
              (args.username,password_hash(password),args.role,args.employee_id if args.role=='employee' else None))
    c.execute('DELETE FROM sessions WHERE username=?',(args.username,))
print('Account saved. Existing sessions for this account were revoked.')
