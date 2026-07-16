"""
MCP Client — wraps the awslabs.mysql-mcp-server via subprocess/stdio transport.
Author: Kiran Mayee Mulupuru, Sr. Specialist Database TAM, AWS Enterprise Support

✅ FIXES:
- Serialized send+receive (root cause of readuntil() concurrency error)
- Sequential batch execution (MCP server is single-threaded stdio)
- CloudWatch log breakdown by log type with richer parsing
- Environment probe: detect Aurora/RDS/EC2 once at connect time
- Per-category error thresholds (aborted connections != failed logins)
- rdsadmin filtering in slow query analysis
- long_query_time too-low detection
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import boto3

logger = logging.getLogger(__name__)


@dataclass
class ClusterConfig:
    """
    Connection parameters for a single Aurora/RDS MySQL cluster.
    Either (resource_arn) for RDS Data API, or (hostname) for direct TCP.
    """
    name: str
    secret_arn: str
    database: str
    region: str
    readonly: bool = True
    resource_arn: Optional[str] = None
    hostname: Optional[str] = None
    port: int = 3306
    aws_profile: str = "default"
    cloudwatch_log_group: Optional[str] = None
    cloudwatch_slow_query_log_group: Optional[str] = None
    cloudwatch_audit_log_group: Optional[str] = None

    def to_uvx_args(self) -> List[str]:
        args = [
            "uvx", "awslabs.mysql-mcp-server@latest",
            "--database",   self.database,
            "--region",     self.region,
        ]
        if self.resource_arn:
            # RDS Data API mode (no VPC needed)
            args += [
                "--connection_method", "RDS_API",
                "--db_cluster_arn", self.resource_arn,
                "--db_type", "aurora-mysql",
            ]
        elif self.hostname:
            # Direct TCP mode (requires VPC connectivity)
            args += [
                "--connection_method", "MYSQL_WIRE_PROTOCOL",
                "--db_endpoint", self.hostname,
                "--port", str(self.port),
            ]
        else:
            raise ValueError(
                f"ClusterConfig '{self.name}' requires either resource_arn or hostname."
            )
        # Read-only is the default (omit --allow_write_query)
        return args

    def to_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env["AWS_PROFILE"] = self.aws_profile
        env["AWS_REGION"]  = self.region
        env["FASTMCP_LOG_LEVEL"] = "ERROR"
        return env


@dataclass
class QueryResult:
    check_name: str
    sql: str
    raw: Any = None
    rows: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class EnvironmentInfo:
    """
    Detected cluster environment — probed once at session start,
    injected into every Bedrock prompt so the AI never guesses.
    """
    cluster_type: str          # "aurora" | "rds" | "ec2"
    mysql_version: str
    aurora_version: Optional[str]
    hostname: str
    aurora_version_comment: str
    max_connections: int
    buffer_pool_gb: float
    server_id: int
    read_only: bool
    gtid_mode: str
    log_bin: bool
    raw_row: Dict[str, Any]

    @property
    def is_aurora(self) -> bool:
        return self.cluster_type == "aurora"

    @property
    def is_rds(self) -> bool:
        return self.cluster_type == "rds"

    @property
    def is_ec2(self) -> bool:
        return self.cluster_type == "ec2"

    def describe(self) -> str:
        """One-line human-readable summary injected into prompts."""
        if self.is_aurora:
            return (
                f"Amazon Aurora MySQL {self.aurora_version} "
                f"(MySQL {self.mysql_version}) — AWS-managed cluster. "
                f"Host: {self.hostname}"
            )
        elif self.is_rds:
            return (
                f"Amazon RDS MySQL {self.mysql_version} — AWS-managed instance. "
                f"Host: {self.hostname}"
            )
        else:
            return (
                f"EC2/Self-Managed MySQL {self.mysql_version}. "
                f"Host: {self.hostname}"
            )

    def prompt_context(self) -> str:
        """
        Full environment block injected into every Bedrock prompt.
        Tells the AI exactly what type of cluster this is so it
        never outputs conditional 'if Aurora / if RDS / if EC2' language.
        """
        lines = [
            "=== CONFIRMED CLUSTER ENVIRONMENT ===",
            f"Type            : {self.cluster_type.upper()}",
            f"Description     : {self.describe()}",
            f"MySQL Version   : {self.mysql_version}",
        ]
        if self.aurora_version:
            lines.append(f"Aurora Version  : {self.aurora_version}")
        lines += [
            f"Hostname        : {self.hostname}",
            f"Max Connections : {self.max_connections}",
            f"Buffer Pool     : {self.buffer_pool_gb:.2f} GB",
            f"Read Only       : {self.read_only}",
            f"Binary Logging  : {'ON' if self.log_bin else 'OFF'}",
            f"GTID Mode       : {self.gtid_mode}",
            "",
            "GUIDANCE SCOPE — respond ONLY for this environment type:",
        ]
        if self.is_aurora:
            lines += [
                "  Use: Aurora Parameter Groups, RDS Proxy, Read Replicas,",
                "       Performance Insights, Aurora Global Database,",
                "       Aurora Serverless v2, Aurora Backtracking",
                "  Never mention: GTID config, binary log setup, my.cnf,",
                "                 Percona tools, manual replication, EC2 tuning",
                "  Never say 'if Aurora' or 'if RDS' — this IS Aurora",
            ]
        elif self.is_rds:
            lines += [
                "  Use: RDS Parameter Groups, RDS Proxy, Multi-AZ, Read Replicas,",
                "       Performance Insights, RDS Blue/Green Deployments,",
                "       Automated Backups",
                "  Never mention: Aurora-only features, my.cnf, Percona tools",
                "  Never say 'if Aurora' or 'if EC2' — this IS RDS MySQL",
            ]
        else:
            lines += [
                "  Use: my.cnf tuning, GTID, binary logging, Percona tools,",
                "       ProxySQL, Orchestrator, manual backup strategies",
                "  Never say 'if RDS' or 'if Aurora' — this IS self-managed MySQL",
            ]
        lines.append("=== END ENVIRONMENT ===")
        return "\n".join(lines)


class MySQLMCPClient:
    """
    Manages a long-lived connection to one instance of awslabs.mysql-mcp-server.

    KEY DESIGN: The MCP server communicates over a single stdio pipe.
    It is inherently single-threaded — one request at a time.
    All send+receive pairs are serialized via _rpc_lock.
    Sequential execution used in run_health_checks_batch().
    """

    STREAM_LIMIT = 2 * 1024 * 1024  # 2 MB

    # Lightweight environment probe — runs once at session start
    # Uses a safer query that doesn't fail if @@aurora_version is unavailable
    _ENV_PROBE_SQL = """
        SELECT
            @@version                                        AS mysql_version,
            @@version_comment                                AS version_comment,
            @@hostname                                       AS hostname,
            @@max_connections                                AS max_connections,
            ROUND(@@innodb_buffer_pool_size/1073741824, 2)   AS buffer_pool_gb,
            @@server_id                                      AS server_id,
            @@read_only                                      AS read_only,
            @@gtid_mode                                      AS gtid_mode,
            @@log_bin                                        AS log_bin
    """

    # Separate Aurora detection query — may fail on non-Aurora, which is fine
    _AURORA_PROBE_SQL = "SELECT @@aurora_version AS aurora_version"

    def __init__(self, cluster_config: ClusterConfig) -> None:
        self.config = cluster_config
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._request_id: int = 0
        self._rpc_lock = asyncio.Lock()
        self._available_tools: List[str] = []

        session = boto3.Session(
            profile_name=cluster_config.aws_profile,
            region_name=cluster_config.region,
        )
        self._cloudwatch_logs = session.client("logs")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        args = self.config.to_uvx_args()
        env  = self.config.to_env()
        logger.info("Starting MCP server: %s", " ".join(args))
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=self.STREAM_LIMIT,
        )
        await self._initialize()

    async def disconnect(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.stdin.close()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._proc = None

    async def __aenter__(self) -> "MySQLMCPClient":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.disconnect()

    # ------------------------------------------------------------------
    # MCP JSON-RPC — fully serialized send+receive
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send one JSON-RPC request and read exactly one response.
        The entire write->read cycle is inside _rpc_lock to prevent
        concurrent readuntil() calls on the same stdout pipe.
        """
        if not self._proc:
            raise RuntimeError("Not connected. Call connect() first.")

        async with self._rpc_lock:
            line = json.dumps(payload) + "\n"
            self._proc.stdin.write(line.encode())
            await self._proc.stdin.drain()

            try:
                raw_line = await asyncio.wait_for(
                    self._proc.stdout.readuntil(b"\n"),
                    timeout=60,
                )
            except asyncio.LimitOverrunError as exc:
                logger.warning(
                    "Response exceeded %d-byte buffer, consuming remainder.",
                    exc.consumed,
                )
                buffered  = await self._proc.stdout.read(exc.consumed)
                remainder = await asyncio.wait_for(
                    self._proc.stdout.readline(), timeout=60
                )
                raw_line = buffered + remainder

        return json.loads(raw_line.decode().strip())

    async def _initialize(self) -> None:
        """MCP initialize handshake + tool discovery."""
        req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities":    {},
                "clientInfo":      {"name": "rds-aidba", "version": "0.2.0"},
            },
        }
        resp = await self._send(req)
        logger.debug("initialize response: %s", resp)

        notif = {
            "jsonrpc": "2.0",
            "method":  "notifications/initialized",
            "params":  {},
        }
        self._proc.stdin.write((json.dumps(notif) + "\n").encode())
        await self._proc.stdin.drain()

        tools_resp = await self._send({
            "jsonrpc": "2.0",
            "id":      self._next_id(),
            "method":  "tools/list",
            "params":  {},
        })
        logger.debug("tools/list response: %s", tools_resp)

        if "result" in tools_resp:
            tools = tools_resp["result"].get("tools", [])
            self._available_tools = [t["name"] for t in tools if "name" in t]
            logger.info("MCP tools available: %s", self._available_tools)
            if "run_query" not in self._available_tools:
                raise RuntimeError(
                    f"run_query tool not found. Available: {self._available_tools}"
                )
        elif "error" in tools_resp:
            raise RuntimeError(f"tools/list failed: {tools_resp['error']}")

    # ------------------------------------------------------------------
    # Public query API
    # ------------------------------------------------------------------

    async def execute_query(
        self, sql: str, check_name: str = "ad_hoc"
    ) -> QueryResult:
        req = {
            "jsonrpc": "2.0",
            "id":      self._next_id(),
            "method":  "tools/call",
            "params":  {
                "name": "run_query",
                "arguments": {
                    "sql": sql,
                    "connection_method": "rdsapi" if self.config.resource_arn else "mysqlwire",
                    "cluster_identifier": self.config.resource_arn.split(":")[-1] if self.config.resource_arn else "",
                    "db_endpoint": self.config.hostname or "",
                    "database": self.config.database,
                },
            },
        }
        try:
            resp = await self._send(req)
            if "error" in resp:
                return QueryResult(
                    check_name=check_name, sql=sql, error=str(resp["error"])
                )
            content = resp.get("result", {}).get("content", [])
            rows    = self._parse_content(content)
            return QueryResult(
                check_name=check_name, sql=sql, raw=content, rows=rows
            )
        except Exception as exc:
            logger.exception("Query failed for check '%s'", check_name)
            return QueryResult(check_name=check_name, sql=sql, error=str(exc))

    async def run_health_check(self, check: Dict[str, Any]) -> QueryResult:
        """Execute a single health check. Skips CloudWatch-only checks."""
        if check.get("is_cloudwatch") or not check.get("sql"):
            return QueryResult(
                check_name=check["name"],
                sql="",
                rows=[{"info": "CloudWatch check — handled separately"}],
            )
        return await self.execute_query(
            sql=check["sql"], check_name=check["name"]
        )

    async def run_health_checks_batch(
        self, checks: List[Dict[str, Any]]
    ) -> List[QueryResult]:
        """
        Run checks SEQUENTIALLY — MCP server uses single stdio pipe.
        asyncio.gather() would cause concurrent readuntil() on same stdout.
        """
        results = []
        for check in checks:
            result = await self.run_health_check(check)
            results.append(result)
        return results

    async def probe_environment(self) -> EnvironmentInfo:
        """
        Run a lightweight SQL probe to detect cluster type.
        Called ONCE at agent startup. Result is cached and injected
        into every subsequent prompt — never shown raw to the user.

        Detection priority:
        1. Config resource_arn contains "cluster:" → Aurora
        2. @@aurora_version variable exists → Aurora
        3. version_comment contains "Aurora" → Aurora
        4. hostname contains ".rds.amazonaws.com" → RDS
        5. Fallback → ec2
        """
        result = await self.execute_query(
            sql=self._ENV_PROBE_SQL, check_name="_env_probe"
        )

        if result.error or not result.rows:
            logger.warning("Environment probe failed: %s", result.error)
            # Fall back to config-based detection
            config_type = "unknown"
            if self.config.resource_arn and ":cluster:" in self.config.resource_arn:
                config_type = "aurora"
            elif self.config.hostname and ".rds.amazonaws.com" in self.config.hostname:
                config_type = "rds"
            return EnvironmentInfo(
                cluster_type=config_type,
                mysql_version="unknown",
                aurora_version=None,
                hostname=self.config.hostname or "unknown",
                aurora_version_comment="",
                max_connections=0,
                buffer_pool_gb=0.0,
                server_id=0,
                read_only=False,
                gtid_mode="unknown",
                log_bin=False,
                raw_row={},
            )

        row         = result.rows[0]
        hostname    = str(row.get("hostname", ""))
        ver_comment = str(row.get("version_comment", ""))

        # Try to get Aurora version separately (may fail on non-Aurora)
        aurora_ver = ""
        aurora_result = await self.execute_query(
            sql=self._AURORA_PROBE_SQL, check_name="_aurora_probe"
        )
        if not aurora_result.error and aurora_result.rows:
            aurora_ver = str(aurora_result.rows[0].get("aurora_version", "")).strip()

        # Determine cluster type with multiple signals
        if aurora_ver and aurora_ver not in ("", "None", "NULL"):
            cluster_type = "aurora"
        elif "Aurora" in ver_comment:
            cluster_type = "aurora"
        elif self.config.resource_arn and ":cluster:" in self.config.resource_arn:
            # Config says it's a cluster ARN → Aurora
            cluster_type = "aurora"
        elif ".cluster-" in hostname:
            cluster_type = "aurora"
        elif hostname.endswith(".rds.amazonaws.com"):
            cluster_type = "rds"
        elif self.config.hostname and ".rds.amazonaws.com" in self.config.hostname:
            cluster_type = "rds"
        else:
            cluster_type = "ec2"

        return EnvironmentInfo(
            cluster_type=cluster_type,
            mysql_version=str(row.get("mysql_version", "unknown")),
            aurora_version=aurora_ver if aurora_ver else None,
            hostname=hostname or self.config.hostname or "unknown",
            aurora_version_comment=ver_comment,
            max_connections=int(row.get("max_connections", 0)),
            buffer_pool_gb=float(row.get("buffer_pool_gb", 0.0)),
            server_id=int(row.get("server_id", 0)),
            read_only=bool(int(row.get("read_only", 0))),
            gtid_mode=str(row.get("gtid_mode", "unknown")),
            log_bin=bool(int(row.get("log_bin", 0))),
            raw_row=row,
        )

    # ------------------------------------------------------------------
    # CloudWatch Logs
    # ------------------------------------------------------------------

    async def fetch_cloudwatch_logs(
        self,
        log_group: str,
        log_stream_type: str,
        hours_back: int = 1,
        limit: int = 10000,
    ) -> List[Dict[str, Any]]:
        """Fetch raw log events from a CloudWatch log group."""
        try:
            start_ms = int(
                (datetime.utcnow() - timedelta(hours=hours_back)).timestamp() * 1000
            )
            end_ms = int(datetime.utcnow().timestamp() * 1000)
            logger.info(
                "Fetching %s logs from %s (last %dh)",
                log_stream_type, log_group, hours_back,
            )
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._cloudwatch_logs.filter_log_events(
                    logGroupName=log_group,
                    startTime=start_ms,
                    endTime=end_ms,
                    limit=limit,
                ),
            )
            events = response.get("events", [])
            logger.info("Retrieved %d events from %s", len(events), log_group)
            return events
        except Exception as exc:
            logger.error("CloudWatch fetch error (%s): %s", log_group, exc)
            return []

    def analyze_error_logs(
        self, log_events: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Categorize MySQL error log events.
        Per-category severity thresholds — aborted connections != failed logins.
        """
        buckets: Dict[str, List[Dict]] = {
            "connection_errors":  [],
            "access_denied":      [],
            "table_errors":       [],
            "replication_errors": [],
            "innodb_errors":      [],
            "deadlocks":          [],
            "warnings":           [],
            "other":              [],
        }
        patterns = {
            "connection_errors":  (
                r"(Too many connections|Aborted connection"
                r"|Can't connect|Connection refused|max_connections)"
            ),
            "access_denied":      r"(Access denied|authentication failed|password.*failed)",
            "table_errors":       r"(Table.*doesn't exist|Can't find file|Incorrect table definition|Corrupt)",
            "replication_errors": r"(Slave|Replica|Relay log|Replication|Binlog|replica.*error)",
            "innodb_errors":      r"(InnoDB|Tablespace|redo log|undo log|innodb_)",
            "deadlocks":          r"(Deadlock|Lock wait timeout|waiting for.*lock)",
            "warnings":           r"$$Warning$$",
        }

        for event in log_events:
            msg = event.get("message", "")
            ts  = event.get("timestamp", 0)
            entry = {
                "timestamp": datetime.fromtimestamp(ts / 1000).isoformat(),
                "message":   msg[:600],
            }
            matched = False
            for bucket, pattern in patterns.items():
                if re.search(pattern, msg, re.IGNORECASE):
                    buckets[bucket].append(entry)
                    matched = True
                    break
            if not matched:
                buckets["other"].append(entry)

        total  = sum(len(v) for v in buckets.values())
        counts = {k: len(v) for k, v in buckets.items()}

        # Per-category thresholds
        # Aborted connections : warn >5,  critical >20
        # Access denied       : warn >5,  critical >20
        # InnoDB errors       : any = critical
        # Deadlocks           : warn >2,  critical >10
        # Replication errors  : any = critical
        severity = "OK"
        if (
            counts["innodb_errors"] > 0
            or counts["replication_errors"] > 0
            or counts["connection_errors"] > 20
            or counts["access_denied"] > 20
            or counts["deadlocks"] > 10
        ):
            severity = "CRITICAL"
        elif (
            counts["connection_errors"] > 5
            or counts["access_denied"] > 5
            or counts["deadlocks"] > 2
            or counts["table_errors"] > 0
        ):
            severity = "WARNING"
        elif total > 0:
            severity = "INFO"

        return {
            "log_type":       "error",
            "total_events":   total,
            "severity":       severity,
            "counts_by_type": counts,
            "thresholds": {
                "connection_errors_warn":       5,
                "connection_errors_critical":  20,
                "access_denied_warn":           5,
                "access_denied_critical":      20,
                "innodb_errors_critical":       1,
                "deadlocks_warn":               2,
                "deadlocks_critical":          10,
                "replication_errors_critical":  1,
            },
            "samples":         {k: v[:5] for k, v in buckets.items() if v},
            "timeline":        self._build_timeline(log_events),
            "recommendations": self._error_recommendations(counts),
        }

    def analyze_slow_query_logs(
        self, log_events: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Parse slow query log events.
        Filters rdsadmin queries before counting.
        Reports customer vs rdsadmin split clearly.
        Flags long_query_time set too low (capturing sub-ms queries).
        """
        all_queries: List[Dict[str, Any]] = []
        current: Dict[str, Any] = {}

        for event in log_events:
            msg = event.get("message", "")
            ts  = event.get("timestamp", 0)

            qt   = re.search(r"Query_time:\s*([\d.]+)", msg)
            lt   = re.search(r"Lock_time:\s*([\d.]+)",  msg)
            rs   = re.search(r"Rows_sent:\s*(\d+)",     msg)
            re_  = re.search(r"Rows_examined:\s*(\d+)", msg)
            user = re.search(r"# User@Host:\s*(\S+)",   msg)

            if qt:
                user_str = user.group(1) if user else "unknown"
                current = {
                    "timestamp":      datetime.fromtimestamp(ts / 1000).isoformat(),
                    "query_time_sec": float(qt.group(1)),
                    "lock_time_sec":  float(lt.group(1)) if lt else 0.0,
                    "rows_sent":      int(rs.group(1))   if rs  else 0,
                    "rows_examined":  int(re_.group(1))  if re_ else 0,
                    "user_host":      user_str,
                    "is_rdsadmin":    "rdsadmin" in user_str.lower(),
                    "query_text":     "",
                }
            elif current and re.match(
                r"\s*(SELECT|INSERT|UPDATE|DELETE|REPLACE|CALL|WITH)",
                msg, re.IGNORECASE
            ):
                current["query_text"] = msg.strip()[:1000]
                eff = current["rows_examined"] / max(current["rows_sent"], 1)
                current["efficiency_ratio"] = round(eff, 1)
                current["performance_band"] = (
                    "CRITICAL (>10s)" if current["query_time_sec"] > 10
                    else "SLOW (1-10s)"    if current["query_time_sec"] > 1
                    else "MEDIUM (0.1-1s)" if current["query_time_sec"] > 0.1
                    else "FAST (<0.1s)"
                )
                all_queries.append(current)
                current = {}

        # Split customer vs rdsadmin
        rdsadmin_queries = [q for q in all_queries if q.get("is_rdsadmin")]
        customer_queries = [q for q in all_queries if not q.get("is_rdsadmin")]
        rdsadmin_count   = len(rdsadmin_queries)

        # All statistics on customer queries only
        queries = customer_queries
        total   = len(queries)

        if queries:
            avg_t   = sum(q["query_time_sec"] for q in queries) / total
            max_t   = max(q["query_time_sec"] for q in queries)
            p95_t   = sorted(
                q["query_time_sec"] for q in queries
            )[int(total * 0.95)] if total > 1 else avg_t
            over10  = sum(1 for q in queries if q["query_time_sec"] > 10)
            over1   = sum(1 for q in queries if q["query_time_sec"] > 1)
            bad_eff = sum(1 for q in queries if q.get("efficiency_ratio", 0) > 100)
            high_lk = sum(1 for q in queries if q["lock_time_sec"] > 1)
        else:
            avg_t = max_t = p95_t = 0.0
            over10 = over1 = bad_eff = high_lk = 0

        severity = "OK"
        if over10 > 0 or bad_eff > 10:
            severity = "CRITICAL"
        elif over1 > 5 or total > 50:
            severity = "WARNING"
        elif total > 0:
            severity = "INFO"

        band_breakdown: Dict[str, int] = {}
        for q in queries:
            b = q.get("performance_band", "FAST (<0.1s)")
            band_breakdown[b] = band_breakdown.get(b, 0) + 1

        # Detect long_query_time set too aggressively (near 0)
        rdsadmin_max_time = (
            max((q["query_time_sec"] for q in rdsadmin_queries), default=0.0)
            if rdsadmin_queries else 0.0
        )
        threshold_too_low = (
            rdsadmin_count > 100
            and rdsadmin_max_time < 0.001
            and total == 0
        )

        recs = self._slow_query_recommendations(over10, over1, bad_eff, high_lk, total)
        if threshold_too_low:
            recs.insert(
                0,
                "INFO: long_query_time appears to be set at 0 or near-0 — "
                "capturing all rdsadmin internal queries as 'slow'. "
                "Set long_query_time = 1 in Aurora Cluster Parameter Group "
                "to capture only genuine slow queries."
            )

        return {
            "log_type":                  "slowquery",
            "total_events_parsed":       len(all_queries),
            "customer_slow_queries":     total,
            "rdsadmin_queries_filtered": rdsadmin_count,
            "severity":                  severity,
            "threshold_too_low_detected": threshold_too_low,
            "statistics": {
                "avg_query_time_sec":   round(avg_t, 3),
                "max_query_time_sec":   round(max_t, 3),
                "p95_query_time_sec":   round(p95_t, 3),
                "queries_over_10s":     over10,
                "queries_over_1s":      over1,
                "bad_efficiency_count": bad_eff,
                "high_lock_time_count": high_lk,
            },
            "performance_band_breakdown":   band_breakdown,
            "top_customer_queries_by_time": sorted(
                queries, key=lambda x: x["query_time_sec"], reverse=True
            )[:10],
            "rdsadmin_sample":   rdsadmin_queries[:3],
            "recommendations":   recs,
        }

    def analyze_audit_logs(
        self, log_events: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Parse audit log events into security-focused categories."""
        buckets: Dict[str, List[Dict]] = {
            "failed_logins":      [],
            "successful_logins":  [],
            "privilege_changes":  [],
            "schema_changes":     [],
            "data_manipulation":  [],
            "suspicious_queries": [],
            "connection_events":  [],
        }
        patterns = {
            "failed_logins":      r"(Access denied|authentication failed|LOGIN_FAILED)",
            "successful_logins":  r"(LOGIN_SUCCESS|Connect.*success|authenticated)",
            "privilege_changes":  r"\b(GRANT|REVOKE)\b",
            "schema_changes":     (
                r"\b(CREATE|ALTER|DROP|TRUNCATE)\s+"
                r"(TABLE|DATABASE|INDEX|VIEW|PROCEDURE|FUNCTION)"
            ),
            "data_manipulation":  r"\b(INSERT|UPDATE|DELETE)\b",
            "suspicious_queries": (
                r"\b(DROP\s+DATABASE|TRUNCATE|DELETE\s+FROM\s+\w+\s*;"
                r"|INTO\s+OUTFILE|LOAD\s+DATA)\b"
            ),
            "connection_events":  r"(CONNECT|DISCONNECT|QUIT)",
        }

        for event in log_events:
            msg = event.get("message", "")
            ts  = event.get("timestamp", 0)
            entry = {
                "timestamp": datetime.fromtimestamp(ts / 1000).isoformat(),
                "message":   msg[:400],
            }
            for bucket, pattern in patterns.items():
                if re.search(pattern, msg, re.IGNORECASE):
                    buckets[bucket].append(entry)
                    break

        counts = {k: len(v) for k, v in buckets.items()}
        total  = sum(counts.values())

        severity = "OK"
        if counts["failed_logins"] > 10 or counts["suspicious_queries"] > 0:
            severity = "WARNING"
        if counts["failed_logins"] > 50 or counts["suspicious_queries"] > 5:
            severity = "CRITICAL"

        return {
            "log_type":     "audit",
            "total_events": total,
            "severity":     severity,
            "counts_by_type": counts,
            "samples":        {k: v[:5] for k, v in buckets.items() if v},
            "security_summary": {
                "brute_force_risk":   counts["failed_logins"] > 10,
                "schema_change_risk": counts["schema_changes"] > 0,
                "data_exfil_risk":    counts["suspicious_queries"] > 0,
            },
            "recommendations": self._audit_recommendations(counts),
        }

    # ------------------------------------------------------------------
    # CloudWatch helpers
    # ------------------------------------------------------------------

    def _build_timeline(
        self, events: List[Dict[str, Any]], bucket_minutes: int = 10
    ) -> List[Dict[str, Any]]:
        if not events:
            return []
        buckets: Dict[str, int] = {}
        for e in events:
            ts  = e.get("timestamp", 0)
            dt  = datetime.fromtimestamp(ts / 1000)
            key = dt.strftime(
                f"%Y-%m-%dT%H:{(dt.minute // bucket_minutes) * bucket_minutes:02d}"
            )
            buckets[key] = buckets.get(key, 0) + 1
        return [
            {"time_bucket": k, "event_count": v}
            for k, v in sorted(buckets.items())
        ]

    def _error_recommendations(self, counts: Dict[str, int]) -> List[str]:
        recs = []
        if counts.get("connection_errors", 0) > 0:
            recs.append(
                "Implement RDS Proxy for connection pooling to prevent aborted connections"
            )
        if counts.get("access_denied", 0) > 0:
            recs.append(
                "WARNING: Review user privileges — access denied errors detected"
            )
        if counts.get("deadlocks", 0) > 0:
            recs.append(
                "WARNING: Review transaction ordering and add indexes to reduce deadlocks"
            )
        if counts.get("innodb_errors", 0) > 0:
            recs.append(
                "CRITICAL: InnoDB errors detected — check tablespace health and disk space"
            )
        if counts.get("replication_errors", 0) > 0:
            recs.append(
                "CRITICAL: Replication errors — check Aurora replica lag via CloudWatch"
            )
        return recs

    def _slow_query_recommendations(
        self, over10: int, over1: int, bad_eff: int, high_lock: int, total: int
    ) -> List[str]:
        recs = []
        if over10 > 0:
            recs.append(
                f"CRITICAL: {over10} customer queries exceeded 10s — "
                "immediate index review required"
            )
        if over1 > 0:
            recs.append(
                f"WARNING: {over1} customer queries between 1-10s — "
                "add indexes on WHERE/JOIN columns"
            )
        if bad_eff > 0:
            recs.append(
                f"WARNING: {bad_eff} queries with efficiency ratio >100 — "
                "full table scans detected"
            )
        if high_lock > 0:
            recs.append(
                f"WARNING: {high_lock} queries with lock_time >1s — "
                "review transaction isolation level"
            )
        if total > 100:
            recs.append(
                "INFO: High customer slow query volume — "
                "consider raising long_query_time threshold"
            )
        return recs

    def _audit_recommendations(self, counts: Dict[str, int]) -> List[str]:
        recs = []
        if counts.get("failed_logins", 0) > 10:
            recs.append(
                "WARNING: High failed login count — possible brute force, "
                "review Security Groups"
            )
        if counts.get("suspicious_queries", 0) > 0:
            recs.append(
                "CRITICAL: Suspicious destructive queries detected — review immediately"
            )
        if counts.get("schema_changes", 0) > 0:
            recs.append(
                "INFO: Schema changes logged — verify authorized change management"
            )
        if counts.get("privilege_changes", 0) > 0:
            recs.append(
                "WARNING: Privilege changes detected — verify authorized access grants"
            )
        return recs

    # Backward-compatibility aliases
    def parse_mysql_error_logs(
        self, e: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return self.analyze_error_logs(e)

    def parse_slow_query_logs(
        self, e: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return self.analyze_slow_query_logs(e)

    def parse_audit_logs(
        self, e: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return self.analyze_audit_logs(e)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_content(content: List[Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        rows.extend(parsed)
                    elif isinstance(parsed, dict):
                        rows.append(parsed)
                except json.JSONDecodeError:
                    rows.append({"raw_text": text})
        return rows