"""Local POC health observations and deduplicated alert transitions."""
from datetime import datetime, timezone


def transition(previous, probes, at, threshold=2):
    """Two consecutive failures open, two successes resolve; no alert storms."""
    current, events = {}, []
    for key, observation in sorted(probes.items()):
        old = previous.get(key, {})
        good = observation['healthy'] is True
        failures = 0 if good else old.get('failures', 0) + 1
        successes = old.get('successes', 0) + 1 if good else 0
        active = old.get('active', False)
        if not active and failures >= threshold:
            active = True
            events.append({'probe': key, 'event': 'opened', 'at': at, 'reason': observation['detail']})
        elif active and successes >= threshold:
            active = False
            events.append({'probe': key, 'event': 'resolved', 'at': at, 'reason': observation['detail']})
        current[key] = dict(observation, failures=failures, successes=successes, active=active,
                            opened_at=at if events and events[-1]['probe'] == key and events[-1]['event'] == 'opened'
                            else old.get('opened_at'), checked_at=at)
    return current, events


def fresh_status(data, maximum_age=45):
    generated = datetime.fromisoformat(data['generated_at'])
    age = (datetime.now(timezone.utc) - generated).total_seconds()
    return -2 <= age <= maximum_age
