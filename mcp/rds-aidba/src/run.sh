#!/bin/bash
export PYTHONPATH="/var/task:${PYTHONPATH}"
cd /var/task
exec python3 -m mcp_proxy --port=8000 --stateless --pass-environment -- \
  python3 server.py
