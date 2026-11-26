# Compatibility entry point; keep the full build definition in one place.
from pathlib import Path

spec_file = Path(SPEC).resolve().parent / "packaging" / "biuro.spec"
exec(compile(spec_file.read_text(encoding="utf-8"), str(spec_file), "exec"), globals())
