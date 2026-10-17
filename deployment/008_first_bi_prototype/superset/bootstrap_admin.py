"""Create the local experiment admin without putting its password in argv."""

import os

from superset.app import create_app
from superset.extensions import security_manager

app = create_app()
with app.app_context():
    if security_manager.find_user(username="admin"):
        print("Admin already exists; password left unchanged.")
    else:
        user = security_manager.add_user(
            username="admin",
            first_name="BI",
            last_name="Research",
            email="bi008@example.invalid",
            role=security_manager.find_role("Admin"),
            password=os.environ["SUPERSET_ADMIN_PASSWORD"],
        )
        if not user:
            raise RuntimeError("Could not create experiment admin")
        print("Experiment admin created; password not printed.")
