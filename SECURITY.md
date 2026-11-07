# Security

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | yes |

## Reporting a vulnerability

Report vulnerabilities privately. On the repository's **Security** tab, choose
**Report a vulnerability** if it is offered. If it is not, open an issue titled
"Security contact request" that contains no details, and you will be given a
private channel. Include the version, the steps to reproduce and the impact you
expect. You should get a reply within seven days.

## Security model

BuildScope is a single-user tool for one machine. It runs commands you approve,
with your permissions, and stores history in a local SQLite file.

**What it defends against**

- **Other machines.** `buildscope serve` binds only to loopback addresses and
  refuses others. Every API route also rejects requests whose client address is
  not loopback.
- **Web pages in your browser.** Every route rejects a `Host` header other than
  `localhost`, `127.0.0.1` or `::1`, which stops DNS rebinding, and rejects any
  `Origin` that is not the same origin, which stops cross-site requests. This
  includes the write endpoints for repositories and pipelines.
- **Running commands you did not review.** The API never executes stages. A run
  starts only after `buildscope.workers.cli enqueue --approve`, and the dashboard
  and prediction endpoints are read-only (tests assert they write nothing and
  spawn nothing but `git`).
- **Shell injection.** Stage commands are argument lists passed to `execve`
  without a shell. The C++ worker receives arguments through a length-prefixed
  file.
- **Malicious test reports.** JUnit XML is parsed with `defusedxml` with DTDs and
  entities refused, and is size-capped.
- **Swapped model files.** A model is loaded only from a directory you own that
  no one else can write, with no symlinks, and only if the bytes being unpickled
  match the checksum in its metadata. Changed files are re-verified.
- **Leaking local state.** `.gitignore` covers databases, models, datasets, logs
  and `.env` files. `python -m buildscope.release preflight` fails if any are
  tracked or packaged, and CI runs gitleaks over the full history.

**What it does not defend against**

- **Untrusted pipelines.** Stages are not sandboxed. A pipeline you approve can do
  anything your user account can. Review commands with `workers.cli preview`.
- **Other local users and processes.** Any process running as you can read the
  database and logs, call the API, or change the model directory.
- **Untrusted model files.** The checksum lives next to the model, so it only
  detects accidental changes. Train models yourself or get them from someone
  you trust.
- **Network exposure through a proxy.** Putting BuildScope behind a reverse proxy
  or tunnel bypasses the loopback assumption. There is no authentication.

## Data

Logs, test reports, benchmark reports and models stay under `data/` (or wherever
you point them). Prediction scores are not calibrated probabilities and should
not gate releases on their own.
