"""
AI-DBA Agent — Amazon Bedrock-backed MySQL expert.
Author: Kiran Mayee Mulupuru, Sr. Specialist Database TAM, AWS Enterprise Support

✅ FIXES:
- Environment probed ONCE at session start, cached, injected into every prompt
- Explicit knowledge priority: live data → AWS guidance → training (as fallback only)
- Prompt assembly order: env context → guidance → live data
- chat() detects "run it / show me live" and fires actual SQL
- chat() detects CloudWatch log requests with broader pattern matching
- Context-aware CloudWatch detection: remembers user confirmed logs are enabled
- Keyword-based intent classifier
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import textwrap
from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config

from rds_aidba.mcp_client import (
    ClusterConfig,
    EnvironmentInfo,
    MySQLMCPClient,
    QueryResult,
)
from rds_aidba.health_checks import (
    ALL_HEALTH_CHECKS,
    CATEGORIES,
    get_checks_by_category,
)
from rds_aidba.health_checks.guidance import build_guidance_context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = textwrap.dedent("""
    You are rds-aidba, an expert AI Database Administrator specialised in
    Amazon Aurora MySQL and Amazon RDS MySQL running on AWS.
    Created by Kiran Mayee Mulupuru, Sr. Specialist Database TAM, AWS Enterprise Support.

    ═══════════════════════════════════════════════════════
    KNOWLEDGE PRIORITY — ALWAYS FOLLOW THIS ORDER
    ═══════════════════════════════════════════════════════

    1. LIVE QUERY RESULTS  (highest authority)
       Facts about THIS specific cluster right now.
       Always report what the data actually shows.

    2. AWS GUIDANCE BLOCK  (primary knowledge source)
       Every prompt contains a guidance block sourced from
       AWS official documentation and AWS prescriptive guidance.
       This is your PRIMARY reference for ALL recommendations.
       Follow it exactly. Do not deviate from it.

    3. YOUR TRAINING KNOWLEDGE  (fallback only)
       Use ONLY when:
         a) The guidance block does not cover the topic, AND
         b) Your knowledge is consistent with AWS official docs
       Never use training knowledge to OVERRIDE the guidance block.
       Never use community blogs, Stack Overflow, or Percona/MariaDB
       recommendations for Aurora or RDS environments.

    ═══════════════════════════════════════════════════════
    CRITICAL RESPONSE RULES
    ═══════════════════════════════════════════════════════

    1. ENVIRONMENT-SPECIFIC OUTPUT ONLY
       The CONFIRMED CLUSTER ENVIRONMENT block tells you exactly
       what type this is (aurora / rds / ec2).
       Give recommendations for THAT TYPE ONLY.
       NEVER write:
         "If this is Aurora...", "If RDS...", "If EC2...",
         "In case of Aurora...", "For Aurora environments..."
       State facts directly using the confirmed environment.

    2. REAL-TIME ANALYSIS FIRST
       "bad query", "what is running", "is there load", "blocking",
       "hanging", "stuck" → report PROCESSLIST results first,
       exactly as shown in the data. Quote actual query text,
       user, state, and duration.

    3. WHEN CLOUDWATCH LOG DATA IS PROVIDED — ANALYSE IT DIRECTLY
       If CloudWatch log data is in the prompt, analyse the actual
       events. Do NOT tell the user how to query CloudWatch Logs
       Insights manually — the data is already here. Report:
         - Actual slow query times from the log events
         - Actual SQL text from the log events
         - Actual lock times, rows examined, rows sent
         - Timeline patterns (spikes, sustained load)
       Only suggest CloudWatch Logs Insights if NO log data
       was retrievable (empty results).

    4. EXECUTE WHEN ASKED
       "run it", "check it", "execute", "show me live" →
       actual results will be provided; analyse them directly.

    5. FILTER rdsadmin
       rdsadmin = AWS internal managed service user.
       Exclude from analysis unless consuming >50% of resources.
       Never flag rdsadmin heartbeat as a customer issue.

    6. CONVERSATION CONTEXT
       Remember findings from earlier in this session.
       Build on prior analysis for follow-up questions.
       If the user says a feature is already enabled/configured,
       act on that immediately — fetch the data, don't re-explain
       how to enable it.

    7. CLOUDWATCH LOG TYPES — SEPARATE SECTIONS
       Present error / slowquery / audit logs as separate sections.
       Never merge them into a single generic summary.

    8. OUTPUT LENGTH
       Real-time / follow-up: 200-400 words, direct and factual
       Category checks:       500-800 words
       Full health check:     1000-1500 words with scored assessment
       CloudWatch log analysis: 600-1000 words per log type

    ═══════════════════════════════════════════════════════
    OUTPUT FORMAT
    ═══════════════════════════════════════════════════════

    ## SUMMARY
    [2-3 sentences: key finding + severity.
     State environment type ONCE here, then never again.]

    ## FINDINGS
    [SEVERITY] Finding title
    - Evidence from live data (quote actual values)
    - Root cause
    - Impact

    ## RECOMMENDATIONS
    [PRIORITY] Action
    - Sourced from guidance block
    - Implementation steps specific to confirmed environment
    - Expected outcome with metrics
    - Validation query
