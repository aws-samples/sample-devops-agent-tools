#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# Lambda Web Adapter entry point.
# Starts the FastMCP server on the port expected by LWA.
export PYTHONPATH="/opt/python:${PYTHONPATH}"
cd /var/task
exec python3 server.py
