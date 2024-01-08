[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

**Still in early development.**

# PyEpic
An asynchronous, object-oriented API wrapper for the Epic/Fortnite HTTP services, written in Python.

# Key Features
- Use of the `asyncio` framework to handle many IO-bound tasks concurrently.
- Automatic, configurable rate limit handling and caching.
- Optimised for speed and memory.

# Basic Example

```py
import asyncio
import pyepic


async def main():
    async with pyepic.HTTPClient() as client:
        auth_code = input(f'Enter authorization code from {client.user_auth_path} here: ')

        async with client.create_auth_session(auth_code) as auth_session:
            account = await auth_session.account()

            print(f'Logged in as: {account}')


asyncio.run(main())
```

# Notes
- PyEpic does not support Epic Games' XMPP services. This includes real-time in-game events such as party invites or whispers.

# Disclaimers
- The APIs that PyEpic with are not officially documented, nor are they intended to be used outside the official clients. As a result, the package could experience major breaking changes (or stop working!) at any moment.