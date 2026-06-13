"""Canonical kind labels for knowledge-graph nodes and edges.

Centralized so all writers agree on spelling and downstream consumers
(ReAct loop, source-hunt pipeline, campaign reporter) can filter
reliably. Nothing else in the system should hard-code these strings.
"""

from __future__ import annotations

from enum import StrEnum


class NodeKind(StrEnum):
    # Reconnaissance entities
    TARGET = "target"
    HOST = "host"
    PORT = "port"
    SERVICE = "service"
    # Vulnerability/exploit entities
    VULNERABILITY = "vulnerability"
    EXPLOIT = "exploit"
    CVE = "cve"
    # Source-hunt entities
    REPO = "repo"
    FILE = "file"
    SYMBOL = "symbol"
    # Result entities
    FINDING = "finding"


class EdgeKind(StrEnum):
    # Recon topology
    HOSTS = "hosts"            # target → host
    EXPOSES = "exposes"        # host → port
    RUNS = "runs"              # port → service
    # Vulnerability mapping
    VULNERABLE_TO = "vulnerable_to"   # service|host → vulnerability
    REFERENCES = "references"          # vulnerability → cve
    EXPLOITS = "exploits"              # exploit → vulnerability
    # Source-hunt structure
    CONTAINS = "contains"      # repo → file, file → symbol
    CALLS = "calls"            # symbol → symbol
    # Findings provenance
    FOUND_IN = "found_in"      # finding → host|service|file
    DERIVED_FROM = "derived_from"   # finding → finding (variant hunting)
    EVIDENCE_FOR = "evidence_for"    # finding → vulnerability
