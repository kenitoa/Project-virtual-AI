"""Expression policy boundary; no automatic emotion inference or model parsing."""

from virtual_ai.safety import map_expression


def select_expression(response, allowed, policy=None):
    if response.blocked or policy is None:
        return "neutral"
    return map_expression(policy(response), allowed)
