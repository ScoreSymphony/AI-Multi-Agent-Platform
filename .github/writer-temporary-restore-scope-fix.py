from pathlib import Path


SERVICE = Path("src/ai_multi_agent_platform/learning/service.py")
RESTORE = Path("src/ai_multi_agent_platform/deployment/restore_integrity_current.py")
POST_PROMOTION = Path("src/ai_multi_agent_platform/learning/post_promotion_repository.py")
TEST = Path("tests/test_issue_594_595_restore_scope_regression.py")


service = SERVICE.read_text(encoding="utf-8")
if "def require_evaluation_project_scope(" not in service:
    raise SystemExit("canonical Learning evaluation scope method is missing")
if "self.require_evaluation_project_scope(" not in service:
    raise SystemExit("Learning lifecycle is not using the canonical scope method")

restore = RESTORE.read_text(encoding="utf-8")
if "learning.service.require_evaluation_project_scope(" not in restore:
    raise SystemExit("restore validator is not using the canonical Learning scope method")
mutable_reader = 'sqlite3.connect(f"file:{post_database}?mode=ro", uri=True)'
immutable_reader = 'sqlite3.connect(f"file:{post_database}?mode=ro&immutable=1", uri=True)'
if immutable_reader not in restore:
    if mutable_reader not in restore:
        raise SystemExit("post-promotion restore SQLite reader target not found")
    restore = restore.replace(mutable_reader, immutable_reader, 1)
RESTORE.write_text(restore, encoding="utf-8")

post_promotion = POST_PROMOTION.read_text(encoding="utf-8")
schema_checkpoint = (
    '            # Restore-integrity readers open immutable snapshots. Checkpoint the schema too,\n'
    '            # so a newly initialized empty recorder is fully visible without consulting its WAL.\n'
    '            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")\n'
)
initializer, separator, remainder = post_promotion.partition("    def store")
if not separator:
    raise SystemExit("post-promotion recorder store boundary not found")
if 'connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")' not in initializer:
    target = '''                );
                """
            )

'''
    if target not in initializer:
        raise SystemExit("post-promotion schema initializer target not found")
    initializer = initializer.replace(target, target + schema_checkpoint, 1)
    post_promotion = initializer + separator + remainder
POST_PROMOTION.write_text(post_promotion, encoding="utf-8")

if not TEST.exists():
    raise SystemExit("restore scope regression test is missing")
