# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Tests for the bundled SOP runbook tools (list_sops / get_sop).

Covers: catalogue/file consistency both ways, retrieval, rejection of unknown
slugs, and path-traversal safety.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("ALLOWED_ACCOUNTS", "111122223333")
os.environ.setdefault("ALLOWED_REGIONS", "us-east-1")

from server import SOP_CATALOGUE, SOP_DIR, get_sop, list_sops  # noqa: E402


def _fn(tool):
    """Unwrap a FastMCP-decorated tool to its underlying callable."""
    return getattr(tool, "fn", tool)


class TestSopCatalogue:
    def test_every_catalogued_slug_has_a_file(self):
        missing = [
            slug
            for slug in SOP_CATALOGUE
            if not os.path.isfile(os.path.join(SOP_DIR, f"{slug}.md"))
        ]
        assert missing == [], f"catalogued but no file: {missing}"

    def test_every_file_is_catalogued(self):
        on_disk = {f[:-3] for f in os.listdir(SOP_DIR) if f.endswith(".md")}
        uncatalogued = sorted(on_disk - set(SOP_CATALOGUE))
        assert uncatalogued == [], f"file present but not catalogued: {uncatalogued}"

    def test_triage_entry_point_exists(self):
        """The vague-symptom entry point must always be present."""
        assert "Z-general-triage" in SOP_CATALOGUE

    def test_catalogue_descriptions_are_non_empty(self):
        for slug, desc in SOP_CATALOGUE.items():
            assert desc.strip(), f"empty description for {slug}"


class TestListSops:
    def test_lists_every_slug(self):
        out = _fn(list_sops)()
        for slug in SOP_CATALOGUE:
            assert slug in out, f"{slug} missing from list_sops output"

    def test_points_at_triage_runbook(self):
        assert "Z-general-triage" in _fn(list_sops)()


class TestGetSop:
    def test_returns_content_for_each_slug(self):
        for slug in SOP_CATALOGUE:
            body = _fn(get_sop)(slug)
            assert not body.startswith("ERROR"), f"{slug} failed to load"
            assert len(body) > 200, f"{slug} content suspiciously short"

    def test_unknown_slug_is_rejected_with_valid_list(self):
        out = _fn(get_sop)("no-such-runbook")
        assert out.startswith("ERROR")
        assert "Z-general-triage" in out, "error should list valid slugs"

    @pytest.mark.parametrize(
        "evil",
        [
            "../server",
            "../../etc/passwd",
            "/etc/passwd",
            "Z-general-triage/../../server",
            "..%2f..%2fetc%2fpasswd",
            "Z-general-triage.md",
            "",
        ],
    )
    def test_path_traversal_is_refused(self, evil):
        """Slugs are allowlist-validated, so no traversal can reach the FS."""
        out = _fn(get_sop)(evil)
        assert out.startswith("ERROR"), f"traversal not refused: {evil!r}"
