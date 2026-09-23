# pytest-asyncio 1.x manages the event loop itself via asyncio_mode = auto
# (set in pytest.ini). A custom `event_loop` fixture is no longer supported
# and raises a deprecation error on collection, so none is defined here.
