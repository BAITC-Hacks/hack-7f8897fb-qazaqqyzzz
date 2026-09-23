import hashlib
import hmac
import secrets

def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 310000).hex()
    return f'pbkdf2_sha256$310000${salt}${digest}'

def verify_password(password, encoded):
    try:
        _, count, salt, expected = encoded.split('$')
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(count)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False

def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()
