"""
DBA Guidance & Recommendations Knowledge Base
Author: Kiran Mayee Mulupuru, Sr. Specialist Database TAM, AWS Premium Support

✅ UPDATED:
- All guidance functions now accept cluster_type ("aurora" | "rds" | "ec2")
- build_guidance_context() returns ONLY the guidance relevant to the detected
  environment — no conditional "if Aurora / if RDS / if EC2" text blocks
- Bedrock receives a single, flat, environment-specific guidance block
- Knowledge authority header added — Claude treats this as primary source
"""

from typing import Dict, Any, List


GLOBAL_DISCLAIMER = """
IMPLEMENTATION GUIDELINES:
- Test all changes in non-production first
- Use AWS-managed services for Aurora/RDS (Parameter Groups, not my.cnf)
- This tool is READ-ONLY — no configuration changes are made automatically
"""

# Authority header prepended to every guidance block sent to Bedrock
# Tells Claude explicitly that this block is the primary knowledge source
_AUTHORITY_HEADER = """
╔══════════════════════════════════════════════════════════════╗
║          PRIMARY KNOWLEDGE SOURCE — USE THIS FIRST          ║
║                                                              ║
║  The guidance below is the authoritative reference for       ║
║  all recommendations in your response. It is sourced from:  ║
║                                                              ║
║  • AWS Official Documentation (docs.aws.amazon.com)         ║
║  • AWS Prescriptive Guidance                                 ║
║  • Amazon Aurora MySQL Best Practices                        ║
║  • Amazon RDS MySQL Best Practices                           ║
║  • AWS Premium Support validated runbooks                    ║
║                                                              ║
║  KNOWLEDGE PRIORITY ORDER:                                   ║
║  1. Live query results (facts about THIS cluster)            ║
║  2. Guidance block below (AWS-prescribed best practices)     ║
║  3. Your training knowledge (only to fill gaps, and          ║
║     only when consistent with guidance above)                ║
║                                                              ║
║  If your training knowledge conflicts with this guidance:    ║
║  FOLLOW THIS GUIDANCE — it is environment-specific and       ║
║  more current than general training data.                    ║
╚══════════════════════════════════════════════════════════════╝
""".strip()

# Grounding footer appended to every guidance block
# Reminds Claude to stay within scope at the point of answering
_GROUNDING_FOOTER = """
╔══════════════════════════════════════════════════════════════╗
║                    RESPONSE GROUNDING                        ║
║                                                              ║
║  When formulating your response:                             ║
║                                                              ║
║  ✅ Base all recommendations on the guidance above           ║
║  ✅ Cite specific AWS services named in the guidance         ║
║  ✅ Use thresholds defined above for severity assessment      ║
║  ✅ If guidance covers the topic: follow it exactly          ║
║                                                              ║
║  ⚠️  If you must go beyond the guidance block:              ║
║     - Only use AWS official documentation as the source      ║
║     - Do not recommend community tools for Aurora/RDS        ║
║     - Do not recommend my.cnf edits for Aurora/RDS           ║
║     - Do not recommend GTID/binary log setup for Aurora      ║
║                                                              ║
║  ❌ Do NOT use:                                              ║
║     - Generic MySQL blog posts or Stack Overflow answers     ║
║     - Percona/MariaDB recommendations for Aurora/RDS         ║
║     - Outdated practices superseded by AWS managed services  ║
╚══════════════════════════════════════════════════════════════╝
""".strip()


# ---------------------------------------------------------------------------
# Per-category guidance — structured by environment type
# ---------------------------------------------------------------------------

