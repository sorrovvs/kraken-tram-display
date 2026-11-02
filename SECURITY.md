# Security Policy

## Supported versions

Only the latest version on the `main` branch is supported.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Use GitHub's [private vulnerability reporting](../../security/advisories/new) instead. Include:

- what the problem is and where it is (file / line)
- steps to reproduce
- what impact you think it has

You should get a reply within a week. If the report is confirmed, a fix will be released and you will be
credited in the changelog (unless you prefer not to be).

## Scope notes

This project talks directly to USB hardware and downloads data from a public API. Things that are in scope:
code that could make the script write unexpected data to the device, or problems with how API responses are parsed.