""").strip()


# ---------------------------------------------------------------------------
# Ad-hoc SQL patterns
# ---------------------------------------------------------------------------

RUN_IT_PATTERNS = [
    r"\brun\s+(it|that|this|the\s+query|a\s+query)\b",
    r"\bexecute\s+(it|that|this)\b",
    r"\bcheck\s+(it|that|live|now|currently)\b",
    r"\bshow\s+me\s+(live|current|now|the\s+data|what.s\s+running)\b",
    r"\bcan\s+you\s+(run|check|execute|query)\b",
    r"\bactually\s+run\b",
    r"\bquery\s+the\s+(db|database|cluster)\b",
    r"\bwhat\s+(is|are)\s+(currently\s+)?(running|active|executing)\b",
    r"\bshow\s+(processlist|running\s+queries|active\s+queries)\b",
]

# ---------------------------------------------------------------------------
# ✅ UPDATED: Broader CloudWatch fetch trigger patterns
# ---------------------------------------------------------------------------

CLOUDWATCH_FETCH_PATTERNS = [
    # "show me [latest] X logs"
    r"\bshow\s+(me\s+)?(the\s+)?(latest\s+|recent\s+|last\s+)?(slow\s+query\s+logs?|error\s+logs?|audit\s+logs?|cloudwatch\s+logs?|logs?)\b",
    # "fetch/get/pull/check/analyse X logs"
    r"\b(fetch|get|pull|retrieve|read|check|analyse|analyze)\s+(the\s+)?(latest\s+|recent\s+|last\s+)?(slow\s+query\s+logs?|error\s+logs?|audit\s+logs?|cloudwatch\s+logs?|logs?)\b",
    # "what do the logs show/say"
    r"\bwhat\s+(do|does|is|are)\s+(the\s+)?(slow\s+query\s+logs?|error\s+logs?|cloudwatch\s+logs?|logs?)\s+(show|say|contain|have|look\s+like)\b",
    # "X log details/data/content"
    r"\b(slow\s+query\s+log|error\s+log|audit\s+log|cloudwatch\s+log)\s+(details?|data|content|results?|analysis|summary|breakdown)\b",
    # "analyze the X logs"
    r"\banalyze\s+(the\s+)?(slow\s+query\s+logs?|error\s+logs?|audit\s+logs?|cloudwatch\s+logs?|logs?)\b",
    # "already enabled/publishing/configured" → act on it
    r"\balready\s+(enabled|publishing|published|configured|sending|streaming|exporting|active|running|set\s+up|setup)\b",
    # "logs are enabled/publishing/available"
    r"\b(slow\s+query|error|audit|cloudwatch)?\s*logs?\s+(are\s+)?(enabled|configured|publishing|published|available|active|flowing|streaming|going\s+to)\b",
    # "can you check/look at logs"
    r"\bcan\s+you\s+(check|look\s+at|fetch|get|pull|analyse|analyze|review)\s+(the\s+)?(latest\s+|recent\s+)?(logs?|error\s+logs?|slow\s+query\s+logs?|cloudwatch\s+logs?)\b",
    # "check/look at/review X logs"
    r"\b(check|look\s+at|review)\s+(the\s+)?(latest\s+|recent\s+|last\s+)?(cloudwatch\s+)?(error\s+logs?|slow\s+query\s+logs?|audit\s+logs?|logs?)\b",
    # "published/publishing to cloudwatch"
    r"\b(published?|publishing|sending|streaming|exporting|going)\s+(to\s+)?(cloudwatch|cw|cloud\s+watch)\b",
    # "cloudwatch logs/error/slow/audit" anywhere
    r"\bcloudwatch\s+(logs?|error|slow|audit)\b",
    # "X logs in/on/from cloudwatch"
    r"\b(error|slow|audit)\s+logs?\s+(in|on|at|from|to)\s+cloudwatch\b",
    # "latest/recent X logs"
    r"\b(latest|recent|current|new)\s+(error\s+logs?|slow\s+query\s+logs?|audit\s+logs?|logs?)\b",
    # "log details/analysis/events"
    r"\blog\s+(details?|analysis|breakdown|summary|events?|data)\b",
]

REALTIME_ACTIVITY_SQL = """
    SELECT
        p.ID,
        p.USER,
        p.HOST,
        p.DB,
        p.COMMAND,
        p.TIME        AS seconds_running,
        p.STATE,
        LEFT(COALESCE(p.INFO, ''), 500) AS query_text,
        CASE
            WHEN p.STATE LIKE '%lock%'         THEN 'LOCK_WAIT'
            WHEN p.STATE LIKE '%wait%'         THEN 'WAITING'
            WHEN p.COMMAND = 'Query'
             AND p.TIME > 30                   THEN 'LONG_RUNNING'
            WHEN p.COMMAND = 'Query'           THEN 'EXECUTING'
            ELSE p.COMMAND
        END AS status_category
    FROM information_schema.PROCESSLIST p
    WHERE p.USER != 'rdsadmin'
      AND p.COMMAND != 'Sleep'
    ORDER BY p.TIME DESC
    LIMIT 30
