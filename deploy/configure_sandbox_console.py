"""Create a password for the isolated console without printing it or storing plaintext."""
import base64
import getpass
import hashlib
import os
from pathlib import Path
from dotenv import dotenv_values, set_key

path = Path(__file__).with_name('.env')
values = dotenv_values(path)
if (values.get('POSTGRES_HOST') != '127.0.0.1'
        or values.get('POSTGRES_DB') != 'inspire_payments_sandbox'
        or values.get('PAYMENTS_PAYHERE_MODE') != 'sandbox'
        or values.get('PAYMENTS_MESSAGES_ENABLED', '').lower() != 'false'):
    raise SystemExit('Expected the isolated sandbox .env. No changes made.')
password = getpass.getpass('Choose a NEW sandbox console password (14+ characters): ')
if len(password) < 14 or not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
    raise SystemExit('Use at least 14 characters including letters and numbers. No changes made.')
if password != getpass.getpass('Enter it again: '):
    raise SystemExit('Passwords did not match. No changes made.')
salt = os.urandom(16)
encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip('=')
digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
value = 'scrypt$16384$8$1$'+encode(salt)+'$'+encode(digest)
set_key(str(path), 'SANDBOX_CONSOLE_PASSWORD_HASH', value)
path.chmod(0o600)
print('Sandbox console password saved as a salted hash.')
print('Username: sandbox-admin')
print('Restart only inspire-payments-sandbox after installing the console files.')