_GUIDANCE: Dict[str, Dict[str, Any]] = {

    # -----------------------------------------------------------------------
    "connections": {
        "title": "Connection Management",
        "aurora": {
            "recommendations": [
                "Use RDS Proxy for connection pooling — reduces overhead by up to 80%",
                "Monitor DatabaseConnections CloudWatch metric; alarm at 75% of max_connections",
                "Adjust wait_timeout via Aurora Cluster Parameter Group (not my.cnf)",
                "Use reader endpoint for read-only application connections",
                "Aurora automatically handles connection draining during failover via RDS Proxy",
            ],
            "thresholds": {
                "connection_usage_warn_pct":     75,
                "connection_usage_critical_pct": 90,
                "threads_running_warn":          20,
                "threads_running_critical":      50,
            },
            "sources": [
                "https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/rds-proxy.html",
                "https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraMySQL.BestPractices.html",
            ],
        },
        "rds": {
            "recommendations": [
                "Use RDS Proxy for connection pooling",
                "Adjust max_connections via DB Parameter Group",
                "Monitor DatabaseConnections CloudWatch metric; alarm at 75%",
                "Set wait_timeout to 300-900s for web applications via Parameter Group",
                "RDS Proxy handles connection draining automatically during Multi-AZ failover",
            ],
            "thresholds": {
                "connection_usage_warn_pct":     75,
                "connection_usage_critical_pct": 90,
                "threads_running_warn":          20,
                "threads_running_critical":      50,
            },
            "sources": [
                "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-proxy.html",
            ],
        },
        "ec2": {
            "recommendations": [
                "Use ProxySQL or MySQL Router for connection pooling",
                "Set thread_cache_size to reduce thread creation overhead",
                "Monitor Threads_connected and Threads_running via SHOW STATUS",
                "Set wait_timeout = 300 for web application connections in my.cnf",
                "Consider migrating to Aurora/RDS for managed connection pooling via RDS Proxy",
            ],
            "thresholds": {
                "connection_usage_warn_pct":     75,
                "connection_usage_critical_pct": 90,
                "threads_running_warn":          20,
                "threads_running_critical":      50,
            },
        },
    },

    # -----------------------------------------------------------------------
    "configuration": {
        "title": "System Configuration",
        "aurora": {
            "recommendations": [
                "All parameter changes must go through Aurora Cluster or Instance Parameter Groups",
                "Aurora auto-tunes innodb_buffer_pool_size — do not set it manually",
                "Enable Performance Insights (7-day free retention) for query analysis",
                "Enable Enhanced Monitoring for OS-level metrics at 1-second granularity",
                "Aurora uses redo logs internally — binary logging is optional and off by default",
                "Use Aurora Serverless v2 for variable workloads (scales 0.5–128 ACUs)",
            ],
            "sources": [
                "https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraMySQL.Managing.Tuning.concepts.html",
                "https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraMySQL.BestPractices.Performance.html",
            ],
        },
        "rds": {
            "recommendations": [
                "All parameter changes must go through RDS DB Parameter Groups",
                "Set innodb_buffer_pool_size to 70-75% of instance RAM via Parameter Group",
                "Enable Performance Insights for query-level analysis",
                "Enable Enhanced Monitoring for OS-level metrics",
                "Static parameters require instance reboot; dynamic parameters apply immediately",
            ],
            "sources": [
                "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_WorkingWithParamGroups.html",
            ],
        },
        "ec2": {
            "recommendations": [
                "Edit /etc/my.cnf or /etc/mysql/mysql.conf.d/mysqld.cnf for configuration",
                "Set innodb_buffer_pool_size to 70-80% of available RAM",
                "Set innodb_flush_log_at_trx_commit=1 for full ACID compliance",
                "Enable slow_query_log with long_query_time=1",
                "Use Percona Monitoring and Management (PMM) for dashboards",
            ],
        },
    },

    # -----------------------------------------------------------------------
    "replication": {
        "title": "Replication & High Availability",
        "aurora": {
            "recommendations": [
                "Aurora uses shared storage replication — 6 copies across 3 AZs, automatic",
                "Create Read Replicas via AWS Console or CLI — no manual replication setup",
                "Monitor AuroraReplicaLag CloudWatch metric (target: <100ms)",
                "Use Aurora Global Database for cross-region replication with <1s lag",
                "Configure replica promotion tiers (tier 0 = highest failover priority)",
                "Aurora Failover completes in <30 seconds with RDS Proxy in place",
                "Use cluster reader endpoint for automatic load balancing across replicas",
            ],
            "thresholds": {
                "replica_lag_warn_ms":     1000,
                "replica_lag_critical_ms": 5000,
            },
            "sources": [
                "https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.Replication.html",
                "https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html",
            ],
        },
        "rds": {
            "recommendations": [
                "Enable Multi-AZ for synchronous standby replication and automatic failover (1-2 min RTO)",
                "Create Read Replicas for read scaling (up to 15 per source instance)",
                "Monitor ReplicaLag CloudWatch metric (target: <30 seconds)",
                "Enable automated backups with 7-35 day retention for point-in-time recovery",
                "Use RDS Blue/Green Deployments for zero-downtime major version upgrades",
            ],
            "thresholds": {
                "replica_lag_warn_sec":     30,
                "replica_lag_critical_sec": 60,
            },
            "sources": [
                "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_ReadRepl.html",
                "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.html",
            ],
        },
        "ec2": {
            "recommendations": [
                "Enable GTID: set gtid_mode=ON, enforce_gtid_consistency=ON in my.cnf",
                "Use binlog_format=ROW for replication consistency",
                "Enable semi-sync: rpl_semi_sync_source_enabled=1",
                "Set replica_parallel_workers > 1 for parallel replication",
                "Use MHA or Orchestrator for automatic failover management",
                "Use Percona XtraBackup for non-blocking consistent backups",
            ],
            "thresholds": {
                "replica_lag_warn_sec":     30,
                "replica_lag_critical_sec": 60,
            },
        },
    },

    # -----------------------------------------------------------------------
    "storage": {
        "title": "Storage & Capacity",
        "aurora": {
            "recommendations": [
                "Aurora storage auto-grows in 10 GB increments up to 128 TiB — no pre-provisioning needed",
                "Storage billed per GB-month consumed (not provisioned capacity)",
                "Monitor FreeLocalStorage CloudWatch metric for local temp space",
                "Use ALTER TABLE ... FORCE to reclaim space (OPTIMIZE TABLE is a no-op on Aurora InnoDB)",
                "Use Aurora Fast Cloning for instant test environment copies (copy-on-write, low cost)",
                "Enable Aurora Backtracking (up to 72 hours) for point-in-time rewind without restore",
            ],
            "thresholds": {
                "fragmentation_warn_pct":     15,
                "fragmentation_critical_pct": 30,
            },
            "sources": [
                "https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.Managing.Performance.html",
            ],
        },
        "rds": {
            "recommendations": [
                "Enable RDS Storage Auto Scaling — expands automatically at configured threshold",
                "Use gp3 storage: baseline 3000 IOPS, independent throughput (cost-effective default)",
                "Use io1/io2 Block Express for latency-sensitive workloads requiring guaranteed IOPS",
                "Monitor FreeStorageSpace CloudWatch metric; alarm at 20% remaining",
                "Enable encryption at rest using AWS KMS — must be set at creation time",
            ],
            "thresholds": {
                "storage_free_warn_pct":      20,
                "fragmentation_warn_pct":     15,
                "fragmentation_critical_pct": 30,
            },
            "sources": [
                "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PIOPS.StorageTypes.html",
            ],
        },
        "ec2": {
            "recommendations": [
                "Use LVM or EBS volume groups for online storage expansion",
                "Monitor df -h and set OS-level alerting at 80% disk usage",
                "Run OPTIMIZE TABLE during low-traffic windows to reclaim fragmented space",
                "Use Percona XtraBackup for non-blocking consistent hot backups",
            ],
            "thresholds": {
                "fragmentation_warn_pct":     15,
                "fragmentation_critical_pct": 30,
            },
        },
    },

    # -----------------------------------------------------------------------
    "innodb": {
        "title": "InnoDB Buffer Pool & Performance",
        "aurora": {
            "recommendations": [
                "Aurora auto-tunes innodb_buffer_pool_size — do not override manually",
                "Use Performance Insights to identify queries with high wait event counts",
                "Aurora Parallel Query automatically offloads full table scans to the storage layer",
                "Use Read Replicas to offload read-heavy analytical queries from the writer",
                "Monitor BufferCacheHitRatio CloudWatch metric (target: >99%)",
                "Aurora uses a distributed cache — buffer pool restores automatically after restart",
            ],
            "thresholds": {
                "buffer_pool_hit_ratio_warn_pct":     99,
                "buffer_pool_hit_ratio_critical_pct": 95,
            },
            "sources": [
                "https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraMySQL.BestPractices.Performance.html",
            ],
        },
        "rds": {
            "recommendations": [
                "Set innodb_buffer_pool_size to 70-75% of instance RAM via DB Parameter Group",
                "Enable Performance Insights for query-level wait event analysis",
                "Monitor FreeableMemory CloudWatch metric; alarm below 10% of total RAM",
                "Use Read Replicas to offload read workloads from the primary",
                "Enable innodb_buffer_pool_dump_at_shutdown and load_at_startup for warm restarts",
            ],
            "thresholds": {
                "buffer_pool_hit_ratio_warn_pct":     99,
                "buffer_pool_hit_ratio_critical_pct": 95,
            },
        },
        "ec2": {
            "recommendations": [
                "Set innodb_buffer_pool_size = 70-80% of available RAM in my.cnf",
                "Set innodb_buffer_pool_instances = number of vCPUs",
                "Monitor Innodb_buffer_pool_reads vs Innodb_buffer_pool_read_requests",
                "Use sys.innodb_buffer_stats_by_schema to see per-schema memory consumption",
                "Enable innodb_buffer_pool_dump_at_shutdown for warm restarts",
            ],
            "thresholds": {
                "buffer_pool_hit_ratio_warn_pct":     99,
                "buffer_pool_hit_ratio_critical_pct": 95,
            },
        },
    },

    # -----------------------------------------------------------------------
    "performance": {
        "title": "Query Performance Optimization",
        "aurora": {
            "recommendations": [
                "Use Performance Insights as the primary tool for slow query identification",
                "Add indexes on columns used in WHERE, JOIN ON, ORDER BY, GROUP BY",
                "Use covering indexes to eliminate table lookups",
                "Avoid SELECT * — specify only required columns",
                "Aurora Parallel Query handles large analytical scans automatically",
                "Use EXPLAIN ANALYZE (MySQL 8.0+) for actual vs estimated row counts",
                "Batch INSERT/UPDATE operations; avoid single-row loops",
                "Avoid functions on indexed columns in WHERE clauses (prevents index use)",
            ],
            "thresholds": {
                "avg_query_latency_slow_sec":      1,
                "avg_query_latency_very_slow_sec": 10,
                "rows_examined_per_sent_warn":     100,
                "rows_examined_per_sent_critical": 1000,
                "no_index_used_pct_warn":          20,
                "no_index_used_pct_critical":      50,
            },
            "sources": [
                "https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraMySQL.BestPractices.Performance.html",
                "https://aws.amazon.com/blogs/database/best-practices-for-amazon-aurora-mysql-database-configuration/",
            ],
        },
        "rds": {
            "recommendations": [
                "Use Performance Insights (available on db.t3.medium and larger)",
                "Add indexes on WHERE, JOIN, ORDER BY, GROUP BY columns",
                "Use EXPLAIN to validate query execution plans before deploying",
                "Monitor ReadIOPS spikes — indicates missing indexes",
                "Use Enhanced Monitoring for CPU and memory correlation with query load",
            ],
            "thresholds": {
                "avg_query_latency_slow_sec":      1,
                "avg_query_latency_very_slow_sec": 10,
                "rows_examined_per_sent_warn":     100,
                "rows_examined_per_sent_critical": 1000,
                "no_index_used_pct_warn":          20,
                "no_index_used_pct_critical":      50,
            },
        },
        "ec2": {
            "recommendations": [
                "Use pt-query-digest (Percona Toolkit) to analyse slow query logs",
                "Use sys.statement_analysis view for aggregated query statistics",
                "Enable performance_schema for full SQL-level instrumentation",
                "Use pt-index-usage to identify indexes never used in queries",
                "Use EXPLAIN FORMAT=JSON for detailed optimizer cost breakdowns",
            ],
            "thresholds": {
                "avg_query_latency_slow_sec":      1,
                "avg_query_latency_very_slow_sec": 10,
                "rows_examined_per_sent_warn":     100,
                "rows_examined_per_sent_critical": 1000,
            },
        },
    },

    # -----------------------------------------------------------------------
    "maintenance": {
        "title": "Table Maintenance",
        "aurora": {
            "recommendations": [
                "Aurora handles storage-level defragmentation automatically",
                "Use ALTER TABLE ... FORCE to rebuild a specific table when needed",
                "OPTIMIZE TABLE on Aurora InnoDB is equivalent to ALTER TABLE ... FORCE",
                "Schedule ANALYZE TABLE via Lambda or Aurora MySQL Event Scheduler",
                "Take a manual RDS snapshot before any ALTER TABLE on large tables",
                "Use Aurora Fast Cloning to create a safe test copy before large operations",
            ],
            "sources": [
                "https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraMySQL.BestPractices.html",
            ],
        },
        "rds": {
            "recommendations": [
                "Schedule OPTIMIZE TABLE and ANALYZE TABLE via RDS maintenance window or Lambda",
                "Take an RDS snapshot before ALTER TABLE on large tables",
                "Monitor FreeStorageSpace after OPTIMIZE TABLE — temporary space required",
                "Use RDS Blue/Green Deployments for zero-downtime schema changes",
            ],
        },
        "ec2": {
            "recommendations": [
                "Schedule OPTIMIZE TABLE and ANALYZE TABLE via MySQL Event Scheduler or cron",
                "Use pt-online-schema-change for large ALTER TABLE without production locking",
                "Use Percona XtraBackup before any maintenance operations",
                "Monitor disk space after OPTIMIZE TABLE — requires temporary double-space",
            ],
        },
    },

    # -----------------------------------------------------------------------
    "optimization": {
        "title": "Index Optimization",
        "aurora": {
            "recommendations": [
                "Use Performance Insights to identify queries with high rows_examined counts",
                "Drop indexes with zero reads in table_io_waits_summary_by_index_usage",
                "Use MySQL 8.0 INVISIBLE indexes to safely test removal before dropping",
                "Build composite indexes covering WHERE + JOIN + ORDER BY columns together",
                "Aurora Parallel Query may partially compensate for missing indexes on large scans",
            ],
            "sources": [
                "https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraMySQL.BestPractices.Performance.html",
            ],
        },
        "rds": {
            "recommendations": [
                "Use Performance Insights to correlate index I/O with query performance",
                "Use RDS Blue/Green Deployments to safely test index additions in production",
                "Use INVISIBLE indexes to validate removal impact before dropping",
                "Monitor ReadIOPS drops after adding indexes to confirm improvement",
            ],
        },
        "ec2": {
            "recommendations": [
                "Use pt-index-usage (Percona Toolkit) for unused index detection",
                "Use pt-duplicate-key-checker for redundant index identification",
                "Use INVISIBLE indexes to test removal impact safely",
                "Verify index coverage with EXPLAIN FORMAT=JSON before and after changes",
            ],
        },
    },

    # -----------------------------------------------------------------------
    "summary": {
        "title": "Health Assessment",
        "aurora": {
            "recommendations": [
                "Enable Performance Insights for query-level SQL analysis",
                "Enable Enhanced Monitoring for OS metrics at 1-second resolution",
                "Set CloudWatch alarms: DatabaseConnections, CPUUtilization, AuroraReplicaLag",
                "Configure RDS Event Subscriptions (SNS) for failover and maintenance alerts",
                "Review cluster topology: writer instance, reader instances, promotion tiers",
            ],
            "scoring_rules": [
                "CRITICAL: Connection usage >90% of max_connections",
                "CRITICAL: Buffer pool hit ratio <95%",
                "CRITICAL: AuroraReplicaLag >5000ms",
                "WARNING:  Connection usage >75% of max_connections",
                "WARNING:  Buffer pool hit ratio <99%",
                "WARNING:  AuroraReplicaLag >1000ms",
            ],
        },
        "rds": {
            "recommendations": [
                "Enable Performance Insights (db.t3.medium and larger instances)",
                "Enable Enhanced Monitoring for OS-level metrics",
                "Set CloudWatch alarms: DatabaseConnections, CPUUtilization, ReplicaLag",
                "Configure RDS Event Subscriptions for failover and maintenance notifications",
            ],
            "scoring_rules": [
                "CRITICAL: Connection usage >90% of max_connections",
                "CRITICAL: Buffer pool hit ratio <95%",
                "CRITICAL: ReplicaLag >60 seconds",
                "WARNING:  Connection usage >75% of max_connections",
                "WARNING:  Buffer pool hit ratio <99%",
                "WARNING:  ReplicaLag >30 seconds",
            ],
        },
        "ec2": {
            "recommendations": [
                "Deploy Percona Monitoring and Management (PMM) for dashboards",
                "Configure OS-level alerting via Prometheus + Grafana or CloudWatch Agent",
                "Automate ANALYZE TABLE via MySQL Event Scheduler",
                "Implement automated backup strategy using Percona XtraBackup",
            ],
            "scoring_rules": [
                "CRITICAL: Connection usage >90% of max_connections",
                "CRITICAL: Buffer pool hit ratio <95%",
                "CRITICAL: Replica lag >60 seconds",
                "WARNING:  Connection usage >75% of max_connections",
                "WARNING:  Buffer pool hit ratio <99%",
                "WARNING:  Replica lag >30 seconds",
            ],
        },
    },

   # In the "cloudwatch" guidance section, update the thresholds block:

"cloudwatch": {
    "title": "CloudWatch Logs Analysis",
    "aurora": {
        "recommendations": [
            "Log groups: /aws/rds/cluster/{cluster-name}/{log-type}",
            "Set long_query_time = 1 via Aurora Cluster Parameter Group (default captures too much if set to 0)",
            "Enable Aurora MySQL audit plugin via Parameter Group for compliance logging",
            "Use CloudWatch Logs Insights for ad-hoc SQL-like log queries",
            "Create CloudWatch Metric Filters for automated alerting on error patterns",
            "Export logs to S3 for long-term retention beyond 90 days",
            "Filter rdsadmin queries from slow query analysis — focus on customer application queries only",
        ],
        # ✅ FIX: Per-category thresholds — not a single number for everything
        "thresholds": {
            "aborted_connections_warn":       5,
            "aborted_connections_critical":  20,
            "access_denied_warn":             5,
            "access_denied_critical":        20,
            "innodb_errors_critical":         1,
            "deadlocks_warn":                 2,
            "deadlocks_critical":            10,
            "replication_errors_critical":    1,
            "slow_query_warn_per_hour":      10,
            "slow_query_critical_per_hour":  50,
            "query_time_slow_sec":            1,
            "query_time_critical_sec":       10,
            "failed_login_warn":              5,
            "failed_login_critical":         20,
            "long_query_time_recommended_sec": 1,
        },
        "sources": [
            "https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraMySQL.BestPractices.CW.html",
        ],
    },
    "rds": {
        "recommendations": [
            "Enable log export via modify-db-instance (error, slowquery, audit, general)",
            "Log groups: /aws/rds/instance/{instance-id}/{log-type}",
            "Set long_query_time = 1 via DB Parameter Group",
            "Use CloudWatch Logs Insights for log analysis",
            "Create Metric Filters to alert on access denied and connection errors",
            "Filter rdsadmin queries from slow query analysis",
        ],
        "thresholds": {
            "aborted_connections_warn":       5,
            "aborted_connections_critical":  20,
            "access_denied_warn":             5,
            "access_denied_critical":        20,
            "innodb_errors_critical":         1,
            "deadlocks_warn":                 2,
            "deadlocks_critical":            10,
            "slow_query_warn_per_hour":      10,
            "slow_query_critical_per_hour":  50,
            "query_time_slow_sec":            1,
            "query_time_critical_sec":       10,
            "failed_login_warn":              5,
            "failed_login_critical":         20,
            "long_query_time_recommended_sec": 1,
        },
    },
    "ec2": {
        "recommendations": [
            "Install CloudWatch Logs Agent to stream MySQL log files to CloudWatch",
            "Configure /etc/awslogs/awslogs.conf for error log and slow query log",
            "Create CloudWatch Metric Filters for error pattern detection",
            "Use awslogs Docker log driver for containerized MySQL",
        ],
        "thresholds": {
            "aborted_connections_warn":       5,
            "aborted_connections_critical":  20,
            "slow_query_warn_per_hour":      10,
            "slow_query_critical_per_hour":  50,
            "query_time_slow_sec":            1,
            "query_time_critical_sec":       10,
            "long_query_time_recommended_sec": 1,
        },
    },
},

    # -----------------------------------------------------------------------
    "activity": {
        "title": "Current Activity & Lock Analysis",
        "aurora": {
            "recommendations": [
                "Use Performance Insights Active Sessions view for real-time query analysis",
                "Monitor Threads_running via CloudWatch or performance_schema",
                "Long-running transactions block Aurora storage — keep transactions short",
                "Use KILL <thread_id> with caution — verify query is not a critical process",
                "Enable performance_schema.events_statements_current for live SQL text",
            ],
            "thresholds": {
                "long_query_warn_seconds":     30,
                "long_query_critical_seconds": 300,
                "threads_running_warn":         20,
                "threads_running_critical":     50,
            },
        },
        "rds": {
            "recommendations": [
                "Use Performance Insights Active Sessions for real-time activity monitoring",
                "Monitor DatabaseConnections and Threads_running via CloudWatch",
                "Set wait_timeout = 300-900s to close idle connections automatically",
                "Use KILL  sparingly — confirm impact before executing",
            ],
            "thresholds": {
                "long_query_warn_seconds":     30,
                "long_query_critical_seconds": 300,
                "threads_running_warn":         20,
                "threads_running_critical":     50,
            },
        },
        "ec2": {
            "recommendations": [
                "Use SHOW FULL PROCESSLIST for real-time activity",
                "Use performance_schema.events_statements_current for live SQL text",
                "Monitor Threads_running via SHOW GLOBAL STATUS",
                "Use pt-kill (Percona Toolkit) for automated long-running query termination",
            ],
            "thresholds": {
                "long_query_warn_seconds":     30,
                "long_query_critical_seconds": 300,
                "threads_running_warn":         20,
                "threads_running_critical":     50,
            },
        },
    },
}