"""

REALTIME_WAIT_SQL = """
    SELECT
        t.PROCESSLIST_ID                            AS thread_id,
        t.PROCESSLIST_USER                          AS user,
        t.PROCESSLIST_HOST                          AS host,
        t.PROCESSLIST_DB                            AS db,
        t.PROCESSLIST_TIME                          AS seconds_running,
        t.PROCESSLIST_STATE                         AS state,
        LEFT(COALESCE(t.PROCESSLIST_INFO, ''), 400) AS query_text,
        LEFT(COALESCE(esc.SQL_TEXT, ''), 400)       AS full_sql_from_events
    FROM performance_schema.threads t
    LEFT JOIN performance_schema.events_statements_current esc
           ON t.THREAD_ID = esc.THREAD_ID
    WHERE t.PROCESSLIST_USER IS NOT NULL
      AND t.PROCESSLIST_USER != 'rdsadmin'
      AND t.PROCESSLIST_COMMAND != 'Sleep'
    ORDER BY t.PROCESSLIST_TIME DESC
    LIMIT 20
"""


# ---------------------------------------------------------------------------
# Intent keyword map
# ---------------------------------------------------------------------------

INTENT_KEYWORDS: Dict[str, List[str]] = {
    "activity": [
        "running", "active", "current", "now", "live", "bad query",
        "slow query now", "what is running", "blocking", "locked",
        "hanging", "stuck", "load", "heavy", "processlist", "kill",
        "threads", "waiting", "executing", "who is connected",
    ],
    "performance": [
        "slow", "top queries", "expensive", "worst queries", "digest",
        "query time", "long query", "performance", "statements",
        "optimizer", "execution plan", "explain", "slow queries",
    ],
    "connections": [
        "connection", "connect", "max_connections", "too many",
        "aborted", "sleep", "idle", "thread", "pool",
    ],
    "innodb": [
        "buffer pool", "innodb", "dirty pages", "hit ratio",
        "ibdata", "redo log", "checkpoint",
    ],
    "replication": [
        "replica", "replication", "lag", "binlog", "gtid",
        "slave", "primary", "standby", "failover", "read replica",
    ],
    "storage": [
        "storage", "disk", "size", "space", "table size",
        "database size", "fragmentation", "iops", "tablespace",
    ],
    "maintenance": [
        "fragment", "optimize", "analyze", "repair", "bloat",
        "free space", "maintenance", "defrag",
    ],
    "optimization": [
        "index", "unused index", "missing index", "full scan",
        "drop index", "add index", "covering index", "no index",
    ],
    "configuration": [
        "config", "variable", "parameter", "setting", "version",
        "environment", "aurora version", "server id", "what version",
        "what type", "aurora or", "rds or", "what database",
    ],
    "summary": [
        "health", "overall", "status", "score", "assessment",
        "all checks", "everything", "full check",
    ],
    "cloudwatch": [
        "cloudwatch", "logs", "error log", "slow log", "audit log",
        "log group", "log analysis", "cw logs",
    ],
}

_CW_LOG_TYPE_KEYWORDS: Dict[str, List[str]] = {
    "slowquery": [
        "slow", "slow query", "slow queries", "slowquery",
        "long running", "query time", "slow log",
    ],
    "error": [
        "error", "errors", "error log", "crash", "warning",
        "connection error", "access denied",
    ],
    "audit": [
        "audit", "audit log", "security", "login", "privilege",
        "who accessed", "user activity",
    ],
}


class AIDBAAgent:
    DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-20250514-v1:0"

    def __init__(
        self,
        cluster_config: ClusterConfig,
        bedrock_model_id: str = DEFAULT_MODEL,
        aws_region: str = "us-east-1",
        aws_profile: str = "default",
    ) -> None:
        self.cluster_config = cluster_config
        self.model_id       = bedrock_model_id
        self.aws_region     = aws_region

        self._env: Optional[EnvironmentInfo]     = None
        self._conversation: List[Dict[str, Any]] = []
        self._session_results: List[QueryResult] = []

        session = boto3.Session(profile_name=aws_profile, region_name=aws_region)
        self._bedrock = session.client(
            "bedrock-runtime",
            config=Config(retries={"max_attempts": 3, "mode": "adaptive"}),
        )

    # ------------------------------------------------------------------
    # Environment probe
    # ------------------------------------------------------------------

    async def _ensure_env(self, mcp: MySQLMCPClient) -> EnvironmentInfo:
        if self._env is None:
            self._env = await mcp.probe_environment()
            logger.info("Environment: %s", self._env.describe())
        return self._env

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat(self, user_message: str) -> str:
        """
        Four cases:
        1. "run it / show me live"   → execute real-time PROCESSLIST SQL
        2. CloudWatch log request    → fetch actual log data from CloudWatch
        3. Normal question           → classify intent, run health checks
        4. Follow-up                 → session context + minimal re-query
        """
        # Case 1: live SQL execution
        if self._is_run_request(user_message):
            async with MySQLMCPClient(self.cluster_config) as mcp:
                env     = await self._ensure_env(mcp)
                results = await self._run_realtime_queries(mcp)

            self._session_results.extend(results)
            self._session_results = self._session_results[-50:]
            prompt   = self._build_chat_prompt(user_message, results, env)
            response = await self._invoke_bedrock(prompt)

        # Case 2: CloudWatch log fetch
        elif self._is_cloudwatch_fetch_request(user_message):
            log_types  = self._detect_cloudwatch_log_types(user_message)
            hours_back = self._detect_hours_back(user_message)
            response   = await self._fetch_and_analyse_cloudwatch(
                user_message, log_types, hours_back
            )

        # Case 3 / 4: intent-based health checks
        else:
            async with MySQLMCPClient(self.cluster_config) as mcp:
                env        = await self._ensure_env(mcp)
                categories = self._classify_intent(user_message)
                checks: List[Dict[str, Any]] = []
                for cat in categories:
                    checks.extend(get_checks_by_category(cat))

                seen: set = set()
                unique: List[Dict[str, Any]] = []
                for c in checks:
                    if c["name"] not in seen:
                        seen.add(c["name"])
                        unique.append(c)

                results = await mcp.run_health_checks_batch(unique) if unique else []

            self._session_results.extend(results)
            self._session_results = self._session_results[-50:]
            prompt   = self._build_chat_prompt(user_message, results, env)
            response = await self._invoke_bedrock(prompt)

        self._conversation.append(
            {"role": "user",      "content": [{"text": user_message}]}
        )
        self._conversation.append(
            {"role": "assistant", "content": [{"text": response}]}
        )
        return response

    async def run_full_health_check(self) -> str:
        sql_checks = [c for c in ALL_HEALTH_CHECKS if not c.get("is_cloudwatch")]
        async with MySQLMCPClient(self.cluster_config) as mcp:
            env     = await self._ensure_env(mcp)
            results = await mcp.run_health_checks_batch(sql_checks)
        prompt = self._build_analysis_prompt(
            "Comprehensive MySQL health check — full scored assessment.", results, env
        )
        return await self._invoke_bedrock(prompt)

    async def run_category_check(self, category: str) -> str:
        checks = get_checks_by_category(category)
        if not checks:
            return (
                f"Unknown category '{category}'. "
                f"Available: {', '.join(CATEGORIES.keys())}"
            )
        if category == "cloudwatch":
            return await self.analyze_cloudwatch_logs(hours_back=1)

        sql_checks = [c for c in checks if not c.get("is_cloudwatch")]
        async with MySQLMCPClient(self.cluster_config) as mcp:
            env     = await self._ensure_env(mcp)
            results = await mcp.run_health_checks_batch(sql_checks)
        prompt = self._build_analysis_prompt(
            f"Detailed '{category}' analysis with recommendations.", results, env
        )
        return await self._invoke_bedrock(prompt)

    async def analyze_cloudwatch_logs(
        self,
        log_group: Optional[str] = None,
        hours_back: int = 1,
    ) -> str:
        async with MySQLMCPClient(self.cluster_config) as mcp:
            env          = await self._ensure_env(mcp)
            log_sections = await self._fetch_all_log_sections(mcp, hours_back)

        if not log_sections:
            return (
                "No CloudWatch log groups configured for this cluster.\n"
                "Add cloudwatch_log_group, cloudwatch_slow_query_log_group, "
                "and/or cloudwatch_audit_log_group to config.yaml."
            )
        prompt = self._build_cloudwatch_prompt(log_sections, hours_back, env)
        return await self._invoke_bedrock(prompt)

    def reset_conversation(self) -> None:
        self._conversation    = []
        self._session_results = []

    # ------------------------------------------------------------------
    # ✅ UPDATED: Intent detection with context awareness
    # ------------------------------------------------------------------

    def _is_run_request(self, message: str) -> bool:
        return any(re.search(p, message.lower()) for p in RUN_IT_PATTERNS)

    def _is_cloudwatch_fetch_request(self, message: str) -> bool:
        """
        Detect CloudWatch log fetch requests.

        Two-stage detection:
        1. Pattern match on current message (broad patterns)
        2. Context-aware: if user confirmed logs are enabled in recent
           turns and current message mentions logs → fetch them
        """
        msg_lower = message.lower()

        # Stage 1: direct pattern match
        if any(re.search(p, msg_lower) for p in CLOUDWATCH_FETCH_PATTERNS):
            return True

        # Stage 2: context-aware detection
        # If user recently confirmed logs are published/enabled and
        # current message mentions logs → they want the data fetched
        log_confirmed_phrases = [
            "already", "enabled", "publishing", "published", "configured",
            "sending", "streaming", "exporting", "active", "set up", "setup",
            "it is", "they are", "i have", "i did",
        ]
        log_topic_phrases = [
            "log", "logs", "error", "slow", "audit", "cloudwatch",
        ]
        if self._conversation:
            recent_turns = self._conversation[-6:]   # last 3 exchanges
            user_turns   = [
                t["content"][0]["text"].lower()
                for t in recent_turns
                if t["role"] == "user"
            ]
            log_was_confirmed = any(
                any(phrase in turn for phrase in log_confirmed_phrases)
                and any(topic in turn for topic in log_topic_phrases)
                for turn in user_turns
            )
            current_mentions_logs = any(
                topic in msg_lower for topic in log_topic_phrases
            )
            if log_was_confirmed and current_mentions_logs:
                return True

        return False

    def _detect_cloudwatch_log_types(self, message: str) -> List[str]:
        """Determine which log types to fetch from message content."""
        msg_lower = message.lower()
        matched   = []
        for log_type, keywords in _CW_LOG_TYPE_KEYWORDS.items():
            if any(kw in msg_lower for kw in keywords):
                matched.append(log_type)

        # No specific type → fetch all configured
        if not matched:
            if self.cluster_config.cloudwatch_log_group:
                matched.append("error")
            if self.cluster_config.cloudwatch_slow_query_log_group:
                matched.append("slowquery")
            if self.cluster_config.cloudwatch_audit_log_group:
                matched.append("audit")

        return matched if matched else ["error", "slowquery"]

    def _detect_hours_back(self, message: str) -> int:
        """Extract time window from message; default 1 hour."""
        match = re.search(
            r'(?:last|past|previous|)\s*(\d+)\s*(?:hour|hr)s?',
            message.lower()
        )
        if match:
            return min(max(int(match.group(1)), 1), 24)
        return 1

    def _classify_intent(self, message: str) -> List[str]:
        msg_lower = message.lower()
        matched: List[str] = []
        for category, keywords in INTENT_KEYWORDS.items():
            if any(kw in msg_lower for kw in keywords):
                matched.append(category)

        realtime = [
            "running", "now", "current", "active", "bad query",
            "load", "hanging", "stuck", "blocking", "what is",
        ]
        if any(t in msg_lower for t in realtime):
            for must in ["activity", "connections"]:
                if must not in matched:
                    matched.insert(0, must)

        return matched if matched else ["performance", "activity"]

    # ------------------------------------------------------------------
    # CloudWatch fetch helpers
    # ------------------------------------------------------------------

    async def _fetch_and_analyse_cloudwatch(
        self,
        user_message: str,
        log_types: List[str],
        hours_back: int,
    ) -> str:
        async with MySQLMCPClient(self.cluster_config) as mcp:
            env          = await self._ensure_env(mcp)
            log_sections = await self._fetch_selected_log_sections(
                mcp, log_types, hours_back
            )

        if not log_sections:
            missing = []
            if "error"     in log_types and not self.cluster_config.cloudwatch_log_group:
                missing.append("cloudwatch_log_group")
            if "slowquery" in log_types and not self.cluster_config.cloudwatch_slow_query_log_group:
                missing.append("cloudwatch_slow_query_log_group")
            if "audit"     in log_types and not self.cluster_config.cloudwatch_audit_log_group:
                missing.append("cloudwatch_audit_log_group")

            if missing:
                return (
                    f"The following CloudWatch log groups are not configured "
                    f"in config.yaml:\n"
                    + "\n".join(f"  • {m}" for m in missing)
                    + "\n\nAdd them to config.yaml and restart the tool."
                )
            return "No log events found in the specified time window."

        prompt = self._build_cloudwatch_chat_prompt(
            user_message, log_sections, hours_back, env
        )
        return await self._invoke_bedrock(prompt)

    async def _fetch_all_log_sections(
        self, mcp: MySQLMCPClient, hours_back: int
    ) -> List[Dict[str, Any]]:
        return await self._fetch_selected_log_sections(
            mcp, ["error", "slowquery", "audit"], hours_back
        )

    async def _fetch_selected_log_sections(
        self,
        mcp: MySQLMCPClient,
        log_types: List[str],
        hours_back: int,
    ) -> List[Dict[str, Any]]:
        log_sections: List[Dict[str, Any]] = []

        if "error" in log_types and self.cluster_config.cloudwatch_log_group:
            events = await mcp.fetch_cloudwatch_logs(
                self.cluster_config.cloudwatch_log_group, "error", hours_back
            )
            log_sections.append({
                "log_type":       "error",
                "log_group":      self.cluster_config.cloudwatch_log_group,
                "events_fetched": len(events),
                "analysis":       mcp.analyze_error_logs(events),
                "raw_sample":     events[:20],
            })

        if "slowquery" in log_types and self.cluster_config.cloudwatch_slow_query_log_group:
            events = await mcp.fetch_cloudwatch_logs(
                self.cluster_config.cloudwatch_slow_query_log_group, "slowquery", hours_back
            )
            log_sections.append({
                "log_type":       "slowquery",
                "log_group":      self.cluster_config.cloudwatch_slow_query_log_group,
                "events_fetched": len(events),
                "analysis":       mcp.analyze_slow_query_logs(events),
                "raw_sample":     events[:20],
            })

        if "audit" in log_types and self.cluster_config.cloudwatch_audit_log_group:
            events = await mcp.fetch_cloudwatch_logs(
                self.cluster_config.cloudwatch_audit_log_group, "audit", hours_back
            )
            log_sections.append({
                "log_type":       "audit",
                "log_group":      self.cluster_config.cloudwatch_audit_log_group,
                "events_fetched": len(events),
                "analysis":       mcp.analyze_audit_logs(events),
                "raw_sample":     events[:20],
            })

        return log_sections

    # ------------------------------------------------------------------
    # Real-time queries
    # ------------------------------------------------------------------

    async def _run_realtime_queries(
        self, mcp: MySQLMCPClient
    ) -> List[QueryResult]:
        results = []
        results.append(
            await mcp.execute_query(REALTIME_ACTIVITY_SQL, "live_processlist")
        )
        results.append(
            await mcp.execute_query(REALTIME_WAIT_SQL, "live_statements_current")
        )
        return results

    # ------------------------------------------------------------------
    # Prompt builders — order: env → guidance → data → question
    # ------------------------------------------------------------------

    def _build_chat_prompt(
        self,
        user_question: str,
        new_results: List[QueryResult],
        env: EnvironmentInfo,
    ) -> str:
        check_names = {r.check_name for r in new_results}
        active_cats = list({
            c["category"] for c in ALL_HEALTH_CHECKS if c["name"] in check_names
        })
        for must in ["activity", "connections"]:
            if must not in active_cats:
                active_cats.append(must)

        guidance     = build_guidance_context(active_cats, cluster_type=env.cluster_type)
        results_text = self._format_results(new_results)

        prior_context = ""
        if self._session_results:
            prior_names = [
                r.check_name for r in self._session_results
                if r not in new_results and r.success
            ]
            if prior_names:
                prior_context = (
                    f"\nPRIOR SESSION CHECKS:\n"
                    f"{', '.join(prior_names[:15])}\n"
                )

        return f"""{env.prompt_context()}

