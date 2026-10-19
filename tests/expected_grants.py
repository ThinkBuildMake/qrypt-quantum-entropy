"""The architecture's "Database role grants" table (docs/ARCHITECTURE.md), committed as data.

Each cell is {privilege: columns}; None means every column of the table, a tuple names the columns.
"""

ALL = None

EXPECTED: dict[str, dict[str, dict[str, tuple[str, ...] | None]]] = {
    "credentials": {
        "mcp_server": {"SELECT": ALL},
        "http_service": {"SELECT": ALL, "INSERT": ALL, "UPDATE": ("status", "revoked_at")},
        "backup": {"SELECT": ALL},
    },
    "budget": {
        "mcp_server": {"SELECT": ALL, "UPDATE": ("used_kib", "updated_at")},
        "http_service": {"SELECT": ALL, "UPDATE": ("cap_kib", "updated_at")},
        "backup": {"SELECT": ALL},
    },
    "batches": {
        "mcp_server": {"SELECT": ALL, "INSERT": ALL, "UPDATE": ("outcome", "settled_at")},
        "http_service": {"SELECT": ALL},
        "backup": {"SELECT": ALL},
    },
    "issuances": {
        "mcp_server": {"INSERT": ALL},
        "http_service": {"SELECT": ALL},
        "backup": {"SELECT": ALL},
    },
    "issuance_batches": {
        "mcp_server": {"INSERT": ALL},
        "http_service": {"SELECT": ALL},
        "backup": {"SELECT": ALL},
    },
    "tokens": {
        "mcp_server": {"INSERT": ALL},
        "http_service": {"SELECT": ALL, "UPDATE": ("used_at",)},
        "backup": {"SELECT": ALL},
    },
    "redemption_outcomes": {
        "mcp_server": {},
        "http_service": {"SELECT": ALL, "INSERT": ALL},
        "backup": {"SELECT": ALL},
    },
    "server_status": {
        "mcp_server": {"SELECT": ALL, "INSERT": ALL, "UPDATE": ALL},
        "http_service": {"SELECT": ALL},
        "backup": {"SELECT": ALL},
    },
}
ROLES = ("mcp_server", "http_service", "backup")
