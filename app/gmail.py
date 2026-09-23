import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken


class GmailConnector:
    scope = 'https://www.googleapis.com/auth/gmail.readonly'

    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.configured = bool(settings.google_client_id and settings.google_client_secret
                               and settings.google_redirect_uri and settings.oauth_token_key)
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.oauth_token_key.encode()).digest())
        self.cipher = Fernet(key)

    def status(self, employee_id):
        with self.store.connect() as c:
            row = c.execute('SELECT email,updated FROM oauth_connections WHERE employee_id=? AND provider=?',
                            (employee_id, 'google')).fetchone()
        return {'configured': self.configured, 'connected': bool(row),
                'email': row['email'] if row else None, 'updated': row['updated'] if row else None}

    def authorization_url(self, username):
        if not self.configured:
            raise ValueError('Gmail OAuth is not configured on this server.')
        state = secrets.token_urlsafe(32)
        with self.store.connect(write=True) as c:
            c.execute('DELETE FROM oauth_states WHERE expires<?', (time.time(),))
            c.execute('INSERT INTO oauth_states VALUES (?,?,?)', (state, username, time.time() + 600))
        params = {'client_id': self.settings.google_client_id, 'redirect_uri': self.settings.google_redirect_uri,
                  'response_type': 'code', 'scope': self.scope, 'access_type': 'offline',
                  'include_granted_scopes': 'true', 'prompt': 'consent', 'state': state}
        return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(params)

    async def callback(self, code, state):
        with self.store.connect(write=True) as c:
            row = c.execute('SELECT username FROM oauth_states WHERE state=? AND expires>?',
                            (state, time.time())).fetchone()
            c.execute('DELETE FROM oauth_states WHERE state=?', (state,))
            if not row:
                raise ValueError('The Gmail connection request expired. Start again from your profile.')
            user = c.execute('SELECT employee_id FROM users WHERE username=?', (row['username'],)).fetchone()
            if not user or not user['employee_id']:
                raise ValueError('Only employee profiles can connect a Gmail inbox.')
            username, employee_id = row['username'], user['employee_id']
        payload = {'code': code, 'client_id': self.settings.google_client_id,
                   'client_secret': self.settings.google_client_secret,
                   'redirect_uri': self.settings.google_redirect_uri, 'grant_type': 'authorization_code'}
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post('https://oauth2.googleapis.com/token', data=payload)
            response.raise_for_status()
            token = response.json()
            profile = await client.get('https://gmail.googleapis.com/gmail/v1/users/me/profile',
                                       headers={'Authorization': f"Bearer {token['access_token']}"})
            profile.raise_for_status()
        expires = time.time() + int(token.get('expires_in', 3600)) - 60
        encrypted = self.cipher.encrypt(json.dumps(token).encode()).decode()
        with self.store.connect(write=True) as c:
            c.execute('''INSERT INTO oauth_connections(employee_id,provider,email,encrypted_token,expires)
                         VALUES (?,?,?,?,?) ON CONFLICT(employee_id,provider) DO UPDATE SET
                         email=excluded.email,encrypted_token=excluded.encrypted_token,expires=excluded.expires,
                         updated=CURRENT_TIMESTAMP''',
                      (employee_id, 'google', profile.json().get('emailAddress', ''), encrypted, expires))
            c.execute('INSERT INTO audit(actor,action,subject) VALUES (?,?,?)',
                      (username, 'connect_gmail', employee_id))
        return employee_id

    def disconnect(self, employee_id, actor):
        with self.store.connect(write=True) as c:
            c.execute('DELETE FROM oauth_connections WHERE employee_id=? AND provider=?', (employee_id, 'google'))
            c.execute('INSERT INTO audit(actor,action,subject) VALUES (?,?,?)',
                      (actor, 'disconnect_gmail', employee_id))

    async def messages(self, employee_id):
        token = await self._access_token(employee_id)
        headers = {'Authorization': f'Bearer {token}'}
        async with httpx.AsyncClient(timeout=12) as client:
            listing = await client.get('https://gmail.googleapis.com/gmail/v1/users/me/messages',
                                       headers=headers, params={'maxResults': 18, 'q': 'newer_than:30d'})
            listing.raise_for_status()
            result = []
            for row in listing.json().get('messages', []):
                response = await client.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{row['id']}",
                    headers=headers,
                    params=[('format', 'metadata'), ('metadataHeaders', 'Subject'),
                            ('metadataHeaders', 'From'), ('metadataHeaders', 'Date')])
                response.raise_for_status()
                message = response.json()
                message_headers = {h['name'].lower(): h['value'] for h in message.get('payload', {}).get('headers', [])}
                result.append({'id': row['id'], 'subject': message_headers.get('subject', '(No subject)')[:240],
                               'sender': message_headers.get('from', '')[:180],
                               'date': message_headers.get('date', '')[:100],
                               'snippet': message.get('snippet', '')[:500]})
        return result

    async def _access_token(self, employee_id):
        with self.store.connect() as c:
            row = c.execute('SELECT encrypted_token,expires FROM oauth_connections WHERE employee_id=? AND provider=?',
                            (employee_id, 'google')).fetchone()
        if not row:
            raise ValueError('Connect Gmail first.')
        try:
            token = json.loads(self.cipher.decrypt(row['encrypted_token'].encode()).decode())
        except (InvalidToken, ValueError, json.JSONDecodeError) as ex:
            raise ValueError('The saved Gmail connection cannot be opened. Reconnect Gmail.') from ex
        if row['expires'] > time.time() and token.get('access_token'):
            return token['access_token']
        refresh = token.get('refresh_token')
        if not refresh:
            raise ValueError('Gmail access expired. Reconnect Gmail.')
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post('https://oauth2.googleapis.com/token', data={
                'client_id': self.settings.google_client_id, 'client_secret': self.settings.google_client_secret,
                'refresh_token': refresh, 'grant_type': 'refresh_token'})
            response.raise_for_status()
            updated = response.json()
        token = {**token, **updated, 'refresh_token': refresh}
        expires = time.time() + int(updated.get('expires_in', 3600)) - 60
        encrypted = self.cipher.encrypt(json.dumps(token).encode()).decode()
        with self.store.connect(write=True) as c:
            c.execute('UPDATE oauth_connections SET encrypted_token=?,expires=?,updated=CURRENT_TIMESTAMP WHERE employee_id=? AND provider=?',
                      (encrypted, expires, employee_id, 'google'))
        return token['access_token']
