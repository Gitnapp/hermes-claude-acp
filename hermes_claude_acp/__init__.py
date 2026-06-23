"""Hermes Claude ACP plugin package."""

from .provider import register_provider_profile


def register(ctx):
    """Hermes plugin entrypoint."""
    register_provider_profile()

    try:
        ctx.register_command(
            "claude-acp-status",
            _status_command,
            description="Show Claude ACP bridge configuration",
        )
    except Exception:
        # Slash command registration is best-effort; provider registration is
        # the important part for headless/gateway usage.
        pass


def _status_command(_raw_args: str = "") -> str:
    from .bridge import resolve_acpx_command

    command = resolve_acpx_command()
    return (
        "Claude ACP provider registered.\n"
        f"Bridge base URL: {default_base_url()}\n"
        f"acpx command: {command or '<not found>'}"
    )


def default_base_url() -> str:
    from .provider import DEFAULT_BASE_URL

    return DEFAULT_BASE_URL
