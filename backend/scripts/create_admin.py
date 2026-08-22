"""
Create or promote an admin user. There is deliberately no API route that grants
admin — that would let any authenticated user self-promote. Run from backend/:

    python -m scripts.create_admin --email admin@crossfx.test --password adminpass123

If the email already exists, this promotes that user to admin (password left
unchanged); otherwise it creates a new admin user with the given password.
"""
import argparse

from app.database import SessionLocal
from app.models.user import User
from app.security.hashing import hash_password


def create_or_promote_admin(email: str, password: str, full_name: str) -> User:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            user = User(email=email, hashed_password=hash_password(password), full_name=full_name)
            db.add(user)
        user.is_admin = True
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True, help="Only used if the user doesn't exist yet")
    parser.add_argument("--full-name", default="Admin", dest="full_name")
    args = parser.parse_args()

    user = create_or_promote_admin(args.email, args.password, args.full_name)
    print(f"{user.email} is now an admin (id={user.id}).")


if __name__ == "__main__":
    main()
