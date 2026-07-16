"""
Health Check Query Registry — MySQL + PostgreSQL
Author: Kiran Mayee Mulupuru, Sr. Specialist Database TAM, AWS Enterprise Support

Unified registry for both MySQL and PostgreSQL health check queries.
Engine selection is determined by cluster config `engine` field.
"""

from rds_aidba.health_checks.mysql import (
    ALL_HEALTH_CHECKS,
    CATEGORIES,
    CATEGORY_DESCRIPTIONS,
    get_checks_by_category,
    get_check_by_name,
    get_critical_checks,
    get_aurora_checks,
    get_cloudwatch_checks,
)

from rds_aidba.health_checks.postgresql import (
    ALL_PG_HEALTH_CHECKS,
    PG_CATEGORIES,
    PG_CATEGORY_DESCRIPTIONS,
    get_pg_checks_by_category,
    get_pg_check_by_name,
)

__all__ = [
    # MySQL
    "ALL_HEALTH_CHECKS",
    "CATEGORIES",
    "CATEGORY_DESCRIPTIONS",
    "get_checks_by_category",
    "get_check_by_name",
    "get_critical_checks",
    "get_aurora_checks",
    "get_cloudwatch_checks",
    # PostgreSQL
    "ALL_PG_HEALTH_CHECKS",
    "PG_CATEGORIES",
    "PG_CATEGORY_DESCRIPTIONS",
    "get_pg_checks_by_category",
    "get_pg_check_by_name",
]
