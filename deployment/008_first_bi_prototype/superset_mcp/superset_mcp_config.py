"""S6 loopback-only native MCP experiment; shares S3 metadata configuration."""
import os
from superset_config import *  # noqa: F403

MCP_AUTH_ENABLED = False
MCP_DEV_USERNAME = 'admin'
MCP_RBAC_ENABLED = True
MCP_TOOL_SEARCH_CONFIG = {'enabled':False}
MCP_CACHE_CONFIG = {'enabled':False}
WEBDRIVER_BASEURL = 'http://superset:8088/'
WEBDRIVER_BASEURL_USER_FRIENDLY = os.environ['BI008_WEB_ORIGIN']
