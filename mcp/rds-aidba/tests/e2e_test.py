"""End-to-end tests for rds-aidba MCP Server.

These tests require:
- A deployed Lambda Function URL
- AWS credentials with lambda:InvokeFunctionUrl permission

Run with: pytest tests/e2e_test.py -v
Set MCP_ENDPOINT_URL env var to your deployed endpoint.
"""

import json
import os
import urllib.request
import boto3
import pytest
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

MCP_ENDPOINT_URL = os.environ.get("MCP_ENDPOINT_URL", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")


def sign_request(url, body, session_id=None):
    """Sign request with SigV4 for Lambda Function URL."""
    session = boto3.Session(region_name=REGION)
    credentials = session.get_credentials().get_frozen_credentials()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    aws_request = AWSRequest(method="POST", url=url, data=body, headers=headers)
    SigV4Auth(credentials, "lambda", REGION).add_auth(aws_request)
    return dict(aws_request.headers), aws_request.body


def mcp_call(method, params=None, req_id=1, session_id=None):
    """Make an MCP JSON-RPC call to the Function URL."""
    payload = {"jsonrpc": "2.0", "method": method, "id": req_id}
    if params:
        payload["params"] = params
    body = json.dumps(payload)
    url = MCP_ENDPOINT_URL.rstrip("/")
    headers, signed_body = sign_request(url, body, session_id)
    data = signed_body if isinstance(signed_body, bytes) else signed_body.encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        response_body = resp.read().decode()
        for line in response_body.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return json.loads(response_body)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}


@pytest.mark.skipif(not MCP_ENDPOINT_URL, reason="Set MCP_ENDPOINT_URL env var")
class TestE2E:
    """End-to-end tests against deployed MCP server."""

    def test_initialize(self):
        result = mcp_call("initialize", params={
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "e2e-test", "version": "1.0"},
        })
        assert "result" in result
        assert result["result"]["serverInfo"]["name"] == "rds-aidba"

    def test_tools_list(self):
        result = mcp_call("tools/list", req_id=2)
        assert "result" in result
        tools = result["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "execute_health_query" in tool_names
        assert "list_health_queries" in tool_names
        assert "run_full_health_check" in tool_names

    def test_list_health_queries(self):
        result = mcp_call("tools/call", params={
            "name": "list_health_queries",
            "arguments": {"engine": "mysql"},
        }, req_id=3)
        assert "result" in result
        content = result["result"]["content"][0]["text"]
        assert "Category 1" in content
        assert "Category 9" in content

    def test_execute_health_query(self):
        result = mcp_call("tools/call", params={
            "name": "execute_health_query",
            "arguments": {"engine": "mysql", "category": "3", "query_id": "3.1"},
        }, req_id=4)
        assert "result" in result
        content = result["result"]["content"][0]["text"]
        assert "Connection Overview" in content

    def test_execute_pg_query(self):
        result = mcp_call("tools/call", params={
            "name": "execute_health_query",
            "arguments": {"engine": "postgresql", "category": "1", "query_id": "1.1"},
        }, req_id=5)
        assert "result" in result
