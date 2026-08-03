# Licensing metadata

The canonical repository does not currently contain a publisher-approved
licence file. The `0.3.3` package therefore declares the SPDX-compatible custom
reference `LicenseRef-NOASSERTION` in `pyproject.toml` instead of implying
rights that the publisher has not granted. Packaging metadata requires a valid
SPDX expression and does not accept SPDX's bare document sentinel
`NOASSERTION`, hence the `LicenseRef-` form.

`LicenseRef-NOASSERTION` is not a permissive licence and must not be interpreted as one.
Before redistributing or embedding the package outside an already-authorized
environment, obtain an explicit licence decision from the repository owner. A
future release may replace it with a publisher-approved SPDX licence
expression and repository licence text; that decision is intentionally not
made by this remediation patch.
