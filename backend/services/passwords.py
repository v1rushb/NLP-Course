from passlib.hash import bcrypt, bcrypt_sha256


def hash_password(password: str) -> str:
    return bcrypt_sha256.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        if password_hash.startswith("$bcrypt-sha256$"):
            return bcrypt_sha256.verify(password, password_hash)
        return bcrypt.verify(password, password_hash)
    except ValueError:
        return False
