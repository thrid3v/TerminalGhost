# terminalghost — top-level package
#
# Responsibility:
#   Package metadata and public re-exports. This file should stay minimal.
#   Version is the single source of truth (also read by pyproject.toml via
#   hatchling's dynamic versioning if that feature is enabled later).
#
# Exports:
#   __version__  — PEP 440 version string
#
# Edge cases / notes:
#   - Do NOT import submodules here; doing so would make the package import
#     heavyweight and force early SQLite/config initialization on every
#     `import terminalghost`. Submodules should be imported lazily at call sites.

__version__ = "0.3.0"