def build_guidance_context(
    categories: List[str],
    cluster_type: str = "aurora",
) -> str:
    """
    Build a flat, environment-specific guidance string with explicit
    knowledge authority headers.

    Parameters
    ----------
    categories  : list of category names from the health check run
    cluster_type: "aurora" | "rds" | "ec2"  (from EnvironmentInfo.cluster_type)

    Returns
    -------
    Guidance block structured as:
      [AUTHORITY HEADER]
      [Per-category recommendations for this environment only]
      [GROUNDING FOOTER]

    Bedrock is instructed to treat this as the PRIMARY knowledge source,
    ahead of its own training data.
    """
    env = cluster_type if cluster_type in ("aurora", "rds", "ec2") else "aurora"

    seen:  set        = set()
    body:  List[str]  = []

    for cat in categories:
        g = _GUIDANCE.get(cat)
        if not g or g["title"] in seen:
            continue
        seen.add(g["title"])

        env_block = g.get(env, {})
        if not env_block:
            continue

        body.append(f"\n--- {g['title'].upper()} ---")

        recs = env_block.get("recommendations", [])
        if recs:
            body.append("Recommendations (follow these — from AWS official documentation):")
            for r in recs:
                body.append(f"  • {r}")

        thresholds = env_block.get("thresholds", {})
        if thresholds:
            body.append("Authoritative Thresholds (use these exact values for severity assessment):")
            for k, v in thresholds.items():
                body.append(f"  • {k.replace('_', ' ')}: {v}")

        scoring = env_block.get("scoring_rules", [])
        if scoring:
            body.append("Scoring Rules:")
            for s in scoring:
                body.append(f"  • {s}")

        sources = env_block.get("sources", [])
        if sources:
            body.append("Sources:")
            for s in sources:
                body.append(f"  • {s}")

    if not body:
        return ""

    return "\n".join([
        _AUTHORITY_HEADER,
        GLOBAL_DISCLAIMER,
        *body,
        "",
        _GROUNDING_FOOTER,
    ])


def get_guidance_for_category(
    category: str, cluster_type: str = "aurora"
) -> Dict[str, Any]:
    """Return guidance block for a category filtered to the detected environment."""
    g = _GUIDANCE.get(category, {})
    if not g:
        return {}
    env = cluster_type if cluster_type in ("aurora", "rds", "ec2") else "aurora"
    return {
        "title":    g.get("title", category),
        "guidance": g.get(env, {}),
    }