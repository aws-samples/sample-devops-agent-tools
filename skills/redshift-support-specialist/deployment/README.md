# Redshift MCP Server — Serverless Deployment (Lambda, no ECR)

Full deployment documentation (architecture, both deploy options, prerequisites, IAM setup, testing, configuration, and teardown) now lives in the main skill README — see [**Step 2 — Deploy the Redshift MCP Server**](../README.md#step-2--deploy-the-redshift-mcp-server).

Quick reference for the files in this directory:

| File | Purpose |
|---|---|
| [`sam-app/`](sam-app/) | Option A — AWS SAM application (recommended deploy path) |
| `build_zip.sh`, `deploy.sh` | Option B — plain AWS CLI + shell script deploy path |
| `deployer-permissions-policy.json` | IAM policy for the credentials used to *deploy* this stack |
| `redshift-access-policy.json` | IAM policy attached to the Lambda's own execution role (Redshift Data API read access) |
| `lambda-trust-policy.json` | Trust policy for the Lambda execution role |
| `scripts/mcp_call.py`, `scripts/list_clusters.py` | SigV4 test helpers for calling the deployed endpoint directly |
| `scripts/mcp_initialize_test.py` | Basic MCP `initialize` handshake test |
