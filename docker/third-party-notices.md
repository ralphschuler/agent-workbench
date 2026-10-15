# Chrome seccomp profile

`seccomp-chrome.json` derives from Microsoft Playwright's Docker seccomp
profile at commit `a038fe7e0eb955ef41db53d40a859ce487889b6d`:

<https://github.com/microsoft/playwright/blob/a038fe7e0eb955ef41db53d40a859ce487889b6d/utils/docker/seccomp_profile.json>

The upstream file's SHA-256 is
`cc3e61cabda6bbc1e53e54d27ba4d55a9d3be829b6dd1a596f4a7b31b1cc7849`.
Its Apache 2.0 license is reproduced in `LICENSE.playwright`.

Local changes:

- Allow `chroot` alongside `clone`, `setns`, and `unshare` so Chromium's
  user namespace sandbox works when Docker drops all initial capabilities.
  Normal kernel permission checks still apply to these system calls.
- Return `ENOSYS` (38) for `clone3`, matching Docker's fallback for callers
  without `CAP_SYS_ADMIN`. This lets glibc fall back to `clone` instead of
  failing thread creation with `EPERM`.
- Reformat the JSON with two-space indentation.

The resulting file's SHA-256 is
`b24ceab14e0b36b570c61887f8fe25bfbc290621647ca53b85f00354cb6c8cc9`.

Docker's reference for the `clone3` rule:
<https://github.com/moby/profiles/blob/main/seccomp/default.json>.
