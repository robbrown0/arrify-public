"""Length-first policy for new passwords; existing password verification is unchanged."""
from pathlib import Path
COMMON = frozenset(Path(__file__).with_name("common-passwords.txt").read_text().splitlines()) | frozenset(('arrifyarrifyarrify', 'robnetsrobnetsrobnets', 'password', 'password123', 'password123456789', '123456789012345', '1234567890123456', 'qwertyuiopasdfgh', 'letmeinletmeinletmein', 'iloveyouiloveyou', 'thisismypassword', 'correcthorsebatterystaple'))
def validate(password):
    if not isinstance(password,str) or not 15 <= len(password) <= 128:
        raise ValueError('Use 15–128 characters. A few unrelated words work well.')
    normalized=password.casefold().replace(' ','')
    if normalized in COMMON or len(set(normalized))<4:
        raise ValueError('This password is too predictable. Choose a unique password or unrelated words.')
