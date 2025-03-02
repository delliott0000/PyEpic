[![Python Version: >=3.10](https://img.shields.io/pypi/pyversions/py-epic.svg)](https://pypi.python.org/pypi/py-epic)
[![Code Style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Imports: isort](https://img.shields.io/badge/imports-isort-ef8336.svg)](https://github.com/PyCQA/isort)

**Still in early development.**

# PyEpic
An asynchronous, object-oriented API wrapper for the Epic/Fortnite HTTP services, written in Python.

# Key Features
- Use of the `asyncio` framework to handle many IO-bound tasks concurrently.
- Automatic, configurable rate limit handling and caching.
- Optimised for a balance of speed and memory.

# Installing
**Python 3.10 or higher is required. For project dependencies, see [requirements.txt](https://github.com/delliott0000/PyEpic/blob/master/requirements.txt).**

It is recommended to install the library within a virtual environment instead of the global Python installation.

```sh
# Windows
py -m pip install py-epic

# Linux/MacOS
python3 -m pip install py-epic
```

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
- PyEpic does not (currently) support Epic Games' XMPP services. This includes real-time in-game events such as party invites and friend requests.

# Disclaimers
- The APIs that PyEpic interacts with are not officially documented, nor are they intended to be used outside the official clients.
  - The package could experience major breaking changes (or stop working!) at any moment.
  - The developer can not take responsibility for any damages that result from using the package.