{guidance}

=== LIVE QUERY RESULTS ===
{results_text}
{prior_context}

User question: {user_question}

Answer using the knowledge priority order:
1. Report what the live data shows (facts first)
2. Apply the guidance block for recommendations
3. Be specific to the confirmed environment — no conditional language"""

    def _build_analysis_prompt(
        self,
        question: str,
        results: List[QueryResult],
        env: EnvironmentInfo,
    ) -> str:
        check_names  = {r.check_name for r in results}
        active_cats  = list({
            c["category"] for c in ALL_HEALTH_CHECKS if c["name"] in check_names
        })
        guidance     = build_guidance_context(active_cats, cluster_type=env.cluster_type)
        results_text = self._format_results(results)

        return f"""{env.prompt_context()}

{guidance}

=== QUERY RESULTS ===
{results_text}

Request: {question}

Structure your response:
1. SUMMARY — confirmed environment, overall severity score
2. FINDINGS — each issue with live data evidence, root cause, impact
3. RECOMMENDATIONS — follow guidance block exactly,
   no conditional language, include implementation steps and validation"""

    def _build_cloudwatch_prompt(
        self,
        log_sections: List[Dict[str, Any]],
        hours_back: int,
        env: EnvironmentInfo,
    ) -> str:
        guidance = build_guidance_context(["cloudwatch"], cluster_type=env.cluster_type)
        section_texts = []
        for section in log_sections:
            log_type = section["log_type"].upper()
            raw_msgs = "\n".join(
                e.get("message", "")[:300]
                for e in section.get("raw_sample", [])
                if e.get("message", "").strip()
            )
            section_texts.append(
                f"=== {log_type} LOG DATA ===\n"
                f"Log group     : {section['log_group']}\n"
                f"Events fetched: {section['events_fetched']}\n"
                f"Parsed analysis:\n"
                f"{json.dumps(section['analysis'], indent=2, default=str)}\n"
                f"\nSample raw log lines:\n{raw_msgs}"
            )

        return f"""{env.prompt_context()}

