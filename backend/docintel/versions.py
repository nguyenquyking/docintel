"""Version stamps. Every extraction records them, so any change re-processes exactly
what it affects and an eval run can always say what produced a number."""

PIPELINE_VERSION = "1.0.0"
PARSER_VERSION = "parse-1"      # bump when the IR changes: cached page artifacts are keyed by it
SCHEMA_VERSION = "contract-v1"  # bump when extracted fields change meaning
