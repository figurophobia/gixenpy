<a id="readme-top"></a>

[![PyPI][pypi-shield]][pypi-url]
[![Python Versions][pyversions-shield]][pypi-url]
[![Downloads][downloads-shield]][pypi-url]
[![Contributors][contributors-shield]][contributors-url]
[![Forks][forks-shield]][forks-url]
[![Stargazers][stars-shield]][stars-url]
[![Issues][issues-shield]][issues-url]
[![MIT License][license-shield]][license-url]

<br />
<div align="center">
<h3 align="center">gixenpy</h3>

  <p align="center">
    Unofficial Python client for Gixen.com (eBay sniping)
    <br />
    <a href="#usage"><strong>See usage examples »</strong></a>
    <br />
    <br />
    <a href="https://github.com/figurophobia/gixenpy/issues/new?labels=bug">Report a bug</a>
    &middot;
    <a href="https://github.com/figurophobia/gixenpy/issues/new?labels=enhancement">Request a feature</a>
  </p>
</div>

<details>
  <summary>Table of Contents</summary>
  <ol>
    <li><a href="#about-the-project">About The Project</a>
      <ul><li><a href="#built-with">Built With</a></li></ul>
    </li>
    <li><a href="#getting-started">Getting Started</a>
      <ul>
        <li><a href="#prerequisites">Prerequisites</a></li>
        <li><a href="#installation">Installation</a></li>
      </ul>
    </li>
    <li><a href="#usage">Usage</a>
      <ul>
        <li><a href="#as-a-library">As a library</a></li>
        <li><a href="#as-a-cli">As a CLI</a></li>
      </ul>
    </li>
    <li><a href="#security">Security</a></li>
    <li><a href="#tests">Tests</a></li>
    <li><a href="#publishing-to-pypi">Publishing to PyPI</a></li>
    <li><a href="#roadmap">Roadmap</a></li>
    <li><a href="#contributing">Contributing</a></li>
    <li><a href="#license">License</a></li>
    <li><a href="#contact">Contact</a></li>
    <li><a href="#acknowledgments">Acknowledgments</a></li>
  </ol>
</details>

## About The Project

