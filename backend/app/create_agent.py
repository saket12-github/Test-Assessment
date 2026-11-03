"""Create or reset an agent account.

    python -m app.create_agent agent@example.com "Full Name"   (prompts for the password)
"""

import getpass
import sys

from .db import SessionLocal, init_db
from .models import Agent
from .security import hash_password


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    email = sys.argv[1].strip().lower()
    name = sys.argv[2] if len(sys.argv) > 2 else email.split("@")[0]
    password = getpass.getpass("Password (min 8 chars): ")
    if len(password) < 8:
        sys.exit("Password too short")

    init_db()
    with SessionLocal() as db:
        agent = db.query(Agent).filter(Agent.email == email).first()
        if agent:
            agent.password_hash = hash_password(password)
            print(f"Password reset for {email}")
        else:
            db.add(Agent(email=email, name=name, password_hash=hash_password(password)))
            print(f"Created agent {email}")
        db.commit()


if __name__ == "__main__":
    main()
