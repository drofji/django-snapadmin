"""
Backup collector for ``snapadmin_info`` (#BKP1g).

Reports which destinations are active, when each one last ran, whether AGE
encryption is on and — if so — the configured recipients' fingerprints, plus
whether a restore has ever completed. Never the AGE identity (private key) —
only public, safe-to-print material reaches this report, matching the rule
already documented for backup encryption in SECURITY.md.
"""

from __future__ import annotations

from snapadmin.backup import _active_destinations, _load_state, get_backup_config
from snapadmin.crypto import fingerprint
from snapadmin.diagnostics.registry import register
from snapadmin.restore import last_restore_run


@register("backups", title="Backups", icon="💾", order=22)
def collect(*, verbose: bool) -> dict:
    """Collect the backups section."""
    config = get_backup_config()
    data: dict = {"enabled": config.enabled}
    if not config.enabled:
        return data

    destinations = _active_destinations(config)
    state = _load_state(config)
    data["destinations"] = destinations
    data["last_run"] = {dest: state.get(dest) for dest in destinations}
    data["encrypted"] = bool(config.age_recipients)
    if config.age_recipients:
        data["recipient_fingerprints"] = [fingerprint(r) for r in config.age_recipients]
    data["restore_last_run"] = last_restore_run(config)
    return data
