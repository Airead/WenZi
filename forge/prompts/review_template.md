Review the code changes in `{diff_file}`.
Refer to `{source_code_dir}` for full project context.

Review time: {review_time}.
Project stack: {project_context}.
Strictness level: {strictness}.

## Review focus

1. **Memory & resource leaks** — IOSurface cleanup, CGEventTap ctypes prevent GC of callbacks, CFRelease for synthetic events.
2. **PyObjC correctness** — No arbitrary attributes on AppKit objects, unique ObjC class names, NSGlassEffectView with adaptive appearance disabled.
3. **Security** — Command injection, path traversal, secrets in plaintext (must use wz.keychain).
4. **Test safety** — Tests must use tmp_path, never real user data paths.
5. **Dark mode** — System semantic colors only, no hardcoded RGB.

## What to ignore

- Style nits and import ordering — handled by ruff.
- Minor docstring or comment issues.

## Output format

For each issue found, output:
- **File**: path relative to source root
- **Line(s)**: line number or range
- **Severity**: critical / warning / suggestion
- **Description**: concise explanation and recommended fix