[Gixen](https://www.gixen.com/) used to offer a public API for scheduling
snipes (last-second eBay bids); that API is now **restricted to regular
users**. gixenpy solves that by automating Gixen's web form with `requests`,
just like a browser with JavaScript disabled would — without depending on an
API that's no longer available.

> Not a Gixen product, not affiliated with Gixen.com. Checked against their
> [terms of use](https://www.gixen.com/main/terms.php) and
> [FAQ](https://www.gixen.com/main/faq.php): they don't prohibit automated
> access, but use it at your own risk — Gixen may block traffic it doesn't
> recognize at its discretion.

What you can do with it:

- Schedule (`add_snipe`), list (`list_snipes`), edit (`update_snipe`), delete
  (`delete_snipe`) and purge ended snipes (`purge_completed`).
- Bid, offset (seconds before the close) and bid group, all configurable; in
  `update_snipe` each one is independent and optional (whatever isn't passed
  is kept as-is).
- Tell active, won and lost snipes apart (`Snipe.status`), not just active-vs-ended.
- Dynamically locate Gixen's forms by parsing the HTML, instead of assuming
  fixed field names: if Gixen changes their site, it keeps working as long
  as the form structure doesn't change too much.
- **Dry-run** mode (the default for `add_snipe` unless told otherwise):
  sends nothing, just reports what it would send.
- A full CLI: `gixenpy list/add/edit/remove/purge/group`.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

### Built With

* [![Python][Python.org]][Python-url]
* [![Requests][Requests.badge]][Requests-url]
* [![Click][Click.badge]][Click-url]

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Getting Started

### Prerequisites

* Python 3.10 or newer
* A [Gixen](https://www.gixen.com/) account (credentials are Gixen's, not
  eBay's)

### Installation

```bash
pip install gixenpy
```

Or straight from GitHub, for the latest unreleased changes:

```bash
pip install git+https://github.com/figurophobia/gixenpy.git
```

For development (with tests):

```bash
git clone https://github.com/figurophobia/gixenpy.git
cd gixenpy
pip install -e ".[dev]"
```

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Usage

### As a library

```python
from gixenpy import GixenClient

client = GixenClient(username="your_gixen_username", password="your_password", dry_run=True)

# Schedule a snipe (dry_run=True by default: doesn't bid for real).
result = client.add_snipe(item_id="123456789012", max_bid=42.50)
print(result.message)

# When you want to bid for real:
result = client.add_snipe(item_id="123456789012", max_bid=42.50, dry_run=False)

# Manage existing snipes.
for snipe in client.list_snipes():
    print(snipe.item_id, snipe.max_bid, snipe.status)  # "active" | "won" | "lost" | "ended" | "unknown"

client.update_snipe(item_id="123456789012", new_max=55)
client.delete_snipe(item_id="123456789012")

# Offset (seconds before the close) and bid group, optional:
client.add_snipe(item_id="123456789012", max_bid=42.50, offset=9, group=1, dry_run=False)
client.update_snipe(item_id="123456789012", offset=12)   # change only the offset
client.update_snipe(item_id="123456789012", group=2)     # change only the group

client.purge_completed()  # remove already-ended snipes from the history
```

Credentials are your **Gixen** account's (not eBay's). Never logged or
printed.

### As a CLI

```bash
export GIXEN_USERNAME=your_username
export GIXEN_PASSWORD=your_password
# or create a .env with those two lines (see .env.example)

gixenpy --help                 # list of commands
gixenpy --version

gixenpy list                   # active and ended snipes

gixenpy add 123456789012 42.50                       # schedules a snipe FOR REAL
gixenpy add 123456789012 42.50 --offset 9 --group 1  # with offset/group
gixenpy add 123456789012 42.50 --dry-run             # just shows what would be sent

gixenpy edit 123456789012 55          # change the max bid
gixenpy edit 123456789012 --offset 12 # change only the offset (keeps the bid)
gixenpy edit 123456789012 --group 2   # change only the group

gixenpy remove 123456789012    # delete the snipe
gixenpy purge                  # purge ended snipes from the history

gixenpy group 3 123456789012 987654321098  # assign group 3 to several items
```

Every command exits with code `0` if Gixen confirmed the operation, or `1`
if it failed/errored (message on stderr, prefixed with `ERROR:`).

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Security

- A small, fixed number of requests per action (1 GET + 1 POST), no
  aggressive polling.
- Reuses the existing session instead of relogging on every action
  (avoiding unnecessarily kicking out other sessions — Gixen only allows one
  active session per account).
- The max bid amount is always decided by whoever calls the library; it
  never picks a value on its own.
- Any form URL extracted from Gixen's HTML is validated against
  `gixen.com` (and HTTPS only) before sending anything; a tampered response
  can't make the client send the payload to a different host.
- Configurable connect/read timeouts (`GixenClient(timeout=...)`, default
  `(5, 25)` seconds) to fail fast on network issues.
- A single retry with backoff (`GixenClient(retry_backoff=...)`, 2 seconds
  by default) if Gixen accepts a write but doesn't end up applying it —
  never on an explicit rejection.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

Neither suite makes any network requests: `tests/test_client.py` replaces
Gixen's logged-in page with test HTML and verifies form parsing and the
payload built; `tests/test_cli.py` exercises the CLI with
`click.testing.CliRunner`, injecting a fake client.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Publishing to PyPI

`gixenpy` is [published on PyPI](https://pypi.org/project/gixenpy/). New
releases are published automatically: publishing a
[GitHub Release](https://github.com/figurophobia/gixenpy/releases) triggers
[`.github/workflows/publish.yml`](.github/workflows/publish.yml), which
builds the package and uploads it to PyPI using
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC —
no API token stored anywhere).

To cut a new release:

1. Bump the version in `pyproject.toml` and `src/gixenpy/__init__.py`, update
   `CHANGELOG.md`, commit and push.
2. Tag it and publish a GitHub Release (e.g. `gh release create v0.3.0`).
3. That's it — the workflow builds and uploads it to PyPI.

Manual publishing still works too, if ever needed:

```bash
pip install -e ".[dev]" build twine
python -m build                 # generates dist/*.tar.gz and dist/*.whl
twine check dist/*              # validates the package
twine upload dist/*             # requires your own PyPI API token
```

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Roadmap

- [x] Publish `gixenpy` on PyPI
- [ ] Persistent session across processes (today every new `GixenClient`
      logs in if needed; there's no on-disk session cache)
- [ ] Revisit the active/ended status heuristic if Gixen changes the
      text/order of "Status (main): ..." in their HTML

See the [open issues](https://github.com/figurophobia/gixenpy/issues) for
the full list of proposed features (and known issues).

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Contributing

Contributions are welcome. If you have an idea that would make this better,
fork the repo and open a pull request, or simply open an issue with the
"enhancement" tag.

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/my-feature`)
3. Commit your Changes (`git commit -m 'Add my-feature'`)
4. Push to the Branch (`git push origin feature/my-feature`)
5. Open a Pull Request

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for more
information.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Contact

Project issues: [github.com/figurophobia/gixenpy/issues](https://github.com/figurophobia/gixenpy/issues)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Acknowledgments

* [hsukenooi/gixen-cli](https://github.com/hsukenooi/comic-pipeline/tree/main/packages/gixen-cli) — another independent implementation of the same problem, used as a reference for several improvements to this library
* [Best-README-Template](https://github.com/othneildrew/Best-README-Template) — the template used for this README
* [Gixen.com](https://www.gixen.com/) for the sniping service itself

<p align="right">(<a href="#readme-top">back to top</a>)</p>

[pypi-shield]: https://img.shields.io/pypi/v/gixenpy.svg?style=for-the-badge
[pypi-url]: https://pypi.org/project/gixenpy/
[pyversions-shield]: https://img.shields.io/pypi/pyversions/gixenpy.svg?style=for-the-badge
[downloads-shield]: https://img.shields.io/pypi/dm/gixenpy.svg?style=for-the-badge
[contributors-shield]: https://img.shields.io/github/contributors/figurophobia/gixenpy.svg?style=for-the-badge
[contributors-url]: https://github.com/figurophobia/gixenpy/graphs/contributors
[forks-shield]: https://img.shields.io/github/forks/figurophobia/gixenpy.svg?style=for-the-badge
[forks-url]: https://github.com/figurophobia/gixenpy/network/members
[stars-shield]: https://img.shields.io/github/stars/figurophobia/gixenpy.svg?style=for-the-badge
[stars-url]: https://github.com/figurophobia/gixenpy/stargazers
[issues-shield]: https://img.shields.io/github/issues/figurophobia/gixenpy.svg?style=for-the-badge
[issues-url]: https://github.com/figurophobia/gixenpy/issues
[license-shield]: https://img.shields.io/github/license/figurophobia/gixenpy.svg?style=for-the-badge
[license-url]: https://github.com/figurophobia/gixenpy/blob/master/LICENSE
[Python.org]: https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white
[Python-url]: https://www.python.org/
[Requests.badge]: https://img.shields.io/badge/Requests-2.32%2B-000000?style=for-the-badge&logo=python&logoColor=white
[Requests-url]: https://requests.readthedocs.io/
[Click.badge]: https://img.shields.io/badge/Click-8.1%2B-000000?style=for-the-badge&logo=python&logoColor=white
[Click-url]: https://click.palletsprojects.com/
