"""Emit named pins even for the base image's editable /app distribution."""
from importlib.metadata import distributions

for distribution in sorted(distributions(), key=lambda d:d.metadata['Name'].lower()):
    print(distribution.metadata['Name']+'=='+distribution.version)
