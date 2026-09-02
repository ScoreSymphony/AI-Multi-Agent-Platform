from pathlib import Path

path = Path("tests/test_kernel_consistency.py")
text = path.read_text()
old = '''            k.create_run(
                idempotency_key="mode:step",
                task_id=task_id,
                subject_type="step",
                subject_id=new_id("step"),
            )
'''
new = '''            k.create_run(
                idempotency_key="mode:step",
                task_id=task_id,
                subject_type="step",
                subject_id=planned_step_ids(k, task_id)[0],
            )
'''
if text.count(old) != 1:
    raise SystemExit(f"mode-step prepatch target mismatch: {text.count(old)}")
path.write_text(text.replace(old, new, 1))
