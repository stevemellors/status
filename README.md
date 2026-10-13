# Mellors Family Status

**https://stevemellors.github.io/status/**

Every 5 minutes GitHub checks the family services from outside the house (`check.py`, run by
`.github/workflows/monitor.yml`). Results are kept on the `data` branch. If something fails twice in a row, a phone
alert goes out through ntfy, and another when it's back. If nothing at all answers, the alert says the house's
internet or power is probably out.
