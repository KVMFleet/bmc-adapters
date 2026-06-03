"""Exception types raised by the Redfish client."""


class RedfishError(Exception):
    """Anything we couldn't recover from while talking to a BMC.

    Treat as terminal for the current operation; the caller decides
    whether to retry (e.g. polling cycles treat it as a transient
    offline blip).
    """
