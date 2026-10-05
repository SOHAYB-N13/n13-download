"""N13 Download Manager test suite.

Run with:  python -m unittest discover -s tests

The suite must be hermetic.  Constructing the API auto-starts the loopback live
server, which by design registers OS integration (the ``dldm://`` protocol
handler and the Chrome/Edge native-messaging host).  Running the tests must
never modify the developer's real registry, so OS integration is disabled for
every test process here.

The registration logic itself is still fully exercised — against an in-memory
registry fake — in ``tests/test_protocol_registration.py``.
"""

import os

os.environ.setdefault("N13_SKIP_OS_INTEGRATION", "1")