{guidance}

=== CLOUDWATCH LOG DATA — Last {hours_back} hour(s) ===
{chr(10).join(section_texts)}

Analyse the ACTUAL log data above. Do not tell the user how to query
CloudWatch Logs Insights — the data is already here.

## ERROR LOG ANALYSIS
## SLOW QUERY LOG ANALYSIS
## AUDIT LOG ANALYSIS

For each section:
1. Severity and actual event count
2. Breakdown by type with actual counts
3. Top 3-5 actual log samples with timestamps
4. Timeline spikes or patterns
5. Specific recommendations from guidance block

## COMBINED HEALTH SUMMARY"""

    def _build_cloudwatch_chat_prompt(
        self,
        user_question: str,
        log_sections: List[Dict[str, Any]],
        hours_back: int,
        env: EnvironmentInfo,
    ) -> str:
        guidance = build_guidance_context(["cloudwatch"], cluster_type=env.cluster_type)
        section_texts = []
        for section in log_sections:
            log_type = section["log_type"].upper()
            raw_msgs = "\n".join(
                f"[{e.get('timestamp','')}] {e.get('message','')[:400]}"
                for e in section.get("raw_sample", [])
                if e.get("message", "").strip()
            )
            section_texts.append(
                f"=== {log_type} LOG DATA (last {hours_back}h) ===\n"
                f"Log group     : {section['log_group']}\n"
                f"Events fetched: {section['events_fetched']}\n"
                f"Parsed metrics:\n"
                f"{json.dumps(section['analysis'], indent=2, default=str)}\n"
                f"\nActual log samples:\n"
                f"{raw_msgs if raw_msgs else '(no events in time window)'}"
            )

        prior_context = ""
        if self._session_results:
            prior_names = [r.check_name for r in self._session_results if r.success]
            if prior_names:
                prior_context = (
                    f"\nPRIOR SESSION CONTEXT:\n"
                    f"Earlier checks: {', '.join(prior_names[:15])}\n"
                )

        return f"""{env.prompt_context()}

{guidance}

{chr(10).join(section_texts)}
{prior_context}

User question: {user_question}

Analyse the ACTUAL log data above. Do NOT suggest how to query CloudWatch
Logs Insights manually — the data is already fetched and present here.

For each log type:
1. State actual severity based on event counts
2. Quote actual log lines with timestamps
3. Identify patterns (spikes, recurring errors, sustained load)
4. Give specific recommendations from the guidance block
5. Cross-reference with prior session findings if relevant"""

    def _format_results(self, results: List[QueryResult]) -> str:
        parts = []
        for r in results:
            if r.error:
                parts.append(f"[{r.check_name}]\nERROR: {r.error}")
            else:
                parts.append(
                    f"[{r.check_name}]\n"
                    f"{json.dumps(r.rows, indent=2, default=str)}"
                )
        return "\n\n".join(parts) if parts else "No results available."

    # ------------------------------------------------------------------
    # Bedrock invocation
    # ------------------------------------------------------------------

    async def _invoke_bedrock(self, prompt: str) -> str:
        messages = list(self._conversation) + [
            {"role": "user", "content": [{"text": prompt}]}
        ]
        return await self._converse_raw(messages)

    async def _converse_raw(self, messages: List[Dict]) -> str:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._bedrock.converse(
                modelId=self.model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=messages,
                inferenceConfig={"maxTokens": 4096, "temperature": 0.1},
            ),
        )
        return "".join(
            b.get("text", "")
            for b in response["output"]["message"]["content"]
        )